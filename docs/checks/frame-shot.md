# Taking a picture of what a Steam Frame shows (study, 2026-11-23)

A standalone headset draws some screens itself, such as the Steam Frame's "outside the play area"
camera screen. They never pass through the PC's compositor, so no PC-side route shows them
(see the README, "What the PC can see"). This study looked for a way to capture them without a
Vulkan capture layer, which would need the headset's compositor restarted with changed settings.

Rules for the study: nothing is changed on the Frame; a route that needs root runs only after the
way the password is handed over is decided. The Frame was on the desk in standby throughout
(SteamOS 0.4.5 "vr", SteamVR 2.18.2 on the headset, reached over SSH with a registered key).

## What the Frame has

- One panel, `DSI-1`, 4320 x 2160 at 90 Hz: both eyes side by side.
- The panel's primary plane flips between two framebuffers (106 and 107 in a one-second sample),
  so the current framebuffer holds the frame being shown. Format `XR24` (XRGB8888) with
  Qualcomm's compressed layout (modifier `0x0500000000000001`).
- GPU: Adreno 750 with Mesa (Turnip, desktop GL through Zink); EGL with surfaceless contexts and
  dma-buf import with modifiers.
- `ffmpeg` (with `kmsgrab`), `gamescopectl`, PipeWire with a `gamescope` video node, `python3`.
- `sudo` needs a password.

## The routes compared

| Route | How | What it would show | Needs | Change on the Frame | Result |
|---|---|---|---|---|---|
| A. The panel's framebuffer | Get the scanout framebuffer's memory, import it through EGL, read it back | Everything the panel shows (camera screen included), after the lens correction | Root (CAP_SYS_ADMIN) for one step | None to settings; root for one read | Prepared; every step but the root one proven (below) |
| B. The compositor's mirror | A client on the Frame asks OpenVR for the mirror texture (`GetMirrorTextureGL`) | What the compositor draws | A GL context on the Frame | None | Refused by the OpenVR client library (below) |
| C. SteamVR's screenshot API | `RequestScreenshot` on the Frame | Likely the scene only (on the PC it left out overlays) | — | Adds screenshots to the user's Steam library | Not tried: changes user data, little value |
| D. gamescope | Its screenshot, its PipeWire stream, or Steam's UI through its debugging port | Steam's 2D UI only | — | None | Not tried: cannot show the camera screen |
| E. Compositor debug dumps | Turn on dump settings in the headset's steamvr.vrsettings | Unknown | — | A settings change | Not tried: changes the Frame |
| Done earlier | The compositor's `screenshot_all_request`; `frame overlays` | Nothing written; overlay state only | — | None | — |

## Route B: the mirror (tried 2026-11-23, approved)

`examples/frame_mirror_probe.py`, fed to `python3 -` on the Frame. It makes a surfaceless EGL
context with desktop GL, connects to the Frame's SteamVR, and asks for each eye's mirror texture.

- As a background client and as an overlay client, `GetMirrorTextureGL` returned
  `VRCompositorError_InvalidTexture` (102) at once for both eyes. The compositor's log shows the
  connection and nothing else: the client library refuses before asking. The client runs as "the
  limited version of CVRCompositorClient"; a scene application might get the mirror, but becoming
  the scene application on the Frame would take the display over from what it shows (the PC's
  stream), so that was not tried.
- Effect on the compositor: none seen. Same process (no restart), 450 frames presented in each
  five-second window before, during and after (90 Hz), dropped frames 7 before and after,
  reprojected frames unchanged.
- Each client connecting or disconnecting woke the headset from standby for five seconds
  (`vrserver.txt`: "leaving standby", then "entering standby"). Restarts of the PC's SteamVR do the
  same.

Route B does not give an image.

## Route A: the panel's framebuffer (prepared, root not used)

`sergulath/core/frame_scanout.py` runs on the Frame:

1. Find the plane that scans out to the panel and its current framebuffer. No root.
2. Get a handle to the framebuffer's memory (`DRM_IOCTL_MODE_GETFB2`) and export it as a dma-buf
   (`drmPrimeHandleToFD`). The kernel hands the handle out only to the DRM master or to
   CAP_SYS_ADMIN: **this is the one step that needs root.** As the normal user, GETFB2 returns the
   size, format and modifier with handle 0, and the script stops here with exit 3 (checked).
3. Import the dma-buf through EGL with its modifier, read it back as RGBA with `glGetTexImage`,
   and write a PNG (4320 x 2160, both eyes). No root needed.

Proven without root (`examples/frame_dmabuf_proof.py`): EGL lists `XR24` with modifier
`0x0500000000000001` as importable, and a buffer of our own in that compressed layout, drawn half
red and half blue through GL, was imported again as a dma-buf and read back as exact red and
blue. So the GPU decodes the compressed layout, and step 3 works.

Running it as root answered the rest: in standby the panel shows a flat colour (not the last
frame), and one capture takes under a second (see Runs).

## `sergulath frame shot`

```sh
sergulath frame shot --out records/frame/shot.png                              # dry run, no root
sergulath frame shot --out records/frame/shot.png --password-file PASSWORD_FILE
```

It copies `frame_scanout.py` to a private temporary folder on the Frame, runs it once, copies the
PNG back and removes the folder, also after a failure. With `--password-file` the run is
`sudo -S -k -p '' ...`: sudo reads the password from the ssh command's standard input, asks once
(`-k`: no cached sudo ticket is used or left behind), and runs only that capture as root. The
password is read from the first line of the file and is never printed, put on a command line or
in an environment variable, or written anywhere on the Frame. Without `--password-file` the
capture runs as the normal user and stops before the step that needs root (exit 1), which checks
that everything else is in place. Root is used for one process that reads the framebuffer and
writes one PNG that it hands to the normal user; nothing is installed and no setting changes.

Exit codes: 0 saved; 1 dry run, or no plane scanning out (the panel off); 2 the password file
cannot be read or is empty; 4 sudo refused the password, or the capture failed. A refused
password is not tried again: each wrong attempt counts towards the Frame's login lockout.

## How the password is handed over (decided 2026-11-23)

A one-time secret prompt of the session tool (T3 Code): the maintainer types the Frame user's
password into the secret card the session opens in its thread. The tool writes it to a file that
only the next command can read, and the agent sees neither the value nor the file's contents. The
agent then runs `frame shot --password-file <that file>` once. Nothing is kept: no sudoers rule
on the Frame and no password file afterwards. A password is never asked for or given in a chat.

## Runs

- 2026-11-23, first run with the password: sudo accepted it and the capture ran as root, but it
  stopped before the image. The script's copy of libdrm's `drmModeFB2` had the handles after the
  pitches and offsets (libdrm has them first), so it read an offset (0) as the handle and stopped
  as if no handle were given. Fixed, with a test that keeps the order. With the right order the
  normal user still gets handle 0 (pitch 17408 bytes), so root is still needed. The password file
  was deleted unread afterwards; nothing was written on the Frame.
- The earlier unprivileged probes ran Mesa with its default shader cache, which adds entries to
  the user's `~/.cache/mesa_shader_cache` on the Frame (shared with other apps and managed by
  Mesa, so left alone). `frame shot` now turns the cache off and points anything else at the
  temporary folder.
- 2026-11-23, second run (after a hold on the hand-off was lifted): **captured.** The Frame lay on
  the desk in standby.

  ![What the Frame's panel showed in standby: two lens-shaped areas of flat dark blue on black](images/frame-shot-standby.png)

  - 4320 x 2160, both eyes, full resolution and lossless (PNG). The image is the one sent to the
    panel, after the lens correction, so each eye's visible area is a rounded shape and the
    corners stay black.
  - In standby the panel gets one flat colour, RGB (24, 28, 53), over 71% of the image (each
    eye's area about 2000 x 2060 px), black elsewhere, and one edge colour: three colours in all.
    Not the last frame before standby.
  - Time: 0.83 s for the run over ssh (sudo, framebuffer, read, PNG); on the Frame the read took
    82 ms and the PNG 291 ms. A real scene compresses less well, so its PNG will take longer.
  - Effect: the compositor and vrserver kept running (same processes); no temporary folder and no
    sudo ticket were left; the password file was deleted unread.
- Not captured yet: the Frame awake (worn), with an app or the outside-the-play-area camera screen
  showing. That needs someone to wear or move the headset, and the password once per capture.

