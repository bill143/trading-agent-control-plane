"""Encrypted secrets vault (build step 1.18).

API keys, broker logins, two-factor seeds and internal signing keys live only here; never in
code, config files, the database or logs.

* Storage: one file encrypted with AES-256-GCM (authenticated; any modification is detected).
* Key protection:
  - Windows: the 256-bit vault key is wrapped with DPAPI (``CryptProtectData``, Windows
    protected storage bound to the logged-in user) and stored next to the vault.
  - Other platforms / server: the key is held in the OS keyring (``keyring``), or supplied by
    the server's secret manager through :class:`StaticKeyProvider` at process start
    (Phase 7, step 7.06).
* Values are returned as :class:`SecretValue`, whose ``repr``/``str`` never show the value.
* Only a human Owner can add, replace or delete secrets; every change is audited by name only.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import secrets
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from api.audit import AuditCategory, AuditSink
from risk.authority import Permission, Principal

KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12
FORMAT: Final = "tacp-vault-v1"
_NAME_CHARS: Final = set("abcdefghijklmnopqrstuvwxyz0123456789_.-")


class VaultError(RuntimeError):
    """The vault cannot be opened or a secret is missing."""


class VaultTampered(VaultError):  # noqa: N818
    """Authentication failed: the vault file was modified or the key is wrong."""


@dataclass(frozen=True)
class SecretValue:
    """A secret that cannot leak through logging, f-strings or exceptions."""

    _value: str

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue('********')"

    def __str__(self) -> str:
        return "********"


class KeyProvider(Protocol):
    def load_or_create(self) -> bytes: ...


class StaticKeyProvider:
    """Key supplied by the caller (server secret manager at start-up; tests)."""

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError("vault key must be 32 bytes")
        self._key = key

    def load_or_create(self) -> bytes:
        return self._key


class KeyringKeyProvider:
    """Vault key stored in the operating system's credential store."""

    def __init__(self, service: str = "TradingSystem", username: str = "vault-key") -> None:
        self.service, self.username = service, username

    def load_or_create(self) -> bytes:
        import keyring  # noqa: PLC0415 - optional platform backend

        stored = keyring.get_password(self.service, self.username)
        if stored:
            return base64.b64decode(stored)
        key = secrets.token_bytes(KEY_BYTES)
        keyring.set_password(self.service, self.username, base64.b64encode(key).decode())
        return key


IS_WINDOWS: Final[bool] = sys.platform == "win32"


def _win32(dll: str) -> Any:
    """``ctypes.windll.<dll>`` without tying type-checking to the build platform."""
    return getattr(ctypes.windll, dll)  # type: ignore[attr-defined,unused-ignore]


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def dpapi_protect(data: bytes, description: str = "TradingSystem vault key") -> bytes:
    """Encrypt with Windows DPAPI for the current user (Windows only)."""
    if not IS_WINDOWS:
        raise VaultError("DPAPI is only available on Windows")
    crypt32, kernel32 = _win32("crypt32"), _win32("kernel32")
    src, out = _blob(data), _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(src), description, None, None, None, 0x01, ctypes.byref(out)
    ):
        raise VaultError(f"CryptProtectData failed ({kernel32.GetLastError()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def dpapi_unprotect(data: bytes) -> bytes:
    if not IS_WINDOWS:
        raise VaultError("DPAPI is only available on Windows")
    crypt32, kernel32 = _win32("crypt32"), _win32("kernel32")
    src, out = _blob(data), _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(src), None, None, None, None, 0x01, ctypes.byref(out)
    ):
        raise VaultTampered("DPAPI could not unwrap the vault key (different Windows user?)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


class DpapiKeyProvider:
    """Vault key wrapped with Windows DPAPI, stored as ``<vault>.key``."""

    def __init__(self, key_file: Path) -> None:
        self.key_file = key_file

    def load_or_create(self) -> bytes:
        if self.key_file.exists():
            return dpapi_unprotect(self.key_file.read_bytes())
        key = secrets.token_bytes(KEY_BYTES)
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        self.key_file.write_bytes(dpapi_protect(key))
        return key


class OwnerOnlyFileKeyProvider:
    """Development fallback for machines without DPAPI or an OS keyring (e.g. headless Linux):
    the key sits in a file only the current user can read. Never used on Windows."""

    def __init__(self, key_file: Path) -> None:
        self.key_file = key_file

    def load_or_create(self) -> bytes:
        if self.key_file.exists():
            return base64.b64decode(self.key_file.read_bytes())
        key = secrets.token_bytes(KEY_BYTES)
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(base64.b64encode(key))
        return key


def _keyring_usable() -> bool:
    try:
        import keyring  # noqa: PLC0415
        from keyring.backends import fail  # noqa: PLC0415

        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:
        return False


def default_key_provider(vault_path: Path) -> KeyProvider:
    if IS_WINDOWS:
        return DpapiKeyProvider(vault_path.with_suffix(".key"))
    if _keyring_usable():
        return KeyringKeyProvider()
    return OwnerOnlyFileKeyProvider(vault_path.with_suffix(".key"))


def _check_name(name: str) -> str:
    if not name or len(name) > 64 or set(name) - _NAME_CHARS:
        raise VaultError("secret names: 1-64 chars of a-z 0-9 _ . -")
    return name


class Vault:
    def __init__(self, path: Path, keys: KeyProvider, audit: AuditSink) -> None:
        self.path = path
        self._aead = AESGCM(keys.load_or_create())
        self._audit = audit
        self._lock = threading.Lock()
        self._data: dict[str, str] = self._read() if path.exists() else {}
        if not path.exists():
            self._write()

    # ----- file format ------------------------------------------------------------------------
    def _read(self) -> dict[str, str]:
        doc = json.loads(self.path.read_text("utf-8"))
        if doc.get("format") != FORMAT:
            raise VaultError("not a vault file")
        try:
            plain = self._aead.decrypt(
                base64.b64decode(doc["nonce"]),
                base64.b64decode(doc["ciphertext"]),
                FORMAT.encode(),
            )
        except InvalidTag as exc:
            raise VaultTampered("vault file was modified or the key does not match") from exc
        data = json.loads(plain)
        if not isinstance(data, dict):
            raise VaultError("vault contents malformed")
        return {str(k): str(v) for k, v in data.items()}

    def _write(self) -> None:
        nonce = os.urandom(NONCE_BYTES)
        blob = self._aead.encrypt(nonce, json.dumps(self._data).encode(), FORMAT.encode())
        doc = {
            "format": FORMAT,
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(blob).decode(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc), "utf-8")
        tmp.replace(self.path)

    # ----- owner operations (audited, names only) -----------------------------------------------
    def put(self, principal: Principal, name: str, value: str) -> None:
        principal.require(Permission.MANAGE_SECRETS)
        _check_name(name)
        if not value:
            raise VaultError("empty secret")
        with self._lock:
            replaced = name in self._data
            self._data[name] = value
            self._write()
        self._audit.record(
            AuditCategory.SECURITY,
            "secret_replaced" if replaced else "secret_added",
            principal.id,
            {"name": name},
        )

    def delete(self, principal: Principal, name: str) -> None:
        principal.require(Permission.MANAGE_SECRETS)
        with self._lock:
            if self._data.pop(name, None) is None:
                raise VaultError(f"no secret named '{name}'")
            self._write()
        self._audit.record(AuditCategory.SECURITY, "secret_deleted", principal.id, {"name": name})

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._data)

    # ----- engine-internal access -------------------------------------------------------------
    def get(self, name: str) -> SecretValue:
        with self._lock:
            if name not in self._data:
                raise VaultError(f"secret '{name}' is not set")
            return SecretValue(self._data[name])

    def has(self, name: str) -> bool:
        return name in self._data

    def internal_key(self, name: str, nbytes: int = 32) -> bytes:
        """A random key the system itself needs (e.g. settings signing), created on first use."""
        _check_name(name)
        with self._lock:
            if name not in self._data:
                self._data[name] = base64.b64encode(secrets.token_bytes(nbytes)).decode()
                self._write()
            return base64.b64decode(self._data[name])
