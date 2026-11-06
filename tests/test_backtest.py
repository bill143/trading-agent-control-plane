"""Step 1.14: backtest runner with results report."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from engine import sample_data
from engine.backtest import BacktestResult, list_runs, run
from engine.calendar import CT
from engine.metrics import TradeRecord, by_symbol, compute
from engine.nautilus.catalog import PackInfo, write_pack

T0 = datetime(2026, 11, 3, 9, 0, tzinfo=CT)


def tr(pnl: str, minute: int, day: int = 3, root: str = "MES") -> TradeRecord:
    closed = T0.replace(day=day) + timedelta(minutes=minute)
    return TradeRecord(
        root,
        f"{root}Z6",
        "long",
        1,
        closed - timedelta(minutes=1),
        closed,
        1.0,
        1.0,
        Decimal(pnl),
        Decimal("1.24"),
        closed.date(),
    )


def test_metrics_math() -> None:
    trades = [tr("10", 1), tr("-5", 2), tr("20", 3), tr("-15", 4, day=4), tr("10", 5, day=4)]
    m = compute(trades, Decimal(1000))
    assert m.trades == 5
    assert (m.wins, m.losses) == (3, 2)
    assert m.net_pnl == Decimal(20)
    assert m.gross_profit == Decimal(40)
    assert m.gross_loss == Decimal(20)
    assert m.profit_factor == pytest.approx(2.0)
    assert m.win_rate == pytest.approx(0.6)
    assert m.avg_win == Decimal(40) / 3
    assert m.avg_loss == Decimal(-10)
    assert m.expectancy == Decimal(4)
    assert m.max_drawdown == Decimal(15)  # peak 1025 -> 1010
    assert m.max_drawdown_pct == pytest.approx(15 / 1025)
    assert m.trading_days == 2
    assert m.sharpe is not None
    assert m.commissions == Decimal("6.20")
    assert [p[1] for p in m.equity_curve] == [1010, 1005, 1025, 1010, 1020]


def test_metrics_edge_cases() -> None:
    empty = compute([], Decimal(1000))
    assert empty.trades == 0
    assert empty.profit_factor is None
    assert empty.win_rate is None
    only_wins = compute([tr("5", 1)], Decimal(1000))
    assert only_wins.profit_factor is None  # undefined without losses
    assert only_wins.sharpe is None  # one day only
    split = by_symbol([tr("5", 1), tr("-2", 2, root="MCL")], Decimal(1000))
    assert list(split) == ["MCL", "MES"]


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> BacktestResult:
    base = tmp_path_factory.mktemp("bt")
    pack: PackInfo = write_pack(
        sample_data.generate(dates=(date(2026, 11, 3), date(2026, 11, 4)), roots=("MES", "M6E")),
        base / "pack",
    )
    return run(pack, base / "runs")


def test_runner_produces_complete_results(result: BacktestResult) -> None:
    m = result.metrics
    assert m.trades > 0
    assert set(result.per_symbol) == {"MES", "M6E"}
    assert sum(s.trades for s in result.per_symbol.values()) == m.trades
    assert result.ending_equity == result.starting_equity + m.net_pnl
    assert m.commissions > 0
    assert result.decisions_approved >= m.trades
    for t in result.trades:
        assert t.commission > 0  # every round trip paid fees


def test_pnl_reconciles_with_simulated_account(result: BacktestResult) -> None:
    # Every position is closed by the 15:50 CT flat-by rule, so the sum of net trade P&L must
    # equal the simulated broker's balance change to the cent.
    assert result.open_positions_at_end == 0
    assert result.account_balance - result.starting_equity == result.metrics.net_pnl


def test_report_files_written(result: BacktestResult) -> None:
    data = json.loads((result.folder / "result.json").read_text("utf-8"))
    for key in (
        "profit_factor",
        "expectancy",
        "max_drawdown",
        "sharpe",
        "win_rate",
        "avg_win",
        "avg_loss",
        "trades",
    ):
        assert key in data["metrics"]
    page = (result.folder / "report.html").read_text("utf-8")
    assert "PIPELINE TEST ONLY, NOT A RESULT" in page
    assert data["result_label"] == "Pipeline test only, not a result"
    assert "SAMPLE DATA" in page
    assert "<svg" in page
    assert "http" not in page.split("<body>")[1]  # self-contained


def test_list_runs(result: BacktestResult) -> None:
    runs = list_runs(result.folder.parent)
    assert runs[0]["run_id"] == result.run_id
    assert runs[0]["trades"] == result.metrics.trades
    assert runs[0]["result_label"] == "Pipeline test only, not a result"


def test_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from engine.backtest import main  # noqa: PLC0415
    from engine.nautilus.catalog import read_pack  # noqa: PLC0415

    pack_dir = tmp_path / "pack"
    write_pack(sample_data.generate(dates=(date(2026, 11, 4),), roots=("MCL",)), pack_dir)
    # Use the prebuilt small pack as-is (ensure_pack would rebuild the full default pack).
    monkeypatch.setattr("engine.backtest.ensure_pack", read_pack)
    assert main(["--out", str(tmp_path / "runs"), "--pack", str(pack_dir)]) == 0
    assert "report.html" in capsys.readouterr().out
