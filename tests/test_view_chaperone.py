"""`view open/close`, the VR View's blank check and the chaperone snapshot, without SteamVR."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sergulath import cli, output
from sergulath.commands import view
from sergulath.core import chaperone, driver, host, settings, steamvr, window_capture


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def _window(title="VRビュー"):
    return window_capture.Window(1, 2, "vrmonitor.exe", title, True, False)


class VrViewBlankTest(unittest.TestCase):
    def test_the_toolbar_does_not_count(self):
        width, height = 100, 100
        grey = bytes([80, 90, 100, 255])
        rows = [bytes([20, 20, 20, 255]) * width if y < 6 else grey * width for y in range(height)]
        rows[2] = bytes([200, 200, 200, 255]) * width  # toolbar text
        rgba = b"".join(rows)
        self.assertFalse(window_capture.is_blank(rgba))
        self.assertTrue(window_capture.vr_view_is_blank(rgba, width, height))

    def test_a_scene_below_the_toolbar_is_not_blank(self):
        width, height = 100, 100
        rgba = bytearray(bytes([0, 0, 0, 255]) * width * height)
        for i in range(4 * width * 50, len(rgba), 4 * 7):
            rgba[i:i + 3] = b"\x10\x40\x90"
        self.assertFalse(window_capture.vr_view_is_blank(bytes(rgba), width, height))


@mock.patch.object(host, "WINDOWS", True)  # the VR View is found through Win32; mocked here
class ViewOpenTest(unittest.TestCase):
    def test_an_open_window_changes_nothing(self):
        with mock.patch.object(view, "_find_vr_view", lambda *a: _window()), \
                mock.patch.object(settings, "apply", side_effect=AssertionError("no change expected")):
            code, out, _ = _run(["view", "open", "--format", "json"])
        self.assertEqual(code, output.OK)
        self.assertEqual(json.loads(out)["changed"], False)

    def test_open_then_close_puts_the_setting_back(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            file = root / "steamvr.vrsettings"
            file.write_text(json.dumps({"steamvr": {"mirrorViewEye": 0}}))
            calls, windows = [], [None, _window()]
            with mock.patch.object(settings, "SNAPSHOTS", root / "snapshots"), \
                    mock.patch.object(settings, "settings_file", lambda: file), \
                    mock.patch.object(steamvr, "running_processes", lambda: {"vrserver.exe"}), \
                    mock.patch.object(driver, "quit_steamvr", lambda: calls.append("quit")), \
                    mock.patch.object(driver, "start_steamvr", lambda: calls.append("start")), \
                    mock.patch.object(view, "_find_vr_view", lambda *a: windows.pop(0) if windows else _window()):
                code, _, _ = _run(["view", "open", "--timeout", "0"])
                self.assertEqual(code, output.OK)
                self.assertIs(json.loads(file.read_text())["steamvr"]["showMirrorView"], True)
                code, _, _ = _run(["view", "close"])
                self.assertEqual(code, output.OK)
                self.assertEqual(json.loads(file.read_text()), {"steamvr": {"mirrorViewEye": 0}})
                self.assertEqual(calls, ["quit", "start", "quit", "start"])
                code, _, err = _run(["view", "close"])
                self.assertEqual(code, output.NOT_MET)
                self.assertIn("nothing to undo", err)


class ChaperoneSnapshotTest(unittest.TestCase):
    def test_universes_ignore_the_time_stamp(self):
        a = json.dumps({"universes": [{"universeID": "12", "play_area": [1, 1], "time": "Sun Feb 16 2025"}]})
        b = json.dumps({"universes": [{"universeID": "12", "play_area": [1, 1], "time": "Mon Nov 23 2026"}]})
        self.assertEqual(chaperone.universes(a), chaperone.universes(b))
        self.assertEqual(chaperone.universes(None), {})

    def test_write_back_restores_the_file_or_removes_a_new_one(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            file = root / "chaperone_info.vrchap"
            kept = root / "kept.json"
            kept.write_text(json.dumps({"file": str(file), "content": "{\"universes\": []}\n"}))
            file.write_text("changed")
            chaperone.write_back(kept)
            self.assertEqual(file.read_text(), "{\"universes\": []}\n")
            kept.write_text(json.dumps({"file": str(file), "content": None}))
            chaperone.write_back(kept)
            self.assertFalse(file.exists())

    def test_walls_close_the_rectangle(self):
        import ctypes
        from types import SimpleNamespace

        class Vector(ctypes.Structure):  # openvr.HmdVector3_t; CI runs without the openvr package
            _fields_ = [("v", ctypes.c_float * 3)]

        class Quad(ctypes.Structure):  # openvr.HmdQuad_t
            _fields_ = [("vCorners", Vector * 4)]

        quads = chaperone.walls(SimpleNamespace(HmdQuad_t=Quad), 2.0, 3.0)
        floor = [(round(q.vCorners[0].v[0], 3), round(q.vCorners[0].v[2], 3)) for q in quads]
        self.assertEqual(floor, [(-1.0, -1.5), (1.0, -1.5), (1.0, 1.5), (-1.0, 1.5)])
        for q in quads:
            self.assertEqual([round(c.v[1], 2) for c in q.vCorners], [0.0, 0.0, 2.43, 2.43])

    def test_restore_without_a_snapshot_changes_nothing(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(chaperone, "SNAPSHOTS", Path(root)), \
                mock.patch.object(driver, "quit_steamvr", side_effect=AssertionError("no restart expected")):
            code, _, err = _run(["chaperone", "restore"])
        self.assertEqual(code, output.NOT_MET)
        self.assertIn("nothing to restore", err)


if __name__ == "__main__":
    unittest.main()
