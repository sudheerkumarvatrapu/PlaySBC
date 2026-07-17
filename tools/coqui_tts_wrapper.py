#!/usr/bin/env python3
"""Generate a WAV with Coqui TTS for PlaySBC AI voice gateway tests."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_gateway.speech import lab_speech_pcm, write_wav  # noqa: E402


DEFAULT_MODEL = "tts_models/en/ljspeech/tacotron2-DDC"


def write_lab_fallback(text: str, output: Path) -> None:
    seconds = min(8.0, max(1.0, len(text.split()) / 3.0))
    write_wav(output, lab_speech_pcm(text, seconds=seconds))


def main() -> int:
    parser = argparse.ArgumentParser(description="PlaySBC Coqui TTS wrapper")
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=os.environ.get("PLAYSBC_COQUI_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--allow-lab-fallback",
        action="store_true",
        help="Generate deterministic lab speech when Coqui TTS is not installed.",
    )
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        from TTS.api import TTS  # type: ignore
    except Exception as exc:
        if args.allow_lab_fallback:
            write_lab_fallback(args.text, output)
            print(str(output))
            return 0
        sys.stderr.write(f"coqui import failed: {exc}\n")
        return 2

    try:
        tts = TTS(model_name=args.model)
        tts.tts_to_file(text=args.text, file_path=str(output))
    except Exception as exc:
        if args.allow_lab_fallback:
            write_lab_fallback(args.text, output)
            print(str(output))
            return 0
        sys.stderr.write(f"coqui synthesis failed: {exc}\n")
        return 3

    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
