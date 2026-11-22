# SERGULATH

**Sergulath's Emulation, Recording, Gauging & Unattended-Launch Automation Testing Harness**

Tools for operating and checking SteamVR overlay apps on a real headset (first a Steam Frame) from a Windows PC: SteamVR state and settings, the VR view, overlay pixels, device tracking and, later, virtual devices.

[日本語](docs/README.ja.md)

> Status: stage 0. `watch`, `overlay` and `profiles` are here; the other commands move in step by step (see [Plan](#plan)).

## The command line comes first

SERGULATH is a command-line tool. Every capability is a subcommand of `sergulath`, usable by hand and from scripts, CI or other agents, and commands combine through pipes and files. The Python package underneath (`sergulath.core`) prints nothing and serves the commands; it is not a separate interface.

Every command keeps these conventions:

- Data goes to stdout, messages go to stderr.
- `--format text` (the default) is for people; `--format json` prints JSON Lines, one object per record, for `jq` and other commands.
- No command prompts or waits for input.
- Exit codes: `0` done, `1` ran but found nothing, `2` usage error, `3` SteamVR is not running (the commands never start it), `4` failed.
- Files a command writes are named by an option (`--csv`, `--save`); nothing is written elsewhere.

## Use

```sh
pip install .            # Python 3.11 or later, Windows

sergulath profiles                         # the bundled app profiles and their overlay keys

# Headset and controllers once a minute for 20 minutes (21 samples): connection, pose, tracking result, headset activity
sergulath watch --csv records/watch.csv

# An app's overlays: found, shown, alpha, width; --save also writes their pixels as PNG
sergulath overlay --profile astarot --save records/overlays

# Composing: is the multi-line caption overlay shown right now?
sergulath overlay --format json | jq -e 'select(.key == "ASTAROT1") | .visible'
```

`watch`, `overlay` and `profiles` only read. `watch` and `overlay` connect to a SteamVR that already runs (`vrserver` and `vrcompositor`) as a background OpenVR client.

Records can show a room, a desktop or file names; `records/` is ignored by git.

## Why

Checking an overlay app in VR needs someone in the headset. A Steam Frame on a desk loses its tracking, goes to standby after a few minutes and takes its controllers with it, and the VR view mirror goes black. SERGULATH gathers the steps that make such checks repeatable and, where possible, unattended, so they do not live as one-off scripts in each app's repository.

The first app it serves is [ASTAROT](https://github.com/kein5/astarot). Anything app-specific (overlay keys, log paths, log lines to look for) lives in a profile, so the tools stay usable for other OpenVR overlay apps.

## Plan

Each part is a subcommand of `sergulath`. Commands that change something have a read-only form and take an explicit action word for the change.

| Command | What it does | Stage |
|---|---|---|
| `sergulath watch` | Samples headset and controller connection, pose validity, tracking result and the headset's activity level at a fixed interval | 0, done |
| `sergulath overlay` | Lists an app's overlays and saves their pixels (`IVROverlay::GetOverlayImageData`) | 0, done |
| `sergulath profiles` | Lists the bundled app profiles (per-app, per-version facts, starting with ASTAROT's) | 0, done |
| `sergulath logs` | Builds one timeline from `vrserver.txt`, `vrcompositor.txt` and the app's log | 0 |
| `sergulath steamvr status` / `start` / `wait` | Reports SteamVR (including whether `vrmonitor` runs), starts it, waits for the headset | 0 |
| `sergulath settings show` / `set` / `restore` (PROCULO) | Standby and sleep settings, with a snapshot to restore | 0 |
| `sergulath view open` / `save` | Opens the VR view and saves its image | 1 |
| `sergulath chaperone show` / `apply` / `restore` | The play area; `apply` uses the chaperone setup API's working copy for this session only | 1 |
| `sergulath driver register` / `send` / `unregister` | A virtual controller (an OpenVR driver) for unattended input, registered only for a check | 2, if stage 1 shows it is needed |

Stage 0 turns the scripts that already exist into commands. Stage 1 measures how far a real headset can be checked without a person. Stage 2 is decided from those measurements.

## Ground rules

1. Read-only by default. A change needs an explicit action word or flag.
2. Snapshot before changing any SteamVR setting, play area or registration, and restore on exit, including after a failure.
3. A virtual driver is registered only for the duration of a check, so the user's normal VR sessions never see it.
4. Never touch an app's own settings. When the tools start an app, they give it a separate settings folder.
5. No model downloads and no microphone use without the person's consent in the app's own dialog.
6. Records state the machine, the time (with the UTC offset) and the versions they were taken with. A result from one PC is not a general guarantee.

## Requirements

- Windows 10 or 11 with SteamVR
- Python 3.11 or later and [pyopenvr](https://github.com/cmbruns/pyopenvr) for the client tools
- For the virtual driver (stage 2): a C++ toolchain with MSVC; CI builds it

## License

MIT, see [LICENSE](LICENSE). pyopenvr and the OpenVR SDK keep their own license (BSD-3-Clause); see [NOTICE.md](NOTICE.md).

The name follows ASTAROT's internal code names, taken from demons said to serve Astarot ([kein5/astarot#689](https://github.com/kein5/astarot/issues/689)). Sergulath is listed in the *Grimorium Verum* under Nebiros. The name is a label only and implies nothing supernatural.
