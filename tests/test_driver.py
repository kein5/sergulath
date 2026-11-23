"""The virtual controller's aiming, registry handling and commands, without SteamVR."""

import contextlib
import io
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sergulath import cli, output
from sergulath.core import aim, driver, placement, steamvr


def close(a, b, places=6):
    return all(abs(x - y) < 10 ** -places for x, y in zip(a, b))


class AimTest(unittest.TestCase):
    def test_quaternions_round_trip(self):
        for q in ([1, 0, 0, 0], [0, 1, 0, 0], [0.5, 0.5, 0.5, 0.5], [0.9238795, 0, 0.3826834, 0]):
            m = aim.from_quaternion([1, 2, 3], q)
            back = aim.quaternion(m)
            if back[0] * q[0] < 0:
                back = [-c for c in back]
            self.assertTrue(close(back, q), (q, back))

    def test_inverse_undoes_a_pose(self):
        m = aim.from_quaternion([0.3, 1.2, -0.5], [0.9238795, 0.3826834, 0, 0])
        self.assertTrue(close(sum(placement.multiply(m, aim.inverse(m)), []), sum(aim.IDENTITY, [])))

    def test_the_tilted_laser_of_the_body_is_the_wanted_ray(self):
        overlay = aim.from_quaternion([0.1, 1.0, -0.4], [0.7071068, 0, 0.7071068, 0])
        target = aim.point_on(overlay, 0.4, 0.2, 0.25, 0.75)
        ray = aim.ray_at(overlay, target, 0.12)
        body = aim.body_for_ray(ray, 12.0)
        laser = placement.multiply(body, aim.laser_tilt(12.0))
        self.assertTrue(close(sum(laser, []), sum(ray, [])))
        # The laser leaves 0.12 m in front of the target and runs along -z into it.
        start = aim.axis(laser, 3)
        direction = [-c for c in aim.axis(laser, 2)]
        self.assertTrue(close([start[i] + direction[i] * 0.12 for i in range(3)], target))
        # (u, v) = (0.25, 0.75): left of centre and below it, in the overlay's own frame.
        self.assertTrue(close(aim.apply(aim.inverse(overlay), target), [-0.1, -0.05, 0.0]))

    def test_look_at_points_minus_z_at_the_target(self):
        pose = aim.look_at([0, 1, 0], [0, 1, -2])
        self.assertTrue(close([-c for c in aim.axis(pose, 2)], [0, 0, -1]))
        self.assertTrue(close(aim.axis(pose, 1), [0, 1, 0]))
        self.assertAlmostEqual(math.dist(aim.axis(pose, 3), [0, 1, 0]), 0)


class RegistryTest(unittest.TestCase):
    def test_registration_is_read_from_openvrpaths(self):
        folder = Path(tempfile.gettempdir()) / "sergulath-driver"
        self.assertFalse(driver.is_registered(folder, {"external_drivers": None}))
        self.assertTrue(driver.is_registered(folder, {"external_drivers": [str(folder) + os.sep]}))

    def test_a_reply_that_is_not_ok_is_an_error(self):
        class Socket:
            def __init__(self, reply):
                self.reply = reply

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def settimeout(self, t):
                pass

            def sendto(self, data, address):
                self.sent = data

            def recv(self, n):
                return self.reply

        with mock.patch("socket.socket", lambda *a: Socket(b"ok {\"version\": \"0.1.0\"}")):
            self.assertEqual(driver.state(), {"version": "0.1.0"})
        with mock.patch("socket.socket", lambda *a: Socket(b"error unknown hand \"middle\"")):
            with self.assertRaises(driver.DriverError):
                driver.send("connect middle")


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class CommandsTest(unittest.TestCase):
    def test_driver_status_without_steamvr_says_not_loaded(self):
        with mock.patch.object(driver, "is_registered", lambda *a: False), \
                mock.patch.object(driver, "state", side_effect=driver.DriverNotLoaded("no")):
            code, out, _ = _run(["driver", "status", "--format", "json"])
        self.assertEqual(code, output.NOT_MET)
        record = json.loads(out)
        self.assertEqual((record["registered"], record["loaded"]), (False, False))

    def test_driver_run_unregisters_even_when_the_command_fails(self):
        calls = []
        with mock.patch.object(driver, "register", lambda *a: calls.append("register")), \
                mock.patch.object(driver, "unregister", lambda *a: calls.append("unregister")), \
                mock.patch("subprocess.run", lambda *a, **k: SimpleNamespace(returncode=7)):
            code, _, _ = _run(["driver", "run", "--", "false"])
        self.assertEqual(code, 7)
        self.assertEqual(calls, ["register", "unregister"])

    def test_press_needs_a_button_the_profile_knows(self):
        code, _, err = _run(["overlay", "press", "--overlay", "ASTAROT3", "--button", "nothing"])
        self.assertEqual(code, output.USAGE)
        self.assertIn("log", err)

    def test_press_reports_the_hit_and_exits_1_without_one(self):
        from sergulath.core import press

        result = press.PressResult("ASTAROT3", "right", [0.58, 0.5], target_m=[0, 1, 0], controller_m=[0, 1, 0.12])

        @contextlib.contextmanager
        def client():
            yield object()

        with mock.patch.object(steamvr, "background_client", client), \
                mock.patch.object(press, "press", lambda *a, **k: result):
            code, out, _ = _run(["overlay", "press", "--overlay", "ASTAROT3", "--button", "log", "--dry-run"])
        self.assertEqual(code, output.NOT_MET)
        self.assertIn("does not hit", out)


if __name__ == "__main__":
    unittest.main()
