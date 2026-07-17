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
from pathlib import Path
from typing import Optional, Sequence

from .speech import synthesize_lab_tts_to_rtp, wav_to_rtp_packets, write_rtp_pcap


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
    rtp_path: str = ""
    error: str = ""
    chunk_index: int = 1
    chunk_count: int = 1


class SpeechToTextAdapter:
    def __init__(self, provider: str, command: str = ""):
        self.provider = provider
        self.command = command

    async def transcribe(self, fallback_text: str, audio_path: str = "") -> SttResult:
        started = time.monotonic()
        provider = self.provider.lower()
        if provider in {"", "scripted", "lab-scripted"}:
            return SttResult("lab-scripted", fallback_text, bool(audio_path), True, time.monotonic() - started)

        if provider not in {"whisper", "vosk"}:
            return SttResult(provider, fallback_text, False, False, time.monotonic() - started, "unsupported_stt_provider")

        if not self.command:
            return SttResult(
                provider,
                fallback_text,
                bool(audio_path),
                False,
                time.monotonic() - started,
                "stt_command_not_configured; used_lab_transcript" if audio_path else "stt_command_not_configured",
            )

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

    async def synthesize(
        self,
        text: str,
        output_path: str = "",
        rtp_path: str = "",
        codec: str = "PCMU",
        chunk_index: int = 1,
        chunk_count: int = 1,
    ) -> TtsResult:
        started = time.monotonic()
        provider = self.provider.lower()
        if provider in {"", "text-only", "lab-text"}:
            return TtsResult(
                "text-only",
                text,
                False,
                False,
                True,
                time.monotonic() - started,
                chunk_index=chunk_index,
                chunk_count=chunk_count,
            )

        if provider not in {"piper", "coqui"}:
            return TtsResult(
                provider,
                text,
                False,
                False,
                False,
                time.monotonic() - started,
                error="unsupported_tts_provider",
                chunk_index=chunk_index,
                chunk_count=chunk_count,
            )

        if not self.command:
            if output_path and rtp_path:
                prompt = synthesize_lab_tts_to_rtp(text, Path(output_path), Path(rtp_path), codec=codec)
                return TtsResult(
                    provider,
                    text,
                    True,
                    True,
                    False,
                    time.monotonic() - started,
                    audio_path=prompt.wav_path,
                    rtp_path=prompt.rtp_pcap_path,
                    error="tts_command_not_configured; used_lab_fallback",
                    chunk_index=chunk_index,
                    chunk_count=chunk_count,
                )
            return TtsResult(
                provider,
                text,
                False,
                False,
                False,
                time.monotonic() - started,
                error="tts_command_not_configured",
                chunk_index=chunk_index,
                chunk_count=chunk_count,
            )

        command = format_engine_command(self.command, text=text, audio_path=output_path)
        try:
            completed = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(completed.communicate(), timeout=30.0)
        except Exception as exc:  # pragma: no cover - depends on local engine binaries.
            return TtsResult(
                provider,
                text,
                False,
                False,
                False,
                time.monotonic() - started,
                error=str(exc),
                chunk_index=chunk_index,
                chunk_count=chunk_count,
            )

        if completed.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip() or f"returncode={completed.returncode}"
            return TtsResult(
                provider,
                text,
                False,
                False,
                False,
                time.monotonic() - started,
                error=detail,
                chunk_index=chunk_index,
                chunk_count=chunk_count,
            )

        audio_path = output_path or stdout.decode("utf-8", errors="replace").strip()
        rtp_generated = False
        if audio_path and rtp_path:
            try:
                packets = wav_to_rtp_packets(Path(audio_path), codec=codec)
                write_rtp_pcap(Path(rtp_path), packets)
                rtp_generated = True
            except Exception as exc:  # pragma: no cover - depends on external TTS output.
                return TtsResult(
                    provider,
                    text,
                    bool(audio_path),
                    False,
                    True,
                    time.monotonic() - started,
                    audio_path=audio_path,
                    error=f"tts_rtp_prompt_failed: {exc}",
                    chunk_index=chunk_index,
                    chunk_count=chunk_count,
                )
        return TtsResult(
            provider,
            text,
            bool(audio_path),
            rtp_generated,
            True,
            time.monotonic() - started,
            audio_path=audio_path,
            rtp_path=rtp_path if rtp_generated else "",
            chunk_index=chunk_index,
            chunk_count=chunk_count,
        )


def format_engine_command(command: str, *, text: str, audio_path: str = "") -> Sequence[str]:
    return [
        part.format(text=text, audio_path=audio_path)
        for part in shlex.split(command)
    ]
