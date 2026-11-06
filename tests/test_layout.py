"""Step 1.02: project layout engine/, risk/, strategies/, api/, app/, tests/, docs/."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("pkg", ["engine", "risk", "strategies", "api", "app"])
def test_package_importable_and_documented(pkg: str) -> None:
    mod = importlib.import_module(pkg)
    assert mod.__doc__, f"{pkg}/__init__.py needs a docstring describing the package"


def test_tests_and_docs_present() -> None:
    assert (ROOT / "tests").is_dir()
    assert (ROOT / "docs" / "BUILD_PLAN.html").is_file()


def test_only_adapters_import_nautilus() -> None:
    """Pure-logic packages stay Nautilus-free so they are fully typed and unit-testable."""
    for pkg in ("risk", "api"):
        for py in (ROOT / pkg).rglob("*.py"):
            assert "nautilus_trader" not in py.read_text(encoding="utf-8"), py
