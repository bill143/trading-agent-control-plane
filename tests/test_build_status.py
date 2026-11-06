"""Tests for tools/build_status.py: parsing, sync and governance rules."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools import build_status as bs

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tracker(tmp_path: Path) -> bs.Tracker:
    plan = tmp_path / "BUILD_PLAN.html"
    seq = tmp_path / "BUILD_SEQUENCE.md"
    shutil.copy(ROOT / "docs" / "BUILD_PLAN.html", plan)
    shutil.copy(ROOT / "docs" / "BUILD_SEQUENCE.md", seq)
    # Start from a clean status so tests do not depend on real build progress.
    html = plan.read_text(encoding="utf-8")
    clean: dict[str, object] = {
        "updated": None,
        "current_step": "0.01",
        "steps": {},
        "checks": {},
        "gates": {},
    }
    plan.write_text(bs.write_status_block(html, clean), encoding="utf-8")
    return bs.Tracker(plan, seq, tmp_path / "BUILD_STATUS.md")


def test_sequence_parses_every_phase_step_check_and_gate() -> None:
    seq = bs.parse_sequence((ROOT / "docs" / "BUILD_SEQUENCE.md").read_text(encoding="utf-8"))
    assert [p.id for p in seq.phases] == [str(i) for i in range(10)]
    assert len(seq.steps) == 103
    assert len(seq.checks) == 99
    assert all(p.gate for p in seq.phases)
    assert seq.step("1.09").track == "Risk"
    assert seq.check("C0.09").text.startswith("Holdout period dates")


def test_step_done_requires_proof(tracker: bs.Tracker) -> None:
    with pytest.raises(bs.BuildStatusError, match="requires proof"):
        tracker.set_step("0.02", "done")


def test_blocked_requires_note(tracker: bs.Tracker) -> None:
    with pytest.raises(bs.BuildStatusError, match="requires a note"):
        tracker.set_step("0.01", "blocked")


def test_unknown_ids_rejected(tracker: bs.Tracker) -> None:
    with pytest.raises(bs.BuildStatusError):
        tracker.set_step("0.99", "in_progress")
    with pytest.raises(bs.BuildStatusError):
        tracker.set_check("C9.99", proof="x")
    with pytest.raises(bs.BuildStatusError):
        tracker.set_gate("42", "awaiting_bill")


def test_save_round_trips_and_writes_markdown(tracker: bs.Tracker) -> None:
    scripts_before = tracker.plan_path.read_text(encoding="utf-8").count("</script>")
    tracker.set_step("0.01", "in_progress")
    assert tracker.data["current_step"] == "0.01"
    tracker.set_step("0.01", "done", proof="ok </script> <b>x</b>")
    tracker.save()
    reloaded = bs.Tracker(
        tracker.plan_path, ROOT / "docs" / "BUILD_SEQUENCE.md", tracker.status_md_path
    )
    assert reloaded.data["steps"]["0.01"]["proof"] == "ok </script> <b>x</b>"
    assert reloaded.data["current_step"] == "0.02"
    assert reloaded.data["updated"].endswith(" CT")
    html = tracker.plan_path.read_text(encoding="utf-8")
    assert html.count("</script>") == scripts_before  # proof text cannot close the block
    md = tracker.status_md_path.read_text(encoding="utf-8")
    assert "| 0.01 " in md
    assert "DONE" in md


def test_only_status_block_changes(tracker: bs.Tracker) -> None:
    before = tracker.plan_path.read_text(encoding="utf-8")
    tracker.set_step("0.02", "in_progress")
    tracker.save()
    after = tracker.plan_path.read_text(encoding="utf-8")
    assert bs._BLOCK_RE.sub("", before) == bs._BLOCK_RE.sub("", after)


def test_check_can_only_be_built_never_verified(tracker: bs.Tracker) -> None:
    tracker.set_check("C0.09", proof="DECISIONS.md commit")
    assert tracker.data["checks"]["C0.09"]["status"] == "built"
    tracker.data["checks"]["C0.01"] = {"status": "verified", "date": "", "proof": "x"}
    with pytest.raises(bs.BuildStatusError, match="verified tick is his alone"):
        bs.validate(tracker.data, tracker.sequence)


def test_gate_requires_phase_closed(tracker: bs.Tracker) -> None:
    with pytest.raises(bs.BuildStatusError, match="steps still open"):
        tracker.set_gate("0", "awaiting_bill")


def test_gate_pass_requires_bill_greenlight(tracker: bs.Tracker) -> None:
    for s in tracker.sequence.phase("0").steps:
        tracker.set_step(s.id, "done", proof="test")
    tracker.set_gate("0", "awaiting_bill")
    with pytest.raises(bs.BuildStatusError, match="only Bill's green light"):
        tracker.set_gate("0", "passed")
    tracker.data["gates"]["0"] = {"status": "passed", "date": "x"}
    with pytest.raises(bs.BuildStatusError, match="recorded green light"):
        bs.validate(tracker.data, tracker.sequence)
    tracker.set_gate("0", "passed", bill_greenlight="Bill, chat 2026-11-06: green light")
    tracker.save()
    assert tracker.data["gates"]["0"]["approved_by"].startswith("Bill")


def test_next_step_skips_done_and_blocked(tracker: bs.Tracker) -> None:
    tracker.set_step("0.01", "blocked", note="needs Bill: repo settings")
    tracker.set_step("0.02", "done", proof="test")
    nxt = tracker.next_step()
    assert nxt is not None
    assert nxt.id == "0.03"
    assert [s.id for s, _ in tracker.blocked_steps()] == ["0.01"]


def test_malformed_block_rejected() -> None:
    with pytest.raises(bs.BuildStatusError, match="not valid JSON"):
        bs.read_status_block('<script id="build-status" type="application/json">\n{bad\n</script>')
    with pytest.raises(bs.BuildStatusError, match="exactly one"):
        bs.read_status_block("<html></html>")


def test_cli_validate_on_real_repo(capsys: pytest.CaptureFixture[str]) -> None:
    assert bs.main(["validate"]) == 0
    assert "build-status OK" in capsys.readouterr().out
