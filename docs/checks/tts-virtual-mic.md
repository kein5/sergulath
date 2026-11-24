# Check: ASTAROT's text to speech reaches the virtual microphone

What it shows: a message sent in ASTAROT is read aloud into the virtual cable that VRChat takes as
its microphone, and nothing reaches it while ASTAROT is muted. No TTS model, no microphone and no
person are needed: a stand-in speech server answers with a tone, and SERGULATH listens to the
cable's input through its loopback.

## Why the cable's input, not its output

A virtual cable (VB-CABLE here) has a playback end ("CABLE Input", where an app plays) and a
recording end ("CABLE Output", what VRChat records as its microphone). Both carry the same signal.
Reading the recording end is microphone use: Windows asks for permission per app and the user's
consent comes first (ground rule 5), so `audio check` refuses a recording device unless
`--allow-input` says the user agreed. The playback end is read through its loopback, which needs
no permission. That is the route this check takes.

## What it needs

- A virtual cable installed (VB-CABLE), and `pip install sergulath[audio]` (PyAudioWPatch).
- ASTAROT run with a settings folder of its own (`sergulath app start --settings-dir`), so the
  user's settings are not touched. `app start` stops nothing: quit the installed ASTAROT first.

## Steps

1. Start the stand-in speech server (each request gets a 3-second 440 Hz tone whose loudness steps
   up and down; one step in seven is silent):

   ```sh
   sergulath tts serve --port 39610
   ```

2. Start ASTAROT with its own settings, reading sent messages aloud into the cable. The output
   device is written the way ASTAROT stores it, host API and device name as a JSON list (a bare
   name shows as "未接続" on the TTS page with `output_missing_or_ambiguous`):

   ```sh
   sergulath app stop
   sergulath app start --settings-dir records/tts-check --fresh --set "VOICE_SETTINGS={\"mode\": \"original\", \"japanese_server\": \"external\", \"japanese_url\": \"http://127.0.0.1:39610\", \"output_device\": \"[\\\"Windows WASAPI\\\", \\\"CABLE Input (VB-Audio Virtual Cable)\\\"]\"}"
   ```

3. Turn text to speech on and check the cable is silent:

   ```sh
   sergulath app press "テキスト読み上げ（TTS）"
   sergulath audio check --device "CABLE Input" --seconds 3 --expect silence
   ```

4. Listen while a message is sent and read aloud (the command after `--` runs once listening has
   begun):

   ```sh
   sergulath audio check --device "CABLE Input" --seconds 8 --save records/tts-check/heard.wav -- sergulath app type --field "メッセージ" --text "読み上げの確認です" --then "メッセージを送信"
   ```

   Exit 0 means sound reached the cable: at least 300 ms of 20 ms frames above -50 dBFS.

5. Mute ASTAROT, send again, and check nothing reaches the cable; then unmute:

   ```sh
   sergulath app press "ミュート"
   sergulath audio check --device "CABLE Input" --seconds 8 --expect silence -- sergulath app type --field "メッセージ" --text "ミュート中の確認" --then "メッセージを送信"
   sergulath app press "ミュート"
   ```

6. Stop the app and the server, and start the installed app again (`sergulath app stop`,
   `sergulath app start`).

## Result on 2026-11-23 (this Windows PC, ASTAROT 0.6.0-beta.2, VB-CABLE)

| Step | Result |
|---|---|
| Before speaking | silence: RMS -96.7 dBFS, peak -90.3 dBFS (the cable's floor) |
| A sent message read aloud | sound: 2620 ms above -50 dBFS (longest 880 ms), RMS -23.0 dBFS, peak -10.7 dBFS. The stand-in's tone is 3 s with one silent step in seven (2.57 s sounding) at a peak of 0.8 x 12000, -10.7 dBFS: what arrived is what the server sent |
| Muted, a message sent | silence (the app did not even ask the server to speak) |

The first try used the bare device name and stayed silent: the TTS page showed the output as
"未接続" (not connected) with `output_missing_or_ambiguous`, and the server was still asked to
speak. Choosing the device on the page stored `["Windows WASAPI", "CABLE Input (VB-Audio Virtual
Cable)"]`, the form step 2 now writes.
