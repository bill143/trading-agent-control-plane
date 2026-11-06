"""Users, roles and login (build steps 1.16 and 1.17).

* Roles: Owner (full control), Approver (approve trades and the kill switch only), Viewer
  (read-only); see ``risk.authority``.
* Passwords are hashed with Argon2id; minimum 12 characters.
* Two-factor: time-based one-time codes (RFC 6238, any authenticator app). Seeds are kept in the
  encrypted vault, never in the user file.
* Sessions: random 256-bit tokens, stored only as SHA-256 hashes; idle timeout and absolute
  lifetime; logout revokes.
* Lockout: after ``MAX_FAILED`` failed logins the account is locked for ``LOCKOUT``.
* Every login, failure, lockout, logout and user change is written to the audit log.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pyotp
import segno
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from api.audit import AuditCategory, AuditSink
from api.vault import Vault
from risk.authority import Permission, Principal, PrincipalKind, Role

MIN_PASSWORD: Final = 12
MAX_FAILED: Final = 5
LOCKOUT: Final = timedelta(minutes=15)
IDLE_TIMEOUT: Final = timedelta(minutes=15)
ABSOLUTE_TIMEOUT: Final = timedelta(hours=12)
ISSUER: Final = "TradingSystem"

_hasher = PasswordHasher()  # Argon2id, library defaults (RFC 9106 recommended parameters)


class AuthError(Exception):
    """Login refused. The message is safe to show the user (no detail on which factor failed)."""


class AccountLocked(AuthError):  # noqa: N818
    pass


@dataclass
class User:
    id: str
    username: str
    role: Role
    password_hash: str
    totp_enrolled: bool
    created: str
    failed_attempts: int = 0
    locked_until: str = ""
    last_login: str = ""
    disabled: bool = False

    def principal(self) -> Principal:
        return Principal(self.username, self.role, PrincipalKind.HUMAN)

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role.value,
            "totp_enrolled": self.totp_enrolled,
            "created": self.created,
            "last_login": self.last_login,
            "locked": bool(self.locked_until),
            "disabled": self.disabled,
        }


@dataclass
class Session:
    token_hash: str
    username: str
    created: datetime
    last_seen: datetime


@dataclass(frozen=True)
class Enrollment:
    """Shown once when a user is created: the two-factor seed as a link and QR code."""

    username: str
    otpauth_uri: str
    qr_svg: str


def _now() -> datetime:
    return datetime.now(UTC)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_password_strength(password: str, username: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    if username.lower() in password.lower():
        raise ValueError("password must not contain the username")
    if len(set(password)) < 6:
        raise ValueError("password is too repetitive")


class UserStore:
    def __init__(
        self,
        path: Path,
        vault: Vault,
        audit: AuditSink,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.path = path
        self._vault = vault
        self._audit = audit
        self._clock = clock
        self._lock = threading.RLock()
        self._users: dict[str, User] = {}
        self._sessions: dict[str, Session] = {}
        if path.exists():
            for raw in json.loads(path.read_text("utf-8")):
                raw["role"] = Role(raw["role"])
                u = User(**raw)
                self._users[u.username] = u

    # ----- persistence ------------------------------------------------------------------------
    def _save(self) -> None:
        rows = [{**asdict(u), "role": u.role.value} for u in self._users.values()]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2), "utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _seed_name(username: str) -> str:
        return f"totp.{hashlib.sha256(username.encode()).hexdigest()[:16]}"

    # ----- users ------------------------------------------------------------------------------
    @property
    def has_users(self) -> bool:
        return bool(self._users)

    def users(self) -> list[User]:
        return list(self._users.values())

    def get(self, username: str) -> User | None:
        return self._users.get(username)

    def create_first_owner(self, username: str, password: str) -> Enrollment:
        """First-run setup only: creates the Owner when no users exist yet."""
        with self._lock:
            if self._users:
                raise PermissionError("setup already completed")
            return self._create(username, password, Role.OWNER, actor="first-run-setup")

    def create_user(self, actor: Principal, username: str, password: str, role: Role) -> Enrollment:
        actor.require(Permission.MANAGE_USERS)
        with self._lock:
            if username in self._users:
                raise ValueError("username already exists")
            return self._create(username, password, role, actor=actor.id)

    def _create(self, username: str, password: str, role: Role, actor: str) -> Enrollment:
        if not username or len(username) > 32 or not username.replace("_", "").isalnum():
            raise ValueError("username: 1-32 letters, digits or _")
        check_password_strength(password, username)
        seed = pyotp.random_base32()
        owner = Principal("user-admin", Role.OWNER)
        self._vault.put(owner, self._seed_name(username), seed)
        user = User(
            id=secrets.token_hex(8),
            username=username,
            role=role,
            password_hash=_hasher.hash(password),
            totp_enrolled=True,
            created=self._clock().isoformat(),
        )
        self._users[username] = user
        self._save()
        self._audit.record(
            AuditCategory.SECURITY, "user_created", actor, {"user": username, "role": role.value}
        )
        uri = pyotp.TOTP(seed).provisioning_uri(name=username, issuer_name=ISSUER)
        svg = segno.make(uri, error="m").svg_inline(scale=4, dark="#000", light="#fff")
        return Enrollment(username, uri, svg)

    def set_disabled(self, actor: Principal, username: str, disabled: bool) -> None:
        actor.require(Permission.MANAGE_USERS)
        with self._lock:
            user = self._require(username)
            if user.role is Role.OWNER and disabled and self._owner_count() == 1:
                raise ValueError("cannot disable the only Owner")
            user.disabled = disabled
            self._save()
            self._revoke_user_sessions(username)
        self._audit.record(
            AuditCategory.SECURITY,
            "user_disabled" if disabled else "user_enabled",
            actor.id,
            {"user": username},
        )

    def unlock(self, actor: Principal, username: str) -> None:
        actor.require(Permission.MANAGE_USERS)
        with self._lock:
            user = self._require(username)
            user.failed_attempts, user.locked_until = 0, ""
            self._save()
        self._audit.record(AuditCategory.SECURITY, "user_unlocked", actor.id, {"user": username})

    def _owner_count(self) -> int:
        return sum(1 for u in self._users.values() if u.role is Role.OWNER and not u.disabled)

    def _require(self, username: str) -> User:
        user = self._users.get(username)
        if user is None:
            raise KeyError(username)
        return user

    # ----- login ------------------------------------------------------------------------------
    def login(self, username: str, password: str, code: str) -> str:
        """Verify password and two-factor code; return a new session token."""
        now = self._clock()
        with self._lock:
            user = self._users.get(username)
            if user is None:
                _hasher.hash(password)  # equalize timing for unknown users
                self._audit.record(
                    AuditCategory.LOGIN, "login_failed", username[:32], {"reason": "unknown user"}
                )
                raise AuthError("username, password or code is incorrect")
            if user.locked_until and datetime.fromisoformat(user.locked_until) > now:
                self._audit.record(AuditCategory.LOGIN, "login_refused_locked", username, {})
                raise AccountLocked("account locked after repeated failures; try later")
            if user.disabled:
                self._audit.record(AuditCategory.LOGIN, "login_refused_disabled", username, {})
                raise AuthError("account disabled")
            ok_pw = self._verify_password(user, password)
            ok_code = self._verify_code(user, code)
            if not (ok_pw and ok_code):
                self._fail(user, now, "password" if not ok_pw else "second factor")
                raise AuthError("username, password or code is incorrect")
            user.failed_attempts, user.locked_until = 0, ""
            user.last_login = now.isoformat()
            if _hasher.check_needs_rehash(user.password_hash):
                user.password_hash = _hasher.hash(password)
            self._save()
            token = secrets.token_urlsafe(32)
            self._sessions[_hash_token(token)] = Session(_hash_token(token), username, now, now)
        self._audit.record(AuditCategory.LOGIN, "login", username, {"role": user.role.value})
        return token

    def _verify_password(self, user: User, password: str) -> bool:
        try:
            return _hasher.verify(user.password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def _verify_code(self, user: User, code: str) -> bool:
        if not user.totp_enrolled or not code.isdigit():
            return False
        seed = self._vault.get(self._seed_name(user.username)).reveal()
        return pyotp.TOTP(seed).verify(code, for_time=self._clock(), valid_window=1)

    def _fail(self, user: User, now: datetime, factor: str) -> None:
        user.failed_attempts += 1
        locked = user.failed_attempts >= MAX_FAILED
        if locked:
            user.locked_until = (now + LOCKOUT).isoformat()
        self._save()
        self._audit.record(
            AuditCategory.LOGIN,
            "account_locked" if locked else "login_failed",
            user.username,
            {"failed_attempts": user.failed_attempts, "factor": factor},
        )

    # ----- sessions ---------------------------------------------------------------------------
    def authenticate(self, token: str) -> Principal | None:
        """Principal for a valid session token; refreshes the idle timer."""
        now = self._clock()
        with self._lock:
            s = self._sessions.get(_hash_token(token))
            if s is None:
                return None
            user = self._users.get(s.username)
            expired = now - s.last_seen > IDLE_TIMEOUT or now - s.created > ABSOLUTE_TIMEOUT
            if user is None or user.disabled or expired:
                self._sessions.pop(s.token_hash, None)
                if expired:
                    self._audit.record(AuditCategory.LOGIN, "session_expired", s.username, {})
                return None
            s.last_seen = now
            return user.principal()

    def logout(self, token: str) -> None:
        with self._lock:
            s = self._sessions.pop(_hash_token(token), None)
        if s is not None:
            self._audit.record(AuditCategory.LOGIN, "logout", s.username, {})

    def _revoke_user_sessions(self, username: str) -> None:
        for h in [h for h, s in self._sessions.items() if s.username == username]:
            self._sessions.pop(h, None)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
