"""Control API between the engine and the apps (build step 1.15).

HTTPS on 127.0.0.1 only (see ``api.tls``); every endpoint except ``/api/health`` and first-run
setup requires a session token from a password + two-factor login. Permissions come from the
caller's role (``risk.authority``). Every command is written to the audit log by the service.

Read:      GET  /api/state, /api/decisions, /api/trades, /api/settings/risk, /api/backtests,
                /api/backtests/{id}/report, /api/audit (Owner), /api/users (Owner),
                /api/secrets (Owner, names only)
Commands:  POST /api/engine/{start|stop|pause|resume|speed|mode}, /api/kill, /api/kill/reset,
                /api/backtests, /api/demo/fault; PUT /api/settings/risk
"""

# No "from __future__ import annotations" here: FastAPI resolves the request-scoped
# dependency aliases defined inside create_app() from real annotation objects.

import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from decimal import InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.audit import AuditCategory, AuditLog
from api.service import EngineService, ServiceError
from api.users import AccountLocked, AuthError, UserStore
from api.vault import Vault, VaultError
from engine.context import Mode
from risk.authority import NotAuthorized, Permission, Principal, Role
from risk.settings import SettingsError

VERSION: Final = "0.1.0"
LOGIN_RATE: Final = 10  # attempts per minute per client


class LoginBody(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=256)
    code: str = Field(max_length=8)


class SetupBody(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=256)


class NewUserBody(SetupBody):
    role: Role


class SpeedBody(BaseModel):
    speed: float


class ModeBody(BaseModel):
    mode: Mode


class KillBody(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class ResetBody(BaseModel):
    note: str = Field(min_length=1, max_length=200)


class FaultBody(BaseModel):
    kind: str
    root: str = ""


class SecretBody(BaseModel):
    value: str = Field(min_length=1, max_length=4096)


class StartBody(BaseModel):
    speed: float = 60.0


def create_app(  # noqa: PLR0915 - one route table, kept together for review
    service: EngineService,
    users: UserStore,
    vault: Vault,
    audit: AuditLog,
    web_dir: Path | None = None,
    tls_fingerprint: str = "",
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        audit.record(AuditCategory.SYSTEM, "api_started", "engine", {"version": VERSION})
        yield
        service.shutdown()
        audit.record(AuditCategory.SYSTEM, "api_stopped", "engine", {})

    app = FastAPI(
        title="TradingSystem control API",
        version=VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    attempts: dict[str, deque[float]] = defaultdict(deque)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "frame-src 'self'; object-src 'none'; base-uri 'none'"
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    # ----- auth dependency -------------------------------------------------------------------
    def principal(request: Request) -> Principal:
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        who = users.authenticate(token) if token else None
        if who is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in required")
        return who

    Who = Annotated[Principal, Depends(principal)]  # noqa: N806 - FastAPI dependency alias

    def need(p: Principal, perm: Permission) -> None:
        if not p.can(perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"your role cannot {perm.value}")

    @app.exception_handler(NotAuthorized)
    async def _forbidden(_: Request, exc: NotAuthorized) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status.HTTP_403_FORBIDDEN)

    @app.exception_handler(ServiceError)
    async def _conflict(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status.HTTP_409_CONFLICT)

    @app.exception_handler(SettingsError)
    async def _bad_settings(_: Request, exc: SettingsError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status.HTTP_422_UNPROCESSABLE_CONTENT)

    # ----- public ------------------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": VERSION,
            "setup_required": not users.has_users,
            "tls_fingerprint": tls_fingerprint,
        }

    @app.post("/api/auth/setup")
    def setup(body: SetupBody) -> dict[str, str]:
        try:
            enrollment = users.create_first_owner(body.username, body.password)
        except PermissionError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        return {
            "username": enrollment.username,
            "otpauth_uri": enrollment.otpauth_uri,
            "qr_svg": enrollment.qr_svg,
        }

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request) -> dict[str, Any]:
        client = request.client.host if request.client else "unknown"
        window = attempts[client]
        now = time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= LOGIN_RATE:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts; wait")
        window.append(now)
        try:
            token = users.login(body.username, body.password, body.code)
        except AccountLocked as exc:
            raise HTTPException(status.HTTP_423_LOCKED, str(exc)) from exc
        except AuthError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        user = users.get(body.username)
        assert user is not None  # noqa: S101 - login succeeded
        return {"token": token, "user": user.public()}

    # ----- session ---------------------------------------------------------------------------
    @app.post("/api/auth/logout")
    def logout(request: Request, who: Who) -> dict[str, str]:
        users.logout(request.headers["Authorization"].removeprefix("Bearer ").strip())
        return {"status": "signed out"}

    @app.get("/api/auth/me")
    def me(who: Who) -> dict[str, Any]:
        return {
            "username": who.id,
            "role": who.role.value,
            "permissions": sorted(p.value for p in Permission if who.can(p)),
        }

    # ----- read endpoints --------------------------------------------------------------------
    @app.get("/api/state")
    def state(who: Who) -> dict[str, Any]:
        need(who, Permission.READ)
        return service.state()

    @app.get("/api/decisions")
    def decisions(who: Who, n: int = 100) -> list[dict[str, Any]]:
        need(who, Permission.READ)
        return service.decisions(max(1, min(n, 500)))

    @app.get("/api/trades")
    def trades(who: Who) -> list[dict[str, Any]]:
        need(who, Permission.READ)
        return service.trades()

    @app.get("/api/settings/risk")
    def get_settings(who: Who) -> dict[str, Any]:
        need(who, Permission.READ)
        return {
            "settings": service.settings.current.to_dict(),
            "editable": who.can(Permission.CHANGE_RISK_SETTINGS),
        }

    @app.put("/api/settings/risk")
    def put_settings(changes: dict[str, Any], who: Who) -> dict[str, Any]:
        need(who, Permission.CHANGE_RISK_SETTINGS)
        try:
            return service.update_settings(who, changes)
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    @app.get("/api/audit")
    def audit_tail(who: Who, n: int = 200) -> dict[str, Any]:
        need(who, Permission.MANAGE_USERS)
        records = audit.tail(max(1, min(n, 1000)))
        return {
            "verified_records": audit.verify(),
            "records": [r.__dict__ for r in reversed(records)],
        }

    # ----- engine commands -------------------------------------------------------------------
    @app.post("/api/engine/start")
    def start(who: Who, body: StartBody | None = None) -> dict[str, Any]:
        service.start(who, speed=(body.speed if body else 60.0))
        return service.state()

    @app.post("/api/engine/stop")
    def stop(who: Who) -> dict[str, Any]:
        service.stop(who)
        return service.state()

    @app.post("/api/engine/pause")
    def pause(who: Who) -> dict[str, str]:
        service.pause(who)
        return {"status": "pausing"}

    @app.post("/api/engine/resume")
    def resume(who: Who) -> dict[str, str]:
        service.resume(who)
        return {"status": "resuming"}

    @app.post("/api/engine/speed")
    def speed(body: SpeedBody, who: Who) -> dict[str, float]:
        service.set_speed(who, body.speed)
        return {"speed": body.speed}

    @app.post("/api/engine/mode")
    def mode(body: ModeBody, who: Who) -> dict[str, str]:
        service.set_mode(who, body.mode)
        return {"mode": service.mode.value}

    @app.post("/api/kill")
    def kill(body: KillBody, who: Who) -> dict[str, Any]:
        first = service.kill(who, body.reason)
        return {"tripped_now": first, "kill_switch": service.kill_state()}

    @app.post("/api/kill/reset")
    def reset_kill(body: ResetBody, who: Who) -> dict[str, Any]:
        service.reset_kill(who, body.note)
        return {"kill_switch": service.kill_state()}

    @app.post("/api/demo/fault")
    def fault(body: FaultBody, who: Who) -> dict[str, str]:
        service.inject_fault(who, body.kind, body.root)
        return {"status": "injected"}

    # ----- backtests -------------------------------------------------------------------------
    @app.get("/api/data/quote")
    def get_data_quote(who: Who) -> dict[str, Any]:
        need(who, Permission.MANAGE_SECRETS)
        return service.data_quote()

    @app.post("/api/data/quote")
    def request_data_quote(who: Who) -> dict[str, Any]:
        service.start_data_quote(who)
        return service.data_quote()

    @app.get("/api/backtests")
    def list_backtests(who: Who) -> dict[str, Any]:
        need(who, Permission.READ)
        return {"status": service.backtest_status, "runs": service.backtests()}

    @app.post("/api/backtests")
    def run_backtest(who: Who) -> dict[str, Any]:
        service.start_backtest(who)
        return {"status": service.backtest_status}

    @app.get("/api/backtests/{run_id}/report", response_model=None)
    def backtest_report(run_id: str, who: Who) -> HTMLResponse:
        need(who, Permission.READ)
        path = service.backtest_file(run_id, "report.html")
        return HTMLResponse(path.read_text("utf-8"))

    # ----- users -------------------------------------------------------------------------------
    @app.get("/api/users")
    def list_users(who: Who) -> list[dict[str, object]]:
        need(who, Permission.MANAGE_USERS)
        return [u.public() for u in users.users()]

    @app.post("/api/users")
    def add_user(body: NewUserBody, who: Who) -> dict[str, str]:
        try:
            e = users.create_user(who, body.username, body.password, body.role)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        return {"username": e.username, "otpauth_uri": e.otpauth_uri, "qr_svg": e.qr_svg}

    @app.post("/api/users/{username}/unlock")
    def unlock_user(username: str, who: Who) -> dict[str, str]:
        users.unlock(who, username)
        return {"status": "unlocked"}

    # ----- secrets (names only ever leave the vault) -----------------------------------------
    @app.get("/api/secrets")
    def list_secrets(who: Who) -> dict[str, list[str]]:
        need(who, Permission.MANAGE_SECRETS)
        return {"names": [n for n in vault.names() if not n.startswith(("totp.", "risk.", "api."))]}

    @app.put("/api/secrets/{name}")
    def put_secret(name: str, body: SecretBody, who: Who) -> dict[str, str]:
        if name.startswith(("totp.", "risk.", "api.")):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "system secrets cannot be set here")
        try:
            vault.put(who, name, body.value)
        except VaultError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        return {"status": "saved", "name": name}

    @app.delete("/api/secrets/{name}")
    def delete_secret(name: str, who: Who) -> dict[str, str]:
        if name.startswith(("totp.", "risk.", "api.")):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "system secrets cannot be removed")
        try:
            vault.delete(who, name)
        except VaultError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return {"status": "deleted", "name": name}

    # ----- desktop web UI -------------------------------------------------------------------------
    if web_dir is not None and web_dir.is_dir():

        @app.get("/", response_model=None)
        def index() -> FileResponse:
            return FileResponse(web_dir / "index.html")

        app.mount("/static", StaticFiles(directory=web_dir), name="static")

    return app
