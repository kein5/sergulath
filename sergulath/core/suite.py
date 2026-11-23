"""Run a suite of end-to-end cases from a case file (TOML), and sum up pass, fail and skip.

A case file:

    [suite]
    name = "astarot-e2e"
    # Run once before the cases and once after them, also when a case fails or the run is
    # interrupted (teardown always). Each entry is one command line.
    setup = [["sergulath", "hmd", "on", "--with-driver"]]
    teardown = [["sergulath", "hmd", "off"]]

    [[case]]
    id = "S-01"
    group = "startup"
    title = "SteamVR runs and the app's overlays exist"
    steps = [
        { run = ["sergulath", "steamvr", "status", "--no-devices"] },
        { run = ["sergulath", "overlay", "--key", "ASTAROT0", "--format", "json"],
          json = { found = true } },
    ]

    [[case]]
    id = "W-04"
    group = "watch"
    title = "Tracking comes back after the headset is lifted"
    unsupported = "needs a person to lift the headset; the virtual headset has no tracking loss to recover from"

A step passes when its exit code is in `exit` (default [0]) and, with `json`, some JSON line of
its output has every given key at the given value ("a.b" reaches into objects). `sergulath` at
the start of a command runs this package with the same Python. `wait` (seconds) pauses before a
step. A case with `unsupported` (no means yet) or `manual` (needs a person) is not run and is
counted apart from failures, with its reason.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

PASS, FAIL, UNSUPPORTED, MANUAL, ERROR, SKIPPED = "pass", "fail", "unsupported", "manual", "error", "skipped"


@dataclass
class Step:
    run: list[str]
    exit: list[int] = field(default_factory=lambda: [0])
    json: dict = field(default_factory=dict)
    unsupported_when: dict = field(default_factory=dict)
    wait: float = 0.0
    timeout: float = 300.0


@dataclass
class Case:
    id: str
    title: str
    group: str = ""
    steps: list[Step] = field(default_factory=list)
    unsupported: str = ""
    manual: str = ""
    note: str = ""
    phase: str = ""
    slow: bool = False


@dataclass
class Phase:
    setup: list[list[str]] = field(default_factory=list)
    teardown: list[list[str]] = field(default_factory=list)


@dataclass
class Suite:
    name: str
    cases: list[Case]
    setup: list[list[str]] = field(default_factory=list)
    teardown: list[list[str]] = field(default_factory=list)
    phases: dict[str, Phase] = field(default_factory=dict)


@dataclass
class StepResult:
    run: list[str]
    exit_code: int | None
    ok: bool
    why: str = ""
    seconds: float = 0.0
    unsupported: bool = False


@dataclass
class CaseResult:
    id: str
    title: str
    group: str
    status: str
    reason: str = ""
    steps: list[StepResult] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class CaseFileError(ValueError):
    pass


def load(path: Path) -> Suite:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    head = data.get("suite", {})
    cases = []
    seen = set()
    for raw in data.get("case", []):
        if "id" not in raw or "title" not in raw:
            raise CaseFileError(f"a case needs an id and a title: {raw}")
        if raw["id"] in seen:
            raise CaseFileError(f"case id {raw['id']} appears twice")
        seen.add(raw["id"])
        steps = [Step(**step) for step in raw.get("steps", [])]
        if not steps and not raw.get("unsupported") and not raw.get("manual"):
            raise CaseFileError(f"case {raw['id']} has no steps and is not marked unsupported or manual")
        cases.append(Case(id=raw["id"], title=raw["title"], group=raw.get("group", ""), steps=steps,
                          unsupported=raw.get("unsupported", ""), manual=raw.get("manual", ""),
                          note=raw.get("note", ""), phase=raw.get("phase", ""), slow=bool(raw.get("slow", False))))
    phases = {name: Phase(setup=p.get("setup", []), teardown=p.get("teardown", []))
              for name, p in data.get("phase", {}).items()}
    for case in cases:
        if case.phase and case.phase not in phases:
            raise CaseFileError(f"case {case.id} names an unknown phase {case.phase!r}")
    return Suite(name=head.get("name", path.stem), cases=cases,
                 setup=head.get("setup", []), teardown=head.get("teardown", []), phases=phases)


def command(argv: list[str]) -> list[str]:
    return [sys.executable, "-m", "sergulath", *argv[1:]] if argv and argv[0] == "sergulath" else list(argv)


def lookup(record: dict, key: str):
    value = record
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return KeyError
        value = value[part]
    return value


OPS = {"gt": lambda a, b: a > b, "ge": lambda a, b: a >= b, "lt": lambda a, b: a < b,
       "le": lambda a, b: a <= b, "ne": lambda a, b: a != b, "eq": lambda a, b: a == b}


def matches(value, want) -> bool:
    """`want` is a value to equal, or a table of tests: {gt = 0}, {present = true}, {ne = "x"}."""
    if isinstance(want, dict) and want and set(want) <= set(OPS) | {"present"}:
        if "present" in want:
            if (value is not KeyError and value is not None) != bool(want["present"]):
                return False
        for op, operand in want.items():
            if op == "present":
                continue
            try:
                if value is KeyError or not OPS[op](value, operand):
                    return False
            except TypeError:
                return False
        return True
    return value == want


def json_find(stdout: str, wanted: dict) -> dict | None:
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and all(matches(lookup(record, k), v) for k, v in wanted.items()):
            return record
    return None


def json_matches(stdout: str, wanted: dict) -> bool:
    return json_find(stdout, wanted) is not None


def run_step(step: Step, log=None) -> StepResult:
    if step.wait:
        time.sleep(step.wait)
    started = time.monotonic()
    try:
        done = subprocess.run(command(step.run), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=step.timeout)
    except subprocess.TimeoutExpired:
        return StepResult(step.run, None, False, f"timed out after {step.timeout:.0f} s", time.monotonic() - started)
    except OSError as e:
        return StepResult(step.run, None, False, f"could not start: {e}", time.monotonic() - started)
    if log:
        log(step.run, done)
    seconds = time.monotonic() - started
    if step.unsupported_when and done.returncode == 1:
        matched = json_find(done.stdout, step.unsupported_when)
        if matched is not None:
            reason = str(matched.get("reason") or "reported unsupported")
            return StepResult(step.run, done.returncode, False, reason, seconds, unsupported=True)
    if done.returncode not in step.exit:
        last = (done.stderr.strip().splitlines() or done.stdout.strip().splitlines() or [""])[-1]
        return StepResult(step.run, done.returncode, False, f"exit {done.returncode}, wanted {step.exit}: {last}", seconds)
    if step.json and not json_matches(done.stdout, step.json):
        return StepResult(step.run, done.returncode, False, f"no output line with {step.json}", seconds)
    return StepResult(step.run, done.returncode, True, "", seconds)


def run_case(case: Case, log=None, slow: bool = False) -> CaseResult:
    result = CaseResult(case.id, case.title, case.group, PASS)
    if case.slow and not slow:
        result.status, result.reason = SKIPPED, "a long case; run with --slow"
        return result
    if case.unsupported:
        result.status, result.reason = UNSUPPORTED, case.unsupported
        return result
    if case.manual:
        result.status, result.reason = MANUAL, case.manual
        return result
    started = time.monotonic()
    for step in case.steps:
        outcome = run_step(step, log)
        result.steps.append(outcome)
        if outcome.unsupported:
            result.status, result.reason = UNSUPPORTED, outcome.why
            break
        if not outcome.ok:
            result.status, result.reason = FAIL, f"{' '.join(step.run)}: {outcome.why}"
            break
    result.seconds = round(time.monotonic() - started, 1)
    return result


def _run_all(commands: list[list[str]], log) -> str:
    """Run setup or teardown commands; return the first problem ("" when none)."""
    for argv in commands:
        outcome = run_step(Step(run=argv), log)
        if not outcome.ok:
            return f"{' '.join(argv)}: {outcome.why}"
    return ""


def run_suite(suite: Suite, only: list[str] | None = None, on_result=None, log=None,
              slow: bool = False) -> tuple[list[CaseResult], str]:
    """Run the suite's setup, the cases in file order, then its teardown (always).

    Cases of a phase run inside that phase's setup and teardown; consecutive cases of the same
    phase share one. A failed setup marks its cases as errors and skips them. Returns the results
    and the setup or teardown problems ("" when none)."""
    cases = [c for c in suite.cases if not only or c.id in only]
    results: list[CaseResult] = []
    problems: list[str] = []

    def report(result: CaseResult) -> None:
        results.append(result)
        if on_result:
            on_result(result)

    def runnable(case: Case) -> bool:
        return not (case.unsupported or case.manual or (case.slow and not slow))

    active = ""  # the phase whose setup ran and whose teardown is owed
    failed_phase = ""  # a phase whose setup failed
    try:
        problem = _run_all(suite.setup, log)
        if problem:
            problems.append(f"setup {problem}")
            for case in cases:
                report(CaseResult(case.id, case.title, case.group, ERROR, problems[-1]))
            return results, "; ".join(problems)
        for case in cases:
            if runnable(case) and case.phase != active:
                if active:
                    problem = _run_all(suite.phases[active].teardown, log)
                    if problem:
                        problems.append(f"teardown of {active}: {problem}")
                    active = ""
                failed_phase = ""
                if case.phase:
                    problem = _run_all(suite.phases[case.phase].setup, log)
                    active = case.phase  # its teardown runs even after a failed setup
                    if problem:
                        failed_phase = case.phase
                        problems.append(f"setup of {case.phase}: {problem}")
            if runnable(case) and case.phase and case.phase == failed_phase:
                report(CaseResult(case.id, case.title, case.group, ERROR, f"setup of {case.phase} failed"))
                continue
            report(run_case(case, log, slow))
    finally:
        if active:
            problem = _run_all(suite.phases[active].teardown, log)
            if problem:
                problems.append(f"teardown of {active}: {problem}")
        problem = _run_all(suite.teardown, log)
        if problem:
            problems.append(f"teardown {problem}")
    return results, "; ".join(problems)

def summary(results: list[CaseResult]) -> dict:
    counts = {s: sum(r.status == s for r in results) for s in (PASS, FAIL, ERROR, UNSUPPORTED, MANUAL, SKIPPED)}
    return {"cases": len(results), **counts}
