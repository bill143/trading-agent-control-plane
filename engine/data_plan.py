"""Historical market-data download plan and free price quote (C0.07, Bill's decision 2026-11-06).

The spec needs 3+ years of tick and order-book history for training, walk-forward testing and
the locked holdout (DECISIONS.md section B). Databento bills historical data per byte. This module

* defines the exact plan: dataset, schema, contracts, date ranges and purpose of every request;
* asks Databento for the **exact price and size of each request without downloading anything**
  (``metadata.get_cost`` / ``metadata.get_billable_size``, which Databento does not charge for);
* never downloads. Downloading paid data happens only after Bill signs off on the quoted total
  (recorded in DECISIONS.md), in a later build step.

Contracts are requested as volume-based continuous front months (``MES.v.0``), so only the
contract that was actually being traded each day is paid for, not every listed expiry.

Run (after the Databento key is saved in Settings > Connections):
    uv run python -m engine.data_plan            # prints the quote and saves it
or press "Get price quote" in Settings > Connections.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final, Protocol

DATASET: Final = "GLBX.MDP3"
# Windows from DECISIONS.md section B (dates are inclusive CT session dates; Databento ranges
# are [start, end) in UTC, so the end is the day after the last session).
DEV_START: Final = date(2022, 11, 1)
DEV_END_EXCL: Final = date(2025, 11, 1)
HOLDOUT_START: Final = date(2025, 11, 2)  # Sunday evening open of the 2025-11-03 session
HOLDOUT_END_EXCL: Final = date(2026, 10, 31)
DEPTH_START: Final = date(2024, 11, 1)  # last 12 months of the development window

ACTIVE: Final = ("MES", "MCL", "MGC", "M6E", "10Y")
PROP_LIST: Final = ("MNQ", "MYM", "MBT")
ALL_ROOTS: Final = ACTIVE + PROP_LIST


def front(roots: tuple[str, ...]) -> list[str]:
    return [f"{r}.v.0" for r in roots]


@dataclass(frozen=True)
class DataRequest:
    key: str
    tier: str  # "A" required now, "B" required for training/testing, "C" for order-flow work
    purpose: str
    schema: str
    symbols: tuple[str, ...]
    start: date
    end: date  # exclusive
    stype_in: str = "continuous"
    holdout: bool = False  # stored separately; reads are audit-logged (step 6.03)

    def describe(self) -> str:
        return (
            f"{self.schema} {', '.join(s.split('.')[0] for s in self.symbols)} "
            f"{self.start:%Y-%m-%d}..{self.end:%Y-%m-%d}"
        )


PLAN: Final[tuple[DataRequest, ...]] = (
    DataRequest(
        "definitions",
        "A",
        "Contract definitions: tick size, expiry, roll mapping",
        "definition",
        tuple(front(ALL_ROOTS)),
        DEV_START,
        HOLDOUT_END_EXCL,
    ),
    DataRequest(
        "bars_1m",
        "A",
        "1-minute bars for ATR, regime and level features",
        "ohlcv-1m",
        tuple(front(ALL_ROOTS)),
        DEV_START,
        HOLDOUT_END_EXCL,
    ),
    DataRequest(
        "statistics",
        "A",
        "Daily settlement, open interest, session high/low",
        "statistics",
        tuple(front(ALL_ROOTS)),
        DEV_START,
        HOLDOUT_END_EXCL,
    ),
    DataRequest(
        "tbbo_dev",
        "B",
        "Every trade with the best bid/ask at that moment "
        "(tape, VWAP, slippage model) - development window",
        "tbbo",
        tuple(front(ALL_ROOTS)),
        DEV_START,
        DEV_END_EXCL,
    ),
    DataRequest(
        "tbbo_holdout",
        "B",
        "Same, locked holdout year (never used for training)",
        "tbbo",
        tuple(front(ALL_ROOTS)),
        HOLDOUT_START,
        HOLDOUT_END_EXCL,
        holdout=True,
    ),
    DataRequest(
        "mbp1_active_dev",
        "B",
        "Every top-of-book change (book lean, spread, queue) "
        "for the five Phase 1 contracts - development window",
        "mbp-1",
        tuple(front(ACTIVE)),
        DEV_START,
        DEV_END_EXCL,
    ),
    DataRequest(
        "mbp1_active_holdout",
        "B",
        "Same, locked holdout year",
        "mbp-1",
        tuple(front(ACTIVE)),
        HOLDOUT_START,
        HOLDOUT_END_EXCL,
        holdout=True,
    ),
    DataRequest(
        "mbp10_index_12m",
        "C",
        "10-level depth for the order-flow expert "
        "(MES, MNQ), last 12 months of the development window",
        "mbp-10",
        tuple(front(("MES", "MNQ"))),
        DEPTH_START,
        DEV_END_EXCL,
    ),
)


class CostClient(Protocol):
    """The two free Databento metadata calls (``databento.Historical().metadata``)."""

    def get_cost(self, **kw: Any) -> float: ...
    def get_billable_size(self, **kw: Any) -> int: ...


@dataclass(frozen=True)
class QuoteLine:
    key: str
    tier: str
    purpose: str
    request: str
    holdout: bool
    usd: Decimal
    gigabytes: Decimal


@dataclass(frozen=True)
class Quote:
    lines: list[QuoteLine]
    quoted_at: str
    dataset: str = DATASET

    def total(self, tiers: str = "ABC") -> Decimal:
        return sum((ln.usd for ln in self.lines if ln.tier in tiers), Decimal(0))

    def gigabytes(self, tiers: str = "ABC") -> Decimal:
        return sum((ln.gigabytes for ln in self.lines if ln.tier in tiers), Decimal(0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "quoted_at": self.quoted_at,
            "lines": [
                {**asdict(ln), "usd": str(ln.usd), "gigabytes": str(ln.gigabytes)}
                for ln in self.lines
            ],
            "totals": {
                "tier_A": str(self.total("A")),
                "tier_A_B": str(self.total("AB")),
                "all": str(self.total("ABC")),
                "gigabytes_all": str(self.gigabytes("ABC")),
            },
            "note": "Quoted by Databento's free metadata endpoints. Nothing was downloaded "
            "or charged. Paid downloads need Bill's sign-off on this total.",
        }


def _money(x: float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def quote(
    client: CostClient,
    plan: tuple[DataRequest, ...] = PLAN,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Quote:
    lines: list[QuoteLine] = []
    for r in plan:
        args = {
            "dataset": DATASET,
            "symbols": list(r.symbols),
            "schema": r.schema,
            "stype_in": r.stype_in,
            "start": r.start.isoformat(),
            "end": r.end.isoformat(),
        }
        usd = _money(client.get_cost(**args))
        size = client.get_billable_size(**args)
        lines.append(
            QuoteLine(
                r.key,
                r.tier,
                r.purpose,
                r.describe(),
                r.holdout,
                usd,
                (Decimal(size) / Decimal(1024**3)).quantize(Decimal("0.01")),
            )
        )
    return Quote(lines, now().isoformat())


def render(q: Quote) -> str:
    rows = [f"{'Tier':<5}{'Request':<58}{'GB':>10}{'USD':>12}"]
    for ln in q.lines:
        rows.append(f"{ln.tier:<5}{ln.request[:57]:<58}{ln.gigabytes:>10}{ln.usd:>12}")
    rows += [
        f"{'':<5}{'Tier A (required now)':<58}{'':>10}{q.total('A'):>12}",
        f"{'':<5}{'Tiers A+B (training, walk-forward, holdout)':<58}{'':>10}{q.total('AB'):>12}",
        f"{'':<5}{'All tiers incl. C (order-flow depth)':<58}{q.gigabytes():>10}{q.total():>12}",
    ]
    return "\n".join(rows)


class _DatabentoCost:
    """Only the two free metadata calls of the real client are reachable from here."""

    def __init__(self, api_key: str) -> None:
        import databento  # noqa: PLC0415 - optional at import time

        self._meta: Any = databento.Historical(key=api_key).metadata

    def get_cost(self, **kw: Any) -> float:
        return float(self._meta.get_cost(**kw))

    def get_billable_size(self, **kw: Any) -> int:
        return int(self._meta.get_billable_size(**kw))


def databento_client(api_key: str) -> CostClient:
    return _DatabentoCost(api_key)


def main() -> int:
    from api.audit import AuditCategory, AuditLog  # noqa: PLC0415
    from api.vault import Vault, VaultError, default_key_provider  # noqa: PLC0415
    from engine import paths  # noqa: PLC0415

    state = paths.state_dir()
    audit = AuditLog(state / "audit.jsonl")
    vault = Vault(state / "vault.json", default_key_provider(state / "vault.json"), audit)
    try:
        key = vault.get("databento.api_key").reveal()
    except VaultError:
        print("Save your Databento API key in Settings > Connections first.")
        return 2
    q = quote(databento_client(key))
    out = save_quote(q, state)
    audit.record(
        AuditCategory.COMMAND,
        "data_plan_quoted",
        "engine",
        {"total_all": str(q.total()), "file": str(out)},
    )
    print(render(q))
    print(f"\nSaved to {out}. Nothing was downloaded or charged.")
    return 0


def save_quote(q: Quote, state_dir: Path) -> Path:
    out = state_dir / "data_plan_quote.json"
    out.write_text(json.dumps(q.to_dict(), indent=2), "utf-8")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
