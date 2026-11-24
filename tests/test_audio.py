"""The audio level measure and the stand-in TTS server (no sound card needed)."""

import array
import json
import math
import threading
import unittest
import urllib.request

from sergulath.commands import tts
from sergulath.core import audio


class MeasureTest(unittest.TestCase):
    def test_a_tone_then_silence_and_undelivered_time(self):
        rate = 48000
        loud = [round(16384 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate // 2)]  # 0.5 s, -9 dBFS RMS
        samples = array.array("h", loud + [0] * (rate // 2))  # mono, 1 s delivered
        rms, peak, levels = audio.measure(samples, 1, rate, expected_frames=100, threshold_dbfs=-50, min_active_ms=300)
        self.assertEqual(len(levels), 100, "the undelivered second counts as silent frames")
        above = sum(level > -50 for level in levels)
        self.assertEqual(above, 25, "half a second of tone is 25 frames of 20 ms")
        self.assertAlmostEqual(peak, -6.0, delta=0.1)
        self.assertLess(rms, -9.0)

    def test_dbfs(self):
        self.assertEqual(audio.dbfs(1.0), 0.0)
        self.assertEqual(audio.dbfs(0.0), -120.0)


class FakeTtsTest(unittest.TestCase):
    def test_pcm_answer_and_the_requests_it_saw(self):
        seen = []
        pcm = tts.tone(0.1)
        self.assertEqual(len(pcm), 4800 * 2)
        server = tts.make_server(pcm, 0, seen.append)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            self.assertEqual(json.loads(urllib.request.urlopen(base + "/health", timeout=5).read()), {"status": "ok"})
            body = json.dumps({"input": "こんにちは", "response_format": "pcm", "voice": "none"}).encode()
            answer = urllib.request.urlopen(urllib.request.Request(base + "/v1/audio/speech", data=body, method="POST"), timeout=5)
            self.assertEqual(answer.headers["X-Sample-Rate"], "48000")
            self.assertEqual(answer.read(), pcm)
            self.assertEqual(seen[0]["input"], "こんにちは")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
