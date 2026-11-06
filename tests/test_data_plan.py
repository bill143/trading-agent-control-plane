"""C0.07: historical data download plan and free Databento price quote (never downloads)."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from engine import data_plan as dp

ROOT = Path(__file__).resolve().parent.parent


class FakeMetadata:
    """Stands in for databento.Historical().metadata; only the two free calls exist."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_cost(self, **kw: Any) -> float:
        self.calls.append(kw)
        days = (date.fromisoformat(kw["end"]) - date.fromisoformat(kw["start"])).days
        return (
            {
                "definition": 0.001,
                "ohlcv-1m": 0.002,
                "statistics": 0.001,
                "tbbo": 0.05,
                "mbp-1": 0.12,
                "mbp-10": 0.9,
            }[kw["schema"]]
            * days
            * len(kw["symbols"])
        )

    def get_billable_size(self, **kw: Any) -> int:
        return 1024**3 // 4


def test_plan_matches_locked_windows_in_decisions() -> None:
    text = (ROOT / "DECISIONS.md").read_text("utf-8")
    dev = re.search(r"\| Development \| (\S+) \| (\S+) \|", text)
    hold = re.search(r"\| \*\*Locked holdout\*\* \| \*\*(\S+)\*\* \| \*\*(\S+)\*\*", text)
    assert dev is not None
    assert hold is not None
    assert date.fromisoformat(dev.group(1)) == dp.DEV_START
    assert date.fromisoformat(dev.group(2)) < dp.DEV_END_EXCL
    assert date.fromisoformat(hold.group(1)) > dp.HOLDOUT_START
    assert date.fromisoformat(hold.group(2)) < dp.HOLDOUT_END_EXCL
    assert dp.DEV_END_EXCL <= dp.HOLDOUT_START  # no overlap between training and holdout
    # 3+ years of development history
    assert (dp.DEV_END_EXCL - dp.DEV_START).days >= 3 * 365


def test_holdout_requests_flagged_and_training_requests_stop_before_it() -> None:
    for r in dp.PLAN:
        if r.holdout:
            assert r.start >= dp.HOLDOUT_START
        elif r.tier != "A":
            assert r.end <= dp.DEV_END_EXCL, r.key
    assert {r.key for r in dp.PLAN if r.holdout} == {"tbbo_holdout", "mbp1_active_holdout"}


def test_front_month_continuous_symbols_for_every_contract() -> None:
    covered = {s.split(".")[0] for r in dp.PLAN for s in r.symbols}
    assert covered == set(dp.ALL_ROOTS)
    for r in dp.PLAN:
        assert r.stype_in == "continuous"
        assert all(s.endswith(".v.0") for s in r.symbols)


def test_quote_uses_only_free_metadata_calls_and_totals() -> None:
    fake = FakeMetadata()
    q = dp.quote(fake)
    assert len(fake.calls) == len(dp.PLAN)
    assert all(c["dataset"] == "GLBX.MDP3" for c in fake.calls)
    assert q.total("A") < q.total("AB") < q.total()
    assert q.total() == sum(ln.usd for ln in q.lines)
    assert q.gigabytes() == Decimal("0.25") * len(dp.PLAN)
    d = q.to_dict()
    assert "Nothing was downloaded" in d["note"]
    assert json.dumps(d)
    assert "Tiers A+B" in dp.render(q)


def test_module_cannot_download() -> None:
    src = (ROOT / "engine" / "data_plan.py").read_text("utf-8")
    for forbidden in ("timeseries", "batch.submit_job", "batch.download", "get_range"):
        assert forbidden not in src
