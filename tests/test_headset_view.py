"""Cutting an overlay out of the virtual headset's window: the geometry, without SteamVR."""

import unittest
from types import SimpleNamespace

from sergulath.core import headset_view as hv

IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
RAW = (-1.0, 1.0, -1.0, 1.0)  # the null driver's 90-degree projection


class GeometryTest(unittest.TestCase):
    def test_invert_undoes_a_rigid_transform(self):
        m = [[0.0, 0.0, 1.0, 1.0], [0.0, 1.0, 0.0, 2.0], [-1.0, 0.0, 0.0, 3.0]]  # 90 degrees about y, moved
        p = [0.3, -0.2, 0.5]
        back = hv.apply(hv.invert(m), hv.apply(m, p))
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(back, p)))

    def test_project_puts_up_at_the_top_and_right_at_the_right(self):
        self.assertEqual(hv.project([0.0, 0.0, -1.0], RAW), (0.5, 0.5))
        u, v = hv.project([0.3, 0.3, -1.0], RAW)
        self.assertAlmostEqual(u, 0.65)
        self.assertAlmostEqual(v, 0.35)
        self.assertIsNone(hv.project([0.0, 0.0, 1.0], RAW))  # behind the eye

    def test_two_probe_spots_give_the_eye_map_measured_on_2026_11_23(self):
        # Left eye, 480 x 270 window: the eye took 180 x 150 pixels at the top left.
        spots_uv = {"left": [(0.5, 0.5), (0.65, 0.35)]}
        spots_px = {"left": [(90.0, 75.0), (117.0, 52.5)]}
        eye = hv.solve(spots_uv, spots_px)["left"]
        self.assertAlmostEqual(eye.sx, 180.0)
        self.assertAlmostEqual(eye.sy, 150.0)
        for got, want in zip(hv.eye_area(eye), [0.0, 0.0, 180.0, 150.0]):
            self.assertAlmostEqual(got, want)

    def test_a_caption_in_front_is_cut_where_it_was_seen(self):
        # The one-line caption: 1.0 m wide, 0.077 m high, 0.4 m below and 1 m in front of the head.
        eye = hv.EyeMap(960.0, 0.0, 1080.0, 0.0)
        world = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, -0.4], [0.0, 0.0, 1.0, -1.0]]
        crop = hv.crop_box(hv.corners(world, 1.0, 0.077), IDENTITY, RAW, eye, hv.eye_area(eye))
        self.assertEqual(crop.inside, 1.0)
        left, top, right, bottom = crop.box
        self.assertEqual((left, right), (240, 720))
        self.assertTrue(733 <= top < bottom <= 778)  # seen at 735..777 in the 1920 x 1080 window

    def test_an_overlay_off_to_the_side_is_partly_in_view(self):
        eye = hv.EyeMap(100.0, 0.0, 100.0, 0.0)
        world = [[1.0, 0.0, 0.0, 1.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, -1.0]]  # centred on the right edge
        crop = hv.crop_box(hv.corners(world, 0.5, 0.5), IDENTITY, RAW, eye, hv.eye_area(eye))
        self.assertAlmostEqual(crop.inside, 0.5, places=2)
        self.assertEqual(crop.box[2], 100)


class PixelsTest(unittest.TestCase):
    def test_the_probe_is_found_once_per_eye(self):
        width, height = 40, 10
        rgba = bytearray(bytes([0, 0, 80, 255]) * width * height)
        for x0 in (5, 25):
            for y in range(3, 6):
                for x in range(x0, x0 + 3):
                    i = (y * width + x) * 4
                    rgba[i:i + 3] = bytes(hv.PROBE_COLOUR)
        clusters = hv.find_colour(width, height, bytes(rgba))
        self.assertEqual([hv.centre(c) for c in sorted(clusters, key=lambda c: c[0][0])], [(6.5, 4.5), (26.5, 4.5)])

    def test_cut_keeps_the_box(self):
        width = 4
        rgba = bytes(range(4 * 4 * 3))  # 4 x 3 pixels
        w, h, part = hv.cut(width, rgba, [1, 1, 3, 3])
        self.assertEqual((w, h), (2, 2))
        self.assertEqual(part, rgba[20:28] + rgba[36:44])


class SizeTest(unittest.TestCase):
    def test_height_comes_from_the_texture_and_a_first_failure_is_retried(self):
        calls = []

        class Overlay:
            def getOverlayWidthInMeters(self, h):
                return 1.0

            def getOverlayTextureSize(self, h):
                calls.append(h)
                if len(calls) == 1:
                    raise RuntimeError("OverlayError_InvalidTexture")
                return 3940, 304

            def getOverlayTextureBounds(self, h):
                return SimpleNamespace(uMin=0.0, vMin=0.0, uMax=1.0, vMax=1.0)

            def getOverlayTexelAspect(self, h):
                return 1.0

        width, height = hv.overlay_size(None, Overlay(), 7)
        self.assertEqual(width, 1.0)
        self.assertAlmostEqual(height, 304 / 3940)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()


class ProbeDepthTest(unittest.TestCase):
    """2026-11-26: with the VR panel (ASTAROT2) open 0.7 m in front, a probe at 1 m was hidden
    behind it (found 0 times), whatever the sort order. The probe now goes in front."""

    def test_half_the_overlays_distance_within_limits(self):
        self.assertEqual(hv.probe_depth(0.707), 0.3535)
        self.assertEqual(hv.probe_depth(3.0), 1.0)
        self.assertEqual(hv.probe_depth(0.1), 0.2)
        self.assertEqual(hv.probe_depth(None), 1.0)

    def test_a_hidden_probe_is_tried_again_at_the_nearest_depth(self):
        from unittest import mock

        tried = []

        def at(openvr, capture, settle, depth):
            tried.append(depth)
            if depth > 0.2:
                raise hv.ProbeNotFound("found 0 time(s)")
            return {"left": "map"}

        with mock.patch.object(hv, "_calibrate_at", at):
            self.assertEqual(hv.calibrate(None, None, depth=0.5), {"left": "map"})
        self.assertEqual(tried, [0.5, 0.2])

    def test_at_the_nearest_depth_the_failure_is_reported(self):
        from unittest import mock

        def at(openvr, capture, settle, depth):
            raise hv.ProbeNotFound(f"probe ({depth:.2f} m) found 0 time(s)")

        with mock.patch.object(hv, "_calibrate_at", at):
            with self.assertRaises(hv.ProbeNotFound) as caught:
                hv.calibrate(None, None, depth=0.2)
        self.assertIn("0.20 m", str(caught.exception))
