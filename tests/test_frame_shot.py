"""`frame shot` without a headset: the password goes only to sudo's stdin, and the folder goes away."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sergulath import cli, output
from sergulath.core import frame

SECRET = "correct horse battery"


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class FakeHeadset:
    """Stands in for ssh and scp; records every call."""

    def __init__(self, capture_lines, exit_code=0, stderr=""):
        self.calls = []
        self.capture_lines = capture_lines
        self.exit_code = exit_code
        self.stderr = stderr

    def __call__(self, args, stdin=None, timeout=90):
        self.calls.append((list(args), stdin))
        command = args[-1]
        if command.startswith("mktemp"):
            return SimpleNamespace(returncode=0, stdout="/tmp/sergulath.abc123\n", stderr="")
        if command.startswith("cat >") or command.startswith("rm -rf"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[0] == "scp":
            Path(args[-1]).write_bytes(b"\x89PNG")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=self.exit_code, stdout="\n".join(json.dumps(l) for l in self.capture_lines),
                               stderr=self.stderr)


PLANE = {"step": "plane", "ok": True, "plane": 41, "crtc": 101, "fb": 106, "width": 4320, "height": 2160,
         "format": "XR24", "modifier": "0x500000000000001", "root": True}


class FrameShotTest(unittest.TestCase):
    def _shot(self, headset, password=True):
        with tempfile.TemporaryDirectory() as root:
            argv = ["frame", "shot", "--out", os.path.join(root, "shot.png")]
            if password:
                secret = Path(root) / "secret"
                secret.write_text(SECRET + "\n", encoding="utf-8")
                argv += ["--password-file", str(secret)]
            with mock.patch.object(frame, "_run", headset):
                return _run(argv + ["--format", "json"])

    def test_the_password_goes_only_to_sudo_on_stdin(self):
        headset = FakeHeadset([PLANE, {"step": "handle", "ok": True, "ms": 5},
                               {"step": "read", "ok": True, "gl_error": 0, "width": 4320, "height": 2160,
                                "lit_share": 0.5, "read_ms": 80, "png_ms": 3000,
                                "remote_file": "/tmp/sergulath.abc123/frame-scanout.png"}])
        code, out, err = self._shot(headset)
        self.assertEqual(code, output.OK)
        capture = [c for c in headset.calls if "capture.py" in c[0][-1] and not c[0][-1].startswith("cat")]
        self.assertEqual(len(capture), 1)
        args, stdin = capture[0]
        self.assertIn("sudo -S -k -p ''", args[-1])
        self.assertEqual(stdin, SECRET + "\n")
        for call_args, call_stdin in headset.calls:
            self.assertFalse(any(SECRET in a for a in call_args))
            if call_args is not args:
                self.assertFalse(call_stdin and SECRET in call_stdin)
        self.assertNotIn(SECRET, out + err)
        self.assertEqual(headset.calls[-1][0][-1], "rm -rf /tmp/sergulath.abc123")

    def test_without_a_password_file_it_is_a_dry_run_without_sudo(self):
        headset = FakeHeadset([{**PLANE, "root": False},
                               {"step": "handle", "ok": False, "error": "handed out only to root"}], exit_code=3)
        code, out, _ = self._shot(headset, password=False)
        self.assertEqual(code, output.NOT_MET)
        self.assertFalse(any("sudo" in c[0][-1] for c in headset.calls))
        self.assertIn("rm -rf", headset.calls[-1][0][-1])

    def test_a_refused_password_is_said_without_repeating_it(self):
        headset = FakeHeadset([], exit_code=1, stderr="Sorry, try again.\nsudo: 1 incorrect password attempt")
        code, out, err = self._shot(headset)
        self.assertEqual(code, output.FAILED)
        self.assertIn("sudo refused the password", err)
        self.assertNotIn(SECRET, out + err)
        self.assertIn("rm -rf", headset.calls[-1][0][-1])


class ScanoutScriptTest(unittest.TestCase):
    def test_the_framebuffer_struct_follows_libdrm(self):
        # drmModeFB2: fb_id, width, height, pixel_format, modifier, flags, handles[4], pitches[4], offsets[4].
        # The first root run on 2026-11-23 read the offsets as handles because of a wrong order.
        text = (Path(frame.__file__).parent / "frame_scanout.py").read_text(encoding="utf-8")
        start = text.index("class FB2")
        fields = text[start:text.index("]", text.index("_fields_", start) + 200) + 1]
        order = [name for name in ("fb_id", "width", "height", "pixel_format", "modifier", "flags",
                                   "handles", "pitches", "offsets")]
        positions = [fields.index(f'"{name}"') for name in order]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
