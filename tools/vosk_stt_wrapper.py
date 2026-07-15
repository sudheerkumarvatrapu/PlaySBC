#!/usr/bin/env python3
"""Transcribe a WAV with Vosk for PlaySBC AI voice gateway tests."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_gateway.speech import read_wav_pcm  # noqa: E402

DEFAULT_MODEL_NAMES = ("vosk-model-small-en-us-0.15",)
MODEL_SAMPLE_RATE = 16000


def find_model(explicit: str = "") -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_value = os.environ.get("PLAYSBC_VOSK_MODEL", "")
    if env_value:
        candidates.append(Path(env_value))
    for name in DEFAULT_MODEL_NAMES:
        candidates.extend(
            [
                ROOT / ".models" / "vosk" / name,
                Path("/opt/playsbc/models/vosk") / name,
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Vosk model not found. Download vosk-model-small-en-us-0.15 into .models/vosk"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="PlaySBC Vosk STT wrapper")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel
    except Exception as exc:
        sys.stderr.write(f"vosk import failed: {exc}\n")
        return 2

    model_path = find_model(args.model)
    pcm, sample_rate = read_wav_pcm(Path(args.audio), target_rate=MODEL_SAMPLE_RATE)
    SetLogLevel(-1)
    recognizer = KaldiRecognizer(Model(str(model_path)), sample_rate)
    chunk_size = 4000
    for index in range(0, len(pcm), chunk_size):
        recognizer.AcceptWaveform(pcm[index : index + chunk_size])
    result = json.loads(recognizer.FinalResult() or "{}")

    transcript = str(result.get("text") or "").strip()
    print(transcript)
    return 0 if transcript else 3


if __name__ == "__main__":
    raise SystemExit(main())
