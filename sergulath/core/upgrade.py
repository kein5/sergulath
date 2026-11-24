"""Upgrade path check: install/stage an older version, update via beta channel or local package,
and verify settings, models and normal launch in an isolated folder (ground rule 4).

Never touches the user's installed app or its settings. When an NSIS installer runs against the
sandbox folder, its HKCU registry keys are snapshotted beforehand and restored in `finally`
(ground rule 2).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import overlay as overlay_core
from . import steamvr, window_capture

# Files and folders owned by user state rather than the application package.
USER_DATA_ENTRIES = frozenset({
    "config.json",
    "config.json.lock",
    "downloaded_models",
    "logs",
    "webview",
    "telemetry_state.json",
    "voice_mute_state.json",
    "secrets.json",
    "user_models.json",
    "translation_catalog.json",
    "transcription_catalog.json",
    "vc_runtimes",
    ".upd",
})

DEFAULT_MODEL_FIXTURES: dict[str, bytes] = {
    "e2e-upgrade-sentinel/rev1/model.bin": b"ASTAROT-UPGRADE-SENTINEL-MODEL-V1\n" * 64,
    "e2e-upgrade-sentinel/rev1/manifest.json": b'{"id":"e2e-upgrade-sentinel","revision":"rev1","synthetic":true}\n',
}

_SEMVER_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class UacRequiredError(RuntimeError):
    """Raised when launching an installer requires UAC elevation (WinError 740)."""


@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    def _key(self):
        # Release > prerelease. Numeric identifiers compare as ints and sort before strings.
        if not self.prerelease:
            return (self.major, self.minor, self.patch, 1, ())
        parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in self.prerelease)
        return (self.major, self.minor, self.patch, 0, parts)

    def __lt__(self, other: SemVer) -> bool:
        return self._key() < other._key()

    def __le__(self, other: SemVer) -> bool:
        return self._key() <= other._key()

    def __gt__(self, other: SemVer) -> bool:
        return self._key() > other._key()

    def __ge__(self, other: SemVer) -> bool:
        return self._key() >= other._key()

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{'.'.join(self.prerelease)}" if self.prerelease else base


def parse_semver(text: str) -> SemVer:
    match = _SEMVER_RE.match(text.strip())
    if not match:
        raise ValueError(f"not a semantic version: {text!r}")
    pre = tuple(match.group("pre").split(".")) if match.group("pre") else ()
    return SemVer(int(match.group("major")), int(match.group("minor")), int(match.group("patch")), pre)


def is_newer(candidate: str, current: str) -> bool:
    return parse_semver(candidate) > parse_semver(current)


@dataclass
class ChannelStatus:
    channel: str
    manifest_url: str
    from_version: str
    channel_version: str
    newer: bool
    download_url: str = ""
    pub_date: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_json(url: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "SERGULATH-upgrade-check"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_channel(
    manifest_url: str,
    from_version: str,
    channel: str = "beta",
    platform: str = "windows-x86_64",
    fetch=None,
) -> ChannelStatus:
    fetch = fetch or fetch_json
    manifest = fetch(manifest_url)
    version = str(manifest.get("version", "")).strip()
    if not version:
        raise ValueError(f"manifest at {manifest_url} has no version")
    entry = (manifest.get("platforms") or {}).get(platform) or {}
    return ChannelStatus(
        channel=channel,
        manifest_url=manifest_url,
        from_version=from_version,
        channel_version=version,
        newer=is_newer(version, from_version),
        download_url=str(entry.get("url", "")),
        pub_date=str(manifest.get("pub_date", "")),
        notes=str(manifest.get("notes", "")),
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sha256_text(text: str) -> str:
    token = text.strip().split()[0].lower() if text.strip() else ""
    if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError(f"invalid sha256 text: {text[:80]!r}")
    return token


def download_verified(
    url: str,
    dest: Path,
    sha256_url: str | None = None,
    expected_sha256: str | None = None,
    timeout: float = 180.0,
) -> Path:
    """Download `url` to `dest` and verify SHA-256 (reusing a cached `dest` whose hash matches)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if expected_sha256 is None and sha256_url:
        req = urllib.request.Request(sha256_url, headers={"User-Agent": "SERGULATH-upgrade-check"})
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            expected_sha256 = parse_sha256_text(resp.read().decode("utf-8"))
    if dest.is_file() and expected_sha256 and sha256_file(dest) == expected_sha256.lower():
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "SERGULATH-upgrade-check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out, length=1024 * 1024)
        if expected_sha256:
            actual = sha256_file(tmp)
            if actual != expected_sha256.lower():
                raise ValueError(f"SHA-256 mismatch for {dest.name}: got {actual}, expected {expected_sha256.lower()}")
        tmp.replace(dest)
        return dest
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def read_exe_version(exe: Path) -> str | None:
    """Read ProductVersion (falling back to FileVersion) from a Windows PE executable."""
    if sys.platform != "win32" or not exe.is_file():
        return None
    version_dll = ctypes.WinDLL("version", use_last_error=True)
    version_dll.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    version_dll.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    version_dll.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    version_dll.GetFileVersionInfoW.restype = ctypes.c_int
    version_dll.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
    version_dll.VerQueryValueW.restype = ctypes.c_int

    handle = ctypes.c_uint32(0)
    size = version_dll.GetFileVersionInfoSizeW(str(exe), ctypes.byref(handle))
    if not size:
        return None
    buf = (ctypes.c_ubyte * size)()
    if not version_dll.GetFileVersionInfoW(str(exe), 0, size, buf):
        return None

    # Query translation table for language/codepage pairs.
    ptr = ctypes.c_void_p()
    length = ctypes.c_uint32(0)
    langs: list[tuple[int, int]] = []
    if version_dll.VerQueryValueW(buf, r"\VarFileInfo\Translation", ctypes.byref(ptr), ctypes.byref(length)) and length.value >= 4:
        raw = ctypes.string_at(ptr.value, length.value)
        for offset in range(0, len(raw) - 3, 4):
            lang = int.from_bytes(raw[offset:offset + 2], "little")
            cp = int.from_bytes(raw[offset + 2:offset + 4], "little")
            langs.append((lang, cp))
    langs.extend([(0x0409, 0x04B0), (0x0409, 0x04E4), (0x0000, 0x04B0)])

    for field_name in ("ProductVersion", "FileVersion"):
        for lang, cp in langs:
            subblock = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\{field_name}"
            val_ptr = ctypes.c_void_p()
            val_len = ctypes.c_uint32(0)
            if version_dll.VerQueryValueW(buf, subblock, ctypes.byref(val_ptr), ctypes.byref(val_len)) and val_len.value:
                text = ctypes.wstring_at(val_ptr.value).strip()
                if text:
                    return text
    return None


def snapshot_hkcu_registry(keys: list[str], snapshot_path: Path) -> dict:
    """Snapshot HKCU registry keys before running an NSIS installer (ground rule 2)."""
    record: dict = {"taken": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "keys": {}}
    if sys.platform == "win32":
        import winreg

        for key_path in keys:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as k:
                    values = []
                    idx = 0
                    while True:
                        try:
                            name, value, reg_type = winreg.EnumValue(k, idx)
                            if isinstance(value, bytes):
                                value = value.hex()
                                encoded = "hex"
                            else:
                                encoded = "plain"
                            values.append({"name": name, "value": value, "type": reg_type, "encoding": encoded})
                            idx += 1
                        except OSError:
                            break
                    record["keys"][key_path] = {"exists": True, "values": values}
            except FileNotFoundError:
                record["keys"][key_path] = {"exists": False, "values": []}
    else:
        for key_path in keys:
            record["keys"][key_path] = {"exists": False, "values": []}
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def restore_hkcu_registry(snapshot_path: Path) -> None:
    """Restore HKCU registry keys from a snapshot and mark it restored."""
    if not snapshot_path.is_file():
        return
    record = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if sys.platform == "win32":
        import winreg

        for key_path, entry in record.get("keys", {}).items():
            if not entry.get("exists"):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
                except OSError:
                    pass
                continue
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as k:
                # Remove any values not in the snapshot.
                keep = {item["name"] for item in entry.get("values", [])}
                existing = []
                idx = 0
                while True:
                    try:
                        name, _, _ = winreg.EnumValue(k, idx)
                        existing.append(name)
                        idx += 1
                    except OSError:
                        break
                for name in existing:
                    if name not in keep:
                        try:
                            winreg.DeleteValue(k, name)
                        except OSError:
                            pass
                for item in entry.get("values", []):
                    val = bytes.fromhex(item["value"]) if item.get("encoding") == "hex" else item["value"]
                    winreg.SetValueEx(k, item["name"], 0, int(item["type"]), val)
    snapshot_path.replace(snapshot_path.with_name(snapshot_path.stem + ".restored.json"))


def extract_zip_safe(archive: Path, dest: Path) -> Path:
    """Extract a ZIP archive into `dest`, rejecting path traversal (zip-slip)."""
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "r") as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise ValueError(f"zip entry escapes target folder: {info.filename!r}")
        zf.extractall(dest)
    return dest


def _exe_names(exe_name: str | Sequence[str]) -> list[str]:
    """One executable name or several (the current name first, then older names)."""
    return [exe_name] if isinstance(exe_name, str) else list(exe_name)


def find_exe(folder: Path, exe_name: str | Sequence[str]) -> Path | None:
    """The first of the names that exists as a file in `folder` (ASTAROT 0.7 renamed vrct-vox.exe)."""
    for name in _exe_names(exe_name):
        if (folder / name).is_file():
            return folder / name
    return None


def _resolve_payload_root(folder: Path, exe_name: str | Sequence[str]) -> Path:
    """Return `folder` if an executable is directly inside it, or its single wrapper subdirectory."""
    if find_exe(folder, exe_name):
        return folder
    entries = [p for p in folder.iterdir() if p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir() and find_exe(entries[0], exe_name):
        return entries[0]
    raise ValueError(f"package does not contain {' or '.join(_exe_names(exe_name))}: {folder}")


def _move_entry(src: Path, dst: Path) -> None:
    if dst.exists():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.move(str(src), str(dst))


def apply_package_update(install_dir: Path, source: Path, exe_name: str | Sequence[str]) -> str:
    """Stage and apply a ZIP archive or unpacked directory into `install_dir`, keeping user data.

    Mirrors ASTAROT's NSIS commit staging (`install_dir/.upd/new`, `.upd/old`): rolls back any
    replaced entries if a move fails, and never overwrites or removes `USER_DATA_ENTRIES`.
    Returns the delivery kind (`local_zip` or `local_dir`).
    """
    install_dir = install_dir.resolve()
    install_dir.mkdir(parents=True, exist_ok=True)
    upd_root = install_dir / ".upd"
    if upd_root.exists():
        shutil.rmtree(upd_root)
    staged = upd_root / "new"
    backup = upd_root / "old"
    staged.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=True)

    if source.is_file() and source.suffix.lower() == ".zip":
        extract_zip_safe(source, staged)
        payload = _resolve_payload_root(staged, exe_name)
        delivery = "local_zip"
    elif source.is_dir():
        src_root = _resolve_payload_root(source, exe_name)
        for item in src_root.iterdir():
            if item.name in USER_DATA_ENTRIES:
                continue
            dst = staged / item.name
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
        payload = staged
        delivery = "local_dir"
    else:
        raise ValueError(f"unsupported package source: {source}")

    entries = [p for p in payload.iterdir() if p.name not in USER_DATA_ENTRIES]
    payload_names = {p.name.lower() for p in entries}
    known_exes = _exe_names(exe_name)
    # Remove any older executable name left behind when the update renames the binary (0.6 -> 0.7).
    stale_exes = [name for name in known_exes if name.lower() not in payload_names and (install_dir / name).is_file()]
    replaced: list[str] = []
    added: list[str] = []
    try:
        for item in entries:
            target = install_dir / item.name
            if target.exists():
                _move_entry(target, backup / item.name)
                replaced.append(item.name)
            else:
                added.append(item.name)
            _move_entry(item, target)
        for old_exe in stale_exes:
            _move_entry(install_dir / old_exe, backup / old_exe)
            replaced.append(old_exe)
    except Exception:
        # Roll back to the prior files.
        for name in reversed(added):
            p = install_dir / name
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink(missing_ok=True)
        for name in reversed(replaced):
            old = backup / name
            if old.exists():
                _move_entry(old, install_dir / name)
        raise
    finally:
        if upd_root.exists():
            shutil.rmtree(upd_root, ignore_errors=True)
    return delivery


def apply_installer_update(
    install_dir: Path,
    installer_exe: Path,
    registry_keys: list[str],
    snapshot_path: Path,
    timeout: float = 600.0,
) -> None:
    """Run an NSIS installer silently into `install_dir` with HKCU registry snapshot and restore."""
    install_dir = install_dir.resolve()
    snapshot_hkcu_registry(registry_keys, snapshot_path)
    try:
        # NSIS requires /D=<dir> to be the final argument and unquoted.
        cmd = f'"{installer_exe}" /S /UPDATE /NS /D={install_dir}'
        try:
            done = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True, encoding="utf-8", errors="replace")
        except OSError as e:
            if getattr(e, "winerror", None) == 740:
                raise UacRequiredError("installer requested UAC elevation; stopped without elevating") from e
            raise
        if done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip()[-300:]
            raise RuntimeError(f"installer exited with {done.returncode}" + (f": {detail}" if detail else ""))
    finally:
        restore_hkcu_registry(snapshot_path)


def seed_state(
    install_dir: Path,
    base_settings: dict,
    sentinel_settings: dict,
    models_dir_name: str = "downloaded_models",
    model_fixtures: dict[str, bytes] | None = None,
) -> dict[str, str]:
    """Write `config.json` and synthetic model files into `install_dir`; return model SHA-256 map."""
    install_dir.mkdir(parents=True, exist_ok=True)
    merged = {**base_settings, **sentinel_settings}
    (install_dir / "config.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    fixtures = model_fixtures if model_fixtures is not None else DEFAULT_MODEL_FIXTURES
    models_root = install_dir / models_dir_name
    hashes: dict[str, str] = {}
    for rel, content in fixtures.items():
        path = models_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        hashes[rel] = hashlib.sha256(content).hexdigest()
    return hashes


def verify_settings(install_dir: Path, expected: dict) -> tuple[bool, list[str]]:
    config_path = install_dir / "config.json"
    if not config_path.is_file():
        return False, ["config.json is missing"]
    try:
        actual = json.loads(config_path.read_text(encoding="utf-8"))
    except ValueError as e:
        return False, [f"config.json is not valid JSON: {e}"]
    problems = []
    for key, want in expected.items():
        if key not in actual:
            problems.append(f"setting {key} is missing")
        elif actual[key] != want:
            problems.append(f"setting {key}={actual[key]!r}, expected {want!r}")
    return not problems, problems


def verify_models(install_dir: Path, models_dir_name: str, expected_hashes: dict[str, str]) -> tuple[bool, list[str]]:
    models_root = install_dir / models_dir_name
    problems = []
    for rel, want in expected_hashes.items():
        path = models_root / rel
        if not path.is_file():
            problems.append(f"model file {rel} is missing")
            continue
        actual = sha256_file(path)
        if actual != want:
            problems.append(f"model file {rel} changed (sha256 {actual[:12]} != {want[:12]})")
    return not problems, problems


def stop_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def boot_and_verify(
    exe: Path,
    settings_dir: Path,
    window_title: str,
    boot_wait_s: float = 10.0,
    boot_timeout_s: float = 30.0,
    check_window: bool = True,
    overlay_key: str | None = None,
) -> tuple[bool, bool, str]:
    """Launch `exe` with isolated settings, check it stays alive (and window/overlay), then stop it.

    Returns `(launched_ok, overlay_ok, reason)`.
    """
    env = dict(os.environ)
    resolved = str(settings_dir.resolve())
    env.update(
        ASTAROT_CONFIG_DIR=resolved,
        VRCT_CONFIG_DIR=resolved,
        WEBVIEW2_USER_DATA_FOLDER=str((settings_dir / "webview").resolve()),
    )
    proc = subprocess.Popen(
        [str(exe)],
        cwd=str(exe.parent),
        env=env,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    try:
        deadline = time.monotonic() + boot_timeout_s
        window_seen = not check_window or sys.platform != "win32"
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False, False, f"process exited early with code {proc.returncode}"
            if not window_seen:
                if any(w.pid == proc.pid and w.title == window_title and w.visible for w in window_capture.windows()):
                    window_seen = True
                    break
            else:
                break
            time.sleep(0.3)

        if proc.poll() is not None:
            return False, False, f"process exited early with code {proc.returncode}"
        if not window_seen:
            return False, False, f"main window '{window_title}' did not appear within {boot_timeout_s:.0f} s"

        # Let the app settle and finish start-up/config persistence.
        settle_until = time.monotonic() + boot_wait_s
        while time.monotonic() < settle_until:
            if proc.poll() is not None:
                return False, False, f"process exited during startup with code {proc.returncode}"
            time.sleep(0.3)

        overlay_ok = True
        if overlay_key:
            overlay_ok = False
            ov_deadline = time.monotonic() + 15.0
            with steamvr.background_client() as openvr:
                while time.monotonic() < ov_deadline:
                    if proc.poll() is not None:
                        return False, False, f"process exited while waiting for overlay {overlay_key}"
                    state = overlay_core.capture(openvr, [overlay_key], read_pixels=False)[0]
                    if state.found:
                        overlay_ok = True
                        break
                    time.sleep(0.5)
            if not overlay_ok:
                return True, False, f"overlay {overlay_key} did not appear after launch"
        return True, overlay_ok, ""
    finally:
        stop_process_tree(proc)


@dataclass
class UpgradeResult:
    status: str  # "pass", "fail", "unsupported"
    ok: bool
    from_version: str = ""
    to_version: str = ""
    delivery: str = ""
    channel: str = ""
    work_dir: str = ""
    settings_preserved: bool = False
    models_preserved: bool = False
    launched_ok: bool = False
    overlay_ok: bool = True
    reason: str = ""
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _github_release_asset_url(repository: str, version: str, asset_name: str) -> str:
    tag = version if version.startswith("v") else f"v{version}"
    return f"https://github.com/{repository}/releases/download/{tag}/{asset_name}"


def run_upgrade_check(
    profile: dict,
    work_dir: Path,
    *,
    from_path: Path | None = None,
    from_version: str = "0.6.0",
    to_path: Path | None = None,
    channel: str = "beta",
    method: str = "auto",
    cache_dir: Path | None = None,
    expect_version: str | None = None,
    allow_same_version: bool = False,
    boot_wait_s: float = 10.0,
    boot_timeout_s: float = 30.0,
    check_window: bool = True,
    overlay_key: str | None = None,
    skip_old_boot: bool = False,
    fetch=None,
    download=None,
    version_reader=None,
    boot_runner=None,
    installer_runner=None,
    on_stage=None,
) -> UpgradeResult:
    """Run the isolated upgrade check end-to-end."""
    fetch = fetch or fetch_json
    download = download or download_verified
    version_reader = version_reader or read_exe_version
    boot_runner = boot_runner or boot_and_verify
    installer_runner = installer_runner or apply_installer_update
    app_info = profile["app"]
    up_info = profile.get("upgrade", {})
    exe_name = app_info["process"]
    # Names an older version used (0.6 and earlier: vrct-vox.exe); the current name comes first.
    exe_names = [exe_name, *app_info.get("legacy_process", [])]
    window_title = app_info["main_window_title"]
    repository = up_info.get("repository", "kein5/astarot")
    package_asset = up_info.get("package_asset", "ASTAROT.zip")
    base_package_assets = [package_asset, *up_info.get("legacy_package_assets", [])]
    models_dir_name = up_info.get("models_dir", "downloaded_models")
    registry_keys = list(up_info.get("registry_keys", []))
    base_settings = dict(app_info.get("check_settings", {}))
    sentinel_settings = dict(up_info.get("sentinel_settings", {
        "SELECTED_RELEASE_CHANNEL": channel,
        "RELEASE_CHANNEL_CHOSEN": True,
        "TRANSPARENCY": 87,
        "MIC_THRESHOLD": 456,
    }))
    manifest_url = up_info.get(f"{channel}_manifest", "")
    work_dir = work_dir.resolve()
    install_dir = work_dir / "install"
    cache = (cache_dir or (work_dir / "cache")).resolve()

    def report_stage(stage: str, **info) -> None:
        if on_stage:
            on_stage(stage, info)

    # 1. Resolve target (local package or live update channel).
    target_source: Path | None = to_path.resolve() if to_path else None
    channel_status: ChannelStatus | None = None
    delivery = ""
    if target_source is not None:
        if not target_source.exists():
            return UpgradeResult(
                status="fail", ok=False, from_version=from_version, channel=channel,
                work_dir=str(work_dir), reason=f"local target package does not exist: {target_source}",
            )
        delivery = "local_installer" if target_source.suffix.lower() == ".exe" else (
            "local_zip" if target_source.is_file() else "local_dir"
        )
        report_stage("resolve", delivery=delivery, target=str(target_source))
    else:
        if not manifest_url:
            return UpgradeResult(
                status="unsupported", ok=False, from_version=from_version, channel=channel,
                work_dir=str(work_dir), reason=f"no {channel}_manifest configured in profile",
            )
        try:
            channel_status = check_channel(manifest_url, from_version, channel=channel, fetch=fetch)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            return UpgradeResult(
                status="fail", ok=False, from_version=from_version, channel=channel,
                work_dir=str(work_dir), reason=f"could not read {channel} channel manifest ({manifest_url}): {e}",
            )
        report_stage("resolve", delivery=f"{channel}_channel", channel_version=channel_status.channel_version,
                     newer=channel_status.newer)
        if not channel_status.newer:
            return UpgradeResult(
                status="unsupported",
                ok=False,
                from_version=from_version,
                to_version=channel_status.channel_version,
                delivery=f"{channel}_channel",
                channel=channel,
                work_dir=str(work_dir),
                reason=(
                    f"no release newer than {from_version} is published on the {channel} channel "
                    f"(latest is {channel_status.channel_version}); pass --to PATH or set SERGULATH_UPGRADE_TO "
                    "to run with a local package"
                ),
            )
        delivery = f"{channel}_channel"
        expect_version = expect_version or channel_status.channel_version

    # Fail fast before any download if an OpenVR overlay check was requested while SteamVR is down.
    if overlay_key and boot_runner is boot_and_verify:
        missing = steamvr.missing_processes()
        if missing:
            raise steamvr.SteamVRNotRunning("SteamVR is not running (missing: " + ", ".join(missing) + ")")

    # 2. Resolve and stage the old version (0.6.0) into a fresh sandbox folder.
    if install_dir.exists():
        shutil.rmtree(install_dir)
    install_dir.mkdir(parents=True, exist_ok=True)

    if from_path is not None:
        source_old = from_path.resolve()
        if not source_old.exists():
            return UpgradeResult(
                status="fail", ok=False, from_version=from_version, delivery=delivery, channel=channel,
                work_dir=str(work_dir), reason=f"old package does not exist: {source_old}",
            )
    else:
        # The asset is named after the app at that version, so try the current name, then older ones.
        source_old = None
        download_error: Exception | None = None
        for asset in base_package_assets:
            old_url = _github_release_asset_url(repository, from_version, asset)
            old_dest = cache / f"v{from_version}" / asset
            try:
                source_old = download(old_url, old_dest, sha256_url=old_url + ".sha256")
                break
            except Exception as e:
                download_error = e
        if source_old is None:
            return UpgradeResult(
                status="fail", ok=False, from_version=from_version, delivery=delivery, channel=channel,
                work_dir=str(work_dir), reason=f"could not download base version {from_version}: {download_error}",
            )

    if source_old.suffix.lower() == ".exe":
        installer_runner(install_dir, source_old, registry_keys, work_dir / "registry-old.json")
    else:
        apply_package_update(install_dir, source_old, exe_names)

    exe_path = find_exe(install_dir, exe_names)
    if exe_path is None:
        return UpgradeResult(
            status="fail", ok=False, from_version=from_version, delivery=delivery, channel=channel,
            work_dir=str(work_dir), reason=f"{exe_name} not found after staging {from_version}",
        )
    actual_old = version_reader(exe_path) or from_version
    report_stage("stage_old", version=actual_old, path=str(install_dir))

    # 3. Seed settings and synthetic model files, then optionally boot old version once.
    model_hashes = seed_state(install_dir, base_settings, sentinel_settings, models_dir_name=models_dir_name)
    if not skip_old_boot:
        old_ok, _, old_reason = boot_runner(
            exe_path, install_dir, window_title, boot_wait_s=min(boot_wait_s, 8.0),
            boot_timeout_s=boot_timeout_s, check_window=check_window, overlay_key=None,
        )
        report_stage("boot_old", ok=old_ok, reason=old_reason)
        if not old_ok:
            return UpgradeResult(
                status="fail", ok=False, from_version=actual_old, delivery=delivery, channel=channel,
                work_dir=str(work_dir), reason=f"old version ({actual_old}) failed to start: {old_reason}",
            )

    # 4. Apply the update (beta channel or local package).
    use_installer = (
        method == "installer"
        or (method == "auto" and target_source is not None and target_source.suffix.lower() == ".exe")
        or (method == "auto" and target_source is None and channel_status is not None and channel_status.download_url.lower().endswith(".exe"))
    )
    try:
        if target_source is None:
            assert channel_status is not None
            if use_installer and channel_status.download_url:
                dl_url = channel_status.download_url
                dl_name = dl_url.rsplit("/", 1)[-1]
                target_source = download(dl_url, cache / f"v{channel_status.channel_version}" / dl_name, sha256_url=dl_url + ".sha256")
            else:
                target_error: Exception | None = None
                for asset in base_package_assets:
                    dl_url = _github_release_asset_url(repository, channel_status.channel_version, asset)
                    try:
                        target_source = download(dl_url, cache / f"v{channel_status.channel_version}" / asset, sha256_url=dl_url + ".sha256")
                        break
                    except Exception as e:
                        target_error = e
                if target_source is None:
                    raise target_error or RuntimeError(f"could not download package for {channel_status.channel_version}")
        if use_installer:
            installer_runner(install_dir, target_source, registry_keys, work_dir / "registry-update.json")
        else:
            apply_package_update(install_dir, target_source, exe_names)
    except UacRequiredError as e:
        return UpgradeResult(
            status="fail", ok=False, from_version=actual_old, delivery=delivery, channel=channel,
            work_dir=str(work_dir), reason=str(e),
        )
    except Exception as e:
        return UpgradeResult(
            status="fail", ok=False, from_version=actual_old, delivery=delivery, channel=channel,
            work_dir=str(work_dir), reason=f"update failed: {e}",
        )

    # The update may have renamed the executable (0.7: vrct-vox.exe to astarot.exe).
    exe_path = find_exe(install_dir, exe_names) or exe_path
    actual_new = version_reader(exe_path) or (expect_version or "")
    report_stage("apply", version=actual_new, method="installer" if use_installer else "package")

    # 5. Pre-launch checks: version, settings, models.
    problems: list[str] = []
    if expect_version and actual_new and actual_new != expect_version:
        problems.append(f"updated version is {actual_new}, expected {expect_version}")
    elif not allow_same_version and actual_new and actual_old and actual_new == actual_old:
        problems.append(f"version stayed at {actual_new} (pass --allow-same-version to permit same-version checks)")

    pre_settings_ok, pre_settings_probs = verify_settings(install_dir, sentinel_settings)
    pre_models_ok, pre_models_probs = verify_models(install_dir, models_dir_name, model_hashes)
    problems.extend(f"before launch: {p}" for p in pre_settings_probs + pre_models_probs)

    # 6. Normal launch check on the updated binary.
    launched_ok, overlay_ok, boot_reason = boot_runner(
        exe_path, install_dir, window_title, boot_wait_s=boot_wait_s,
        boot_timeout_s=boot_timeout_s, check_window=check_window, overlay_key=overlay_key,
    )
    report_stage("boot_new", ok=launched_ok, overlay_ok=overlay_ok, reason=boot_reason)
    if not launched_ok:
        problems.append(f"updated version did not start cleanly: {boot_reason}")
    elif not overlay_ok:
        problems.append(boot_reason)

    # 7. Post-launch checks: settings and models survived startup/migrations.
    post_settings_ok, post_settings_probs = verify_settings(install_dir, sentinel_settings)
    post_models_ok, post_models_probs = verify_models(install_dir, models_dir_name, model_hashes)
    problems.extend(f"after launch: {p}" for p in post_settings_probs + post_models_probs)

    settings_preserved = pre_settings_ok and post_settings_ok
    models_preserved = pre_models_ok and post_models_ok
    ok = not problems and settings_preserved and models_preserved and launched_ok and overlay_ok
    report_stage("verify", ok=ok, settings_preserved=settings_preserved, models_preserved=models_preserved)

    return UpgradeResult(
        status="pass" if ok else "fail",
        ok=ok,
        from_version=actual_old,
        to_version=actual_new,
        delivery=delivery,
        channel=channel,
        work_dir=str(work_dir),
        settings_preserved=settings_preserved,
        models_preserved=models_preserved,
        launched_ok=launched_ok,
        overlay_ok=overlay_ok,
        reason="; ".join(problems),
        details=problems,
    )
