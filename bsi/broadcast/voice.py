"""Narration.

Backends, in order of preference for the channel's health:
  own     the producer's own recorded voice: drop own/scene_01.wav ... into the episode
          folder and re-render. A real human voice is the strongest signal of an
          authentic channel.
  piper   local neural TTS (Piper, runs on CPU, nothing leaves the machine).
          Set BSI_PIPER_VOICE to a .onnx voice model.
  silent  no audio; scene timing from word count. For drafts and tests.
"""
import os
import subprocess
import wave
from pathlib import Path

import numpy as np

RATE = 44100


def backend() -> str:
    b = os.environ.get("BSI_VOICE", "")
    if b:
        return b
    return "piper" if os.environ.get("BSI_PIPER_VOICE") else "silent"


def _to_pcm(path: Path) -> np.ndarray:
    """Any audio file -> mono 44.1 kHz int16 via ffmpeg."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16)


def speak(text: str, out_dir: Path, idx: int, ep_dir: Path) -> tuple[np.ndarray, str]:
    own = ep_dir / "own" / f"scene_{idx:02d}.wav"
    if own.exists():
        return _to_pcm(own), "own"
    b = backend()
    if b == "piper":
        model = os.environ["BSI_PIPER_VOICE"]
        wav = out_dir / f"tts_{idx:02d}.wav"
        speed = os.environ.get("BSI_PIPER_LENGTH_SCALE", "1.08")   # >1 = slower, documentary pacing
        subprocess.run(["python3", "-m", "piper", "-m", model, "-f", str(wav), "--length-scale", speed,
                        "--sentence-silence", "0.35"], input=text.encode(), check=True, capture_output=True)
        return _to_pcm(wav), f"piper:{Path(model).stem}"
    secs = max(2.5, len(text.split()) / 2.6)
    return np.zeros(int(secs * RATE), dtype=np.int16), "silent"


def write_wav(path: Path, pcm: np.ndarray):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.astype(np.int16).tobytes())
