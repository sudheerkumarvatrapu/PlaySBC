from __future__ import annotations

from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent


def default_transcript_dir() -> str:
    return ""


def write_transcript(output_dir: str, filename: str, lines: Sequence[str]) -> None:
    if not output_dir:
        return
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text("\n".join(lines), encoding="utf-8")
