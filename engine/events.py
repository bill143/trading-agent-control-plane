"""Scheduled economic events used by the news blackout (risk rule: no new entries within X
minutes of a scheduled release).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from engine.calendar import CT


class Impact(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class EconomicEvent:
    ts: datetime  # timezone-aware
    name: str
    impact: Impact
    roots: tuple[str, ...]  # contracts the event affects
    source: str = "calendar"

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("EconomicEvent.ts must be timezone-aware")

    def affects(self, root: str) -> bool:
        return root in self.roots

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.astimezone(CT).isoformat(),
            "name": self.name,
            "impact": self.impact.value,
            "roots": list(self.roots),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EconomicEvent:
        return cls(
            ts=datetime.fromisoformat(d["ts"]),
            name=str(d["name"]),
            impact=Impact(d["impact"]),
            roots=tuple(d["roots"]),
            source=str(d.get("source", "calendar")),
        )


class EventCalendar:
    """Ordered scheduled events with blackout-window queries."""

    def __init__(self, events: list[EconomicEvent]) -> None:
        self._events = sorted(events, key=lambda e: e.ts)

    def __iter__(self) -> Iterator[EconomicEvent]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def blocking(
        self, root: str, at: datetime, before: timedelta, after: timedelta
    ) -> EconomicEvent | None:
        """The event that puts ``root`` in blackout at ``at``, if any: ``at`` falls in
        [event - before, event + after]."""
        for e in self._events:
            if e.affects(root) and e.ts - before <= at <= e.ts + after:
                return e
        return None

    def next_event(self, root: str, at: datetime) -> EconomicEvent | None:
        for e in self._events:
            if e.affects(root) and e.ts >= at:
                return e
        return None

    def save(self, path: Path) -> None:
        path.write_text(json.dumps([e.to_dict() for e in self._events], indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> EventCalendar:
        return cls([EconomicEvent.from_dict(d) for d in json.loads(path.read_text("utf-8"))])
