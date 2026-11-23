# Reading a caption from the headset's image

`sergulath view save --headset --overlay KEY` cuts one overlay out of the virtual headset's window
(`sergulath hmd on`). It is the route for checking what an overlay shows when SteamVR will not
hand its pixels to another process.

```sh
sergulath hmd on --with-driver --window 0 0 1920 1080
sergulath controller connect --hand left
sergulath controller dashboard close
sergulath caption sample start
sergulath view save --headset --overlay ASTAROT0 --out records/caption.png           # one-line caption, left eye
sergulath view save --headset --overlay ASTAROT1 --eye right --out records/hand.png  # multi-line, on the hand
sergulath caption sample stop
sergulath hmd off
```

It writes the cut-out (`--out`) and the whole window next to it (`*-window.png`). The JSON record
gives the overlay's size in metres, the pixel box, the share of the overlay in the eye's view
(`inside`), and `visible` and `blank`. Exit 0 when the overlay is shown, in view and not one flat
colour; 1 otherwise.

## How the box is found

1. The overlay's pose comes from SteamVR (`overlay --placement` reads the same). Its width is
   SteamVR's, and its height follows from the texture's size and bounds.
2. Each eye's view follows from the head's pose, the eye's offset (`GetEyeToHeadTransform`) and
   its projection (`GetProjectionRaw`). The corners of the overlay, taken through them, give where
   it lands as a share of the eye's view.
3. Where each eye sits in the window is measured, not assumed. On 2026-11-23, a 480 x 270 window
   (on a 125% display) gave each eye 180 x 150 pixels at the top left, and the rest stayed black.
   A 1920 x 1080 window gave each eye 960 x 1080. So every capture first shows a small magenta
   probe overlay at two spots in front of the head, finds it in both eyes, and gets each eye's
   pixel scale and offset from the two spots. The probe is destroyed before the capture.

The box is axis-aligned around the projected corners. A tilted overlay (the hand-held one) gets a
box that also holds a little of what is around it.

Use a large window. In 480 x 270 the one-line caption was 6 pixels high and unreadable; in
1920 x 1080 it was 42 pixels high. The suite's virtual phase uses 1920 x 1080.

## Measured (2026-11-23, this Windows PC, SteamVR 2.19.4, ASTAROT 0.6.0-beta.2 with its own settings)

| Overlay | Result |
|---|---|
| ASTAROT0 (one-line caption, follows the head) | 480 x 42 px in the left eye, the sample's text readable, 100% in view |
| ASTAROT1 (multi-line, on the left hand held up with `pose --show`) | 275 x 147 px, every line readable |
| ASTAROT3 (launcher), not looked at | reported hidden (`visible` false), exit 1 |

E09 of the suite now uses this route on the virtual headset, and it passed. The same run also read
`overlay --save` on beta.2, whose caption overlay still hands out raw pixels.

## Can another process read the overlay directly? (one-time check, 2026-11-23)

No. The routes tried, on ASTAROT0 and ASTAROT1:

| Route | Result |
|---|---|
| `IVROverlay::GetOverlayImageData` | error 24 (`InvalidTexture`) for an overlay given as a GPU texture (develop builds), and for every overlay while a real headset is in standby (E21). Works for raw-pixel overlays otherwise |
| `IVROverlay::GetOverlayTexture` with a D3D11 device of our own | error 24 (`InvalidTexture`) for both. The API is meant for "an overlay you have created" |
| `getOverlayTextureSize` | works (the first call of a client sometimes answers 24; `view save` asks again), but it gives the size only |
| The compositor's mirror (what the VR View shows) | under the virtual headset the VR View stays black, and with a real headset in standby it is flat grey. It carries pixels only while a real headset tracks |

What is left:

- The headset's image, which the compositor has already drawn, as this route does.
- A change on the app's side, such as a debug option that writes its overlay textures to files.
  That is the app's decision, not SERGULATH's.

Reading another process's GPU memory or injecting into it is not a route these tools take.
