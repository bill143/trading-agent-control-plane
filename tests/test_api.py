"""Steps 1.15 - 1.19 through the HTTP control API."""

from __future__ import annotations

import secrets
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pyotp
import pytest
import uvicorn
from fastapi.testclient import TestClient

from api.audit import AuditLog
from api.server import create_app
from api.service import EngineService, ServicePaths
from api.tls import ensure_certificate, fingerprint
from api.users import IDLE_TIMEOUT, MAX_FAILED, UserStore
from api.vault import StaticKeyProvider, Vault
from engine import sample_data
from engine.nautilus.catalog import PackInfo, write_pack
from risk.authority import Principal, Role

PASSWORD = "Correct-Horse-42!"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(scope="module")
def pack(tmp_path_factory: pytest.TempPathFactory) -> PackInfo:
    path = tmp_path_factory.mktemp("apipack") / "sample"
    return write_pack(sample_data.generate(dates=(date(2026, 11, 4),), roots=("MES", "MCL")), path)


class Env:
    def __init__(self, base: Path, pack: PackInfo) -> None:
        self.clock = Clock()
        self.audit = AuditLog(base / "audit.jsonl")
        self.vault = Vault(
            base / "vault.json", StaticKeyProvider(secrets.token_bytes(32)), self.audit
        )
        self.users = UserStore(base / "users.json", self.vault, self.audit, clock=self.clock)
        self.service = EngineService(
            ServicePaths(base / "state", base / "runs", pack.path),
            lambda: pack,
            self.vault,
            self.audit,
        )
        self.app = create_app(self.service, self.users, self.vault, self.audit)
        self.client = TestClient(self.app)

    def code(self, username: str) -> str:
        seed = self.vault.get(UserStore._seed_name(username)).reveal()
        return pyotp.TOTP(seed).at(self.clock())

    def login(self, username: str, password: str = PASSWORD) -> dict[str, str]:
        r = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password, "code": self.code(username)},
        )
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def env(tmp_path: Path, pack: PackInfo) -> Iterator[Env]:
    e = Env(tmp_path, pack)
    r = e.client.post("/api/auth/setup", json={"username": "bill", "password": PASSWORD})
    assert r.status_code == 200, r.text
    yield e
    e.service.shutdown()


def add_user(env: Env, owner: dict[str, str], name: str, role: Role) -> dict[str, str]:
    r = env.client.post(
        "/api/users",
        headers=owner,
        json={"username": name, "password": PASSWORD + "#2", "role": role.value},
    )
    assert r.status_code == 200, r.text
    return env.login(name, PASSWORD + "#2")


# ----- 1.17 login, two-factor, timeout, lockout ----------------------------------------------
def test_health_and_one_time_setup(env: Env) -> None:
    h = env.client.get("/api/health").json()
    assert h["setup_required"] is False
    again = env.client.post("/api/auth/setup", json={"username": "eve", "password": PASSWORD})
    assert again.status_code == 409


def test_setup_returns_two_factor_enrollment(tmp_path: Path, pack: PackInfo) -> None:
    e = Env(tmp_path, pack)
    r = e.client.post("/api/auth/setup", json={"username": "bill", "password": PASSWORD})
    body = r.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/TradingSystem:bill")
    assert body["qr_svg"].startswith("<svg")
    weak = Env(tmp_path / "w", pack).client.post(
        "/api/auth/setup", json={"username": "bill", "password": "short"}
    )
    assert weak.status_code == 422


def test_login_requires_password_and_code(env: Env) -> None:
    bad_code = env.client.post(
        "/api/auth/login", json={"username": "bill", "password": PASSWORD, "code": "000000"}
    )
    assert bad_code.status_code == 401
    bad_pw = env.client.post(
        "/api/auth/login",
        json={"username": "bill", "password": "nope-nope-x", "code": env.code("bill")},
    )
    assert bad_pw.status_code == 401
    assert bad_code.json()["detail"] == bad_pw.json()["detail"]  # no hint which factor failed
    headers = env.login("bill")
    me = env.client.get("/api/auth/me", headers=headers).json()
    assert me["role"] == "owner"
    assert "change_risk_settings" in me["permissions"]


def test_lockout_after_failed_attempts(env: Env) -> None:
    for _ in range(MAX_FAILED):
        env.client.post(
            "/api/auth/login",
            json={"username": "bill", "password": "wrong-password-1", "code": "1"},
        )
    locked = env.client.post(
        "/api/auth/login", json={"username": "bill", "password": PASSWORD, "code": env.code("bill")}
    )
    assert locked.status_code == 423
    env.clock.now += timedelta(minutes=16)
    assert env.login("bill")
    actions = [r.action for r in env.audit.records()]
    assert "account_locked" in actions
    assert "login_refused_locked" in actions


def test_session_idle_timeout_and_logout(env: Env) -> None:
    h = env.login("bill")
    assert env.client.get("/api/state", headers=h).status_code == 200
    env.clock.now += IDLE_TIMEOUT + timedelta(seconds=1)
    assert env.client.get("/api/state", headers=h).status_code == 401
    h2 = env.login("bill")
    assert env.client.post("/api/auth/logout", headers=h2).status_code == 200
    assert env.client.get("/api/state", headers=h2).status_code == 401


def test_every_endpoint_needs_a_session(env: Env) -> None:
    for method, url in [
        ("get", "/api/state"),
        ("post", "/api/kill"),
        ("get", "/api/audit"),
        ("put", "/api/settings/risk"),
        ("post", "/api/engine/start"),
        ("get", "/api/secrets"),
        ("get", "/api/users"),
    ]:
        assert getattr(env.client, method)(url).status_code in (401, 422), url
    r = env.client.get("/api/state", headers={"Authorization": "Bearer forged"})
    assert r.status_code == 401


# ----- 1.16 roles ----------------------------------------------------------------------------
def test_roles_enforced(env: Env) -> None:
    owner = env.login("bill")
    viewer = add_user(env, owner, "guest", Role.VIEWER)
    approver = add_user(env, owner, "deputy", Role.APPROVER)
    c = env.client
    assert c.get("/api/state", headers=viewer).status_code == 200
    assert c.post("/api/kill", headers=viewer, json={"reason": "x"}).status_code == 403
    assert c.post("/api/engine/start", headers=viewer).status_code == 403
    assert c.put("/api/settings/risk", headers=viewer, json={}).status_code == 403
    assert c.post("/api/kill", headers=approver, json={"reason": "deputy"}).status_code == 200
    assert c.post("/api/kill/reset", headers=approver, json={"note": "x"}).status_code == 403
    assert (
        c.put("/api/settings/risk", headers=approver, json={"daily_loss_limit": "100"}).status_code
        == 403
    )
    assert c.get("/api/users", headers=approver).status_code == 403
    assert c.post("/api/kill/reset", headers=owner, json={"note": "checked"}).status_code == 200


# ----- 1.10 settings through the API ---------------------------------------------------------
def test_owner_changes_settings_and_change_is_audited(env: Env) -> None:
    h = env.login("bill")
    r = env.client.put("/api/settings/risk", headers=h, json={"daily_loss_limit": "400"})
    assert r.status_code == 200
    assert r.json()["daily_loss_limit"] == "400"
    bad = env.client.put("/api/settings/risk", headers=h, json={"buying_power_buffer": "0.1"})
    assert bad.status_code == 422
    unknown = env.client.put("/api/settings/risk", headers=h, json={"leverage": 10})
    assert unknown.status_code == 422
    rec = [r for r in env.audit.records() if r.action == "risk_settings_changed"]
    assert rec[-1].details["daily_loss_limit"] == {"from": "500", "to": "400"}


# ----- 1.18 secrets via API ----------------------------------------------------------------------
def test_secrets_names_only(env: Env) -> None:
    h = env.login("bill")
    r = env.client.put(
        "/api/secrets/databento.api_key",
        headers=h,
        json={"value": "db-NOT-A-REAL-KEY-000000000000"},
    )
    assert r.status_code == 200
    names = env.client.get("/api/secrets", headers=h).json()["names"]
    assert names == ["databento.api_key"]  # system secrets (2FA seeds, signing keys) hidden
    blob = (
        env.client.get("/api/secrets", headers=h).text
        + env.client.get("/api/audit", headers=h).text
    )
    assert "NOT-A-REAL-KEY" not in blob
    assert env.client.put("/api/secrets/totp.x", headers=h, json={"value": "x"}).status_code == 403


# ----- 1.19 audit endpoint ----------------------------------------------------------------------
def test_audit_log_covers_logins_commands_settings(env: Env) -> None:
    h = env.login("bill")
    env.client.put("/api/settings/risk", headers=h, json={"max_open_positions": 2})
    env.client.post("/api/kill", headers=h, json={"reason": "drill"})
    body = env.client.get("/api/audit", headers=h).json()
    actions = {r["action"] for r in body["records"]}
    assert {"login", "risk_settings_changed", "kill_switch_tripped", "user_created"} <= actions
    assert body["verified_records"] == len(env.audit.records())


# ----- 1.15 engine commands end to end -------------------------------------------------------
def wait_for(pred: Any, timeout: float = 60.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.2)
    raise AssertionError("condition not reached")


def test_start_state_kill_stop(env: Env) -> None:
    h = env.login("bill")
    c = env.client
    assert c.post("/api/engine/start", headers=h, json={"speed": 3600}).status_code == 200
    assert c.post("/api/engine/start", headers=h).status_code == 409  # already running

    def symbols_live() -> bool:
        s = c.get("/api/state", headers=h).json()
        return len(s["symbols"]) == 2 and all(x["price"] for x in s["symbols"])

    wait_for(symbols_live)
    state = c.get("/api/state", headers=h).json()
    assert state["sample_data"] is True
    assert state["progress"]["state"] in ("running", "paused")
    assert c.post("/api/engine/speed", headers=h, json={"speed": 1800}).status_code == 200
    assert c.post("/api/engine/pause", headers=h).status_code == 200
    assert c.post("/api/engine/resume", headers=h).status_code == 200
    r = c.post("/api/kill", headers=h, json={"reason": "manual drill"})
    assert r.json()["tripped_now"] is True
    wait_for(lambda: c.get("/api/state", headers=h).json()["kill_switch"]["flattened"])
    assert c.post("/api/engine/stop", headers=h).status_code == 200
    # Kill switch survives an engine restart until an Owner resets it.
    assert c.post("/api/engine/start", headers=h, json={"speed": 3600}).status_code == 200
    wait_for(lambda: c.get("/api/state", headers=h).json()["kill_switch"]["tripped"])
    c.post("/api/engine/stop", headers=h)
    assert (
        c.post("/api/kill/reset", headers=h, json={"note": "ok"}).json()["kill_switch"]["tripped"]
        is False
    )


def test_paper_mode_requires_accounts(env: Env) -> None:
    h = env.login("bill")
    r = env.client.post("/api/engine/mode", headers=h, json={"mode": "paper"})
    assert r.status_code == 409
    assert "Databento" in r.json()["detail"]


def test_backtest_via_api(env: Env) -> None:
    h = env.login("bill")
    assert env.client.post("/api/backtests", headers=h).status_code == 200
    wait_for(
        lambda: (
            env.client.get("/api/backtests", headers=h).json()["status"]["state"]
            in ("finished", "error")
        ),
        timeout=120,
    )
    body = env.client.get("/api/backtests", headers=h).json()
    assert body["status"]["state"] == "finished", body
    run_id = body["runs"][0]["run_id"]
    page = env.client.get(f"/api/backtests/{run_id}/report", headers=h)
    assert page.status_code == 200
    assert "Backtest results" in page.text
    assert env.client.get("/api/backtests/..%2f..%2fetc/report", headers=h).status_code in (
        404,
        409,
    )


# ----- 1.15 TLS -----------------------------------------------------------------------------------
def test_certificate_generated_and_reused(tmp_path: Path) -> None:
    a = ensure_certificate(tmp_path)
    b = ensure_certificate(tmp_path)
    assert a.fingerprint == b.fingerprint == fingerprint(a.cert.read_bytes())
    pem = a.cert.read_text()
    assert "BEGIN CERTIFICATE" in pem


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_api_serves_https_only_with_pinned_certificate(env: Env, tmp_path: Path) -> None:
    tls = ensure_certificate(tmp_path / "tls")
    port = _free_port()
    app = create_app(env.service, env.users, env.vault, env.audit, tls_fingerprint=tls.fingerprint)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            ssl_certfile=str(tls.cert),
            ssl_keyfile=str(tls.key),
        )
    )
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        wait_for(lambda: server.started, timeout=15)
        ctx = ssl.create_default_context(cafile=str(tls.cert))
        r = httpx.get(f"https://127.0.0.1:{port}/api/health", verify=ctx)
        assert r.json()["tls_fingerprint"] == tls.fingerprint
        assert r.headers["strict-transport-security"]
        with pytest.raises(httpx.HTTPError):
            httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=3)
        with pytest.raises(httpx.ConnectError):
            httpx.get(f"https://127.0.0.1:{port}/api/health", verify=True)  # untrusted CA
        der = ssl.PEM_cert_to_DER_cert(ssl.get_server_certificate(("127.0.0.1", port)))
        import hashlib  # noqa: PLC0415

        assert hashlib.sha256(der).hexdigest() == tls.fingerprint
    finally:
        server.should_exit = True
        t.join(10)


def test_ai_principal_blocked_from_settings_in_service(env: Env) -> None:
    from risk.authority import NotAuthorized, PrincipalKind  # noqa: PLC0415

    ai = Principal("supervisor", Role.OWNER, PrincipalKind.AI)
    with pytest.raises(NotAuthorized):
        env.service.update_settings(ai, {"daily_loss_limit": "5000"})
