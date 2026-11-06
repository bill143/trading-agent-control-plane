"""Starter strategy (build step 1.13): one instance per contract (the "symbol engine").

Purpose: prove the full chain end to end on every symbol (data -> state -> signal -> risk
manager -> bracket order -> fills -> P&L -> journal). It is a plain, explainable rule with no
claimed edge; real strategies arrive with the expert AIs in Phase 4.

Rule (VWAP stretch reversion, regular hours only):
* LONG when price is at least ``deviation_atr`` ATRs below session VWAP **and** the book leans to
  the bid (buyers stepping in); SHORT on the mirror image.
* Bracket sent together (step 1.12): market entry, stop ``stop_atr`` ATRs away (at least
  ``min_stop_ticks``), take-profit at the configured target ticks (which pass the fee filter).
* One position per symbol, cooldown after each exit, every signal evaluated once.

Order safety (step 1.12): every signal has a deterministic ID; the persistent signal ledger lets
an ID produce at most one order, ever. Nothing is sent until restart reconciliation has passed.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import (
    OrderCanceled,
    OrderDenied,
    OrderFilled,
    OrderRejected,
    PositionChanged,
    PositionClosed,
    PositionOpened,
)
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from api.audit import AuditCategory
from engine.calendar import CT, from_unix_ns
from engine.context import EngineContext, PositionRecord
from engine.instruments import SPECS
from engine.nautilus.portfolio import portfolio_view
from engine.state import SymbolTracker
from risk.manager import OrderIntent, Side


class StarterConfig(StrategyConfig, frozen=True):
    root: str
    instrument_id: InstrumentId
    trade_size: int = 1
    deviation_atr: float = 2.0
    lean_threshold: float = 0.15
    stop_atr: float = 1.0
    min_stop_ticks: int = 4
    cooldown_minutes: int = 10
    entry_start_ct: str = "08:35"
    entry_end_ct: str = "14:45"


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


class StarterStrategy(Strategy):
    def __init__(self, config: StarterConfig, ctx: EngineContext) -> None:
        super().__init__(config)
        self.root = config.root
        self.iid = config.instrument_id
        self.ctx = ctx
        self.spec = SPECS[config.root]
        quota = ctx.settings.quota_for(config.root)
        self.tracker = ctx.store.add(
            SymbolTracker(config.root, config.instrument_id.symbol.value, quota, ctx.sample_data)
        )
        self._entry_start = _hhmm(config.entry_start_ct)
        self._entry_end = _hhmm(config.entry_end_ct)
        self._evaluated: set[str] = set()
        self._entry_orders: dict[str, str] = {}  # client order id -> signal id
        self._cooldown_until: datetime | None = None
        self.signals_seen = 0
        self.orders_sent = 0

    # ----- lifecycle --------------------------------------------------------------------------
    def on_start(self) -> None:
        self.subscribe_quote_ticks(self.iid)
        self.subscribe_trade_ticks(self.iid)

    def on_stop(self) -> None:
        self.unsubscribe_quote_ticks(self.iid)
        self.unsubscribe_trade_ticks(self.iid)

    # ----- market data ------------------------------------------------------------------------
    def on_quote_tick(self, tick: QuoteTick) -> None:
        with self.ctx.store.lock:
            self.tracker.on_quote(
                tick.ts_event,
                tick.bid_price.as_double(),
                tick.ask_price.as_double(),
                tick.bid_size.as_double(),
                tick.ask_size.as_double(),
            )
            self._refresh_position()
        self._maybe_signal(tick.ts_event)

    def on_trade_tick(self, tick: TradeTick) -> None:
        with self.ctx.store.lock:
            self.tracker.on_trade(tick.ts_event, tick.price.as_double(), tick.size.as_double())

    # ----- signal ---------------------------------------------------------------------------------
    def _ready(self, now: datetime) -> bool:
        """Trading allowed for this symbol right now (before looking at the market)."""
        if not self.ctx.reconciled.is_set() or self.ctx.kill.tripped:
            return False
        if not self._entry_start <= now.astimezone(CT).time() <= self._entry_end:
            return False
        if self._cooldown_until and now < self._cooldown_until:
            return False
        return self.tracker.position == 0 and not self._pending_for_root()

    def _side(self) -> tuple[Side, float] | None:
        """Signal side and ATR if the VWAP-stretch rule fires on the current state."""
        t = self.tracker
        atr, vwap, lean, mid = t.atr, t.vwap, t.book_lean, t.mid
        if atr is None or vwap is None or lean is None or mid is None or atr <= 0:
            return None
        cfg: StarterConfig = self.config
        dev = (mid - vwap) / atr
        if dev <= -cfg.deviation_atr and lean >= cfg.lean_threshold:
            return Side.LONG, atr
        if dev >= cfg.deviation_atr and lean <= -cfg.lean_threshold:
            return Side.SHORT, atr
        return None

    def _maybe_signal(self, ts_ns: int) -> None:
        now = from_unix_ns(ts_ns)
        if not self._ready(now):
            return
        fired = self._side()
        if fired is None:
            return
        side, atr = fired
        signal_id = f"{self.root}:{side.value}:{ts_ns // 60_000_000_000}"
        if signal_id in self._evaluated:
            return
        self._evaluated.add(signal_id)
        self.signals_seen += 1
        self.submit_signal(self.build_intent(signal_id, side, now, atr))

    def build_intent(self, signal_id: str, side: Side, now: datetime, atr: float) -> OrderIntent:
        cfg: StarterConfig = self.config
        tick = self.spec.tick_size
        entry = Decimal(str(self.tracker.ask if side is Side.LONG else self.tracker.bid))
        stop_dist = max(Decimal(str(atr * cfg.stop_atr)), tick * cfg.min_stop_ticks)
        stop_ticks = (stop_dist / tick).to_integral_value(rounding=ROUND_CEILING)
        target_ticks = Decimal(self.ctx.settings.target_ticks.get(self.root, 1))
        stop = entry - side.sign * stop_ticks * tick
        target = entry + side.sign * target_ticks * tick
        return OrderIntent(
            signal_id=signal_id,
            root=self.root,
            side=side,
            qty=cfg.trade_size,
            entry=_snap(entry, tick),
            stop=_snap(stop, tick),
            target=_snap(target, tick),
            ts=now,
            strategy="starter_vwap_reversion",
        )

    def submit_signal(self, intent: OrderIntent) -> bool:
        """Risk-check and send ``intent``. Returns True if an order was sent.

        The ledger guarantees a signal ID can produce at most one order, even if this is
        called twice with the same signal or the engine restarts in between.
        """
        if intent.signal_id in self.ctx.ledger:
            self.ctx.audit.record(
                AuditCategory.ORDER,
                "duplicate_signal_blocked",
                self.root,
                {"signal_id": intent.signal_id},
            )
            return False
        view = portfolio_view(self, self.ctx)
        decision = self.ctx.risk.evaluate(intent, view, self.tracker.session)
        if not decision.approved:
            self.ctx.decisions.add(decision, "refused")
            return False
        if not self.ctx.ledger.claim(intent.signal_id):
            self.ctx.decisions.add(decision, "duplicate")
            return False
        self.ctx.decisions.add(decision, "sent")
        self._send_bracket(intent)
        return True

    def _send_bracket(self, intent: OrderIntent) -> None:
        inst = self.cache.instrument(self.iid)
        prec = inst.price_precision
        order_list = self.order_factory.bracket(
            instrument_id=self.iid,
            order_side=OrderSide.BUY if intent.side is Side.LONG else OrderSide.SELL,
            quantity=Quantity.from_int(intent.qty),
            sl_trigger_price=Price(float(intent.stop), prec),
            tp_price=Price(float(intent.target), prec),
            time_in_force=TimeInForce.GTC,
            entry_tags=[f"signal={intent.signal_id}"],
        )
        entry = order_list.first
        self._entry_orders[entry.client_order_id.value] = intent.signal_id
        with self.ctx.store.lock:
            self.ctx.pending[intent.signal_id] = intent
        self.submit_order_list(order_list)
        self.orders_sent += 1
        self.ctx.orders_submitted += 1
        self.ctx.audit.record(
            AuditCategory.ORDER,
            "bracket_submitted",
            self.root,
            {
                "signal_id": intent.signal_id,
                "side": intent.side.value,
                "qty": intent.qty,
                "entry": str(intent.entry),
                "stop": str(intent.stop),
                "target": str(intent.target),
                "orders": [o.client_order_id.value for o in order_list.orders],
            },
        )

    def _pending_for_root(self) -> bool:
        return any(i.root == self.root for i in self.ctx.pending.values())

    # ----- order and position events --------------------------------------------------------------
    def _clear_pending(self, client_order_id: str) -> None:
        signal_id = self._entry_orders.pop(client_order_id, None)
        if signal_id is not None:
            with self.ctx.store.lock:
                self.ctx.pending.pop(signal_id, None)

    def on_order_filled(self, event: OrderFilled) -> None:
        coid = event.client_order_id.value
        if coid in self._entry_orders:
            with self.ctx.store.lock:
                self.tracker.add_contracts(int(event.last_qty.as_double()))
            self._clear_pending(coid)
        self.ctx.audit.record(
            AuditCategory.ORDER,
            "fill",
            self.root,
            {
                "order": coid,
                "side": event.order_side.name,
                "qty": event.last_qty.as_double(),
                "price": event.last_px.as_double(),
                "commission": str(event.commission),
            },
        )

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._clear_pending(event.client_order_id.value)

    def on_order_denied(self, event: OrderDenied) -> None:
        self._clear_pending(event.client_order_id.value)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self._clear_pending(event.client_order_id.value)

    def on_position_opened(self, event: PositionOpened) -> None:
        self._refresh_position()
        self._save_journal()

    def on_position_changed(self, event: PositionChanged) -> None:
        self._refresh_position()
        self._save_journal()

    def on_position_closed(self, event: PositionClosed) -> None:
        with self.ctx.store.lock:
            self.tracker.add_realized(float(event.realized_pnl.as_double()))
            self._refresh_position()
        self._cooldown_until = from_unix_ns(event.ts_event) + timedelta(
            minutes=self.config.cooldown_minutes
        )
        self._save_journal()
        # Close the remaining bracket leg so no orphan order stays working.
        for order in self.cache.orders_open(instrument_id=self.iid, strategy_id=self.id):
            self.cancel_order(order)

    def _refresh_position(self) -> None:
        net = self.portfolio.net_position(self.iid)
        qty = int(net) if net is not None else 0
        avg: float | None = None
        unrealized = 0.0
        if qty:
            positions = self.cache.positions_open(instrument_id=self.iid)
            if positions:
                avg = float(positions[0].avg_px_open)
            pnl: Any = self.portfolio.unrealized_pnl(self.iid)
            unrealized = float(pnl.as_double()) if pnl is not None else 0.0
        self.tracker.set_position(qty, avg, unrealized)

    def _save_journal(self) -> None:
        records = {}
        for pos in self.cache.positions_open():
            records[pos.instrument_id.symbol.value] = PositionRecord(
                pos.instrument_id.symbol.value, int(pos.signed_qty), float(pos.avg_px_open)
            )
        self.ctx.journal.save(records)

    # ----- kill switch ----------------------------------------------------------------------------
    def flatten(self) -> None:
        """Cancel every working order and close every position for this symbol."""
        self.cancel_all_orders(self.iid)
        self.close_all_positions(self.iid)
        with self.ctx.store.lock:
            for sid in [s for s, i in self.ctx.pending.items() if i.root == self.root]:
                self.ctx.pending.pop(sid, None)


def _snap(px: Decimal, tick: Decimal) -> Decimal:
    return (px / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
