"""sergulath suite: run end-to-end cases from a case file and sum them up.

  suite list --cases FILE            The cases, their groups and whether they can run.
  suite run --cases FILE [--only ID ...] [--record DIR]
                                     Run setup, the cases in order, then teardown (always, also
                                     after a failure or an interruption). One line per case, then
                                     a summary. With --record, every command's output goes to
                                     DIR/<n>-<command>.log.

Statuses: pass; fail (a step's exit code or JSON did not match); error (a setup failed, so the
case could not run); unsupported (no means yet, with the reason); manual (needs a person, with
the reason); skipped (a long case, without --slow). Only fail and error count as failures.

Exit codes: 0 no failure and no error; 1 a case failed or errored; 2 a bad case file;
4 the runner itself failed. The file format is in sergulath/core/suite.py and the bundled
suites are in suites/.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import output
from ..core import suite as core

NAME = "suite"
HELP = "run end-to-end cases from a case file and sum up pass, fail and skip"

MARK = {core.PASS: "PASS", core.FAIL: "FAIL", core.ERROR: "ERROR", core.UNSUPPORTED: "UNSUPPORTED", core.MANUAL: "MANUAL",
        core.SKIPPED: "SKIPPED"}


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    for name, text in (("list", "list the cases"), ("run", "run the cases")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--cases", required=True, metavar="FILE", help="case file (TOML)")
        if name == "run":
            p.add_argument("--only", action="append", metavar="ID", help="run only this case (repeatable)")
            p.add_argument("--record", metavar="DIR", help="write every command's output under DIR")
            p.add_argument("--slow", action="store_true", help="also run the long cases (slow = true)")
        output.add_format(p)


def _recorder(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    count = [0]

    def log(argv, done):
        count[0] += 1
        name = re.sub(r"[^A-Za-z0-9_.-]+", "-", "-".join(argv[:4]))[:60]
        (folder / f"{count[0]:03d}-{name}.log").write_text(
            f"$ {' '.join(argv)}\nexit {done.returncode}\n--- stdout\n{done.stdout}\n--- stderr\n{done.stderr}", encoding="utf-8")
    return log


def run(args) -> int:
    try:
        suite = core.load(Path(args.cases))
    except (OSError, ValueError, TypeError) as e:
        output.note(f"bad case file: {e}")
        return output.USAGE
    if args.action == "list":
        for case in suite.cases:
            state = ("unsupported" if case.unsupported else "manual" if case.manual
                     else "slow" if case.slow else "runnable")
            output.emit({"id": case.id, "group": case.group, "phase": case.phase or None, "title": case.title,
                         "state": state, "reason": case.unsupported or case.manual},
                        f"{case.id:<6} {case.group:<10} {case.phase or '-':<8} {state:<11} {case.title}", args.format)
        return output.OK
    unknown = [i for i in (args.only or []) if i not in {c.id for c in suite.cases}]
    if unknown:
        output.note(f"unknown case ids: {', '.join(unknown)}")
        return output.USAGE

    def show(result: core.CaseResult) -> None:
        text = f"{MARK[result.status]:<11} {result.id:<6} {result.title}"
        if result.reason:
            text += f" ({result.reason})"
        output.emit({"source": "case", **result.to_dict()}, text, args.format)

    results, problem = core.run_suite(suite, args.only, show, _recorder(Path(args.record)) if args.record else None,
                                      slow=args.slow)
    totals = core.summary(results)
    if problem:
        output.note(problem)
    output.emit({"source": "summary", "suite": suite.name, **totals, "problem": problem or None},
                f"{suite.name}: {totals['pass']} passed, {totals['fail']} failed, {totals['error']} errors, "
                f"{totals['unsupported']} unsupported, {totals['manual']} manual, {totals['skipped']} skipped "
                f"(of {totals['cases']})", args.format)
    return output.NOT_MET if totals["fail"] or totals["error"] else output.OK
