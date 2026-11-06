"""Steps 1.08 - 1.11 and 1.19: fee filter, risk manager, locked settings, kill switch, audit log."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from api.audit import AuditIntegrityError, AuditLog, MemoryAudit
from engine.calendar import CT, ProductGroup, SessionCalendar
from engine.events import EconomicEvent, EventCalendar, Impact
from risk import fees
from risk.authority import SYSTEM, NotAuthorized, Principal, PrincipalKind, Role
from risk.killswitch import KillSwitch, TripSource
from risk.manager import (
    AccountView,
    OrderIntent,
    PortfolioRiskManager,
    PortfolioView,
    PositionView,
    Side,
)
from risk.settings import RiskSettings, RiskSettingsStore, SettingsError, SettingsTampered

KEY = b"k" * 32
OWNER = Principal("bill", Role.OWNER)
APPROVER = Principal("deputy", Role.APPROVER)
VIEWER = Principal("guest", Role.VIEWER)
AI_OWNER = Principal("supervisor", Role.OWNER, PrincipalKind.AI)  # even with Owner role
AI_APPROVER = Principal("supervisor", Role.APPROVER, PrincipalKind.AI)

TS = datetime(2026, 11, 4, 10, 0, tzinfo=CT)  # Wednesday, regular hours
SESSION = SessionCalendar(ProductGroup.EQUITY_INDEX).session_for(date(2026, 11, 4))
EIA = EconomicEvent(datetime(2026, 11, 4, 9, 30, tzinfo=CT), "EIA", Impact.HIGH, ("MCL",))


def intent(**kw: object) -> OrderIntent:
    base: dict[str, object] = {
        "signal_id": "sig-1",
        "root": "MES",
        "side": Side.LONG,
        "qty": 1,
        "entry": Decimal("6000.00"),
        "stop": Decimal("5996.00"),  # 16 ticks = $20
        "target": Decimal("6002.00"),  # 8 ticks = $10
        "ts": TS,
    }
    base.update(kw)
    return OrderIntent(**base)  # type: ignore[arg-type]


def view(**kw: object) -> PortfolioView:
    base: dict[str, object] = {"account": AccountView(Decimal(25_000), Decimal(0))}
    base.update(kw)
    return PortfolioView(**base)  # type: ignore[arg-type]


@pytest.fixture
def kill() -> KillSwitch:
    return KillSwitch(MemoryAudit())


@pytest.fixture
def mgr(kill: KillSwitch) -> PortfolioRiskManager:
    settings = RiskSettings()
    return PortfolioRiskManager(lambda: settings, fees.DailyFeeFilter(), EventCalendar([EIA]), kill)


def failed(mgr: PortfolioRiskManager, i: OrderIntent, v: PortfolioView) -> set[str]:
    assert SESSION is not None
    return {c.name for c in mgr.evaluate(i, v, SESSION).checks if not c.passed}


# ----- 1.08 fee filter -----------------------------------------------------------------------
def test_fee_filter_math() -> None:
    mes = fees.check("MES", 8, date(2026, 11, 4))
    assert mes.round_trip_fees == Decimal("1.24")
    assert mes.target_value == Decimal("10.00")
    assert mes.qualifies
    assert mes.min_target_ticks == 2  # 2 x 1.24 / 1.25 = 1.98 -> 2 ticks


def test_one_or_two_tick_scalps_fail_on_fifty_cent_contracts() -> None:
    for root in ("MNQ", "MYM"):
        assert not fees.check(root, 1, date(2026, 11, 4)).qualifies
        assert not fees.check(root, 2, date(2026, 11, 4)).qualifies


@pytest.mark.parametrize("root", ["MES", "MCL", "MGC", "M6E", "10Y"])
def test_default_targets_qualify_every_active_symbol(root: str) -> None:
    target = RiskSettings().target_ticks[root]
    assert fees.check(root, target, date(2026, 11, 4)).qualifies


def test_daily_filter_recalculates_when_fees_change() -> None:
    f = fees.DailyFeeFilter()
    day = date(2026, 11, 4)
    assert f.evaluate("MES", 2, day, Decimal(2)).qualifies
    f.update_fees("MES", fees.FeeSchedule(Decimal("1.00"), Decimal("0.50"), source="fills"))
    after = f.evaluate("MES", 2, day, Decimal(2))
    assert not after.qualifies
    assert after.fee_source == "fills"


# ----- 1.09 risk manager ---------------------------------------------------------------------
def test_clean_intent_is_approved(mgr: PortfolioRiskManager) -> None:
    assert failed(mgr, intent(), view()) == set()


def test_all_checks_run_and_are_reported(mgr: PortfolioRiskManager) -> None:
    assert SESSION is not None
    d = mgr.evaluate(intent(), view(), SESSION)
    assert [c.name for c in d.checks] == [
        "kill_switch",
        "symbol",
        "order_shape",
        "session",
        "news_blackout",
        "fee_filter",
        "per_trade_loss",
        "daily_loss",
        "buying_power",
        "quota",
        "position_count",
        "correlation",
    ]
    assert json.dumps(d.to_dict())


def test_kill_switch_blocks_entries(mgr: PortfolioRiskManager, kill: KillSwitch) -> None:
    kill.trip(OWNER, TripSource.MANUAL, "test")
    assert "kill_switch" in failed(mgr, intent(), view())


def test_inactive_symbol_refused(mgr: PortfolioRiskManager) -> None:
    i = intent(root="MNQ", entry=Decimal("21000"), stop=Decimal("20990"), target=Decimal("21010"))
    assert "symbol" in failed(mgr, i, view())


def test_malformed_brackets_refused(mgr: PortfolioRiskManager) -> None:
    assert "order_shape" in failed(mgr, intent(stop=Decimal("6004.00")), view())
    assert "order_shape" in failed(mgr, intent(target=Decimal("5999.00")), view())
    assert "order_shape" in failed(mgr, intent(entry=Decimal("6000.10")), view())
    assert "order_shape" in failed(mgr, intent(qty=5), view())


def test_session_rules(mgr: PortfolioRiskManager) -> None:
    assert SESSION is not None
    late = SESSION.close - timedelta(minutes=10)
    assert "session" in failed(mgr, intent(ts=late), view())
    assert "session" in failed(mgr, intent(ts=SESSION.close + timedelta(minutes=5)), view())


def test_news_blackout_only_for_affected_symbol(mgr: PortfolioRiskManager) -> None:
    ts = EIA.ts - timedelta(minutes=3)
    mcl = intent(
        root="MCL", entry=Decimal("70.00"), stop=Decimal("69.80"), target=Decimal("70.10"), ts=ts
    )
    assert "news_blackout" in failed(mgr, mcl, view())
    assert "news_blackout" not in failed(mgr, intent(ts=ts), view())
    clear = replace(mcl, ts=EIA.ts + timedelta(minutes=6))
    assert "news_blackout" not in failed(mgr, clear, view())


def test_fee_filter_enforced_per_intent(mgr: PortfolioRiskManager) -> None:
    # MES: 1 tick = $1.25 is below 2 x $1.24 round-trip fees, so a 1-tick target is refused
    # even though the symbol qualifies for the day; 2 ticks ($2.50) passes.
    one_tick = intent(target=Decimal("6000.25"))
    assert one_tick.target_ticks() == 1
    assert "fee_filter" in failed(mgr, one_tick, view())
    assert "fee_filter" not in failed(mgr, intent(target=Decimal("6000.50")), view())


def test_fee_filter_rejects_target_below_minimum(kill: KillSwitch) -> None:
    s = RiskSettings(target_ticks={"MES": 1, "MCL": 10, "MGC": 10, "M6E": 6, "10Y": 6})
    m = PortfolioRiskManager(lambda: s, fees.DailyFeeFilter(), EventCalendar([]), kill)
    assert "fee_filter" in failed(m, intent(target=Decimal("6000.25")), view())


def test_per_trade_loss_limit(mgr: PortfolioRiskManager) -> None:
    wide = intent(stop=Decimal("5960.00"))  # 160 ticks = $200 > $150
    assert "per_trade_loss" in failed(mgr, wide, view())


def test_daily_loss_limit(mgr: PortfolioRiskManager) -> None:
    assert "daily_loss" in failed(mgr, intent(), view(realized_today=Decimal(-490)))
    assert "daily_loss" not in failed(mgr, intent(), view(realized_today=Decimal(-100)))


def test_buying_power_buffer(mgr: PortfolioRiskManager) -> None:
    # MES margin $2,400; with 40% buffer an account needs $4,000 available.
    assert "buying_power" in failed(
        mgr, intent(), view(account=AccountView(Decimal(3_900), Decimal(0)))
    )
    assert "buying_power" not in failed(
        mgr, intent(), view(account=AccountView(Decimal(4_100), Decimal(0)))
    )
    pending = (
        intent(
            signal_id="p",
            root="MGC",
            entry=Decimal("2700.0"),
            stop=Decimal("2698.0"),
            target=Decimal("2701.0"),
        ),
    )
    assert "buying_power" in failed(
        mgr, intent(), view(account=AccountView(Decimal(6_000), Decimal(0)), pending=pending)
    )


def test_quotas(mgr: PortfolioRiskManager) -> None:
    assert "quota" in failed(mgr, intent(), view(contracts_used={"MES": 40}))
    assert "quota" in failed(
        mgr, intent(), view(contracts_used={"MCL": 40, "MGC": 40, "M6E": 40, "10Y": 30})
    )
    assert "quota" not in failed(mgr, intent(), view(contracts_used={"MES": 39}))


def test_max_three_open_positions(mgr: PortfolioRiskManager) -> None:
    open3 = tuple(
        PositionView(r, Side.SHORT if r == "MCL" else Side.LONG, 1, Decimal(1))
        for r in ("MCL", "MGC", "M6E")
    )
    assert "position_count" in failed(mgr, intent(), view(positions=open3))


def test_one_exposure_per_correlation_group(mgr: PortfolioRiskManager) -> None:
    mnq = (PositionView("MNQ", Side.LONG, 1, Decimal(21000)),)
    assert "correlation" in failed(mgr, intent(), view(positions=mnq))


def test_cap_on_same_direction(mgr: PortfolioRiskManager) -> None:
    longs = (
        PositionView("MCL", Side.LONG, 1, Decimal(70)),
        PositionView("MGC", Side.LONG, 1, Decimal(2700)),
    )
    assert "correlation" in failed(mgr, intent(side=Side.LONG), view(positions=longs))
    short = intent(side=Side.SHORT, stop=Decimal("6004.00"), target=Decimal("5998.00"))
    assert "correlation" not in failed(mgr, short, view(positions=longs))


def test_breaches_drive_kill_switch(mgr: PortfolioRiskManager) -> None:
    assert mgr.breaches(view(realized_today=Decimal(-500)))
    assert not mgr.breaches(view(realized_today=Decimal(-499)))


# ----- 1.10 locked settings ------------------------------------------------------------------
def test_settings_validation() -> None:
    with pytest.raises(SettingsError, match="buying_power_buffer"):
        RiskSettings(buying_power_buffer=Decimal("0.10"))
    with pytest.raises(SettingsError, match="max_open_positions"):
        RiskSettings(max_open_positions=5)
    with pytest.raises(SettingsError, match="per_trade_loss_limit"):
        RiskSettings(per_trade_loss_limit=Decimal(900))
    s = RiskSettings()
    with pytest.raises(AttributeError):
        s.daily_loss_limit = Decimal(1)  # type: ignore[misc]
    with pytest.raises(TypeError):
        s.daily_contract_quota["MES"] = 999  # type: ignore[index]


def test_owner_can_change_settings_and_change_is_audited(tmp_path: Path) -> None:
    audit = MemoryAudit()
    store = RiskSettingsStore(tmp_path / "risk.json", KEY, audit)
    seen: list[RiskSettings] = []
    store.subscribe(seen.append)
    new = store.update(OWNER, daily_loss_limit=Decimal(400))
    assert new.daily_loss_limit == Decimal(400)
    assert seen == [new]
    assert audit.items[-1][1] == "risk_settings_changed"
    assert audit.items[-1][3]["daily_loss_limit"] == {"from": "500", "to": "400"}
    reopened = RiskSettingsStore(tmp_path / "risk.json", KEY, MemoryAudit())
    assert reopened.current.daily_loss_limit == Decimal(400)


@pytest.mark.parametrize("who", [AI_OWNER, AI_APPROVER, APPROVER, VIEWER, SYSTEM])
def test_nobody_but_a_human_owner_can_change_settings(tmp_path: Path, who: Principal) -> None:
    audit = MemoryAudit()
    store = RiskSettingsStore(tmp_path / "risk.json", KEY, audit)
    with pytest.raises(NotAuthorized):
        store.update(who, daily_loss_limit=Decimal(5000))
    assert store.current.daily_loss_limit == Decimal(500)
    assert audit.items[-1][1] == "risk_settings_change_refused"


def test_tampered_settings_file_detected(tmp_path: Path) -> None:
    path = tmp_path / "risk.json"
    RiskSettingsStore(path, KEY, MemoryAudit())
    doc = json.loads(path.read_text("utf-8"))
    doc["payload"] = doc["payload"].replace(
        '"daily_loss_limit": "500"', '"daily_loss_limit": "99999"'
    )
    path.write_text(json.dumps(doc), "utf-8")
    audit = MemoryAudit()
    with pytest.raises(SettingsTampered):
        RiskSettingsStore(path, KEY, audit)
    assert audit.items[-1][1] == "risk_settings_tampered"
    with pytest.raises(SettingsTampered):
        RiskSettingsStore(path, b"x" * 32, MemoryAudit())


def test_invalid_change_rejected_and_not_saved(tmp_path: Path) -> None:
    store = RiskSettingsStore(tmp_path / "risk.json", KEY, MemoryAudit())
    with pytest.raises(SettingsError):
        store.update(OWNER, buying_power_buffer=Decimal("0.05"))
    assert store.current.buying_power_buffer == Decimal("0.40")


# ----- 1.11 kill switch ----------------------------------------------------------------------
def test_manual_trip_flattens_once_and_blocks(kill: KillSwitch) -> None:
    calls: list[str] = []
    kill.on_trip(lambda t: calls.append(t.reason))
    assert kill.trip(APPROVER, TripSource.MANUAL, "button")
    assert not kill.trip(SYSTEM, TripSource.DATA_FEED, "stale")
    assert calls == ["button"]
    st = kill.state
    assert st.tripped
    assert st.flattened
    assert [t.source for t in st.trips] == [TripSource.MANUAL, TripSource.DATA_FEED]


def test_flatten_handler_errors_are_reported_not_swallowed(kill: KillSwitch) -> None:
    def boom(_: object) -> None:
        raise RuntimeError("broker offline")

    ran: list[int] = []
    kill.on_trip(boom)
    kill.on_trip(lambda _: ran.append(1))
    kill.trip(OWNER, TripSource.MANUAL, "x")
    assert ran == [1]  # later handlers still run
    assert not kill.state.flattened
    assert "broker offline" in kill.state.flatten_error


def test_only_human_owner_resets(kill: KillSwitch) -> None:
    kill.trip(AI_APPROVER, TripSource.SUPERVISOR, "supervisor saw chaos")  # AI may trip
    for who in (AI_OWNER, APPROVER, VIEWER, SYSTEM):
        with pytest.raises(NotAuthorized):
            kill.reset(who, "nope")
    with pytest.raises(NotAuthorized):
        kill.trip(VIEWER, TripSource.MANUAL, "viewer cannot")
    kill.reset(OWNER, "checked")
    assert not kill.tripped


# ----- 1.19 audit log ------------------------------------------------------------------------
def test_audit_chain_verifies_and_survives_reopen(tmp_path: Path) -> None:
    from api.audit import AuditCategory  # noqa: PLC0415

    log = AuditLog(tmp_path / "audit.jsonl")
    for n in range(5):
        log.record(AuditCategory.COMMAND, "start", "bill", {"n": n})
    assert log.verify() == 5
    log2 = AuditLog(tmp_path / "audit.jsonl")
    log2.record(AuditCategory.COMMAND, "stop", "bill", {})
    assert log2.verify() == 6
    assert [r.seq for r in log2.records()] == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("attack", ["edit", "delete", "reorder"])
def test_audit_tampering_detected(tmp_path: Path, attack: str) -> None:
    from api.audit import AuditCategory  # noqa: PLC0415

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for n in range(4):
        log.record(AuditCategory.ORDER, "submit", "engine", {"n": n})
    lines = path.read_text("utf-8").splitlines()
    if attack == "edit":
        lines[1] = lines[1].replace('"n": 1', '"n": 9')
    elif attack == "delete":
        del lines[2]
    else:
        lines[1], lines[2] = lines[2], lines[1]
    path.write_text("\n".join(lines) + "\n", "utf-8")
    with pytest.raises(AuditIntegrityError):
        AuditLog(path).verify()


def test_audit_refuses_secret_fields(tmp_path: Path) -> None:
    from api.audit import AuditCategory  # noqa: PLC0415

    log = AuditLog(tmp_path / "audit.jsonl")
    for bad in ({"password": "x"}, {"broker": {"api_key": "x"}}, {"totp_code": "1"}):
        with pytest.raises(ValueError, match="secrets"):
            log.record(AuditCategory.LOGIN, "login", "bill", bad)
