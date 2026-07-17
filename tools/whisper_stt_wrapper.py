#!/usr/bin/env python3
"""Transcribe a WAV with Whisper for PlaySBC AI voice gateway tests."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_gateway.speech import transcript_sidecar  # noqa: E402


def fallback_transcript(path: Path, explicit: str = "") -> str:
    return (explicit or transcript_sidecar(path) or "I need support").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="PlaySBC Whisper STT wrapper")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--model", default=os.environ.get("PLAYSBC_WHISPER_MODEL", "base"))
    parser.add_argument("--language", default=os.environ.get("PLAYSBC_WHISPER_LANGUAGE", "en"))
    parser.add_argument("--fallback-transcript", default="")
    parser.add_argument(
        "--allow-lab-fallback",
        action="store_true",
        help="Return the PCAP sidecar transcript when Whisper is not installed.",
    )
    args = parser.parse_args()

    audio = Path(args.audio)
    try:
        import whisper  # type: ignore
    except Exception as exc:
        if args.allow_lab_fallback:
            print(fallback_transcript(audio, args.fallback_transcript))
            return 0
        sys.stderr.write(f"whisper import failed: {exc}\n")
        return 2

    try:
        model = whisper.load_model(args.model)
        result = model.transcribe(str(audio), language=args.language)
    except Exception as exc:
        if args.allow_lab_fallback:
            print(fallback_transcript(audio, args.fallback_transcript))
            return 0
        sys.stderr.write(f"whisper transcription failed: {exc}\n")
        return 3

    transcript = str(result.get("text") or "").strip()
    if not transcript and args.allow_lab_fallback:
        transcript = fallback_transcript(audio, args.fallback_transcript)
    print(transcript)
    return 0 if transcript else 4


if __name__ == "__main__":
    raise SystemExit(main())
