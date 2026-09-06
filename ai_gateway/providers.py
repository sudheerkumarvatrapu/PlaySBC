"""Provider-neutral asynchronous conversation contract for AI voice calls."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional, Protocol

from .rasa import RasaBotResponse, RasaRestClient


@dataclass(frozen=True)
class ConversationRequest:
    sender: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConversationChunk:
    response: RasaBotResponse
    index: int
    final: bool


class ConversationProvider(Protocol):
    """Streams ordered bot responses without exposing provider transport details."""

    def stream(self, request: ConversationRequest) -> AsyncIterator[ConversationChunk]: ...


class ConversationProviderError(RuntimeError):
    """Base error for a provider stream that cannot complete deterministically."""


class ConversationProviderTimeout(ConversationProviderError):
    """Raised when a provider stream exceeds its configured turn deadline."""


class ConversationProviderCancelled(ConversationProviderError):
    """Raised when call control interrupts a provider stream."""


class RasaConversationProvider:
    def __init__(self, client: RasaRestClient):
        self.client = client

    async def stream(self, request: ConversationRequest) -> AsyncIterator[ConversationChunk]:
        responses = await self.client.send_message_async(request.sender, request.text, request.metadata)
        for index, response in enumerate(responses, start=1):
            yield ConversationChunk(response=response, index=index, final=index == len(responses))


async def _collect_ordered_responses(
    provider: ConversationProvider,
    request: ConversationRequest,
) -> list[RasaBotResponse]:
    responses: list[RasaBotResponse] = []
    expected_index = 1
    saw_final = False
    async for chunk in provider.stream(request):
        if saw_final:
            raise ValueError("conversation provider emitted data after the final chunk")
        if chunk.index != expected_index:
            raise ValueError(
                f"conversation provider chunk index {chunk.index} does not match expected {expected_index}"
            )
        responses.append(chunk.response)
        expected_index += 1
        saw_final = chunk.final
    if responses and not saw_final:
        raise ValueError("conversation provider stream ended without a final chunk")
    return responses


async def collect_responses(
    provider: ConversationProvider,
    request: ConversationRequest,
    *,
    timeout_seconds: Optional[float] = None,
    cancellation_event: Optional[asyncio.Event] = None,
) -> list[RasaBotResponse]:
    """Collect one ordered provider turn with an overall deadline and call cancellation."""

    if cancellation_event and cancellation_event.is_set():
        raise ConversationProviderCancelled("conversation provider turn was interrupted")

    collector = asyncio.create_task(_collect_ordered_responses(provider, request))
    cancellation_waiter = (
        asyncio.create_task(cancellation_event.wait()) if cancellation_event is not None else None
    )
    waiters = {collector}
    if cancellation_waiter is not None:
        waiters.add(cancellation_waiter)

    try:
        done, _pending = await asyncio.wait(
            waiters,
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancellation_waiter is not None and cancellation_waiter in done:
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await collector
            raise ConversationProviderCancelled("conversation provider turn was interrupted")
        if collector in done:
            try:
                return collector.result()
            except asyncio.CancelledError:
                raise
            except ConversationProviderError:
                raise
            except Exception as exc:
                raise ConversationProviderError(
                    f"conversation provider failed: {type(exc).__name__}: {exc}"
                ) from exc

        collector.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await collector
        raise ConversationProviderTimeout(
            f"conversation provider exceeded {float(timeout_seconds or 0):.3f}s deadline"
        )
    finally:
        if not collector.done():
            collector.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await collector
        if cancellation_waiter is not None and not cancellation_waiter.done():
            cancellation_waiter.cancel()
        if cancellation_waiter is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await cancellation_waiter
