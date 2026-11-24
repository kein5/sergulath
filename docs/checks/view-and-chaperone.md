# Opening the VR View and changing the play area, unattended

`view open` / `view close` and `chaperone show` / `apply` / `restore`. Both change something of
SteamVR's, so both start with a snapshot and have a command that puts it back (ground rule 2).

## The VR View

```sh
sergulath view open      # snapshot steamvr.showMirrorView, set it to true, restart SteamVR through Steam
sergulath view save --out records/vr-view.png
sergulath view close     # restore the snapshot, restart SteamVR
```

- SteamVR reads `showMirrorView` only when it starts. Setting it while SteamVR runs (through
  `vrcmd --set-settings-bool` or `IVRSettings`) does not open the window, so `view open` restarts
  SteamVR. If the window is already open it changes nothing, and `view close` then has nothing to
  undo (exit 1): a VR View opened by hand stays open.
- The snapshot is labelled `view` in `records/settings/`; `settings pending` lists it until
  `view close`.

What the VR View shows (2026-11-23, this Windows PC, SteamVR 2.19.4, Steam Frame over Steam Link):

| Headset | VR View below its toolbar | `view save` |
|---|---|---|
| Steam Frame on the desk, not tracking (`calibrating_out_of_range`) | one flat grey | blank, exit 1 |
| The virtual headset (`hmd on`) | black: the null driver gives it no mirror | blank, exit 1 |
| Steam Frame worn and tracking | the scene and overlays (2026-11-22, by hand) | not blank, exit 0 |

The toolbar (menu, dock, full screen) is drawn by vrmonitor over the mirror even when the mirror is
empty; the blank check leaves out the top 10% of the window for it. Before that, a not-tracking
VR View was reported as not blank. Under the virtual headset, read what it shows from its own
window instead (`view save --headset`).

E22 of the suite runs `view open`, checks the blank VR View of the Frame on the desk, and closes
it again. The other half of that case (captions in the VR View while tracking) needs the headset
worn.

## The play area

```sh
sergulath chaperone show                       # read-only
sergulath hmd on
sergulath chaperone apply --size 2 3 --walls   # snapshot chaperone_info.vrchap, then set 2 x 3 m with walls
sergulath chaperone restore                    # quit SteamVR, write the file back, start SteamVR
sergulath hmd off
```

`apply` goes through `IVRChaperoneSetup`: revert the working copy, set the play area size (and four
2.43 m walls with `--walls`), commit with `EChaperoneConfigFile_Temp`. A virtual headset's universe
has no calibration (`error_base_station_uninitialized`) and SteamVR refuses a commit without a
standing origin, so `apply` then sets the standing and seated origins to the raw origin.

**The "temporary" commit is written to disk.** The API documents `EChaperoneConfigFile_Temp` as
valid for the current session and not written to disk. On SteamVR 2.19.4 it was written to
`chaperone_info.vrchap` at once (the file's time changed to the commit's second, with the new
universe in it), and `ReloadFromDisk(Live)` then kept it. A change made this way would outlive the
session. So:

- the snapshot is the whole file (or the fact that there was none), plus the live chaperone's
  export, in `records/chaperone/`;
- `restore` quits SteamVR, writes the file back as it was, starts SteamVR through Steam, and
  compares the universes in the file with the snapshot's (SteamVR's time stamps aside).

Measured on 2026-11-23 under the virtual headset: `apply --size 2 3 --walls` gave a 2 x 3 m play
area with 4 walls (calibration `ok`) in universe 2, the null driver's; `restore` brought the file
back byte for byte (same SHA-256) and universe 2 was uncalibrated again. The Steam Frame's universe
in the same file was not touched.

`apply` refuses while a real headset is SteamVR's headset unless `--real-headset` is passed. A
Steam Frame keeps its own boundary on the headset, and what this commit does to it has not been
checked: the Frame stays read-only in these checks.

pyopenvr 2.12's `exportLiveToBuffer` and `getLiveCollisionBoundsInfo` wrappers pass the length by
value and read one quad only; `sergulath.core.chaperone` calls the function table directly.

## Also found

A SteamVR started moments after the previous one quit sometimes never got the Steam Frame back:
vrserver ran, the compositor did not start, and `vrserver.txt` said
`VRInitError_Driver_WirelessHmdNotConnected` for over ten minutes. Quitting and starting SteamVR
once more brought the Frame back at once, so the start used by every restart (`hmd`, `driver`,
`settings --restart`, `view`, `chaperone`) now tries a second time when the first does not come up
within 90 s.
