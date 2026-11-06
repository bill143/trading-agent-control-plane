"""Backtest runner with results report (build step 1.14).

Runs every session in a data pack through the full engine (symbol engines, risk manager, kill
switch, simulated fills with per-contract commissions and one-tick slippage) at full speed and
writes a results folder:

    <runs>/<run id>/result.json    metrics, per-symbol metrics, trades, refusals, settings
    <runs>/<run id>/report.html    the same as a self-contained page the app shows

Usage::

    uv run python -m engine.backtest                 # sample pack, default settings
    uv run python -m engine.backtest --out ./runs
"""

from __future__ import annotations

import argparse
import json
import secrets
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.model.currencies import USD

from api.audit import AuditLog
from engine import paths
from engine.context import EngineContext, Mode
from engine.metrics import Metrics, TradeRecord, by_symbol, compute
from engine.nautilus.catalog import PackInfo, ensure_pack
from engine.nautilus.instruments import GLOBEX
from engine.nautilus.node import DEFAULT_BALANCE, EngineNode
from engine.report import render_html
from risk.settings import RiskSettings, RiskSettingsStore


@dataclass(frozen=True)
class BacktestResult:
    run_id: str
    started: str
    seconds: float
    data_label: str
    data_digest: str
    dates: list[str]
    starting_equity: Decimal
    ending_equity: Decimal
    account_balance: Decimal  # simulated broker's final cash balance
    open_positions_at_end: int
    metrics: Metrics
    per_symbol: dict[str, Metrics]
    trades: list[TradeRecord]
    refusals: dict[str, int]
    decisions_approved: int
    decisions_refused: int
    kill_switch: dict[str, object]
    settings: dict[str, Any]
    folder: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started": self.started,
            "seconds": round(self.seconds, 1),
            "data_label": self.data_label,
            "result_label": result_label(self.data_label),
            "data_digest": self.data_digest,
            "dates": self.dates,
            "starting_equity": str(self.starting_equity),
            "ending_equity": str(self.ending_equity),
            "account_balance": str(self.account_balance),
            "open_positions_at_end": self.open_positions_at_end,
            "metrics": self.metrics.to_dict(),
            "per_symbol": {k: v.to_dict() for k, v in self.per_symbol.items()},
            "trades": [t.to_dict() for t in self.trades],
            "refusals": self.refusals,
            "decisions": {"approved": self.decisions_approved, "refused": self.decisions_refused},
            "kill_switch": self.kill_switch,
            "settings": self.settings,
        }


def run(
    pack: PackInfo,
    runs_dir: Path,
    settings: RiskSettings | None = None,
    starting_balance: Decimal = DEFAULT_BALANCE,
) -> BacktestResult:
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    folder = runs_dir / run_id
    folder.mkdir(parents=True, exist_ok=False)
    work = Path(tempfile.mkdtemp(prefix="bt-", dir=folder))
    audit = AuditLog(work / "audit.jsonl")
    store = RiskSettingsStore(work / "risk.json", secrets.token_bytes(32), audit)
    if settings is not None:
        from risk.authority import Principal, Role  # noqa: PLC0415

        store.update(Principal("backtest", Role.OWNER), **settings.to_dict())
    ctx = EngineContext(
        Mode.BACKTEST,
        store,
        audit,
        pack.events,
        state_dir=work,
        sample_data="sample" in pack.label.lower(),
    )
    node = EngineNode(ctx, pack, starting_balance=starting_balance)
    t0 = time.time()
    started = datetime.now(UTC).isoformat()
    try:
        node.run_backtest()
        trades = sorted(ctx.trades, key=lambda t: t.closed)
        account = node.engine.portfolio.account(GLOBEX)
        balance = Decimal(str(account.balance_total(USD).as_decimal()))
        still_open = len(node.engine.cache.positions_open())
    finally:
        node.dispose()
    refusals: Counter[str] = Counter()
    for row in ctx.decisions.tail(10_000):
        for c in row["checks"]:
            if not c["passed"]:
                refusals[c["name"]] += 1
    metrics = compute(trades, starting_balance)
    result = BacktestResult(
        run_id=run_id,
        started=started,
        seconds=time.time() - t0,
        data_label=pack.label,
        data_digest=pack.digest,
        dates=[d.isoformat() for d in pack.dates],
        starting_equity=starting_balance,
        ending_equity=starting_balance + metrics.net_pnl,
        account_balance=balance,
        open_positions_at_end=still_open,
        metrics=metrics,
        per_symbol=by_symbol(trades, starting_balance),
        trades=trades,
        refusals=dict(refusals.most_common()),
        decisions_approved=ctx.decisions.approved,
        decisions_refused=ctx.decisions.refused,
        kill_switch=ctx.kill.state.to_dict(),
        settings=ctx.settings.to_dict(),
        folder=folder,
    )
    (folder / "result.json").write_text(json.dumps(result.to_dict(), indent=2), "utf-8")
    (folder / "report.html").write_text(render_html(result.to_dict()), "utf-8")
    return result


PIPELINE_LABEL = "Pipeline test only, not a result"


def result_label(data_label: str) -> str:
    """Every metric computed on synthetic sample data carries this label (DECISIONS.md D-13)."""
    return PIPELINE_LABEL if "sample" in data_label.lower() else ""


def list_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """Summaries of previous runs, newest first (for the app's backtest viewer)."""
    out: list[dict[str, Any]] = []
    for f in sorted(runs_dir.glob("*/result.json"), reverse=True):
        try:
            d = json.loads(f.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        m = d["metrics"]
        out.append(
            {
                "run_id": d["run_id"],
                "started": d["started"],
                "dates": d["dates"],
                "data_label": d["data_label"],
                "result_label": result_label(d["data_label"]),
                "trades": m["trades"],
                "net_pnl": m["net_pnl"],
                "profit_factor": m["profit_factor"],
                "max_drawdown": m["max_drawdown"],
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a backtest over the sample data pack.")
    ap.add_argument("--out", type=Path, default=None, help="runs folder (default: app data)")
    ap.add_argument("--pack", type=Path, default=None, help="data pack folder")
    args = ap.parse_args(argv)
    pack = ensure_pack(args.pack or paths.sample_pack_dir())
    result = run(pack, args.out or paths.runs_dir())
    m = result.metrics
    print(
        f"[{result_label(result.data_label) or 'real data'}] "
        f"{result.run_id}: {m.trades} trades, net ${m.net_pnl:.2f}, "
        f"PF {m.profit_factor if m.profit_factor is not None else '-'}, "
        f"max DD ${m.max_drawdown:.2f} -> {result.folder / 'report.html'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
