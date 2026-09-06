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
from sip.business_services import (
    CallForwarding,
    CallTransfer,
    ConsultationHold,
    ConsultationState,
    ForwardCondition,
    ForwardingRule,
    HoldState,
    MusicOnHold,
    TransferKind,
    TransferState,
)


PROFILES = (
    "ai-provider-streaming-contract",
    "ai-provider-interruption-fallback",
    "rfc5359-consultation-hold",
    "rfc5359-consultation-failure-recovery",
    "rfc5359-music-on-hold",
    "rfc5359-unattended-transfer",
    "rfc5359-attended-transfer",
    "rfc5359-call-forwarding",
)


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


async def run_ai_provider_interruption_fallback() -> dict[str, object]:
    class StalledProvider:
        def __init__(self):
            self.started = asyncio.Event()

        async def stream(self, _request: ConversationRequest) -> AsyncIterator[ConversationChunk]:
            self.started.set()
            await asyncio.Event().wait()
            yield ConversationChunk(response=RasaBotResponse(text="unreachable"), index=1, final=True)

    timeout_gateway = AiVoiceGateway(
        AiVoiceConfig(
            enabled=True,
            initial_message="support",
            provider_timeout=0.01,
            fallback_text="provider fallback",
        ),
        conversation_provider=StalledProvider(),
    )
    timeout_result = await timeout_gateway.start_turn("regression-ai-timeout", {})

    provider = StalledProvider()
    cancellation = asyncio.Event()
    interruption_gateway = AiVoiceGateway(
        AiVoiceConfig(enabled=True, initial_message="support", provider_timeout=1.0),
        conversation_provider=provider,
    )
    turn = asyncio.create_task(
        interruption_gateway.start_turn(
            "regression-ai-interruption",
            {},
            cancellation_event=cancellation,
        )
    )
    await asyncio.wait_for(provider.started.wait(), timeout=0.25)
    cancellation.set()
    interruption_result = await asyncio.wait_for(turn, timeout=0.25)

    passed = (
        timeout_result.fallback_used
        and timeout_result.error_code == "provider_timeout"
        and timeout_result.rendered_text == "provider fallback"
        and interruption_result.interrupted
        and interruption_result.error_code == "provider_cancelled"
        and interruption_result.tts_chunk_count == 0
    )
    return {
        "profile": "ai-provider-interruption-fallback",
        "passed": passed,
        "timeout_error_code": timeout_result.error_code,
        "timeout_fallback_used": timeout_result.fallback_used,
        "interruption_error_code": interruption_result.error_code,
        "interruption_fallback_used": interruption_result.fallback_used,
        "interruption_tts_chunk_count": interruption_result.tts_chunk_count,
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


def run_consultation_failure_recovery() -> dict[str, object]:
    failed_consultation = ConsultationHold("original-recovery-dialog")
    failed_consultation.hold_original()
    failed_consultation.start_consultation(
        "failed-consultation-dialog",
        "sip:busy-expert@example.com",
    )
    failed_consultation.fail_consultation("486 Busy Here")
    recovering_state = failed_consultation.state.value
    failed_consultation.resume_original()

    terminated_original = ConsultationHold("original-terminated-dialog")
    terminated_original.hold_original()
    terminated_original.start_consultation(
        "racing-consultation-dialog",
        "sip:expert@example.com",
    )
    terminated_original.terminate_original("BYE received")
    terminated_original.terminate_original("duplicate BYE")

    passed = (
        recovering_state == ConsultationState.RECOVERING.value
        and failed_consultation.state is ConsultationState.RECOVERED
        and failed_consultation.failure_reason == "486 Busy Here"
        and failed_consultation.original_dialog_preserved
        and terminated_original.state is ConsultationState.TERMINATED
        and terminated_original.termination_reason == "BYE received"
        and not terminated_original.original_dialog_preserved
    )
    return {
        "profile": "rfc5359-consultation-failure-recovery",
        "passed": passed,
        "failure_reason": failed_consultation.failure_reason,
        "recovery_state": failed_consultation.state.value,
        "race_state": terminated_original.state.value,
        "termination_reason": terminated_original.termination_reason,
    }


def run_music_on_hold() -> dict[str, object]:
    service = MusicOnHold("moh-dialog")
    service.hold("sendonly")
    service.start_music("sip:moh@media.example.com")
    active_state = service.state.value
    service.stop_music()
    service.resume()
    return {
        "profile": "rfc5359-music-on-hold",
        "passed": active_state == HoldState.MUSIC_ACTIVE.value and service.state is HoldState.RESUMED,
        "active_state": active_state,
        "final_state": service.state.value,
        "source": service.source,
    }


def run_unattended_transfer() -> dict[str, object]:
    service = CallTransfer("unattended-original")
    target = service.request("<sip:transfer-target@example.com>")
    service.accept()
    service.notify(100, "Trying")
    service.notify(200, "OK")
    return {
        "profile": "rfc5359-unattended-transfer",
        "passed": target.kind is TransferKind.UNATTENDED and service.state is TransferState.COMPLETED,
        "kind": target.kind.value,
        "notify_statuses": service.notify_statuses,
        "state": service.state.value,
        "target": target.uri,
    }


def run_attended_transfer() -> dict[str, object]:
    service = CallTransfer("attended-original")
    target = service.request(
        "<sip:transfer-target@example.com?Replaces=consult-dialog%3Bto-tag%3Dcallee"
        "%3Bfrom-tag%3Dcaller>"
    )
    service.accept()
    service.notify(486, "Busy Here")
    failed_state = service.state.value
    service.recover_original()
    return {
        "profile": "rfc5359-attended-transfer",
        "passed": (
            target.kind is TransferKind.ATTENDED
            and target.replaces_call_id == "consult-dialog"
            and failed_state == TransferState.FAILED.value
            and service.state is TransferState.RECOVERED
        ),
        "failed_state": failed_state,
        "final_state": service.state.value,
        "kind": target.kind.value,
        "replaces_call_id": target.replaces_call_id,
    }


def run_call_forwarding() -> dict[str, object]:
    service = CallForwarding(
        (
            ForwardingRule("unconditional", "1001", "sip:2001@example.com"),
            ForwardingRule("busy", "1002", "2002", ForwardCondition.BUSY),
            ForwardingRule("no-answer", "1003", "2003", ForwardCondition.NO_ANSWER),
        )
    )
    unconditional = service.select("1001")
    busy = service.select("1002", status=486)
    no_answer = service.select("1003", timed_out=True)
    return {
        "profile": "rfc5359-call-forwarding",
        "passed": (
            unconditional is not None
            and unconditional.target == "sip:2001@example.com"
            and busy is not None
            and busy.target == "2002"
            and no_answer is not None
            and no_answer.target == "2003"
        ),
        "unconditional_target": unconditional.target if unconditional else "",
        "busy_target": busy.target if busy else "",
        "no_answer_target": no_answer.target if no_answer else "",
    }


async def run_profile(profile: str) -> dict[str, object]:
    if profile == "ai-provider-streaming-contract":
        return await run_ai_provider_streaming_contract()
    if profile == "ai-provider-interruption-fallback":
        return await run_ai_provider_interruption_fallback()
    if profile == "rfc5359-consultation-hold":
        return run_consultation_hold()
    if profile == "rfc5359-consultation-failure-recovery":
        return run_consultation_failure_recovery()
    if profile == "rfc5359-music-on-hold":
        return run_music_on_hold()
    if profile == "rfc5359-unattended-transfer":
        return run_unattended_transfer()
    if profile == "rfc5359-attended-transfer":
        return run_attended_transfer()
    return run_call_forwarding()


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
