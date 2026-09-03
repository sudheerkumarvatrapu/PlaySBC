#!/usr/bin/env python3
"""Focused pre-v6 foundation and RFC 5359 regression profiles."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import AsyncIterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_gateway import AiVoiceConfig, AiVoiceGateway, ConversationChunk, ConversationRequest
from ai_gateway.rasa import RasaBotResponse
from sip.business_services import ConsultationHold, ConsultationState


PROFILES = ("ai-provider-streaming-contract", "rfc5359-consultation-hold")


class DeterministicStreamingProvider:
    async def stream(self, request: ConversationRequest) -> AsyncIterator[ConversationChunk]:
        responses = (
            RasaBotResponse(text=f"accepted:{request.text}"),
            RasaBotResponse(text="foundation-ready"),
        )
        for index, response in enumerate(responses, start=1):
            yield ConversationChunk(response=response, index=index, final=index == len(responses))


async def run_ai_provider_streaming_contract() -> dict[str, object]:
    gateway = AiVoiceGateway(
        AiVoiceConfig(enabled=True, initial_message="support", response_mode="streaming"),
        conversation_provider=DeterministicStreamingProvider(),
    )
    result = await gateway.start_turn("regression-ai-stream", {"callee": "ai-bot"})
    passed = result.rendered_text == "accepted:support foundation-ready" and result.tts_chunk_count == 2
    return {
        "profile": "ai-provider-streaming-contract",
        "passed": passed,
        "response_mode": result.response_mode,
        "response_count": len(result.bot_responses),
        "tts_chunk_count": result.tts_chunk_count,
    }


def run_consultation_hold() -> dict[str, object]:
    service = ConsultationHold("original-dialog")
    service.hold_original()
    service.start_consultation("consultation-dialog", "sip:expert@example.com")
    consulting_preserved = service.original_dialog_preserved and service.state is ConsultationState.CONSULTING
    service.complete_consultation()
    service.resume_original()
    return {
        "profile": "rfc5359-consultation-hold",
        "passed": consulting_preserved and service.state is ConsultationState.COMPLETED,
        "original_call_id": service.original_call_id,
        "consultation_call_id": service.consultation_call_id,
        "state": service.state.value,
    }


async def run_profile(profile: str) -> dict[str, object]:
    if profile == "ai-provider-streaming-contract":
        return await run_ai_provider_streaming_contract()
    return run_consultation_hold()


async def main_async(args: argparse.Namespace) -> int:
    selected = args.profile or list(PROFILES)
    results = [await run_profile(profile) for profile in selected]
    print(json.dumps({"profiles": results}, indent=2, sort_keys=True))
    return 0 if all(bool(result["passed"]) for result in results) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", action="append", choices=PROFILES)
    parser.add_argument("--list-profiles", action="store_true")
    args = parser.parse_args()
    if args.list_profiles:
        for profile in PROFILES:
            print(profile)
        raise SystemExit(0)
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
