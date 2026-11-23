# Check: speaker names on ASTAROT's single-line caption overlay

What it shows: ASTAROT's single-line caption overlay (ASTAROT0) with a speaker's name above the
sentence, as SteamVR's compositor draws it in a headset. Nobody wears the headset, and no model,
microphone or sound is needed. Added for ASTAROT PR #984 (0.7.0 development), whose open item
was the name row's size and colour in a real SteamVR.

## What it needs

- SteamVR (any headset; the check switches to the virtual headset and back).
- An ASTAROT build that has the feature. 0.6.0 does not; build develop or use a candidate.
- No other ASTAROT running: the overlay keys (ASTAROT0..4) belong to one app at a time. Quit the
  installed app first and start it again after.

## Steps

1. Start the stand-in speech server. It sends two sentences in turn as speakers A and B, one every
   5 s, once an app's session is set up:

   ```sh
   sergulath asr serve --port 39600 --every 5
   ```

2. Start the ASTAROT build with a settings folder of its own (`VRCT_CONFIG_DIR` also lets it run
   next to nothing else), and point the build at the OpenVR library a package ships
   (`ASTAROT_OPENVR_API`, for a build without `_internal`). Its `config.json` before the first
   start:

   ```json
   {
     "FIRST_RUN_SETUP_PENDING": false, "TELEMETRY_NOTICE": 1, "ENABLE_TELEMETRY": false,
     "OSC_IP_ADDRESS": "127.0.0.2", "SEND_MESSAGE_TO_VRC": false, "WEBSOCKET_SERVER": false,
     "UI_LANGUAGE": "en", "OVERLAY_SMALL_LOG": true, "OVERLAY_LARGE_LOG": true,
     "SELECTED_TRANSCRIPTION_ENGINE": "SpeechCpp",
     "REALTIME_ASR_SETTINGS": {
       "server": "external", "base_url": "http://127.0.0.1:39600", "model": "haas-ja",
       "mic": {"language": "ja", "endpoint": null, "follow_default": true},
       "speaker": {"language": "ja", "endpoint": null, "follow_default": true},
       "vocabulary": {"builtin": false, "terms": []}
     }
   }
   ```

   `OSC_IP_ADDRESS` 127.0.0.2 keeps a build at a new path from asking for a firewall rule.

3. In the app, turn on "Listening". The log shows the sentences under "Speaker A" and "Speaker B".
   Name one voice: press "Name Speaker B", type a name, "Save names". Names apply from the next
   caption on (#984 does not redraw the caption on screen).

4. Switch to the virtual headset with a window large enough to judge text, and save what the
   compositor sends to it:

   ```sh
   sergulath hmd on --window 0 0 2400 1350
   sergulath steamvr wait --until tracking
   sergulath view save --headset --out records/speaker-names.png
   sergulath hmd off
   ```

   The single-line overlay sits 1 m in front of the head and 0.4 m down, so it is in view of the
   virtual headset's fixed pose. Save a few frames a second apart if the named voice's caption has
   to be caught.

5. Stop the app and the speech server; start the installed app again.

`sergulath overlay --key ASTAROT0 --save DIR` does not work here: since the overlay is a GPU
texture SteamVR answers OverlayError_InvalidTexture for its pixels, so the headset window is the
way to see it.

## Judging

Crop the caption from the left eye at its own size and look at:

- the name row is there, above the sentence, in the received purple;
- its height against the sentence (the design is 60 px against 90 px, 2/3);
- its contrast against the caption background, computed from the pixels (WCAG contrast ratio).

The null headset's window is not a Steam Frame: its pixels per degree differ (a Frame shows the
same overlay about 1.4 times as many pixels across), so the numbers are a floor, not a measurement
of the headset.

## Result on 2026-11-23 (ASTAROT develop d6e549ae8, SteamVR 2.19.4, this Windows PC)

- Name row present: "ミカ" in purple above "このあと一緒にワールドを回りましょう。", centred; a voice
  without a name showed its label ("A").
- At 1200 px per eye: name 7 px tall, sentence 12 px (0.58).
- Contrast: the purple's brightest pixel (144, 85, 154) on the background (41, 42, 45) is 2.7:1 in
  the frame; the colours in the code (TEXT_RECEIVE 168, 97, 180 on BACKGROUND 41, 42, 45) give
  3.4:1, the sentence's text 10.8:1. 3.4:1 passes 3:1 for large text and misses 4.5:1 for
  normal text; at 7 px the name is small text.
- SteamVR's dashboard showed behind the caption (no game ran), as in a real session without one.
