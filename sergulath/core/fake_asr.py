"""A stand-in for a speech.cpp server: its model list and its Realtime WebSocket (stdlib only).

An app that takes realtime captions from a speech.cpp server (ASTAROT: REALTIME_ASR_SETTINGS
with "server": "external") can be pointed at this instead. It recognises nothing: once a
session is set up it sends scripted utterances on a timer, each as a confirmed caption with the
voices a diarizing server would name (`speakers`). That makes a caption path checkable with no
model, no microphone and no sound, the same way every time.

Protocol, as speech.cpp's server and ASTAROT's own test fake (native/core/tests/common/fake_asr.rs)
speak it: GET /v1/models; GET /v1/realtime upgraded to a WebSocket; the server sends
`session.created`, answers `session.update` with `session.updated`, and announces an utterance as
`input_audio_buffer.speech_started`, `conversation.item.input_audio_transcription.delta` and
`...completed` (with "speakers": [{"speaker": "A", "start_ms": 0, "end_ms": 900}, ...]).
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


@dataclass
class Script:
    """What the stand-in says: utterances in turn, one every `every_s` seconds."""
    texts: list[str]
    speakers: list[str]  # one label per utterance, in turn ("A", "B", ...); empty: no diarization
    every_s: float = 4.0
    model: str = "haas-ja"
    diarization_model: str = "nemotron-3-diarization"
    language: str = "ja"
    sent: list[dict] = field(default_factory=list)  # what went out, for the record

    def utterance(self, n: int) -> tuple[str, list[dict] | None]:
        text = self.texts[n % len(self.texts)]
        if not self.speakers:
            return text, None
        label = self.speakers[n % len(self.speakers)]
        return text, [{"speaker": label, "start_ms": 0, "end_ms": 1500}]

    def models(self) -> dict:
        rows = [{"id": self.model, "speech": {"task": "recognition", "languages": [self.language],
                                               "options": [{"name": "language", "default": self.language,
                                                            "choices": [self.language]}]}}]
        if self.speakers:
            rows.append({"id": self.diarization_model, "speech": {"task": "diarization", "device": "Vulkan0"}})
        return {"data": rows}


# ---- WebSocket framing (RFC 6455, the parts a client of this server uses) -----------------------

def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """A final, unmasked server frame."""
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


def _exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


def read_frame(sock: socket.socket) -> tuple[int, bytes]:
    """One frame from a client: (opcode, unmasked payload)."""
    b0, b1 = _exact(sock, 2)
    opcode, masked, n = b0 & 0x0F, b1 & 0x80, b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", _exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _exact(sock, 8))[0]
    mask = _exact(sock, 4) if masked else b"\0\0\0\0"
    payload = bytearray(_exact(sock, n))
    for i in range(n):
        payload[i] ^= mask[i % 4]
    return opcode, bytes(payload)


# ---- the server -----------------------------------------------------------------------------------

class FakeSpeechServer:
    def __init__(self, script: Script, host: str = "127.0.0.1", port: int = 0, log=None):
        self.script = script
        self.log = log or (lambda message: None)
        self.listener = socket.create_server((host, port))
        self.port = self.listener.getsockname()[1]
        self.stopped = threading.Event()
        self.utterances = 0
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def serve_forever(self) -> None:
        self.listener.settimeout(0.5)
        while not self.stopped.is_set():
            try:
                conn, _ = self.listener.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def stop(self) -> None:
        self.stopped.set()
        self.listener.close()

    def _handle(self, conn: socket.socket) -> None:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
            text = head.split(b"\r\n\r\n", 1)[0].decode("latin-1")
            line = text.split("\r\n", 1)[0]
            headers = {k.strip().lower(): v.strip() for k, _, v in
                       (h.partition(":") for h in text.split("\r\n")[1:]) if k}
            method, target = (line.split(" ") + ["", ""])[:2]
            path = target.split("?", 1)[0]
            if method == "GET" and path == "/v1/realtime" and "sec-websocket-key" in headers:
                self.log(f"realtime session from {target}")
                return self._realtime(conn, headers["sec-websocket-key"])
            body = json.dumps(self.script.models() if (method, path) == ("GET", "/v1/models") else {}).encode()
            status = "200 OK" if (method, path) == ("GET", "/v1/models") else "404 Not Found"
            conn.sendall((f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                          "Connection: close\r\n\r\n").encode() + body)
        except (OSError, ConnectionError):
            pass
        finally:
            conn.close()

    def _realtime(self, conn: socket.socket, key: str) -> None:
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n").encode())
        lock = threading.Lock()
        events = iter(range(1, 1 << 30))
        open_ = threading.Event()
        open_.set()

        def send(kind: str, **extra) -> bool:
            value = {"event_id": f"event_{next(events)}", "type": kind, **extra}
            try:
                with lock:
                    conn.sendall(frame(json.dumps(value, ensure_ascii=False).encode()))
                return True
            except OSError:
                open_.clear()
                return False

        def speak() -> None:
            # Utterances on a timer, whether or not audio arrives (a loopback capture is silent
            # while nothing plays, and sends nothing then).
            while open_.is_set() and not self.stopped.is_set():
                if not configured.wait(0.5):
                    continue
                time.sleep(self.script.every_s)
                if not open_.is_set():
                    return
                with self._lock:
                    n = self.utterances
                    self.utterances += 1
                text, speakers = self.script.utterance(n)
                item = f"item_{n + 1}"
                done = {"item_id": item, "transcript": text, "stop": "complete", "languages": [{"code": self.script.language}]}
                if speakers is not None:
                    done["speakers"] = speakers
                half = text[: len(text) // 2]
                ok = (send("input_audio_buffer.speech_started", item_id=item)
                      and send("conversation.item.input_audio_transcription.delta", item_id=item, delta=half)
                      and send("conversation.item.input_audio_transcription.completed", **done))
                if ok:
                    self.script.sent.append({"time": time.strftime("%H:%M:%S"), "text": text, "speakers": speakers})
                    self.log(f"said {text!r} as {speakers[0]['speaker'] if speakers else '-'}")

        configured = threading.Event()
        send("session.created")
        threading.Thread(target=speak, daemon=True).start()
        try:
            while open_.is_set() and not self.stopped.is_set():
                opcode, payload = read_frame(conn)
                if opcode == 0x8:  # close
                    break
                if opcode == 0x9:  # ping
                    with lock:
                        conn.sendall(frame(payload, 0xA))
                    continue
                if opcode != 0x1:
                    continue
                try:
                    message = json.loads(payload)
                except ValueError:
                    continue
                if message.get("type") == "session.update":
                    session = message.get("session", {})
                    session.setdefault("audio", {}).setdefault("input", {}).setdefault("diarization", None)
                    send("session.updated", session=session)
                    configured.set()
        except (OSError, ConnectionError):
            pass
        finally:
            open_.clear()
            self.log("realtime session closed")
