"""Listen to an audio device for a few seconds and measure what came out (Windows, WASAPI).

A playback device (speakers, a virtual cable's input such as "CABLE Input") is read through its
loopback: what the PC plays into it. That needs no microphone permission. Reading a recording
device (a microphone, a cable's output such as "CABLE Output") is microphone use: Windows asks
for permission per app, and the user's consent comes first (ground rule 5), so it has to be
asked for by name (`allow_input`). For a virtual cable the two ends carry the same signal, so the
loopback of its input is the way to check what reaches the virtual microphone.

Needs PyAudioWPatch (a PortAudio build with WASAPI loopback): `pip install sergulath[audio]`.
A WASAPI loopback stream delivers nothing while nothing plays; the time it did not deliver counts
as silence.
"""

from __future__ import annotations

import array
import math
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import host

FRAME_MS = 20


@dataclass
class Device:
    index: int
    name: str
    kind: str  # "output" (read through its loopback) or "input"
    channels: int
    rate: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Level:
    device: str
    kind: str
    seconds: float
    rate: int
    rms_dbfs: float
    peak_dbfs: float
    active_ms: int  # 20 ms frames above the threshold
    longest_active_ms: int
    threshold_dbfs: float
    sound: bool
    frames: list[float] = field(default_factory=list, repr=False)  # per-frame dBFS (not in to_dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("frames")
        return data


def _pyaudio():
    host.require_windows("audio", "it captures through WASAPI loopback (PyAudioWPatch)")
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as e:
        raise RuntimeError("audio needs PyAudioWPatch (Windows): pip install sergulath[audio]") from e
    return pyaudio


def devices() -> list[Device]:
    """Playback devices (by their loopbacks) and recording devices on WASAPI."""
    pyaudio = _pyaudio()
    pa = pyaudio.PyAudio()
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        found = []
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d["hostApi"] != wasapi:
                continue
            if d.get("isLoopbackDevice"):
                found.append(Device(i, d["name"].removesuffix(" [Loopback]"), "output", int(d["maxInputChannels"]),
                                    int(d["defaultSampleRate"])))
            elif d["maxInputChannels"] > 0:
                found.append(Device(i, d["name"], "input", int(d["maxInputChannels"]), int(d["defaultSampleRate"])))
        return found
    finally:
        pa.terminate()


def find(name: str, allow_input: bool = False) -> Device:
    """The device whose name contains `name` (case-insensitive); an exact name wins."""
    candidates = [d for d in devices() if name.lower() in d.name.lower()]
    exact = [d for d in candidates if d.name.lower() == name.lower()]
    pick = exact or candidates
    outputs = [d for d in pick if d.kind == "output"]
    if outputs:
        if len(outputs) > 1:
            raise ValueError(f"{name!r} matches several devices: " + ", ".join(d.name for d in outputs))
        return outputs[0]
    inputs = [d for d in pick if d.kind == "input"]
    if inputs and not allow_input:
        raise PermissionError(f"{inputs[0].name!r} is a recording device: reading it is microphone use and needs "
                              "the user's consent (--allow-input); for a virtual cable, read its input's loopback")
    if inputs:
        return inputs[0]
    raise LookupError(f"no audio device matches {name!r}")


def dbfs(value: float) -> float:
    return round(20 * math.log10(value), 1) if value > 0 else -120.0


def measure(samples: array.array, channels: int, rate: int, expected_frames: int, threshold_dbfs: float,
            min_active_ms: int) -> tuple[float, float, list[float]]:
    """Overall RMS and peak (dBFS) and per-20-ms-frame dBFS, padding undelivered time with silence."""
    per_frame = max(1, rate * FRAME_MS // 1000) * channels
    levels = []
    total = 0.0
    peak = 0
    for start in range(0, len(samples), per_frame):
        chunk = samples[start:start + per_frame]
        if not chunk:
            break
        square = sum(v * v for v in chunk)
        total += square
        peak = max(peak, max(abs(v) for v in chunk))
        levels.append(dbfs(math.sqrt(square / len(chunk)) / 32768))
    levels += [-120.0] * max(0, expected_frames - len(levels))
    count = max(1, len(levels) * per_frame)
    return dbfs(math.sqrt(total / count) / 32768), dbfs(peak / 32768), levels


def check(device: Device, seconds: float, threshold_dbfs: float = -50.0, min_active_ms: int = 300,
          save: Path | None = None, during=None) -> Level:
    """Listen for `seconds`. `during`, when given, is called once listening has begun (to make
    the sound being checked, such as sending a message to be read aloud)."""
    pyaudio = _pyaudio()
    pa = pyaudio.PyAudio()
    chunks: list[bytes] = []
    try:
        stream = pa.open(format=pyaudio.paInt16, channels=device.channels, rate=device.rate, input=True,
                         input_device_index=device.index, frames_per_buffer=device.rate // 50,
                         stream_callback=lambda data, *_: (chunks.append(data), (None, pyaudio.paContinue))[1])
        stream.start_stream()
        started = time.monotonic()
        if during:
            during()
        time.sleep(max(0.0, seconds - (time.monotonic() - started)))
        stream.stop_stream()
        stream.close()
    finally:
        pa.terminate()
    raw = b"".join(chunks)
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    expected = int(seconds * 1000 // FRAME_MS)
    rms, peak, levels = measure(samples, device.channels, device.rate, expected, threshold_dbfs, min_active_ms)
    active = [level > threshold_dbfs for level in levels]
    longest = run = 0
    for on in active:
        run = run + 1 if on else 0
        longest = max(longest, run)
    if save:
        save.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(save), "wb") as out:
            out.setnchannels(device.channels)
            out.setsampwidth(2)
            out.setframerate(device.rate)
            out.writeframes(raw)
    active_ms = sum(active) * FRAME_MS
    return Level(device.name, device.kind, seconds, device.rate, rms, peak, active_ms, longest * FRAME_MS,
                 threshold_dbfs, active_ms >= min_active_ms, levels)
