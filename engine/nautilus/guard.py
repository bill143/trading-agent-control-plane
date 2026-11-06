"""Portfolio guard actor: restart reconciliation, account monitoring and the automatic kill-switch
triggers (steps 1.09, 1.11, 1.12).

Runs once per second of engine time:
* publishes account equity, margin, P&L and exposure to the state store;
* trips the kill switch on a risk-limit breach, a market-data feed that has gone silent while
  its session is open, or a broker disconnect;
* confirms the kill switch's flatten once nothing is open.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.objects import Money

from engine.calendar import from_unix_ns
from engine.context import EngineContext, reconcile
from engine.nautilus.portfolio import portfolio_view, root_of
from risk.authority import SYSTEM
from risk.fees import DailyFeeFilter
from risk.killswitch import TripSource


class GuardConfig(ActorConfig, frozen=True):
    interval_seconds: int = 1


class PortfolioGuard(Actor):
    def __init__(self, config: GuardConfig, ctx: EngineContext) -> None:
        super().__init__(config)
        self.ctx = ctx
        self._interval = config.interval_seconds
        self.breaches_seen: list[str] = []

    def on_start(self) -> None:
        self.reconcile_on_start()
        self.clock.set_timer(
            "portfolio_guard", timedelta(seconds=self._interval), callback=self._on_timer
        )

    def on_stop(self) -> None:
        if "portfolio_guard" in self.clock.timer_names:
            self.clock.cancel_timer("portfolio_guard")

    # ----- restart reconciliation (step 1.12) -------------------------------------------------
    def reconcile_on_start(self) -> None:
        broker = {
            pos.instrument_id.symbol.value: int(pos.signed_qty)
            for pos in self.cache.positions_open()
        }
        report = reconcile(broker, self.ctx.journal.load())
        self.ctx.record_reconcile(report)
        if not report.may_trade:
            self.log.warning(f"Reconciliation mismatch, trading blocked: {report.differences}")

    # ----- periodic checks ----------------------------------------------------------------------
    def _on_timer(self, event: Any) -> None:
        now = from_unix_ns(event.ts_event)
        self.check(now)

    def check(self, now: datetime) -> None:
        ctx = self.ctx
        view = portfolio_view(self, ctx)
        ctx.store.set_clock(now)
        ctx.store.set_account(
            equity=view.account.equity.quantize(Decimal("0.01")),
            margin_used=view.account.margin_used,
            available=view.account.available.quantize(Decimal("0.01")),
            usable_after_buffer=(
                view.account.available * (1 - ctx.settings.buying_power_buffer)
            ).quantize(Decimal("0.01")),
            pnl_today=view.pnl_today.quantize(Decimal("0.01")),
            realized_today=view.realized_today.quantize(Decimal("0.01")),
            unrealized=view.unrealized.quantize(Decimal("0.01")),
            open_positions=len(view.positions),
            pending_entries=len(view.pending),
            contracts_used=view.contracts_used_total,
            daily_loss_limit=ctx.settings.daily_loss_limit,
            kill_switch=ctx.kill.tripped,
            reconciled=ctx.reconciled.is_set(),
            mode=ctx.mode.value,
            now=now.isoformat(),
        )
        if ctx.kill.tripped:
            if not view.positions and not self.cache.orders_open():
                ctx.kill.confirm_flat()
            return
        for breach in ctx.risk.breaches(view):
            self.breaches_seen.append(breach)
            ctx.kill.trip(SYSTEM, TripSource.RISK_BREACH, breach)
            return
        stale = self._stale_feeds(now)
        if stale:
            ctx.kill.trip(SYSTEM, TripSource.DATA_FEED, "; ".join(stale))
            return
        if ctx.faults.broker_disconnected:
            ctx.kill.trip(SYSTEM, TripSource.BROKER_DISCONNECT, "broker connection lost")

    def _stale_feeds(self, now: datetime) -> list[str]:
        limit = timedelta(seconds=self.ctx.settings.data_stale_seconds)
        out: list[str] = []
        with self.ctx.store.lock:
            for root in self.ctx.store.roots:
                t = self.ctx.store.tracker(root)
                session = t.calendar.session_at(now)
                if session is None:
                    continue  # market closed for this product: silence is expected
                since = max(t.last_ts, session.open) if t.last_ts else session.open
                if now - since > limit:
                    out.append(f"{root}: no market data for {(now - since).total_seconds():.0f}s")
        return out


class PerContractFees(FeeModel):
    """Simulated commission per contract per side, from the same fee table the fee filter uses."""

    def __init__(self, fee_filter: DailyFeeFilter) -> None:
        super().__init__()
        self._fees = fee_filter

    def get_commission(self, order: Any, fill_qty: Any, fill_px: Any, instrument: Any) -> Money:
        root = root_of(instrument.id.symbol.value)
        per_side = self._fees.fees(root).per_side
        return Money(float(per_side) * fill_qty.as_double(), instrument.quote_currency)
