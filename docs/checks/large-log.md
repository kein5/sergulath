# Check: how readable ASTAROT's large log is (the multi-line caption overlay)

What it shows: ASTAROT's large log (ASTAROT1, the multi-line caption overlay on the left hand) as
SteamVR's compositor draws it in a headset, with received rows ("Receive" heading, timestamp,
speaker label) and a sent row ("Send"). Nobody wears the headset; no model, microphone or sound is
needed. It follows [speaker-names.md](speaker-names.md): same stand-in route, plus the virtual
controllers, because the large log follows the left hand.

## Steps

1. Start the stand-in speech server (two sentences as speakers A and B, one every 5 s):

   ```sh
   sergulath asr serve --port 39600 --every 5
   ```

2. Switch to the virtual headset with the virtual controllers, in a window large enough to judge
   text:

   ```sh
   sergulath hmd on --with-driver --window 0 0 2400 1350
   sergulath steamvr wait --until tracking
   ```

3. Quit any other ASTAROT, and start the build to check with its own settings folder, set up as in
   [speaker-names.md](speaker-names.md) step 2 (`OVERLAY_LARGE_LOG` true). Turn on "Listening";
   received rows appear in the large log every 5 s.

4. Hold the left hand so the large log faces the head, close SteamVR's dashboard (it opens when no
   game runs and sits behind the log), and send one message from the app's message box so a
   "Send" row is shown too:

   ```sh
   sergulath controller connect --hand left
   sergulath controller connect --hand right
   sergulath controller dashboard close
   sergulath controller pose --hand left --show ASTAROT1 --distance 0.4 --below 0.05
   ```

   0.4 m is about where a wrist is held to read it. The log keeps five rows; capture within a few
   seconds of sending, before received rows push the sent one out.

5. Save the compositor's image, then put everything back:

   ```sh
   sergulath view save --headset --out records/large-log.png
   sergulath hmd off      # also removes the virtual controllers
   ```

   Stop the app and the speech server, and start the installed app again.

## Judging

Crop the log from the left eye at its own size. For each element, take the brightest pixel of its
colour inside a box around it and compute its WCAG contrast ratio against the log's background,
and take the height of its ink in pixels:

- the "Receive" heading (code: TEXT_RECEIVE 168, 97, 180 on BACKGROUND 41, 42, 45);
- its timestamp (code: TEXT_TIME 120, 120, 120);
- the speaker label after the heading and the message text (code: TEXT 223, 223, 223);
- the "Send" heading (code: TEXT_SEND 97, 151, 180).

The null headset's window is not a Steam Frame: a Frame gives the same overlay about 1.4 times as
many pixels across, so the heights are a floor. Antialiasing lowers the colour of thin, small text
below the code's colour, so rendered contrast is lower than computed contrast.

## Result on 2026-11-23 (ASTAROT develop d6e549ae8, SteamVR 2.19.4, this Windows PC)

Large log 0.25 m wide, held 0.40 m from the head; 1200 px per eye.

| Element | Code colour, computed contrast | Brightest rendered pixel | Rendered contrast | Ink height |
|---|---|---|---|---|
| "Receive" heading | 168, 97, 180 · 3.44:1 | 141, 84, 152 / 142, 85, 153 | 2.63 / 2.67:1 | 5–6 px |
| Timestamp ("12:00") | 120, 120, 120 · 3.25:1 | 95, 96, 96 / 100, 100, 100 | 2.27 / 2.43:1 | 7 px (digits) |
| Speaker label ("A", "B") | 223, 223, 223 · 10.8:1 | 196, 196, 196 | 8.2:1 | 5 px |
| Message text | 223, 223, 223 · 10.8:1 | 215, 215, 215 | 8.4–10.0:1 | 10 px |
| "Send" heading | 97, 151, 180 · 4.51:1 | 83, 127, 151 | 3.31:1 | 6 px |
| Send timestamp | 120, 120, 120 · 3.25:1 | 96, 96, 97 | 2.28:1 | 6 px |

The headings are about 0.55–0.6 of the message text's height. The messages read at a glance;
the headings and timestamps do not: "Receive" and the time read as dim coloured marks, and need a
close look (or the enlarged crop) to be read. "Send" is a little better (3.3:1) but still below
4.5:1. The speaker label is bright enough but as small as the heading.
