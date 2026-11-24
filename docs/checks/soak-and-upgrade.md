# Long-running soak (E25) and upgrade path (E26) checks

Two release-readiness checks added to `suites/astarot-e2e.toml` (cases `E25` and `E26`), each backed
by its own CLI subcommand (`sergulath soak` and `sergulath upgrade`).

## Long-running soak (`sergulath soak`, E25)

`sergulath soak` is read-only. It watches a running app's process tree (main process plus children
such as WebView2) and, with `--overlay`, its OpenVR overlay state at a fixed interval.

```sh
# 30-minute check (31 samples, 60 s apart), recording every sample to CSV:
sergulath caption sample start
sergulath soak --duration 1800 --interval 60 --warmup 120 \
    --max-growth-mb 100 --max-slope-mb-per-hour 120 \
    --overlay ASTAROT0 --min-alpha 0.01 \
    --csv records/soak.csv
sergulath caption sample stop

# Or through the E2E suite (phase `soak` starts and always stops the caption sample):
sergulath suite run --cases suites/astarot-e2e.toml --only E25 --slow --record records/suite/e25
```

What it checks:

1. **Process liveness (`alive`)**: the initial PID stays alive across every sample (a crash or
   restart ends the run immediately with `alive = false`).
2. **Bounded memory (`memory_ok`)**: reads process-tree private bytes (`PrivateUsage`) and working
   set (`WorkingSetSize`) via Windows `K32GetProcessMemoryInfo`. Samples before `--warmup`
   (default 120 s) are excluded from the baseline. Fails if post-warmup net growth exceeds
   `--max-growth-mb` (default 100 MiB), or if the linear trend exceeds `--max-slope-mb-per-hour`
   (default 120 MiB/h) alongside at least 16 MiB of net growth.
3. **Caption overlay continuity (`overlay_ok`)**: every `--overlay` key stays `found = true`,
   `visible = true`, and `alpha >= --min-alpha` in every sample. Pixel reads are omitted so the
   check works both on GPU-texture builds and while a real headset sits in standby.

Exit codes follow SERGULATH's convention: `0` passed, `1` app not running or check failed, `2`
usage error, `3` SteamVR not running (when `--overlay` is used), `4` failed.

## Upgrade path (`sergulath upgrade`, E26)

`sergulath upgrade` checks upgrading from an older version (0.6.0) to a beta release (or a local
distribution file while no beta is published yet) inside an isolated sandbox folder, without
touching the user's installed ASTAROT or settings (ground rule 4).

```sh
# Read-only: check whether the beta channel publishes a version newer than 0.6.0 (exit 0 newer, 1 not yet)
sergulath upgrade channel --channel beta --from-version 0.6.0

# Run with the live beta channel once a beta > 0.6.0 is published:
sergulath upgrade check --from-version 0.6.0 --channel beta \
    --work-dir records/suite/E26 --cache-dir records/suite/cache --check-overlay ASTAROT0

# Before a beta is published, substitute a local package (ASTAROT.zip, unpacked folder or ASTAROT_setup.exe):
sergulath upgrade check --from-version 0.6.0 --to path/to/ASTAROT.zip \
    --work-dir records/suite/E26 --cache-dir records/suite/cache --check-overlay ASTAROT0

# Or through the E2E suite via environment variables:
SERGULATH_UPGRADE_TO=path/to/ASTAROT.zip \
    sergulath suite run --cases suites/astarot-e2e.toml --only E26 --record records/suite/e26
```

What `upgrade check` does:

1. **Resolve target**: uses `--to` (or `SERGULATH_UPGRADE_TO`) when given; otherwise queries the
   profile's `beta_manifest` (`https://vrct-vox-updates.kei9d09.workers.dev/beta/latest.json`).
   When the channel is still at or below `--from-version` and no local file is given, it writes
   nothing, emits `{"source": "upgrade", "status": "unsupported", ...}` and exits `1`; the suite
   runner maps that to `UNSUPPORTED` (not `FAIL`).
2. **Stage base version (0.6.0)**: unpacks `--from` (`SERGULATH_UPGRADE_FROM`), or downloads and
   SHA-256-verifies `v0.6.0`'s `VRCT-VOX.zip` into `--cache-dir`, into `<work-dir>/install`.
   ASTAROT 0.7.0 renamed the executable (`vrct-vox.exe` to `astarot.exe`) and the assets
   (`ASTAROT.zip`, `ASTAROT_setup.exe`); the profile lists the new names first and the old ones as
   `legacy_*`, so an update across the rename finds the right file on both sides.
3. **Seed state**: writes `config.json` from the profile's `[app.check_settings]` plus non-default
   sentinel values (`SELECTED_RELEASE_CHANNEL = "beta"`, `RELEASE_CHANNEL_CHOSEN = true`,
   `TRANSPARENCY = 87`, `MIC_THRESHOLD = 456`) and creates synthetic sentinel model files under
   `downloaded_models/` (ground rule 5: no model downloads without user consent). Boots 0.6.0
   once with `ASTAROT_CONFIG_DIR` / `VRCT_CONFIG_DIR` pointed at the sandbox and stops it.
4. **Apply update**:
   - For a `.zip` or unpacked folder: stages into `.upd/new`, swaps only app-owned entries with
     rollback on error, and leaves user data (`config.json`, `downloaded_models/`, `logs/`, etc.)
     untouched.
   - For an NSIS `ASTAROT_setup.exe` (or beta channel installer): snapshots the installer's HKCU
     registry keys (`Software\kein5\ASTAROT`, `Software\kein5\VRCT-VOX` and the matching
     `Software\Microsoft\Windows\CurrentVersion\Uninstall\` keys), runs
     `setup.exe /S /UPDATE /NS /D=<sandbox>` (silent, no shortcuts, no extra offers), aborts if
     UAC elevation is required, and always restores the HKCU registry keys in `finally` (ground
     rule 2).
5. **Verify**: confirms the executable's PE version updated, all sentinel settings and synthetic
   model SHA-256 hashes survived before launch, launches the updated app normally (checking
   liveness, main window, and `--check-overlay ASTAROT0`), stops it, and re-verifies settings and
   models after launch.

### Note on in-app updater UI vs sandboxed update

ASTAROT's in-app updater (`tauri-plugin-updater`) compiles its manifest URLs and minisign public
key into `astarot.exe` (`vrct-vox.exe` up to 0.6.x) and launches `ASTAROT_setup.exe /P /UPDATE /R`
(`VRCT-VOX_setup.exe` up to 0.6.x) without `/D=`, which reads `$INSTDIR` from
`HKCU\Software\kein5\ASTAROT` (or `HKCU\Software\kein5\VRCT-VOX` up to 0.6.x). Because ASTAROT
itself is kept read-only and local unpublished packages are unsigned, `upgrade check` exercises the
beta channel manifest, signed/SHA-256-verified package download, NSIS `/S /UPDATE /NS /D=<sandbox>`
(or staged ZIP replacement for local builds), data preservation and post-update startup in isolation
instead of triggering the in-app updater against the user's real installation.
