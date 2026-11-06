"""Steps 1.06, 1.07, 1.11 - 1.13 inside the real NautilusTrader engine.

Uses a small sample pack (2 contracts, 1-2 sessions) so the suite stays fast; the full pack is
exercised by the backtest-runner tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from api.audit import AuditCategory, AuditLog
from engine import sample_data
from engine.context import EngineContext, Mode, PositionRecord, ReconcileStatus, reconcile
from engine.nautilus.catalog import PackInfo, write_pack
from engine.nautilus.node import EngineNode
from risk.authority import Principal, Role
from risk.killswitch import TripSource
from risk.manager import Side
from risk.settings import RiskSettingsStore

KEY = b"t" * 32
OWNER = Principal("bill", Role.OWNER)
DATES = (date(2026, 11, 3), date(2026, 11, 4))


@pytest.fixture(scope="module")
def pack(tmp_path_factory: pytest.TempPathFactory) -> PackInfo:
    path = tmp_path_factory.mktemp("pack") / "sample"
    return write_pack(sample_data.generate(dates=DATES, roots=("MES", "MCL")), path)


def make_ctx(tmp: Path, pack: PackInfo) -> EngineContext:
    audit = AuditLog(tmp / "audit.jsonl")
    store = RiskSettingsStore(tmp / "risk.json", KEY, audit)
    return EngineContext(Mode.BACKTEST, store, audit, pack.events, state_dir=tmp, sample_data=True)


@pytest.fixture
def node(tmp_path: Path, pack: PackInfo) -> Iterator[EngineNode]:
    n = EngineNode(make_ctx(tmp_path, pack), pack)
    yield n
    n.dispose()


def run_day(n: EngineNode, d: date, until: float = 1.0) -> None:
    """Run ``until`` (fraction) of session ``d`` through the engine without finalizing."""
    n._engine_thread = __import__("threading").get_ident()
    data = n.day_data(d)
    stop = int(len(data) * until)
    for k in range(0, stop, 20_000):
        n._run_batch(data[k : min(k + 20_000, stop)])


# ----- 1.06 one symbol engine per contract in one node ---------------------------------------
def test_one_strategy_per_contract_in_one_node(node: EngineNode) -> None:
    assert sorted(node.strategies) == ["MCL", "MES"]
    assert sorted(str(s.iid) for s in node.strategies.values()) == ["MCLZ6.GLBX", "MESZ6.GLBX"]
    assert len(node.engine.trader.strategies()) == 2
    assert node.guard in node.engine.trader.actors()


def test_full_run_trades_every_symbol_with_real_fees(node: EngineNode) -> None:
    node.run_backtest()
    ctx = node.ctx
    fills = node.fills()
    traded = {str(i).split(".")[0][:-2] for i in fills["instrument_id"]}
    assert traded == {"MES", "MCL"}
    assert ctx.orders_submitted > 0
    assert ctx.decisions.approved == ctx.orders_submitted
    # Commission per fill = fee table per side x quantity (MES $0.62, MCL $0.77).
    commissions = {str(r.instrument_id)[:3]: str(r.commissions) for r in fills.itertuples()}
    assert "0.62" in commissions["MES"]
    assert "0.77" in commissions["MCL"]
    assert ctx.audit.verify() > 0  # type: ignore[attr-defined]


# ----- 1.07 live state per symbol, fed by the engine ---------------------------------------------
def test_state_tracked_per_symbol_through_engine(node: EngineNode) -> None:
    run_day(node, DATES[0], until=0.6)
    snaps = {s.root: s for s in node.ctx.store.snapshots()}
    assert set(snaps) == {"MES", "MCL"}
    for s in snaps.values():
        assert s.price is not None
        assert s.vwap is not None
        assert s.atr is not None
        assert s.overnight_high is not None
        assert s.book_lean is not None
        assert s.spread_ticks is not None
        assert s.sample_data
    acct = node.ctx.store.account()
    assert Decimal(str(acct["equity"])) > 0
    assert acct["reconciled"] is True


# ----- 1.12 order safety --------------------------------------------------------------------------
def test_same_signal_twice_sends_one_order(node: EngineNode) -> None:
    run_day(node, DATES[0], until=0.45)  # into regular hours
    strat = node.strategies["MES"]
    strat.flatten()
    run_day(node, DATES[0], until=0.46)
    t = strat.tracker
    assert t.atr is not None
    assert t.last_ts is not None
    intent = strat.build_intent("MES:long:test-dup", Side.LONG, t.last_ts, t.atr)
    sent_before = node.ctx.orders_submitted
    first = strat.submit_signal(intent)
    second = strat.submit_signal(intent)
    assert first is True, node.ctx.decisions.tail(1)
    assert second is False
    assert node.ctx.orders_submitted == sent_before + 1
    actions = [r.action for r in node.ctx.audit.records()]  # type: ignore[attr-defined]
    assert "duplicate_signal_blocked" in actions


def test_signal_ledger_survives_restart(tmp_path: Path, pack: PackInfo) -> None:
    ctx = make_ctx(tmp_path, pack)
    assert ctx.ledger.claim("MES:long:1")
    again = make_ctx(tmp_path, pack)
    assert not again.ledger.claim("MES:long:1")


def test_bracket_orders_sent_together(node: EngineNode) -> None:
    run_day(node, DATES[0])
    orders = node.engine.cache.orders()
    lists = {o.order_list_id for o in orders if o.order_list_id is not None}
    assert lists
    for ol in lists:
        members = [o for o in orders if o.order_list_id == ol]
        types = sorted(o.order_type.name for o in members)
        assert types == ["LIMIT", "MARKET", "STOP_MARKET"]


def test_reconcile_rules() -> None:
    assert reconcile({}, {}).status is ReconcileStatus.CLEAN
    rec = {"MESZ6": PositionRecord("MESZ6", 1, 6000.0)}
    assert reconcile({"MESZ6": 1}, rec).status is ReconcileStatus.ADOPTED
    mm = reconcile({}, rec)
    assert mm.status is ReconcileStatus.MISMATCH
    assert not mm.may_trade
    assert "MESZ6: broker +0, engine last recorded +1" in mm.differences


def test_restart_mismatch_blocks_all_trading(tmp_path: Path, pack: PackInfo) -> None:
    ctx = make_ctx(tmp_path, pack)
    # The engine recorded an open MES position, but the broker reports none.
    ctx.journal.save({"MESZ6": PositionRecord("MESZ6", 1, 6000.0)})
    n = EngineNode(ctx, pack)
    try:
        run_day(n, DATES[0])
        assert ctx.reconcile_report is not None
        assert ctx.reconcile_report.status is ReconcileStatus.MISMATCH
        assert ctx.orders_submitted == 0
        assert not ctx.reconciled.is_set()
    finally:
        n.dispose()


# ----- 1.11 kill switch inside the engine ---------------------------------------------------------
def _assert_flat(n: EngineNode) -> None:
    assert not n.engine.cache.positions_open()
    assert not [o for o in n.engine.cache.orders_open() if not o.is_closed]


def test_manual_kill_switch_flattens_everything(node: EngineNode) -> None:
    run_day(node, DATES[0], until=0.55)
    node.ctx.kill.trip(OWNER, TripSource.MANUAL, "button pressed")
    run_day(node, DATES[0], until=0.551)  # orders process on the next data
    _assert_flat(node)
    sent = node.ctx.orders_submitted
    run_day(node, DATES[1])
    assert node.ctx.orders_submitted == sent  # nothing new while tripped
    assert node.ctx.kill.state.flattened


def test_automatic_kill_on_risk_breach(node: EngineNode) -> None:
    run_day(node, DATES[0], until=0.5)
    node.strategies["MES"].tracker.add_realized(-600.0)  # beyond the $500 daily limit
    run_day(node, DATES[0], until=0.51)
    trips = node.ctx.kill.state.trips
    assert trips
    assert trips[0].source is TripSource.RISK_BREACH
    _assert_flat(node)


def test_automatic_kill_on_data_feed_failure(node: EngineNode) -> None:
    run_day(node, DATES[0], until=0.3)
    node.ctx.faults.data_feed.add("MCL")
    run_day(node, DATES[0], until=0.32)
    trips = node.ctx.kill.state.trips
    assert trips
    assert trips[0].source is TripSource.DATA_FEED
    assert "MCL" in trips[0].reason


def test_automatic_kill_on_broker_disconnect(node: EngineNode) -> None:
    run_day(node, DATES[0], until=0.5)
    node.ctx.faults.broker_disconnected = True
    run_day(node, DATES[0], until=0.51)
    trips = node.ctx.kill.state.trips
    assert trips
    assert trips[0].source is TripSource.BROKER_DISCONNECT
    _assert_flat(node)
    cats = {(r.category, r.action) for r in node.ctx.audit.records()}  # type: ignore[attr-defined]
    assert (AuditCategory.RISK.value, "kill_switch_tripped") in cats


def test_quiet_overnight_feed_does_not_false_trip(node: EngineNode) -> None:
    run_day(node, DATES[0], until=0.3)  # all of the overnight session
    assert not node.ctx.kill.tripped


def test_news_blackout_refusals_logged(tmp_path: Path, pack: PackInfo) -> None:
    ctx = make_ctx(tmp_path, pack)
    n = EngineNode(ctx, pack)
    try:
        n.run_backtest()
    finally:
        n.dispose()
    refused = [r for r in ctx.decisions.tail(500) if not r["approved"]]
    for r in refused:
        assert [c for c in r["checks"] if not c["passed"]]
