"""Who may do what (build steps 1.10 and 1.16).

Roles (step 1.16):
* ``OWNER``    full control, the only role that can change risk settings.
* ``APPROVER`` approve trades and use the kill switch, nothing else.
* ``VIEWER``   read-only.

Every caller is a :class:`Principal`. AI processes (the supervisor, coordinator, language
services) are principals of kind ``AI``: whatever role they are given, they can never change risk
settings (step 1.10, Tab 4: "No AI can change them").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    APPROVER = "approver"
    VIEWER = "viewer"


class PrincipalKind(StrEnum):
    HUMAN = "human"
    AI = "ai"
    SYSTEM = "system"  # the engine itself (automatic kill-switch triggers, reconciliation)


class Permission(StrEnum):
    READ = "read"
    APPROVE_TRADES = "approve_trades"
    KILL_SWITCH = "kill_switch"
    START_STOP = "start_stop"
    CHANGE_MODE = "change_mode"
    CHANGE_RISK_SETTINGS = "change_risk_settings"
    RESET_KILL_SWITCH = "reset_kill_switch"
    MANAGE_USERS = "manage_users"
    MANAGE_SECRETS = "manage_secrets"
    RUN_BACKTEST = "run_backtest"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.APPROVER: frozenset({Permission.READ, Permission.APPROVE_TRADES, Permission.KILL_SWITCH}),
    Role.VIEWER: frozenset({Permission.READ}),
}

#: Permissions no AI principal ever holds, regardless of role.
AI_FORBIDDEN: frozenset[Permission] = frozenset(
    {
        Permission.CHANGE_RISK_SETTINGS,
        Permission.RESET_KILL_SWITCH,
        Permission.MANAGE_USERS,
        Permission.MANAGE_SECRETS,
        Permission.CHANGE_MODE,
    }
)


class NotAuthorized(PermissionError):  # noqa: N818 - reads naturally at call sites
    """The principal lacks the permission for this action."""


@dataclass(frozen=True)
class Principal:
    id: str
    role: Role
    kind: PrincipalKind = PrincipalKind.HUMAN

    def can(self, permission: Permission) -> bool:
        if self.kind is PrincipalKind.AI and permission in AI_FORBIDDEN:
            return False
        if self.kind is PrincipalKind.SYSTEM:
            # The engine may trip (never reset) the kill switch and read state.
            return permission in {Permission.READ, Permission.KILL_SWITCH}
        return permission in ROLE_PERMISSIONS[self.role]

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise NotAuthorized(
                f"{self.kind.value} '{self.id}' ({self.role.value}) may not {permission.value}"
            )


SYSTEM = Principal("engine", Role.VIEWER, PrincipalKind.SYSTEM)
