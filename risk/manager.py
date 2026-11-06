"""Portfolio risk manager (build step 1.09).

Fixed rules between every trade decision and the broker. Every entry is checked against all of:

1. kill switch not tripped
2. symbol allowed and active
3. order is well formed (bracket sides, tick grid, size cap)
4. session open and not close to the close
5. news blackout (no new entries within X minutes of a scheduled release)
6. daily fee filter (target ticks x tick value >= 2x round-trip fees)
7. per-trade loss limit (stop distance x tick value x size + fees)
8. daily loss limit (today's P&L minus this trade's risk stays above the limit)
9. buying power with a 30-50% buffer, margin on open + pending + new positions
10. per-symbol and total daily contract quotas
11. max open positions (3 portfolio-wide) and per symbol
12. correlation groups: one exposure per group; cap on groups moving in the same direction

All checks run every time (no short-circuit) so the decision log shows the full picture. No AI
can change these rules or their settings (see ``risk.settings``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final

from engine.calendar import Session
from engine.events import EventCalendar
from engine.instruments import SPECS, CorrelationGroup
from risk.fees import DailyFeeFilter
from risk.killswitch import KillSwitch
from risk.settings import RiskSettings

ZERO: Final = Decimal(0)


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


@dataclass(frozen=True)
class OrderIntent:
    signal_id: str
    root: str
    side: Side
    qty: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    ts: datetime
    strategy: str = ""

    def risk_ticks(self) -> Decimal:
        return abs(self.entry - self.stop) / SPECS[self.root].tick_size

    def target_ticks(self) -> Decimal:
        return abs(self.target - self.entry) / SPECS[self.root].tick_size


@dataclass(frozen=True)
class PositionView:
    root: str
    side: Side
    qty: int
    avg_price: Decimal
    unrealized: Decimal = ZERO


@dataclass(frozen=True)
class AccountView:
    equity: Decimal
    margin_used: Decimal

    @property
    def available(self) -> Decimal:
        return self.equity - self.margin_used


@dataclass(frozen=True)
class PortfolioView:
    account: AccountView
    positions: tuple[PositionView, ...] = ()
    pending: tuple[OrderIntent, ...] = ()
    realized_today: Decimal = ZERO
    contracts_used: Mapping[str, int] = field(default_factory=dict)

    @property
    def unrealized(self) -> Decimal:
        return sum((p.unrealized for p in self.positions), ZERO)

    @property
    def pnl_today(self) -> Decimal:
        return self.realized_today + self.unrealized

    @property
    def contracts_used_total(self) -> int:
        return sum(self.contracts_used.values())

    def exposures(self) -> list[tuple[str, Side]]:
        """(root, side) for open positions and pending entries."""
        return [(p.root, p.side) for p in self.positions if p.qty > 0] + [
            (o.root, o.side) for o in self.pending
        ]


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Decision:
    intent: OrderIntent
    checks: tuple[CheckResult, ...]
    at: datetime

    @property
    def approved(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, object]:
        i = self.intent
        return {
            "at": self.at.isoformat(),
            "approved": self.approved,
            "signal_id": i.signal_id,
            "root": i.root,
            "side": i.side.value,
            "qty": i.qty,
            "entry": str(i.entry),
            "stop": str(i.stop),
            "target": str(i.target),
            "strategy": i.strategy,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks
            ],
        }


MarginTable = Mapping[str, Decimal]


def estimated_margins() -> dict[str, Decimal]:
    return {r: s.est_initial_margin for r, s in SPECS.items()}


class PortfolioRiskManager:
    def __init__(
        self,
        settings: Callable[[], RiskSettings],
        fee_filter: DailyFeeFilter,
        events: EventCalendar,
        kill_switch: KillSwitch,
        margins: MarginTable | None = None,
    ) -> None:
        self._settings = settings
        self._fees = fee_filter
        self._events = events
        self._kill = kill_switch
        self._margins: dict[str, Decimal] = dict(margins or estimated_margins())

    def set_margins(self, margins: MarginTable) -> None:
        """Replace per-contract margins (e.g. as reported by the connected broker)."""
        self._margins.update(margins)

    def margin(self, root: str) -> Decimal:
        return self._margins[root]

    # ----- the entry decision -----------------------------------------------------------------
    def evaluate(
        self, intent: OrderIntent, view: PortfolioView, session: Session | None
    ) -> Decision:
        s = self._settings()
        checks = [
            self._kill_check(),
            self._symbol_check(intent, s),
            self._shape_check(intent, s),
            self._session_check(intent, session, s),
            self._news_check(intent, s),
            self._fee_check(intent, session, s),
            self._per_trade_check(intent, s),
            self._daily_loss_check(intent, view, s),
            self._buying_power_check(intent, view, s),
            self._quota_check(intent, view, s),
            self._position_count_check(intent, view, s),
            self._correlation_check(intent, view, s),
        ]
        return Decision(intent, tuple(checks), intent.ts)

    # ----- monitoring (drives the automatic kill switch) ---------------------------------------
    def breaches(self, view: PortfolioView) -> list[str]:
        s = self._settings()
        out: list[str] = []
        if view.pnl_today <= -s.daily_loss_limit:
            out.append(
                f"daily loss limit reached: P&L {view.pnl_today:.2f} <= -{s.daily_loss_limit}"
            )
        if view.account.equity <= 0:
            out.append("account equity is zero or negative")
        open_positions = sum(1 for p in view.positions if p.qty > 0)
        if open_positions > s.max_open_positions:
            out.append(f"{open_positions} positions open, limit {s.max_open_positions}")
        return out

    # ----- individual checks -------------------------------------------------------------------
    def _kill_check(self) -> CheckResult:
        return CheckResult(
            "kill_switch", not self._kill.tripped, "tripped" if self._kill.tripped else "armed"
        )

    @staticmethod
    def _symbol_check(i: OrderIntent, s: RiskSettings) -> CheckResult:
        spec = SPECS.get(i.root)
        ok = spec is not None and spec.active and i.root in s.allowed_roots
        return CheckResult("symbol", ok, f"{i.root} {'allowed' if ok else 'not allowed'}")

    @staticmethod
    def _shape_check(i: OrderIntent, s: RiskSettings) -> CheckResult:
        spec = SPECS.get(i.root)
        if spec is None:
            return CheckResult("order_shape", False, "unknown symbol")
        problems: list[str] = []
        if not 1 <= i.qty <= s.max_contracts_per_order:
            problems.append(f"size {i.qty} outside 1..{s.max_contracts_per_order}")
        sign = i.side.sign
        if (i.stop - i.entry) * sign >= 0:
            problems.append("stop is not on the losing side of entry")
        if (i.target - i.entry) * sign <= 0:
            problems.append("target is not on the winning side of entry")
        for name, px in (("entry", i.entry), ("stop", i.stop), ("target", i.target)):
            if px % spec.tick_size != 0:
                problems.append(f"{name} {px} not on the {spec.tick_size} tick grid")
        return CheckResult("order_shape", not problems, "; ".join(problems) or "bracket valid")

    @staticmethod
    def _session_check(i: OrderIntent, session: Session | None, s: RiskSettings) -> CheckResult:
        if session is None or not session.contains(i.ts):
            return CheckResult("session", False, "market closed")
        cutoff = session.close - timedelta(minutes=s.no_entry_before_close_min)
        if i.ts >= cutoff:
            return CheckResult(
                "session", False, f"within {s.no_entry_before_close_min} min of the close"
            )
        return CheckResult("session", True, f"open until {session.close:%H:%M} CT")

    def _news_check(self, i: OrderIntent, s: RiskSettings) -> CheckResult:
        ev = self._events.blocking(
            i.root,
            i.ts,
            before=timedelta(minutes=s.news_blackout_before_min),
            after=timedelta(minutes=s.news_blackout_after_min),
        )
        if ev is None:
            return CheckResult("news_blackout", True, "no scheduled release nearby")
        return CheckResult("news_blackout", False, f"{ev.name} at {ev.ts:%H:%M} CT")

    def _fee_check(self, i: OrderIntent, session: Session | None, s: RiskSettings) -> CheckResult:
        trade_date = session.trade_date if session else i.ts.date()
        configured = s.target_ticks.get(i.root)
        if configured is None:
            return CheckResult("fee_filter", False, f"no target ticks configured for {i.root}")
        daily = self._fees.evaluate(i.root, configured, trade_date, s.fee_filter_multiple)
        if not daily.qualifies:
            return CheckResult(
                "fee_filter",
                False,
                f"{i.root} does not qualify today: target ${daily.target_value} < "
                f"{daily.multiple_required}x fees ${daily.round_trip_fees}",
            )
        if i.target_ticks() < daily.min_target_ticks:
            return CheckResult(
                "fee_filter",
                False,
                f"this target is {i.target_ticks()} ticks; needs {daily.min_target_ticks}",
            )
        return CheckResult("fee_filter", True, f"target/fees ratio {daily.ratio}")

    def _trade_risk(self, i: OrderIntent) -> Decimal:
        spec = SPECS[i.root]
        fees = self._fees.fees(i.root).round_trip
        return (i.risk_ticks() * spec.tick_value + fees) * i.qty

    def _per_trade_check(self, i: OrderIntent, s: RiskSettings) -> CheckResult:
        risk = self._trade_risk(i)
        ok = risk <= s.per_trade_loss_limit
        return CheckResult(
            "per_trade_loss", ok, f"risk ${risk:.2f} vs limit ${s.per_trade_loss_limit}"
        )

    def _daily_loss_check(self, i: OrderIntent, v: PortfolioView, s: RiskSettings) -> CheckResult:
        worst = v.pnl_today - self._trade_risk(i)
        ok = worst > -s.daily_loss_limit
        return CheckResult(
            "daily_loss",
            ok,
            f"P&L today ${v.pnl_today:.2f}, after a full stop ${worst:.2f}, "
            f"limit -${s.daily_loss_limit}",
        )

    def _buying_power_check(self, i: OrderIntent, v: PortfolioView, s: RiskSettings) -> CheckResult:
        pending = sum((self.margin(o.root) * o.qty for o in v.pending), ZERO)
        new = self.margin(i.root) * i.qty
        usable = v.account.available * (1 - s.buying_power_buffer)
        ok = pending + new <= usable
        return CheckResult(
            "buying_power",
            ok,
            f"needs ${pending + new:.2f} margin (pending ${pending:.2f}), usable ${usable:.2f} "
            f"after {s.buying_power_buffer:.0%} buffer",
        )

    @staticmethod
    def _quota_check(i: OrderIntent, v: PortfolioView, s: RiskSettings) -> CheckResult:
        used = v.contracts_used.get(i.root, 0)
        quota = s.quota_for(i.root)
        total = v.contracts_used_total
        problems = []
        if used + i.qty > quota:
            problems.append(f"{i.root} quota {used}/{quota} used")
        if total + i.qty > s.daily_contract_quota_total:
            problems.append(f"total quota {total}/{s.daily_contract_quota_total} used")
        return CheckResult(
            "quota",
            not problems,
            "; ".join(problems)
            or f"{i.root} {used}/{quota}, total {total}/{s.daily_contract_quota_total}",
        )

    @staticmethod
    def _position_count_check(i: OrderIntent, v: PortfolioView, s: RiskSettings) -> CheckResult:
        exposures = v.exposures()
        same_symbol = sum(1 for r, _ in exposures if r == i.root)
        problems = []
        if len(exposures) + 1 > s.max_open_positions:
            problems.append(f"{len(exposures)} open/pending, max {s.max_open_positions}")
        if same_symbol + 1 > s.max_positions_per_symbol:
            problems.append(f"{i.root} already has {same_symbol}")
        return CheckResult(
            "position_count", not problems, "; ".join(problems) or f"{len(exposures)} open"
        )

    @staticmethod
    def _correlation_check(i: OrderIntent, v: PortfolioView, s: RiskSettings) -> CheckResult:
        group = SPECS[i.root].correlation_group
        exposures = v.exposures()
        # The same symbol is handled by the position-count check; here: *other* symbols that
        # move with this one (e.g. MES with MNQ/MYM) count as the same exposure.
        in_group = [
            (r, side)
            for r, side in exposures
            if r != i.root and SPECS[r].correlation_group == group
        ]
        if in_group:
            return CheckResult(
                "correlation",
                False,
                f"{group.value} already exposed via {in_group[0][0]} (counts as one position)",
            )
        groups_same_dir: set[CorrelationGroup] = {
            SPECS[r].correlation_group for r, side in exposures if side == i.side
        }
        if len(groups_same_dir) + 1 > s.max_same_direction:
            return CheckResult(
                "correlation",
                False,
                f"{len(groups_same_dir)} groups already {i.side.value}, max {s.max_same_direction}",
            )
        return CheckResult("correlation", True, f"{group.value} free")
