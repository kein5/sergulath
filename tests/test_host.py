"""Linux support: the process list from /proc, Steam's folders, and Windows-only features that
end with a clear "not available" instead of a traceback. Runs on Windows and Linux alike."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sergulath import cli, output
from sergulath.core import driver, host, settings, soak, steamvr_status, suite


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def _fake_proc(root: Path, pid: int, comm: str, ppid: int = 1, cmdline: bytes = b"", status: str = "",
               rollup: str | None = None) -> None:
    folder = root / str(pid)
    folder.mkdir(parents=True)
    (folder / "comm").write_text(comm + "\n")
    (folder / "cmdline").write_bytes(cmdline)
    (folder / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} 0 -1 4194560\n")
    (folder / "status").write_text(status or f"Name:\t{comm}\nState:\tS (sleeping)\nVmRSS:\t    2048 kB\n")
    if rollup is not None:
        (folder / "smaps_rollup").write_text(rollup)


class ProcessTableTest(unittest.TestCase):
    def test_names_keep_the_windows_spelling(self):
        self.assertEqual(host.normalize("vrserver"), "vrserver.exe")
        self.assertEqual(host.normalize("VRServer.EXE"), "vrserver.exe")

    def test_proc_table_reads_argv0_then_comm(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            # /proc/PID/exe is a link only the kernel makes; without it argv[0] names the program.
            _fake_proc(root, 100, "vrserver", cmdline=b"/home/u/.steam/steam/steamapps/common/SteamVR/bin/linux64/vrserver\0--keepalive\0")
            _fake_proc(root, 101, "vrcompositor")
            (root / "self").mkdir()  # not a pid
            table = sorted(host._proc_table(root))
        self.assertEqual(table, [("vrcompositor.exe", 101), ("vrserver.exe", 100)])

    def test_tree_follows_parents_even_with_spaces_in_names(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            _fake_proc(root, 10, "astarot", ppid=1)
            _fake_proc(root, 11, "Web Content (x)", ppid=10)
            _fake_proc(root, 12, "python3", ppid=11)
            _fake_proc(root, 20, "other", ppid=1)
            parents = host.parent_map(root)
        self.assertEqual(parents[11], 10)
        self.assertEqual(host.tree(10, parents), [10, 11, 12])
        self.assertEqual(host.tree(99, parents), [])

    def test_status_uses_the_shared_process_table(self):
        with mock.patch.object(host, "process_table", lambda: [("vrserver.exe", 5), ("vrcompositor.exe", 6)]):
            pids = steamvr_status.process_pids()
        self.assertEqual(pids["vrserver.exe"], 5)
        self.assertIsNone(pids["vrmonitor.exe"])

    def test_linux_steam_folders(self):
        roots = host.linux_steam_roots(Path("/home/u"))
        self.assertEqual(roots[0], Path("/home/u/.steam/steam"))
        self.assertIn(Path("/home/u/.var/app/com.valvesoftware.Steam/.local/share/Steam"), roots)


class LinuxMemoryTest(unittest.TestCase):
    def test_private_and_resident_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            _fake_proc(root, 7, "astarot", rollup="Rss:  4096 kB\nPrivate_Clean:  100 kB\nPrivate_Dirty:  900 kB\nSwap:  24 kB\n")
            self.assertEqual(soak._linux_memory_bytes(7, root), (1024 * 1024, 2048 * 1024))

    def test_without_smaps_rollup_falls_back_to_status(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            _fake_proc(root, 8, "x", status="State:\tS\nVmRSS:\t 300 kB\nRssAnon:\t 200 kB\nVmSwap:\t 56 kB\n")
            self.assertEqual(soak._linux_memory_bytes(8, root), (256 * 1024, 300 * 1024))

    def test_a_zombie_or_missing_process_has_no_memory(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            _fake_proc(root, 9, "x", status="State:\tZ (zombie)\n")
            self.assertIsNone(soak._linux_memory_bytes(9, root))
            self.assertIsNone(soak._linux_memory_bytes(404, root))


@mock.patch.object(host, "WINDOWS", False)
class WindowsOnlyFeaturesTest(unittest.TestCase):
    """What a Linux user sees for the Windows-only parts: exit 1, one line, JSON status unsupported."""

    def test_audio_says_unsupported_in_json(self):
        code, out, err = _run(["audio", "devices", "--format", "json"])
        self.assertEqual(code, output.NOT_MET)
        record = json.loads(out)
        self.assertEqual((record["source"], record["status"]), ("audio", "unsupported"))
        self.assertIn("unsupported_on", record)
        self.assertIn("not available", err)
        self.assertIn("WASAPI", err)

    def test_text_format_prints_nothing_on_stdout(self):
        code, out, err = _run(["app", "press", "Start"])
        self.assertEqual(code, output.NOT_MET)
        self.assertEqual(out, "")
        self.assertIn("UI Automation", err)

    def test_view_open_changes_nothing_before_saying_so(self):
        with mock.patch.object(settings, "apply", side_effect=AssertionError("no change expected")), \
                mock.patch.object(driver, "quit_steamvr", side_effect=AssertionError("SteamVR must not stop")):
            code, _, err = _run(["view", "open"])
        self.assertEqual(code, output.NOT_MET)
        self.assertIn("view open", err)

    def test_hmd_with_driver_stops_before_steamvr_quits(self):
        with mock.patch.object(settings, "latest_snapshot", lambda label="": None), \
                mock.patch.object(driver, "quit_steamvr", side_effect=AssertionError("SteamVR must not stop")):
            code, _, err = _run(["hmd", "on", "--with-driver"])
        self.assertEqual(code, output.NOT_MET)
        self.assertIn("--with-driver", err)

    def test_controller_and_driver(self):
        for argv in (["controller", "connect", "--hand", "left"], ["driver", "register"], ["driver", "build"],
                     ["overlay", "press", "--overlay", "ASTAROT3", "--at", "0.5", "0.5"], ["caption", "sample", "start"],
                     ["upgrade", "check", "--work-dir", "unused"]):
            with self.subTest(argv=argv):
                code, _, err = _run(argv)
                self.assertEqual(code, output.NOT_MET)
                self.assertIn("not available", err)

    def test_read_only_dashboard_status_is_not_refused(self):
        with mock.patch("sergulath.core.steamvr.missing_processes", lambda running=None: ["vrserver.exe"]):
            code, _, _ = _run(["controller", "dashboard", "status"])
        self.assertEqual(code, output.STEAMVR_NOT_RUNNING)


class SuiteCountsOsUnsupportedApartTest(unittest.TestCase):
    def test_a_step_reporting_unsupported_on_is_unsupported_without_unsupported_when(self):
        record = {"source": "audio", "status": "unsupported", "unsupported_on": "Linux", "reason": "audio needs Windows"}
        step = suite.Step(run=[sys.executable, "-c", f"import sys; print({json.dumps(json.dumps(record))}); sys.exit(1)"])
        result = suite.run_step(step)
        self.assertTrue(result.unsupported)
        self.assertEqual(result.why, "audio needs Windows")

    def test_a_plain_exit_1_still_fails(self):
        step = suite.Step(run=[sys.executable, "-c", "import sys; sys.exit(1)"])
        result = suite.run_step(step)
        self.assertFalse(result.unsupported)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
