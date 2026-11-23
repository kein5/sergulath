"""The stand-in speech.cpp server, driven by a raw WebSocket client (no SteamVR, no app)."""

import base64
import json
import os
import socket
import struct
import threading
import unittest
import urllib.request

from sergulath.core.fake_asr import FakeSpeechServer, Script, accept_key, frame


def client_frame(payload: bytes) -> bytes:
    """A masked client text frame."""
    mask = os.urandom(4)
    n = len(payload)
    head = bytes([0x81])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def read_server_frame(sock):
    def exact(n):
        data = b""
        while len(data) < n:
            data += sock.recv(n - len(data))
        return data
    b0, b1 = exact(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", exact(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", exact(8))[0]
    return json.loads(exact(n))


class FakeAsrTest(unittest.TestCase):
    def test_the_accept_key_is_rfc_6455s_example(self):
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
        self.assertEqual(frame(b"x" * 200)[:4], bytes([0x81, 126, 0, 200]))

    def test_models_and_a_diarized_caption(self):
        script = Script(texts=["こんにちは。"], speakers=["B"], every_s=0.1)
        server = FakeSpeechServer(script)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            models = json.loads(urllib.request.urlopen(server.base_url + "/v1/models", timeout=5).read())
            self.assertEqual([m["speech"]["task"] for m in models["data"]], ["recognition", "diarization"])
            sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
            key = base64.b64encode(os.urandom(16)).decode()
            sock.sendall(("GET /v1/realtime?intent=transcription HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            head = b""
            while b"\r\n\r\n" not in head:
                head += sock.recv(1)
            self.assertIn(accept_key(key).encode(), head)
            self.assertEqual(read_server_frame(sock)["type"], "session.created")
            sock.sendall(client_frame(json.dumps({"type": "session.update", "session": {"audio": {"input": {}}}}).encode()))
            # A large append, as an app sends audio (16-bit length frame).
            sock.sendall(client_frame(json.dumps({"type": "input_audio_buffer.append", "audio": "A" * 2000}).encode()))
            kinds = []
            while not kinds or kinds[-1]["type"] != "conversation.item.input_audio_transcription.completed":
                kinds.append(read_server_frame(sock))
            self.assertEqual(kinds[0]["type"], "session.updated")
            done = kinds[-1]
            self.assertEqual(done["transcript"], "こんにちは。")
            self.assertEqual(done["speakers"][0]["speaker"], "B")
            sock.close()
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
