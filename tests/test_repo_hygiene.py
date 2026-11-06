"""Repository governance checks for Phase 0 (steps 0.02 - 0.07)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from datetime import date
from pathlib import Path

import pytest

from tools import build_status as bs
from tools import check_toolchain

ROOT = Path(__file__).resolve().parent.parent
GIT = shutil.which("git")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    assert GIT is not None
    return subprocess.run([GIT, *args], cwd=ROOT, capture_output=True, text=True, check=False)


# ----- 0.02 docs and status -----------------------------------------------------------------
def test_plan_files_present() -> None:
    for rel in ("docs/BUILD_PLAN.html", "docs/BUILD_SEQUENCE.md", "BUILD_STATUS.md"):
        assert (ROOT / rel).is_file(), rel


def test_build_status_markdown_in_sync_with_plan_json() -> None:
    t = bs.Tracker()
    assert (ROOT / "BUILD_STATUS.md").read_text(encoding="utf-8") == t.render_markdown()
    md = (ROOT / "BUILD_STATUS.md").read_text(encoding="utf-8")
    for step in t.sequence.steps:
        assert f"| {step.id} " in md, f"step {step.id} missing from BUILD_STATUS.md"


# ----- 0.03 / 0.04 decisions and holdout ----------------------------------------------------
def test_decisions_cover_required_topics() -> None:
    text = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    for needle in (
        "MES, MCL, MGC, M6E, 10Y",
        "Broker route",
        "Hosting",
        "Locked holdout",
        "Tab 5 items",
        "Departures from the plan",
    ):
        assert needle in text, needle


def test_holdout_dates_locked_and_after_development_window() -> None:
    text = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    dev = re.search(r"\| Development \| (\d{4}-\d{2}-\d{2}) \| (\d{4}-\d{2}-\d{2}) \|", text)
    hold = re.search(
        r"\| \*\*Locked holdout\*\* \| \*\*(\d{4}-\d{2}-\d{2})\*\* \| \*\*(\d{4}-\d{2}-\d{2})\*\*",
        text,
    )
    assert dev is not None
    assert hold is not None
    d0, d1 = (date.fromisoformat(x) for x in dev.groups())
    h0, h1 = (date.fromisoformat(x) for x in hold.groups())
    assert d0 < d1 < h0 < h1
    assert (d1 - d0).days >= 3 * 365 - 1, "development window must cover 3+ years"
    assert (h1 - h0).days >= 300, "holdout must cover roughly a year"


def test_no_market_data_committed() -> None:
    tracked = _git("ls-files").stdout.splitlines()
    data_ext = (".dbn", ".zst", ".parquet", ".feather", ".arrow")
    assert not [f for f in tracked if f.endswith(data_ext) or f.startswith("data/holdout/")]


# ----- 0.05 toolchain -----------------------------------------------------------------------
def test_toolchain_pins_match_running_environment() -> None:
    assert check_toolchain.problems() == []


def test_lock_contains_only_windows_compatible_resolution() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    # A universal lock (no platform restriction) resolves for Windows as well as Linux/Mac.
    assert "supported-markers" not in lock or any("win32" in m for m in lock["supported-markers"])


# ----- 0.06 secrets policy ------------------------------------------------------------------
@pytest.mark.skipif(GIT is None, reason="git not installed")
@pytest.mark.parametrize(
    "path",
    [".env", ".env.local", "config/.env", "secrets/ib.json", "broker.pem", "data/holdout/x.dbn"],
)
def test_secret_and_data_paths_are_git_ignored(path: str) -> None:
    assert _git("check-ignore", "-q", "--no-index", path).returncode == 0, path


@pytest.mark.skipif(GIT is None, reason="git not installed")
def test_env_example_is_not_ignored() -> None:
    assert _git("check-ignore", "-q", "--no-index", ".env.example").returncode == 1


def test_env_example_holds_no_values_that_look_secret() -> None:
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            assert not re.search(r"(?i)key|secret|token|pass", key), key
            assert len(value) < 20


def test_precommit_runs_gitleaks_and_blocks_env_files() -> None:
    cfg = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "https://github.com/gitleaks/gitleaks" in cfg
    assert "rev: v8.30.0" in cfg
    assert "id: forbid-env-files" in cfg


def test_gitleaks_config_extends_defaults() -> None:
    cfg = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert cfg["extend"]["useDefault"] is True
    ids = {r["id"] for r in cfg["rules"]}
    assert {"databento-api-key", "generic-broker-credential"} <= ids
    for rule in cfg["rules"]:
        re.compile(rule["regex"])  # every custom rule is a valid pattern


def test_custom_gitleaks_rules_catch_examples() -> None:
    cfg = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    rules = {r["id"]: re.compile(r["regex"]) for r in cfg["rules"]}
    assert rules["databento-api-key"].search("key = db-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O")
    assert rules["generic-broker-credential"].search("RITHMIC_PASSWORD=" + "hunter2hunter2")
    assert not rules["generic-broker-credential"].search("RITHMIC_PASSWORD=")


# ----- 0.07 CI ------------------------------------------------------------------------------
def test_ci_runs_every_required_check_on_every_push() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^on:\n  push:", ci, re.MULTILINE)
    for needle in (
        "ruff check",
        "ruff format --check",
        "mypy",
        "pytest",
        "pip-audit",
        "gitleaks git",
        "windows-latest",
        "setup-windows.ps1",
        "fetch-depth: 0",
    ):
        assert needle in ci, needle
    # Third-party actions are pinned to full commit SHAs, not movable tags.
    for ref in re.findall(r"uses: ([^\s]+)", ci):
        assert re.search(r"@[0-9a-f]{40}$", ref), ref


def test_ruleset_requires_every_ci_job() -> None:
    ruleset = json.loads((ROOT / "docs" / "github" / "main-ruleset.json").read_text("utf-8"))
    checks = next(r for r in ruleset["rules"] if r["type"] == "required_status_checks")
    contexts = {c["context"] for c in checks["parameters"]["required_status_checks"]}
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    names = set(re.findall(r"^    name: (.+)$", ci, re.MULTILINE))
    expanded = {
        n.replace("${{ matrix.os }}", os)
        for n in names
        for os in ("windows-latest", "ubuntu-latest")
    }
    assert contexts <= expanded
    assert {r["type"] for r in ruleset["rules"]} >= {"pull_request", "non_fast_forward", "deletion"}


def test_no_dependency_patches_ssl_globally() -> None:
    """pip-system-certs rewrites Python's TLS verification process-wide (DECISIONS.md D-14)."""
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    refs = [ln for ln in lock.splitlines() if '{ name = "pip-system-certs"' in ln]
    assert refs, "expected the override reference in uv.lock"
    assert all("sys_platform == 'never'" in ln for ln in refs)
