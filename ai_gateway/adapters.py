"""Adapter boundaries for PlaySBC AI voice input and output.

The real engines are intentionally optional. A lab run can stay fully
portable, while a developer can point these adapters at Whisper/Vosk/Piper or
Coqui command wrappers when those binaries are installed locally.
"""

from __future__ import annotations

import asyncio
import shlex
import time
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class SttResult:
    provider: str
    text: str
    audio_decoded: bool
    engine_ready: bool
    duration_seconds: float
    error: str = ""


@dataclass(frozen=True)
class TtsResult:
    provider: str
    text: str
    audio_generated: bool
    rtp_prompt_generated: bool
    engine_ready: bool
    duration_seconds: float
    audio_path: str = ""
    error: str = ""


class SpeechToTextAdapter:
    def __init__(self, provider: str, command: str = ""):
        self.provider = provider
        self.command = command

    async def transcribe(self, fallback_text: str, audio_path: str = "") -> SttResult:
        started = time.monotonic()
        provider = self.provider.lower()
        if provider in {"", "scripted", "lab-scripted"}:
            return SttResult("lab-scripted", fallback_text, False, True, time.monotonic() - started)

        if provider not in {"whisper", "vosk"}:
            return SttResult(provider, fallback_text, False, False, time.monotonic() - started, "unsupported_stt_provider")

        if not self.command:
            return SttResult(provider, fallback_text, False, False, time.monotonic() - started, "stt_command_not_configured")

        command = format_engine_command(self.command, text=fallback_text, audio_path=audio_path)
        try:
            completed = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(completed.communicate(), timeout=30.0)
        except Exception as exc:  # pragma: no cover - depends on local engine binaries.
            return SttResult(provider, fallback_text, False, False, time.monotonic() - started, str(exc))

        if completed.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip() or f"returncode={completed.returncode}"
            return SttResult(provider, fallback_text, False, False, time.monotonic() - started, detail)

        transcript = stdout.decode("utf-8", errors="replace").strip() or fallback_text
        return SttResult(provider, transcript, bool(audio_path), True, time.monotonic() - started)


class TextToSpeechAdapter:
    def __init__(self, provider: str, command: str = ""):
        self.provider = provider
        self.command = command

    async def synthesize(self, text: str, output_path: str = "") -> TtsResult:
        started = time.monotonic()
        provider = self.provider.lower()
        if provider in {"", "text-only", "lab-text"}:
            return TtsResult("text-only", text, False, False, True, time.monotonic() - started)

        if provider not in {"piper", "coqui"}:
            return TtsResult(provider, text, False, False, False, time.monotonic() - started, error="unsupported_tts_provider")

        if not self.command:
            return TtsResult(provider, text, False, False, False, time.monotonic() - started, error="tts_command_not_configured")

        command = format_engine_command(self.command, text=text, audio_path=output_path)
        try:
            completed = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(completed.communicate(), timeout=30.0)
        except Exception as exc:  # pragma: no cover - depends on local engine binaries.
            return TtsResult(provider, text, False, False, False, time.monotonic() - started, error=str(exc))

        if completed.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip() or f"returncode={completed.returncode}"
            return TtsResult(provider, text, False, False, False, time.monotonic() - started, error=detail)

        audio_path = output_path or stdout.decode("utf-8", errors="replace").strip()
        return TtsResult(provider, text, bool(audio_path), False, True, time.monotonic() - started, audio_path=audio_path)


def format_engine_command(command: str, *, text: str, audio_path: str = "") -> Sequence[str]:
    return [
        part.format(text=text, audio_path=audio_path)
        for part in shlex.split(command)
    ]
