"""Step 1.07: live state tracker per symbol."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

import numpy as np
import pytest

from engine import sample_data
from engine.calendar import CT
from engine.state import ATR_PERIOD, StateStore, SymbolTracker


@pytest.fixture(scope="module")
def mes_days() -> list[sample_data.ContractDay]:
    pack = sample_data.generate(dates=sample_data.DEFAULT_DATES[:2], roots=("MES",))
    return pack.days


def _feed(t: SymbolTracker, day: sample_data.ContractDay) -> None:
    q, tr = day.quotes, day.trades
    j = 0
    for i in range(q.ts_ns.size):
        while j < tr.ts_ns.size and tr.ts_ns[j] <= q.ts_ns[i]:
            t.on_trade(int(tr.ts_ns[j]), float(tr.price[j]), float(tr.size[j]))
            j += 1
        t.on_quote(
            int(q.ts_ns[i]),
            float(q.bid[i]),
            float(q.ask[i]),
            float(q.bid_size[i]),
            float(q.ask_size[i]),
        )
    while j < tr.ts_ns.size:
        t.on_trade(int(tr.ts_ns[j]), float(tr.price[j]), float(tr.size[j]))
        j += 1


def test_levels_vwap_and_overnight_match_the_data(mes_days: list[sample_data.ContractDay]) -> None:
    d0, d1 = mes_days
    t = SymbolTracker("MES", d0.contract.symbol, quota=40, sample_data=True)
    _feed(t, d0)
    tr = d0.trades
    vwap = float(np.sum(tr.price * tr.size) / np.sum(tr.size))
    assert t.vwap == pytest.approx(vwap, rel=1e-9)
    mid0 = (d0.quotes.bid + d0.quotes.ask) / 2
    rth = int(d0.session.rth_open.timestamp() * 1e9)
    before = d0.quotes.ts_ns < rth
    snap = t.snapshot()
    assert snap.overnight_high is not None
    assert snap.overnight_high >= float(mid0[before].max())
    assert snap.prior_high is None  # no previous session yet
    # Second session: the first becomes "prior day"; VWAP resets.
    _feed(t, d1)
    s2 = t.snapshot()
    assert s2.trade_date == d1.session.trade_date
    assert s2.prior_close == pytest.approx(float(d0.trades.price[-1])) or s2.prior_close == (
        pytest.approx(float(mid0[-1]))
    )
    assert s2.prior_high is not None
    assert s2.prior_high >= float(mid0.max())
    vwap1 = float(np.sum(d1.trades.price * d1.trades.size) / np.sum(d1.trades.size))
    assert s2.vwap == pytest.approx(vwap1, rel=1e-9)


def test_atr_spread_and_lean(mes_days: list[sample_data.ContractDay]) -> None:
    t = SymbolTracker("MES", mes_days[0].contract.symbol)
    _feed(t, mes_days[0])
    s = t.snapshot()
    assert s.atr is not None
    assert s.atr > 0
    assert s.atr_ticks is not None
    assert s.atr_ticks == pytest.approx(s.atr / 0.25, rel=1e-3)
    assert s.spread_ticks in (1.0, 2.0)
    assert s.book_lean is not None
    assert -1 <= s.book_lean <= 1


def test_atr_needs_full_period() -> None:
    t = SymbolTracker("MES", "MESZ6")
    base = int(datetime(2026, 11, 4, 9, 0, tzinfo=CT).timestamp() * 1e9)
    for m in range(ATR_PERIOD):
        for k, px in enumerate((6000.0, 6001.0, 5999.0, 6000.0)):
            t.on_quote(base + m * 60_000_000_000 + k * 1_000_000_000, px, px + 0.25, 5, 5)
    assert t.atr is None  # 14 bars opened, only 13 closed
    t.on_quote(base + ATR_PERIOD * 60_000_000_000, 6000.0, 6000.25, 5, 5)
    assert t.atr == pytest.approx(2.0)


def test_book_lean_sign() -> None:
    t = SymbolTracker("MES", "MESZ6")
    ts = int(datetime(2026, 11, 4, 9, 0, tzinfo=CT).timestamp() * 1e9)
    for i in range(30):
        t.on_quote(ts + i, 6000.0, 6000.25, 90, 10)
    assert t.book_lean == pytest.approx(0.8)


def test_position_pnl_quota_and_data_age() -> None:
    t = SymbolTracker("MES", "MESZ6", quota=40)
    ts = datetime(2026, 11, 4, 9, 0, tzinfo=CT)
    t.on_quote(int(ts.timestamp() * 1e9), 6000.0, 6000.25, 5, 5)
    t.set_position(-2, 6001.0, 7.5)
    t.add_contracts(2)
    t.add_realized(-12.5)
    s = t.snapshot(now=ts + timedelta(seconds=4))
    assert (s.position, s.avg_price, s.unrealized_pnl) == (-2, 6001.0, 7.5)
    assert (s.contracts_used, s.contracts_quota, s.realized_pnl) == (2, 40, -12.5)
    assert s.data_age_seconds == pytest.approx(4.0)
    t.set_position(0, None, 0.0)
    assert t.snapshot().avg_price is None


def test_quotes_outside_sessions_do_not_create_sessions() -> None:
    t = SymbolTracker("MES", "MESZ6")
    sat = datetime(2026, 11, 7, 12, 0, tzinfo=CT)
    t.on_quote(int(sat.timestamp() * 1e9), 6000.0, 6000.25, 5, 5)
    s = t.snapshot()
    assert s.trade_date is None
    assert not s.session_open
    assert s.price == pytest.approx(6000.125)


def test_store_snapshots_are_json_ready(mes_days: list[sample_data.ContractDay]) -> None:
    store = StateStore()
    t = store.add(SymbolTracker("MES", "MESZ6", sample_data=True))
    _feed(t, mes_days[0])
    store.set_account(equity=50_000)
    snaps = store.snapshots()
    blob = json.dumps([s.to_dict() for s in snaps])
    assert "MESZ6" in blob
    assert snaps[0].sample_data
    for v in snaps[0].to_dict().values():
        assert not (isinstance(v, float) and math.isnan(v))
