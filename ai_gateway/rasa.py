"""Small Rasa REST channel client used by the PlaySBC AI Voice Gateway."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class RasaRestError(RuntimeError):
    """Raised when the Rasa REST channel is unreachable or returns invalid data."""


@dataclass(frozen=True)
class RasaRestConfig:
    webhook_url: str = "http://127.0.0.1:5005/webhooks/rest/webhook"
    timeout: float = 3.0
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RasaBotResponse:
    text: str = ""
    image: str = ""
    custom: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "RasaBotResponse":
        custom = payload.get("custom") if isinstance(payload.get("custom"), dict) else None
        return cls(
            text=str(payload.get("text") or ""),
            image=str(payload.get("image") or ""),
            custom=custom,
            raw=dict(payload),
        )


class RasaRestClient:
    """Client for Rasa's REST input channel.

    The official Rasa REST channel accepts a JSON request with sender, message,
    and optional metadata, then returns a JSON list of bot response objects.
    """

    def __init__(self, config: RasaRestConfig):
        if not config.webhook_url:
            raise ValueError("Rasa webhook URL must not be empty")
        if config.timeout <= 0:
            raise ValueError("Rasa timeout must be greater than zero")
        self.config = config

    def send_message(
        self,
        sender: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[RasaBotResponse]:
        payload: Dict[str, Any] = {"sender": sender, "message": message}
        if metadata:
            payload["metadata"] = metadata
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", **self.config.headers}
        request = urllib.request.Request(self.config.webhook_url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                status = int(response.status)
                response_body = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RasaRestError(f"Rasa REST request failed: {exc}") from exc

        if status < 200 or status >= 300:
            raise RasaRestError(f"Rasa REST returned HTTP {status}")
        try:
            decoded = json.loads(response_body or "[]")
        except json.JSONDecodeError as exc:
            raise RasaRestError(f"Rasa REST returned invalid JSON: {exc}") from exc
        if isinstance(decoded, dict):
            decoded = [decoded]
        if not isinstance(decoded, list):
            raise RasaRestError("Rasa REST response must be a list or object")
        responses = []
        for item in decoded:
            if isinstance(item, dict):
                responses.append(RasaBotResponse.from_payload(item))
        return responses

    async def send_message_async(
        self,
        sender: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[RasaBotResponse]:
        return await asyncio.to_thread(self.send_message, sender, message, metadata)
