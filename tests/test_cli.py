"""The command line: formats, exit codes and composition, with a stand-in for OpenVR."""

import contextlib
import io
import json
import unittest
from unittest import mock

from sergulath import cli, output
from sergulath.core import steamvr


class _OverlayError(Exception):
    pass


class _FakeOverlay:
    shown = {"ASTAROT0": (True, 1.0, 1.0), "ASTAROT1": (True, 1.0, 0.25)}

    def findOverlay(self, key):
        if key not in self.shown:
            raise _OverlayError(key)
        return key

    def isOverlayVisible(self, handle):
        return self.shown[handle][0]

    def getOverlayAlpha(self, handle):
        return self.shown[handle][1]

    def getOverlayWidthInMeters(self, handle):
        return self.shown[handle][2]


class _FakeOpenVR:
    class error_code:
        OverlayError = _OverlayError

    @staticmethod
    def IVROverlay():
        return _FakeOverlay()


@contextlib.contextmanager
def _fake_client():
    yield _FakeOpenVR


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class OverlayCommandTest(unittest.TestCase):
    def test_json_lines_one_object_per_overlay(self):
        with mock.patch.object(steamvr, "background_client", _fake_client):
            code, out, _ = _run(["overlay", "--format", "json"])
        self.assertEqual(code, output.OK)
        records = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([r["key"] for r in records], [f"ASTAROT{i}" for i in range(5)])
        self.assertTrue(records[0]["found"] and records[0]["visible"])
        self.assertEqual(records[1]["label"], "caption, multiple lines (hand)")
        self.assertFalse(records[2]["found"])

    def test_nothing_found_is_exit_code_1(self):
        with mock.patch.object(steamvr, "background_client", _fake_client):
            code, out, _ = _run(["overlay", "--legacy"])
        self.assertEqual(code, output.NOT_MET)
        self.assertIn("VRCT0", out)


class ProfilesCommandTest(unittest.TestCase):
    def test_lists_astarot_without_steamvr(self):
        code, out, _ = _run(["profiles", "--format", "json"])
        self.assertEqual(code, output.OK)
        record = json.loads(out.splitlines()[0])
        self.assertEqual(record["profile"], "astarot")
        self.assertEqual(record["overlays"][0], "ASTAROT0")


class ExitCodeTest(unittest.TestCase):
    def test_steamvr_not_running_is_exit_code_3_with_a_message_on_stderr(self):
        with mock.patch.object(steamvr, "missing_processes", lambda running=None: ["vrserver.exe"]):
            code, out, err = _run(["overlay"])
        self.assertEqual(code, output.STEAMVR_NOT_RUNNING)
        self.assertEqual(out, "")
        self.assertIn("SteamVR is not running", err)

    def test_watch_refuses_a_count_below_one(self):
        code, _, err = _run(["watch", "--count", "0"])
        self.assertEqual(code, output.USAGE)
        self.assertIn("--count", err)


if __name__ == "__main__":
    unittest.main()
