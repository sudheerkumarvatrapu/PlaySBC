#!/usr/bin/env python3
"""Check a Rasa REST webhook used by the PlaySBC AI Voice Gateway."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def post_rasa(url: str, sender: str, message: str, timeout: float) -> list[dict[str, Any]]:
    body = json.dumps({"sender": sender, "message": message, "metadata": {"source": "playsbc-check-rasa"}}).encode(
        "utf-8"
    )
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload or "[]")
    if isinstance(decoded, dict):
        decoded = [decoded]
    if not isinstance(decoded, list):
        raise ValueError("Rasa REST response must be a JSON list or object")
    return [item for item in decoded if isinstance(item, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5005/webhooks/rest/webhook")
    parser.add_argument("--sender", default="playsbc-rasa-check")
    parser.add_argument("--message", default="hello from playsbc real rasa")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()

    try:
        responses = post_rasa(args.url, args.sender, args.message, args.timeout)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        print(f"Rasa BLOCKED: {args.url} is not ready ({exc})")
        return 1

    texts = [str(item.get("text", "")) for item in responses if item.get("text")]
    custom_actions = [
        str(item.get("custom", {}).get("playsbc_action") or item.get("custom", {}).get("action"))
        for item in responses
        if isinstance(item.get("custom"), dict)
        and (item.get("custom", {}).get("playsbc_action") or item.get("custom", {}).get("action"))
    ]
    summary = f"responses={len(responses)}"
    if texts:
        summary += f" text={json.dumps(' '.join(texts))}"
    if custom_actions:
        summary += f" actions={','.join(custom_actions)}"
    print(f"Rasa OK: {args.url} {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
