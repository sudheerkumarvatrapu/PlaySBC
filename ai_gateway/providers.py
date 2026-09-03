"""Provider-neutral asynchronous conversation contract for AI voice calls."""

from __future__ import annotations

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


class RasaConversationProvider:
    def __init__(self, client: RasaRestClient):
        self.client = client

    async def stream(self, request: ConversationRequest) -> AsyncIterator[ConversationChunk]:
        responses = await self.client.send_message_async(request.sender, request.text, request.metadata)
        for index, response in enumerate(responses, start=1):
            yield ConversationChunk(response=response, index=index, final=index == len(responses))


async def collect_responses(
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
