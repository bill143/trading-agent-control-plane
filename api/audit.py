"""Append-only, tamper-evident audit log (build step 1.19).

Every login, command, setting change and order is written as one JSON line. Each record carries
the SHA-256 of the previous record, so editing, deleting or reordering any line breaks the chain
and :meth:`AuditLog.verify` reports exactly where. Writes are flushed and fsync'd before the call
returns. Values passed in ``details`` must never contain secrets (enforced by key-name check).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

GENESIS: Final = "0" * 64
_SECRET_KEY: Final = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|totp|otp_seed)")


class AuditCategory(StrEnum):
    LOGIN = "login"
    COMMAND = "command"
    SETTING = "setting"
    ORDER = "order"
    RISK = "risk"
    SECURITY = "security"
    SYSTEM = "system"


class AuditSink(Protocol):
    def record(
        self, category: AuditCategory, action: str, actor: str, details: Mapping[str, Any]
    ) -> None: ...


class AuditIntegrityError(RuntimeError):
    """The audit chain is broken: a record was edited, removed or reordered."""


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    ts: str
    category: str
    action: str
    actor: str
    details: dict[str, Any]
    prev: str
    hash: str


def _digest(body: Mapping[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check_no_secrets(details: Mapping[str, Any], path: str = "") -> None:
    for k, v in details.items():
        if _SECRET_KEY.search(str(k)):
            raise ValueError(f"audit details must not contain secrets (field '{path}{k}')")
        if isinstance(v, Mapping):
            _check_no_secrets(v, f"{path}{k}.")


class AuditLog:
    """Thread-safe append-only log at ``path`` (one JSON object per line)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        existing = list(self._iter_raw())
        last = existing[-1] if existing else None
        self._seq = last.seq if last else 0
        self._prev = last.hash if last else GENESIS

    def record(
        self, category: AuditCategory, action: str, actor: str, details: Mapping[str, Any]
    ) -> None:
        _check_no_secrets(details)
        with self._lock:
            body = {
                "seq": self._seq + 1,
                "ts": datetime.now(UTC).isoformat(timespec="microseconds"),
                "category": category.value,
                "action": action,
                "actor": actor,
                "details": json.loads(json.dumps(details, default=str)),
                "prev": self._prev,
            }
            body["hash"] = _digest(body)
            line = json.dumps(body, sort_keys=True, ensure_ascii=False) + "\n"
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            self._seq += 1
            self._prev = body["hash"]

    def _iter_raw(self) -> Iterator[AuditRecord]:
        with self.path.open(encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    yield AuditRecord(**json.loads(line))
                except (json.JSONDecodeError, TypeError) as exc:
                    raise AuditIntegrityError(f"line {n}: unreadable record") from exc

    def records(self) -> list[AuditRecord]:
        return list(self._iter_raw())

    def tail(self, n: int = 100) -> list[AuditRecord]:
        return self.records()[-n:]

    def verify(self) -> int:
        """Check the whole chain; return the number of records. Raises on any break."""
        prev, count = GENESIS, 0
        for rec in self._iter_raw():
            count += 1
            if rec.seq != count:
                raise AuditIntegrityError(f"record {count}: sequence {rec.seq} out of order")
            if rec.prev != prev:
                raise AuditIntegrityError(f"record {rec.seq}: previous-hash link broken")
            body = {k: v for k, v in rec.__dict__.items() if k != "hash"}
            if _digest(body) != rec.hash:
                raise AuditIntegrityError(f"record {rec.seq}: contents were modified")
            prev = rec.hash
        return count


class MemoryAudit:
    """In-memory sink for unit tests of components that write audit records."""

    def __init__(self) -> None:
        self.items: list[tuple[AuditCategory, str, str, dict[str, Any]]] = []

    def record(
        self, category: AuditCategory, action: str, actor: str, details: Mapping[str, Any]
    ) -> None:
        _check_no_secrets(details)
        self.items.append((category, action, actor, dict(details)))
