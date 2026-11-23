"""Offline unit tests for `sergulath soak` (no SteamVR, no live app needed)."""

import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sergulath import cli, output
from sergulath.core import app as app_core
from sergulath.core import soak, steamvr


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def _sample(n: int, elapsed: float, tree_mb: float, main_mb: float | None = None, alive: bool = True):
    return soak.SoakSample(
        time=f"2026-11-24T09:{n:02d}:00+09:00",
        sample=n,
        elapsed_s=elapsed,
        pid=1234,
        alive=alive,
        process_count=3 if alive else 0,
        main_private_mb=main_mb if main_mb is not None else tree_mb * 0.5,
        main_working_set_mb=tree_mb * 0.5,
        tree_private_mb=tree_mb,
        tree_working_set_mb=tree_mb,
    )


class MemoryEvaluationTest(unittest.TestCase):
    def test_flat_memory_after_warmup_passes(self):
        # Big warmup allocation from 150 -> 260 MiB in first 2 minutes, then steady around 260 MiB.
        samples = [
            _sample(0, 0.0, 150.0),
            _sample(1, 60.0, 220.0),
            _sample(2, 120.0, 260.0),
            _sample(3, 600.0, 265.0),
            _sample(4, 1200.0, 262.0),
            _sample(5, 1800.0, 264.0),
        ]
        report = soak.evaluate_memory(samples, warmup_s=120.0, max_growth_mb=100.0, max_slope_mb_per_hour=120.0)
        self.assertTrue(report.ok, report.reason)
        self.assertEqual(report.warmup_samples, 2)
        self.assertEqual(report.baseline_mb, 260.0)
        self.assertEqual(report.growth_mb, 4.0)

    def test_steady_leak_exceeding_slope_fails_even_below_max_growth(self):
        # Steady +3 MiB/min = 180 MiB/h over 20 minutes post-warmup (+60 MiB < 100 MiB cap, but slope > 120).
        samples = [_sample(i, i * 60.0, 200.0 + i * 3.0) for i in range(23)]
        report = soak.evaluate_memory(samples, warmup_s=120.0, max_growth_mb=100.0, max_slope_mb_per_hour=120.0)
        self.assertFalse(report.ok)
        self.assertIn("trend", report.reason)
        self.assertAlmostEqual(report.slope_mb_per_hour, 180.0, places=1)

    def test_short_run_noise_does_not_false_fail_on_slope(self):
        # +2 MiB over 10 seconds extrapolates to 720 MiB/h, but net growth is below the 16 MiB floor.
        samples = [_sample(0, 0.0, 200.0), _sample(1, 10.0, 202.0)]
        report = soak.evaluate_memory(samples, warmup_s=0.0, max_growth_mb=100.0, max_slope_mb_per_hour=120.0)
        self.assertTrue(report.ok, report.reason)

    def test_growth_over_limit_fails(self):
        samples = [_sample(0, 0.0, 200.0), _sample(1, 900.0, 320.0)]
        report = soak.evaluate_memory(samples, warmup_s=0.0, max_growth_mb=100.0, max_slope_mb_per_hour=1000.0)
        self.assertFalse(report.ok)
        self.assertIn("grew by 120.00 MiB", report.reason)


class SoakLoopTest(unittest.TestCase):
    def test_process_exit_stops_early_and_fails(self):
        ticks = [0.0]
        responses = [
            soak.ProcessMemory(99, True, 2, 100.0, 110.0, 200.0, 220.0),
            soak.ProcessMemory(99, True, 2, 101.0, 111.0, 201.0, 221.0),
            None,
        ]
        idx = [0]

        def read_proc(pid):
            r = responses[min(idx[0], len(responses) - 1)]
            idx[0] += 1
            return r

        def fake_sleep(s):
            ticks[0] += s

        samples, summary = soak.run_soak(
            pid=99, duration_s=600.0, interval_s=60.0, warmup_s=0.0,
            read_process=read_proc, sleep=fake_sleep, clock=lambda: ticks[0],
        )
        self.assertEqual(len(samples), 3)
        self.assertFalse(summary.ok)
        self.assertFalse(summary.alive)
        self.assertIn("exited", summary.reason)

    def test_overlay_dropping_below_min_alpha_fails(self):
        ticks = [0.0]
        alphas = [1.0, 1.0, 0.0]
        idx = [0]

        def read_ov(keys, min_alpha):
            a = alphas[idx[0]]
            idx[0] += 1
            return [{"key": "ASTAROT0", "found": True, "visible": True, "alpha": a, "error": ""}], a >= min_alpha

        _, summary = soak.run_soak(
            pid=99, duration_s=120.0, interval_s=60.0, warmup_s=0.0,
            overlay_keys=["ASTAROT0"], min_alpha=0.01,
            read_process=lambda pid: soak.ProcessMemory(pid, True, 2, 100.0, 100.0, 200.0, 200.0),
            read_overlays=read_ov,
            sleep=lambda s: ticks.__setitem__(0, ticks[0] + s),
            clock=lambda: ticks[0],
        )
        self.assertFalse(summary.ok)
        self.assertTrue(summary.alive)
        self.assertTrue(summary.memory_ok)
        self.assertFalse(summary.overlay_ok)
        self.assertEqual(summary.overlay_failures, 1)


class SoakCommandTest(unittest.TestCase):
    def test_cli_json_and_csv_pass(self):
        with tempfile.TemporaryDirectory() as root:
            csv_path = Path(root) / "soak.csv"
            mem = soak.ProcessMemory(42, True, 3, 120.0, 130.0, 250.0, 260.0)
            with mock.patch.object(app_core, "running_pid", lambda name: 42), \
                    mock.patch.object(soak, "sample_process_tree", lambda pid: mem), \
                    mock.patch("time.sleep", lambda s: None):
                code, out, _ = _run([
                    "soak", "--duration", "120", "--interval", "60", "--warmup", "0",
                    "--csv", str(csv_path), "--format", "json",
                ])
            self.assertEqual(code, output.OK)
            records = [json.loads(line) for line in out.splitlines()]
            self.assertEqual(records[-1]["source"], "summary")
            self.assertTrue(records[-1]["ok"])
            self.assertEqual(records[-1]["samples"], 3)
            rows = list(csv.reader(csv_path.open(encoding="utf-8")))
            self.assertEqual(rows[0], soak.CSV_FIELDS)
            self.assertEqual(len(rows), 4)

    def test_not_running_exits_1(self):
        with mock.patch.object(app_core, "running_pid", lambda name: None):
            code, out, _ = _run(["soak", "--duration", "0", "--format", "json"])
        self.assertEqual(code, output.NOT_MET)
        self.assertFalse(json.loads(out)["alive"])

    def test_overlay_requires_steamvr_exit_3(self):
        with mock.patch.object(app_core, "running_pid", lambda name: 42), \
                mock.patch.object(steamvr, "missing_processes", lambda running=None: ["vrserver.exe"]):
            code, out, err = _run(["soak", "--duration", "0", "--overlay", "ASTAROT0"])
        self.assertEqual(code, output.STEAMVR_NOT_RUNNING)
        self.assertEqual(out, "")
        self.assertIn("SteamVR is not running", err)

    def test_bad_arguments_exit_2(self):
        code, _, err = _run(["soak", "--interval", "0"])
        self.assertEqual(code, output.USAGE)
        self.assertIn("--interval", err)


if __name__ == "__main__":
    unittest.main()
