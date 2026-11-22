# SERGULATH

**Sergulath's Emulation, Recording, Gauging & Unattended-Launch Automation Testing Harness**

Tools for operating and checking SteamVR overlay apps on a real headset (first a Steam Frame) from a Windows PC: SteamVR state and settings, the VR view, overlay pixels, device tracking and, later, virtual devices.

[日本語](docs/README.ja.md)

> Status: planning. The repository holds the plan; the tools move in step by step (see [Plan](#plan)).

## Why

Checking an overlay app in VR needs someone in the headset. A Steam Frame on a desk loses its tracking, goes to standby after a few minutes and takes its controllers with it, and the VR view mirror goes black. SERGULATH gathers the steps that make such checks repeatable and, where possible, unattended, so they do not live as one-off scripts in each app's repository.

The first app it serves is [ASTAROT](https://github.com/kein5/astarot). Anything app-specific (overlay keys, log paths, log lines to look for) lives in a profile, so the tools stay usable for other OpenVR overlay apps.

## Plan

| Part | What it does | Stage |
|---|---|---|
| `watch` | Logs headset and controller connection, pose validity, tracking result and activity level at a fixed interval | 0 |
| `overlay` | Lists an app's overlays and saves their pixels (`IVROverlay::GetOverlayImageData`) | 0 |
| `logs` | Builds one timeline from `vrserver.txt`, `vrcompositor.txt` and the app's log | 0 |
| `steamvr` | Reports and starts SteamVR, tells whether `vrmonitor` runs, waits for the headset | 0 |
| `settings` (PROCULO) | Reads and changes standby and sleep settings, with a snapshot to restore | 0 |
| `view` | Opens the VR view and saves its image | 1 |
| `chaperone` | Reads the play area and applies a temporary one through the chaperone setup API's working copy, then restores it | 1 |
| `driver` | A virtual controller (an OpenVR driver) for unattended input, registered only for a check | 2, if stage 1 shows it is needed |
| `profiles/` | Per-app, per-version facts, starting with `astarot.toml` | 0 |

Stage 0 moves in tools that already exist as scripts. Stage 1 measures how far a real headset can be checked without a person. Stage 2 is decided from those measurements.

## Ground rules

1. Read-only by default. Every command that changes something asks for an explicit flag.
2. Snapshot before changing any SteamVR setting, play area or registration, and restore on exit, including after a failure.
3. A virtual driver is registered only for the duration of a check, so the user's normal VR sessions never see it.
4. Never touch an app's own settings. When the tools start an app, they give it a separate settings folder.
5. No model downloads and no microphone use without the person's consent in the app's own dialog.
6. Records state the machine, the time (with the UTC offset) and the versions they were taken with. A result from one PC is not a general guarantee.

## Requirements

- Windows 10 or 11 with SteamVR
- Python 3.14 and [pyopenvr](https://github.com/cmbruns/pyopenvr) for the client tools
- For the virtual driver (stage 2): a C++ toolchain with MSVC; CI builds it

## License

MIT, see [LICENSE](LICENSE). The OpenVR SDK and pyopenvr keep their own licenses (BSD-3-Clause); their notices go into `NOTICE.md` once code that uses them lands.

The name follows ASTAROT's internal code names, taken from demons said to serve Astarot ([kein5/astarot#689](https://github.com/kein5/astarot/issues/689)). Sergulath is listed in the *Grimorium Verum* under Nebiros. The name is a label only and implies nothing supernatural.
