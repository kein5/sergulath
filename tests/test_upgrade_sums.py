"""Where `upgrade check` finds a downloaded package's SHA-256: `<asset>.sha256`, else the
release's SHA256SUMS-windows (ASTAROT 0.7 publishes only the latter)."""

import io
import unittest
import urllib.error
from unittest import mock

from sergulath.core import upgrade

HASH_ZIP = "a" * 64
HASH_EXE = "b" * 64
SUMS = f"{HASH_ZIP}  ASTAROT.zip\n{HASH_EXE} *ASTAROT_setup.exe\n"
BASE = "https://github.com/kein5/astarot/releases/download/v0.7.0-beta.2"


def _server(files: dict):
    def get(url, timeout=30.0):
        if url in files:
            return files[url]
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
    return get


class Sha256SumsTest(unittest.TestCase):
    def test_parse_sums_with_text_and_binary_markers(self):
        self.assertEqual(upgrade.parse_sha256_sums(SUMS, "ASTAROT.zip"), HASH_ZIP)
        self.assertEqual(upgrade.parse_sha256_sums(SUMS, "ASTAROT_setup.exe"), HASH_EXE)
        self.assertIsNone(upgrade.parse_sha256_sums(SUMS, "VRCT-VOX.zip"))

    def test_a_per_asset_file_still_comes_first(self):
        files = {f"{BASE}/ASTAROT_setup.exe.sha256": f"{'c' * 64}  ASTAROT_setup.exe\n",
                 f"{BASE}/SHA256SUMS-windows": SUMS}
        with mock.patch.object(upgrade, "_get_text", _server(files)):
            found = upgrade.expected_sha256_for(f"{BASE}/ASTAROT_setup.exe", f"{BASE}/ASTAROT_setup.exe.sha256")
        self.assertEqual(found, "c" * 64)

    def test_without_it_the_release_sums_file_is_read(self):
        with mock.patch.object(upgrade, "_get_text", _server({f"{BASE}/SHA256SUMS-windows": SUMS})):
            found = upgrade.expected_sha256_for(f"{BASE}/ASTAROT_setup.exe", f"{BASE}/ASTAROT_setup.exe.sha256")
        self.assertEqual(found, HASH_EXE)

    def test_no_hash_anywhere_is_an_error_naming_what_was_tried(self):
        with mock.patch.object(upgrade, "_get_text", _server({f"{BASE}/SHA256SUMS-windows": SUMS})):
            with self.assertRaises(ValueError) as caught:
                upgrade.expected_sha256_for(f"{BASE}/Other.zip", f"{BASE}/Other.zip.sha256")
        self.assertIn("Other.zip.sha256: 404", str(caught.exception))
        self.assertIn("SHA256SUMS-windows: no line for Other.zip", str(caught.exception))

    def test_other_http_errors_are_not_hidden(self):
        def boom(url, timeout=30.0):
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, io.BytesIO(b""))
        with mock.patch.object(upgrade, "_get_text", boom):
            with self.assertRaises(urllib.error.HTTPError):
                upgrade.expected_sha256_for(f"{BASE}/ASTAROT.zip", f"{BASE}/ASTAROT.zip.sha256")


if __name__ == "__main__":
    unittest.main()
