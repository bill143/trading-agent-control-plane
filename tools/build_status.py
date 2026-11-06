"""Build-status tracker for the Multi-Expert AI Trading System build.

Keeps the two status records required by docs/BUILD_SEQUENCE.md in sync:

1. The JSON inside ``<script id="build-status">`` in docs/BUILD_PLAN.html (only that block is
   ever rewritten).
2. BUILD_STATUS.md at the repository root (regenerated from the JSON on every write).

It enforces the governance rules of the build:

* a step can only be marked ``done`` with proof;
* a Tab 3 check can only be marked ``built`` (never Bill's "verified" tick) and only with proof;
* a phase gate can be set to ``awaiting_bill`` only when every step in the phase is ``done`` or
  ``blocked`` with a note, and to ``passed`` only when Bill's green light is recorded.

Usage (from the repository root)::

    uv run python -m tools.build_status next
    uv run python -m tools.build_status step 0.02 done --proof "tests/test_x.py: 4 passed"
    uv run python -m tools.build_status check C0.09 --proof "DECISIONS.md commit abc123"
    uv run python -m tools.build_status gate 0 awaiting_bill
    uv run python -m tools.build_status gate 0 passed --bill-greenlight "Bill: 'green light' 11/06"
    uv run python -m tools.build_status render
    uv run python -m tools.build_status validate
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal, cast
from zoneinfo import ZoneInfo

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
PLAN_PATH: Final = REPO_ROOT / "docs" / "BUILD_PLAN.html"
SEQUENCE_PATH: Final = REPO_ROOT / "docs" / "BUILD_SEQUENCE.md"
STATUS_MD_PATH: Final = REPO_ROOT / "BUILD_STATUS.md"

CT: Final = ZoneInfo("America/Chicago")

StepStatus = Literal["done", "in_progress", "blocked"]
GateStatus = Literal["awaiting_bill", "passed"]
STEP_STATUSES: Final[frozenset[str]] = frozenset({"done", "in_progress", "blocked"})
GATE_STATUSES: Final[frozenset[str]] = frozenset({"awaiting_bill", "passed"})
CHECK_STATUSES: Final[frozenset[str]] = frozenset({"built"})
TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {"updated", "current_step", "steps", "checks", "gates"}
)
STEP_KEYS: Final[frozenset[str]] = frozenset({"status", "date", "proof", "note"})
CHECK_KEYS: Final[frozenset[str]] = frozenset({"status", "date", "proof"})
GATE_KEYS: Final[frozenset[str]] = frozenset({"status", "date", "approved_by"})

_BLOCK_RE: Final = re.compile(
    r'(<script id="build-status" type="application/json">\s*\n)(.*?)(\n</script>)', re.DOTALL
)
_PHASE_RE: Final = re.compile(r"^## Phase (\d+) \u2014 (.+?)\s*$")
_STEP_RE: Final = re.compile(r"^- \[ \] \*\*(\d+\.\d{2})\*\* (.+?) _\((.+?)\)_\s*$")
_CHECK_RE: Final = re.compile(r"^- \[ \] \*\*(C\d+\.\d{2})\*\* (.+?)\s*$")
_GATE_RE: Final = re.compile(r"^> \*\*GATE:\*\* (.+?)\s*$")


class BuildStatusError(ValueError):
    """Raised when a status change would break a governance rule or the files are malformed."""


@dataclass(frozen=True)
class Step:
    id: str
    text: str
    track: str


@dataclass(frozen=True)
class Check:
    id: str
    text: str


@dataclass
class Phase:
    id: str
    name: str
    steps: list[Step] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    gate: str = ""


@dataclass(frozen=True)
class Sequence:
    phases: list[Phase]

    @property
    def steps(self) -> list[Step]:
        return [s for p in self.phases for s in p.steps]

    @property
    def checks(self) -> list[Check]:
        return [c for p in self.phases for c in p.checks]

    def phase(self, phase_id: str) -> Phase:
        for p in self.phases:
            if p.id == phase_id:
                return p
        raise BuildStatusError(f"Unknown phase {phase_id!r}")

    def step(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise BuildStatusError(f"Unknown step {step_id!r}")

    def check(self, check_id: str) -> Check:
        for c in self.checks:
            if c.id == check_id:
                return c
        raise BuildStatusError(f"Unknown check {check_id!r}")


def now_ct() -> str:
    """Current time in Central Time, formatted as the status protocol requires."""
    return datetime.now(CT).strftime("%Y-%m-%d %H:%M CT")


def parse_sequence(text: str) -> Sequence:
    phases: list[Phase] = []
    for line in text.splitlines():
        if m := _PHASE_RE.match(line):
            phases.append(Phase(id=m.group(1), name=m.group(2)))
            continue
        if not phases:
            continue
        current = phases[-1]
        if m := _STEP_RE.match(line):
            if not m.group(1).startswith(current.id + "."):
                raise BuildStatusError(f"Step {m.group(1)} is listed under Phase {current.id}")
            current.steps.append(Step(m.group(1), m.group(2), m.group(3)))
        elif m := _CHECK_RE.match(line):
            if not m.group(1).startswith(f"C{current.id}."):
                raise BuildStatusError(f"Check {m.group(1)} is listed under Phase {current.id}")
            current.checks.append(Check(m.group(1), m.group(2)))
        elif m := _GATE_RE.match(line):
            current.gate = m.group(1)
    if not phases:
        raise BuildStatusError("No phases found in BUILD_SEQUENCE.md")
    ids = [s.id for s in (st for p in phases for st in p.steps)]
    if len(ids) != len(set(ids)):
        raise BuildStatusError("Duplicate step IDs in BUILD_SEQUENCE.md")
    return Sequence(phases)


def read_status_block(html: str) -> dict[str, Any]:
    matches = _BLOCK_RE.findall(html)
    if len(matches) != 1:
        raise BuildStatusError(
            f'Expected exactly one <script id="build-status"> block, found {len(matches)}'
        )
    try:
        data = json.loads(matches[0][1])
    except json.JSONDecodeError as exc:
        raise BuildStatusError(f"build-status block is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BuildStatusError("build-status block must be a JSON object")
    return cast(dict[str, Any], data)


def write_status_block(html: str, data: dict[str, Any]) -> str:
    # "</" is escaped so no value can ever close the <script> element early.
    payload = json.dumps(data, indent=2, ensure_ascii=False).replace("</", "<\\/")
    new_html, count = _BLOCK_RE.subn(lambda m: m.group(1) + payload + m.group(3), html, count=1)
    if count != 1:
        raise BuildStatusError("build-status block not found in BUILD_PLAN.html")
    return new_html


def validate(data: dict[str, Any], seq: Sequence) -> None:
    """Raise BuildStatusError if the status data breaks the schema or the governance rules."""
    extra = set(data) - TOP_LEVEL_KEYS
    if extra:
        raise BuildStatusError(f"Unexpected top-level keys: {sorted(extra)}")
    for key in ("steps", "checks", "gates"):
        if not isinstance(data.get(key), dict):
            raise BuildStatusError(f"'{key}' must be an object")
    current = data.get("current_step")
    if current is not None and current not in {s.id for s in seq.steps}:
        raise BuildStatusError(f"current_step {current!r} is not a known step")
    _validate_steps(data["steps"], {s.id for s in seq.steps})
    _validate_checks(data["checks"], {c.id for c in seq.checks})
    _validate_gates(data, seq)


def _validate_steps(steps: dict[str, Any], known: set[str]) -> None:
    for sid, rec in steps.items():
        _require_record(f"step {sid}", rec, STEP_KEYS)
        if sid not in known:
            raise BuildStatusError(f"Unknown step {sid!r}")
        if rec.get("status") not in STEP_STATUSES:
            raise BuildStatusError(f"step {sid}: invalid status {rec.get('status')!r}")
        if rec["status"] == "done" and not str(rec.get("proof", "")).strip():
            raise BuildStatusError(f"step {sid}: 'done' requires proof")
        if rec["status"] == "blocked" and not str(rec.get("note", "")).strip():
            raise BuildStatusError(f"step {sid}: 'blocked' requires a note saying why")


def _validate_checks(checks: dict[str, Any], known: set[str]) -> None:
    for cid, rec in checks.items():
        _require_record(f"check {cid}", rec, CHECK_KEYS)
        if cid not in known:
            raise BuildStatusError(f"Unknown check {cid!r}")
        if rec.get("status") not in CHECK_STATUSES:
            raise BuildStatusError(
                f"check {cid}: status must be 'built' (Bill's verified tick is his alone)"
            )
        if not str(rec.get("proof", "")).strip():
            raise BuildStatusError(f"check {cid}: 'built' requires proof")


def _validate_gates(data: dict[str, Any], seq: Sequence) -> None:
    phase_ids = {p.id for p in seq.phases}
    for pid, rec in data["gates"].items():
        _require_record(f"gate {pid}", rec, GATE_KEYS)
        if pid not in phase_ids:
            raise BuildStatusError(f"Unknown phase gate {pid!r}")
        if rec.get("status") not in GATE_STATUSES:
            raise BuildStatusError(f"gate {pid}: invalid status {rec.get('status')!r}")
        if rec["status"] == "passed" and not str(rec.get("approved_by", "")).strip():
            raise BuildStatusError(f"gate {pid}: 'passed' requires Bill's recorded green light")
        _require_phase_closed(pid, data, seq)


def _require_record(label: str, rec: object, allowed: frozenset[str]) -> None:
    if not isinstance(rec, dict):
        raise BuildStatusError(f"{label}: record must be an object")
    extra = set(rec) - allowed
    if extra:
        raise BuildStatusError(f"{label}: unexpected fields {sorted(extra)}")


def _require_phase_closed(phase_id: str, data: dict[str, Any], seq: Sequence) -> None:
    open_steps = [
        s.id
        for s in seq.phase(phase_id).steps
        if data["steps"].get(s.id, {}).get("status") not in {"done", "blocked"}
    ]
    if open_steps:
        raise BuildStatusError(f"gate {phase_id}: steps still open: {', '.join(open_steps)}")


class Tracker:
    """Loads, mutates and saves the build status, keeping both records in sync."""

    def __init__(
        self,
        plan_path: Path = PLAN_PATH,
        sequence_path: Path = SEQUENCE_PATH,
        status_md_path: Path = STATUS_MD_PATH,
    ) -> None:
        self.plan_path = plan_path
        self.status_md_path = status_md_path
        self.sequence = parse_sequence(sequence_path.read_text(encoding="utf-8"))
        self._html = plan_path.read_text(encoding="utf-8")
        self.data = read_status_block(self._html)
        for key in ("steps", "checks", "gates"):
            self.data.setdefault(key, {})
        validate(self.data, self.sequence)

    # ----- queries -------------------------------------------------------------------------
    def next_step(self) -> Step | None:
        """First step that is neither done nor blocked (the loop's next unit of work)."""
        for s in self.sequence.steps:
            if self.data["steps"].get(s.id, {}).get("status") not in {"done", "blocked"}:
                return s
        return None

    def blocked_steps(self) -> list[tuple[Step, str]]:
        return [
            (s, self.data["steps"][s.id].get("note", ""))
            for s in self.sequence.steps
            if self.data["steps"].get(s.id, {}).get("status") == "blocked"
        ]

    # ----- mutations -----------------------------------------------------------------------
    def set_step(self, step_id: str, status: StepStatus, proof: str = "", note: str = "") -> None:
        self.sequence.step(step_id)
        if status not in STEP_STATUSES:
            raise BuildStatusError(f"Invalid step status {status!r}")
        if status == "done" and not proof.strip():
            raise BuildStatusError(f"step {step_id}: 'done' requires proof")
        if status == "blocked" and not note.strip():
            raise BuildStatusError(f"step {step_id}: 'blocked' requires a note saying why")
        self.data["steps"][step_id] = {
            "status": status,
            "date": now_ct(),
            "proof": proof.strip(),
            "note": note.strip(),
        }
        if status == "in_progress":
            self.data["current_step"] = step_id
        else:
            nxt = self.next_step()
            self.data["current_step"] = nxt.id if nxt else step_id

    def set_check(self, check_id: str, proof: str) -> None:
        self.sequence.check(check_id)
        if not proof.strip():
            raise BuildStatusError(f"check {check_id}: 'built' requires proof")
        self.data["checks"][check_id] = {"status": "built", "date": now_ct(), "proof": proof}

    def set_gate(self, phase_id: str, status: GateStatus, bill_greenlight: str = "") -> None:
        self.sequence.phase(phase_id)
        if status not in GATE_STATUSES:
            raise BuildStatusError(f"Invalid gate status {status!r}")
        if status == "passed" and not bill_greenlight.strip():
            raise BuildStatusError(
                f"gate {phase_id}: only Bill's green light passes a gate; record it with "
                "--bill-greenlight"
            )
        _require_phase_closed(phase_id, self.data, self.sequence)
        rec: dict[str, str] = {"status": status, "date": now_ct()}
        if status == "passed":
            rec["approved_by"] = bill_greenlight.strip()
        self.data["gates"][phase_id] = rec

    def save(self) -> None:
        self.data["updated"] = now_ct()
        validate(self.data, self.sequence)
        self._html = write_status_block(self._html, self.data)
        # Round-trip check: what we wrote must parse back to exactly what we hold.
        if read_status_block(self._html) != self.data:
            raise BuildStatusError("build-status round-trip mismatch; nothing written")
        self.plan_path.write_text(self._html, encoding="utf-8", newline="\n")
        self.status_md_path.write_text(self.render_markdown(), encoding="utf-8", newline="\n")

    # ----- rendering -----------------------------------------------------------------------
    def render_markdown(self) -> str:
        d = self.data
        icon = {"done": "DONE", "in_progress": "IN PROGRESS", "blocked": "BLOCKED"}
        total = len(self.sequence.steps)
        done = sum(
            1 for s in self.sequence.steps if d["steps"].get(s.id, {}).get("status") == "done"
        )
        out = [
            "# BUILD_STATUS",
            "",
            "Generated by `tools/build_status.py` from the build-status JSON in "
            "docs/BUILD_PLAN.html. Do not edit by hand.",
            "",
            f"- **Last updated:** {d.get('updated') or 'not started'}",
            f"- **Current step:** {d.get('current_step') or '-'}",
            f"- **Steps done:** {done} of {total}",
            "",
        ]
        for p in self.sequence.phases:
            gate = d["gates"].get(p.id, {})
            gate_txt = {"awaiting_bill": "AWAITING BILL", "passed": "PASSED"}.get(
                gate.get("status", ""), "not reached"
            )
            out += [f"## Phase {p.id} - {p.name}", "", f"**Gate:** {gate_txt} - {p.gate}", ""]
            if gate.get("approved_by"):
                out += [f"Green light: {gate['approved_by']} ({gate.get('date', '')})", ""]
            out += ["| Step | Status | Date | Proof / note |", "|---|---|---|---|"]
            for s in p.steps:
                r = d["steps"].get(s.id, {})
                detail = " ".join(x for x in (r.get("proof", ""), r.get("note", "")) if x)
                out.append(
                    f"| {s.id} {_md(s.text)} | {icon.get(r.get('status', ''), 'not started')} "
                    f"| {r.get('date', '')} | {_md(detail)} |"
                )
            out += ["", "| Tab 3 check | Builder | Date | Proof |", "|---|---|---|---|"]
            for c in p.checks:
                r = d["checks"].get(c.id, {})
                out.append(
                    f"| {c.id} {_md(c.text)} | {'BUILT' if r else '-'} "
                    f"| {r.get('date', '')} | {_md(r.get('proof', ''))} |"
                )
            out.append("")
        return "\n".join(out)


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_status", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("next", help="print the next step to build")
    sub.add_parser("render", help="regenerate BUILD_STATUS.md")
    sub.add_parser("validate", help="validate the status data against the rules")
    p_step = sub.add_parser("step", help="set a build step's status")
    p_step.add_argument("id")
    p_step.add_argument("status", choices=sorted(STEP_STATUSES))
    p_step.add_argument("--proof", default="")
    p_step.add_argument("--note", default="")
    p_check = sub.add_parser("check", help="mark a Tab 3 check as built")
    p_check.add_argument("id")
    p_check.add_argument("--proof", required=True)
    p_gate = sub.add_parser("gate", help="set a phase gate")
    p_gate.add_argument("phase")
    p_gate.add_argument("status", choices=sorted(GATE_STATUSES))
    p_gate.add_argument("--bill-greenlight", default="")
    args = parser.parse_args(argv)

    try:
        t = Tracker()
        if args.cmd == "next":
            nxt = t.next_step()
            print(f"{nxt.id} {nxt.text}" if nxt else "All steps done or blocked.")
            for s, note in t.blocked_steps():
                print(f"BLOCKED {s.id}: {note}")
            return 0
        if args.cmd == "validate":
            print("build-status OK")
            return 0
        if args.cmd == "step":
            t.set_step(args.id, args.status, proof=args.proof, note=args.note)
        elif args.cmd == "check":
            t.set_check(args.id, args.proof)
        elif args.cmd == "gate":
            t.set_gate(args.phase, args.status, bill_greenlight=args.bill_greenlight)
        t.save()
    except BuildStatusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"saved: current_step={t.data.get('current_step')} updated={t.data.get('updated')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
