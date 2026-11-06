"""One engine node with one symbol engine per contract (build step 1.06).

``EngineNode`` builds a NautilusTrader engine with:
* the simulated CME Globex venue (margin account, per-contract fees, one-tick slippage model);
* every contract in the data pack as an instrument;
* one :class:`~strategies.starter.StarterStrategy` per active contract (the symbol engines);
* the :class:`~engine.nautilus.guard.PortfolioGuard` above all of them;
* the kill switch wired to flatten every symbol engine.

Two ways to run it:
* :meth:`run_backtest`: every session at full speed, then results (step 1.14);
* :meth:`run_paced`: demo replay at a chosen speed (1x = real time) in small time slices, applying
  commands from the control API between slices (start/pause, speed, kill switch, fault
  injection). The engine thread is the only thread that touches Nautilus objects.

Live paper trading on Databento + Interactive Brokers uses the same strategies and guard inside a
``TradingNode`` (step 1.23).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import LoggingConfig, RiskEngineConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money

from engine.calendar import from_unix_ns
from engine.context import EngineContext
from engine.instruments import SPECS
from engine.nautilus.catalog import PackInfo
from engine.nautilus.guard import GuardConfig, PerContractFees, PortfolioGuard
from engine.nautilus.instruments import GLOBEX
from engine.nautilus.portfolio import root_of
from risk.killswitch import Trip
from strategies.starter import StarterConfig, StarterStrategy

log = logging.getLogger(__name__)

DEFAULT_BALANCE: Final = Decimal(50_000)
SLICE_SECONDS: Final = 0.25  # wall-clock length of one paced slice


class RunState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class Progress:
    state: RunState = RunState.IDLE
    speed: float = 60.0
    market_time: datetime | None = None
    trade_date: date | None = None
    day_index: int = 0
    days_total: int = 0
    error: str = ""
    started_wall: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "speed": self.speed,
            "market_time": self.market_time.isoformat() if self.market_time else None,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "day": self.day_index,
            "days_total": self.days_total,
            "error": self.error,
        }


Command = tuple[str, Any]


class EngineNode:
    def __init__(
        self,
        ctx: EngineContext,
        pack: PackInfo,
        starting_balance: Decimal = DEFAULT_BALANCE,
        log_level: str = "ERROR",
        fill_seed: int = 42,
    ) -> None:
        self.ctx = ctx
        self.pack = pack
        self.starting_balance = starting_balance
        self.progress = Progress(days_total=len(pack.dates))
        self.commands: queue.Queue[Command] = queue.Queue()
        self._engine_thread: int | None = None
        self._flatten_requested = threading.Event()
        self.engine = BacktestEngine(
            BacktestEngineConfig(
                trader_id=TraderId("TACP-001"),
                logging=LoggingConfig(log_level=log_level),
                risk_engine=RiskEngineConfig(max_order_submit_rate="20/00:00:01"),
            )
        )
        self.engine.add_venue(
            GLOBEX,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            starting_balances=[Money(float(starting_balance), USD)],
            fill_model=FillModel(prob_fill_on_limit=0.5, prob_slippage=0.5, random_seed=fill_seed),
            fee_model=PerContractFees(ctx.fee_filter),
        )
        self.instruments = {i.id.symbol.value: i for i in pack.instruments()}
        for inst in self.instruments.values():
            self.engine.add_instrument(inst)
        self.strategies: dict[str, StarterStrategy] = {}
        active = set(ctx.settings.allowed_roots)
        for symbol, inst in sorted(self.instruments.items()):
            root = root_of(symbol)
            if root not in active or not SPECS[root].active:
                continue
            strat = StarterStrategy(
                StarterConfig(
                    strategy_id=f"STARTER-{root}",
                    order_id_tag=root.replace("10Y", "TENY"),
                    root=root,
                    instrument_id=inst.id,
                ),
                ctx,
            )
            self.strategies[root] = strat
            self.engine.add_strategy(strat)
        self.guard = PortfolioGuard(GuardConfig(component_id="PORTFOLIO-GUARD"), ctx)
        self.engine.add_actor(self.guard)
        ctx.kill.on_trip(self._on_kill)

    # ----- kill switch: flatten on the engine thread ---------------------------------------------
    def _on_kill(self, trip: Trip) -> None:
        if threading.get_ident() == self._engine_thread:
            self.flatten_all()
        else:
            self._flatten_requested.set()

    def flatten_all(self) -> None:
        self._flatten_requested.clear()
        for strat in self.strategies.values():
            strat.flatten()

    # ----- data -----------------------------------------------------------------------------------
    def day_data(self, trade_date: date) -> list[Any]:
        """All quotes and trades of the active contracts for ``trade_date``, time-ordered."""
        cat = self.pack.catalog
        ids = [str(s.iid) for s in self.strategies.values()]
        sessions = [
            s
            for root in self.strategies
            if (s := SPECS[root].calendar.session_for(trade_date)) is not None
        ]
        if not sessions or not ids:
            return []
        start = min(s.open for s in sessions)
        end = max(s.close for s in sessions)
        data: list[Any] = [
            *cat.quote_ticks(instrument_ids=ids, start=start, end=end),
            *cat.trade_ticks(instrument_ids=ids, start=start, end=end),
        ]
        data.sort(key=lambda d: d.ts_init)
        return data

    def _withhold_faulted(self, batch: list[Any]) -> list[Any]:
        faulted = self.ctx.faults.data_feed
        if not faulted:
            return batch
        return [d for d in batch if root_of(d.instrument_id.symbol.value) not in faulted]

    def _run_batch(self, batch: list[Any]) -> None:
        batch = self._withhold_faulted(batch)
        if self._flatten_requested.is_set():
            self.flatten_all()
        if not batch:
            return
        self.engine.add_data(batch)
        self.engine.run(streaming=True)
        self.engine.clear_data()
        self.progress.market_time = from_unix_ns(batch[-1].ts_init)

    # ----- full-speed backtest --------------------------------------------------------------------
    def run_backtest(self, on_day: Callable[[date], None] | None = None) -> None:
        self._engine_thread = threading.get_ident()
        self.progress.state = RunState.RUNNING
        try:
            for i, d in enumerate(self.pack.dates):
                self.progress.day_index, self.progress.trade_date = i + 1, d
                data = self.day_data(d)
                for k in range(0, len(data), 50_000):
                    self._run_batch(data[k : k + 50_000])
                if on_day:
                    on_day(d)
            self.engine.end()
            self.progress.state = RunState.FINISHED
        except Exception as exc:
            self.progress.state = RunState.ERROR
            self.progress.error = f"{type(exc).__name__}: {exc}"
            raise

    # ----- paced demo replay ----------------------------------------------------------------------
    def run_paced(self, stop: threading.Event, speed: float = 60.0, loop: bool = False) -> None:
        """Replay the pack at ``speed`` x real time until finished or ``stop`` is set."""
        self._engine_thread = threading.get_ident()
        self.progress.speed = speed
        self.progress.state = RunState.RUNNING
        try:
            while not stop.is_set():
                for i, d in enumerate(self.pack.dates):
                    self.progress.day_index, self.progress.trade_date = i + 1, d
                    if not self._replay_day(self.day_data(d), stop):
                        break
                if not loop or stop.is_set():
                    break
            self.progress.state = RunState.STOPPED if stop.is_set() else RunState.FINISHED
        except Exception as exc:
            self.progress.state = RunState.ERROR
            self.progress.error = f"{type(exc).__name__}: {exc}"
            log.exception("engine stopped on error")
        finally:
            self.engine.end()

    def _replay_day(self, data: list[Any], stop: threading.Event) -> bool:
        idx, n = 0, len(data)
        market_ns = data[0].ts_init if data else 0
        while idx < n:
            if stop.is_set():
                return False
            self._drain_commands()
            if self.progress.state is RunState.PAUSED:
                if self._flatten_requested.is_set():
                    self._run_batch([])
                time.sleep(SLICE_SECONDS)
                continue
            t0 = time.monotonic()
            market_ns += int(self.progress.speed * SLICE_SECONDS * 1e9)
            # Skip quiet gaps (weekend, maintenance break) instead of waiting through them.
            market_ns = max(market_ns, data[idx].ts_init)
            end = idx
            while end < n and data[end].ts_init <= market_ns:
                end += 1
            self._run_batch(data[idx:end])
            idx = end
            time.sleep(max(0.0, SLICE_SECONDS - (time.monotonic() - t0)))
        return True

    def _drain_commands(self) -> None:
        while True:
            try:
                name, arg = self.commands.get_nowait()
            except queue.Empty:
                return
            if name == "pause":
                self.progress.state = RunState.PAUSED
            elif name == "resume":
                self.progress.state = RunState.RUNNING
            elif name == "speed":
                self.progress.speed = max(1.0, min(float(arg), 3600.0))
            elif name == "fault_data":
                self.ctx.faults.data_feed.add(str(arg))
            elif name == "fault_broker":
                self.ctx.faults.broker_disconnected = True
            elif name == "clear_faults":
                self.ctx.faults.data_feed.clear()
                self.ctx.faults.broker_disconnected = False

    # ----- results --------------------------------------------------------------------------------
    def fills(self) -> Any:
        return self.engine.trader.generate_order_fills_report()

    def positions(self) -> Any:
        return self.engine.trader.generate_positions_report()

    def account_report(self) -> Any:
        return self.engine.trader.generate_account_report(GLOBEX)

    def dispose(self) -> None:
        self.engine.dispose()
