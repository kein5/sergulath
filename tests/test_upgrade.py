"""Offline unit tests for `sergulath upgrade` (works on Windows and Linux without network)."""

import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from sergulath import cli, output, profiles
from sergulath.core import steamvr, suite, upgrade


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def _make_package_zip(path: Path, version: str, extra_files: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("vrct-vox.exe", f"VERSION={version}\n".encode("utf-8"))
        zf.writestr("_internal/runtime.txt", f"runtime-{version}\n".encode("utf-8"))
        for name, data in (extra_files or {}).items():
            zf.writestr(name, data)
    return path


def _fake_version_reader(exe: Path) -> str | None:
    text = exe.read_text(encoding="utf-8", errors="ignore").strip()
    return text.split("=", 1)[1] if text.startswith("VERSION=") else None


class SemVerTest(unittest.TestCase):
    def test_precedence_with_beta_prereleases(self):
        ordered = ["0.5.2", "0.6.0-beta.1", "0.6.0-beta.2", "0.6.0-beta.10", "0.6.0", "0.6.1-beta.1", "0.6.1", "0.7.0-beta.1"]
        parsed = [upgrade.parse_semver(v) for v in ordered]
        for low, high in zip(parsed, parsed[1:]):
            self.assertLess(low, high, f"{low} should be < {high}")
        self.assertFalse(upgrade.is_newer("0.6.0", "0.6.0"))
        self.assertTrue(upgrade.is_newer("0.6.1-beta.1", "0.6.0"))
        self.assertFalse(upgrade.is_newer("0.6.0-beta.2", "0.6.0"))


class PackageUpdateTest(unittest.TestCase):
    def test_zip_update_preserves_user_settings_and_models(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            install = root / "install"
            old_zip = _make_package_zip(root / "old.zip", "0.6.0")
            new_zip = _make_package_zip(root / "new.zip", "0.6.1-beta.1")
            upgrade.apply_package_update(install, old_zip, "vrct-vox.exe")
            hashes = upgrade.seed_state(install, {"UI_LANGUAGE": "ja"}, {"TRANSPARENCY": 87})
            upgrade.apply_package_update(install, new_zip, "vrct-vox.exe")
            self.assertEqual(_fake_version_reader(install / "vrct-vox.exe"), "0.6.1-beta.1")
            self.assertEqual((install / "_internal" / "runtime.txt").read_text(encoding="utf-8").strip(), "runtime-0.6.1-beta.1")
            self.assertTrue(upgrade.verify_settings(install, {"UI_LANGUAGE": "ja", "TRANSPARENCY": 87})[0])
            self.assertTrue(upgrade.verify_models(install, "downloaded_models", hashes)[0])
            self.assertFalse((install / ".upd").exists())

    def test_zip_slip_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            bad = root / "bad.zip"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("../escape.txt", b"nope")
            with self.assertRaises(ValueError):
                upgrade.extract_zip_safe(bad, root / "out")

    def test_local_dir_update_ignores_source_user_data(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            install = root / "install"
            old_zip = _make_package_zip(root / "old.zip", "0.6.0")
            upgrade.apply_package_update(install, old_zip, "vrct-vox.exe")
            hashes = upgrade.seed_state(install, {"UI_LANGUAGE": "ja"}, {"TRANSPARENCY": 87})

            src_dir = root / "local_build"
            (src_dir / "_internal").mkdir(parents=True)
            (src_dir / "vrct-vox.exe").write_text("VERSION=0.6.1-beta.1\n", encoding="utf-8")
            (src_dir / "_internal" / "runtime.txt").write_text("runtime-dir\n", encoding="utf-8")
            # Even if the source folder contains its own config.json, sandbox user data must stay untouched.
            (src_dir / "config.json").write_text('{"TRANSPARENCY": 100}', encoding="utf-8")

            delivery = upgrade.apply_package_update(install, src_dir, "vrct-vox.exe")
            self.assertEqual(delivery, "local_dir")
            self.assertTrue(upgrade.verify_settings(install, {"TRANSPARENCY": 87})[0])
            self.assertTrue(upgrade.verify_models(install, "downloaded_models", hashes)[0])


class UpgradeFlowTest(unittest.TestCase):
    def test_unpublished_beta_without_local_file_reports_unsupported(self):
        profile = profiles.load("astarot")
        manifest = {"version": "0.6.0", "platforms": {"windows-x86_64": {"url": "https://example.invalid/setup.exe"}}}
        with tempfile.TemporaryDirectory() as root:
            result = upgrade.run_upgrade_check(
                profile=profile,
                work_dir=Path(root) / "work",
                from_version="0.6.0",
                channel="beta",
                fetch=lambda url: manifest,
            )
        self.assertEqual(result.status, "unsupported")
        self.assertFalse(result.ok)
        self.assertIn("no release newer than 0.6.0", result.reason)

    def test_local_zip_upgrade_passes_end_to_end(self):
        profile = profiles.load("astarot")
        boots = []

        def fake_boot(exe, settings_dir, window_title, **kw):
            boots.append(_fake_version_reader(exe))
            return True, True, ""

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            old_zip = _make_package_zip(root / "astarot-0.6.0.zip", "0.6.0")
            beta_zip = _make_package_zip(root / "astarot-0.6.1-beta.1.zip", "0.6.1-beta.1")
            result = upgrade.run_upgrade_check(
                profile=profile,
                work_dir=root / "work",
                from_path=old_zip,
                from_version="0.6.0",
                to_path=beta_zip,
                version_reader=_fake_version_reader,
                boot_runner=fake_boot,
            )
        self.assertEqual(result.status, "pass", result.reason)
        self.assertTrue(result.ok)
        self.assertEqual((result.from_version, result.to_version), ("0.6.0", "0.6.1-beta.1"))
        self.assertTrue(result.settings_preserved)
        self.assertTrue(result.models_preserved)
        self.assertTrue(result.launched_ok)
        self.assertEqual(boots, ["0.6.0", "0.6.1-beta.1"])

    def test_lost_setting_after_launch_fails(self):
        profile = profiles.load("astarot")

        def corrupting_boot(exe, settings_dir, window_title, **kw):
            if _fake_version_reader(exe) == "0.6.1-beta.1":
                cfg = settings_dir / "config.json"
                data = json.loads(cfg.read_text(encoding="utf-8"))
                data["TRANSPARENCY"] = 100
                cfg.write_text(json.dumps(data), encoding="utf-8")
            return True, True, ""

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            old_zip = _make_package_zip(root / "old.zip", "0.6.0")
            beta_zip = _make_package_zip(root / "beta.zip", "0.6.1-beta.1")
            result = upgrade.run_upgrade_check(
                profile=profile,
                work_dir=root / "work",
                from_path=old_zip,
                from_version="0.6.0",
                to_path=beta_zip,
                version_reader=_fake_version_reader,
                boot_runner=corrupting_boot,
            )
        self.assertEqual(result.status, "fail")
        self.assertFalse(result.settings_preserved)
        self.assertTrue(result.models_preserved)
        self.assertIn("TRANSPARENCY", result.reason)

    def test_deleted_model_after_launch_fails(self):
        profile = profiles.load("astarot")

        def deleting_boot(exe, settings_dir, window_title, **kw):
            if _fake_version_reader(exe) == "0.6.1-beta.1":
                (settings_dir / "downloaded_models" / "e2e-upgrade-sentinel" / "rev1" / "model.bin").unlink()
            return True, True, ""

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            old_zip = _make_package_zip(root / "old.zip", "0.6.0")
            beta_zip = _make_package_zip(root / "beta.zip", "0.6.1-beta.1")
            result = upgrade.run_upgrade_check(
                profile=profile,
                work_dir=root / "work",
                from_path=old_zip,
                from_version="0.6.0",
                to_path=beta_zip,
                version_reader=_fake_version_reader,
                boot_runner=deleting_boot,
            )
        self.assertEqual(result.status, "fail")
        self.assertTrue(result.settings_preserved)
        self.assertFalse(result.models_preserved)
        self.assertIn("model.bin", result.reason)

    def test_beta_channel_installer_path_snapshots_and_runs_installer(self):
        profile = profiles.load("astarot")
        manifest = {
            "version": "0.6.1-beta.1",
            "platforms": {"windows-x86_64": {"url": "https://github.com/kein5/astarot/releases/download/v0.6.1-beta.1/VRCT-VOX_setup.exe"}},
        }
        installer_calls = []

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            old_zip = _make_package_zip(root / "old.zip", "0.6.0")
            fake_setup = root / "VRCT-VOX_setup.exe"
            fake_setup.write_bytes(b"setup")

            def fake_download(url, dest, sha256_url=None):
                return fake_setup if url.endswith(".exe") else old_zip

            def fake_installer(install_dir, installer_exe, registry_keys, snapshot_path):
                installer_calls.append((install_dir, installer_exe.name, registry_keys))
                (install_dir / "vrct-vox.exe").write_text("VERSION=0.6.1-beta.1\n", encoding="utf-8")

            result = upgrade.run_upgrade_check(
                profile=profile,
                work_dir=root / "work",
                from_version="0.6.0",
                channel="beta",
                fetch=lambda url: manifest,
                download=fake_download,
                version_reader=_fake_version_reader,
                boot_runner=lambda exe, *a, **kw: (True, True, ""),
                installer_runner=fake_installer,
            )
        self.assertEqual(result.status, "pass", result.reason)
        self.assertEqual(result.delivery, "beta_channel")
        self.assertEqual(len(installer_calls), 1)
        self.assertEqual(installer_calls[0][1], "VRCT-VOX_setup.exe")


class UpgradeCommandTest(unittest.TestCase):
    def test_channel_command_exit_codes(self):
        same = {"version": "0.6.0", "platforms": {"windows-x86_64": {"url": "https://example.invalid/s.exe"}}}
        newer = {"version": "0.6.1-beta.1", "platforms": {"windows-x86_64": {"url": "https://example.invalid/s.exe"}}}
        with mock.patch.object(upgrade, "fetch_json", lambda url: same):
            code, out, _ = _run(["upgrade", "channel", "--channel", "beta", "--from-version", "0.6.0", "--format", "json"])
        self.assertEqual(code, output.NOT_MET)
        self.assertFalse(json.loads(out)["newer"])

        with mock.patch.object(upgrade, "fetch_json", lambda url: newer):
            code, out, _ = _run(["upgrade", "channel", "--channel", "beta", "--from-version", "0.6.0", "--format", "json"])
        self.assertEqual(code, output.OK)
        self.assertTrue(json.loads(out)["newer"])

    def test_env_var_fallback_and_steamvr_guard(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            old_zip = _make_package_zip(root / "old.zip", "0.6.0")
            beta_zip = _make_package_zip(root / "beta.zip", "0.6.1-beta.1")
            env_patch = {"SERGULATH_UPGRADE_FROM": str(old_zip), "SERGULATH_UPGRADE_TO": str(beta_zip)}
            with mock.patch.dict(os.environ, env_patch, clear=False), \
                    mock.patch.object(steamvr, "missing_processes", lambda running=None: ["vrserver.exe"]):
                code, _, err = _run([
                    "upgrade", "check", "--work-dir", str(root / "work"), "--check-overlay", "ASTAROT0",
                ])
        self.assertEqual(code, output.STEAMVR_NOT_RUNNING)
        self.assertIn("SteamVR is not running", err)

    def test_suite_maps_unpublished_upgrade_to_unsupported_not_fail(self):
        py = __import__("sys").executable.replace("\\", "\\\\")
        with tempfile.TemporaryDirectory() as root:
            case_file = Path(root) / "u.toml"
            step = (
                f'{{ run = ["{py}", "-c", "import json, sys; '
                "print(json.dumps({'source': 'upgrade', 'status': 'unsupported', 'reason': 'no beta yet'})); "
                'sys.exit(1)"], json = { source = "upgrade", ok = true }, '
                'unsupported_when = { source = "upgrade", status = "unsupported" } }'
            )
            case_file.write_text(
                f'[suite]\nname = "u"\n\n[[case]]\nid = "E26"\ngroup = "upgrade"\ntitle = "upgrade"\nsteps = [{step}]\n',
                encoding="utf-8",
            )
            results, problem = suite.run_suite(suite.load(case_file))
        self.assertEqual(problem, "")
        self.assertEqual(results[0].status, suite.UNSUPPORTED)
        self.assertEqual(results[0].reason, "no beta yet")


class InstallerSafetyTest(unittest.TestCase):
    def test_uac_error_stops_and_still_restores_registry_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            snap = root / "reg.json"
            err = OSError("elevation required")
            err.winerror = 740
            restored = []
            with mock.patch.object(upgrade, "snapshot_hkcu_registry", lambda keys, p: p.write_text("{}", encoding="utf-8")), \
                    mock.patch.object(upgrade, "restore_hkcu_registry", lambda p: restored.append(p)), \
                    mock.patch("subprocess.run", side_effect=err):
                with self.assertRaises(upgrade.UacRequiredError):
                    upgrade.apply_installer_update(root / "install", root / "setup.exe", ["Software\\Test"], snap)
            self.assertEqual(restored, [snap])

    @unittest.skipUnless(os.name == "nt", "Windows HKCU registry test")
    def test_hkcu_registry_snapshot_and_restore_round_trip(self):
        import winreg

        key_path = f"Software\\sergulath-unit-test-{os.getpid()}"
        with tempfile.TemporaryDirectory() as root:
            snap = Path(root) / "reg.json"
            try:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as k:
                    winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "C:\\Original\\ASTAROT")
                    winreg.SetValueEx(k, "Count", 0, winreg.REG_DWORD, 7)
                upgrade.snapshot_hkcu_registry([key_path], snap)
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as k:
                    winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "C:\\Sandbox\\ASTAROT")
                    winreg.SetValueEx(k, "Extra", 0, winreg.REG_SZ, "remove-me")
                upgrade.restore_hkcu_registry(snap)
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as k:
                    self.assertEqual(winreg.QueryValueEx(k, "")[0], "C:\\Original\\ASTAROT")
                    self.assertEqual(winreg.QueryValueEx(k, "Count")[0], 7)
                    with self.assertRaises(FileNotFoundError):
                        winreg.QueryValueEx(k, "Extra")
                self.assertTrue((Path(root) / "reg.restored.json").is_file())
            finally:
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
