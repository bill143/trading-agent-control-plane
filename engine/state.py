"""Live state per symbol (build step 1.07).

For each contract: price, spread, book lean, ATR, VWAP, prior-day and overnight levels, position,
P&L and contracts used today. Pure Python, fed by the Nautilus adapter (quotes, trades,
positions) and read by the strategy, the risk manager and the control API.

Definitions:
* **book lean** = (bid size - ask size) / (bid size + ask size) at the top of book, smoothed over
  the last 20 updates; +1 means all size is on the bid (buyers leaning in).
* **ATR** = Wilder's 14-period average true range of 1-minute mid-price bars.
* **VWAP** = session volume-weighted average trade price, reset at the session open (17:00 CT);
  the band is one volume-weighted standard deviation.
* **prior day** = high / low / last of the previous CME session.
* **overnight** = high / low from the session open to 08:30 CT.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from engine.calendar import CT, Session, SessionCalendar, from_unix_ns
from engine.instruments import SPECS, ContractSpec

ATR_PERIOD: Final = 14
LEAN_WINDOW: Final = 20


@dataclass
class _Bar:
    minute: int  # unix minute
    open: float
    high: float
    low: float
    close: float


@dataclass
class _SessionStats:
    session: Session
    high: float = -math.inf
    low: float = math.inf
    last: float = math.nan
    on_high: float = -math.inf
    on_low: float = math.inf
    pv: float = 0.0
    p2v: float = 0.0
    volume: float = 0.0
    contracts_used: int = 0
    realized: float = 0.0

    def vwap(self) -> float | None:
        return self.pv / self.volume if self.volume > 0 else None

    def vwap_sd(self) -> float | None:
        if self.volume <= 0:
            return None
        mean = self.pv / self.volume
        return math.sqrt(max(self.p2v / self.volume - mean * mean, 0.0))


@dataclass(frozen=True)
class SymbolSnapshot:
    """Plain-data view of one symbol, safe to hand to other threads and to the API."""

    root: str
    symbol: str
    name: str
    trade_date: date | None
    session_open: bool
    ts: datetime | None
    bid: float | None
    ask: float | None
    price: float | None
    spread_ticks: float | None
    book_lean: float | None
    atr: float | None
    atr_ticks: float | None
    vwap: float | None
    vwap_band: float | None
    session_high: float | None
    session_low: float | None
    prior_high: float | None
    prior_low: float | None
    prior_close: float | None
    overnight_high: float | None
    overnight_low: float | None
    position: int  # signed contracts
    avg_price: float | None
    unrealized_pnl: float
    realized_pnl: float
    contracts_used: int
    contracts_quota: int
    data_age_seconds: float | None
    price_precision: int
    sample_data: bool = False
    extra: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for k, v in self.__dict__.items():
            if isinstance(v, date):  # also covers datetime
                out[k] = v.isoformat()
            elif isinstance(v, float):
                out[k] = None if math.isnan(v) or math.isinf(v) else round(v, 8)
            else:
                out[k] = v
        return out


def _finite(x: float) -> float | None:
    return None if math.isnan(x) or math.isinf(x) else x


class SymbolTracker:
    """Tracks one contract. Not thread-safe by itself; :class:`StateStore` guards access."""

    def __init__(self, root: str, symbol: str, quota: int = 0, sample_data: bool = False) -> None:
        self.root = root
        self.symbol = symbol
        self.spec: ContractSpec = SPECS[root]
        self.calendar = SessionCalendar(self.spec.calendar_group)
        self.quota = quota
        self.sample_data = sample_data
        self._tick = float(self.spec.tick_size)
        self._cur: _SessionStats | None = None
        self._prior: _SessionStats | None = None
        self._bar: _Bar | None = None
        self._tr: deque[float] = deque(maxlen=ATR_PERIOD)
        self._atr: float | None = None
        self._prev_close: float | None = None
        self._lean: deque[float] = deque(maxlen=LEAN_WINDOW)
        self.bid = self.ask = self.last_trade = math.nan
        self.last_ts: datetime | None = None
        self.position = 0
        self.avg_price: float | None = None
        self.unrealized = 0.0

    # ----- session handling -------------------------------------------------------------------
    def _session_for(self, ts: datetime) -> _SessionStats | None:
        cur = self._cur
        if cur is not None and cur.session.contains(ts):
            return cur
        session = self.calendar.session_at(ts)
        if session is None:
            return None
        if cur is None or session.trade_date != cur.session.trade_date:
            if cur is not None and not math.isnan(cur.last):
                self._prior = cur
            self._cur = _SessionStats(session)
            self._bar = None
        return self._cur

    # ----- market data ------------------------------------------------------------------------
    def on_quote(
        self, ts_ns: int, bid: float, ask: float, bid_size: float, ask_size: float
    ) -> None:
        ts = from_unix_ns(ts_ns)
        stats = self._session_for(ts)
        self.bid, self.ask, self.last_ts = bid, ask, ts
        total = bid_size + ask_size
        if total > 0:
            self._lean.append((bid_size - ask_size) / total)
        if stats is None:
            return
        mid = (bid + ask) / 2
        self._update_levels(stats, ts, mid)
        self._update_bar(ts_ns, mid)

    def on_trade(self, ts_ns: int, price: float, size: float) -> None:
        ts = from_unix_ns(ts_ns)
        stats = self._session_for(ts)
        self.last_trade, self.last_ts = price, ts
        if stats is None:
            return
        stats.pv += price * size
        stats.p2v += price * price * size
        stats.volume += size
        self._update_levels(stats, ts, price)

    def _update_levels(self, stats: _SessionStats, ts: datetime, px: float) -> None:
        stats.high = max(stats.high, px)
        stats.low = min(stats.low, px)
        stats.last = px
        if ts < stats.session.rth_open:
            stats.on_high = max(stats.on_high, px)
            stats.on_low = min(stats.on_low, px)

    def _update_bar(self, ts_ns: int, mid: float) -> None:
        minute = ts_ns // 60_000_000_000
        bar = self._bar
        if bar is None or minute != bar.minute:
            if bar is not None:
                self._close_bar(bar)
            self._bar = _Bar(minute, mid, mid, mid, mid)
            return
        bar.high = max(bar.high, mid)
        bar.low = min(bar.low, mid)
        bar.close = mid

    def _close_bar(self, bar: _Bar) -> None:
        prev = self._prev_close if self._prev_close is not None else bar.open
        tr = max(bar.high - bar.low, abs(bar.high - prev), abs(bar.low - prev))
        if self._atr is None:
            self._tr.append(tr)
            if len(self._tr) == ATR_PERIOD:
                self._atr = sum(self._tr) / ATR_PERIOD
        else:
            self._atr = (self._atr * (ATR_PERIOD - 1) + tr) / ATR_PERIOD
        self._prev_close = bar.close

    # ----- position and account updates (from the engine portfolio) ---------------------------
    def set_position(self, signed_qty: int, avg_price: float | None, unrealized: float) -> None:
        self.position = signed_qty
        self.avg_price = avg_price if signed_qty else None
        self.unrealized = unrealized if signed_qty else 0.0

    def add_realized(self, pnl: float) -> None:
        if self._cur is not None:
            self._cur.realized += pnl

    def add_contracts(self, qty: int) -> None:
        if self._cur is not None:
            self._cur.contracts_used += qty

    # ----- derived values -------------------------------------------------------------------------
    @property
    def mid(self) -> float | None:
        return _finite((self.bid + self.ask) / 2)

    @property
    def price(self) -> float | None:
        return self.mid if self.mid is not None else _finite(self.last_trade)

    @property
    def atr(self) -> float | None:
        return self._atr

    @property
    def book_lean(self) -> float | None:
        return sum(self._lean) / len(self._lean) if self._lean else None

    @property
    def vwap(self) -> float | None:
        return self._cur.vwap() if self._cur else None

    @property
    def vwap_band(self) -> float | None:
        return self._cur.vwap_sd() if self._cur else None

    @property
    def session(self) -> Session | None:
        return self._cur.session if self._cur else None

    @property
    def contracts_used(self) -> int:
        return self._cur.contracts_used if self._cur else 0

    @property
    def realized_today(self) -> float:
        return self._cur.realized if self._cur else 0.0

    def snapshot(self, now: datetime | None = None) -> SymbolSnapshot:
        cur, prior = self._cur, self._prior
        ref = now or self.last_ts
        age = (ref - self.last_ts).total_seconds() if ref and self.last_ts else None
        spread = (self.ask - self.bid) / self._tick if self.mid is not None else None
        atr = self._atr
        return SymbolSnapshot(
            root=self.root,
            symbol=self.symbol,
            name=self.spec.name,
            trade_date=cur.session.trade_date if cur else None,
            session_open=bool(cur and self.last_ts and cur.session.contains(self.last_ts)),
            ts=self.last_ts.astimezone(CT) if self.last_ts else None,
            bid=_finite(self.bid),
            ask=_finite(self.ask),
            price=self.price,
            spread_ticks=round(spread, 2) if spread is not None else None,
            book_lean=round(self.book_lean, 4) if self.book_lean is not None else None,
            atr=atr,
            atr_ticks=round(atr / self._tick, 2) if atr is not None else None,
            vwap=self.vwap,
            vwap_band=self.vwap_band,
            session_high=_finite(cur.high) if cur else None,
            session_low=_finite(cur.low) if cur else None,
            prior_high=_finite(prior.high) if prior else None,
            prior_low=_finite(prior.low) if prior else None,
            prior_close=_finite(prior.last) if prior else None,
            overnight_high=_finite(cur.on_high) if cur else None,
            overnight_low=_finite(cur.on_low) if cur else None,
            position=self.position,
            avg_price=self.avg_price,
            unrealized_pnl=round(self.unrealized, 2),
            realized_pnl=round(self.realized_today, 2),
            contracts_used=self.contracts_used,
            contracts_quota=self.quota,
            data_age_seconds=round(age, 3) if age is not None else None,
            price_precision=self.spec.price_precision,
            sample_data=self.sample_data,
        )


class StateStore:
    """Thread-safe registry of trackers: the engine thread writes, the API thread reads."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._trackers: dict[str, SymbolTracker] = {}
        self._account: dict[str, object] = {}
        self._clock: datetime | None = None

    def add(self, tracker: SymbolTracker) -> SymbolTracker:
        with self._lock:
            self._trackers[tracker.root] = tracker
        return tracker

    def tracker(self, root: str) -> SymbolTracker:
        return self._trackers[root]

    @property
    def roots(self) -> list[str]:
        return list(self._trackers)

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def set_clock(self, now: datetime) -> None:
        with self._lock:
            self._clock = now

    @property
    def clock(self) -> datetime | None:
        return self._clock

    def set_account(self, **values: object) -> None:
        with self._lock:
            self._account = {k: str(v) if isinstance(v, Decimal) else v for k, v in values.items()}

    def account(self) -> dict[str, object]:
        with self._lock:
            return dict(self._account)

    def snapshots(self) -> list[SymbolSnapshot]:
        with self._lock:
            return [t.snapshot(self._clock) for t in self._trackers.values()]
