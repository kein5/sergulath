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


class ProfileTest(unittest.TestCase):
    def test_astarot_lists_current_and_legacy_overlay_keys(self):
        profile = profiles.load("astarot")
        self.assertEqual(profiles.overlay_keys(profile), [f"ASTAROT{i}" for i in range(5)])
        self.assertEqual(profiles.overlay_keys(profile, legacy=True), [f"VRCT{i}" for i in range(5)])


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
