"""AI Voice Gateway orchestration primitives.

This first slice is intentionally adapter-based: PlaySBC owns SIP/RTP, while
Rasa owns the conversation brain behind a REST webhook.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .rasa import RasaBotResponse, RasaRestClient, RasaRestConfig, RasaRestError


DEFAULT_DTMF_INTENTS = {
    "0": "agent",
    "1": "sales",
    "2": "support",
    "3": "billing",
    "5": "help",
    "*": "repeat",
    "#": "confirm",
}


@dataclass(frozen=True)
class AiVoiceConfig:
    enabled: bool = False
    provider: str = "rasa"
    bot_name: str = "rasa-support"
    rasa_webhook_url: str = "http://127.0.0.1:5005/webhooks/rest/webhook"
    rasa_timeout: float = 3.0
    initial_message: str = "hello"
    fallback_text: str = "AI assistant is not available right now."
    input_mode: str = "scripted"
    dtmf_intents: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_DTMF_INTENTS))

    @classmethod
    def from_dict(cls, value: Optional[Dict[str, Any]]) -> "AiVoiceConfig":
        raw = dict(value or {})
        intents = raw.get("dtmf_intents")
        if intents is not None and not isinstance(intents, dict):
            raise ValueError("ai_voice_gateway.dtmf_intents must be a mapping")
        return cls(
            enabled=bool(raw.get("enabled", False)),
            provider=str(raw.get("provider", "rasa")).lower(),
            bot_name=str(raw.get("bot_name", "rasa-support")),
            rasa_webhook_url=str(raw.get("rasa_webhook_url", "http://127.0.0.1:5005/webhooks/rest/webhook")),
            rasa_timeout=float(raw.get("rasa_timeout", 3.0)),
            initial_message=str(raw.get("initial_message", "hello")),
            fallback_text=str(raw.get("fallback_text", "AI assistant is not available right now.")),
            input_mode=str(raw.get("input_mode", "scripted")).lower(),
            dtmf_intents={str(key): str(text) for key, text in (intents or DEFAULT_DTMF_INTENTS).items()},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "bot_name": self.bot_name,
            "rasa_webhook_url": self.rasa_webhook_url,
            "rasa_timeout": self.rasa_timeout,
            "initial_message": self.initial_message,
            "fallback_text": self.fallback_text,
            "input_mode": self.input_mode,
            "dtmf_intents": dict(self.dtmf_intents),
        }


@dataclass(frozen=True)
class AiTurnResult:
    sender: str
    user_text: str
    bot_responses: List[RasaBotResponse]
    fallback_used: bool
    error: str
    duration_seconds: float

    @property
    def rendered_text(self) -> str:
        texts = [response.text for response in self.bot_responses if response.text]
        return " ".join(texts) if texts else ""


class DtmfIntentMapper:
    def __init__(self, intents: Optional[Dict[str, str]] = None):
        self.intents = dict(intents or DEFAULT_DTMF_INTENTS)

    def text_for_digits(self, digits: str) -> str:
        cleaned = "".join(character for character in digits if character in "0123456789*#ABCD")
        if not cleaned:
            return ""
        if cleaned in self.intents:
            return self.intents[cleaned]
        return " ".join(self.intents.get(character, f"dtmf {character}") for character in cleaned)


class AiVoiceGateway:
    def __init__(self, config: AiVoiceConfig):
        if config.provider != "rasa":
            raise ValueError(f"Unsupported AI voice provider {config.provider!r}")
        self.config = config
        self.rasa = RasaRestClient(
            RasaRestConfig(
                webhook_url=config.rasa_webhook_url,
                timeout=config.rasa_timeout,
            )
        )
        self.dtmf_mapper = DtmfIntentMapper(config.dtmf_intents)

    def initial_user_text(self, dtmf_digits: str = "") -> str:
        if self.config.input_mode == "dtmf":
            mapped = self.dtmf_mapper.text_for_digits(dtmf_digits)
            return mapped or self.config.initial_message
        return self.config.initial_message

    async def start_turn(
        self,
        sender: str,
        metadata: Optional[Dict[str, Any]] = None,
        dtmf_digits: str = "",
    ) -> AiTurnResult:
        started = time.monotonic()
        user_text = self.initial_user_text(dtmf_digits)
        try:
            responses = await self.rasa.send_message_async(sender, user_text, metadata or {})
            return AiTurnResult(
                sender=sender,
                user_text=user_text,
                bot_responses=responses,
                fallback_used=False,
                error="",
                duration_seconds=time.monotonic() - started,
            )
        except RasaRestError as exc:
            fallback = RasaBotResponse(text=self.config.fallback_text)
            return AiTurnResult(
                sender=sender,
                user_text=user_text,
                bot_responses=[fallback],
                fallback_used=True,
                error=str(exc),
                duration_seconds=time.monotonic() - started,
            )
