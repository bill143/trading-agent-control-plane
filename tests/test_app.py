"""Step 1.20: desktop application runtime (engine + HTTPS API + bundled dashboard)."""

from __future__ import annotations

import logging
import secrets
import ssl
from pathlib import Path

import httpx
import pytest

from api.tls import spki_pin
from api.vault import StaticKeyProvider
from app.main import (
    RedactingFilter,
    Runtime,
    SingleInstance,
    build_runtime,
    free_port,
    resource_dir,
    start_server,
)


@pytest.fixture
def runtime(tmp_path: Path) -> Runtime:
    return build_runtime(tmp_path, keys=StaticKeyProvider(secrets.token_bytes(32)))


def test_resource_dir_contains_the_dashboard() -> None:
    web = resource_dir()
    for name in ("index.html", "app.js", "styles.css", "icon.svg"):
        assert (web / name).is_file(), name


def test_runtime_serves_dashboard_over_pinned_https(runtime: Runtime) -> None:
    port = free_port()
    server = start_server(runtime, port)
    try:
        ctx = ssl.create_default_context(cafile=str(runtime.tls.cert))
        base = f"https://127.0.0.1:{port}"
        page = httpx.get(f"{base}/", verify=ctx)
        assert page.status_code == 200
        assert "<title>Trading System</title>" in page.text
        csp = page.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "script-src" not in csp or "'unsafe-inline'" not in csp.split("script-src")[1]
        js = httpx.get(f"{base}/static/app.js", verify=ctx)
        assert js.status_code == 200
        assert "javascript" in js.headers["content-type"]
        health = httpx.get(f"{base}/api/health", verify=ctx).json()
        assert health["setup_required"] is True
        assert health["tls_fingerprint"] == runtime.tls.fingerprint
        assert len(spki_pin(runtime.tls.cert.read_bytes())) == 44  # base64 SHA-256
    finally:
        server.should_exit = True


def test_dashboard_has_every_required_screen() -> None:
    html = (resource_dir() / "index.html").read_text("utf-8")
    for marker in (
        'id="tiles"',  # symbol tiles
        'id="positions"',  # positions
        'id="account"',  # P&L, buying power, quotas
        'id="btn-start"',
        'id="btn-stop"',  # start / stop
        'id="btn-kill"',  # kill switch
        'id="view-backtests"',  # backtest viewer
        'id="view-settings"',  # settings
        'id="sample-banner"',  # demo-mode banner
    ):
        assert marker in html, marker
    js = (resource_dir() / "app.js").read_text("utf-8")
    assert "localStorage" not in js  # the session token is never written to disk
    assert "innerHTML = r.qr_svg" in js  # QR only from the server's own SVG


def test_logs_redact_credentials() -> None:
    f = RedactingFilter()
    rec = logging.LogRecord(
        "x",
        logging.INFO,
        __file__,
        1,
        "auth Bearer abcdefghijklmnop key db-%s password=%s",
        ("A" * 29, "hunter2hunter2"),
        None,
    )
    f.filter(rec)
    msg = rec.getMessage()
    assert "abcdefghijklmnop" not in msg
    assert "AAAAAAAA" not in msg
    assert "hunter2" not in msg
    assert "[redacted" in msg


def test_single_instance_lock(tmp_path: Path) -> None:
    first = SingleInstance(tmp_path)
    assert first.acquire()
    assert not SingleInstance(tmp_path).acquire()


def test_engine_state_before_start(runtime: Runtime) -> None:
    s = runtime.service.state()
    assert s["running"] is False
    assert s["sample_data"] is True
    assert s["kill_switch"]["tripped"] is False
