"""Contract definitions (build step 1.04) and expiry / rollover rules (step 1.05).

Active in Phase 1: MES, MCL, MGC, M6E, 10Y. Configured but off: MNQ, MYM, MBT (DECISIONS.md A-02,
A-03). Tick sizes, multipliers, listing cycles and last-trade rules follow the CME Group contract
specifications. Margin figures are *estimates* for sizing in demo mode; the live risk manager
uses the broker's reported margin once a broker is connected (step 1.23).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final

from engine.calendar import CT, ProductGroup, SessionCalendar

MONTH_CODES: Final = "FGHJKMNQUVXZ"  # Jan..Dec
QUARTERLY: Final = "HMUZ"
ALL_MONTHS: Final = MONTH_CODES
MGC_CYCLE: Final = "GJMQVZ"  # Feb, Apr, Jun, Aug, Oct, Dec


class AssetClass(StrEnum):
    EQUITY_INDEX = "equity_index"
    ENERGY = "energy"
    METALS = "metals"
    FX = "fx"
    RATES = "rates"
    CRYPTO = "crypto"


class CorrelationGroup(StrEnum):
    """Symbols in one group count as one exposure in the risk manager (DECISIONS.md A-04)."""

    US_EQUITY_INDEX = "us_equity_index"
    CRUDE = "crude"
    GOLD = "gold"
    EURO = "euro"
    US_RATES = "us_rates"
    CRYPTO = "crypto"


ExpiryRule = Callable[[SessionCalendar, int, int], date]
RollRule = Callable[[SessionCalendar, int, int, date], date]


# ----- expiry rules (last trading day of the contract month) ---------------------------------
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + month // 12, month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def third_friday(cal: SessionCalendar, year: int, month: int) -> date:
    """Equity index (MES, MNQ, MYM): 3rd Friday of the contract month."""
    return cal.previous_or_same_business_day(_nth_weekday(year, month, 4, 3))


def two_days_before_third_wednesday(cal: SessionCalendar, year: int, month: int) -> date:
    """FX (M6E): 2 business days before the 3rd Wednesday of the contract month."""
    return cal.add_business_days(_nth_weekday(year, month, 2, 3), -2)


def mcl_last_trade(cal: SessionCalendar, year: int, month: int) -> date:
    """MCL: 4 business days before the 25th calendar day of the prior month (5 if the 25th is
    not a business day)."""
    py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
    the_25th = date(py, pm, 25)
    return cal.add_business_days(the_25th, -4 if cal.is_business_day(the_25th) else -5)


def third_last_business_day(cal: SessionCalendar, year: int, month: int) -> date:
    """Gold (MGC): 3rd-last business day of the contract month."""
    return cal.add_business_days(cal.last_business_day_of_month(year, month), -2)


def last_business_day(cal: SessionCalendar, year: int, month: int) -> date:
    """Micro Treasury yield (10Y): last business day of the contract month."""
    return cal.last_business_day_of_month(year, month)


def last_friday(cal: SessionCalendar, year: int, month: int) -> date:
    """Micro Bitcoin (MBT): last Friday of the contract month."""
    return cal.previous_or_same_business_day(_last_weekday(year, month, 4))


# ----- roll rules (date the system switches to the next contract) ----------------------------
def roll_before_expiry(n: int) -> RollRule:
    def rule(cal: SessionCalendar, _year: int, _month: int, expiry: date) -> date:
        return cal.add_business_days(expiry, -n)

    return rule


def roll_before_first_notice(n: int) -> RollRule:
    """Physically delivered (MGC): roll ``n`` business days before first notice day, which is
    the last business day of the month before the contract month."""

    def rule(cal: SessionCalendar, year: int, month: int, _expiry: date) -> date:
        py, pm = (year - 1, 12) if month == 1 else (year, month - 1)
        return cal.add_business_days(cal.last_business_day_of_month(py, pm), -n)

    return rule


@dataclass(frozen=True)
class ContractSpec:
    root: str
    name: str
    exchange: str  # ISO 10383 MIC
    asset_class: AssetClass
    calendar_group: ProductGroup
    correlation_group: CorrelationGroup
    tick_size: Decimal
    multiplier: Decimal
    price_precision: int
    listing_cycle: str
    expiry_rule: ExpiryRule
    roll_rule: RollRule
    last_trade_time_ct: time
    active: bool
    #: Estimated exchange initial margin per contract, USD (demo sizing only; see module doc).
    est_initial_margin: Decimal
    #: Estimated intraday (day-trade) margin per contract, USD.
    est_day_margin: Decimal
    #: Typical price level, used to seed sample data and simulated margin ratios.
    reference_price: Decimal
    currency: str = "USD"

    @property
    def tick_value(self) -> Decimal:
        return self.tick_size * self.multiplier

    @property
    def calendar(self) -> SessionCalendar:
        return SessionCalendar(self.calendar_group)


SPECS: Final[dict[str, ContractSpec]] = {
    s.root: s
    for s in (
        ContractSpec(
            "MES",
            "Micro E-mini S&P 500",
            "XCME",
            AssetClass.EQUITY_INDEX,
            ProductGroup.EQUITY_INDEX,
            CorrelationGroup.US_EQUITY_INDEX,
            Decimal("0.25"),
            Decimal(5),
            2,
            QUARTERLY,
            third_friday,
            roll_before_expiry(6),
            time(8, 30),
            active=True,
            est_initial_margin=Decimal(2400),
            est_day_margin=Decimal(600),
            reference_price=Decimal(6000),
        ),
        ContractSpec(
            "MCL",
            "Micro WTI Crude Oil",
            "XNYM",
            AssetClass.ENERGY,
            ProductGroup.CRUDE,
            CorrelationGroup.CRUDE,
            Decimal("0.01"),
            Decimal(100),
            2,
            ALL_MONTHS,
            mcl_last_trade,
            roll_before_expiry(3),
            time(13, 30),
            active=True,
            est_initial_margin=Decimal(700),
            est_day_margin=Decimal(175),
            reference_price=Decimal(70),
        ),
        ContractSpec(
            "MGC",
            "Micro Gold",
            "XCEC",
            AssetClass.METALS,
            ProductGroup.METALS,
            CorrelationGroup.GOLD,
            Decimal("0.1"),
            Decimal(10),
            1,
            MGC_CYCLE,
            third_last_business_day,
            roll_before_first_notice(3),
            time(12, 30),
            active=True,
            est_initial_margin=Decimal(2500),
            est_day_margin=Decimal(625),
            reference_price=Decimal(2700),
        ),
        ContractSpec(
            "M6E",
            "Micro EUR/USD",
            "XCME",
            AssetClass.FX,
            ProductGroup.FX,
            CorrelationGroup.EURO,
            Decimal("0.0001"),
            Decimal(12_500),
            4,
            QUARTERLY,
            two_days_before_third_wednesday,
            roll_before_expiry(6),
            time(9, 16),
            active=True,
            est_initial_margin=Decimal(300),
            est_day_margin=Decimal(75),
            reference_price=Decimal("1.08"),
        ),
        ContractSpec(
            "10Y",
            "Micro 10-Year Yield",
            "XCBT",
            AssetClass.RATES,
            ProductGroup.INTEREST_RATE,
            CorrelationGroup.US_RATES,
            Decimal("0.001"),
            Decimal(1000),
            3,
            ALL_MONTHS,
            last_business_day,
            roll_before_expiry(3),
            time(12, 0),
            active=True,
            est_initial_margin=Decimal(350),
            est_day_margin=Decimal(90),
            reference_price=Decimal("4.250"),
        ),
        ContractSpec(
            "MNQ",
            "Micro E-mini Nasdaq-100",
            "XCME",
            AssetClass.EQUITY_INDEX,
            ProductGroup.EQUITY_INDEX,
            CorrelationGroup.US_EQUITY_INDEX,
            Decimal("0.25"),
            Decimal(2),
            2,
            QUARTERLY,
            third_friday,
            roll_before_expiry(6),
            time(8, 30),
            active=False,
            est_initial_margin=Decimal(3500),
            est_day_margin=Decimal(875),
            reference_price=Decimal(21000),
        ),
        ContractSpec(
            "MYM",
            "Micro E-mini Dow",
            "XCBT",
            AssetClass.EQUITY_INDEX,
            ProductGroup.EQUITY_INDEX,
            CorrelationGroup.US_EQUITY_INDEX,
            Decimal(1),
            Decimal("0.5"),
            0,
            QUARTERLY,
            third_friday,
            roll_before_expiry(6),
            time(8, 30),
            active=False,
            est_initial_margin=Decimal(1700),
            est_day_margin=Decimal(425),
            reference_price=Decimal(44000),
        ),
        ContractSpec(
            "MBT",
            "Micro Bitcoin",
            "XCME",
            AssetClass.CRYPTO,
            ProductGroup.CRYPTO,
            CorrelationGroup.CRYPTO,
            Decimal(5),
            Decimal("0.1"),
            0,
            ALL_MONTHS,
            last_friday,
            roll_before_expiry(4),
            time(15, 0),
            active=False,
            est_initial_margin=Decimal(3500),
            est_day_margin=Decimal(875),
            reference_price=Decimal(95000),
        ),
    )
}

ACTIVE_ROOTS: Final = tuple(r for r, s in SPECS.items() if s.active)
CONFIGURED_OFF_ROOTS: Final = tuple(r for r, s in SPECS.items() if not s.active)


@dataclass(frozen=True)
class Contract:
    spec: ContractSpec
    year: int
    month: int
    last_trade_date: date
    roll_date: date

    @property
    def symbol(self) -> str:
        """Exchange symbol as Databento/Globex write it, e.g. ``MESZ6``."""
        return f"{self.spec.root}{MONTH_CODES[self.month - 1]}{self.year % 10}"

    @property
    def expiration(self) -> datetime:
        return datetime.combine(self.last_trade_date, self.spec.last_trade_time_ct, tzinfo=CT)


def contract(root: str, year: int, month: int) -> Contract:
    spec = SPECS[root]
    if MONTH_CODES[month - 1] not in spec.listing_cycle:
        raise ValueError(f"{root} does not list a {MONTH_CODES[month - 1]} contract")
    cal = spec.calendar
    ltd = spec.expiry_rule(cal, year, month)
    return Contract(spec, year, month, ltd, spec.roll_rule(cal, year, month, ltd))


def _contracts_from(root: str, start: date, count: int) -> list[Contract]:
    spec = SPECS[root]
    out: list[Contract] = []
    y, m = start.year, start.month
    while len(out) < count:
        if MONTH_CODES[m - 1] in spec.listing_cycle:
            out.append(contract(root, y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def front_contract(root: str, on: date) -> Contract:
    """The contract the system trades on trade date ``on``: the nearest one whose roll date
    is after ``on`` (so trading moves to the next contract on its roll date)."""
    for c in _contracts_from(root, date(on.year, on.month, 1) - timedelta(days=62), 8):
        if c.roll_date > on:
            return c
    raise LookupError(f"no {root} contract found for {on}")


def roll_schedule(root: str, start: date, end: date) -> list[Contract]:
    """Contracts traded between ``start`` and ``end``, in order, with their roll dates."""
    out: list[Contract] = []
    current = front_contract(root, start)
    while True:
        out.append(current)
        if current.roll_date > end:
            return out
        current = front_contract(root, current.roll_date)
