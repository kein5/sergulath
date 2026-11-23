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

What is not known until it runs as root: whether the image while the headset is in standby is
the last frame or black, and how long one capture takes (one read of a 37 MB frame).

### Commands, once the password hand-off is decided

Run from the PC; the script goes to a temporary folder on the Frame, runs once under `sudo`, and
the folder is removed. `sudo -S -p ''` reads the password from standard input, so the password
must come from a file only that command reads (never typed into a chat or a log):

```sh
ssh steamos@FRAME 'mktemp -d /tmp/sergulath.XXXXXX'                 # -> DIR
scp sergulath/core/frame_scanout.py steamos@FRAME:DIR/capture.py
ssh steamos@FRAME "sudo -S -p '' env SERGULATH_SAVE_DIR=DIR python3 DIR/capture.py" < PASSWORD_FILE
scp steamos@FRAME:DIR/frame-scanout.png records/frame/
ssh steamos@FRAME 'rm -rf DIR'
```

Root is used for one process that reads the framebuffer and writes one PNG it hands to the
normal user. Nothing is installed, no setting or file outside the temporary folder is written.

## Ways to hand over the password (for the maintainer to choose)

1. **A one-time secret prompt in the session tool** (recommended for occasional captures): the
   person types the Frame's password into the tool's own secret card; it is written to a file that
   only the capture command reads (`< PASSWORD_FILE` above) and is never shown to the agent or kept.
2. **The person runs the capture command themselves** and types the password at sudo's prompt
   (`ssh -t ... sudo ...`). No secret passes through anything else; needs a person at the PC.
3. **A sudoers rule on the Frame** allowing exactly one root-owned capture script without a
   password. Unattended and repeatable, but it is a change on the Frame (a file in
   `/etc/sudoers.d`, and a root-owned copy of the script), and must be approved and removed
   separately.

Not proposed: giving a binary capabilities (`setcap`) or keeping a password in a file on either
machine.
