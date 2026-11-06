"""Verify the running toolchain matches the repository pins (step 0.05).

Checks:
* the running Python is exactly the version in ``.python-version``;
* ``pyproject.toml`` ``requires-python`` admits that version and nothing newer than its minor;
* ``rust-toolchain.toml`` pins an exact ``X.Y.Z`` channel;
* ``uv.lock`` exists and was generated for this project;
* with ``--rust``: the active ``rustc`` reports the pinned version.

Exit code 0 when everything matches, 1 otherwise (each mismatch is printed).
"""

from __future__ import annotations

import argparse
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parent.parent
_SEMVER: Final = re.compile(r"^\d+\.\d+\.\d+$")


def pinned_python() -> str:
    return (ROOT / ".python-version").read_text(encoding="utf-8").strip()


def pinned_rust() -> str:
    data = tomllib.loads((ROOT / "rust-toolchain.toml").read_text(encoding="utf-8"))
    return str(data["toolchain"]["channel"])


def problems(check_rust: bool = False) -> list[str]:
    errs: list[str] = []
    py_pin = pinned_python()
    if not _SEMVER.match(py_pin):
        errs.append(f".python-version must be an exact X.Y.Z version, found {py_pin!r}")
    running = platform.python_version()
    if running != py_pin:
        errs.append(f"running Python {running} != pinned {py_pin}")

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    major_minor = ".".join(py_pin.split(".")[:2])
    if project.get("requires-python") != f"=={major_minor}.*":
        errs.append(f"requires-python must be '=={major_minor}.*' to match .python-version")

    rust_pin = pinned_rust()
    if not _SEMVER.match(rust_pin):
        errs.append(f"rust-toolchain.toml channel must be exact X.Y.Z, found {rust_pin!r}")

    lock = ROOT / "uv.lock"
    if not lock.is_file():
        errs.append("uv.lock is missing; run 'uv lock'")
    else:
        lock_data = tomllib.loads(lock.read_text(encoding="utf-8"))
        names = {p.get("name") for p in lock_data.get("package", [])}
        if project["name"] not in names:
            errs.append("uv.lock was not generated for this project; run 'uv lock'")
        if lock_data.get("requires-python") != f"=={major_minor}.*":
            errs.append("uv.lock is stale relative to requires-python; run 'uv lock'")

    if check_rust:
        rustc = shutil.which("rustc")
        if rustc is None:
            errs.append("rustc not found; install rustup (scripts/setup-windows.ps1 does this)")
        else:
            out = subprocess.run(  # noqa: S603 - fixed argv, resolved binary
                [rustc, "--version"], cwd=ROOT, capture_output=True, text=True, check=False
            ).stdout
            if f"rustc {rust_pin} " not in out + " ":
                errs.append(f"active rustc is {out.strip()!r}, pinned {rust_pin}")
    return errs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify toolchain pins.")
    parser.add_argument("--rust", action="store_true", help="also verify the active rustc")
    args = parser.parse_args(argv)
    errs = problems(check_rust=args.rust)
    for e in errs:
        print(f"MISMATCH: {e}", file=sys.stderr)
    if not errs:
        print(f"toolchain OK: Python {pinned_python()}, Rust {pinned_rust()}, uv.lock present")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
