"""CME session calendar (build step 1.05).

* Trading sessions per product group from the maintained ``pandas_market_calendars`` CME Globex
  calendars: regular hours, holidays and shortened sessions.
* All times are handled in exchange time (``America/Chicago``) with automatic daylight-saving
  handling; values crossing a module boundary are timezone-aware.
* Business-day arithmetic for contract expiry and rollover rules (``engine.instruments``).

A CME *trade date* runs from the prior evening's open (17:00 CT) to the close (normally 16:00 CT),
with a daily maintenance break from 16:00 to 17:00 CT.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from functools import cache
from typing import Any, Final
from zoneinfo import ZoneInfo

import pandas as pd

CT: Final = ZoneInfo("America/Chicago")
UTC: Final = ZoneInfo("UTC")

#: Regular-trading-hours open used for prior-day / overnight split (equity index cash open).
RTH_OPEN_CT: Final = time(8, 30)
#: Normal Globex daily close and reopen.
GLOBEX_CLOSE_CT: Final = time(16, 0)
GLOBEX_OPEN_CT: Final = time(17, 0)


class ProductGroup(StrEnum):
    """Session-calendar groups; each maps to one maintained CME Globex calendar."""

    EQUITY_INDEX = "CME Globex Equity"
    FX = "CMEGlobex_FX"
    CRUDE = "CMEGlobex_MCL"
    METALS = "CMEGlobex_Gold"
    INTEREST_RATE = "CME Globex Fixed Income"
    CRYPTO = "CME Globex Crypto"


@dataclass(frozen=True)
class Session:
    """One CME trade date: [open, close) as timezone-aware datetimes in Central Time."""

    trade_date: date
    open: datetime
    close: datetime

    @property
    def is_shortened(self) -> bool:
        return self.close.timetz().replace(tzinfo=None) != GLOBEX_CLOSE_CT

    def contains(self, ts: datetime) -> bool:
        return self.open <= ts.astimezone(CT) < self.close

    @property
    def rth_open(self) -> datetime:
        """08:30 CT on the trade date; splits the overnight (ETH) and regular session."""
        return datetime.combine(self.trade_date, RTH_OPEN_CT, tzinfo=CT)


def _require_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime: pass a timezone-aware value")
    return ts


@cache
def _calendar(group: ProductGroup) -> Any:  # pandas_market_calendars ships no type hints
    import pandas_market_calendars as mcal  # noqa: PLC0415 - heavy import, loaded on first use

    return mcal.get_calendar(group.value)


@cache
def _year_sessions(group: ProductGroup, year: int) -> tuple[Session, ...]:
    """Every session whose trade date falls in ``year`` (cached; holidays are fixed per year)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        frame: pd.DataFrame = _calendar(group).schedule(f"{year}-01-01", f"{year}-12-31")
    out: list[Session] = []
    for idx, open_ts, close_ts in zip(
        frame.index, frame["market_open"], frame["market_close"], strict=True
    ):
        out.append(
            Session(
                trade_date=pd.Timestamp(idx).date(),
                open=pd.Timestamp(open_ts).to_pydatetime().astimezone(CT),
                close=pd.Timestamp(close_ts).to_pydatetime().astimezone(CT),
            )
        )
    return tuple(out)


class SessionCalendar:
    """Sessions, business days and session lookups for one product group."""

    def __init__(self, group: ProductGroup) -> None:
        self.group = group

    # ----- sessions ---------------------------------------------------------------------------
    def sessions(self, start: date, end: date) -> list[Session]:
        """All sessions whose trade date falls in [start, end], in order."""
        return [
            sess
            for year in range(start.year, end.year + 1)
            for sess in _year_sessions(self.group, year)
            if start <= sess.trade_date <= end
        ]

    def session_for(self, trade_date: date) -> Session | None:
        found = self.sessions(trade_date, trade_date)
        return found[0] if found else None

    def session_at(self, ts: datetime) -> Session | None:
        """The session open at ``ts``, or None during a break, weekend or holiday."""
        ct = _require_aware(ts).astimezone(CT)
        for s in self.sessions(ct.date() - timedelta(days=1), ct.date() + timedelta(days=1)):
            if s.contains(ct):
                return s
        return None

    def is_open(self, ts: datetime) -> bool:
        return self.session_at(ts) is not None

    def next_open(self, ts: datetime) -> datetime:
        """Next session open strictly after ``ts`` (or ``ts`` itself if a session is open)."""
        ct = _require_aware(ts).astimezone(CT)
        if self.is_open(ct):
            return ct
        for s in self.sessions(ct.date() - timedelta(days=1), ct.date() + timedelta(days=14)):
            if s.open > ct:
                return s.open
        raise LookupError(f"no {self.group} session within 14 days of {ct}")

    def previous_session(self, trade_date: date) -> Session:
        for s in reversed(self.sessions(trade_date - timedelta(days=10), trade_date)):
            if s.trade_date < trade_date:
                return s
        raise LookupError(f"no {self.group} session in the 10 days before {trade_date}")

    # ----- business days ----------------------------------------------------------------------
    def is_business_day(self, d: date) -> bool:
        return self.session_for(d) is not None

    def business_days(self, start: date, end: date) -> list[date]:
        return [s.trade_date for s in self.sessions(start, end)]

    def add_business_days(self, d: date, n: int) -> date:
        """Move ``n`` business days from ``d`` (negative = backwards). ``n=0`` returns ``d``."""
        if n == 0:
            return d
        step = 1 if n > 0 else -1
        window = timedelta(days=abs(n) * 3 + 14)
        lo, hi = (d, d + window) if step > 0 else (d - window, d)
        days = [x for x in self.business_days(lo, hi) if (x > d if step > 0 else x < d)]
        if step < 0:
            days.reverse()
        if len(days) < abs(n):
            raise LookupError(f"not enough business days around {d}")
        return days[abs(n) - 1]

    def previous_or_same_business_day(self, d: date) -> date:
        return d if self.is_business_day(d) else self.add_business_days(d, -1)

    def last_business_day_of_month(self, year: int, month: int) -> date:
        first_next = date(year + (month // 12), month % 12 + 1, 1)
        return self.add_business_days(first_next, -1)


def to_ct(ts: datetime) -> datetime:
    """Convert an aware datetime to exchange time."""
    return _require_aware(ts).astimezone(CT)


def from_unix_ns(ns: int) -> datetime:
    """Nautilus timestamps (UNIX nanoseconds, UTC) to an aware CT datetime."""
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC).astimezone(CT)


def to_unix_ns(ts: datetime) -> int:
    aware = _require_aware(ts)
    return int(pd.Timestamp(aware).value)
