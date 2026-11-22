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

sergulath steamvr status                   # SteamVR running? status window? version, headset and controllers
sergulath profiles                         # the bundled app profiles and their overlay keys

# Headset and controllers once a minute for 20 minutes (21 samples): connection, pose, tracking result, headset activity
sergulath watch --csv records/watch.csv

# The same, plus whether a Steam Frame is showing its own screens (over SSH; e.g. the outside-the-play-space camera view)
sergulath watch --frame

# An app's overlays: found, shown, alpha, width; --save also writes their pixels as PNG
sergulath overlay --profile astarot --save records/overlays
# --placement: what each overlay follows (headset, which hand, the room) and where that puts it
sergulath overlay --placement
# A wrist launcher over time: does it attach to the hand and fade in while the headset is worn?
sergulath watch --count 120 --interval 1 --overlay ASTAROT3

# Composing: is the multi-line caption overlay shown right now?
sergulath overlay --format json | jq -e 'select(.key == "ASTAROT1") | .visible'
```

### Checking a caption without anyone at the PC

The app must run and SteamVR must run. Nothing needs to be opened by hand, even when the app sits in the tray.

```sh
sergulath caption sample start              # bring up the main window, open the captions page, start the app's sample
sergulath caption check --view records/caption.png   # exit 0 when the one-line caption overlay is shown and drawn
sergulath caption sample start --line multi # the same for the multi-line caption (it hangs on the left hand)
sergulath caption check --line multi
sergulath caption sample stop               # stop the sample and leave the settings page
```

How it works: a second start of the app's executable hands over to the running app, which shows its main window (the app stays single). The buttons are then pressed through Windows UI Automation by their accessible names, which the profile lists per UI language (`[ui.ja]` for ASTAROT). The sample is the app's own: it sends a caption once a second while the settings page stays open, and changes no setting. `caption check` reads the caption overlay: shown, not transparent (a caption fades out a few seconds after the last line) and drawn. The VR View shows the caption only while the headset tracks; a Steam Frame left on a desk loses tracking and the view goes black, which `--view` records as blank.

`watch`, `overlay`, `profiles` and `caption check` only read; `caption sample` presses the app's own sample button. `watch` and `overlay` connect to a SteamVR that already runs (`vrserver` and `vrcompositor`) as a background OpenVR client.

Records can show a room, a desktop or file names; `records/` is ignored by git.

## Why

Checking an overlay app in VR needs someone in the headset. A Steam Frame on a desk loses its tracking, goes to standby after a few minutes and takes its controllers with it, and the VR view mirror goes black. SERGULATH gathers the steps that make such checks repeatable and, where possible, unattended, so they do not live as one-off scripts in each app's repository.

The first app it serves is [ASTAROT](https://github.com/kein5/astarot). Anything app-specific (overlay keys, log paths, log lines to look for) lives in a profile, so the tools stay usable for other OpenVR overlay apps.

## Plan

Each part is a subcommand of `sergulath`. Commands that change something have a read-only form and take an explicit action word for the change.

| Command | What it does | Stage |
|---|---|---|
| `sergulath watch` | Samples headset and controller connection, pose validity, tracking result and the headset's activity level at a fixed interval | 0, done |
| `sergulath overlay` | Lists an app's overlays and saves their pixels (`IVROverlay::GetOverlayImageData`); `--placement` reads what each follows and where it sits; `watch --overlay` samples them over time | 0, done |
| `sergulath profiles` | Lists the bundled app profiles (per-app, per-version facts, starting with ASTAROT's) | 0, done |
| `sergulath logs` | Builds one timeline from `vrserver.txt`, `vrcompositor.txt` and the app's log | 0 |
| `sergulath steamvr status` | Reports SteamVR: running or not, whether `vrmonitor` runs, its version, the headset's and controllers' state | 0, done |
| `sergulath steamvr start` / `wait` | Starts SteamVR, waits for the headset | 0 |
| `sergulath settings show` / `set` / `restore` (PROCULO) | Standby and sleep settings, with a snapshot to restore | 0 |
| `sergulath view save` / `screenshot` | Saves what the headset shows: the VR View window (scene and overlays) or a SteamVR screenshot (scene only). See [What the PC can see](#what-the-pc-can-see) | 0, prototype |
| `sergulath caption sample start` / `stop`, `sergulath caption check` | Sends the app's sample caption from the session side, and checks that the caption overlay renders | 0, done |
| `sergulath frame overlays` | Reads a standalone headset's own system overlays over SSH (state, and images where readable) | 1, prototype |
| `sergulath view open` | Opens the VR View | 1 |
| `sergulath chaperone show` / `apply` / `restore` | The play area; `apply` uses the chaperone setup API's working copy for this session only | 1 |
| `sergulath driver register` / `send` / `unregister` | A virtual controller (an OpenVR driver) for unattended input, registered only for a check | 2, if stage 1 shows it is needed |

Stage 0 turns the scripts that already exist into commands. Stage 1 measures how far a real headset can be checked without a person. Stage 2 is decided from those measurements.

## What the PC can see

Measured on 2026-11-22 with a Steam Frame over Steam Link, SteamVR 2.19.4 and VRChat, by showing a checkerboard test overlay in front of the headset:

| Route | Scene app | Overlays (captions, test overlay) | The headset's own screens (Steam Frame "outside the play area" camera screen) |
|---|---|---|---|
| VR View window (`sergulath view save`) | yes | yes | no: the view went black while the headset showed that screen |
| SteamVR screenshot API (`sergulath view screenshot`) | yes | no | no |
| Overlay pixels (`sergulath overlay --save`) | no | one overlay at a time | no |

A standalone headset draws its system screens on its own side; they do not pass through the PC's compositor, so no PC-side route includes them.

On the headset (Steam Frame, over SSH with a registered key; `sergulath frame overlays`): the Frame runs its own SteamVR (vrserver, vrcompositor, vrdashboard, XRService for tracking and cameras, vrlink receiving the PC's frames, gamescope for Steam's 2D UI). The "outside the play space" screen is the on-device overlays `system.systemui` (its button, "プレイスペース外でカメラを有効にする") and `XRService-CameraOverlay` (the camera view). From an on-device background client:

| On-device route | Result |
|---|---|
| Overlay state (shown, alpha) | yes, for every system overlay: tells whether that screen is up |
| Overlay pixels (`GetOverlayImageData`) | only raw-pixel overlays: `system.systemui` (a UI atlas with the button). The camera view, the headset view and Steam's UI are GPU textures and answer error 24 |
| The composited headset image | not found yet. The compositor's debug command `screenshot_all_request` was accepted but wrote nothing; a Vulkan capture layer would need the compositor restarted with changed settings, which these read-only tools do not do |

The VR View shows the scene only while the headset tracks. When a Frame on a desk loses tracking (tracking result `calibrating_out_of_range`), the view goes black; `view save` then reports the image as blank and exits with 1.

## Ground rules

1. Read-only by default. A change needs an explicit action word or flag.
2. Snapshot before changing any SteamVR setting, play area or registration, and restore on exit, including after a failure.
3. A virtual driver is registered only for the duration of a check, so the user's normal VR sessions never see it.
4. Never touch an app's own settings. When the tools start an app, they give it a separate settings folder.
5. No model downloads and no microphone use without the person's consent in the app's own dialog.
6. Records state the machine, the time (with the UTC offset) and the versions they were taken with. A result from one PC is not a general guarantee.

## Requirements

- Windows 10 or 11 with SteamVR (CI runs the unit tests on Windows and Linux)
- Python 3.11 or later and [pyopenvr](https://github.com/cmbruns/pyopenvr) for the client tools
- For the virtual driver (stage 2): a C++ toolchain with MSVC; CI builds it

## Contributing

Branches follow ASTAROT's: work on a feature branch and open a pull request into `develop` (the default branch), merged with a merge commit; `main` is updated from `develop` only for releases. Commit messages follow Conventional Commits.

## License

MIT, see [LICENSE](LICENSE). pyopenvr and the OpenVR SDK keep their own license (BSD-3-Clause); see [NOTICE.md](NOTICE.md).

The name follows ASTAROT's internal code names, taken from demons said to serve Astarot ([kein5/astarot#689](https://github.com/kein5/astarot/issues/689)). Sergulath is listed in the *Grimorium Verum* under Nebiros. The name is a label only and implies nothing supernatural.
