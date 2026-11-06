"""Kill switch (build step 1.11).

* Manual: the button on the desktop app (and later the phone) trips it.
* Automatic: the engine trips it on a risk-limit breach, a market-data feed failure or a broker
  disconnect.

When tripped, every registered flatten handler runs: cancel all working orders and close all
positions. New entries are refused until a human Owner resets it. Each trip and reset is written
to the audit log. Tripping is idempotent; the first reason is kept and later ones are appended.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from api.audit import AuditCategory, AuditSink
from risk.authority import Permission, Principal


class TripSource(StrEnum):
    MANUAL = "manual"
    RISK_BREACH = "risk_breach"
    DATA_FEED = "data_feed"
    BROKER_DISCONNECT = "broker_disconnect"
    SETTINGS_TAMPERED = "settings_tampered"
    SUPERVISOR = "supervisor"


@dataclass(frozen=True)
class Trip:
    source: TripSource
    reason: str
    actor: str
    at: datetime


@dataclass
class KillSwitchState:
    tripped: bool = False
    trips: list[Trip] = field(default_factory=list)
    flattened: bool = False
    flatten_error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "tripped": self.tripped,
            "flattened": self.flattened,
            "flatten_error": self.flatten_error,
            "trips": [
                {
                    "source": t.source.value,
                    "reason": t.reason,
                    "actor": t.actor,
                    "at": t.at.isoformat(),
                }
                for t in self.trips
            ],
        }


FlattenHandler = Callable[[Trip], None]


class KillSwitch:
    def __init__(self, audit: AuditSink, clock: Callable[[], datetime] | None = None) -> None:
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._state = KillSwitchState()
        self._handlers: list[FlattenHandler] = []

    @property
    def tripped(self) -> bool:
        return self._state.tripped

    @property
    def state(self) -> KillSwitchState:
        with self._lock:
            return KillSwitchState(
                self._state.tripped,
                list(self._state.trips),
                self._state.flattened,
                self._state.flatten_error,
            )

    def on_trip(self, handler: FlattenHandler) -> None:
        self._handlers.append(handler)

    def trip(self, principal: Principal, source: TripSource, reason: str) -> bool:
        """Trip the switch. Returns True if this call tripped it (False if already tripped)."""
        principal.require(Permission.KILL_SWITCH)
        with self._lock:
            trip = Trip(source, reason, principal.id, self._clock())
            first = not self._state.tripped
            self._state.tripped = True
            self._state.trips.append(trip)
            self._audit.record(
                AuditCategory.RISK,
                "kill_switch_tripped" if first else "kill_switch_trip_repeated",
                principal.id,
                {"source": source.value, "reason": reason},
            )
            if first:
                self._flatten(trip)
            return first

    def _flatten(self, trip: Trip) -> None:
        errors: list[str] = []
        for handler in self._handlers:
            try:
                handler(trip)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        self._state.flattened = not errors
        self._state.flatten_error = "; ".join(errors)
        self._audit.record(
            AuditCategory.RISK,
            "flatten_complete" if not errors else "flatten_failed",
            "engine",
            {"handlers": len(self._handlers), "errors": errors},
        )

    def confirm_flat(self) -> None:
        """Called by the engine once no positions or working orders remain."""
        with self._lock:
            self._state.flattened = True

    def reset(self, principal: Principal, note: str) -> None:
        principal.require(Permission.RESET_KILL_SWITCH)
        with self._lock:
            if not self._state.tripped:
                return
            self._audit.record(
                AuditCategory.RISK,
                "kill_switch_reset",
                principal.id,
                {"note": note, "trips": len(self._state.trips)},
            )
            self._state = KillSwitchState()
