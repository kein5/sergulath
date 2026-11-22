"""Tests that run without SteamVR."""

import struct
import unittest
import zlib

from sergulath import profiles
from sergulath.core import png, steamvr, watch


class PngTest(unittest.TestCase):
    def test_encodes_rgba_rows_with_filter_bytes(self):
        data = png.encode_rgba(2, 1, bytes([1, 2, 3, 4, 5, 6, 7, 8]))
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (2, 1))
        idat_len = struct.unpack(">I", data[33:37])[0]
        raw = zlib.decompress(data[41:41 + idat_len])
        self.assertEqual(raw, b"\x00" + bytes([1, 2, 3, 4, 5, 6, 7, 8]))

    def test_refuses_a_buffer_of_the_wrong_size(self):
        with self.assertRaises(ValueError):
            png.encode_rgba(2, 2, b"\x00" * 4)


class BlankCaptureTest(unittest.TestCase):
    def test_one_colour_is_blank_and_two_are_not(self):
        from sergulath.core import window_capture

        black = bytes([0, 0, 0, 255]) * 1000
        self.assertTrue(window_capture.is_blank(black))
        mixed = bytearray(black)
        mixed[0:3] = b"\xff\x00\x00"
        self.assertFalse(window_capture.is_blank(bytes(mixed)))


class SteamVRStatusTest(unittest.TestCase):
    def test_version_is_the_last_startup_line(self):
        from sergulath.core import steamvr_status

        log = ("Sun Nov 22 2026 09:20:08 [Info] - vrserver 2.19.3 startup with PID=1\n"
               "Sun Nov 22 2026 10:42:06 [Info] - vrserver 2.19.4 startup with PID=2\n")
        self.assertEqual(steamvr_status.version_from_log(log), "2.19.4")
        self.assertIsNone(steamvr_status.version_from_log("nothing here"))

    def test_logs_folder_is_found_above_the_executable(self):
        import tempfile
        from pathlib import Path

        from sergulath.core import steamvr_status

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "logs").mkdir()
            (root / "logs" / "vrserver.txt").write_text("", encoding="utf-8")
            exe = root / "steamapps" / "common" / "SteamVR" / "bin" / "win64" / "vrserver.exe"
            self.assertEqual(steamvr_status.logs_folder(exe), root / "logs")


class ProfileTest(unittest.TestCase):
    def test_astarot_lists_current_and_legacy_overlay_keys(self):
        profile = profiles.load("astarot")
        self.assertEqual(profiles.overlay_keys(profile), [f"ASTAROT{i}" for i in range(5)])
        self.assertEqual(profiles.overlay_keys(profile, legacy=True), [f"VRCT{i}" for i in range(5)])


class PlacementTest(unittest.TestCase):
    IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]

    def test_composing_puts_the_offset_in_the_device_frame(self):
        from sergulath.core import placement

        # A device at (1, 1, 0) turned 90 degrees about y; the overlay 0.1 m along its -z.
        device = [[0.0, 0.0, 1.0, 1.0], [0.0, 1.0, 0.0, 1.0], [-1.0, 0.0, 0.0, 0.0]]
        offset = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, -0.1]]
        world = placement.multiply(device, offset)
        self.assertEqual([round(v, 6) for v in placement.translation(world)], [0.9, 1.0, 0.0])
        self.assertEqual(placement.yaw_pitch_roll(device)[0], 90.0)

    def test_a_hand_overlay_without_a_hand_pose_has_no_world_position(self):
        from types import SimpleNamespace

        from sergulath.core import placement

        def pose(valid, at):
            m = [row[:] for row in self.IDENTITY]
            for r in range(3):
                m[r][3] = at[r]
            return SimpleNamespace(bPoseIsValid=valid, mDeviceToAbsoluteTracking=m)

        poses = [pose(True, [0.0, 1.6, 0.0]), pose(False, [0.0, 0.0, 0.0]), pose(True, [0.2, 1.0, -0.3])]

        class System:
            def getTrackedDeviceClass(self, index):
                return 1 if index == 0 else 2

            def getControllerRoleForTrackedDeviceIndex(self, index):
                return index

        class OpenVR:
            k_unTrackedDeviceIndex_Hmd = 0

            @staticmethod
            def VRSystem():
                return System()

        offset = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.05], [0.0, 0.0, 1.0, 0.0]]

        class Overlay:
            def __init__(self, device):
                self.device = device

            def getOverlayTransformType(self, handle):
                return 1

            def getOverlayTransformTrackedDeviceRelative(self, handle):
                return self.device, offset

            def getOverlayTextureBounds(self, handle):
                return SimpleNamespace(uMin=0.0, vMin=0.5, uMax=1.0, vMax=1.0)

        left = placement.read(OpenVR, Overlay(1), "h", poses)
        self.assertEqual((left.device, left.device_pose_valid, left.world_m), ("controller/left", False, None))
        right = placement.read(OpenVR, Overlay(2), "h", poses)
        self.assertEqual(right.world_m, [0.2, 1.05, -0.3])
        self.assertEqual(right.texture_bounds, [0.0, 0.5, 1.0, 1.0])
        self.assertAlmostEqual(right.head_distance_m, round((0.2 ** 2 + 0.55 ** 2 + 0.3 ** 2) ** 0.5, 3))
        self.assertIn("world unknown (no pose)", placement.describe(left))

    def test_an_absolute_overlay_in_the_standing_space_is_where_it_says(self):
        from types import SimpleNamespace

        from sergulath.core import placement

        class OpenVR:
            k_unTrackedDeviceIndex_Hmd = 0

            @staticmethod
            def VRSystem():
                return None

        class Overlay:
            def getOverlayTransformType(self, handle):
                return 0

            def getOverlayTransformAbsolute(self, handle):
                return 1, PlacementTest.IDENTITY

        poses = [SimpleNamespace(bPoseIsValid=False, mDeviceToAbsoluteTracking=self.IDENTITY)]
        place = placement.read(OpenVR, Overlay(), "h", poses)
        self.assertEqual((place.transform, place.universe, place.world_m), ("absolute", "standing", [0.0, 0.0, 0.0]))


class SteamVRGuardTest(unittest.TestCase):
    def test_both_server_and_compositor_are_required(self):
        self.assertEqual(steamvr.missing_processes({"vrserver.exe", "vrcompositor.exe"}), [])
        self.assertEqual(steamvr.missing_processes({"vrserver.exe"}), ["vrcompositor.exe"])
        self.assertEqual(steamvr.missing_processes(set()), ["vrserver.exe", "vrcompositor.exe"])


class WatchSummaryTest(unittest.TestCase):
    def test_summary_names_role_and_headset_activity(self):
        samples = [
            watch.DeviceSample("t", 0, 0, "hmd", "-", True, False, "calibrating_out_of_range", "idle"),
            watch.DeviceSample("t", 0, 1, "controller", "right", True, True, "running_ok", ""),
        ]
        text = watch.summary(samples)
        self.assertIn("hmd#0 connected=True pose=False tracking=calibrating_out_of_range activity=idle", text)
        self.assertIn("controller/right#1 connected=True pose=True tracking=running_ok", text)
        self.assertEqual(watch.summary([]), "no headset or controllers")


if __name__ == "__main__":
    unittest.main()
