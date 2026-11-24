"""sergulath tts: a stand-in speech.cpp server for an app that reads text aloud.

  tts serve [--port N] [--seconds 3] [--wav FILE] [--duration S]

Answers GET /health, GET /v1/models and POST /v1/audio/speech with response_format "pcm":
signed 16-bit mono PCM at 48 kHz (header X-Sample-Rate), as speech.cpp's server does and as
ASTAROT's TTS client asks (native/tts/src/client.rs). No model runs: each request gets a 440 Hz
tone whose loudness steps up and down (SECONDS long), or the given WAV file (16-bit mono 48 kHz).
The first stdout line is the address; each request answered is a line after it.

ASTAROT: VOICE_SETTINGS "japanese_server": "external", "japanese_url": the address.
"""

from __future__ import annotations

import json
import math
import struct
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import output

NAME = "tts"
HELP = "a stand-in speech.cpp server that answers text to speech with a tone or a WAV file"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    serve = sub.add_parser("serve", help="serve speech requests")
    serve.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free one)")
    serve.add_argument("--seconds", type=float, default=3.0, help="length of the tone (default 3)")
    serve.add_argument("--wav", metavar="FILE", help="answer with this WAV (16-bit mono 48 kHz) instead of a tone")
    serve.add_argument("--duration", type=float, default=0.0, help="stop after this many seconds (default: never)")
    output.add_format(serve)


def tone(seconds: float, rate: int = 48000) -> bytes:
    pcm = bytearray()
    for i in range(int(rate * seconds)):
        t = i / rate
        amp = (0, .5, .2, .8, .1, .6, .3)[int(t * 7) % 7]
        pcm.extend(struct.pack("<h", round(12000 * amp * math.sin(2 * math.pi * 440 * t))))
    return bytes(pcm)


def read_wav(path: str) -> bytes:
    with wave.open(path, "rb") as source:
        if (source.getsampwidth(), source.getnchannels(), source.getframerate()) != (2, 1, 48000):
            raise ValueError("the WAV must be 16-bit mono at 48 kHz")
        return source.readframes(source.getnframes())


def make_server(pcm: bytes, port: int, on_request) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._json(200, {"status": "ok"} if self.path == "/health" else {"data": []})

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if self.path != "/v1/audio/speech" or data.get("response_format") != "pcm":
                return self._json(400, {"error": "only POST /v1/audio/speech with response_format pcm"})
            on_request(data)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "audio/pcm")
                self.send_header("X-Sample-Rate", "48000")
                self.send_header("Content-Length", str(len(pcm)))
                self.end_headers()
                self.wfile.write(pcm)
            except (OSError, ConnectionError):
                pass  # the app stopped reading (a stop or a mute)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def run(args) -> int:
    pcm = read_wav(args.wav) if args.wav else tone(args.seconds)
    requests: list[dict] = []
    server = make_server(pcm, args.port, requests.append)
    url = f"http://127.0.0.1:{server.server_port}"
    output.emit({"base_url": url, "seconds": round(len(pcm) / 2 / 48000, 2)},
                f"serving {url} ({len(pcm) / 2 / 48000:.1f} s of {'the WAV' if args.wav else 'tone'} per request)", args.format)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    shown = 0
    deadline = time.monotonic() + args.duration if args.duration > 0 else None
    try:
        while deadline is None or time.monotonic() < deadline:
            time.sleep(0.2)
            while shown < len(requests):
                r = requests[shown]
                output.emit({"time": time.strftime("%H:%M:%S"), "input": r.get("input"), "voice": r.get("voice")},
                            f"{time.strftime('%H:%M:%S')} speak {r.get('input')!r}", args.format)
                shown += 1
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return output.OK
