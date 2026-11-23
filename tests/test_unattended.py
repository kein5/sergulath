"""settings (snapshot and restore) and logs (one timeline), without SteamVR."""

import json
import tempfile
import unittest
from datetime import timezone
from pathlib import Path

from sergulath.commands import logs
from sergulath.core import settings


class SettingsTest(unittest.TestCase):
    def test_a_change_is_snapshotted_and_restored_key_by_key(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            path = root / "steamvr.vrsettings"
            path.write_text(json.dumps({"power": {"turnOffScreensTimeout": 300}, "steamvr": {"mirrorViewEye": 0}}))
            done, snapshot = settings.apply({"power.turnOffScreensTimeout": 1800, "steamvr.forcedDriver": "null"},
                                            path, root / "snapshots")
            self.assertEqual(done[0].before, 300)
            self.assertEqual(done[1].before, settings.MISSING)
            data = json.loads(path.read_text())
            self.assertEqual((data["power"]["turnOffScreensTimeout"], data["steamvr"]["forcedDriver"]), (1800, "null"))
            # SteamVR writes other keys meanwhile; restore leaves them.
            data["steamvr"]["mirrorViewEye"] = 1
            path.write_text(json.dumps(data))
            self.assertEqual(settings.pending(root / "snapshots"), [snapshot])
            settings.restore(snapshot)
            data = json.loads(path.read_text())
            self.assertEqual(data["power"]["turnOffScreensTimeout"], 300)
            self.assertNotIn("forcedDriver", data["steamvr"])
            self.assertEqual(data["steamvr"]["mirrorViewEye"], 1)
            self.assertEqual(settings.pending(root / "snapshots"), [])

    def test_values_are_json_or_text(self):
        self.assertIs(settings.parse_value("false"), False)
        self.assertEqual(settings.parse_value("300"), 300)
        self.assertEqual(settings.parse_value("null-driver"), "null-driver")
        with self.assertRaises(ValueError):
            settings.split("nosection")


class LogsTest(unittest.TestCase):
    def test_steamvr_local_and_app_utc_lines_share_one_clock(self):
        steamvr = logs.parse(["Mon Nov 23 2026 08:05:08.810 [Info] - vrserver startup",
                              "   (continued)"], "vrserver")
        app = logs.parse(["2026-11-22 23:05:09,000 UTC - process - INFO - overlay: started"], "app")
        self.assertEqual(steamvr[0].text, "[Info] - vrserver startup (continued)")
        self.assertEqual(app[0].when.astimezone(timezone.utc).hour, 23)
        merged = logs.merge(app, steamvr)
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(a.when <= b.when for a, b in zip(merged, merged[1:])))


if __name__ == "__main__":
    unittest.main()
