"""`overlay --save` writes the colours the headset shows: texture overlays come back BGRA."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sergulath import cli, profiles
from sergulath.core import overlay, steamvr

AMBER_RGBA = bytes((212, 161, 98, 255))
AMBER_BGRA = bytes((98, 161, 212, 255))


def _read_png_pixel(path: Path) -> bytes:
    import zlib
    data = path.read_bytes()
    idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
    return zlib.decompress(idat)[1:5]  # filter byte, then the first pixel


class FakeOverlay:
    def __init__(self, images: dict):
        self.images = images

    def findOverlay(self, key):
        return key

    def isOverlayVisible(self, handle):
        return True

    def getOverlayAlpha(self, handle):
        return 1.0

    def getOverlayWidthInMeters(self, handle):
        return 1.0

    def getOverlayImageData(self, handle, buffer, size):
        data = self.images[handle]
        buffer[: len(data)] = data
        return 1, 1


class OverlayPixelsTest(unittest.TestCase):
    def test_swap(self):
        self.assertEqual(overlay.bgra_to_rgba(AMBER_BGRA * 2), AMBER_RGBA * 2)

    def test_profile_names_the_texture_overlays(self):
        bgra = profiles.load("astarot")["overlay_pixels"]["bgra"]
        self.assertEqual(bgra, ["ASTAROT2", "ASTAROT3", "ASTAROT4"])

    def _save(self, extra):
        fake = FakeOverlay({"ASTAROT0": AMBER_RGBA, "ASTAROT3": AMBER_BGRA})
        openvr = SimpleNamespace(IVROverlay=lambda: fake)

        @contextlib.contextmanager
        def client():
            yield openvr

        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, True))
        with mock.patch.object(steamvr, "background_client", client), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.main(["overlay", "--key", "ASTAROT0", "--key", "ASTAROT3", "--save", str(root), *extra])
        return _read_png_pixel(root / "ASTAROT0.png"), _read_png_pixel(root / "ASTAROT3.png")

    def test_raw_and_texture_overlays_both_save_amber(self):
        self.assertEqual(self._save([]), (AMBER_RGBA, AMBER_RGBA))

    def test_the_byte_order_can_be_forced(self):
        self.assertEqual(self._save(["--pixels", "rgba"]), (AMBER_RGBA, AMBER_BGRA))


if __name__ == "__main__":
    unittest.main()
