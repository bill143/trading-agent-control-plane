"""Step 1.18: encrypted secrets vault."""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

import pytest

from api.audit import MemoryAudit
from api.vault import (
    SecretValue,
    StaticKeyProvider,
    Vault,
    VaultError,
    VaultTampered,
    dpapi_protect,
    dpapi_unprotect,
)
from risk.authority import NotAuthorized, Principal, PrincipalKind, Role

OWNER = Principal("bill", Role.OWNER)
KEY = secrets.token_bytes(32)


def test_round_trip_and_names_only_audit(tmp_path: Path) -> None:
    audit = MemoryAudit()
    v = Vault(tmp_path / "vault.json", StaticKeyProvider(KEY), audit)
    v.put(OWNER, "databento.api_key", "db-EXAMPLEVALUE")
    again = Vault(tmp_path / "vault.json", StaticKeyProvider(KEY), MemoryAudit())
    assert again.get("databento.api_key").reveal() == "db-EXAMPLEVALUE"
    assert again.names() == ["databento.api_key"]
    assert audit.items[-1][1:3] == ("secret_added", "bill")
    assert "db-EXAMPLEVALUE" not in json.dumps([i[3] for i in audit.items])


def test_file_is_encrypted(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json", StaticKeyProvider(KEY), MemoryAudit())
    v.put(OWNER, "ibkr.password", "correct horse battery staple")
    raw = (tmp_path / "vault.json").read_text("utf-8")
    assert "correct horse" not in raw
    assert "ibkr.password" not in raw  # even names are inside the ciphertext


def test_tampering_and_wrong_key_detected(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    Vault(path, StaticKeyProvider(KEY), MemoryAudit()).put(OWNER, "a", "b")
    with pytest.raises(VaultTampered):
        Vault(path, StaticKeyProvider(secrets.token_bytes(32)), MemoryAudit())
    doc = json.loads(path.read_text("utf-8"))
    ct = bytearray(doc["ciphertext"].encode())
    ct[5] = ord("A") if ct[5] != ord("A") else ord("B")
    doc["ciphertext"] = ct.decode()
    path.write_text(json.dumps(doc), "utf-8")
    with pytest.raises(VaultTampered):
        Vault(path, StaticKeyProvider(KEY), MemoryAudit())


def test_secret_value_never_prints() -> None:
    s = SecretValue("hunter2-very-secret")
    assert "hunter2" not in repr(s)
    assert "hunter2" not in str(s)
    assert "hunter2" not in f"{s} {s!r}"
    assert s.reveal() == "hunter2-very-secret"


@pytest.mark.parametrize(
    "who",
    [
        Principal("x", Role.APPROVER),
        Principal("x", Role.VIEWER),
        Principal("ai", Role.OWNER, PrincipalKind.AI),
    ],
)
def test_only_human_owner_manages_secrets(tmp_path: Path, who: Principal) -> None:
    v = Vault(tmp_path / "vault.json", StaticKeyProvider(KEY), MemoryAudit())
    with pytest.raises(NotAuthorized):
        v.put(who, "x", "y")


def test_names_validated_and_missing_reported(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json", StaticKeyProvider(KEY), MemoryAudit())
    with pytest.raises(VaultError):
        v.put(OWNER, "Bad Name!", "x")
    with pytest.raises(VaultError, match="not set"):
        v.get("nothing")
    k1 = v.internal_key("risk.signing_key")
    assert len(k1) == 32
    assert v.internal_key("risk.signing_key") == k1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_dpapi_round_trip() -> None:
    blob = dpapi_protect(b"secret-key-material")
    assert blob != b"secret-key-material"
    assert dpapi_unprotect(blob) == b"secret-key-material"


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_dpapi_refuses_off_windows() -> None:
    with pytest.raises(VaultError, match="Windows"):
        dpapi_protect(b"x")
