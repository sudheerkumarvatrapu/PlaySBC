#!/usr/bin/env python3
"""Check RTPengine NG control reachability."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rtp.rtpengine import RtpengineClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Ping RTPengine NG control")
    parser.add_argument("--url", default="udp://127.0.0.1:2223", help="RTPengine NG control URL")
    parser.add_argument("--timeout", type=float, default=1.0, help="Seconds to wait for a ping response")
    args = parser.parse_args()

    try:
        response = asyncio.run(RtpengineClient(args.url, timeout=args.timeout).ping())
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        print(f"RTPengine BLOCKED: {args.url} is not reachable ({detail})")
        return 1

    result = str(response.get("result", "")).lower()
    if result not in {"ok", "pong"}:
        print(f"RTPengine BLOCKED: unexpected response from {args.url}: {response}")
        return 1

    print(f"RTPengine OK: {args.url} replied with result={response.get('result')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
