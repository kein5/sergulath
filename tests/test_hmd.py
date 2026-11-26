"""The virtual headset command and the hand placement for a wrist overlay, without SteamVR."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sergulath import cli, output
from sergulath.commands import hmd
from sergulath.core import aim, chaperone, driver, host, placement, settings


def close(a, b):
    return all(abs(x - y) < 1e-6 for x, y in zip(a, b))


class PlacementForShowingTest(unittest.TestCase):
    def test_the_overlay_ends_up_facing_the_viewer(self):
        relative = aim.from_quaternion([-0.06, -0.07, 0.25], [0.8, 0.2, -0.4, 0.4])
        wanted = aim.facing([0.0, -0.1, -0.45], [0.0, 0.0, 0.0])
        holder = aim.holder_for(relative, wanted)
        result = placement.multiply(holder, relative)
        self.assertTrue(close(sum(result, []), sum(wanted, [])))
        # Its front (+z) points back at the viewer.
        to_viewer = [-c for c in aim.axis(result, 3)]
        length = sum(c * c for c in to_viewer) ** 0.5
        self.assertTrue(close(aim.axis(result, 2), [c / length for c in to_viewer]))


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class HmdCommandTest(unittest.TestCase):
    def test_on_sets_the_null_driver_and_off_restores_exactly(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            file = root / "steamvr.vrsettings"
            file.write_text(json.dumps({"steamvr": {"mirrorViewEye": 0}}))
            chap = root / "chaperone_info.vrchap"
            frame_universe = {"universeID": "424242", "play_area": [3, 3]}
            chap_text = json.dumps({"jsonid": "chaperone_info", "universes": [frame_universe], "version": 5})
            chap.write_text(chap_text)
            calls = []
            with mock.patch.object(settings, "SNAPSHOTS", root / "snapshots"), \
                    mock.patch.object(settings, "settings_file", lambda: file), \
                    mock.patch.object(chaperone, "chaperone_file", lambda: chap), \
                    mock.patch.object(chaperone, "UNIVERSE_SNAPSHOTS", root / "chaperone"), \
                    mock.patch.object(driver, "quit_steamvr", lambda: calls.append("quit")), \
                    mock.patch.object(driver, "start_steamvr", lambda: calls.append("start")), \
                    mock.patch.object(driver, "register", lambda: calls.append("register")), \
                    mock.patch.object(driver, "wait_loaded", lambda: calls.append("loaded")), \
                    mock.patch.object(driver, "is_registered", lambda: True), \
                    mock.patch.object(driver, "unregister", lambda: calls.append("unregister")), \
                    mock.patch.object(host, "WINDOWS", True):  # the driver DLL is Windows-only; mocked here
                hmd.switch_on([0, 0, 480, 270], with_driver=True)
                data = json.loads(file.read_text())
                self.assertEqual(data["steamvr"]["forcedDriver"], "null")
                self.assertIs(data["driver_null"]["enable"], True)
                self.assertEqual(calls, ["quit", "register", "start", "loaded"])
                # The null headset's universe has a standing play area, so Room Setup does not open.
                ids = [u["universeID"] for u in json.loads(chap.read_text())["universes"]]
                self.assertEqual(ids, ["424242", chaperone.NULL_UNIVERSE])
                with self.assertRaises(RuntimeError):
                    hmd.switch_on([0, 0, 480, 270], with_driver=False)  # a snapshot is pending
                _, universes = hmd.switch_off()
                self.assertEqual(json.loads(file.read_text()), {"steamvr": {"mirrorViewEye": 0}})
                self.assertEqual(json.loads(chap.read_text())["universes"], [frame_universe])
                self.assertEqual(universes, [{"universe": "2", "removed": True, "file_removed": False}])
                self.assertEqual(calls[-3:], ["quit", "unregister", "start"])

    def test_a_universe_that_was_there_is_left_alone(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            chap = root / "chaperone_info.vrchap"
            own = {"universeID": "2", "play_area": [4, 5], "standing": {"translation": [1, 0, 0], "yaw": 90}}
            chap.write_text(json.dumps({"universes": [own]}))
            before = chap.read_text()
            snap = chaperone.ensure_universe("2", path=chap, snapshot_dir=root / "s")
            self.assertEqual(chap.read_text(), before)
            self.assertEqual(chaperone.restore_universe(snap)["removed"], False)
            self.assertEqual(chap.read_text(), before)
            self.assertTrue(snap.with_name(snap.stem + ".restored.json").is_file())

    def test_no_file_before_means_no_file_after(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            chap = root / "chaperone_info.vrchap"
            snap = chaperone.ensure_universe("2", path=chap, snapshot_dir=root / "s")
            universe = json.loads(chap.read_text())["universes"][0]
            self.assertEqual(universe["play_area"], [2.0, 2.0])
            self.assertEqual(len(universe["collision_bounds"]), 4)
            self.assertEqual(chaperone.restore_universe(snap), {"universe": "2", "removed": True, "file_removed": True})
            self.assertFalse(chap.exists())

    def test_run_restores_even_when_the_command_fails(self):
        calls = []
        with mock.patch.object(settings, "latest_snapshot", lambda **k: None), \
                mock.patch.object(hmd, "switch_on", lambda *a: calls.append("on")), \
                mock.patch.object(hmd, "switch_off", lambda: calls.append("off")), \
                mock.patch("subprocess.run", lambda *a, **k: SimpleNamespace(returncode=5)):
            code, _, _ = _run(["hmd", "run", "--", "false"])
        self.assertEqual(code, 5)
        self.assertEqual(calls, ["on", "off"])

    def test_run_refuses_while_a_snapshot_is_pending(self):
        with mock.patch.object(settings, "latest_snapshot", lambda **k: Path("x-hmd.json")):
            code, _, err = _run(["hmd", "run", "--", "false"])
        self.assertEqual(code, output.FAILED)
        self.assertIn("hmd off", err)


if __name__ == "__main__":
    unittest.main()
