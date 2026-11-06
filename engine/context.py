"""Shared engine context: everything the symbol engines, the portfolio guard and the control API
share inside one node, plus order-safety records (build step 1.12):

* :class:`SignalLedger`: one signal can never create two orders (persistent across restarts).
* :class:`DecisionLog`: every risk decision, approved or refused, with all checks.
* :class:`PositionJournal` and :func:`reconcile`: on every restart the engine reads the broker's
  open positions and compares them with what it last recorded *before* any strategy may act.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from api.audit import AuditCategory, AuditSink
from engine.events import EventCalendar
from engine.state import StateStore
from risk.fees import DailyFeeFilter
from risk.killswitch import KillSwitch
from risk.manager import Decision, OrderIntent, PortfolioRiskManager
from risk.settings import RiskSettings, RiskSettingsStore

DECISION_TAIL: Final = 500


class Mode(StrEnum):
    DEMO = "demo"  # sample data, simulated fills, paced replay
    BACKTEST = "backtest"  # historical data at full speed
    PAPER = "paper"  # live data, broker paper account (step 1.23)


class SignalLedger:
    """Persistent set of signal IDs that already produced an order."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        if path is not None and path.exists():
            self._seen = {ln.strip() for ln in path.read_text("utf-8").splitlines() if ln.strip()}

    def claim(self, signal_id: str) -> bool:
        """Record ``signal_id``; True only the first time it is ever seen."""
        with self._lock:
            if signal_id in self._seen:
                return False
            self._seen.add(signal_id)
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(signal_id + "\n")
            return True

    def __contains__(self, signal_id: object) -> bool:
        return signal_id in self._seen

    def __len__(self) -> int:
        return len(self._seen)


class DecisionLog:
    """Append-only log of risk decisions; keeps a bounded tail in memory for the API."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._tail: deque[dict[str, Any]] = deque(maxlen=DECISION_TAIL)
        self.approved = 0
        self.refused = 0

    def add(self, decision: Decision, outcome: str) -> None:
        row = {**decision.to_dict(), "outcome": outcome}
        with self._lock:
            self._tail.append(row)
            if decision.approved:
                self.approved += 1
            else:
                self.refused += 1
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")

    def tail(self, n: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._tail)[-n:]


@dataclass(frozen=True)
class PositionRecord:
    symbol: str
    qty: int  # signed
    avg_price: float


class PositionJournal:
    """What the engine last knew about its open positions (written on every change)."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lock = threading.Lock()

    def save(self, positions: Mapping[str, PositionRecord]) -> None:
        if self._path is None:
            return
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {s: {"qty": p.qty, "avg_price": p.avg_price} for s, p in positions.items()}
                ),
                encoding="utf-8",
            )
            tmp.replace(self._path)

    def load(self) -> dict[str, PositionRecord]:
        if self._path is None or not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text("utf-8"))
        return {s: PositionRecord(s, int(v["qty"]), float(v["avg_price"])) for s, v in raw.items()}


class ReconcileStatus(StrEnum):
    CLEAN = "clean"  # broker and journal agree
    ADOPTED = "adopted"  # broker has positions; engine adopts them (brackets re-placed)
    MISMATCH = "mismatch"  # disagreement: trading blocked until an Owner reviews


@dataclass(frozen=True)
class ReconcileReport:
    status: ReconcileStatus
    broker: dict[str, int]
    journal: dict[str, int]
    differences: list[str] = field(default_factory=list)
    at: str = ""

    @property
    def may_trade(self) -> bool:
        return self.status is not ReconcileStatus.MISMATCH

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "may_trade": self.may_trade,
            "broker": self.broker,
            "journal": self.journal,
            "differences": self.differences,
            "at": self.at,
        }


def reconcile(broker: Mapping[str, int], journal: Mapping[str, PositionRecord]) -> ReconcileReport:
    """Compare broker-reported signed positions with the journal. The broker is the truth."""
    b = {s: q for s, q in broker.items() if q != 0}
    j = {s: p.qty for s, p in journal.items() if p.qty != 0}
    diffs = [
        f"{s}: broker {b.get(s, 0):+d}, engine last recorded {j.get(s, 0):+d}"
        for s in sorted(set(b) | set(j))
        if b.get(s, 0) != j.get(s, 0)
    ]
    if diffs:
        status = ReconcileStatus.MISMATCH
    elif b:
        status = ReconcileStatus.ADOPTED
    else:
        status = ReconcileStatus.CLEAN
    return ReconcileReport(status, b, j, diffs, datetime.now(UTC).isoformat())


@dataclass
class Faults:
    """Demo-mode fault injection, used to demonstrate the automatic kill-switch triggers."""

    data_feed: set[str] = field(default_factory=set)  # roots whose data is withheld
    broker_disconnected: bool = False


class EngineContext:
    """Objects shared by every component in one engine node."""

    def __init__(
        self,
        mode: Mode,
        settings_store: RiskSettingsStore,
        audit: AuditSink,
        events: EventCalendar,
        *,
        state_dir: Path | None,
        sample_data: bool,
    ) -> None:
        self.mode = mode
        self.sample_data = sample_data
        self.settings_store = settings_store
        self.audit = audit
        self.events = events
        self.store = StateStore()
        self.fee_filter = DailyFeeFilter()
        self.kill = KillSwitch(audit)
        self.risk = PortfolioRiskManager(
            lambda: self.settings_store.current, self.fee_filter, events, self.kill
        )
        self.ledger = SignalLedger(state_dir / "signals.txt" if state_dir else None)
        self.decisions = DecisionLog(state_dir / "decisions.jsonl" if state_dir else None)
        self.journal = PositionJournal(state_dir / "positions.json" if state_dir else None)
        self.reconciled = threading.Event()
        self.reconcile_report: ReconcileReport | None = None
        self.faults = Faults()
        self.orders_submitted = 0
        #: Entry orders sent but not yet filled, by signal ID (guarded by ``store.lock``).
        self.pending: dict[str, OrderIntent] = {}

    @property
    def settings(self) -> RiskSettings:
        return self.settings_store.current

    def record_reconcile(self, report: ReconcileReport) -> None:
        self.reconcile_report = report
        self.audit.record(
            AuditCategory.SYSTEM, "restart_reconciliation", "engine", report.to_dict()
        )
        if report.may_trade:
            self.reconciled.set()
        else:
            self.reconciled.clear()
