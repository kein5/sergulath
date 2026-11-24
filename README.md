# SERGULATH

**Sergulath's Emulation, Recording, Gauging & Unattended-Launch Automation Testing Harness**

Tools for operating and checking SteamVR overlay apps on a real headset (first a Steam Frame) from a Windows PC: SteamVR state and settings, the VR view, overlay pixels, device tracking, and virtual devices (a headset and two controllers) for checks nobody wears.

[日本語](docs/README.ja.md)

> Status: stage 2. Reading SteamVR, the overlays and the headset's image, pressing overlays with virtual controllers on a virtual headset, stand-in speech servers, and a suite runner for an app's end-to-end cases are here; see [Plan](#plan) for what each command has reached.

## The command line comes first

SERGULATH is a command-line tool. Every capability is a subcommand of `sergulath`, usable by hand and from scripts, CI or other agents, and commands combine through pipes and files. The Python package underneath (`sergulath.core`) prints nothing and serves the commands; it is not a separate interface.

Every command keeps these conventions:

- Data goes to stdout, messages go to stderr.
- `--format text` (the default) is for people; `--format json` prints JSON Lines, one object per record, for `jq` and other commands.
- No command prompts or waits for input.
- Exit codes: `0` done, `1` ran but found nothing, `2` usage error, `3` SteamVR is not running, `4` failed. Read-only commands never start SteamVR; only the ones that say so (`steamvr start`, `--restart`, `hmd`, `view open`, `chaperone restore`) quit and start it, always through Steam.
- Files a command writes are named by an option (`--csv`, `--save`); nothing is written elsewhere.

## Use

```sh
pip install .            # Python 3.11 or later, Windows
pip install .[audio]     # also `sergulath audio` (PyAudioWPatch)

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

How it works: a second start of the app's executable hands over to the running app, which shows its main window (the app stays single). The buttons are then pressed through Windows UI Automation by their accessible names, which the profile lists per UI language (`[ui.ja]` for ASTAROT). The sample is the app's own: it sends a caption once a second while the settings page stays open, and changes no setting. `caption check` reads the caption overlay: shown, not transparent (a caption fades out a few seconds after the last line) and drawn. The VR View shows the caption only while the headset tracks; a Steam Frame left on a desk loses tracking and the view turns one flat colour, which `--view` records as blank. To see a caption with nobody wearing a headset, cut it out of the virtual headset's image (see [Checks nobody wears](#checks-nobody-wears)).

`watch`, `overlay`, `profiles` and `caption check` only read; `caption sample` presses the app's own sample button. `watch` and `overlay` connect to a SteamVR that already runs (`vrserver` and `vrcompositor`) as a background OpenVR client.

Records can show a room, a desktop or file names; `records/` is ignored by git.

## Pressing an overlay

The virtual controller driver (`driver/`, a SteamVR driver DLL written in Rust) gives SteamVR a left and a right controller that SERGULATH moves and presses over UDP on 127.0.0.1. It is registered only for a check (ground rule 3) and adds no device until `controller connect`.

```sh
sergulath driver build --toolchain 1.100.0-x86_64-pc-windows-gnu   # once; stages build/driver/sergulath
sergulath driver register --restart       # SteamVR loads drivers at start: quit it through its status window, start it through Steam
sergulath controller connect --hand left
sergulath controller pose --hand left --position -0.2 1.1 -0.4   # the launcher follows the left hand
sergulath overlay press --overlay ASTAROT3 --button log --dry-run # aim and report SteamVR's own hit test
sergulath overlay press --overlay ASTAROT3 --button log           # and click the trigger
sergulath driver unregister --restart     # back to an ordinary session
# or, registered only around one command, also when it fails:
sergulath driver run --restart -- sergulath overlay press --overlay ASTAROT3 --button log
```

Apps that read buttons with `GetControllerState` (ASTAROT does) get them through the driver's legacy binding: trigger (`k_EButton_SteamVR_Trigger`), grip and application menu. SteamVR keeps the system button for itself.

Measured on 2026-11-23 (Steam Frame on a desk, in standby; SteamVR 2.19.4): the driver loads beside the Frame's, both controllers get valid poses and the left/right roles, and ASTAROT's launcher follows the virtual left hand. But SteamVR's `ComputeOverlayIntersection` answered no hit for every overlay, even an opaque one SERGULATH made itself and an app run as an overlay app. ASTAROT's laser uses the same call, so with the headset in standby nothing can be pressed; the check needs the headset awake. The VR View goes black in the same state. Blocking standby from the driver (`standby block`, `ShouldBlockStandbyMode`) did not change it; the headset reported `idle` either way. ASTAROT received the virtual grip (its log: `overlay grab: hand 2 grip true hit none`) but its laser hit nothing. The headset's missing pose was the remaining difference.

With the virtual headset (`sergulath hmd`, 2026-11-23, same PC) the launcher is pressed with nobody at the headset:

```sh
sergulath hmd run --with-driver -- python examples/astarot_launcher_e2e.py 6
```

`hmd run` snapshots `steamvr.forcedDriver` and `driver_null.*`, registers the virtual controllers and restarts SteamVR through Steam; the null headset reports a fixed head pose at the standing origin. The script closes SteamVR's dashboard (SteamVR opens it when no game runs, and apps get no buttons while it is open), holds the left hand so the launcher faces the head, and presses the launcher's log button six times: 12 of 12 presses in two runs toggled the VR panel, and 10 of 10 more by hand. Afterwards `hmd run` restores the settings, removes the driver and restarts SteamVR, and the Steam Frame came back as the headset by itself. While the virtual headset is on, the Frame is not SteamVR's headset and shows nothing from the PC. A press holds the trigger 0.3 s: ASTAROT missed 0.15 s, and 0.6 s is its long press (the log button then recalls the log instead).

## Checks nobody wears

A Steam Frame on a desk loses tracking, sleeps, and takes its controllers with it. The virtual headset and controllers stand in for a person, and stand-in servers stand in for the models:

| What | How | Details |
|---|---|---|
| A headset with a fixed head pose | `sergulath hmd on --with-driver --window 0 0 1920 1080` (snapshot, restart through Steam), `hmd off` | [Pressing an overlay](#pressing-an-overlay) |
| What the headset shows | `sergulath view save --headset`: the virtual headset's window, both eyes, scene and overlays | [What the PC can see](#what-the-pc-can-see) |
| One overlay as the wearer sees it | `sergulath view save --headset --overlay ASTAROT0`: the overlay's box, found from its pose and a calibration probe. It reads overlays whose pixels SteamVR does not hand out (GPU textures, a headset in standby) | [docs/checks/headset-view.md](docs/checks/headset-view.md) |
| Captions with speaker names, without a model | `sergulath asr serve`: a stand-in speech.cpp server that sends scripted, speaker-labelled captions | [docs/checks/speaker-names.md](docs/checks/speaker-names.md) |
| Speech reaching the virtual microphone, without a model | `sergulath tts serve` answers with a tone; `sergulath audio check` listens to the virtual cable's loopback | [docs/checks/tts-virtual-mic.md](docs/checks/tts-virtual-mic.md) |
| The VR View and the play area | `sergulath view open` / `close`, `sergulath chaperone show` / `apply` / `restore` | [docs/checks/view-and-chaperone.md](docs/checks/view-and-chaperone.md) |
| An app's end-to-end cases in one run | `sergulath suite run --cases suites/astarot-e2e.toml` (ASTAROT's 24 cases; the app runs with its own settings folder) | [docs/checks/e2e-suite.md](docs/checks/e2e-suite.md) |

What still needs a person: a worn, tracking headset (captions in the VR View, the headset waking from standby), lifting the headset (tracking coming back), and anything the standalone headset draws itself.

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
| `sergulath logs` | Builds one timeline from `vrserver.txt`, `vrcompositor.txt` and the app's log (local time with its offset) | 0, done |
| `sergulath steamvr status` | Reports SteamVR: running or not, whether `vrmonitor` runs, its version, the headset's and controllers' state | 0, done |
| `sergulath steamvr start` / `wait` / `stop` | Starts SteamVR through Steam, waits for the headset (connected or tracking), quits SteamVR | 0, done |
| `sergulath settings show` / `set` / `restore` / `pending` | SteamVR settings (`section.name=value`), changed only after a snapshot of the keys, restored key by key | 0, done |
| `sergulath view save` / `screenshot` | Saves what the headset shows: the VR View window (scene and overlays), the virtual headset's window (`--headset`), or a SteamVR screenshot (scene only). `--headset --overlay KEY` cuts one overlay out of the virtual headset's image, for overlays whose pixels SteamVR does not hand out. See [What the PC can see](#what-the-pc-can-see) and [docs/checks/headset-view.md](docs/checks/headset-view.md) | 0, done |
| `sergulath caption sample start` / `stop`, `sergulath caption check` | Sends the app's sample caption from the session side, and checks that the caption overlay renders | 0, done |
| `sergulath frame overlays` | Reads a standalone headset's own system overlays over SSH (state, and images where readable) | 1, prototype |
| `sergulath frame shot` | Saves what a Steam Frame's panel shows (its scanout framebuffer, both eyes, the camera screen included). Needs root once: the password comes from a one-time file the session tool writes, and goes only to sudo. See [docs/checks/frame-shot.md](docs/checks/frame-shot.md) | 2, prototype (captured in standby; not yet awake) |
| `sergulath view open` / `close` | Opens the VR View unattended (a snapshot of `steamvr.showMirrorView` and a SteamVR restart) and undoes it. See [docs/checks/view-and-chaperone.md](docs/checks/view-and-chaperone.md) | 1, done |
| `sergulath chaperone show` / `apply` / `restore` | The play area: read it; set a size (and walls) through the chaperone setup API after a snapshot of `chaperone_info.vrchap`; write the file back. SteamVR 2.19.4 keeps even a "temporary" commit on disk, so the change lasts until `restore`, not for the session only | 1, done |
| `sergulath driver build` / `register` / `unregister` / `run` / `status` | A virtual controller driver (an OpenVR driver with a left and a right hand), registered only for a check. See [Pressing an overlay](#pressing-an-overlay) | 2, prototype (brought forward) |
| `sergulath controller connect` / `pose` / `button` / `dashboard` | Moves the virtual controllers and presses their buttons; `pose --show KEY` holds a hand so its wrist overlay faces the headset; `dashboard close` closes SteamVR's dashboard, which takes the buttons from apps | 2, prototype |
| `sergulath overlay press` | Points a virtual controller's laser at a spot on an overlay (from its real pose and size) and clicks the trigger | 2, prototype |
| `sergulath hmd on` / `off` / `run` / `status` | A virtual headset (SteamVR's bundled null driver) for checks nobody wears, with a snapshot of the settings it changes and a restore, also after a failure | 2, prototype |
| `sergulath asr serve` | A stand-in speech.cpp server (model list and Realtime WebSocket) that sends scripted, speaker-labelled captions, for checking an app's caption path with no model or sound. See [docs/checks/speaker-names.md](docs/checks/speaker-names.md) | 2, prototype |
| `sergulath audio devices` / `check` | Listens to an audio device for a few seconds: level (RMS and peak, dBFS) and how long it was above a threshold; a playback device is read through its loopback (no microphone permission), a recording device only with `--allow-input`. See [docs/checks/tts-virtual-mic.md](docs/checks/tts-virtual-mic.md) | 2, done |
| `sergulath tts serve` | A stand-in speech.cpp server that answers text to speech with a tone (or a WAV), for checking an app's speech path with no model | 2, done |
| `sergulath suite list` / `run` | Runs end-to-end cases from a case file (TOML) and sums up pass, fail, error, unsupported, manual and skipped; ASTAROT's 24 cases are in `suites/astarot-e2e.toml`. See [docs/checks/e2e-suite.md](docs/checks/e2e-suite.md) | 2, done |
| `sergulath app status` / `start` / `stop` / `press` | The app under check: start it with a settings folder of its own (`--settings-dir`, `--fresh`), stop it, press its buttons by accessible name | 2, done |

Stage 0 turned the scripts that already existed into commands. Stage 1 measured how far a real headset can be checked without a person: SteamVR answers no overlay hit and hands out no overlay pixels while a Steam Frame is in standby, and the VR View is empty while it does not track. Stage 2 follows from that: virtual devices for what needs a pose, the headset's image for what needs pixels, stand-in servers for what needs a model, and a suite runner that puts them together.

## What the PC can see

Measured on 2026-11-22 with a Steam Frame over Steam Link, SteamVR 2.19.4 and VRChat, by showing a checkerboard test overlay in front of the headset:

| Route | Scene app | Overlays (captions, test overlay) | The headset's own screens (Steam Frame "outside the play area" camera screen) |
|---|---|---|---|
| VR View window (`sergulath view save`) | yes | yes | no: the view went black while the headset showed that screen |
| SteamVR screenshot API (`sergulath view screenshot`) | yes | no | no |
| Overlay pixels (`sergulath overlay --save`) | no | one overlay at a time, only raw-pixel overlays and not while the headset is in standby | no |
| The virtual headset's window (`sergulath view save --headset`, `--overlay KEY` for one overlay; measured 2026-11-23) | yes | yes, as drawn by the compositor | not applicable |

A standalone headset draws its system screens on its own side; they do not pass through the PC's compositor, so no PC-side route includes them.

On the headset (Steam Frame, over SSH with a registered key; `sergulath frame overlays`): the Frame runs its own SteamVR (vrserver, vrcompositor, vrdashboard, XRService for tracking and cameras, vrlink receiving the PC's frames, gamescope for Steam's 2D UI). The "outside the play space" screen is the on-device overlays `system.systemui` (its button, "プレイスペース外でカメラを有効にする") and `XRService-CameraOverlay` (the camera view). From an on-device background client:

| On-device route | Result |
|---|---|
| Overlay state (shown, alpha) | yes, for every system overlay: tells whether that screen is up |
| Overlay pixels (`GetOverlayImageData`) | only raw-pixel overlays: `system.systemui` (a UI atlas with the button). The camera view, the headset view and Steam's UI are GPU textures and answer error 24 |
| The composited headset image | yes, with `sergulath frame shot`: the panel's framebuffer, both eyes after the lens correction, 4320 x 2160, in under a second. It needs root once per capture (the password from a one-time prompt). In standby the panel shows one flat colour. The compositor's debug command `screenshot_all_request` wrote nothing, and the OpenVR mirror is refused to clients on the headset ([docs/checks/frame-shot.md](docs/checks/frame-shot.md)) |

The VR View shows the scene only while the headset tracks. When a Frame on a desk loses tracking (tracking result `calibrating_out_of_range`), the view below its toolbar turns one flat colour (black or grey); `view save` then reports the image as blank and exits with 1. Under the virtual headset the VR View stays black: the null driver gives it no mirror, so use `view save --headset` there.

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
- For `sergulath audio`: PyAudioWPatch (`pip install .[audio]`); for the speech check, a virtual audio cable (VB-CABLE was used)
- For the virtual controller driver: a Rust toolchain (no C++ compiler; the driver builds its vtables itself). CI builds and tests it on Windows and Linux; only Windows has been tried with SteamVR

## Contributing

Branches follow ASTAROT's: work on a feature branch and open a pull request into `develop` (the default branch), merged with a merge commit; `main` is updated from `develop` only for releases. Commit messages follow Conventional Commits.

## License

MIT, see [LICENSE](LICENSE). pyopenvr and the OpenVR SDK keep their own license (BSD-3-Clause); see [NOTICE.md](NOTICE.md).

The name follows ASTAROT's internal code names, taken from demons said to serve Astarot ([kein5/astarot#689](https://github.com/kein5/astarot/issues/689)). Sergulath is listed in the *Grimorium Verum* under Nebiros. The name is a label only and implies nothing supernatural.
