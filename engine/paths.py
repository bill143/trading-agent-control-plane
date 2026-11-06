"""Per-user application directories (Windows: %LOCALAPPDATA%; elsewhere: XDG data home).

``TACP_HOME`` overrides the base directory (used by tests and portable installs).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

APP_DIR_NAME: Final = "TradingSystem"


def home() -> Path:
    override = os.environ.get("TACP_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        base = base / APP_DIR_NAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_DIR_NAME.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def sub(name: str) -> Path:
    path = home() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def sample_pack_dir() -> Path:
    return sub("sample")


def runs_dir() -> Path:
    return sub("runs")


def state_dir() -> Path:
    return sub("state")


def logs_dir() -> Path:
    return sub("logs")
