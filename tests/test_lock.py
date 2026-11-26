"""The SteamVR lease shared by sessions on one PC, and snapshots taken over a virtual headset."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sergulath import cli, output
from sergulath.commands import hmd
from sergulath.core import driver, lock, settings


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class LeaseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"SERGULATH_STATE_DIR": self.dir.name, "SERGULATH_OWNER": "A"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def _other_live_process(self):
        """A real process of another owner, holding the lease while it runs."""
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        lease = {"owner": "B", "command": "suite run", "since": "t", "pids": [child.pid], "holds": []}
        lock._write(lease)
        return lease

    def test_free_lease_is_taken_and_given_back(self):
        with lock.steamvr("steamvr stop"):
            lease = lock.read()
            self.assertEqual((lease["owner"], lease["pids"]), ("A", [os.getpid()]))
        self.assertIsNone(lock.read())

    def test_another_owner_with_a_running_process_is_refused(self):
        self._other_live_process()
        with self.assertRaises(lock.Busy) as caught:
            with lock.steamvr("hmd on"):
                self.fail("must not run")
        self.assertIn("owner B", str(caught.exception))
        self.assertEqual(lock.read()["owner"], "B")  # untouched

    def test_the_same_owner_shares_it(self):
        self._other_live_process()
        with mock.patch.dict(os.environ, {"SERGULATH_OWNER": "B"}):
            with lock.steamvr("hmd on"):  # e.g. a suite's child command
                self.assertEqual(len(lock.read()["pids"]), 2)
            self.assertEqual(len(lock.read()["pids"]), 1)

    def test_a_dead_holder_without_holds_is_stale(self):
        lock._write({"owner": "B", "command": "steamvr start", "since": "t", "pids": [2 ** 22 + 12345], "holds": []})
        with lock.steamvr("steamvr start"):
            self.assertEqual(lock.read()["owner"], "A")

    def test_a_hold_lasts_until_its_snapshot_is_restored(self):
        snapshot = Path(self.dir.name) / "20261126T000000-hmd.json"
        snapshot.write_text("{}")
        with mock.patch.dict(os.environ, {"SERGULATH_OWNER": "B"}):
            with lock.steamvr("hmd on"):
                lock.hold("hmd", snapshot)
        with self.assertRaises(lock.Busy):  # B's process ended, but the virtual headset is still on
            lock.acquire("steamvr stop")
        snapshot.rename(snapshot.with_name(snapshot.stem + ".restored.json"))  # B's `hmd off`
        with lock.steamvr("steamvr stop"):
            self.assertEqual(lock.read()["owner"], "A")

    def test_cli_refuses_with_exit_4_and_changes_nothing(self):
        self._other_live_process()
        with mock.patch.object(driver, "quit_steamvr", side_effect=AssertionError("SteamVR must not stop")):
            code, out, err = _run(["steamvr", "stop", "--format", "json"])
        self.assertEqual(code, output.FAILED)
        self.assertEqual(json.loads(out)["status"], "busy")
        self.assertIn("lock release --force", err)

    def test_read_only_commands_need_no_lease(self):
        self._other_live_process()
        code, out, _ = _run(["lock", "status", "--format", "json"])
        self.assertEqual(code, output.NOT_MET)
        self.assertEqual(json.loads(out)["lease"]["owner"], "B")

    def test_release_needs_force(self):
        self._other_live_process()
        self.assertEqual(_run(["lock", "release"])[0], output.USAGE)
        self.assertEqual(_run(["lock", "release", "--force"])[0], output.OK)
        self.assertIsNone(lock.read())


class VirtualBeforeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.file = self.root / "steamvr.vrsettings"
        self.patches = [mock.patch.object(settings, "SNAPSHOTS", self.root / "snapshots"),
                        mock.patch.object(settings, "settings_file", lambda: self.file)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def test_a_snapshot_over_a_virtual_headset_is_marked_and_refused(self):
        self.file.write_text(json.dumps({"steamvr": {"forcedDriver": "null"}, "driver_null": {"enable": True}}))
        _, snap = settings.apply(hmd.changes([0, 0, 1920, 1080]), self.file, label="hmd")
        self.assertEqual(json.loads(snap.read_text())["virtual_before"], ["steamvr.forcedDriver", "driver_null.enable"])
        with self.assertRaises(settings.SuspectSnapshot) as caught:
            settings.restore(snap)
        self.assertIn("keep the virtual headset", str(caught.exception))
        self.assertTrue(snap.is_file())  # still pending, nothing written
        settings.restore(snap, allow_virtual_before=True)  # on purpose only

    def test_the_hint_names_the_last_clean_snapshot(self):
        self.file.write_text(json.dumps({"steamvr": {}}))
        _, clean = settings.apply(hmd.changes([0, 0, 480, 270]), self.file, label="hmd")
        settings.restore(clean)
        self.file.write_text(json.dumps({"steamvr": {"forcedDriver": "null"}}))
        _, suspect = settings.apply(hmd.changes([0, 0, 480, 270]), self.file, label="hmd")
        with self.assertRaises(settings.SuspectSnapshot) as caught:
            settings.check_restorable(suspect)
        self.assertIn(clean.stem + ".restored.json", str(caught.exception))

    def test_hmd_on_refuses_over_someone_elses_virtual_headset(self):
        self.file.write_text(json.dumps({"steamvr": {"forcedDriver": "null"}}))
        with mock.patch.object(driver, "quit_steamvr", side_effect=AssertionError("SteamVR must not stop")):
            code, _, err = _run(["hmd", "on"])
        self.assertEqual(code, output.FAILED)
        self.assertIn("already set to a virtual headset", err)
        self.assertEqual(list((self.root / "snapshots").glob("*.json")) if (self.root / "snapshots").exists() else [], [])

    def test_hmd_off_refuses_a_suspect_snapshot_before_stopping_steamvr(self):
        self.file.write_text(json.dumps({"steamvr": {"forcedDriver": "null"}}))
        settings.apply(hmd.changes([0, 0, 480, 270]), self.file, label="hmd")
        with mock.patch.object(driver, "quit_steamvr", side_effect=AssertionError("SteamVR must not stop")):
            code, _, err = _run(["hmd", "off"])
        self.assertEqual(code, output.FAILED)
        self.assertIn("Not restored", err)

    def test_settings_restore_refuses_unless_allowed(self):
        self.file.write_text(json.dumps({"steamvr": {"forcedDriver": "null"}}))
        _, snap = settings.apply({"steamvr.forcedDriver": "lighthouse"}, self.file)
        with mock.patch.object(driver, "quit_steamvr", side_effect=AssertionError("SteamVR must not stop")):
            code, _, err = _run(["settings", "restore", "--snapshot", str(snap)])
        self.assertEqual(code, output.FAILED)
        with mock.patch("sergulath.core.steamvr.running_processes", lambda: set()):
            code, _, _ = _run(["settings", "restore", "--snapshot", str(snap), "--allow-virtual-before"])
        self.assertEqual(code, output.OK)
        self.assertEqual(json.loads(self.file.read_text())["steamvr"]["forcedDriver"], "null")


if __name__ == "__main__":
    unittest.main()
