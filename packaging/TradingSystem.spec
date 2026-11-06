# PyInstaller build for the Windows desktop app (build step 1.21).
#   uv run pyinstaller packaging/TradingSystem.spec --noconfirm
# Output: dist/TradingSystem/TradingSystem.exe (+ support files), packaged by installer.iss.
# ruff: noqa
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller

datas, binaries, hiddenimports = [], [], []
for pkg in (
    "nautilus_trader",  # compiled engine (Cython + Rust extension) and its data files
    "pandas_market_calendars",
    "exchange_calendars",
    "pyarrow",
    "msgspec",
    "uvicorn",
    "segno",
    "tzdata",
    "argon2",
    "webview",
):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

for pkg in ("engine", "risk", "strategies", "api", "app"):
    hiddenimports += collect_submodules(pkg)

datas += [(str(ROOT / "app" / "web"), "web")]

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "mypy", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TradingSystem",
    console=False,
    icon=str(ROOT / "packaging" / "icon.ico"),
    version=str(ROOT / "packaging" / "version_info.txt") if sys.platform == "win32" else None,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="TradingSystem")
