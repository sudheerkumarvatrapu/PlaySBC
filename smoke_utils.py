from __future__ import annotations

import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def default_transcript_dir() -> Path:
    run_id = time.strftime("sanity-%Y%m%d-%H%M%S", time.localtime())
    return ROOT / "artifacts" / run_id / "transcripts"
