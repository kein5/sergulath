"""sergulath asr: a stand-in speech.cpp server that speaks scripted, speaker-labelled captions.

  asr serve [--port N] [--text TEXT ...] [--speaker LABEL ...] [--every S] [--duration S]

Serves GET /v1/models (a recognition model, and a diarization model when speakers are given)
and the Realtime WebSocket at /v1/realtime. Once an app's session is set up, every S seconds it
sends one utterance as a confirmed caption: the texts in turn, each with the next speaker label
(A, B, ...). It recognises nothing and needs no model, microphone or sound.

The first line on stdout is the server's address ({"base_url": ...}); each utterance sent is a
line after it. Stops after --duration seconds (default: until interrupted).

ASTAROT: point REALTIME_ASR_SETTINGS at it ("server": "external", "base_url": the address,
"model": "haas-ja") and turn on listening (game audio). See docs/checks/speaker-names.md.
"""

from __future__ import annotations

import threading
import time

from .. import output
from ..core.fake_asr import FakeSpeechServer, Script

NAME = "asr"
HELP = "a stand-in speech.cpp server with scripted, speaker-labelled captions"


def add_arguments(parser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    serve = sub.add_parser("serve", help="serve scripted realtime captions")
    serve.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free one)")
    serve.add_argument("--text", action="append", help="an utterance (repeatable; default: two Japanese sentences)")
    serve.add_argument("--speaker", action="append", help="speaker label per utterance, in turn (default: A, B)")
    serve.add_argument("--no-diarization", action="store_true", help="send no speakers and list no diarization model")
    serve.add_argument("--every", type=float, default=4.0, help="seconds between utterances (default 4)")
    serve.add_argument("--model", default="haas-ja", help="the recognition model's id (default haas-ja)")
    serve.add_argument("--duration", type=float, default=0.0, help="stop after this many seconds (default: never)")
    output.add_format(serve)


def run(args) -> int:
    script = Script(
        texts=args.text or ["今日はいい天気ですね。", "このあと一緒にワールドを回りましょう。"],
        speakers=[] if args.no_diarization else (args.speaker or ["A", "B"]),
        every_s=args.every, model=args.model,
    )
    server = FakeSpeechServer(script, port=args.port, log=output.note)
    output.emit({"base_url": server.base_url, "model": script.model, "speakers": script.speakers},
                f"serving {server.base_url} (model {script.model}, speakers {script.speakers or 'none'})", args.format)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    shown = 0
    deadline = time.monotonic() + args.duration if args.duration > 0 else None
    try:
        while deadline is None or time.monotonic() < deadline:
            time.sleep(0.2)
            while shown < len(script.sent):
                sent = script.sent[shown]
                output.emit(sent, f"{sent['time']} {sent['text']} ({sent['speakers'][0]['speaker'] if sent['speakers'] else '-'})",
                            args.format)
                shown += 1
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return output.OK
