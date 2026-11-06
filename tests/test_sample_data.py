"""Step 1.03: sample data pack for demo mode (synthetic, reproducible, labeled)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from engine import sample_data
from engine.calendar import CT
from engine.events import EconomicEvent, EventCalendar, Impact
from engine.instruments import ACTIVE_ROOTS, SPECS
from engine.nautilus.catalog import ensure_pack, read_pack


@pytest.fixture(scope="module")
def pack() -> sample_data.SamplePack:
    return sample_data.generate()


@pytest.fixture(scope="module")
def written(tmp_path_factory: pytest.TempPathFactory, pack: sample_data.SamplePack) -> Path:
    path = tmp_path_factory.mktemp("pack") / "sample"
    ensure_pack(path)
    return path


def test_covers_every_active_contract_on_every_sample_date(pack: sample_data.SamplePack) -> None:
    assert pack.roots == ACTIVE_ROOTS
    for root in ACTIVE_ROOTS:
        days = [d.session.trade_date for d in pack.days if d.root == root]
        assert days == list(sample_data.DEFAULT_DATES)


def test_reproducible_from_seed(pack: sample_data.SamplePack) -> None:
    again = sample_data.generate()
    assert again.digest() == pack.digest()
    other = sample_data.generate(seed=1, dates=(date(2026, 11, 3),), roots=("MES",))
    assert other.digest() != pack.digest()


def test_labeled_as_synthetic(pack: sample_data.SamplePack, written: Path) -> None:
    assert "synthetic" in pack.label.lower()
    assert "synthetic" in read_pack(written).label.lower()


def test_quotes_are_valid_market_data(pack: sample_data.SamplePack) -> None:
    for d in pack.days:
        spec = SPECS[d.root]
        tick = float(spec.tick_size)
        q, t = d.quotes, d.trades
        assert np.all(q.ask > q.bid), d.contract.symbol
        spread_ticks = np.round((q.ask - q.bid) / tick)
        assert set(np.unique(spread_ticks)) <= {1.0, 2.0}
        assert np.allclose(np.round(q.bid / tick) * tick, q.bid, atol=tick * 1e-6)
        assert np.all(np.diff(q.ts_ns) > 0), "quote timestamps strictly increasing"
        assert np.max(np.diff(q.ts_ns)) <= sample_data.HEARTBEAT_S * 1e9 + 1e6, "feed gap"
        assert np.all(q.bid_size > 0)
        assert np.all(q.ask_size > 0)
        assert np.all(t.size > 0)
        # Every tick falls inside the CME session for its trade date.
        open_ns = int(d.session.open.timestamp() * 1e9)
        close_ns = int(d.session.close.timestamp() * 1e9)
        assert q.ts_ns[0] >= open_ns
        assert t.ts_ns[-1] < close_ns + 1_000_000


def test_daily_moves_are_realistic(pack: sample_data.SamplePack) -> None:
    for d in pack.days:
        if d.root == "10Y":
            continue  # quoted in yield points; checked below
        mid = (d.quotes.bid + d.quotes.ask) / 2
        assert abs(mid[-1] / mid[0] - 1) < 0.06, d.contract.symbol
    ten = [d for d in pack.days if d.root == "10Y"]
    for d in ten:
        mid = (d.quotes.bid + d.quotes.ask) / 2
        assert abs(mid[-1] - mid[0]) < 0.40  # under 40 basis points in a day


def test_regular_hours_busier_than_overnight(pack: sample_data.SamplePack) -> None:
    d = pack.days[0]
    rth_start = int(d.session.rth_open.timestamp() * 1e9)
    rth_end = rth_start + int(timedelta(hours=6.5).total_seconds() * 1e9)
    ts = d.quotes.ts_ns
    rth_rate = np.sum((ts >= rth_start) & (ts < rth_end)) / 6.5
    eth_rate = np.sum(ts < rth_start) / ((rth_start - ts[0]) / 3.6e12)
    assert rth_rate > 3 * eth_rate  # overnight includes the 5 s heartbeat quotes


def test_catalog_round_trip(pack: sample_data.SamplePack, written: Path) -> None:
    info = read_pack(written)
    assert info.digest == pack.digest()
    assert info.symbols == sorted(c.symbol for c in pack.contracts())
    assert info.quote_count == sum(d.quotes.ts_ns.size for d in pack.days)
    cat = info.catalog
    assert len(cat.instruments()) == len(ACTIVE_ROOTS)
    mes = next(s for s in info.symbols if s.startswith("MES"))
    ticks = cat.quote_ticks(instrument_ids=[f"{mes}.GLBX"])
    expected = sum(d.quotes.ts_ns.size for d in pack.days if d.contract.symbol == mes)
    assert len(ticks) == expected
    first = next(d for d in pack.days if d.contract.symbol == mes)
    assert Decimal(str(ticks[0].bid_price)) == Decimal(f"{first.quotes.bid[0]:.2f}")
    assert [str(i.id) for i in info.instruments()] == [str(i.id) for i in info.instruments()]
    assert len(info.events) == len(pack.events)


def test_ensure_pack_regenerates_a_tampered_pack(written: Path) -> None:
    manifest = written / "manifest.json"
    data = json.loads(manifest.read_text("utf-8"))
    data["digest"] = "0" * 64
    manifest.write_text(json.dumps(data), "utf-8")
    info = ensure_pack(written)
    assert info.digest == sample_data.generate().digest()


def test_sample_events_follow_release_patterns(pack: sample_data.SamplePack) -> None:
    names = {e.name for e in pack.events}
    assert {"EIA Weekly Crude Inventories", "Initial Jobless Claims", "Nonfarm Payrolls"} <= names
    eia = next(e for e in pack.events if e.name.startswith("EIA"))
    assert eia.ts.weekday() == 2
    assert eia.roots == ("MCL",)
    assert all(e.source == "sample" for e in pack.events)


def test_event_calendar_blackout_window(tmp_path: Path) -> None:
    from datetime import datetime  # noqa: PLC0415

    ev = EconomicEvent(datetime(2026, 11, 4, 9, 30, tzinfo=CT), "EIA", Impact.HIGH, ("MCL",))
    cal = EventCalendar([ev])
    win = timedelta(minutes=5)
    assert cal.blocking("MCL", ev.ts - timedelta(minutes=4), win, win) == ev
    assert cal.blocking("MCL", ev.ts - timedelta(minutes=6), win, win) is None
    assert cal.blocking("MES", ev.ts, win, win) is None
    cal.save(tmp_path / "e.json")
    assert list(EventCalendar.load(tmp_path / "e.json")) == [ev]
    with pytest.raises(ValueError, match="timezone-aware"):
        EconomicEvent(datetime(2026, 11, 4, 9, 30), "x", Impact.LOW, ())
