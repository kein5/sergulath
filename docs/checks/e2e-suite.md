# The ASTAROT end-to-end suite (26 cases)

`suites/astarot-e2e.toml` holds the 24 cases of the list made on 2026-11-23 (E01..E24), plus E25
(30-minute soak) and E26 (0.6.0 to beta / local package upgrade), and `sergulath suite run` runs
them in order and sums them up.

```sh
sergulath suite list --cases suites/astarot-e2e.toml                   # what runs, what cannot, why
sergulath suite run  --cases suites/astarot-e2e.toml --record records/suite/run
sergulath suite run  --cases suites/astarot-e2e.toml --slow            # also E18, E21 and E25 (about 60 min)
sergulath suite run  --cases suites/astarot-e2e.toml --only E16        # one case (its phase's setup runs too)
```

Run it from the repository's root (the case file's paths are relative to it).

## What it needs

- SteamVR with the headset connected. A Steam Frame lying on a desk is fine: the cases that need
  a worn headset or a hand run on the virtual headset (`hmd`) with the virtual controllers.
- The installed ASTAROT 0.6 or later.
- The virtual controller driver built once (`sergulath driver build`).
- E20 and E23: SSH to the Frame (`sergulath frame`); they only read.

## What it changes, and puts back

- The installed ASTAROT is stopped for the run. The cases run their own ASTAROT
  (`app start --settings-dir records/suite/app --fresh`), so pressing its settings never touches
  the user's (ground rule 4). The installed app is started again at the end.
- E01 stops SteamVR and starts it again through Steam.
- The `virtual` phase (E08, E09, E12, E14, E16, E17) switches SteamVR to the virtual headset with
  the virtual controllers (`hmd on --with-driver`: a settings snapshot) and back (`hmd off`). Its
  window is 1920 x 1080, at the top left of the desktop, while the phase runs.
- The `vrview` phase (E22) opens the VR View (`view open`: a settings snapshot and a SteamVR
  restart) and closes it (`view close`).
- The `soak` phase (E25) starts the caption sample before the 30-minute soak and stops it on exit.
- The `upgrade` phase (E26) stops the suite's ASTAROT while E26 runs an isolated copy in
  `records/suite/E26` (never touching the user's installed ASTAROT or settings; HKCU installer
  registry keys are snapshotted and restored around installer runs), then starts the suite's app
  again. See [soak-and-upgrade.md](soak-and-upgrade.md).

Teardowns run also after a failure or an interruption. If a run dies before them anyway:
`sergulath hmd off`, `sergulath view close`, `sergulath app stop`, `sergulath app start`.

E09 alone on the virtual headset, 2026-11-23: passed; the caption cut out of the headset's image
read "サンプルテキスト ...", and the cleanup left the Frame as the headset, the driver
unregistered, no snapshot pending and the installed app running.

E22 alone, 2026-11-23 (same PC, Frame on the desk): passed; the VR View was closed again,
`showMirrorView` back to false, no snapshot pending, and the installed app running.

## The case file

The format is described at the top of `sergulath/core/suite.py`. In short: each case has steps
(`run` = a command; `exit` = the exit codes that pass, default 0; `json` = values some JSON line of
the output must have, with `{gt = 0}`, `{lt = 0.3}`, `{present = true}` for comparisons; `wait` =
seconds before the step), or `unsupported` / `manual` with the reason it is not run. `phase` puts a
case inside a phase's setup and teardown; `slow = true` runs it only with `--slow`.

Statuses: pass, fail, error (a setup failed), unsupported, manual, skipped (slow). Only fail and
error make the exit code 1.

## Cases that do not run as the list wrote them

| Case | Status | Why |
|---|---|---|
| E12 (launcher alpha when worn) | runs on the virtual headset | the list marks it as needing a person; the virtual headset looks at the virtual wrist instead |
| E16 (the panel opens) | runs on the virtual headset | the list says opening it needs a person; the virtual controllers press the launcher now |
| E08 (multi-line caption on the hand) | runs on the virtual headset | a Frame's controllers sleep on a desk, and the hand-held overlay then has no hand to follow |
| E17 (popup) | checks the VR UI popup instead | the list says a notification shows ASTAROT4 and it goes away with time; in 0.6 ASTAROT4 is the VR UI's popup window (language, inputs and outputs, VR settings), opened from the launcher, with no time-out. The case opens and closes it until the list is settled |
| E18 (20-minute watch) | slow; standby throughout | unattended, the headset is not worn at the start, so the record cannot show the change from active |
| E19 (tracking back after lifting) | manual | someone has to lift the headset; the virtual headset never loses tracking |
| E22 (VR View captions, blank when lost) | runs the blank half | `view open` opens the VR View (phase `vrview`, closed again by `view close`) and the Frame on the desk gives a blank view, exit 1. Captions in the VR View need the headset worn: under the virtual headset the VR View stays black (see [view-and-chaperone.md](view-and-chaperone.md)) |
| E24 (0.5.x keys) | unsupported | needs a 0.5.x install (optional in the list) |
| E09 (pixels read) | runs on the virtual headset, from its image | develop builds hand the caption overlays to SteamVR as GPU textures, whose pixels another process cannot read (`OverlayError_InvalidTexture`). The case cuts the caption out of the virtual headset's image (`view save --headset --overlay ASTAROT0`), which works for every build; `overlay --save` stays as a record where it works. See [headset-view.md](headset-view.md) |
| E25 (30-minute soak) | slow (`--slow`) | runs `sergulath soak` for 30 min with the caption sample active; checks PID liveness, post-warmup process-tree private memory bounds, and `ASTAROT0` visibility/alpha (without reading pixels, so standby and GPU-texture builds work). See [soak-and-upgrade.md](soak-and-upgrade.md) |
| E26 (0.6.0 -> beta upgrade) | beta channel, or local fallback; `unsupported` while unpublished | updates an isolated 0.6.0 sandbox in `records/suite/E26` via `beta/latest.json`, or `SERGULATH_UPGRADE_TO` (local ZIP, folder or `setup.exe`) while no beta > 0.6.0 is published; reports `unsupported` otherwise. See [soak-and-upgrade.md](soak-and-upgrade.md) |

## First run, 2026-11-23

This Windows PC, SteamVR 2.19.4, a Steam Frame on the desk in standby, ASTAROT 0.6.0-beta.2
installed: 19 passed, 0 failed, 0 errors, 2 unsupported, 1 manual, 2 skipped (of 24), in 6 minutes.
The cleanup left the installed app running with its own settings, the Frame as the headset, no
settings snapshot pending and the driver unregistered.

The long cases, run after it (`--only E18 --only E21 --slow`):

- E18 passed. The headset stayed in standby throughout, as expected unattended.
- **E21 failed.** After 7 minutes idle the one-line caption was shown (visible, alpha 1.0) but SteamVR answered `OverlayError_InvalidTexture` when its pixels were read, so `caption check` could not see it drawn. The same build read fine before and read fine again right after SteamVR restarted, and under the virtual headset. It fails once the real headset has gone to standby (activity `standby`, after `power.turnOffScreensTimeout`): SteamVR does not hand out an overlay's pixels then. Whether the caption is still drawn when the headset wakes is not something this case can see; it needs the headset worn after a standby, or the headset image route (stage 4 of the gap plan).
