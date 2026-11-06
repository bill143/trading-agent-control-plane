"""Windows desktop application entry point (build step 1.20).

Double-clicking the app:
1. opens the per-user data folder (%LOCALAPPDATA%\\TradingSystem), the audit log and the
   encrypted vault (key protected by Windows DPAPI);
2. prepares the sample data pack (first start only, about 10 seconds);
3. starts the control API over HTTPS on a free 127.0.0.1 port with the engine's own
   certificate;
4. opens a native window (Edge WebView2) on the dashboard. The window trusts exactly this
   engine's certificate (SPKI pin) and nothing else.

Closing the window stops the engine cleanly. Only one copy runs at a time.

Options (for support and automated tests):
    --server-only   run the engine + API without a window (prints the URL)
    --browser       open the dashboard in the default browser instead of a native window
    --port N        fixed port instead of a free one
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import uvicorn

from api.audit import AuditCategory, AuditLog
from api.server import VERSION, create_app
from api.service import EngineService, ServicePaths
from api.tls import TlsFiles, ensure_certificate, spki_pin
from api.users import UserStore
from api.vault import KeyProvider, Vault, default_key_provider
from engine import paths
from engine.nautilus.catalog import ensure_pack

APP_TITLE: Final = "Trading System"
log = logging.getLogger("tacp")

_REDACT: Final = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[redacted]"),
    (re.compile(r"\bdb-[A-Za-z0-9]{20,}\b"), "[redacted-databento-key]"),
    (
        re.compile(r"(?i)(password|passwd|secret|api[_-]?key|token)(\s*[=:]\s*)\S+"),
        r"\1\2[redacted]",
    ),
]


class RedactingFilter(logging.Filter):
    """Masks credentials in every log record before it is written (secrets policy rule 5)."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern, repl in _REDACT:
            msg = pattern.sub(repl, msg)
        record.msg, record.args = msg, None
        return True


def configure_logging(log_dir: Path) -> None:
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def resource_dir() -> Path:
    """Folder holding bundled resources (the web UI), in source checkouts and in the installer."""
    frozen = getattr(sys, "_MEIPASS", None)
    base = Path(frozen) if frozen else Path(__file__).resolve().parent
    return base / "web" if (base / "web").is_dir() else base / "app" / "web"


@dataclass
class Runtime:
    home: Path
    audit: AuditLog
    vault: Vault
    users: UserStore
    service: EngineService
    tls: TlsFiles
    app: Any


def build_runtime(home: Path, keys: KeyProvider | None = None) -> Runtime:
    state = home / "state"
    state.mkdir(parents=True, exist_ok=True)
    audit = AuditLog(state / "audit.jsonl")
    vault_path = state / "vault.json"
    vault = Vault(vault_path, keys or default_key_provider(vault_path), audit)
    users = UserStore(state / "users.json", vault, audit)
    pack_dir = home / "sample"
    service = EngineService(
        ServicePaths(state=state, runs=home / "runs", pack=pack_dir),
        lambda: ensure_pack(pack_dir),
        vault,
        audit,
    )
    tls = ensure_certificate(state / "tls")
    app = create_app(
        service, users, vault, audit, web_dir=resource_dir(), tls_fingerprint=tls.fingerprint
    )
    return Runtime(home, audit, vault, users, service, tls, app)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class SingleInstance:
    """Exclusive lock on <home>/app.lock so two engines never trade the same account."""

    def __init__(self, home: Path) -> None:
        self.path = home / "app.lock"
        self._fh: Any = None

    def acquire(self) -> bool:
        self._fh = self.path.open("a+")
        try:
            if sys.platform == "win32":
                import msvcrt  # noqa: PLC0415

                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined,unused-ignore]
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined,unused-ignore]
        except OSError:
            self._fh.close()
            return False
        return True


def start_server(rt: Runtime, port: int) -> uvicorn.Server:
    server = uvicorn.Server(
        uvicorn.Config(
            rt.app,
            host="127.0.0.1",
            port=port,
            ssl_certfile=str(rt.tls.cert),
            ssl_keyfile=str(rt.tls.key),
            log_config=None,
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name="api", daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started:
        if time.time() > deadline or not thread.is_alive():
            raise RuntimeError("the control API did not start")
        time.sleep(0.05)
    return server


def open_window(url: str, rt: Runtime, server: uvicorn.Server) -> None:
    # Pin the engine's certificate in Edge WebView2: only this exact public key is accepted.
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        f"--ignore-certificate-errors-spki-list={spki_pin(rt.tls.cert.read_bytes())}"
    )
    import webview  # noqa: PLC0415 - pywebview; available on Windows and macOS builds

    window = webview.create_window(APP_TITLE, url, width=1440, height=920, min_size=(980, 640))
    window.events.closed += lambda: setattr(server, "should_exit", True)
    webview.start(private_mode=True, storage_path=str(rt.home / "webview"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="TradingSystem", description=APP_TITLE)
    ap.add_argument("--server-only", action="store_true")
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)

    home = paths.home()
    configure_logging(paths.logs_dir())
    lock = SingleInstance(home)
    if not lock.acquire():
        print("Trading System is already running on this computer.", file=sys.stderr)
        return 2
    rt = build_runtime(home)
    rt.audit.record(
        AuditCategory.SYSTEM, "app_started", "engine", {"version": VERSION, "pid": os.getpid()}
    )
    rt.service.prepare_pack_in_background()
    port = args.port or free_port()
    server = start_server(rt, port)
    url = f"https://127.0.0.1:{port}/"
    log.info("control API listening on %s (certificate %s)", url, rt.tls.fingerprint[:16])
    try:
        if args.server_only:
            print(f"READY {url} fingerprint={rt.tls.fingerprint}", flush=True)
            while not server.should_exit:
                time.sleep(0.5)
        elif args.browser:
            webbrowser.open(url)
            while not server.should_exit:
                time.sleep(0.5)
        else:
            open_window(url, rt, server)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        rt.service.shutdown()
        rt.audit.record(AuditCategory.SYSTEM, "app_stopped", "engine", {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
