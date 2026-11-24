"""The suite runner with stand-in commands (no SteamVR)."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from sergulath import cli, output
from sergulath.core import suite

PY = sys.executable.replace("\\", "\\\\")

CASES = f"""
[suite]
name = "demo"
setup = [["{PY}", "-c", "print('setup')"]]
teardown = [["{PY}", "-c", "open(r'TEARDOWN', 'w').write('done')"]]

[[case]]
id = "A-01"
group = "a"
title = "exit 0 and the JSON matches"
steps = [{{ run = ["{PY}", "-c", "import json; print(json.dumps({{'found': True, 'p': {{'q': 3}}}}))"], json = {{ found = true, "p.q" = 3 }} }}]

[[case]]
id = "A-02"
group = "a"
title = "a wrong exit code fails"
steps = [{{ run = ["{PY}", "-c", "import sys; sys.exit(3)"] }}]

[[case]]
id = "A-03"
group = "a"
title = "exit 1 allowed"
steps = [{{ run = ["{PY}", "-c", "import sys; sys.exit(1)"], exit = [0, 1] }}]

[[case]]
id = "B-01"
group = "b"
title = "needs a person"
manual = "someone lifts the headset"

[[case]]
id = "B-02"
group = "b"
title = "no means yet"
unsupported = "no command reads it"
"""


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class SuiteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.file = self.root / "cases.toml"
        self.file.write_text(CASES.replace("TEARDOWN", str(self.root / "teardown.txt").replace("\\", "\\\\")), encoding="utf-8")

    def tearDown(self):
        self.dir.cleanup()

    def test_statuses_and_summary(self):
        code, out, _ = _run(["suite", "run", "--cases", str(self.file), "--format", "json",
                             "--record", str(self.root / "logs")])
        records = [json.loads(line) for line in out.splitlines()]
        statuses = {r["id"]: r["status"] for r in records if r["source"] == "case"}
        self.assertEqual(statuses, {"A-01": "pass", "A-02": "fail", "A-03": "pass", "B-01": "manual", "B-02": "unsupported"})
        total = records[-1]
        self.assertEqual((total["pass"], total["fail"], total["manual"], total["unsupported"]), (2, 1, 1, 1))
        self.assertEqual(code, output.NOT_MET)
        self.assertTrue((self.root / "teardown.txt").exists(), "teardown runs")
        self.assertTrue(any((self.root / "logs").iterdir()), "outputs recorded")

    def test_only_and_a_clean_run_exits_0(self):
        code, out, _ = _run(["suite", "run", "--cases", str(self.file), "--only", "A-01", "--only", "B-01"])
        self.assertEqual(code, output.OK)
        self.assertIn("1 passed, 0 failed", out)

    def test_a_failed_setup_marks_every_case_and_still_tears_down(self):
        bad = CASES.replace("print('setup')", "raise SystemExit(5)").replace("TEARDOWN", str(self.root / "t.txt").replace("\\", "\\\\"))
        self.file.write_text(bad, encoding="utf-8")
        results, problem = suite.run_suite(suite.load(self.file))
        self.assertIn("setup", problem)
        self.assertTrue(all(r.status == suite.ERROR for r in results))
        self.assertTrue((self.root / "t.txt").exists())

    def test_bad_files_are_usage_errors(self):
        self.file.write_text('[[case]]\nid = "X"\ntitle = "no steps"\n', encoding="utf-8")
        code, _, err = _run(["suite", "list", "--cases", str(self.file)])
        self.assertEqual(code, output.USAGE)
        self.assertIn("no steps", err)


class MatchTest(unittest.TestCase):
    def test_comparisons_and_presence(self):
        out = json.dumps({"drawn_px": 5, "placement": {"device": "hmd", "offset_distance_m": 0.27}, "version": None})
        self.assertTrue(suite.json_matches(out, {"drawn_px": {"gt": 0}, "placement.device": "hmd"}))
        self.assertTrue(suite.json_matches(out, {"placement.offset_distance_m": {"lt": 0.3}}))
        self.assertFalse(suite.json_matches(out, {"drawn_px": {"gt": 5}}))
        self.assertFalse(suite.json_matches(out, {"version": {"present": True}}), "null is not present")
        self.assertTrue(suite.json_matches(out, {"missing": {"present": False}}))
        self.assertFalse(suite.json_matches(out, {"missing.key": 1}))


class PhaseTest(unittest.TestCase):
    def test_a_phase_wraps_its_cases_once_and_tears_down_after_a_failure(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "log.txt"
            def say(word):
                return f'["{PY}", "-c", "open(r\'{str(log).replace(chr(92), chr(92) * 2)}\', \'a\').write(\'{word} \')"]'
            text = f"""
[suite]
name = "phases"
setup = [{say("setup")}]
teardown = [{say("teardown")}]

[phase.v]
setup = [{say("v-on")}]
teardown = [{say("v-off")}]

[[case]]
id = "R1"
title = "real"
steps = [{{ run = {say("R1")} }}]

[[case]]
id = "V1"
title = "virtual, fails"
phase = "v"
steps = [{{ run = ["{PY}", "-c", "raise SystemExit(1)"] }}]

[[case]]
id = "V2"
title = "virtual"
phase = "v"
steps = [{{ run = {say("V2")} }}]

[[case]]
id = "S1"
title = "slow"
slow = true
steps = [{{ run = {say("S1")} }}]
"""
            path = Path(root) / "cases.toml"
            path.write_text(text, encoding="utf-8")
            results, problem = suite.run_suite(suite.load(path))
            self.assertEqual([r.status for r in results], ["pass", "fail", "pass", "skipped"])
            self.assertEqual(problem, "")
            self.assertEqual(log.read_text().split(), ["setup", "R1", "v-on", "V2", "v-off", "teardown"])

    def test_an_unknown_phase_is_a_bad_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "cases.toml"
            path.write_text('[[case]]\nid = "X"\ntitle = "x"\nphase = "nowhere"\nmanual = "m"\n', encoding="utf-8")
            with self.assertRaises(suite.CaseFileError):
                suite.load(path)


class AppStartTest(unittest.TestCase):
    def test_fresh_writes_the_check_settings_and_never_without_a_settings_dir(self):
        from unittest import mock
        from sergulath.commands import app

        with tempfile.TemporaryDirectory() as root:
            started = {}

            class Proc:
                pid = 4242

            def popen(argv, cwd=None, env=None, creationflags=0):
                started["env"] = env
                return Proc()

            exe = Path(root) / "bin" / "astarot.exe"  # the profile's install path need not exist on CI
            exe.parent.mkdir()
            exe.write_bytes(b"exe")
            with mock.patch("subprocess.Popen", popen), mock.patch.object(app, "pids", lambda p: [4242]):
                code, _, _ = _run(["app", "start", "--exe", str(exe), "--settings-dir", root, "--fresh",
                                   "--set", "OVERLAY_VR_PANEL=true"])
            self.assertEqual(code, output.OK)
            config = json.loads((Path(root) / "config.json").read_text(encoding="utf-8"))
            self.assertIs(config["OVERLAY_VR_PANEL"], True)
            self.assertEqual(config["OSC_IP_ADDRESS"], "127.0.0.2")
            self.assertEqual(started["env"]["VRCT_CONFIG_DIR"], str(Path(root).resolve()))
            self.assertEqual(started["env"]["ASTAROT_CONFIG_DIR"], str(Path(root).resolve()))
            code, _, err = _run(["app", "start", "--fresh"])
            self.assertEqual(code, output.USAGE)
            self.assertIn("never written", err)

    def test_app_commands_find_and_stop_legacy_and_new_names(self):
        from unittest import mock
        from sergulath.commands import app
        from sergulath.core import app as app_core

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            legacy_bin = root / "vrct-vox.exe"
            legacy_bin.write_bytes(b"exe")
            info = {"process": "astarot.exe", "legacy_process": ["vrct-vox.exe"],
                    "exe": str(root / "astarot.exe"), "legacy_exe": [str(legacy_bin)]}
            self.assertEqual(app_core.executable(info), legacy_bin)
            new_bin = root / "astarot.exe"
            new_bin.write_bytes(b"exe")
            self.assertEqual(app_core.executable(info), new_bin)

        running = {"astarot.exe": [], "vrct-vox.exe": [777]}
        killed = []

        def fake_pids(name):
            return list(running.get(name, []))

        def fake_end(names, force=False, with_children=False):
            for name in names:
                killed.append(name)
                running[name] = []

        with mock.patch.object(app, "pids", fake_pids):
            code, out, _ = _run(["app", "status", "--format", "json"])
            self.assertEqual(code, output.OK)
            self.assertEqual(json.loads(out)["pids"], [777])
            self.assertEqual(json.loads(out)["process"], "vrct-vox.exe")
        with mock.patch.object(app, "pids", fake_pids), mock.patch.object(app.host, "end", fake_end):
            code, _, _ = _run(["app", "stop", "--format", "json"])
            self.assertEqual(code, output.OK)
            self.assertEqual(killed, ["vrct-vox.exe"])


class BundledAstarotSuiteTest(unittest.TestCase):
    def test_astarot_e2e_suite_loads_all_26_cases(self):
        loaded = suite.load(Path("suites/astarot-e2e.toml"))
        ids = [c.id for c in loaded.cases]
        self.assertEqual(len(ids), 26)
        self.assertIn("E25", ids)
        self.assertIn("E26", ids)
        by_id = {c.id: c for c in loaded.cases}
        self.assertTrue(by_id["E25"].slow)
        self.assertEqual(by_id["E25"].phase, "soak")
        self.assertEqual(by_id["E26"].phase, "upgrade")
        self.assertTrue(by_id["E26"].steps[0].unsupported_when)

if __name__ == "__main__":
    unittest.main()
