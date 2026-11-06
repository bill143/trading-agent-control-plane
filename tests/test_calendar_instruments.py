"""Steps 1.04 and 1.05: contract definitions, CME sessions, holidays, DST and rollover."""

from __future__ import annotations

import itertools
from datetime import date, datetime, time
from decimal import Decimal

import pytest

from engine import instruments as ins
from engine.calendar import CT, UTC, ProductGroup, SessionCalendar
from engine.nautilus.instruments import margin_ratio, to_nautilus

EQ = SessionCalendar(ProductGroup.EQUITY_INDEX)


# ----- 1.04 contract definitions -------------------------------------------------------------
def test_active_and_configured_off_sets() -> None:
    assert ins.ACTIVE_ROOTS == ("MES", "MCL", "MGC", "M6E", "10Y")
    assert ins.CONFIGURED_OFF_ROOTS == ("MNQ", "MYM", "MBT")


@pytest.mark.parametrize(
    ("root", "tick_value"),
    [
        ("MES", "1.25"),
        ("MCL", "1.00"),
        ("MGC", "1.0"),
        ("M6E", "1.25"),
        ("10Y", "1.000"),
        ("MNQ", "0.50"),
        ("MYM", "0.5"),
        ("MBT", "0.5"),
    ],
)
def test_tick_values_match_cme_specs(root: str, tick_value: str) -> None:
    assert ins.SPECS[root].tick_value == Decimal(tick_value)


def test_correlated_index_contracts_share_a_group() -> None:
    groups = {r: ins.SPECS[r].correlation_group for r in ("MES", "MNQ", "MYM")}
    assert set(groups.values()) == {ins.CorrelationGroup.US_EQUITY_INDEX}
    others = {ins.SPECS[r].correlation_group for r in ("MCL", "MGC", "M6E", "10Y")}
    assert len(others) == 4


@pytest.mark.parametrize("root", list(ins.SPECS))
def test_every_spec_builds_a_nautilus_instrument(root: str) -> None:
    c = ins.front_contract(root, date(2026, 11, 2))
    inst = to_nautilus(c)
    assert str(inst.id) == f"{c.symbol}.GLBX"
    assert Decimal(str(inst.price_increment)) == ins.SPECS[root].tick_size
    assert Decimal(str(inst.multiplier)) == ins.SPECS[root].multiplier
    assert 0 < margin_ratio(c) < 1


def test_unlisted_month_rejected() -> None:
    with pytest.raises(ValueError, match="does not list"):
        ins.contract("MES", 2026, 11)


# ----- 1.05 expiry and rollover --------------------------------------------------------------
@pytest.mark.parametrize(
    ("root", "year", "month", "last_trade", "roll"),
    [
        ("MES", 2026, 12, date(2026, 12, 18), date(2026, 12, 10)),
        ("MES", 2027, 3, date(2027, 3, 19), date(2027, 3, 11)),
        ("M6E", 2026, 12, date(2026, 12, 14), date(2026, 12, 4)),
        ("MCL", 2026, 12, date(2026, 11, 19), date(2026, 11, 16)),
        ("MCL", 2027, 1, date(2026, 12, 18), date(2026, 12, 15)),  # 25th is Christmas
        ("MGC", 2026, 12, date(2026, 12, 29), date(2026, 11, 25)),  # Thanksgiving is a short day
        ("10Y", 2026, 11, date(2026, 11, 30), date(2026, 11, 25)),
        ("MBT", 2026, 12, date(2026, 12, 24), date(2026, 12, 18)),  # last Friday is Christmas
    ],
)
def test_last_trade_and_roll_dates(
    root: str, year: int, month: int, last_trade: date, roll: date
) -> None:
    c = ins.contract(root, year, month)
    assert c.last_trade_date == last_trade
    assert c.roll_date == roll


def test_front_contract_switches_on_roll_date() -> None:
    assert ins.front_contract("MES", date(2026, 12, 9)).symbol == "MESZ6"
    assert ins.front_contract("MES", date(2026, 12, 10)).symbol == "MESH7"
    assert ins.front_contract("MGC", date(2026, 11, 2)).symbol == "MGCZ6"


def test_roll_schedule_is_continuous() -> None:
    sched = ins.roll_schedule("MES", date(2026, 1, 1), date(2027, 12, 31))
    assert [c.symbol for c in sched] == [
        "MESH6",
        "MESM6",
        "MESU6",
        "MESZ6",
        "MESH7",
        "MESM7",
        "MESU7",
        "MESZ7",
        "MESH8",
    ]
    for a, b in itertools.pairwise(sched):
        assert ins.front_contract("MES", a.roll_date) == b


# ----- 1.05 sessions, holidays, shortened sessions, DST --------------------------------------
def test_normal_session_hours_in_exchange_time() -> None:
    s = EQ.session_for(date(2026, 11, 4))
    assert s is not None
    assert s.open == datetime(2026, 11, 3, 17, 0, tzinfo=CT)
    assert s.close == datetime(2026, 11, 4, 16, 0, tzinfo=CT)
    assert not s.is_shortened


def test_maintenance_break_and_weekend_closed() -> None:
    assert not EQ.is_open(datetime(2026, 11, 4, 16, 30, tzinfo=CT))
    assert EQ.is_open(datetime(2026, 11, 4, 17, 0, tzinfo=CT))
    assert not EQ.is_open(datetime(2026, 11, 7, 12, 0, tzinfo=CT))  # Saturday
    assert EQ.next_open(datetime(2026, 11, 7, 12, 0, tzinfo=CT)) == datetime(
        2026, 11, 8, 17, 0, tzinfo=CT
    )


def test_holidays_and_shortened_sessions() -> None:
    assert not EQ.is_business_day(date(2026, 12, 25))  # Christmas
    assert not EQ.is_business_day(date(2027, 1, 1))  # New Year's Day
    thanksgiving = EQ.session_for(date(2026, 11, 26))
    assert thanksgiving is not None
    assert thanksgiving.is_shortened
    assert thanksgiving.close.time() == time(12, 0)


@pytest.mark.parametrize(
    ("trade_date", "utc_hour"),
    [
        (date(2026, 3, 6), 23),
        (date(2026, 3, 9), 22),  # DST starts Sun 2026-03-08
        (date(2026, 10, 30), 22),
        (date(2026, 11, 2), 23),
    ],  # DST ends Sun 2026-11-01
)
def test_daylight_saving_switch_keeps_exchange_time(trade_date: date, utc_hour: int) -> None:
    s = EQ.session_for(trade_date)
    assert s is not None
    assert s.open.time() == time(17, 0)  # always 17:00 CT ...
    assert s.open.astimezone(UTC).hour == utc_hour  # ... which moves in UTC


def test_naive_datetimes_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        EQ.is_open(datetime(2026, 11, 4, 10, 0))


def test_business_day_arithmetic() -> None:
    assert EQ.add_business_days(date(2026, 12, 24), 1) == date(2026, 12, 28)
    assert EQ.add_business_days(date(2026, 12, 28), -1) == date(2026, 12, 24)
    assert EQ.last_business_day_of_month(2026, 12) == date(2026, 12, 31)
