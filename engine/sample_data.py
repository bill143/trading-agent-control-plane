"""Sample data pack (build step 1.03): demo mode runs on this with no accounts connected.

The pack is **synthetic**: generated deterministically from a fixed seed on the real CME session
calendar, real contract symbols, tick sizes and roll schedule. It is clearly labeled as sample
data everywhere it is shown. No licensed market data is downloaded or bundled (Gate 0 condition
G0-3). Licensed Databento data replaces it in step 1.23 through the same catalog interface.

Microstructure model (per contract, per session):
* latent price: Gaussian random walk with an intraday volatility profile (quiet overnight,
  busy around the 08:30 CT open and into the close) and a per-session drift regime;
* quotes: best bid/ask snapped to the tick grid, mostly one tick wide, wider when quiet;
* displayed sizes skew toward the side the price is about to move to (so "book lean" carries
  information, as it does in real markets);
* trades: Poisson arrivals at the touch, aggressor side follows short-term direction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Final

import numpy as np
import numpy.typing as npt

from engine.calendar import CT, Session
from engine.events import EconomicEvent, Impact
from engine.instruments import ACTIVE_ROOTS, SPECS, Contract, front_contract

PACK_VERSION: Final = 2
LABEL: Final = "SAMPLE DATA - synthetic, generated (not real market prices)"
DEFAULT_SEED: Final = 20_261_102
#: Five consecutive CME trade dates (Mon 2026-11-02 .. Fri 2026-11-06) after the DST switch.
DEFAULT_DATES: Final = (
    date(2026, 11, 2),
    date(2026, 11, 3),
    date(2026, 11, 4),
    date(2026, 11, 5),
    date(2026, 11, 6),
)

#: Annualized volatility used for the latent price (in price units per sqrt(year) as a fraction
#: of price, except 10Y which is quoted in yield points and uses absolute daily bp).
_ANNUAL_VOL: Final[dict[str, float]] = {
    "MES": 0.16,
    "MNQ": 0.21,
    "MYM": 0.15,
    "MCL": 0.34,
    "MGC": 0.17,
    "M6E": 0.07,
    "MBT": 0.55,
}
_TEN_Y_DAILY_BP: Final = 6.0
#: Quote updates per second during regular hours / overnight.
_RTH_QUOTE_RATE: Final = 1.2
_ETH_QUOTE_RATE: Final = 0.15
_TRADE_TO_QUOTE: Final = 0.35
#: Longest gap between quotes in the sample feed, seconds.
HEARTBEAT_S: Final = 5.0

I64 = npt.NDArray[np.int64]
F64 = npt.NDArray[np.float64]


@dataclass(frozen=True)
class QuoteSeries:
    ts_ns: I64
    bid: F64
    ask: F64
    bid_size: I64
    ask_size: I64


@dataclass(frozen=True)
class TradeSeries:
    ts_ns: I64
    price: F64
    size: I64
    buyer_aggressor: npt.NDArray[np.bool_]


@dataclass(frozen=True)
class ContractDay:
    root: str
    contract: Contract
    session: Session
    quotes: QuoteSeries
    trades: TradeSeries


@dataclass(frozen=True)
class SamplePack:
    seed: int
    days: list[ContractDay]
    events: list[EconomicEvent]
    roots: tuple[str, ...]
    dates: tuple[date, ...]
    label: str = LABEL
    version: int = PACK_VERSION
    meta: dict[str, str] = field(default_factory=dict)

    def contracts(self) -> list[Contract]:
        seen: dict[str, Contract] = {}
        for d in self.days:
            seen.setdefault(d.contract.symbol, d.contract)
        return list(seen.values())

    def digest(self) -> str:
        """SHA-256 over every generated array and event: proves the pack is reproducible."""
        h = hashlib.sha256(f"v{self.version}:{self.seed}".encode())
        for d in self.days:
            h.update(f"{d.contract.symbol}:{d.session.trade_date}".encode())
            for arr in (
                d.quotes.ts_ns,
                d.quotes.bid,
                d.quotes.ask,
                d.quotes.bid_size,
                d.quotes.ask_size,
                d.trades.ts_ns,
                d.trades.price,
                d.trades.size,
                d.trades.buyer_aggressor,
            ):
                h.update(np.ascontiguousarray(arr).tobytes())
        h.update(json.dumps([e.to_dict() for e in self.events], sort_keys=True).encode())
        return h.hexdigest()


def _intensity(seconds_from_open: F64, session: Session) -> F64:
    """Relative activity (1.0 = regular-hours average) for seconds since the session open."""
    ts = session.open + timedelta(seconds=0)
    rth_start = (session.rth_open - ts).total_seconds()
    rth_end = (datetime.combine(session.trade_date, time(15, 0), tzinfo=CT) - ts).total_seconds()
    in_rth = (seconds_from_open >= rth_start) & (seconds_from_open < rth_end)
    base = np.where(in_rth, 1.0, _ETH_QUOTE_RATE / _RTH_QUOTE_RATE)
    # U-shape inside regular hours: busy at the open and the close.
    rth_len = max(rth_end - rth_start, 1.0)
    x = np.clip((seconds_from_open - rth_start) / rth_len, 0.0, 1.0)
    u_shape = 0.6 + 1.6 * (np.exp(-x * 9.0) + np.exp(-(1.0 - x) * 12.0))
    return np.asarray(np.where(in_rth, base * u_shape, base), dtype=np.float64)


def _per_second_sigma(root: str, price: float) -> float:
    seconds_per_day = 23 * 3600
    if root == "10Y":
        return float((_TEN_Y_DAILY_BP / 100.0) / np.sqrt(seconds_per_day))
    daily = _ANNUAL_VOL[root] / np.sqrt(252.0)
    return float(price * daily / np.sqrt(seconds_per_day))


def _generate_day(
    rng: np.random.Generator, root: str, contract: Contract, session: Session, start_price: float
) -> tuple[ContractDay, float]:
    spec = SPECS[root]
    tick = float(spec.tick_size)
    duration = (session.close - session.open).total_seconds()

    # Event times: thinned Poisson process following the intraday intensity profile.
    grid = np.arange(0.0, duration, 1.0)
    lam = _intensity(grid, session) * _RTH_QUOTE_RATE
    n_events = rng.poisson(lam)
    q_secs = np.repeat(grid, n_events) + rng.random(int(n_events.sum()))
    # Heartbeat: a quote at least every HEARTBEAT_S seconds, as a live feed would send.
    q_secs = np.union1d(q_secs, np.arange(0.0, duration, HEARTBEAT_S) + 0.5)
    if q_secs.size == 0:
        raise RuntimeError(f"no quote events generated for {root} {session.trade_date}")

    # Latent price: volatility scales with activity; drift regime per session.
    sigma = _per_second_sigma(root, start_price)
    dt = np.diff(q_secs, prepend=0.0).clip(min=1e-3)
    act = _intensity(q_secs, session)
    # Normalize so the session's total variance equals one trading day's variance.
    var_weight = float(np.sum(dt * act))
    sigma_event = sigma * np.sqrt(23 * 3600 / max(var_weight, 1.0))
    daily_sigma = sigma * np.sqrt(23 * 3600)
    # Session regime: trend up, trend down or range; trend moves ~0.6 daily sigma.
    regime_drift = rng.choice([-1.0, 0.0, 0.0, 1.0]) * 0.6 * daily_sigma / duration
    steps = rng.standard_normal(q_secs.size) * sigma_event * np.sqrt(dt * act)
    steps += regime_drift * dt
    latent = start_price + np.cumsum(steps)
    latent = np.maximum(latent, tick * 10)

    # Quotes on the tick grid; wider when quiet.
    bid_ticks = np.floor(latent / tick)
    wide = rng.random(q_secs.size) < np.where(act < 0.5, 0.08, 0.01)
    spread_ticks = np.where(wide, 2.0, 1.0)
    bid = np.round(bid_ticks * tick, spec.price_precision)
    ask = np.round((bid_ticks + spread_ticks) * tick, spec.price_precision)

    # Displayed size skews toward the side the price moves to next (informative book lean).
    future_move = np.sign(np.roll(latent, -20) - latent)
    future_move[-20:] = 0.0
    base_size = rng.lognormal(mean=2.3, sigma=0.6, size=q_secs.size)
    lean = np.clip(0.5 + 0.18 * future_move + rng.normal(0, 0.12, q_secs.size), 0.1, 0.9)
    bid_size = np.maximum(1, np.round(base_size * 2 * lean)).astype(np.int64)
    ask_size = np.maximum(1, np.round(base_size * 2 * (1 - lean))).astype(np.int64)

    # Trades: a subset of quote events, aggressor follows short-term direction.
    is_trade = rng.random(q_secs.size) < _TRADE_TO_QUOTE
    t_idx = np.flatnonzero(is_trade)
    buyer = (future_move[t_idx] > 0) | ((future_move[t_idx] == 0) & (rng.random(t_idx.size) < 0.5))
    noise_flip = rng.random(t_idx.size) < 0.3
    buyer = np.where(noise_flip, ~buyer, buyer)
    t_price = np.where(buyer, ask[t_idx], bid[t_idx])
    t_size = np.maximum(1, np.round(rng.lognormal(0.4, 0.8, t_idx.size))).astype(np.int64)
    # Trades print 1 microsecond after the quote they hit.
    open_ns = int(session.open.timestamp() * 1e9)
    q_ns = (open_ns + np.round(q_secs * 1e9)).astype(np.int64)
    q_ns = np.maximum.accumulate(q_ns + np.arange(q_ns.size, dtype=np.int64))  # strictly increasing
    t_ns = q_ns[t_idx] + 1_000

    day = ContractDay(
        root=root,
        contract=contract,
        session=session,
        quotes=QuoteSeries(q_ns, bid, ask, bid_size, ask_size),
        trades=TradeSeries(t_ns, t_price.astype(np.float64), t_size, buyer.astype(np.bool_)),
    )
    return day, float(latent[-1])


def sample_events(dates: tuple[date, ...]) -> list[EconomicEvent]:
    """Representative recurring US releases on the sample dates (schedule pattern, not a real
    calendar feed): ISM, EIA crude inventories (Wednesdays), jobless claims (Thursdays),
    payrolls (first Friday)."""
    out: list[EconomicEvent] = []
    for d in dates:
        if d.weekday() == 0 and d.day <= 7:
            out.append(_ev(d, time(9, 0), "ISM Manufacturing PMI", Impact.MEDIUM, ACTIVE_ROOTS))
        if d.weekday() == 2:
            out.append(_ev(d, time(9, 30), "EIA Weekly Crude Inventories", Impact.HIGH, ("MCL",)))
            if d.day <= 7:
                out.append(_ev(d, time(9, 0), "ISM Services PMI", Impact.MEDIUM, ACTIVE_ROOTS))
        if d.weekday() == 3:
            out.append(_ev(d, time(7, 30), "Initial Jobless Claims", Impact.MEDIUM, ACTIVE_ROOTS))
        if d.weekday() == 4 and d.day <= 7:
            out.append(_ev(d, time(7, 30), "Nonfarm Payrolls", Impact.HIGH, ACTIVE_ROOTS))
    return sorted(out, key=lambda e: e.ts)


def _ev(d: date, t: time, name: str, impact: Impact, roots: tuple[str, ...]) -> EconomicEvent:
    return EconomicEvent(datetime.combine(d, t, tzinfo=CT), name, impact, roots, source="sample")


def generate(
    seed: int = DEFAULT_SEED,
    dates: tuple[date, ...] = DEFAULT_DATES,
    roots: tuple[str, ...] = ACTIVE_ROOTS,
) -> SamplePack:
    """Generate the pack. Same arguments always produce byte-identical data."""
    days: list[ContractDay] = []
    for root in roots:
        spec = SPECS[root]
        rng = np.random.default_rng([seed, sum(map(ord, root))])
        price = float(spec.reference_price)
        cal = spec.calendar
        for d in dates:
            session = cal.session_for(d)
            if session is None:
                continue
            c = front_contract(root, d)
            day, price = _generate_day(rng, root, c, session, price)
            days.append(day)
    if not days:
        raise ValueError("no sessions on the requested dates")
    return SamplePack(
        seed=seed,
        days=days,
        events=sample_events(dates),
        roots=roots,
        dates=dates,
        meta={"generator": "engine.sample_data", "tick_rule": "CME contract specs"},
    )


def price_to_decimal(value: float, precision: int) -> Decimal:
    return Decimal(f"{value:.{precision}f}")
