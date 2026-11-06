"""Fees and the daily fee filter (build step 1.08).

A symbol qualifies for a strategy on a given day only if

    target ticks x tick value  >=  fee_filter_multiple (2x) x round-trip fees.

Fee figures are per contract per side, in USD. The defaults are estimates for an Interactive
Brokers account (broker commission + CME Group exchange fee + NFA fee) and are marked as such in
every report; once a broker is connected the filter is recalculated each day from the fees
actually charged on fills (step 1.23).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from engine.instruments import SPECS


@dataclass(frozen=True)
class FeeSchedule:
    commission: Decimal  # broker, per side
    exchange: Decimal  # exchange + clearing, per side
    regulatory: Decimal = Decimal("0.02")  # NFA, per side
    source: str = "estimate"

    @property
    def per_side(self) -> Decimal:
        return self.commission + self.exchange + self.regulatory

    @property
    def round_trip(self) -> Decimal:
        return self.per_side * 2


#: Estimated all-in fees per contract per side (verify against the broker statement).
DEFAULT_FEES: Final[Mapping[str, FeeSchedule]] = {
    "MES": FeeSchedule(Decimal("0.25"), Decimal("0.35")),
    "MNQ": FeeSchedule(Decimal("0.25"), Decimal("0.35")),
    "MYM": FeeSchedule(Decimal("0.25"), Decimal("0.35")),
    "MCL": FeeSchedule(Decimal("0.25"), Decimal("0.50")),
    "MGC": FeeSchedule(Decimal("0.25"), Decimal("0.50")),
    "M6E": FeeSchedule(Decimal("0.25"), Decimal("0.16")),
    "10Y": FeeSchedule(Decimal("0.25"), Decimal("0.25")),
    "MBT": FeeSchedule(Decimal("0.25"), Decimal("2.50")),
}


@dataclass(frozen=True)
class FeeCheck:
    root: str
    trade_date: date
    target_ticks: int
    tick_value: Decimal
    target_value: Decimal
    round_trip_fees: Decimal
    multiple_required: Decimal
    fee_source: str

    @property
    def ratio(self) -> Decimal:
        return (self.target_value / self.round_trip_fees).quantize(Decimal("0.01"))

    @property
    def qualifies(self) -> bool:
        return self.target_value >= self.multiple_required * self.round_trip_fees

    @property
    def min_target_ticks(self) -> int:
        """Smallest target (in ticks) that would qualify."""
        need = self.multiple_required * self.round_trip_fees / self.tick_value
        whole = int(need)
        return whole if Decimal(whole) == need else whole + 1

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "trade_date": self.trade_date.isoformat(),
            "target_ticks": self.target_ticks,
            "tick_value": str(self.tick_value),
            "target_value": str(self.target_value),
            "round_trip_fees": str(self.round_trip_fees),
            "multiple_required": str(self.multiple_required),
            "ratio": str(self.ratio),
            "qualifies": self.qualifies,
            "min_target_ticks": self.min_target_ticks,
            "fee_source": self.fee_source,
        }


def check(
    root: str,
    target_ticks: int,
    trade_date: date,
    multiple: Decimal = Decimal(2),
    fees: Mapping[str, FeeSchedule] = DEFAULT_FEES,
) -> FeeCheck:
    spec = SPECS[root]
    schedule = fees[root]
    return FeeCheck(
        root=root,
        trade_date=trade_date,
        target_ticks=target_ticks,
        tick_value=spec.tick_value,
        target_value=spec.tick_value * target_ticks,
        round_trip_fees=schedule.round_trip,
        multiple_required=multiple,
        fee_source=schedule.source,
    )


class DailyFeeFilter:
    """Recalculated once per trade date (or when fees/targets change), cached in between."""

    def __init__(self, fees: Mapping[str, FeeSchedule] = DEFAULT_FEES) -> None:
        self._fees = dict(fees)
        self._cache: dict[tuple[date, str, int, Decimal], FeeCheck] = {}

    def update_fees(self, root: str, schedule: FeeSchedule) -> None:
        """Replace a fee schedule (e.g. from actual fills); invalidates cached results."""
        self._fees[root] = schedule
        self._cache.clear()

    def fees(self, root: str) -> FeeSchedule:
        return self._fees[root]

    def evaluate(
        self, root: str, target_ticks: int, trade_date: date, multiple: Decimal
    ) -> FeeCheck:
        key = (trade_date, root, target_ticks, multiple)
        if key not in self._cache:
            self._cache[key] = check(root, target_ticks, trade_date, multiple, self._fees)
        return self._cache[key]

    def evaluate_all(
        self, targets: Mapping[str, int], trade_date: date, multiple: Decimal
    ) -> dict[str, FeeCheck]:
        return {r: self.evaluate(r, t, trade_date, multiple) for r, t in targets.items()}
