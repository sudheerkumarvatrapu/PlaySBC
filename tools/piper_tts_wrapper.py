#!/usr/bin/env python3
"""Generate a WAV with Piper for PlaySBC AI voice gateway tests."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_NAMES = (
    "en_US-lessac-low.onnx",
    "en_US-lessac-medium.onnx",
)


def find_model(explicit: str = "") -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_value = os.environ.get("PLAYSBC_PIPER_MODEL", "")
    if env_value:
        candidates.append(Path(env_value))
    for name in DEFAULT_MODEL_NAMES:
        candidates.extend(
            [
                ROOT / ".models" / "piper" / name,
                Path("/opt/playsbc/models/piper") / name,
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Piper model not found. Run: python3 -m piper.download_voices "
        "en_US-lessac-low --download-dir .models/piper"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="PlaySBC Piper TTS wrapper")
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    model = find_model(args.model)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as text_file:
        text_file.write(args.text.strip() + "\n")
        text_path = Path(text_file.name)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "piper",
                "--model",
                str(model),
                "--input-file",
                str(text_path),
                "--output-file",
                str(output),
            ],
            text=True,
            capture_output=True,
            timeout=45,
        )
    finally:
        text_path.unlink(missing_ok=True)

    if completed.returncode != 0:
        sys.stderr.write(completed.stderr or completed.stdout or f"piper returncode={completed.returncode}\n")
        return completed.returncode
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
