"""Risk settings and the locked settings store (build steps 1.09 and 1.10).

* :class:`RiskSettings` is immutable. Every component receives a read-only snapshot.
* :class:`RiskSettingsStore` is the only way to change them: the caller must be a human principal
  with the Owner role. AI principals are always refused. Every accepted or refused change is
  written to the audit log.
* The settings file is signed with HMAC-SHA256 (key held in the secrets vault). A file edited
  outside the app fails verification and the engine refuses to trade until an Owner re-saves.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, replace
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from api.audit import AuditCategory, AuditSink
from engine.instruments import ACTIVE_ROOTS, SPECS
from risk.authority import NotAuthorized, Permission, Principal

SCHEMA_VERSION: Final = 1


class SettingsError(ValueError):
    """Invalid settings values."""


class SettingsTampered(RuntimeError):  # noqa: N818
    """The settings file signature does not match: it was changed outside the app."""


def _default_quotas() -> Mapping[str, int]:
    return MappingProxyType({r: 40 for r in ACTIVE_ROOTS})


def _default_targets() -> Mapping[str, int]:
    # Target ticks per symbol for the fee filter; starter strategy defaults (step 1.13).
    return MappingProxyType({"MES": 8, "MCL": 10, "MGC": 10, "M6E": 6, "10Y": 6})


@dataclass(frozen=True)
class RiskSettings:
    """Hard limits. Units: USD unless stated."""

    daily_loss_limit: Decimal = Decimal(500)
    per_trade_loss_limit: Decimal = Decimal(150)
    #: Share of available buying power kept in reserve (plan: 30-50%).
    buying_power_buffer: Decimal = Decimal("0.40")
    max_open_positions: int = 3
    max_positions_per_symbol: int = 1
    #: Positions in one correlation group count as one exposure; at most this many groups
    #: may be positioned in the same direction at once.
    max_same_direction: int = 2
    max_contracts_per_order: int = 2
    daily_contract_quota_total: int = 150
    daily_contract_quota: Mapping[str, int] = field(default_factory=_default_quotas)
    target_ticks: Mapping[str, int] = field(default_factory=_default_targets)
    #: Fee filter: target ticks x tick value must be at least this multiple of round-trip fees.
    fee_filter_multiple: Decimal = Decimal(2)
    news_blackout_before_min: int = 5
    news_blackout_after_min: int = 5
    #: No new entries this many minutes before a session close.
    no_entry_before_close_min: int = 15
    #: Kill switch fires if an open session has no market data for this many seconds.
    data_stale_seconds: int = 30
    allowed_roots: tuple[str, ...] = ACTIVE_ROOTS

    def __post_init__(self) -> None:
        errs = self.problems()
        if errs:
            raise SettingsError("; ".join(errs))
        object.__setattr__(
            self, "daily_contract_quota", MappingProxyType(dict(self.daily_contract_quota))
        )
        object.__setattr__(self, "target_ticks", MappingProxyType(dict(self.target_ticks)))

    def problems(self) -> list[str]:
        return self._limit_problems() + self._symbol_problems() + self._timing_problems()

    def _limit_problems(self) -> list[str]:
        p: list[str] = []
        if self.daily_loss_limit <= 0:
            p.append("daily_loss_limit must be positive")
        if not 0 < self.per_trade_loss_limit <= self.daily_loss_limit:
            p.append("per_trade_loss_limit must be positive and not above the daily limit")
        if not Decimal("0.30") <= self.buying_power_buffer <= Decimal("0.50"):
            p.append("buying_power_buffer must be between 0.30 and 0.50")
        if not 1 <= self.max_open_positions <= 3:
            p.append("max_open_positions must be 1-3")
        if not 1 <= self.max_positions_per_symbol <= 2:
            p.append("max_positions_per_symbol must be 1-2")
        if not 1 <= self.max_same_direction <= self.max_open_positions:
            p.append("max_same_direction must be 1..max_open_positions")
        if self.max_contracts_per_order < 1:
            p.append("max_contracts_per_order must be at least 1")
        if self.daily_contract_quota_total < 1:
            p.append("daily_contract_quota_total must be at least 1")
        if self.fee_filter_multiple < 1:
            p.append("fee_filter_multiple must be at least 1")
        return p

    def _symbol_problems(self) -> list[str]:
        p = [f"unknown symbol {r}" for r in self.allowed_roots if r not in SPECS]
        p += [
            f"bad quota for {r}"
            for r, q in self.daily_contract_quota.items()
            if r not in SPECS or q < 0
        ]
        p += [
            f"bad target ticks for {r}"
            for r, t in self.target_ticks.items()
            if r not in SPECS or t < 1
        ]
        return p

    def _timing_problems(self) -> list[str]:
        p = [
            f"{name} must be 0-120"
            for name in (
                "news_blackout_before_min",
                "news_blackout_after_min",
                "no_entry_before_close_min",
            )
            if not 0 <= getattr(self, name) <= 120
        ]
        if not 5 <= self.data_stale_seconds <= 600:
            p.append("data_stale_seconds must be 5-600")
        return p

    def quota_for(self, root: str) -> int:
        return int(self.daily_contract_quota.get(root, 0))

    # ----- serialization ----------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, Decimal):
                out[f.name] = str(v)
            elif isinstance(v, Mapping):
                out[f.name] = dict(v)
            elif isinstance(v, tuple):
                out[f.name] = list(v)
            else:
                out[f.name] = v
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> RiskSettings:
        kwargs: dict[str, Any] = {}
        defaults = cls()
        for f in fields(cls):
            if f.name not in d:
                continue
            current = getattr(defaults, f.name)
            v = d[f.name]
            if isinstance(current, Decimal):
                kwargs[f.name] = Decimal(str(v))
            elif isinstance(current, tuple):
                kwargs[f.name] = tuple(v)
            elif isinstance(current, Mapping):
                kwargs[f.name] = {str(k): int(x) for k, x in dict(v).items()}
            else:
                kwargs[f.name] = int(v)
        unknown = set(d) - {f.name for f in fields(cls)}
        if unknown:
            raise SettingsError(f"unknown settings: {sorted(unknown)}")
        return cls(**kwargs)

    def diff(self, other: RiskSettings) -> dict[str, tuple[Any, Any]]:
        a, b = self.to_dict(), other.to_dict()
        return {k: (a[k], b[k]) for k in a if a[k] != b[k]}


Listener = Callable[[RiskSettings], None]


class RiskSettingsStore:
    """Signed, audited, owner-only store for :class:`RiskSettings`."""

    def __init__(self, path: Path, signing_key: bytes, audit: AuditSink) -> None:
        if len(signing_key) < 32:
            raise ValueError("signing key must be at least 32 bytes")
        self.path = path
        self._key = signing_key
        self._audit = audit
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        if path.exists():
            self._current = self._load()
        else:
            self._current = RiskSettings()
            self._write(self._current)

    @property
    def current(self) -> RiskSettings:
        """Read-only snapshot (the dataclass is frozen; mappings are read-only proxies)."""
        return self._current

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def update(self, principal: Principal, **changes: Any) -> RiskSettings:
        """Apply ``changes`` if ``principal`` is a human Owner; audit either way."""
        with self._lock:
            if not principal.can(Permission.CHANGE_RISK_SETTINGS):
                self._audit.record(
                    AuditCategory.SETTING,
                    "risk_settings_change_refused",
                    principal.id,
                    {
                        "kind": principal.kind.value,
                        "role": principal.role.value,
                        "fields": sorted(changes),
                    },
                )
                raise NotAuthorized(
                    f"{principal.kind.value} '{principal.id}' may not change risk settings"
                )
            merged = {**self._current.to_dict(), **_jsonable(changes)}
            new = RiskSettings.from_dict(merged)
            diff = self._current.diff(new)
            if not diff:
                return self._current
            self._write(new)
            self._audit.record(
                AuditCategory.SETTING,
                "risk_settings_changed",
                principal.id,
                {k: {"from": v[0], "to": v[1]} for k, v in diff.items()},
            )
            self._current = new
        for listener in self._listeners:
            listener(new)
        return new

    # ----- signed persistence ----------------------------------------------------------------
    def _sign(self, payload: bytes) -> str:
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def _write(self, settings: RiskSettings) -> None:
        payload = json.dumps(
            {"schema": SCHEMA_VERSION, "settings": settings.to_dict()}, sort_keys=True
        ).encode("utf-8")
        doc = {"payload": payload.decode("utf-8"), "signature": self._sign(payload)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _load(self) -> RiskSettings:
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        payload = str(doc["payload"]).encode("utf-8")
        if not hmac.compare_digest(self._sign(payload), str(doc.get("signature", ""))):
            self._audit.record(
                AuditCategory.SECURITY, "risk_settings_tampered", "engine", {"file": str(self.path)}
            )
            raise SettingsTampered(f"{self.path} was modified outside the app")
        body = json.loads(payload)
        return RiskSettings.from_dict(body["settings"])


def _jsonable(changes: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in changes.items():
        if isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, Mapping):
            out[k] = dict(v)
        elif isinstance(v, tuple):
            out[k] = list(v)
        else:
            out[k] = v
    return out


def with_changes(settings: RiskSettings, **changes: Any) -> RiskSettings:
    """Build a modified copy (for tests and previews; nothing is saved)."""
    return replace(settings, **changes)
