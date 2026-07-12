#!/usr/bin/env python3
"""Tiny mock Rasa REST channel for deterministic AI Voice Gateway regression."""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict


class MockRasaHandler(BaseHTTPRequestHandler):
    reply_template = "PlaySBC Rasa mock heard: {message}"
    delay_seconds = 0.0

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.log_date_time_string()} {format % args}", flush=True)

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self.send_error(404)
            return
        self._write_json({"status": "ok"})

    def do_POST(self) -> None:
        if self.path != "/webhooks/rest/webhook":
            self.send_error(404)
            return
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(content_length).decode("utf-8")
        try:
            payload: Dict[str, Any] = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            self.send_error(400, "invalid JSON")
            return
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        message = str(payload.get("message") or "")
        sender = str(payload.get("sender") or "unknown")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        text = self.reply_template.format(
            sender=sender,
            message=message,
            caller=metadata.get("caller", ""),
            callee=metadata.get("callee", ""),
            call_id=metadata.get("call_id", ""),
        )
        self._write_json([{"recipient_id": sender, "text": text}])

    def _write_json(self, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--reply", default=MockRasaHandler.reply_template)
    parser.add_argument("--delay-seconds", type=float, default=0.0)
    args = parser.parse_args()

    MockRasaHandler.reply_template = args.reply
    MockRasaHandler.delay_seconds = max(0.0, args.delay_seconds)
    server = ThreadingHTTPServer((args.host, args.port), MockRasaHandler)
    print(f"Mock Rasa REST server listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
