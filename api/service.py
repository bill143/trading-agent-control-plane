"""Engine service: owns the engine thread and everything the control API acts on.

* Start / pause / resume / stop / speed of the demo replay (paper mode arrives in step 1.23).
* Kill switch: trip (Approver or Owner), reset (Owner); a tripped switch survives engine
  restarts and app restarts until an Owner resets it.
* Risk settings through the locked, signed store.
* Backtests run on a background thread; results listed and served for the viewer.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.audit import AuditCategory, AuditLog
from api.vault import Vault
from engine import backtest as backtest_runner
from engine.context import EngineContext, Mode
from engine.nautilus.catalog import PackInfo
from engine.nautilus.node import EngineNode, RunState
from engine.sample_data import LABEL as SAMPLE_LABEL
from risk.authority import SYSTEM, NotAuthorized, Permission, Principal
from risk.killswitch import Trip, TripSource
from risk.settings import RiskSettingsStore

log = logging.getLogger(__name__)

PAPER_SECRETS = ("databento.api_key", "ibkr.username", "ibkr.password")


class ServiceError(RuntimeError):
    """A command that cannot be carried out in the current state (message shown to the user)."""


@dataclass(frozen=True)
class ServicePaths:
    state: Path
    runs: Path
    pack: Path


class EngineService:
    def __init__(
        self,
        paths: ServicePaths,
        pack_loader: Callable[[], PackInfo],
        vault: Vault,
        audit: AuditLog,
    ) -> None:
        self.paths = paths
        self._pack_loader = pack_loader
        self._pack: PackInfo | None = None
        self._pack_lock = threading.Lock()
        self.vault = vault
        self.audit = audit
        self.settings = RiskSettingsStore(
            paths.state / "risk_settings.json", vault.internal_key("risk.signing_key"), audit
        )
        self._lock = threading.RLock()
        self._node: EngineNode | None = None
        self._ctx: EngineContext | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._kill_file = paths.state / "kill_switch.json"
        self._backtest_thread: threading.Thread | None = None
        self.backtest_status: dict[str, Any] = {"state": "idle"}
        self.mode = Mode.DEMO

    # ----- data pack --------------------------------------------------------------------------
    @property
    def pack(self) -> PackInfo:
        """The data pack, loaded (and generated on first start) exactly once, thread-safely."""
        if self._pack is None:
            with self._pack_lock:
                if self._pack is None:
                    self._pack = self._pack_loader()
        return self._pack

    def prepare_pack_in_background(self) -> threading.Thread:
        """Build/verify the sample pack while the user signs in (first start: ~10 s)."""
        t = threading.Thread(target=lambda: self.pack, name="pack-prepare", daemon=True)
        t.start()
        return t

    @property
    def pack_ready(self) -> bool:
        return self._pack is not None

    # ----- engine lifecycle -------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, principal: Principal, speed: float = 60.0) -> None:
        principal.require(Permission.START_STOP)
        with self._lock:
            if self.running:
                raise ServiceError("engine is already running")
            if self.mode is not Mode.DEMO:
                raise ServiceError("paper mode connects in step 1.23 once accounts are set up")
            # Demo replays the same sample sessions on every start, and its simulated broker
            # starts flat, so the demo signal ledger and position journal start empty too.
            for name in ("signals.txt", "positions.json"):
                (self.paths.state / "demo" / name).unlink(missing_ok=True)
            ctx = EngineContext(
                Mode.DEMO,
                self.settings,
                self.audit,
                self.pack.events,
                state_dir=self.paths.state / "demo",
                sample_data=True,
            )
            node = EngineNode(ctx, self.pack)
            self._restore_kill(ctx)  # before persisting, so a restore is not saved twice
            ctx.kill.on_trip(self._persist_trip)
            self._ctx, self._node = ctx, node
            self._stop.clear()
            self._thread = threading.Thread(
                target=node.run_paced, args=(self._stop, speed, True), name="engine", daemon=True
            )
            self._thread.start()
        self.audit.record(
            AuditCategory.COMMAND,
            "engine_start",
            principal.id,
            {"mode": self.mode.value, "speed": speed},
        )

    def stop(self, principal: Principal, timeout: float = 30.0) -> None:
        principal.require(Permission.START_STOP)
        with self._lock:
            thread, node = self._thread, self._node
            if thread is None or not thread.is_alive():
                raise ServiceError("engine is not running")
            self._stop.set()
        thread.join(timeout)
        if node is not None:
            node.dispose()
        self.audit.record(AuditCategory.COMMAND, "engine_stop", principal.id, {})

    def pause(self, principal: Principal) -> None:
        self._command(principal, "pause", None)

    def resume(self, principal: Principal) -> None:
        self._command(principal, "resume", None)

    def set_speed(self, principal: Principal, speed: float) -> None:
        if not 1 <= speed <= 3600:
            raise ServiceError("speed must be between 1x and 3600x")
        self._command(principal, "speed", speed)

    def _command(self, principal: Principal, name: str, arg: Any) -> None:
        principal.require(Permission.START_STOP)
        node = self._require_node()
        node.commands.put((name, arg))
        self.audit.record(
            AuditCategory.COMMAND,
            f"engine_{name}",
            principal.id,
            {"value": arg} if arg is not None else {},
        )

    def set_mode(self, principal: Principal, mode: Mode) -> None:
        principal.require(Permission.CHANGE_MODE)
        if mode is Mode.BACKTEST:
            raise ServiceError("backtests run from the Backtests screen")
        if mode is Mode.PAPER:
            missing = [s for s in PAPER_SECRETS if not self.vault.has(s)]
            if missing:
                raise ServiceError(
                    "paper mode needs your Databento and Interactive Brokers paper accounts: "
                    f"add {', '.join(missing)} in Settings > Connections"
                )
            raise ServiceError("paper trading connects in build step 1.23")
        if self.running:
            raise ServiceError("stop the engine before changing mode")
        self.mode = mode
        self.audit.record(AuditCategory.COMMAND, "mode_changed", principal.id, {"mode": mode.value})

    def _require_node(self) -> EngineNode:
        if self._node is None or not self.running:
            raise ServiceError("engine is not running")
        return self._node

    # ----- demo fault injection (demonstrates automatic kill-switch triggers) -----------------
    def inject_fault(self, principal: Principal, kind: str, root: str = "") -> None:
        principal.require(Permission.CHANGE_MODE)  # Owner only
        if self.mode is not Mode.DEMO:
            raise ServiceError("fault injection exists in demo mode only")
        node = self._require_node()
        if kind == "data_feed":
            if root not in node.strategies:
                raise ServiceError(f"unknown symbol {root!r}")
            node.commands.put(("fault_data", root))
        elif kind == "broker_disconnect":
            node.commands.put(("fault_broker", None))
        elif kind == "clear":
            node.commands.put(("clear_faults", None))
        else:
            raise ServiceError(f"unknown fault {kind!r}")
        self.audit.record(
            AuditCategory.COMMAND, "demo_fault_injected", principal.id, {"kind": kind, "root": root}
        )

    # ----- kill switch ---------------------------------------------------------------------------
    def kill(self, principal: Principal, reason: str) -> bool:
        principal.require(Permission.KILL_SWITCH)
        ctx = self._ctx
        if ctx is None:
            self._write_kill(
                [{"source": TripSource.MANUAL.value, "reason": reason, "actor": principal.id}]
            )
            self.audit.record(
                AuditCategory.RISK,
                "kill_switch_tripped",
                principal.id,
                {"source": "manual", "reason": reason, "engine": "not running"},
            )
            return True
        return ctx.kill.trip(principal, TripSource.MANUAL, reason)

    def reset_kill(self, principal: Principal, note: str) -> None:
        principal.require(Permission.RESET_KILL_SWITCH)
        if self._ctx is not None:
            self._ctx.kill.reset(principal, note)
        else:
            self.audit.record(AuditCategory.RISK, "kill_switch_reset", principal.id, {"note": note})
        self._kill_file.unlink(missing_ok=True)

    def kill_state(self) -> dict[str, object]:
        if self._ctx is not None:
            return self._ctx.kill.state.to_dict()
        saved = self._read_kill()
        return {"tripped": bool(saved), "flattened": True, "flatten_error": "", "trips": saved}

    def _persist_trip(self, trip: Trip) -> None:
        trips = self._read_kill()
        trips.append(
            {
                "source": trip.source.value,
                "reason": trip.reason,
                "actor": trip.actor,
                "at": trip.at.isoformat(),
            }
        )
        self._write_kill(trips)

    def _restore_kill(self, ctx: EngineContext) -> None:
        saved = self._read_kill()
        if saved:
            ctx.kill.trip(
                SYSTEM,
                TripSource.MANUAL,
                f"still tripped from before restart: {saved[0]['reason']}",
            )

    def _read_kill(self) -> list[dict[str, Any]]:
        if not self._kill_file.exists():
            return []
        data = json.loads(self._kill_file.read_text("utf-8"))
        return list(data) if isinstance(data, list) else []

    def _write_kill(self, trips: list[dict[str, Any]]) -> None:
        self._kill_file.parent.mkdir(parents=True, exist_ok=True)
        self._kill_file.write_text(json.dumps(trips), "utf-8")

    # ----- risk settings --------------------------------------------------------------------------
    def update_settings(self, principal: Principal, changes: dict[str, Any]) -> dict[str, Any]:
        new = self.settings.update(principal, **changes)
        return new.to_dict()

    # ----- state for the apps ---------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        ctx, node = self._ctx, self._node
        symbols = [s.to_dict() for s in ctx.store.snapshots()] if ctx else []
        progress = node.progress.to_dict() if node else {"state": RunState.IDLE.value}
        if node is not None and not self.running and progress["state"] == "running":
            progress["state"] = RunState.STOPPED.value
        recon = ctx.reconcile_report.to_dict() if ctx and ctx.reconcile_report else None
        return {
            "mode": self.mode.value,
            "running": self.running,
            "sample_data": True,
            "data_label": SAMPLE_LABEL,  # the paper/live label arrives with step 1.23
            "data_ready": self.pack_ready,
            "progress": progress,
            "account": ctx.store.account() if ctx else {},
            "symbols": symbols,
            "kill_switch": self.kill_state(),
            "reconciliation": recon,
            "faults": {
                "data_feed": sorted(ctx.faults.data_feed) if ctx else [],
                "broker_disconnected": ctx.faults.broker_disconnected if ctx else False,
            },
            "orders_submitted": ctx.orders_submitted if ctx else 0,
            "decisions": {"approved": ctx.decisions.approved, "refused": ctx.decisions.refused}
            if ctx
            else {"approved": 0, "refused": 0},
        }

    def decisions(self, n: int = 100) -> list[dict[str, Any]]:
        return self._ctx.decisions.tail(n) if self._ctx else []

    def trades(self) -> list[dict[str, Any]]:
        if self._ctx is None:
            return []
        with self._ctx.store.lock:
            return [t.to_dict() for t in self._ctx.trades]

    # ----- backtests ------------------------------------------------------------------------------
    def start_backtest(self, principal: Principal) -> None:
        principal.require(Permission.RUN_BACKTEST)
        if self._backtest_thread is not None and self._backtest_thread.is_alive():
            raise ServiceError("a backtest is already running")
        settings = self.settings.current
        self.backtest_status = {"state": "running"}

        def work() -> None:
            try:
                result = backtest_runner.run(self.pack, self.paths.runs, settings=settings)
                self.backtest_status = {"state": "finished", "run_id": result.run_id}
            except Exception as exc:
                log.exception("backtest failed")
                self.backtest_status = {"state": "error", "error": f"{type(exc).__name__}: {exc}"}

        self._backtest_thread = threading.Thread(target=work, name="backtest", daemon=True)
        self._backtest_thread.start()
        self.audit.record(AuditCategory.COMMAND, "backtest_started", principal.id, {})

    def backtests(self) -> list[dict[str, Any]]:
        return backtest_runner.list_runs(self.paths.runs)

    def backtest_file(self, run_id: str, name: str) -> Path:
        if name not in {"report.html", "result.json"} or not run_id.replace("-", "").isalnum():
            raise ServiceError("unknown backtest file")
        path = self.paths.runs / run_id / name
        if not path.is_file():
            raise ServiceError("backtest not found")
        return path

    def shutdown(self) -> None:
        if self.running:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(30)
        if self._node is not None:
            self._node.dispose()


__all__ = ["EngineService", "NotAuthorized", "ServiceError", "ServicePaths"]
