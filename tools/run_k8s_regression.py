#!/usr/bin/env python3
"""Run PlaySBC Kubernetes SIPp regression profiles and write an HTML report."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "sipp" / "scenarios"
SMOKE_PROFILES = ("options", "register-contact", "b2bua-signalling")

sys.path.insert(0, str(ROOT))
from tools.run_b2bua_sipp_smoke import (  # noqa: E402
    BASE_DEFAULTS,
    B2BUA_PROFILES,
    MEDIA_PCAPS,
    MEDIA_PAYLOAD_TYPES,
    MEDIA_RTPMAP_LINES,
    PROFILE_DESCRIPTIONS,
    call_limit,
    dump_simple_yaml,
    is_transcoding_profile,
    render_srtp_media_scenario,
    render_harness_config_templates,
    srtp_sender_command,
    sipp_timeout_seconds,
    uas_media_codec,
    wait_for_process_log_marker,
)
from tools.run_regression_suite import (  # noqa: E402
    ALL_B2BUA_PROFILES,
    B2BUA_LOG_FILES,
    RASA_B2BUA_PROFILES,
    REAL_TOPOLOGY_PROFILE,
    cleanup_old_reports,
    ReportPhase,
    ReportRow,
    write_reports,
)
from mini_call_server import B2BUAFlowLog, RouteResult, SipUri  # noqa: E402
from tools.real_device_evidence import read_pcap, sip_events  # noqa: E402

DEFAULT_PROFILES = ("basic-signalling", "basic-media", "transcoding", "registered-inbound", "registered-outbound")
RASA_NLU_PROFILES = ("ai-rasa-chat-nlu", "ai-rasa-chat-negative")
PROTOCOL_CORE_PROFILES = (
    "protocol-parser-validation",
    "protocol-uri-validation",
    "protocol-header-grammar",
    "protocol-mime-binary-body",
    "protocol-request-policy",
    "protocol-error-responses",
    "protocol-stream-parser-limits",
    "protocol-rfc4475-corpus",
    "protocol-parser-fuzz-resource",
    "protocol-server-transactions",
    "protocol-client-transactions",
)
PROTOCOL_CORE_LABELS = {
    "protocol-core-layer1-live-call": {
        "title": "Protocol Core Layer 1 Live Call",
        "suite": "Kubernetes Protocol Core Live Calls",
        "mode": "real UDP B2BUA call through compact/folded/extension headers, parameterized URI parsing, SDP, ACK, and BYE",
    },
    "protocol-core-layer2-live-tcp-call": {
        "title": "Protocol Core Layer 2 Live TCP Call",
        "suite": "Kubernetes Protocol Core Live Calls",
        "mode": "real TCP registrar-backed B2BUA call through stream framing, lifecycle handling, and connection reuse",
    },
    "protocol-core-layer2-live-tls-call": {
        "title": "Protocol Core Layer 2 Live TLS Call",
        "suite": "Kubernetes Protocol Core Live Calls",
        "mode": "real TLS 1.2+ registrar-backed B2BUA call through encrypted stream framing, lifecycle handling, and connection reuse",
    },
    "protocol-core-layer2-live-dns-srv-call": {
        "title": "Protocol Core Layer 2 Live DNS SRV Call",
        "suite": "Kubernetes Protocol Core Live Calls",
        "mode": "real TCP B2BUA call using Kubernetes SRV and A/AAAA discovery with bounded candidate selection",
    },
    "protocol-core-layer2-live-dns-udp-call": {
        "title": "Protocol Core Layer 2 Live DNS UDP Call",
        "suite": "Kubernetes Protocol Core Live Calls",
        "mode": "real UDP B2BUA call through Kubernetes DNS A/AAAA resolution",
    },
    "protocol-core-layer2-live-rport-call": {
        "title": "Protocol Core Layer 2 Live RFC 3581 Call",
        "suite": "Kubernetes Protocol Core Live Calls",
        "mode": "real UDP B2BUA call proving received and rport response routing from a mismatched Via sent-by",
    },
    "protocol-core-layer2-live-idle-timeout": {"title": "Protocol Core Layer 2 Live Idle Timeout", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real TCP call plus idle socket expiry and cleanup"},
    "protocol-core-layer2-live-half-close": {"title": "Protocol Core Layer 2 Live Half Close", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real TCP call plus partial-frame half-close cleanup"},
    "protocol-core-layer2-live-pool-limit": {"title": "Protocol Core Layer 2 Live Pool Limit", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real TCP call plus bounded concurrent connection rejection"},
    "protocol-core-layer2-live-tls-sni": {"title": "Protocol Core Layer 2 Live TLS SNI", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real TLS call plus accepted and rejected SNI identity handshakes"},
    "protocol-core-layer2-live-tls-rotation": {"title": "Protocol Core Layer 2 Live TLS Rotation", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real TLS call plus in-place certificate rotation observed by a fresh handshake"},
    "protocol-core-layer3-live-client-transactions": {"title": "Protocol Core Layer 3 Live Client Transactions", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real B2BUA call with outbound INVITE and BYE UAC transaction state"},
    "protocol-core-layer3-live-non2xx-ack": {"title": "Protocol Core Layer 3 Live Non-2xx ACK", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real rejected INVITE with transaction-layer ACK generation"},
    "protocol-core-layer3-live-cancel": {"title": "Protocol Core Layer 3 Live CANCEL", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real pending INVITE cancellation with 200, 487, ACK and bounded cleanup"},
    "protocol-core-layer3-live-retransmission": {"title": "Protocol Core Layer 3 Live Retransmission", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real duplicate INVITE with cached response replay and no duplicate application action"},
    "protocol-core-layer3-live-transport-error": {"title": "Protocol Core Layer 3 Live Transport Error", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real TCP connection failure propagated into client transaction state"},
    "protocol-core-layer4-live-route-set": {"title": "Protocol Core Layer 4 Live Route Set", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real B2BUA call with reversed Record-Route applied to ACK and BYE"},
    "protocol-core-layer4-live-strict-route": {"title": "Protocol Core Layer 4 Live Strict Route", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real strict-route Request-URI and trailing remote-target validation"},
    "protocol-core-layer4-live-prack": {"title": "Protocol Core Layer 4 Live PRACK", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real reliable 183 and PRACK/RAck across B2BUA legs"},
    "protocol-core-layer4-live-session-timer": {"title": "Protocol Core Layer 4 Live Session Timer", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real Session-Expires negotiation and expiration state"},
    "protocol-core-layer4-live-session-expiry": {"title": "Protocol Core Layer 4 Live Session Expiry", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real 90-second expiration followed by two-leg BYE cleanup"},
    "protocol-core-layer4-live-min-se": {"title": "Protocol Core Layer 4 Live Min-SE", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real rejected INVITE with RFC 4028 422 and Min-SE"},
    "protocol-core-layer4-live-update-target": {"title": "Protocol Core Layer 4 Live UPDATE Target", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real in-dialog UPDATE with Contact target refresh verified by BYE routing"},
    "protocol-core-layer4-live-update-offer": {"title": "Protocol Core Layer 4 Live UPDATE Offer", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real in-dialog UPDATE offer/answer and Contact target refresh"},
    "protocol-core-layer4-live-fork-cleanup": {"title": "Protocol Core Layer 4 Live Fork Cleanup", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real forked 2xx branches with ACK/BYE cleanup of losing dialog"},
    "protocol-core-layer4-live-update-glare": {"title": "Protocol Core Layer 4 Live UPDATE Glare", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real 491 UPDATE followed by delayed higher-CSeq retry"},
    "protocol-core-layer5-live-multi-contact": {"title": "Protocol Core Layer 5 Multi Contact", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real multi-Contact REGISTER with q-value ordering"},
    "protocol-core-layer5-live-wildcard-expiry": {"title": "Protocol Core Layer 5 Wildcard Expiry", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real wildcard de-registration"},
    "protocol-core-layer5-live-path-outbound": {"title": "Protocol Core Layer 5 Path Outbound", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real Path, Service-Route, instance and reg-id exchange"},
    "protocol-core-layer5-live-max-forwards": {"title": "Protocol Core Layer 5 Loop Policy", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real Max-Forwards exhaustion rejection"},
    "protocol-core-layer5-live-digest-replay": {"title": "Protocol Core Layer 5 Digest", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real nonce-count protected digest REGISTER"},
    "protocol-core-layer6-live-options-storm": {"title": "Protocol Core Layer 6 OPTIONS Storm", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real method overload and Retry-After"},
    "protocol-core-layer6-live-register-storm": {"title": "Protocol Core Layer 6 REGISTER Storm", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real registrar overload rejection"},
    "protocol-core-layer6-live-source-limit": {"title": "Protocol Core Layer 6 Source Limit", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real per-source request shedding"},
    "protocol-core-layer6-live-priority-bypass": {"title": "Protocol Core Layer 6 Priority", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real priority admission during overload"},
    "protocol-core-layer6-live-recovery": {"title": "Protocol Core Layer 6 Recovery", "suite": "Kubernetes Protocol Core Live Calls", "mode": "real hysteresis and admission recovery"},
    "protocol-parser-validation": {
        "title": "SIP Parser Validation And Framing",
        "suite": "Kubernetes Protocol Core",
        "mode": "byte-exact SIP grammar, mandatory headers, compact/repeated fields, limits, and Content-Length smuggling rejection",
    },
    "protocol-uri-validation": {
        "title": "SIP/SIPS Request-URI And IPv6 Validation",
        "suite": "Kubernetes Protocol Core",
        "mode": "RFC 3261 SIP/SIPS schemes, hostname/IPv4/IPv6 targets, ports, parameters, escapes, and deterministic rejection",
    },
    "protocol-header-grammar": {"title": "SIP Header Grammar", "suite": "Kubernetes Protocol Core", "mode": "quoted strings, escaped values, parameters, and comma lists"},
    "protocol-mime-binary-body": {"title": "SIP MIME And Binary Bodies", "suite": "Kubernetes Protocol Core", "mode": "Content-Type grammar and byte-exact body preservation"},
    "protocol-request-policy": {"title": "SIP Request Policy", "suite": "Kubernetes Protocol Core", "mode": "Max-Forwards, URI, and Content-Type policy"},
    "protocol-error-responses": {"title": "SIP Protocol Error Responses", "suite": "Kubernetes Protocol Core", "mode": "deterministic wire responses for rejected requests"},
    "protocol-stream-parser-limits": {"title": "SIP Stream Parser Limits", "suite": "Kubernetes Protocol Core", "mode": "bounded TCP/TLS buffers and pipelined framing"},
    "protocol-rfc4475-corpus": {"title": "RFC 4475 Parser Corpus", "suite": "Kubernetes Protocol Core", "mode": "maintained accepted and rejected torture-message corpus"},
    "protocol-parser-fuzz-resource": {"title": "SIP Parser Fuzz And Resources", "suite": "Kubernetes Protocol Core", "mode": "deterministic mutation and resource-bound gates"},
    "protocol-server-transactions": {
        "title": "SIP Server Transaction State Machines",
        "suite": "Kubernetes Protocol Core",
        "mode": "RFC 3261/RFC 6026 server states, matching, retransmissions, and Timers G/H/I/J",
    },
    "protocol-client-transactions": {
        "title": "SIP Client Transaction State Machines",
        "suite": "Kubernetes Protocol Core",
        "mode": "RFC 3261/RFC 6026 client states, response matching, and Timers A/B/D/E/F/K/M",
    },
}
ALL_PROFILES = (*ALL_B2BUA_PROFILES, *PROTOCOL_CORE_PROFILES, *RASA_NLU_PROFILES)
CATALOG_PROFILES = ALL_B2BUA_PROFILES
RASA_PROFILES = (*RASA_B2BUA_PROFILES, *RASA_NLU_PROFILES)
AKS_PROFILES = (
    "esbc-options-keepalive",
    "register-auth-success",
    "register-auth-tcp",
    "register-auth-tls",
    "registered-inbound",
    "rtpengine-media",
    "rtpengine-transcoding",
    "tcp-rtpengine-transcoding",
    "tls-transport-policy",
    "tls-srtp-to-udp-rtp",
    "udp-rtp-to-tls-srtp",
    "rtcp-receiver-quality",
)
STRICT_SRTP_PROFILES = (
    "tls-srtp-to-udp-rtp",
    "tls-srtp-to-tcp-rtp",
    "udp-rtp-to-tls-srtp",
)
SRTP_ERROR_PATTERN = re.compile(
    r"\b(?:SRTP|SDES|crypto)\b.*\b(?:error|failed|failure|mismatch|unsupported|invalid|no crypto)\b"
    r"|\b(?:error|failed|failure)\b.*\b(?:SRTP|SDES|crypto)\b",
    re.IGNORECASE,
)
OPTIONS_ALLOWED_CSEQ_PATTERN = re.compile(r"(?im)^CSeq:\s*\d+\s+OPTIONS\s*$")
OPTIONS_OTHER_CSEQ_PATTERN = re.compile(r"(?im)^CSeq:\s*\d+\s+(?!OPTIONS\s*$)[A-Z]+\s*$")
OPTIONS_REQUEST_PATTERN = re.compile(r"(?im)^OPTIONS\s+\S+\s+SIP/2\.0\s*$")
SIP_CALL_ID_PATTERN = re.compile(r"(?im)^Call-ID:\s*([^\s]+)\s*$")
SELECTABLE_PROFILES = (*SMOKE_PROFILES, *ALL_PROFILES)
LAB_TLS_SECRET_NAME = "playsbc-regression-tls"
DEFAULT_OUTPUT_ROOT = str(ROOT / "logs" / "k8s-Regression")
DEFAULT_REPORT_DIR = str(ROOT / "logs" / "k8s-reports")
RASA_OUTPUT_ROOT = str(ROOT / "logs" / "RASA-Regression")
RASA_REPORT_DIR = str(ROOT / "logs" / "RASA-Regression" / "reports")
AKS_OUTPUT_ROOT = str(ROOT / "logs" / "AKS-Regression")
AKS_REPORT_DIR = str(ROOT / "logs" / "AKS-Regression" / "reports")
RASA_NLU_CASE_FILES = {
    "ai-rasa-chat-nlu": ROOT / "tests" / "rasa" / "chat_nlu_cases.yml",
    "ai-rasa-chat-negative": ROOT / "tests" / "rasa" / "chat_negative_cases.yml",
}
DEFAULT_ROLLOUT_TIMEOUT = 120
RASA_ROLLOUT_TIMEOUT = 600
RASA_PROFILE_LABELS = {
    "ai-rasa-lab": {
        "title": "AI Voice Gateway - Mock Rasa REST",
        "suite": "Kubernetes AI/Rasa Mock",
        "rasa_node": "Mock Rasa REST",
        "stt_node": "Scripted STT",
        "tts_node": "Text TTS",
        "mode": "mock REST webhook, internal PlaySBC media, single bot response",
    },
    "ai-rasa-rtpengine": {
        "title": "AI Voice Gateway - Mock Rasa + RTPengine",
        "suite": "Kubernetes AI/Rasa Mock RTPengine",
        "rasa_node": "Mock Rasa + Action",
        "stt_node": "Scripted STT",
        "tts_node": "Text TTS",
        "mode": "mock REST webhook, RTPengine RTP/RTCP anchor, multi-message bot response plus transfer action",
    },
    "ai-rasa-real-lab": {
        "title": "AI Voice Gateway - Real Rasa Pod + RTPengine",
        "suite": "Kubernetes AI/Rasa Real Lab",
        "rasa_node": "Real Rasa Pod",
        "stt_node": "Scripted STT",
        "tts_node": "Text TTS",
        "mode": "real Rasa deployment, trained in-cluster, RTPengine RTP/RTCP anchor, REST webhook proof",
    },
    "ai-rasa-rtpengine-speech": {
        "title": "AI Voice Gateway - Speech STT/TTS + Real Rasa",
        "suite": "Kubernetes AI/Rasa Speech RTPengine",
        "rasa_node": "Real Rasa Pod",
        "stt_node": "Vosk STT",
        "tts_node": "Piper TTS",
        "mode": "SIPp plays real G.711 speech, RTPengine anchors RTP/RTCP, PlaySBC decodes RTP to WAV, Vosk transcribes, real Rasa responds, and Piper generates RTP prompt evidence",
    },
    "ai-rasa-rtpengine-speech-whisper": {
        "title": "AI Voice Gateway - Whisper STT + Real Rasa",
        "suite": "Kubernetes AI/Rasa Whisper RTPengine",
        "rasa_node": "Real Rasa Pod",
        "stt_node": "Whisper STT",
        "tts_node": "Piper TTS",
        "mode": "SIPp plays G.711 speech, RTPengine anchors RTP/RTCP, PlaySBC decodes RTP to WAV, Whisper transcribes through the adapter boundary, real Rasa responds, and Piper generates RTP prompt evidence",
    },
    "ai-rasa-long-response-streaming": {
        "title": "AI Voice Gateway - Long Response Streaming",
        "suite": "Kubernetes AI/Rasa Streaming",
        "rasa_node": "Real Rasa Pod",
        "stt_node": "Scripted STT",
        "tts_node": "Piper TTS",
        "mode": "SIPp plays speech, RTPengine anchors RTP/RTCP, real Rasa returns a long support response, and PlaySBC emits ordered Piper TTS chunks with per-chunk RTP prompt evidence",
    },
    "ai-rasa-contact-center-sales": {
        "title": "AI Contact Center - SIPp B Sales Bot Agent",
        "suite": "Kubernetes AI/Rasa Contact Center",
        "rasa_node": "SIPp B Bot Agent",
        "stt_node": "Vosk STT",
        "tts_node": "Piper TTS",
        "mode": "SIPp A calls a virtual SIPp B sales agent, RTPengine anchors RTP/RTCP, Vosk transcribes sales speech, real Rasa runs the sales workflow, and Piper returns the bot-agent prompt",
    },
    "ai-rasa-contact-center-sales-coqui": {
        "title": "AI Contact Center - Sales Bot Agent + Coqui",
        "suite": "Kubernetes AI/Rasa Contact Center Coqui",
        "rasa_node": "SIPp B Bot Agent",
        "stt_node": "Vosk STT",
        "tts_node": "Coqui TTS",
        "mode": "SIPp A calls a virtual SIPp B sales agent, RTPengine anchors RTP/RTCP, Vosk transcribes sales speech, real Rasa runs the sales workflow, and Coqui generates the bot-agent prompt",
    },
    "ai-rasa-chat-nlu": {
        "title": "AI Rasa Chat NLU - Intent Matrix",
        "suite": "Kubernetes AI/Rasa NLU",
        "rasa_node": "Real Rasa NLU",
        "stt_node": "Chat Input",
        "tts_node": "Intent Result",
        "mode": "real Rasa /model/parse validates CHAT-NLU-001 through CHAT-NLU-010 intent routing",
    },
    "ai-rasa-chat-negative": {
        "title": "AI Rasa Negative Chat - Guardrails",
        "suite": "Kubernetes AI/Rasa NLU",
        "rasa_node": "Real Rasa NLU",
        "stt_node": "Chat Input",
        "tts_node": "Guardrail Result",
        "mode": "real Rasa /model/parse plus PlaySBC local guards validate CHAT-NEG-001 through CHAT-NEG-010",
    },
}


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    duration_seconds: float
    stdout: str
    stderr: str


@dataclass
class CaptureProcess:
    role: str
    pod: str
    remote_path: str
    local_path: Path
    process: subprocess.Popen[str]


@dataclass
class RtcpTarget:
    target_ip: str
    target_port: int


def make_run_id() -> str:
    return time.strftime("k8s-regression-%Y%m%d-%H%M%S", time.localtime())


def make_rasa_run_id() -> str:
    return time.strftime("rasa-regression-%Y%m%d-%H%M%S", time.localtime())


def make_aks_run_id() -> str:
    return time.strftime("aks-regression-%Y%m%d-%H%M%S", time.localtime())


def selected_profiles(args: argparse.Namespace) -> tuple[str, ...]:
    if getattr(args, "aks_profiles", False):
        return AKS_PROFILES
    if getattr(args, "rasa_profiles", False):
        return RASA_PROFILES
    if getattr(args, "all_profiles", False):
        return ALL_PROFILES
    return tuple(args.profile or DEFAULT_PROFILES)


def profile_display_title(profile_name: str) -> str:
    labels = PROTOCOL_CORE_LABELS.get(profile_name) or RASA_PROFILE_LABELS.get(profile_name, {})
    return str(labels.get("title") or profile_name)


def profile_suite_label(profile_name: str) -> str:
    labels = PROTOCOL_CORE_LABELS.get(profile_name) or RASA_PROFILE_LABELS.get(profile_name, {})
    return str(labels.get("suite") or f"Kubernetes {profile_name}")


def profile_execution_label(profile_name: str) -> str:
    title = profile_display_title(profile_name)
    return f"{title} [{profile_name}]" if title != profile_name else profile_name


def profile_mode_detail(profile_name: str) -> str:
    labels = PROTOCOL_CORE_LABELS.get(profile_name) or RASA_PROFILE_LABELS.get(profile_name, {})
    return str(labels.get("mode") or PROFILE_DESCRIPTIONS.get(profile_name, "special profile"))


def ai_ladder_nodes(profile: SimpleNamespace) -> tuple[str, str, str]:
    profile_name = str(getattr(profile, "profile", ""))
    labels = RASA_PROFILE_LABELS.get(profile_name, {})
    return (
        str(labels.get("stt_node") or "STT Adapter"),
        str(labels.get("rasa_node") or "Rasa Bot"),
        str(labels.get("tts_node") or "TTS Adapter"),
    )


def command_text(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def run_command(
    command: list[str],
    *,
    timeout: int,
    input_text: Optional[str] = None,
    check: bool = False,
) -> CommandResult:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        input=input_text,
        capture_output=True,
        timeout=timeout,
    )
    result = CommandResult(
        command=command,
        returncode=completed.returncode,
        duration_seconds=time.monotonic() - started,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command_text(command)}\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def command_failure_detail(result: CommandResult) -> str:
    return result.stderr.strip() or result.stdout.strip()


def is_statefulset_immutable_upgrade_error(text: str) -> bool:
    lower = text.lower()
    return (
        "statefulset" in lower
        and "spec: forbidden" in lower
        and "updates to statefulset spec" in lower
    )


def ensure_binary(name: str) -> None:
    if not shutil.which(name):
        raise SystemExit(f"{name} executable not found in PATH")


def short_name(text: str, limit: int = 44) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return cleaned[:limit].strip("-") or "profile"


def status_from_codes(returncodes: list[int]) -> str:
    return "passed" if returncodes and all(code == 0 for code in returncodes) else "failed"


def profile_values(profile: str, run_id: str) -> SimpleNamespace:
    values: dict[str, Any] = dict(BASE_DEFAULTS)
    if profile == REAL_TOPOLOGY_PROFILE:
        values.update(B2BUA_PROFILES["rtpengine-transcoding"])
        values.update({"caller": "core-a", "callee": "peer-b"})
    else:
        values.update(B2BUA_PROFILES[profile])
    values.update(
        {
            "profile": profile,
            "resolved_run_id": run_id,
            "host": "",
            "server_host": "",
            "server_port": 5062,
            "uac_port": 5060,
            "uas_port": 5060,
            "register_port": 5070,
            "caller_register_port": 5070,
            "sipp_pcap_sudo": False,
            "dry_run": False,
            "pcap_topology": "kubernetes-dual-realm",
        }
    )
    if values.get("rtpengine_url") == BASE_DEFAULTS["rtpengine_url"]:
        values["rtpengine_url"] = "udp://playsbc-playsbc-rtpengine:2223"
    transports = [item.strip() for item in str(values.get("sip_transport", "udp")).split(",") if item.strip()]
    values["uac_transport"] = values.get("uac_transport") or (transports[0] if len(transports) == 1 else "udp")
    values["uas_transport"] = values.get("uas_transport") or (transports[0] if len(transports) == 1 else "udp")
    values["media_enabled"] = bool(values.get("media_codec"))
    values["media_pcap"] = values.get("media_pcap") or (MEDIA_PCAPS[values["media_codec"]] if values.get("media_codec") else "")
    values["server_codec"] = values.get("server_codec") or values.get("media_codec") or "PCMU"
    values["ladder_enabled"] = values.get("ladder") if values.get("ladder") is not None else (values.get("calls", 1) == 1 and values.get("rate", 1) == 1)
    return SimpleNamespace(**values)


def format_config_value(value: object, profile: SimpleNamespace) -> object:
    rendered = render_harness_config_templates(value, profile)
    return rendered


def deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def route_policies_for(profile: SimpleNamespace) -> list[dict[str, object]]:
    policies = getattr(profile, "route_policies", None) or [
        {"name": "registered-endpoints", "match": "*", "target": "registration", "priority": 10}
    ]
    rendered = format_config_value(policies, profile)
    return rendered if isinstance(rendered, list) else []


def b2bua_routes_for(profile: SimpleNamespace) -> dict[str, object]:
    rendered = format_config_value(getattr(profile, "b2bua_routes", {}) or {}, profile)
    return rendered if isinstance(rendered, dict) else {}


def transport_args(transport: str, role: str) -> list[str]:
    name = str(transport or "udp").lower()
    tls_material = [
        "-tls_cert",
        "/tmp/playsbc-tls/tls.crt",
        "-tls_key",
        "/tmp/playsbc-tls/tls.key",
        "-tls_ca",
        "/tmp/playsbc-tls/ca.crt",
    ]
    if name == "tcp":
        return ["-t", "t1"] if role == "server" else ["-t", "tn", "-max_socket", "1024"]
    if name == "tls":
        return [*(["-t", "l1"] if role == "server" else ["-t", "ln"]), *tls_material]
    return []


def trace_args() -> list[str]:
    return ["-trace_msg", "-trace_err", "-trace_stat", "-trace_counts", "-trace_logs"]


def layer2_probe_script(probe: str) -> str:
    scripts = {
        "idle": (
            "import socket,time,sys; s=socket.create_connection((sys.argv[1],int(sys.argv[2])),3); "
            "started=time.monotonic(); time.sleep(5); s.settimeout(2); data=s.recv(1); "
            "print('idle_eof=',data==b'','elapsed=',round(time.monotonic()-started,3)); "
            "raise SystemExit(0 if data==b'' else 1)"
        ),
        "half-close": (
            "import socket,sys; s=socket.create_connection((sys.argv[1],int(sys.argv[2])),3); "
            "s.sendall(b'OPTIONS sip:x@example SIP/2.0\\r\\nVia: unfinished'); "
            "s.shutdown(socket.SHUT_WR); s.settimeout(3); data=s.recv(1); print('half_close_eof=',data==b''); "
            "raise SystemExit(0 if data==b'' else 1)"
        ),
        "pool": (
            "import socket,sys; a=socket.create_connection((sys.argv[1],int(sys.argv[2])),3); "
            "b=socket.create_connection((sys.argv[1],int(sys.argv[2])),3); "
            "c=socket.create_connection((sys.argv[1],int(sys.argv[2])),3); c.settimeout(3); "
            "data=c.recv(1); print('pool_rejected=',data==b''); a.close(); b.close(); c.close(); "
            "raise SystemExit(0 if data==b'' else 1)"
        ),
        "tls-sni": (
            "import socket,ssl,sys; ctx=ssl.create_default_context(cafile='/tmp/playsbc-tls/ca.crt'); "
            "ok=ctx.wrap_socket(socket.create_connection((sys.argv[1],int(sys.argv[2])),3),server_hostname=sys.argv[3]); ok.close(); "
            "bad=False\ntry:\n ctx.wrap_socket(socket.create_connection((sys.argv[1],int(sys.argv[2])),3),server_hostname='wrong.invalid')\n"
            "except ssl.SSLError:\n bad=True\n"
            "print('valid_sni=True invalid_sni_rejected=',bad); raise SystemExit(0 if bad else 1)"
        ),
    }
    if probe not in scripts:
        raise ValueError(f"Unsupported Layer 2 socket probe: {probe}")
    return scripts[probe]


def sdp_payloads(profile: SimpleNamespace, role: str) -> tuple[str, str]:
    if is_transcoding_profile(profile):
        codec = uas_media_codec(profile) if role == "uas" else str(getattr(profile, "media_codec", "PCMU")).upper()
        payload_type = MEDIA_PAYLOAD_TYPES[codec]
        return f"{payload_type} 101", MEDIA_RTPMAP_LINES[codec]
    return "0 8 101", "\n      ".join(MEDIA_RTPMAP_LINES[codec] for codec in ("PCMU", "PCMA"))


def media_pcap_path(profile: SimpleNamespace, role: str) -> str:
    codec = uas_media_codec(profile) if role == "uas" else str(getattr(profile, "media_codec", "PCMU")).upper()
    configured_name = "uas_media_pcap" if role == "uas" else "media_pcap"
    configured = str(getattr(profile, configured_name, "") or "")
    relative = configured or MEDIA_PCAPS.get(codec, MEDIA_PCAPS["PCMU"])
    return f"/scenarios/{relative}"


def scenario_source(profile: SimpleNamespace, role: str) -> Path:
    if role == "register":
        return SCENARIO_DIR / str(getattr(profile, "registration_scenario", "register_contact.xml"))
    if role == "uac":
        configured = str(getattr(profile, "uac_scenario", "") or "")
        if configured:
            return SCENARIO_DIR / configured
        return SCENARIO_DIR / ("b2bua_uac_a_media.xml" if getattr(profile, "media_enabled", False) else "b2bua_uac_a.xml")
    configured = str(getattr(profile, "uas_scenario", "") or "")
    if configured:
        return SCENARIO_DIR / configured
    return SCENARIO_DIR / ("b2bua_uas_b_media.xml" if getattr(profile, "media_enabled", False) else "b2bua_uas_b.xml")


def rendered_scenario(profile: SimpleNamespace, role: str) -> str:
    source = scenario_source(profile, role)
    text = source.read_text(encoding="ISO-8859-1")
    if role == "register" and str(getattr(profile, "registration_auth_expected", "") or ""):
        username = str(getattr(profile, "registration_username", "") or getattr(profile, "callee", ""))
        password = str(getattr(profile, "registration_password", ""))
        text = text.replace("__AUTH_USERNAME__", username).replace("__AUTH_PASSWORD__", password)
    if "[media_pcap]" in text:
        text = text.replace("[media_pcap]", media_pcap_path(profile, "uas" if role == "uas" else "uac"))
    if "[uac_sdp_payloads]" in text:
        payloads, rtpmaps = sdp_payloads(profile, "uac")
        text = text.replace("[uac_sdp_payloads]", payloads).replace("[uac_sdp_rtpmaps]", rtpmaps)
    if "[uas_sdp_payloads]" in text:
        payloads, rtpmaps = sdp_payloads(profile, "uas")
        text = text.replace("[uas_sdp_payloads]", payloads).replace("[uas_sdp_rtpmaps]", rtpmaps)
    secure_leg = bool(
        getattr(profile, "uac_srtp", False)
        if role == "uac"
        else role == "uas" and getattr(profile, "uas_srtp", False)
    )
    if secure_leg:
        text = render_srtp_media_scenario(text)
    return text


def is_load_profile(profile: SimpleNamespace) -> bool:
    return int(getattr(profile, "calls", 1)) > 1


def k8s_sipp_timeout_seconds(profile: SimpleNamespace) -> int:
    calls = int(getattr(profile, "calls", 1))
    rate = int(getattr(profile, "rate", 1))
    hold_ms = int(getattr(profile, "hold_ms", BASE_DEFAULTS["hold_ms"]))
    base = sipp_timeout_seconds(calls, rate, hold_ms)
    if not is_load_profile(profile):
        return base

    safe_rate = max(rate, 1)
    traffic_seconds = (max(calls, 1) + safe_rate - 1) // safe_rate
    hold_seconds = max(hold_ms, 0) // 1000
    load_headroom = 180
    if profile_uses_rtpengine(profile):
        load_headroom += 180
    if is_transcoding_profile(profile):
        load_headroom += 120
    return max(base, traffic_seconds + hold_seconds + load_headroom)


def profile_transport_tokens(profile: SimpleNamespace) -> set[str]:
    tokens: set[str] = set()
    for attr in ("sip_transport", "uac_transport", "uas_transport"):
        for token in str(getattr(profile, attr, "") or "").split(","):
            token = token.strip().lower()
            if token:
                tokens.add(token)
    return tokens


def profile_uses_tls(profile: SimpleNamespace) -> bool:
    return "tls" in profile_transport_tokens(profile)


def profile_uses_rtpengine(profile: SimpleNamespace) -> bool:
    return str(getattr(profile, "media_backend", "internal")) == "rtpengine"


def profile_uses_real_rasa(profile: SimpleNamespace) -> bool:
    config = getattr(profile, "ai_voice_gateway", {}) or {}
    return bool(
        isinstance(config, dict)
        and config.get("enabled")
        and str(config.get("provider", "rasa")).lower() == "rasa"
        and str(getattr(profile, "rasa_deployment", "")).lower() == "real"
    )


def rasa_project_values() -> dict[str, str]:
    project = ROOT / "rasa"
    return {
        "config": (project / "config.yml").read_text(encoding="utf-8"),
        "domain": (project / "domain.yml").read_text(encoding="utf-8"),
        "nlu": (project / "data" / "nlu.yml").read_text(encoding="utf-8"),
        "rules": (project / "data" / "rules.yml").read_text(encoding="utf-8"),
        "credentials": (project / "credentials.yml").read_text(encoding="utf-8"),
        "endpoints": (project / "endpoints.yml").read_text(encoding="utf-8"),
    }


def rasa_nlu_profile_values(profile_name: str, run_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        profile=profile_name,
        resolved_run_id=run_id,
        sip_transport="udp",
        media_backend="internal",
        users={},
        trunk_groups=[],
        hunt_groups=[],
        number_normalization=[],
        header_normalization={},
        transport_policies=[],
        call_admission={},
        ha={},
        reject_unknown_routes=False,
        ladder_enabled=False,
        rasa_deployment="real",
        ai_voice_gateway={
            "enabled": True,
            "provider": "rasa",
            "bot_name": "rasa-nlu",
            "rasa_webhook_url": "http://playsbc-playsbc-rasa:5005/webhooks/rest/webhook",
            "rasa_timeout": 5.0,
            "initial_message": "support",
            "fallback_text": "Real Rasa NLU bot is unavailable",
        },
    )


def profile_enables_rtpengine_deployment(profile: SimpleNamespace, args: argparse.Namespace) -> bool:
    return bool(
        args.rtpengine_enabled
        and profile_uses_rtpengine(profile)
        and str(getattr(profile, "profile", "")) != "rtpengine-control-failure"
    )


def k8s_pcap_capture_roles(profile: SimpleNamespace) -> tuple[str, ...]:
    if is_load_profile(profile):
        return ()

    roles: list[str] = []
    if bool(getattr(profile, "run_call", True)) or bool(getattr(profile, "register_caller", False)):
        roles.append("core")
    if bool(getattr(profile, "register_callee", True)):
        roles.append("peer")
    elif bool(getattr(profile, "run_call", True)) and bool(getattr(profile, "start_uas", True)):
        roles.append("peer")
    profile_name = str(getattr(profile, "profile", ""))
    if profile_name.startswith("rfc5359-") and any(
        token in profile_name
        for token in ("unattended-transfer", "forwarding-on-busy", "forwarding-on-no-answer")
    ):
        roles.append("target")

    return tuple(dict.fromkeys(roles))


def k8s_pcap_capture_filter(profile: SimpleNamespace) -> str:
    # Keep AKS evidence focused on SIP, RTP/SRTP, and RTCP. DNS and unrelated pod traffic
    # make Wireshark review noisy and can make profile evidence look misleading.
    rtpengine_max = max(32000, int(getattr(profile, "rtpengine_rtp_max", 32000) or 32000))
    return (
        "((udp and (portrange 5060-5079 or portrange 6000-6999 "
        f"or portrange 25000-26000 or portrange 30000-{rtpengine_max})) "
        "or (tcp and portrange 5060-5079))"
    )


def k8s_pcap_packet_limit(profile: SimpleNamespace) -> int | None:
    # OPTIONS-only profiles have exactly one request and one response on the SIPp pod.
    # Let tcpdump close its own file after both packets instead of racing a remote signal.
    return 2 if "options" in str(getattr(profile, "profile", "")).lower() else None


def should_run_k8s_rtcp(profile: SimpleNamespace) -> bool:
    return bool(
        getattr(profile, "rtcp_enabled", True)
        and getattr(profile, "media_enabled", False)
        and getattr(profile, "run_call", True)
        and int(getattr(profile, "calls", 1)) == 1
        and int(getattr(profile, "rate", 1)) == 1
        and int(getattr(profile, "hold_ms", 0)) >= 5000
    )


def rtcp_expected_senders(profile: SimpleNamespace) -> tuple[str, ...]:
    if not bool(getattr(profile, "start_uas", True)):
        return ("core",)
    return ("core", "peer")


def should_expect_rtcp_reply(profile: SimpleNamespace) -> bool:
    return not ("ai-rasa" in str(getattr(profile, "profile", "")) and profile_uses_rtpengine(profile))


def normalize_sipp_stderr(text: str) -> tuple[str, int]:
    non_fatal_patterns = (
        "SSL_ERROR_WANT_READ",
        "Overload warning: the minor watchdog timer",
    )
    has_non_fatal_notice = any(pattern in line for line in text.splitlines() for pattern in non_fatal_patterns)
    filtered: list[str] = []
    suppressed = 0
    for line in text.splitlines():
        if any(pattern in line for pattern in non_fatal_patterns):
            suppressed += 1
            continue
        if has_non_fatal_notice and "There were more errors, see '" in line:
            suppressed += 1
            continue
        filtered.append(line)
    return ("\n".join(filtered) + ("\n" if filtered else "")), suppressed


def kubectl_since_time(started_at: dt.datetime) -> str:
    return started_at.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def evidence_call_ids(bundle: Path) -> set[str]:
    call_ids: set[str] = set()
    for trace in bundle.rglob("sipp-traces.log"):
        text = trace.read_text(encoding="utf-8", errors="replace")
        call_ids.update(match.group(1).strip() for match in SIP_CALL_ID_PATTERN.finditer(text))
    return {call_id for call_id in call_ids if call_id}


def scope_playsbc_log(text: str, call_ids: set[str]) -> tuple[str, int]:
    if not call_ids:
        return text, 0
    kept: list[str] = []
    removed = 0
    for line in text.splitlines():
        match = re.search(r"\bcall_id=([^\s|]+)", line, re.IGNORECASE)
        if not match:
            kept.append(line)
            continue
        observed = match.group(1).strip().rstrip(".")
        if any(call_id.startswith(observed) or observed.startswith(call_id) for call_id in call_ids):
            kept.append(line)
        else:
            removed += 1
    return "\n".join(kept) + ("\n" if kept else ""), removed


def pod_has_previous_container_logs(pod: dict[str, Any], container_name: str) -> bool:
    statuses = pod.get("status", {}).get("containerStatuses", [])
    if not isinstance(statuses, list):
        return False
    for status in statuses:
        if not isinstance(status, dict) or str(status.get("name", "")) != container_name:
            continue
        try:
            if int(status.get("restartCount", 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
        last_state = status.get("lastState", {})
        if isinstance(last_state, dict) and bool(last_state.get("terminated")):
            return True
    return False


def sdp_rtcp_target_from_block(block: str) -> Optional[RtcpTarget]:
    rtp_match = re.search(r"(?m)^m=audio\s+(\d+)", block)
    if not rtp_match:
        return None
    connection_matches = re.findall(r"(?m)^c=IN\s+IP4\s+([^\s]+)", block)
    rtcp_match = re.search(r"(?m)^a=rtcp:(\d+)(?:\s+IN\s+IP4\s+([^\s]+))?", block)
    target_ip = rtcp_match.group(2) if rtcp_match and rtcp_match.group(2) else (connection_matches[-1] if connection_matches else "")
    if not target_ip:
        return None
    target_port = int(rtcp_match.group(1)) if rtcp_match else int(rtp_match.group(1)) + 1
    return RtcpTarget(target_ip=target_ip, target_port=target_port)


def extract_received_sdp_rtcp_target(trace_text: str, *, sip_start: str) -> Optional[RtcpTarget]:
    for block in reversed(re.split(r"-{20,}", trace_text)):
        if "message received" not in block:
            continue
        if f"\n{sip_start}" not in block and not block.lstrip().startswith(sip_start):
            continue
        target = sdp_rtcp_target_from_block(block)
        if target:
            return target
    return None


def merge_pcap_files(paths: list[Path], destination: Path) -> int:
    existing = [path for path in paths if path.exists() and path.stat().st_size > 24]
    if not existing:
        return 0
    header = existing[0].read_bytes()[:24]
    records: list[tuple[int, int, int, bytes]] = []
    sequence = 0
    endian = "<" if header[:4] in (b"\xd4\xc3\xb2\xa1", b"M<\xb2\xa1") else ">"
    with destination.open("wb") as output:
        output.write(header)
        for path in existing:
            data = path.read_bytes()
            if data[:24] != header:
                raise ValueError(f"K8s PCAP link-layer/header mismatch for {path.name}")
            offset = 24
            while offset + 16 <= len(data):
                packet_header = data[offset : offset + 16]
                ts_sec, ts_fraction, captured_len, _original_len = struct.unpack(
                    endian + "IIII",
                    packet_header,
                )
                frame_start = offset + 16
                frame_end = frame_start + captured_len
                if frame_end > len(data):
                    raise ValueError(f"K8s PCAP truncated packet in {path.name}")
                records.append((ts_sec, ts_fraction, sequence, packet_header + data[frame_start:frame_end]))
                sequence += 1
                offset = frame_end
        for _ts_sec, _ts_fraction, _sequence, record in sorted(records):
            output.write(record)
    return destination.stat().st_size


def prune_merged_capture_inputs(paths: list[Path], destination: Path) -> list[str]:
    if not destination.exists() or destination.stat().st_size <= 24:
        return []
    removed: list[str] = []
    for path in paths:
        if path == destination or path.name == destination.name:
            continue
        if path.name.startswith("capture-") and path.suffix == ".pcap" and path.exists():
            path.unlink()
            removed.append(path.name)
    return removed


def write_pcap_leg_summary(
    profile: Optional[SimpleNamespace],
    captures: list[CaptureProcess],
    copied: list[Path],
    destination: Path,
    bundle: Path,
) -> tuple[dict[str, Any], list[str]]:
    """Describe and verify the role sources retained in the merged capture."""
    path_by_role = {
        capture.role: capture.local_path
        for capture in captures
        if capture.local_path in copied
    }
    role_packets: dict[str, int] = {}
    failures: list[str] = []
    for capture in captures:
        path = path_by_role.get(capture.role)
        packet_count = len(read_pcap(path)) if path is not None else 0
        role_packets[capture.role] = packet_count
        if packet_count == 0:
            failures.append(f"capture role {capture.role} has no decoded packets")

    merged_packets = read_pcap(destination) if destination.exists() and destination.stat().st_size > 24 else []
    merged_sip_events = sip_events(merged_packets)
    invite_events = [event for event in merged_sip_events if event.start_line.startswith("INVITE ")]
    invite_call_ids = sorted({event.call_id for event in invite_events if event.call_id != "unknown"})
    expected_roles = [capture.role for capture in captures]
    expects_plain_two_leg_sip = bool(
        profile
        and expected_roles == ["core", "peer"]
        and getattr(profile, "run_call", True)
        and getattr(profile, "start_uas", True)
        and getattr(profile, "register_callee", True)
        and not profile_uses_tls(profile)
    )
    if expects_plain_two_leg_sip and len(invite_call_ids) < 2:
        failures.append(
            "merged capture does not contain distinct core-facing and peer-facing INVITE Call-IDs"
        )

    summary: dict[str, Any] = {
        "profile": str(getattr(profile, "profile", "")) if profile else "",
        "expected_roles": expected_roles,
        "role_packet_counts": role_packets,
        "all_role_sources_present": not any(count == 0 for count in role_packets.values()),
        "merged_packet_count": len(merged_packets),
        "merged_sip_event_count": len(merged_sip_events),
        "invite_leg_count": len(invite_call_ids),
        "invite_call_ids": invite_call_ids,
        "plain_two_leg_sip_required": expects_plain_two_leg_sip,
        "status": "passed" if not failures else "failed",
        "failures": failures,
    }
    (bundle / "pcap-legs.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary, failures


def extract_sipp_message_sections(trace_text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_source = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_source, current_lines
        if current_source and current_source.endswith("_messages.log"):
            body = "\n".join(current_lines).strip()
            if body:
                sections.append((Path(current_source).name, body))
        current_source = ""
        current_lines = []

    for line in trace_text.splitlines():
        if line.startswith("===== ") and line.endswith(" ====="):
            flush()
            current_source = line.removeprefix("===== ").removesuffix(" =====").strip()
            continue
        current_lines.append(line)
    flush()
    return sections


def write_combined_sipmsg_log(bundle: Path, profile_name: str) -> int:
    sections: list[tuple[str, str, str]] = []
    for trace_path in sorted(bundle.glob("*/sipp-traces.log")):
        trace_text = trace_path.read_text(encoding="utf-8", errors="replace")
        for source_name, body in extract_sipp_message_sections(trace_text):
            sections.append((trace_path.parent.name, source_name, body))

    output = bundle / "sipmsg.log"
    if not sections:
        reason = "chat_nlu_profile" if profile_name in RASA_NLU_PROFILES else "no_sipp_message_trace_collected"
        output.write_text(
            (
                "PLAY SBC COMBINED SIP MESSAGE TRACE\n"
                f"profile={profile_name}\n"
                f"status=not_applicable reason={reason}\n"
            ),
            encoding="utf-8",
        )
        return 0

    lines = [
        "PLAY SBC COMBINED SIP MESSAGE TRACE",
        f"profile={profile_name}",
        f"sections={len(sections)}",
        "",
    ]
    for step_name, source_name, body in sections:
        lines.extend(
            [
                f"===== {step_name} / {source_name} =====",
                body,
                "",
            ]
        )
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return len(sections)


def read_bundle_evidence_text(bundle: Path, filenames: Iterable[str]) -> str:
    chunks: list[str] = []
    for filename in filenames:
        path = bundle / filename
        if path.exists():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def sipmsg_call_ids(bundle: Path) -> set[str]:
    path = bundle / "sipmsg.log"
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8", errors="replace")
    return {match.group(1) for match in SIP_CALL_ID_PATTERN.finditer(text)}


def read_call_scoped_evidence_text(bundle: Path, filenames: Iterable[str], call_ids: set[str]) -> str:
    if not call_ids:
        return read_bundle_evidence_text(bundle, filenames)
    lines: list[str] = []
    for filename in filenames:
        path = bundle / filename
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if any(call_id in line for call_id in call_ids):
                lines.append(line)
    return "\n".join(lines)


def parse_key_value_line(line: str) -> dict[str, str]:
    return {
        match.group(1): match.group(2)
        for match in re.finditer(r"([A-Za-z0-9_./-]+)=([^\s]+)", line)
    }


def rtpengine_packet_verdicts(bundle: Path, call_ids: Optional[set[str]] = None) -> list[dict[str, str]]:
    verdicts: list[dict[str, str]] = []
    evidence = read_call_scoped_evidence_text(
        bundle,
        ("log.media", "playsbc.log", "rtpengine.log"),
        call_ids or set(),
    )
    previous_title = ""
    for line in evidence.splitlines():
        if "RTPENGINE PACKET VERDICT" in line:
            previous_title = "RTPENGINE PACKET VERDICT"
            parsed = parse_key_value_line(line)
            if parsed:
                verdicts.append(parsed)
            continue
        if previous_title and ("caller_to_callee=" in line or "callee_to_caller=" in line):
            verdicts.append(parse_key_value_line(line))
        previous_title = ""
    return verdicts


def rtpengine_two_way_verdict_observed(bundle: Path, call_ids: Optional[set[str]] = None) -> bool:
    for verdict in rtpengine_packet_verdicts(bundle, call_ids):
        try:
            total_rtp_packets = int(verdict.get("total_rtp_packets", "0") or "0")
        except ValueError:
            total_rtp_packets = 0
        if (
            verdict.get("caller_to_callee") == "observed"
            and verdict.get("callee_to_caller") == "observed"
            and total_rtp_packets > 0
        ):
            return True
    return False


LADDER_SIP_METHODS = (
    "REGISTER", "OPTIONS", "INVITE", "ACK", "BYE", "CANCEL", "REFER",
    "NOTIFY", "PRACK", "MESSAGE",
)


def validate_ladder_against_sipmsg(ladder: str, sipmsg_text: str) -> list[str]:
    """Reject hand-authored ladders that invent or omit observed SIP behavior."""
    if not ladder or not sipmsg_text or "NLP CHAT / RASA LADDER" in ladder:
        return []
    failures: list[str] = []
    ladder_methods = {
        method for method in LADDER_SIP_METHODS if re.search(rf"\b{method}\b", ladder)
    }
    evidence_methods = {
        method
        for method in LADDER_SIP_METHODS
        if re.search(rf"(?im)^(?:{method}\s+sip:|CSeq:\s*\d+\s+{method}\s*$)", sipmsg_text)
    }
    if ladder_methods != evidence_methods:
        invented = sorted(ladder_methods - evidence_methods)
        omitted = sorted(evidence_methods - ladder_methods)
        failures.append(
            f"ladder SIP methods disagree with sipmsg.log: invented={invented} omitted={omitted}"
        )
    ladder_failures = set(re.findall(r"\b([456]\d\d)\b", ladder))
    evidence_failures = set(re.findall(r"(?m)^SIP/2\.0\s+([456]\d\d)\b", sipmsg_text))
    if ladder_failures != evidence_failures:
        invented = sorted(ladder_failures - evidence_failures)
        omitted = sorted(evidence_failures - ladder_failures)
        failures.append(
            f"ladder final statuses disagree with sipmsg.log: invented={invented} omitted={omitted}"
        )
    return failures


def observed_sip_ladder(profile_name: str, bundle: Path) -> str:
    """Render a completed scenario solely from retained packet or SIPp evidence."""
    capture = bundle / "capture.pcap"
    if capture.exists():
        events = sip_events(read_pcap(capture))
        if events:
            return "\n".join(
                [f"KUBERNETES OBSERVED PCAP SIP LADDER profile={profile_name}", "Time (epoch)        Source                  Destination             Message"]
                + [f"{event.timestamp:<19.6f} {event.src:<23} {event.dst:<23} {event.start_line}" for event in events]
            )
    sipmsg = bundle / "sipmsg.log"
    if not sipmsg.exists():
        return ""
    trace_text = sipmsg.read_text(encoding="utf-8", errors="replace")
    observed = []
    for line in trace_text.splitlines():
        if re.match(r"^(?:[A-Z]+\s+\S+\s+SIP/2\.0$|SIP/2\.0\s+\d{3}\b)", line):
            observed.append(line)
    if not observed:
        return ""
    # SIPp's TCP/TLS trace can omit a buffered request start line while still
    # recording its response and CSeq. Describe that as indirect evidence.
    observed_methods = {line.split(" ", 1)[0] for line in observed if not line.startswith("SIP/2.0")}
    for method in sorted(set(re.findall(r"(?m)^CSeq:\s*\d+\s+([A-Z]+)\s*$", trace_text)) - observed_methods):
        observed.append(f"{method} (evidenced by response CSeq; request bytes not retained)")
    return "\n".join(
        [f"KUBERNETES OBSERVED SIPP SIP LADDER profile={profile_name}"] + observed
    )


def final_evidence_ladder(profile_name: str, bundle: Path, execution_ladder: str = "") -> str:
    """Build the ladder only after all packet and application evidence is collected."""
    observed = observed_sip_ladder(profile_name, bundle)
    ai_log = bundle / "log.ai"
    if ai_log.exists():
        text = ai_log.read_text(encoding="utf-8", errors="replace")
        markers = list(re.finditer(r"(?m)^AI VOICE CALL LADDER\s*$", text))
        if markers:
            start = markers[-1].start()
            next_event = re.search(r"(?m)^\d{4}-\d{2}-\d{2} .*? \| ", text[markers[-1].end():])
            end = markers[-1].end() + next_event.start() if next_event else len(text)
            rich_ladder = text[start:end].strip()
            if observed:
                rich_ladder += "\n\n" + observed
            return rich_ladder
    if observed:
        return observed
    if execution_ladder:
        return execution_ladder
    protocol_log = bundle / "protocol-core.log"
    if protocol_log.exists():
        verdicts = [
            line.strip() for line in protocol_log.read_text(encoding="utf-8", errors="replace").splitlines()
            if re.search(r"\.\.\. (?:ok|FAIL|ERROR|skipped)", line)
        ]
        if verdicts:
            return "\n".join(
                [
                    f"PROTOCOL CORE EVIDENCE LADDER profile={profile_name}",
                    "Runner -> unittest -> protocol component -> verdict",
                ]
                + [f"{index:03d} Runner -> {verdict}" for index, verdict in enumerate(verdicts, 1)]
            )
    # Every profile receives a truthful post-run evidence timeline, including
    # infrastructure failures that occur before any SIP packet can be emitted.
    files = sorted(path.name for path in bundle.iterdir() if path.is_file())
    return "\n".join([
        f"REGRESSION EVIDENCE LADDER profile={profile_name}",
        "01 K8s Runner -> profile preparation",
        "02 Profile execution -> evidence collection",
        f"03 Evidence bundle -> HTML report files={','.join(files) or 'none'}",
    ])


def validate_k8s_profile_evidence(
    profile_name: str, bundle: Path, sip_ladder: str = ""
) -> list[str]:
    failures: list[str] = []
    profile = profile_values(profile_name, "evidence") if profile_name in CATALOG_PROFILES else None

    sipmsg = bundle / "sipmsg.log"
    if profile_name not in (*RASA_NLU_PROFILES, *PROTOCOL_CORE_PROFILES) and not sipmsg.exists():
        failures.append("missing root sipmsg.log")
    if profile_name in PROTOCOL_CORE_PROFILES:
        protocol_log = bundle / "protocol-core.log"
        if not protocol_log.exists():
            failures.append("missing protocol-core.log")
        elif "OK" not in protocol_log.read_text(encoding="utf-8", errors="replace"):
            failures.append("protocol-core.log does not contain a passing unittest verdict")
    if profile_name == "protocol-core-layer1-live-call" and sipmsg.exists():
        layer1_text = sipmsg.read_text(encoding="utf-8", errors="replace")
        for marker in (
            "v: SIP/2.0/UDP",
            'f: "Layer One, Parser"',
            "i:",
            "Subject: Protocol Core Layer 1\n folded-header-continuation",
            "X-Protocol-Core-Layer: parser-validation",
            "c: application/sdp",
            "l:",
        ):
            if marker not in layer1_text:
                failures.append(f"Layer 1 live call evidence is missing {marker!r}")
    if profile_name in {"protocol-core-layer2-live-tcp-call", "protocol-core-layer2-live-tls-call"}:
        transport = "tls" if profile_name.endswith("tls-call") else "tcp"
        stream_log = bundle / f"log.{transport}"
        if not stream_log.exists():
            failures.append(f"Layer 2 live call is missing log.{transport}")
        else:
            stream_text = stream_log.read_text(encoding="utf-8", errors="replace")
            for marker in ("CONNECTED", "RX BYTES", "CONNECTION REUSED", "TX"):
                marker = f"{transport.upper()} {marker}"
                if marker not in stream_text:
                    failures.append(f"Layer 2 live call evidence is missing {marker}")
    if profile_name == "protocol-core-layer2-live-rport-call" and sipmsg.exists():
        rport_text = sipmsg.read_text(encoding="utf-8", errors="replace")
        via_lines = re.findall(r"(?im)^Via:\s+SIP/2\.0/UDP\s+192\.0\.2\.123:9;[^\r\n]+", rport_text)
        if not any("received=" in line and re.search(r";rport=\d+", line) for line in via_lines):
            failures.append("Layer 2 RFC 3581 evidence is missing received/rport on the mismatched Via")
    if profile_name == "protocol-core-layer3-live-client-transactions":
        transaction_log = bundle / "log.sip"
        transaction_text = transaction_log.read_text(encoding="utf-8", errors="replace") if transaction_log.exists() else ""
        for method in ("INVITE", "BYE"):
            if not re.search(rf"CLIENT TRANSACTION STARTED[^\n]*method={method}\b", transaction_text):
                failures.append(f"Layer 3 client transaction evidence is missing outbound {method}")
            if not re.search(rf"CLIENT TRANSACTION RESPONSE[^\n]*method={method} status=200 matched=True", transaction_text):
                failures.append(f"Layer 3 client transaction evidence is missing matched {method} 200")
    if profile_name == "protocol-core-layer3-live-non2xx-ack":
        transaction_log = bundle / "log.sip"
        transaction_text = transaction_log.read_text(encoding="utf-8", errors="replace") if transaction_log.exists() else ""
        packet_text = sipmsg.read_text(encoding="utf-8", errors="replace") if sipmsg.exists() else ""
        for marker in ("CLIENT TRANSACTION NON-2XX FINAL", "status=503 matched=True"):
            if marker not in transaction_text:
                failures.append(f"Layer 3 non-2xx transaction evidence is missing {marker}")
        for marker in ("SIP/2.0 503 Service Unavailable", "ACK sip:"):
            if marker not in packet_text:
                failures.append(f"Layer 3 non-2xx packet evidence is missing {marker}")
    if profile_name == "protocol-core-layer3-live-cancel":
        transaction_log = bundle / "log.sip"
        transaction_text = transaction_log.read_text(encoding="utf-8", errors="replace") if transaction_log.exists() else ""
        packet_text = sipmsg.read_text(encoding="utf-8", errors="replace") if sipmsg.exists() else ""
        for marker in ("method=CANCEL", "status=200 matched=True"):
            if marker not in transaction_text:
                failures.append(f"Layer 3 CANCEL evidence is missing {marker}")
        for marker in ("SIP/2.0 487 Request Terminated", "ACK sip:"):
            if marker not in packet_text:
                failures.append(f"Layer 3 CANCEL packet evidence is missing {marker}")
    if profile_name == "protocol-core-layer3-live-retransmission":
        transaction_log = bundle / "log.sip"
        transaction_text = transaction_log.read_text(encoding="utf-8", errors="replace") if transaction_log.exists() else ""
        outbound_invites = len(re.findall(r"CLIENT TRANSACTION STARTED[^\n]*method=INVITE", transaction_text))
        if outbound_invites != 1:
            failures.append(f"Layer 3 retransmission produced {outbound_invites} outbound INVITE transactions instead of one")
    if profile_name == "protocol-core-layer3-live-transport-error":
        networking_log = bundle / "log.networking"
        networking_text = networking_log.read_text(encoding="utf-8", errors="replace") if networking_log.exists() else ""
        for marker in ("TCP TX FAILED", "CLIENT TRANSACTION TRANSPORT ERROR", "method=INVITE"):
            if marker not in networking_text:
                failures.append(f"Layer 3 transport failure evidence is missing {marker}")
        call_log = bundle / "log.call"
        if not call_log.exists() or "B2BUA FAILURE" not in call_log.read_text(encoding="utf-8", errors="replace"):
            failures.append("Layer 3 transport failure is missing upstream failure mapping")
    if profile and profile_uses_rtpengine(profile) and profile_name != "rtpengine-interface-failure":
        rtpengine_log = bundle / "rtpengine.log"
        if rtpengine_log.exists():
            rtpengine_text = rtpengine_log.read_text(encoding="utf-8", errors="replace")
            for interface in ("core", "peer"):
                if f"Interface '{interface}' not found" in rtpengine_text:
                    failures.append(
                        f"RTPengine is missing configured logical interface {interface!r}"
                    )
    if (sipmsg.exists() and sip_ladder and "OBSERVED PCAP SIP LADDER" not in sip_ladder
            and "AI VOICE CALL LADDER" not in sip_ladder
            and "PROTOCOL CORE EVIDENCE LADDER" not in sip_ladder
            and "REGRESSION EVIDENCE LADDER" not in sip_ladder):
        failures.extend(
            validate_ladder_against_sipmsg(
                sip_ladder,
                sipmsg.read_text(encoding="utf-8", errors="replace"),
            )
        )

    if profile and k8s_pcap_capture_roles(profile):
        capture = bundle / "capture.pcap"
        if not capture.exists() or capture.stat().st_size <= 24:
            failures.append("missing merged capture.pcap")
        split_captures = sorted(path.name for path in bundle.glob("capture-*.pcap") if path.name != "capture.pcap")
        if split_captures:
            failures.append(f"stale split capture files present: {','.join(split_captures)}")

    if "options" in profile_name and sipmsg.exists():
        options_text = sipmsg.read_text(encoding="utf-8", errors="replace")
        if not OPTIONS_REQUEST_PATTERN.search(options_text):
            failures.append("OPTIONS evidence is missing an OPTIONS request")
        elif not OPTIONS_ALLOWED_CSEQ_PATTERN.search(options_text):
            failures.append("OPTIONS evidence is missing an OPTIONS CSeq")
        elif OPTIONS_OTHER_CSEQ_PATTERN.search(options_text):
            failures.append("OPTIONS evidence contains non-OPTIONS SIP traffic")

    if profile_name == "invalid-bye" and sipmsg.exists():
        invalid_bye_text = sipmsg.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"(?im)^BYE\s+sip:", invalid_bye_text):
            failures.append("invalid-BYE evidence is missing the out-of-dialog BYE request")
        if not re.search(r"(?im)^CSeq:\s*\d+\s+BYE\s*$", invalid_bye_text):
            failures.append("invalid-BYE evidence is missing a BYE CSeq")
        if not re.search(
            r"(?im)^SIP/2\.0\s+481\s+Call/Transaction Does Not Exist\s*$",
            invalid_bye_text,
        ):
            failures.append("invalid-BYE evidence is missing the 481 response")
        if re.search(r"(?im)^INVITE\s+", invalid_bye_text):
            failures.append("invalid-BYE evidence unexpectedly contains an INVITE")

    if profile_name.startswith("rfc5359-call-hold-resume") and sipmsg.exists():
        hold_text = sipmsg.read_text(encoding="utf-8", errors="replace")
        required_patterns = {
            "initial INVITE": r"(?im)^CSeq:\s*1\s+INVITE\s*$",
            "hold re-INVITE": r"(?im)^CSeq:\s*2\s+INVITE\s*$",
            "hold ACK": r"(?im)^CSeq:\s*2\s+ACK\s*$",
            "resume re-INVITE": r"(?im)^CSeq:\s*3\s+INVITE\s*$",
            "resume ACK": r"(?im)^CSeq:\s*3\s+ACK\s*$",
            "sendonly hold SDP": r"(?im)^a=sendonly\s*$",
            "sendrecv resume SDP": r"(?im)^a=sendrecv\s*$",
        }
        for label, pattern in required_patterns.items():
            if not re.search(pattern, hold_text):
                failures.append(f"RFC 5359 evidence is missing {label}")
        capture = bundle / "capture.pcap"
        if capture.exists():
            rtp_packets = [
                packet
                for packet in read_pcap(capture)
                if packet.transport == "udp"
                and len(packet.payload) >= 12
                and packet.payload[0] >> 6 == 2
                and packet.src_port not in {5060, 5061, 5062, 2223}
                and packet.dst_port not in {5060, 5061, 5062, 2223}
                and (packet.payload[1] & 0x7F) < 96
            ]
            ordered_packets = sorted(rtp_packets, key=lambda packet: packet.timestamp)
            gaps = [
                later.timestamp - earlier.timestamp
                for earlier, later in zip(ordered_packets, ordered_packets[1:])
            ]
            largest_gap = max(gaps, default=0.0)
            gap_index = gaps.index(largest_gap) + 1 if gaps else 0
            before_hold = ordered_packets[:gap_index]
            after_resume = ordered_packets[gap_index:]

            def has_two_way_sipp_media(packets: list[Any]) -> bool:
                senders = {
                    (packet.src_ip, packet.src_port)
                    for packet in packets
                    if 6000 <= packet.src_port <= 6999
                }
                receivers = {
                    (packet.dst_ip, packet.dst_port)
                    for packet in packets
                    if 6000 <= packet.dst_port <= 6999
                }
                return len(senders) >= 2 and len(receivers) >= 2

            if (
                len(before_hold) < 20
                or len(after_resume) < 20
                or not has_two_way_sipp_media(before_hold)
                or not has_two_way_sipp_media(after_resume)
            ):
                failures.append(
                    "RFC 5359 media evidence is missing bidirectional RTP before hold or after resume"
                )
            if largest_gap < 0.5:
                failures.append("RFC 5359 media evidence did not prove a held-media gap before resume")

    if profile_name.startswith("rfc5359-") and any(
        token in profile_name
        for token in ("unattended-transfer", "forwarding-on-busy", "forwarding-on-no-answer")
    ):
        evidence = read_bundle_evidence_text(
            bundle,
            ("sipmsg.log", "log.sip", "target-sipp-c-uas/sipp-traces.log"),
        )
        target = "transfer-target" if "unattended-transfer" in profile_name else "forward-target"
        if not re.search(rf"(?im)^INVITE\s+sip:{re.escape(target)}@", evidence):
            failures.append(f"RFC 5359 evidence is missing the third-endpoint INVITE to {target}")
        if "target-c-" not in evidence:
            failures.append("RFC 5359 evidence is missing a response from target endpoint C")

    if profile_name in STRICT_SRTP_PROFILES:
        call_ids = sipmsg_call_ids(bundle)
        media_text = read_call_scoped_evidence_text(
            bundle,
            ("log.media", "log.sip", "playsbc.log", "rtpengine.log"),
            call_ids,
        )
        if "RTPENGINE MEDIA SECURITY" not in media_text:
            failures.append("missing RTPENGINE MEDIA SECURITY evidence")
        if SRTP_ERROR_PATTERN.search(media_text):
            failures.append("SRTP/RTPengine crypto negotiation error evidence present")
        if not rtpengine_two_way_verdict_observed(bundle, call_ids):
            failures.append("RTPengine packet verdict did not prove both RTP directions observed")

    if profile_name in {"ai-rasa-lab", "ai-rasa-rtpengine"}:
        ai_text = read_bundle_evidence_text(bundle, ("log.ai",))
        if "RASA REST ERROR" in ai_text or "fallback_used=true" in ai_text:
            failures.append("mock Rasa webhook used fallback instead of deterministic REST evidence")
        if "fallback_used=false" not in ai_text:
            failures.append("mock Rasa evidence is missing fallback_used=false")
        if profile_name == "ai-rasa-rtpengine" and (
            "AI BOT ACTION" not in ai_text or "AI BOT TRANSFER" not in ai_text
        ):
            failures.append("mock Rasa RTPengine evidence is missing bot transfer action")

    return failures


def scenario_configmap_manifest(name: str) -> dict[str, object]:
    data = {}
    for path in sorted(SCENARIO_DIR.glob("*.xml")):
        data[path.name] = path.read_text(encoding="ISO-8859-1")
    if not data:
        raise SystemExit(f"No SIPp XML scenarios found in {SCENARIO_DIR}")
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "labels": {
                "app.kubernetes.io/name": "playsbc-k8s-regression",
                "app.kubernetes.io/part-of": "playsbc",
            },
        },
        "data": data,
    }


def pod_manifest(
    name: str,
    image: str,
    pull_policy: str,
    configmap: str,
    run_id: str,
    realm: str = "",
    tls_secret: str = "",
) -> dict[str, object]:
    labels = {
        "app.kubernetes.io/name": "playsbc-k8s-regression",
        "app.kubernetes.io/part-of": "playsbc",
        "playsbc-regression-run": run_id,
        "playsbc-regression-agent": name,
    }
    if realm:
        labels["playsbc.openai.com/realm"] = realm
    volume_mounts = [{"name": "scenario-overrides", "mountPath": "/scenario-overrides", "readOnly": True}]
    volumes: list[dict[str, object]] = [{"name": "scenario-overrides", "configMap": {"name": configmap}}]
    if tls_secret:
        volume_mounts.append({"name": "tls-secret", "mountPath": "/tmp/playsbc-tls", "readOnly": True})
        volumes.append({"name": "tls-secret", "secret": {"secretName": tls_secret}})
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "sipp-agent",
                    "image": image,
                    "imagePullPolicy": pull_policy,
                    "command": ["sleep", "3600"],
                    "volumeMounts": volume_mounts,
                    "securityContext": {
                        "capabilities": {"add": ["NET_RAW"]},
                    },
                }
            ],
            "volumes": volumes,
        },
    }


def agent_service_manifest(name: str, pod_name: str, run_id: str) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "labels": {"playsbc-regression-run": run_id}},
        "spec": {
            "selector": {"playsbc-regression-agent": pod_name},
            "ports": [
                {"name": "sip", "protocol": "TCP", "port": 5060, "targetPort": 5060},
                {"name": "sips", "protocol": "TCP", "port": 5061, "targetPort": 5060},
                {"name": "sip-udp", "protocol": "UDP", "port": 5060, "targetPort": 5060},
            ],
        },
    }


class PhaseLog:
    def __init__(self) -> None:
        self.phases: list[ReportPhase] = []

    def append(self, name: str, status: str, started: float, detail: str) -> None:
        self.phases.append(
            ReportPhase(
                name=name,
                status=status,
                duration_seconds=time.monotonic() - started,
                detail=detail,
            )
        )


class K8sRegressionRunner:
    def __init__(self, args: argparse.Namespace, run_id: str) -> None:
        self.args = args
        self.run_id = run_id
        self.image_prepared = False
        self.original_values: Optional[dict[str, Any]] = None
        self.tls_secret_prepared = False
        self.mock_rasa_process: Optional[subprocess.Popen[str]] = None
        self.mock_rasa_log_handle: Any = None

    def start_mock_rasa(self, profile: SimpleNamespace, bundle: Path) -> str:
        runner_ip = os.environ.get("PLAYSBC_REGRESSION_RUNNER_IP", "").strip()
        if not runner_ip:
            raise RuntimeError(
                "PLAYSBC_REGRESSION_RUNNER_IP is required for Kubernetes mock Rasa profiles; "
                "launch through tools/run_k8s_regression_job.py"
            )
        log_path = bundle / "mock-rasa.log"
        self.mock_rasa_log_handle = log_path.open("w", encoding="utf-8")
        command = [
            sys.executable,
            str(ROOT / "tools" / "mock_rasa_server.py"),
            "--host",
            "0.0.0.0",
            "--port",
            "5005",
            "--response-count",
            str(int(getattr(profile, "rasa_mock_response_count", 1))),
            "--action",
            str(getattr(profile, "rasa_mock_action", "")),
            "--action-target",
            str(getattr(profile, "rasa_mock_action_target", "")),
        ]
        self.mock_rasa_process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=self.mock_rasa_log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self.mock_rasa_process.poll() is not None:
                raise RuntimeError(f"mock Rasa exited early; see {log_path}")
            try:
                with urllib.request.urlopen("http://127.0.0.1:5005/healthz", timeout=0.5) as response:
                    if response.status == 200:
                        return f"http://{runner_ip}:5005/webhooks/rest/webhook"
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f"mock Rasa did not become ready; see {log_path}")

    def stop_mock_rasa(self) -> None:
        process = self.mock_rasa_process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        self.mock_rasa_process = None
        if self.mock_rasa_log_handle:
            self.mock_rasa_log_handle.close()
        self.mock_rasa_log_handle = None

    def active_active_enabled(self) -> bool:
        value = getattr(self.args, "active_active_topology", True)
        return True if value is None else bool(value)

    @staticmethod
    def values_active_active_enabled(values: dict[str, Any] | None) -> bool:
        topology = (values or {}).get("topology") or {}
        active_active = topology.get("activeActive") or {}
        return bool(active_active.get("enabled", False))

    def workload_ref_for_active_state(self, active_active: bool, workload_name: str) -> str:
        return f"{'statefulset' if active_active else 'deployment'}/{workload_name}"

    def playsbc_workload_ref(self) -> str:
        return self.workload_ref_for_active_state(self.active_active_enabled(), self.args.deployment)

    def rtpengine_workload_ref(self) -> str:
        return self.workload_ref_for_active_state(self.active_active_enabled(), self.args.rtpengine_deployment)

    def helm_upgrade_values(self, values_path: Path, log_path: Path, *, check: bool = True) -> CommandResult:
        command = [
            self.args.helm_bin,
            "upgrade",
            self.args.helm_release,
            self.args.chart,
            "--namespace",
            self.args.namespace,
            "-f",
            str(values_path),
        ]
        result = run_command(command, timeout=self.args.helm_timeout, check=False)
        log_text = result.stdout + result.stderr
        detail = command_failure_detail(result)
        if result.returncode != 0 and is_statefulset_immutable_upgrade_error(detail):
            retry_note = (
                "\n[playsbc] Helm hit an immutable StatefulSet spec migration. "
                "Deleting the StatefulSet with --cascade=orphan keeps existing pods alive, "
                "then Helm can recreate ownership with the new spec.\n"
            )
            delete_result = self.kubectl(
                "delete",
                "statefulset",
                self.args.deployment,
                "--cascade=orphan",
                "--ignore-not-found=true",
                check=False,
                timeout=60,
            )
            retry_result = run_command(command, timeout=self.args.helm_timeout, check=False)
            log_text = (
                log_text
                + retry_note
                + "\n[playsbc] immutable-upgrade orphan delete:\n"
                + delete_result.stdout
                + delete_result.stderr
                + "\n[playsbc] immutable-upgrade retry:\n"
                + retry_result.stdout
                + retry_result.stderr
            )
            result = retry_result
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log_text, encoding="utf-8")
        if check and result.returncode != 0:
            raise RuntimeError(
                f"Command failed ({result.returncode}): {command_text(command)}\n"
                f"{command_failure_detail(result)}"
            )
        return result

    def rollout_status(self, workload_ref: str, *, check: bool = True) -> CommandResult:
        result = self.kubectl(
            "rollout",
            "status",
            workload_ref,
            f"--timeout={self.args.rollout_timeout}s",
            check=False,
            timeout=self.args.rollout_timeout + 30,
        )
        if result.returncode == 0 or not workload_ref.startswith("statefulset/"):
            if check and result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip())
            return result
        detail = (result.stderr + result.stdout).lower()
        if "not found" not in detail:
            if check:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip())
            return result
        fallback = f"deployment/{workload_ref.split('/', 1)[1]}"
        fallback_result = self.kubectl(
            "rollout",
            "status",
            fallback,
            f"--timeout={self.args.rollout_timeout}s",
            check=check,
            timeout=self.args.rollout_timeout + 30,
        )
        return fallback_result

    def rollout_restart(self, workload_ref: str) -> CommandResult:
        return self.kubectl("rollout", "restart", workload_ref, check=True)

    def workload_pod_names(self, target: str) -> list[str]:
        if target == "rtpengine":
            selector = f"app.kubernetes.io/name=playsbc-rtpengine,app.kubernetes.io/instance={self.args.helm_release}"
        else:
            selector = f"app.kubernetes.io/name=playsbc,app.kubernetes.io/instance={self.args.helm_release}"
        result = self.kubectl("get", "pods", "-l", selector, "-o", "json", check=False)
        try:
            pods = json.loads(result.stdout or "{}").get("items", [])
        except json.JSONDecodeError:
            pods = []
        names = [
            str(pod.get("metadata", {}).get("name", ""))
            for pod in pods
            if str(pod.get("metadata", {}).get("name", ""))
        ]
        return sorted(names)

    def workload_pod_name(self, target: str, index: int) -> str:
        if self.active_active_enabled():
            base = self.args.rtpengine_deployment if target == "rtpengine" else self.args.deployment
            return f"{base}-{index}"
        pods = self.workload_pod_names(target)
        if not pods:
            raise RuntimeError(f"No {target} pod found for HA action")
        return pods[min(index, len(pods) - 1)]

    def pod_snapshot(self, pod_name: str) -> dict[str, Any]:
        result = self.kubectl("get", "pod", pod_name, "-o", "json", check=False, timeout=10)
        if result.returncode != 0:
            return {}
        try:
            parsed = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def pod_uid(self, pod_name: str) -> str:
        snapshot = self.pod_snapshot(pod_name)
        return str(snapshot.get("metadata", {}).get("uid", ""))

    def pod_is_ready(self, snapshot: dict[str, Any]) -> bool:
        if snapshot.get("metadata", {}).get("deletionTimestamp"):
            return False
        conditions = snapshot.get("status", {}).get("conditions", [])
        if not isinstance(conditions, list):
            return False
        return any(
            isinstance(condition, dict)
            and condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in conditions
        )

    def wait_pod_ready(self, pod_name: str, bundle: Path, previous_uid: str = "") -> None:
        deadline = time.monotonic() + float(self.args.rollout_timeout)
        last_detail = ""
        while time.monotonic() < deadline:
            snapshot = self.pod_snapshot(pod_name)
            uid = str(snapshot.get("metadata", {}).get("uid", ""))
            phase = str(snapshot.get("status", {}).get("phase", ""))
            last_detail = f"pod={pod_name} uid={uid or 'missing'} phase={phase or 'missing'}"
            if snapshot and uid and uid != previous_uid and self.pod_is_ready(snapshot):
                self.write_log(bundle, "log.platform", "K8S HA POD READY", last_detail)
                return
            time.sleep(1.0)
        self.write_log(bundle, "log.platform", "K8S HA POD READY FAILED", last_detail)
        raise RuntimeError(f"pod/{pod_name} did not become a new Ready pod")

    def control_playsbc_pod(self, pod_name: str, action: str, bundle: Path) -> None:
        path = "/control/drain" if action == "drain" else "/control/undrain"
        python = (
            "import urllib.request; "
            f"print(urllib.request.urlopen('http://127.0.0.1:8080{path}', timeout=3).read().decode())"
        )
        result = self.kubectl(
            "exec",
            pod_name,
            "-c",
            "playsbc",
            "--",
            "python3",
            "-c",
            python,
            check=False,
            timeout=10,
        )
        self.write_log(
            bundle,
            "log.platform",
            "K8S HA CONTROL",
            f"pod={pod_name} action={action} returncode={result.returncode}\n{result.stdout}{result.stderr}",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"control {action} failed on {pod_name}")

    def run_ha_action(self, profile: SimpleNamespace, bundle: Path, phases: PhaseLog, phase: str) -> None:
        action = getattr(profile, "k8s_ha_action", {}) or {}
        if not isinstance(action, dict) or action.get("phase") != phase:
            return
        started = time.monotonic()
        target = str(action.get("target") or "playsbc")
        operation = str(action.get("operation") or "delete-pod")
        pod_index = int(action.get("pod_index", 0) or 0)
        settle_seconds = float(action.get("settle_seconds", 0.0) or 0.0)
        detail_lines = [f"profile={profile.profile} phase={phase} target={target} operation={operation}"]

        if operation == "delete-pod":
            pod_name = self.workload_pod_name(target, pod_index)
            self.snapshot_ha_target_logs(target, pod_name, bundle, phase)
            previous_uid = self.pod_uid(pod_name)
            delete_result = self.kubectl("delete", "pod", pod_name, "--wait=false", check=False, timeout=30)
            detail_lines.append(f"deleted_pod={pod_name} returncode={delete_result.returncode}")
            detail_lines.append(f"previous_uid={previous_uid or 'unknown'}")
            detail_lines.append(delete_result.stdout + delete_result.stderr)
            self.write_log(bundle, "log.platform", "K8S HA ACTION", "\n".join(detail_lines))
            if delete_result.returncode != 0:
                raise RuntimeError(delete_result.stderr.strip() or delete_result.stdout.strip())
            self.wait_pod_ready(pod_name, bundle, previous_uid=previous_uid)
        elif operation == "drain-all":
            pods = self.workload_pod_names("playsbc")
            if not pods:
                raise RuntimeError("No PlaySBC pods found for drain-all")
            for pod_name in pods:
                self.control_playsbc_pod(pod_name, "drain", bundle)
            detail_lines.append(f"drained_pods={','.join(pods)}")
            self.write_log(bundle, "log.platform", "K8S HA ACTION", "\n".join(detail_lines))
        elif operation == "undrain-all":
            pods = self.workload_pod_names("playsbc")
            for pod_name in pods:
                self.control_playsbc_pod(pod_name, "undrain", bundle)
            detail_lines.append(f"undrained_pods={','.join(pods)}")
            self.write_log(bundle, "log.platform", "K8S HA ACTION", "\n".join(detail_lines))
        else:
            raise RuntimeError(f"Unsupported K8s HA operation: {operation}")

        if settle_seconds > 0:
            time.sleep(settle_seconds)
        phases.append(
            "HA Fault Injection",
            "passed",
            started,
            "; ".join(line.strip() for line in detail_lines if line.strip()),
        )

    def snapshot_ha_target_logs(self, target: str, pod_name: str, bundle: Path, phase: str) -> None:
        """Retain pre-fault evidence before Kubernetes permanently removes a target pod."""
        if target != "playsbc":
            logs = self.kubectl("logs", f"pod/{pod_name}", f"--tail={self.args.deployment_log_tail}", check=False)
            (bundle / f"ha-prefault-{target}-{phase}.log").write_text(
                logs.stdout + logs.stderr,
                encoding="utf-8",
            )
            return
        for filename in B2BUA_LOG_FILES:
            result = self.kubectl(
                "exec",
                pod_name,
                "-c",
                "playsbc",
                "--",
                "cat",
                f"/tmp/playsbc-logs/{filename}",
                check=False,
                timeout=15,
            )
            if result.returncode != 0 or not result.stdout.strip():
                continue
            with (bundle / filename).open("a", encoding="utf-8") as handle:
                handle.write(f"\n===== pre-fault pod/{pod_name} phase={phase} {filename} =====\n")
                handle.write(result.stdout.rstrip() + "\n")

    def kubectl(self, *parts: str, timeout: Optional[int] = None, input_text: Optional[str] = None, check: bool = False) -> CommandResult:
        command = [self.args.kubectl_bin]
        if self.args.namespace:
            command.extend(["-n", self.args.namespace])
        command.extend(parts)
        return run_command(command, timeout=timeout or self.args.timeout, input_text=input_text, check=check)

    def kubectl_cluster(self, *parts: str, timeout: Optional[int] = None, input_text: Optional[str] = None, check: bool = False) -> CommandResult:
        command = [self.args.kubectl_bin, *parts]
        return run_command(command, timeout=timeout or self.args.timeout, input_text=input_text, check=check)

    def helm_values(self) -> dict[str, Any]:
        result = run_command(
            [
                self.args.helm_bin,
                "get",
                "values",
                self.args.helm_release,
                "--namespace",
                self.args.namespace,
                "--all",
                "-o",
                "json",
            ],
            timeout=self.args.helm_timeout,
            check=True,
        )
        return json.loads(result.stdout or "{}")

    def capture_original_values(self) -> None:
        if self.original_values is None:
            self.original_values = self.helm_values()

    def restore_original_values(self, report_dir: Path) -> Optional[str]:
        if self.args.no_restore_helm_values or self.original_values is None:
            return None
        restore_path = report_dir / f"{self.run_id}-helm-restore-values.json"
        restore_path.parent.mkdir(parents=True, exist_ok=True)
        restore_path.write_text(json.dumps(self.original_values, indent=2, sort_keys=True), encoding="utf-8")
        result = self.helm_upgrade_values(
            restore_path,
            report_dir / f"{self.run_id}-helm-restore.log",
            check=False,
        )
        if result.returncode != 0:
            return f"Helm restore failed: {command_failure_detail(result)}"
        restored_ref = self.workload_ref_for_active_state(
            self.values_active_active_enabled(self.original_values),
            self.args.deployment,
        )
        rollout = self.rollout_status(restored_ref, check=False)
        (report_dir / f"{self.run_id}-helm-restore-rollout.log").write_text(
            rollout.stdout + rollout.stderr,
            encoding="utf-8",
        )
        if rollout.returncode != 0:
            return f"Helm restore rollout failed: {rollout.stderr.strip() or rollout.stdout.strip()}"
        return None

    def ensure_tls_secret(self, bundle: Path) -> None:
        if self.tls_secret_prepared:
            return
        cert_text, key_text = self.generate_lab_tls_pair(bundle)
        manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": self.args.tls_secret_name,
                "labels": {
                    "app.kubernetes.io/name": "playsbc-k8s-regression",
                    "app.kubernetes.io/part-of": "playsbc",
                    "playsbc-regression-run": self.run_id,
                },
            },
            "type": "kubernetes.io/tls",
            "stringData": {
                "tls.crt": cert_text,
                "tls.key": key_text,
                "ca.crt": cert_text,
            },
        }
        result = self.kubectl("apply", "-f", "-", input_text=json.dumps(manifest), check=True)
        self.write_log(bundle, "log.platform", "TLS REGRESSION SECRET READY", result.stdout + result.stderr)
        self.tls_secret_prepared = True

    def rotate_tls_secret(self, bundle: Path) -> None:
        """Replace the mounted certificate without restarting the PlaySBC workload."""
        self.tls_secret_prepared = False
        self.ensure_tls_secret(bundle)
        self.write_log(bundle, "log.platform", "TLS REGRESSION SECRET ROTATED", self.args.tls_secret_name)

    def generate_lab_tls_pair(self, bundle: Path) -> tuple[str, str]:
        ensure_binary("openssl")
        with tempfile.TemporaryDirectory(prefix="playsbc-k8s-tls-") as tmp:
            tmp_path = Path(tmp)
            cert_path = tmp_path / "tls.crt"
            key_path = tmp_path / "tls.key"
            san = (
                f"DNS:{self.args.service},"
                f"DNS:{self.args.service}.{self.args.namespace}.svc,"
                "DNS:localhost,IP:127.0.0.1"
            )
            result = run_command(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key_path),
                    "-out",
                    str(cert_path),
                    "-days",
                    "30",
                    "-subj",
                    f"/CN={self.args.service}",
                    "-addext",
                    f"subjectAltName={san}",
                ],
                timeout=30,
                check=True,
            )
            self.write_log(bundle, "log.platform", "TLS REGRESSION CERT GENERATED", result.stdout + result.stderr)
            return cert_path.read_text(encoding="utf-8"), key_path.read_text(encoding="utf-8")

    def prepare_common(self, bundle: Path, phases: PhaseLog) -> None:
        started = time.monotonic()
        if not self.args.skip_namespace_check:
            self.kubectl_cluster("get", "namespace", self.args.namespace, check=True)
        self.kubectl("get", "service", self.args.service, check=True)
        self.kubectl("get", "service", self.args.rtpengine_service, check=False)
        manifest = scenario_configmap_manifest(self.args.configmap)
        result = self.kubectl("apply", "-f", "-", input_text=json.dumps(manifest), check=True)
        self.write_log(bundle, "log.platform", "K8S REGRESSION PREPARED", result.stdout or "scenario configmap applied")
        aks_detail = (
            self.validate_aks_exposure(bundle, stage="pre-profile", require_rtpengine_match=False)
            if getattr(self.args, "aks_mode", False)
            else "aks_mode=false"
        )
        phases.append(
            "Setup Preparation",
            "passed",
            started,
            (
                f"Verified namespace={self.args.namespace}, service={self.args.service}:{self.args.sip_port}, "
                f"namespace_check={not self.args.skip_namespace_check}, and applied ConfigMap={self.args.configmap} "
                f"with SIPp XML scenarios. {aks_detail}"
            ),
        )

    def validate_aks_exposure(
        self,
        bundle: Path,
        *,
        stage: str = "post-profile",
        require_rtpengine_match: bool = False,
    ) -> str:
        suffix = "" if stage == "post-profile" else f"-{stage}"
        selector = getattr(self.args, "aks_services_selector", "playsbc.io/cloud=azure")
        result = self.kubectl("get", "svc", "-l", selector, "-o", "json", check=False)
        (bundle / f"aks-services{suffix}.json").write_text(result.stdout + result.stderr, encoding="utf-8")
        wide = self.kubectl("get", "svc", "-l", selector, "-o", "wide", check=False)
        (bundle / f"aks-services-wide{suffix}.log").write_text(wide.stdout + wide.stderr, encoding="utf-8")
        described = self.kubectl("describe", "svc", "-l", selector, check=False)
        (bundle / f"aks-services-describe{suffix}.log").write_text(
            described.stdout + described.stderr,
            encoding="utf-8",
        )

        issues: list[str] = []
        items: list[dict[str, Any]] = []
        try:
            parsed = json.loads(result.stdout or "{}")
            raw_items = parsed.get("items", []) if isinstance(parsed, dict) else []
            items = [item for item in raw_items if isinstance(item, dict)]
        except json.JSONDecodeError as exc:
            issues.append(f"invalid_service_json={exc}")

        def exposure(item: dict[str, Any]) -> str:
            return str(item.get("metadata", {}).get("labels", {}).get("playsbc.io/exposure", ""))

        def service_name(item: dict[str, Any]) -> str:
            return str(item.get("metadata", {}).get("name", "unknown"))

        def ingress_ips(item: dict[str, Any]) -> list[str]:
            ingress = item.get("status", {}).get("loadBalancer", {}).get("ingress", [])
            if not isinstance(ingress, list):
                return []
            values: list[str] = []
            for entry in ingress:
                if not isinstance(entry, dict):
                    continue
                value = str(entry.get("ip") or entry.get("hostname") or "")
                if value:
                    values.append(value)
            return values

        def port_tuples(item: dict[str, Any]) -> set[tuple[str, int]]:
            ports = item.get("spec", {}).get("ports", [])
            tuples: set[tuple[str, int]] = set()
            if isinstance(ports, list):
                for port in ports:
                    if not isinstance(port, dict):
                        continue
                    try:
                        tuples.add((str(port.get("protocol", "")).upper(), int(port.get("port", 0))))
                    except (TypeError, ValueError):
                        continue
            return tuples

        sip_public = [item for item in items if exposure(item) == "sip-public"]
        sip_private = [item for item in items if exposure(item) == "sip-private"]
        rtp_public = [item for item in items if exposure(item) == "rtp-public"]
        sip_public_ingress = [value for item in sip_public for value in ingress_ips(item)]
        rtp_public_ingress = [value for item in rtp_public for value in ingress_ips(item)]
        if getattr(self.args, "aks_require_azure_services", False) and not sip_public:
            issues.append("missing_sip_public_loadbalancer_service")
        if getattr(self.args, "aks_require_public_rtp_ingress", False) and not rtp_public:
            issues.append("missing_rtp_public_loadbalancer_service")

        for item in sip_public:
            name = service_name(item)
            spec = item.get("spec", {})
            annotations = item.get("metadata", {}).get("annotations", {})
            ports = port_tuples(item)
            if spec.get("type") != "LoadBalancer":
                issues.append(f"{name}:type_not_loadbalancer")
            if spec.get("externalTrafficPolicy") != "Local":
                issues.append(f"{name}:externalTrafficPolicy_not_Local")
            if ("UDP", int(self.args.sip_port)) not in ports:
                issues.append(f"{name}:missing_sip_udp_{self.args.sip_port}")
            if ("TCP", int(self.args.sip_port)) not in ports:
                issues.append(f"{name}:missing_sip_tcp_{self.args.sip_port}")
            if ("TCP", int(self.args.tls_port)) not in ports:
                issues.append(f"{name}:missing_sip_tls_{self.args.tls_port}")
            has_static_ref = bool(
                annotations.get("service.beta.kubernetes.io/azure-pip-name")
                or annotations.get("service.beta.kubernetes.io/azure-load-balancer-ipv4")
            )
            if getattr(self.args, "aks_require_static_sip", False) and not has_static_ref:
                issues.append(f"{name}:missing_static_public_ip_annotation")
            ingress = item.get("status", {}).get("loadBalancer", {}).get("ingress", [])
            if getattr(self.args, "aks_require_public_sip_ingress", False) and not ingress:
                issues.append(f"{name}:missing_allocated_public_ingress")

        for item in sip_private:
            name = service_name(item)
            annotations = item.get("metadata", {}).get("annotations", {})
            if annotations.get("service.beta.kubernetes.io/azure-load-balancer-internal") != "true":
                issues.append(f"{name}:missing_internal_loadbalancer_annotation")

        for item in rtp_public:
            name = service_name(item)
            spec = item.get("spec", {})
            if spec.get("type") != "LoadBalancer":
                issues.append(f"{name}:type_not_loadbalancer")
            if spec.get("externalTrafficPolicy") != "Local":
                issues.append(f"{name}:externalTrafficPolicy_not_Local")
            ingress = item.get("status", {}).get("loadBalancer", {}).get("ingress", [])
            if getattr(self.args, "aks_require_public_rtp_ingress", False) and not ingress:
                issues.append(f"{name}:missing_allocated_public_ingress")
            ports = port_tuples(item)
            for protocol, port in ports:
                if protocol != "UDP":
                    issues.append(f"{name}:non_udp_media_port_{port}")
            if getattr(self.args, "aks_require_rtp_port_range", False):
                expected_ports = set(range(int(self.args.aks_rtp_port_min), int(self.args.aks_rtp_port_max) + 1))
                exposed_ports = {port for protocol, port in ports if protocol == "UDP"}
                missing_ports = sorted(expected_ports - exposed_ports)
                if missing_ports:
                    issues.append(
                        f"{name}:missing_rtp_udp_ports_{missing_ports[0]}-{missing_ports[-1]}_count_{len(missing_ports)}"
                    )

        rtpengine_pod_result = self.kubectl(
            "get",
            "pod",
            "-l",
            "app.kubernetes.io/name=playsbc-rtpengine",
            "-o",
            "json",
            check=False,
        )
        (bundle / f"aks-rtpengine-pods{suffix}.json").write_text(
            rtpengine_pod_result.stdout + rtpengine_pod_result.stderr,
            encoding="utf-8",
        )
        advertised_matches: list[str] = []
        rtpengine_pods: list[str] = []
        try:
            parsed_pods = json.loads(rtpengine_pod_result.stdout or "{}")
            pod_items = parsed_pods.get("items", []) if isinstance(parsed_pods, dict) else []
            for pod in pod_items:
                if not isinstance(pod, dict):
                    continue
                pod_name = str(pod.get("metadata", {}).get("name", "unknown"))
                rtpengine_pods.append(pod_name)
                container_specs = pod.get("spec", {}).get("containers", [])
                command_text = json.dumps(container_specs, sort_keys=True)
                for ingress_ip in rtp_public_ingress:
                    if f"!{ingress_ip}" in command_text:
                        advertised_matches.append(f"{pod_name}:{ingress_ip}")
        except json.JSONDecodeError as exc:
            issues.append(f"invalid_rtpengine_pod_json={exc}")
        if require_rtpengine_match and rtp_public_ingress and not advertised_matches:
            issues.append(f"rtpengine_advertised_ip_mismatch_expected_{rtp_public_ingress[0]}")

        summary = {
            "stage": stage,
            "selector": selector,
            "services": [service_name(item) for item in items],
            "sip_public": [service_name(item) for item in sip_public],
            "sip_private": [service_name(item) for item in sip_private],
            "rtp_public": [service_name(item) for item in rtp_public],
            "sip_public_ingress": sip_public_ingress,
            "rtp_public_ingress": rtp_public_ingress,
            "rtpengine_pods": rtpengine_pods,
            "rtpengine_advertised_ip_matches": advertised_matches,
            "require_rtpengine_match": require_rtpengine_match,
            "require_azure_services": bool(getattr(self.args, "aks_require_azure_services", False)),
            "require_static_sip": bool(getattr(self.args, "aks_require_static_sip", False)),
            "require_public_sip_ingress": bool(getattr(self.args, "aks_require_public_sip_ingress", False)),
            "require_public_rtp_ingress": bool(getattr(self.args, "aks_require_public_rtp_ingress", False)),
            "require_rtp_port_range": bool(getattr(self.args, "aks_require_rtp_port_range", False)),
            "rtp_port_range": {
                "min": int(getattr(self.args, "aks_rtp_port_min", 30000)),
                "max": int(getattr(self.args, "aks_rtp_port_max", 30049)),
            },
            "issues": issues,
        }
        (bundle / f"aks-validation{suffix}.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        detail = (
            f"AKS exposure validation stage={stage} services={','.join(summary['services']) or 'none'} "
            f"sip_public={','.join(summary['sip_public']) or 'none'} "
            f"sip_public_ingress={','.join(summary['sip_public_ingress']) or 'none'} "
            f"sip_private={','.join(summary['sip_private']) or 'none'} "
            f"rtp_public={','.join(summary['rtp_public']) or 'none'} "
            f"rtp_public_ingress={','.join(summary['rtp_public_ingress']) or 'none'} "
            f"rtpengine_advertised_ip_matches={','.join(summary['rtpengine_advertised_ip_matches']) or 'none'} "
            f"issues={','.join(issues) or 'none'}"
        )
        self.write_log(bundle, "log.platform", f"AKS AZURE EXPOSURE VALIDATION {stage.upper()}", detail)
        if issues and (
            getattr(self.args, "aks_require_azure_services", False)
            or getattr(self.args, "aks_require_static_sip", False)
            or getattr(self.args, "aks_require_public_sip_ingress", False)
            or getattr(self.args, "aks_require_public_rtp_ingress", False)
            or getattr(self.args, "aks_require_rtp_port_range", False)
        ):
            raise RuntimeError(f"AKS Azure exposure validation failed: {', '.join(issues)}")
        return detail

    def build_and_load_sipp_image(self, bundle: Path, phases: PhaseLog) -> None:
        if not self.args.build_sipp_image and not self.args.kind_load_image:
            return
        started = time.monotonic()
        if self.image_prepared:
            phases.append(
                "Configuration",
                "passed",
                started,
                f"Reused SIPp image preparation from an earlier profile in this run: image={self.args.sipp_image}.",
            )
            return
        if self.args.build_sipp_image:
            ensure_binary("docker")
            result = run_command(
                ["docker", "build", "-f", str(ROOT / "docker" / "sipp.Dockerfile"), "-t", self.args.sipp_image, "."],
                timeout=self.args.image_build_timeout,
                check=True,
            )
            self.write_log(bundle, "log.platform", "SIPP IMAGE BUILD", result.stdout + result.stderr)
        if self.args.kind_load_image:
            ensure_binary("kind")
            result = run_command(
                ["kind", "load", "docker-image", self.args.sipp_image, "--name", self.args.kind_cluster],
                timeout=self.args.timeout,
                check=True,
            )
            self.write_log(bundle, "log.platform", "SIPP IMAGE KIND LOAD", result.stdout + result.stderr)
        phases.append(
            "Configuration",
            "passed",
            started,
            f"Prepared SIPp image={self.args.sipp_image}; build={self.args.build_sipp_image}; kind_load={self.args.kind_load_image}.",
        )
        self.image_prepared = True

    def create_agent(self, name: str, bundle: Path, realm: str = "", tls_secret: str = "") -> str:
        manifest = pod_manifest(
            name,
            self.args.sipp_image,
            self.args.image_pull_policy,
            self.args.configmap,
            self.run_id,
            realm=realm,
            tls_secret=tls_secret,
        )
        self.kubectl("apply", "-f", "-", input_text=json.dumps(manifest), check=True)
        self.kubectl("wait", "--for=condition=Ready", f"pod/{name}", f"--timeout={self.args.pod_ready_timeout}s", check=True)
        ip_result = self.kubectl("get", "pod", name, "-o", "jsonpath={.status.podIP}", check=True)
        pod_ip = ip_result.stdout.strip()
        if not pod_ip:
            describe = self.kubectl("describe", "pod", name, check=False)
            self.write_log(bundle, "log.platform", f"POD {name} DESCRIBE", describe.stdout + describe.stderr)
            raise RuntimeError(f"Pod {name} did not receive an IP address")
        self.write_log(bundle, "log.platform", f"POD {name} READY", f"pod_ip={pod_ip}")
        return pod_ip

    def create_agent_service(self, name: str, pod_name: str, bundle: Path) -> None:
        manifest = agent_service_manifest(name, pod_name, self.run_id)
        result = self.kubectl("apply", "-f", "-", input_text=json.dumps(manifest), check=True)
        self.write_log(bundle, "log.platform", f"SERVICE {name} READY", result.stdout + result.stderr)

    def run_layer2_socket_probe(
        self, profile: SimpleNamespace, core_pod: str, bundle: Path
    ) -> Optional[CommandResult]:
        probe = str(getattr(profile, "k8s_layer2_probe", "") or "")
        if not probe:
            return None
        playsbc_pod = self.workload_pod_name("playsbc", 0)
        snapshot = self.pod_snapshot(playsbc_pod)
        target_ip = str(snapshot.get("status", {}).get("podIP", ""))
        if not target_ip:
            raise RuntimeError(f"Could not resolve pod IP for Layer 2 probe target {playsbc_pod}")
        port = self.args.tls_port if probe in {"tls-sni", "tls-rotation"} else self.args.sip_port
        if probe == "tls-rotation":
            fingerprint_script = (
                "import hashlib,socket,ssl,sys; c=ssl._create_unverified_context(); "
                "s=c.wrap_socket(socket.create_connection((sys.argv[1],int(sys.argv[2])),3),server_hostname=sys.argv[3]); "
                "print(hashlib.sha256(s.getpeercert(binary_form=True)).hexdigest()); s.close()"
            )
            base = [
                self.args.kubectl_bin, "-n", self.args.namespace, "exec", core_pod, "--",
                "python3", "-c", fingerprint_script, target_ip, str(port), self.args.service,
            ]
            before = run_command(base, timeout=20)
            if before.returncode != 0 or not before.stdout.strip():
                return before
            self.rotate_tls_secret(bundle)
            wait_script = (
                "import hashlib,socket,ssl,sys,time; old=sys.argv[4]; deadline=time.time()+120; c=ssl._create_unverified_context(); new=old\n"
                "while time.time()<deadline:\n"
                " try:\n"
                "  s=c.wrap_socket(socket.create_connection((sys.argv[1],int(sys.argv[2])),3),server_hostname=sys.argv[3]); new=hashlib.sha256(s.getpeercert(binary_form=True)).hexdigest(); s.close()\n"
                "  if new != old: break\n"
                " except OSError: pass\n"
                " time.sleep(2)\n"
                "print('certificate_rotated=',new!=old,'before=',old,'after=',new); raise SystemExit(0 if new!=old else 1)"
            )
            command = [
                self.args.kubectl_bin, "-n", self.args.namespace, "exec", core_pod, "--",
                "python3", "-c", wait_script, target_ip, str(port), self.args.service, before.stdout.strip(),
            ]
            result = run_command(command, timeout=135)
        else:
            command = [
                self.args.kubectl_bin, "-n", self.args.namespace, "exec", core_pod, "--",
                "python3", "-c", layer2_probe_script(probe), target_ip, str(port), self.args.service,
            ]
            result = run_command(command, timeout=20)
        step_dir = bundle / f"layer2-{probe}-probe"
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "command.txt").write_text(command_text(command) + "\n", encoding="utf-8")
        (step_dir / "stdout.log").write_text(result.stdout, encoding="utf-8")
        (step_dir / "stderr.log").write_text(result.stderr, encoding="utf-8")
        self.write_log(
            bundle, "log.networking", f"LAYER2 {probe.upper()} PROBE",
            f"target={target_ip}:{port} returncode={result.returncode}\n{result.stdout}{result.stderr}",
        )
        return result

    def delete_run_pods(self, bundle: Path) -> CommandResult:
        selector = f"playsbc-regression-run={self.run_id}"
        result = self.kubectl("delete", "pod", "-l", selector, "--ignore-not-found=true", check=False)
        service_result = self.kubectl("delete", "service", "-l", selector, "--ignore-not-found=true", check=False)
        self.write_log(bundle, "log.platform", "K8S REGRESSION POD CLEANUP", result.stdout + result.stderr)
        self.write_log(bundle, "log.platform", "K8S REGRESSION SERVICE CLEANUP", service_result.stdout + service_result.stderr)
        return result

    def sipp_exec_command(self, pod: str, sipp_args: list[str]) -> list[str]:
        shell_command = f"cd /tmp && {shlex.join(['sipp', *sipp_args])}"
        return [self.args.kubectl_bin, "-n", self.args.namespace, "exec", pod, "--", "sh", "-lc", shell_command]

    def run_sipp_step(self, pod: str, step_name: str, sipp_args: list[str], bundle: Path, timeout: Optional[int] = None) -> CommandResult:
        command = self.sipp_exec_command(pod, sipp_args)
        result = run_command(command, timeout=timeout or self.args.sipp_timeout)
        step_dir = bundle / step_name
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "command.txt").write_text(command_text(command) + "\n", encoding="utf-8")
        (step_dir / "stdout.log").write_text(result.stdout, encoding="utf-8")
        self.write_sipp_stderr(step_dir, result.stderr)
        self.write_log(bundle, "log.sipp", f"{step_name.upper()} COMMAND", command_text(command))
        self.write_log(
            bundle,
            "log.sipp",
            f"{step_name.upper()} RESULT",
            f"returncode={result.returncode} duration_seconds={result.duration_seconds:.3f}",
        )
        self.collect_sipp_traces(pod, step_dir)
        return result

    def start_sipp_process(self, pod: str, step_name: str, sipp_args: list[str], bundle: Path) -> subprocess.Popen[str]:
        command = self.sipp_exec_command(pod, sipp_args)
        step_dir = bundle / step_name
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "command.txt").write_text(command_text(command) + "\n", encoding="utf-8")
        stdout = (step_dir / "stdout.log").open("w", encoding="utf-8")
        stderr = (step_dir / "stderr.log").open("w", encoding="utf-8")
        self.write_log(bundle, "log.sipp", f"{step_name.upper()} COMMAND", command_text(command))
        process = subprocess.Popen(command, cwd=ROOT, text=True, stdout=stdout, stderr=stderr)
        process._playsbc_stdout = stdout  # type: ignore[attr-defined]
        process._playsbc_stderr = stderr  # type: ignore[attr-defined]
        process._playsbc_step_dir = step_dir  # type: ignore[attr-defined]
        return process

    def wait_for_sipp_listener(
        self, process: subprocess.Popen[str], pod: str, step_name: str,
        sipp_args: list[str], bundle: Path,
    ) -> None:
        """Wait for the UAS socket, rather than assuming one second is enough."""
        try:
            port = int(sipp_args[sipp_args.index("-p") + 1])
        except (ValueError, IndexError):
            raise RuntimeError(f"{step_name}: SIPp server arguments have no valid -p port")
        transport = "udp"
        if "-t" in sipp_args:
            mode = sipp_args[sipp_args.index("-t") + 1].lower()
            transport = "tcp" if mode.startswith(("t", "l")) else "udp"
        proc_files = (
            "/proc/net/tcp /proc/net/tcp6"
            if transport == "tcp"
            else "/proc/net/udp /proc/net/udp6"
        )
        state_test = '$4 == "0A"' if transport == "tcp" else '$4 == "07"'
        probe = (
            f"port=$(printf '%04X' {port}); "
            f"awk -v p=\":$port\" '{'{'} if (index($2,p) && {state_test}) "
            f"found=1 {'}'} END {'{'} exit !found {'}'}' {proc_files}"
        )
        deadline = time.monotonic() + float(self.args.sipp_ready_timeout)
        last = ""
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"{step_name}: SIPp exited before listening (returncode={process.returncode})")
            result = run_command(
                [self.args.kubectl_bin, "-n", self.args.namespace, "exec", pod, "--", "sh", "-lc", probe],
                timeout=min(5, self.args.timeout), check=False,
            )
            last = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                self.write_log(
                    bundle, "log.sipp", f"{step_name.upper()} READY",
                    f"transport={transport} port={port}",
                )
                return
            time.sleep(0.2)
        raise RuntimeError(
            f"{step_name}: SIPp did not listen on {transport}/{port} within "
            f"{self.args.sipp_ready_timeout}s{f': {last}' if last else ''}"
        )

    def start_srtp_sender(
        self,
        profile: SimpleNamespace,
        core_pod: str,
        core_ip: str,
        peer_pod: str,
        peer_ip: str,
        bundle: Path,
    ) -> tuple[str, subprocess.Popen[str]] | None:
        if getattr(profile, "uac_srtp", False):
            role, pod, bind_ip = "core", core_pod, core_ip
        elif getattr(profile, "uas_srtp", False):
            role, pod, bind_ip = "peer", peer_pod, peer_ip
        else:
            return None

        step_name = f"{role}-secure-srtp-sender"
        command = [
            self.args.kubectl_bin,
            "-n",
            self.args.namespace,
            "exec",
            pod,
            "--",
            *srtp_sender_command(bind_ip),
        ]
        step_dir = bundle / step_name
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "command.txt").write_text(command_text(command) + "\n", encoding="utf-8")
        stdout = (step_dir / "stdout.log").open("w", encoding="utf-8")
        stderr = (step_dir / "stderr.log").open("w", encoding="utf-8")
        self.write_log(bundle, "log.sipp", f"{step_name.upper()} COMMAND", command_text(command))
        process = subprocess.Popen(command, cwd=ROOT, text=True, stdout=stdout, stderr=stderr)
        process._playsbc_stdout = stdout  # type: ignore[attr-defined]
        process._playsbc_stderr = stderr  # type: ignore[attr-defined]
        process._playsbc_step_dir = step_dir  # type: ignore[attr-defined]
        if not wait_for_process_log_marker(process, step_dir / "stdout.log", "SRTP sender ready"):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self.close_process_files(process)
            stdout_text = (step_dir / "stdout.log").read_text(encoding="utf-8", errors="replace")
            stderr_text = (step_dir / "stderr.log").read_text(encoding="utf-8", errors="replace")
            detail = "\n".join(value.strip() for value in (stdout_text, stderr_text) if value.strip())
            raise RuntimeError(f"Secure SRTP sender did not become ready in {pod}: {detail or 'no output'}")
        return step_name, process

    def wait_for_srtp_sender(
        self,
        sender: tuple[str, subprocess.Popen[str]] | None,
        bundle: Path,
    ) -> int | None:
        if sender is None:
            return None
        step_name, process = sender
        try:
            returncode = process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            returncode = 124
        finally:
            self.close_process_files(process)
        self.write_log(bundle, "log.sipp", f"{step_name.upper()} RESULT", f"returncode={returncode}")
        return int(returncode)

    def write_sipp_stderr(self, step_dir: Path, stderr_text: str) -> None:
        filtered, suppressed = normalize_sipp_stderr(stderr_text)
        if suppressed:
            (step_dir / "stderr.raw.log").write_text(stderr_text, encoding="utf-8")
        (step_dir / "stderr.log").write_text(filtered, encoding="utf-8")

    def finalize_sipp_step_logs(self, step_dir: Path) -> None:
        stderr_path = step_dir / "stderr.log"
        if not stderr_path.exists():
            return
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
        self.write_sipp_stderr(step_dir, stderr_text)

    def close_process_files(self, process: subprocess.Popen[str]) -> None:
        for attr in ("_playsbc_stdout", "_playsbc_stderr"):
            handle = getattr(process, attr, None)
            if handle:
                handle.close()

    def collect_sipp_traces(self, pod: str, step_dir: Path) -> None:
        trace_command = [
            self.args.kubectl_bin,
            "-n",
            self.args.namespace,
            "exec",
            pod,
            "--",
            "sh",
            "-lc",
            (
                "for f in /tmp/*_messages.log /tmp/*_errors.log /tmp/*_statistics.log "
                "/tmp/*_counts.csv /tmp/*_logs.log /tmp/*srtp*.log; do "
                "[ -e \"$f\" ] && echo \"===== $f =====\" && cat \"$f\"; "
                "done"
            ),
        ]
        result = run_command(trace_command, timeout=20)
        (step_dir / "sipp-traces.log").write_text(result.stdout + result.stderr, encoding="utf-8")

    def collect_k8s_evidence(
        self,
        bundle: Path,
        profile_name: str,
        profile_started_at: dt.datetime,
    ) -> None:
        include_rtpengine = False
        include_rasa = False
        since_time = kubectl_since_time(profile_started_at)
        call_ids = evidence_call_ids(bundle)
        if profile_name in CATALOG_PROFILES:
            profile = profile_values(profile_name, self.run_id)
            include_rtpengine = profile_enables_rtpengine_deployment(profile, self.args)
            include_rasa = profile_uses_real_rasa(profile)
        elif profile_name in RASA_NLU_PROFILES:
            include_rasa = True
        commands = {
            "kubectl-pods.log": ["get", "pods", "-o", "wide"],
            "kubectl-services.log": ["get", "svc", "-o", "wide"],
            "kubectl-statefulsets.log": ["get", "statefulsets", "-o", "wide"],
            "kubectl-events.log": ["get", "events", "--sort-by=.lastTimestamp"],
            "playsbc.log": [
                "logs",
                "-l",
                f"app.kubernetes.io/name=playsbc,app.kubernetes.io/instance={self.args.helm_release}",
                "--all-containers=true",
                "--prefix=true",
                f"--since-time={since_time}",
                f"--tail={self.args.deployment_log_tail}",
            ],
        }
        if include_rtpengine:
            commands["rtpengine.log"] = [
                "logs",
                "-l",
                f"app.kubernetes.io/name=playsbc-rtpengine,app.kubernetes.io/instance={self.args.helm_release}",
                "--all-containers=true",
                "--prefix=true",
                f"--since-time={since_time}",
                f"--tail={self.args.deployment_log_tail}",
            ]
        else:
            (bundle / "rtpengine.log").write_text(
                f"RTPengine evidence not applicable for profile={profile_name}; deployment not expected for this profile.\n",
                encoding="utf-8",
            )
        if include_rasa:
            commands["rasa.log"] = [
                "logs",
                f"deployment/{self.args.service}-rasa",
                f"--since-time={since_time}",
                f"--tail={self.args.deployment_log_tail}",
            ]
        else:
            (bundle / "rasa.log").write_text(
                f"Real Rasa evidence not applicable for profile={profile_name}; deployment not expected for this profile.\n",
                encoding="utf-8",
            )
        for filename, parts in commands.items():
            result = self.kubectl(*parts, check=False)
            text = result.stdout + result.stderr
            if filename == "playsbc.log":
                scoped, removed = scope_playsbc_log(text, call_ids)
                if removed:
                    (bundle / "playsbc.raw.log").write_text(text, encoding="utf-8")
                    self.write_log(
                        bundle,
                        "log.platform",
                        "PLAY SBC LOG SCOPE",
                        f"since_time={since_time} call_ids={len(call_ids)} unrelated_call_lines_removed={removed} raw=playsbc.raw.log",
                    )
                text = scoped
            (bundle / filename).write_text(text, encoding="utf-8")
        self.collect_playsbc_pod_evidence(bundle, profile_started_at)
        if profile_name in RASA_NLU_PROFILES:
            self.write_log(
                bundle,
                "log.platform",
                "PLAY SBC PERSISTENT LOG COPY SKIPPED",
                "chat/NLU profiles use rasa-nlu-results.json and log.rasa-nlu as primary evidence; SIP/RTP logs are not applicable",
            )
        else:
            self.collect_playsbc_persistent_logs(bundle)
        if include_rasa:
            self.collect_rasa_pod_evidence(bundle, profile_started_at)

    def start_packet_captures(
        self,
        profile: SimpleNamespace,
        bundle: Path,
        pods: list[tuple[str, str]],
    ) -> list[CaptureProcess]:
        if not pods:
            reason = "load_profile" if is_load_profile(profile) else "no_expected_packet_flow"
            self.write_log(bundle, "log.networking", "K8S PCAP CAPTURE SKIPPED", f"reason={reason}")
            return []
        captures: list[CaptureProcess] = []
        for role, pod in pods:
            remote_path = f"/tmp/{short_name(profile.profile)}-{role}.pcap"
            local_path = bundle / f"capture-{role}.pcap"
            step_dir = bundle / f"k8s-pcap-{role}"
            step_dir.mkdir(parents=True, exist_ok=True)
            capture_filter = k8s_pcap_capture_filter(profile)
            packet_limit = k8s_pcap_packet_limit(profile)
            packet_limit_arg = f" -c {packet_limit}" if packet_limit else ""
            shell_command = (
                f"rm -f {shlex.quote(remote_path)}; "
                f"tcpdump -i any --immediate-mode -U -n -s 0{packet_limit_arg} "
                f"-w {shlex.quote(remote_path)} {shlex.quote(capture_filter)}"
            )
            command = [self.args.kubectl_bin, "-n", self.args.namespace, "exec", pod, "--", "sh", "-lc", shell_command]
            (step_dir / "command.txt").write_text(command_text(command) + "\n", encoding="utf-8")
            stdout = (step_dir / "stdout.log").open("w", encoding="utf-8")
            stderr = (step_dir / "stderr.log").open("w", encoding="utf-8")
            process = subprocess.Popen(command, cwd=ROOT, text=True, stdout=stdout, stderr=stderr)
            process._playsbc_stdout = stdout  # type: ignore[attr-defined]
            process._playsbc_stderr = stderr  # type: ignore[attr-defined]
            captures.append(CaptureProcess(role=role, pod=pod, remote_path=remote_path, local_path=local_path, process=process))
        time.sleep(0.8)
        failed = [capture.role for capture in captures if capture.process.poll() is not None]
        if failed:
            self.stop_packet_captures(captures)
            raise RuntimeError(f"K8s tcpdump capture exited early for role(s): {', '.join(failed)}")
        self.write_log(
            bundle,
            "log.networking",
            "K8S PCAP CAPTURE STARTED",
            (
                f"filter={k8s_pcap_capture_filter(profile)} "
                f"packet_limit={k8s_pcap_packet_limit(profile) or 'none'} "
                + " ".join(f"{capture.role}={capture.pod}:{capture.remote_path}" for capture in captures)
            ),
        )
        return captures

    def stop_packet_captures(self, captures: list[CaptureProcess]) -> None:
        for capture in captures:
            if capture.process.poll() is None:
                self.kubectl("exec", capture.pod, "--", "sh", "-lc", "pkill -INT tcpdump || true", check=False)
                try:
                    capture.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    capture.process.terminate()
                    try:
                        capture.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        capture.process.kill()
                        capture.process.wait(timeout=3)
            self.close_process_files(capture.process)

    def collect_packet_captures(
        self,
        captures: list[CaptureProcess],
        bundle: Path,
        profile: Optional[SimpleNamespace] = None,
    ) -> bool:
        if not captures:
            return True
        ok = True
        copied: list[Path] = []
        for capture in captures:
            result = run_command(
                [
                    self.args.kubectl_bin,
                    "-n",
                    self.args.namespace,
                    "cp",
                    f"{capture.pod}:{capture.remote_path}",
                    str(capture.local_path),
                    "-c",
                    "sipp-agent",
                ],
                timeout=60,
                check=False,
            )
            (bundle / f"k8s-pcap-{capture.role}" / "copy.log").write_text(
                result.stdout + result.stderr,
                encoding="utf-8",
            )
            if result.returncode != 0 or not capture.local_path.exists() or capture.local_path.stat().st_size <= 24:
                ok = False
                continue
            copied.append(capture.local_path)
        leg_summary: dict[str, Any] = {}
        try:
            merged_bytes = merge_pcap_files(copied, bundle / "capture.pcap")
        except Exception as exc:
            ok = False
            merged_bytes = 0
            removed: list[str] = []
            self.write_log(bundle, "log.networking", "K8S PCAP MERGE FAILED", f"{type(exc).__name__}: {exc}")
        else:
            leg_failures: list[str] = []
            if merged_bytes > 0:
                leg_summary, leg_failures = write_pcap_leg_summary(
                    profile,
                    captures,
                    copied,
                    bundle / "capture.pcap",
                    bundle,
                )
                if leg_failures:
                    ok = False
            removed = prune_merged_capture_inputs(copied, bundle / "capture.pcap") if merged_bytes > 0 else []
        self.write_log(
            bundle,
            "log.networking",
            "K8S PCAP CAPTURE COLLECTED",
            (
                f"status={'passed' if ok and merged_bytes > 0 else 'failed'} "
                f"merged_sources={','.join(path.name for path in copied) or 'none'} "
                f"retained_file={'capture.pcap' if merged_bytes > 0 else 'none'} "
                f"discarded_role_pcaps={','.join(removed) or 'none'} "
                f"merge=timestamp_sorted merged_bytes={merged_bytes} "
                f"merged_roles={','.join(capture.role for capture in captures) or 'none'} "
                f"role_packet_counts={json.dumps(leg_summary.get('role_packet_counts', {}), sort_keys=True)} "
                f"invite_legs={leg_summary.get('invite_leg_count', 0)} "
                f"leg_validation={leg_summary.get('status', 'not-run')}"
            ),
        )
        return ok and merged_bytes > 0

    def collect_tmp_messages(self, pod: str) -> str:
        result = run_command(
            [
                self.args.kubectl_bin,
                "-n",
                self.args.namespace,
                "exec",
                pod,
                "--",
                "sh",
                "-lc",
                "cat /tmp/*_messages.log 2>/dev/null || true",
            ],
            timeout=10,
            check=False,
        )
        return result.stdout + result.stderr

    def discover_rtcp_targets(self, profile: SimpleNamespace, core_pod: str, peer_pod: str, bundle: Path) -> dict[str, RtcpTarget]:
        deadline = time.monotonic() + 8.0
        expected = set(rtcp_expected_senders(profile))
        targets: dict[str, RtcpTarget] = {}
        while time.monotonic() < deadline:
            if "core" in expected and "core" not in targets:
                core_trace = self.collect_tmp_messages(core_pod)
                core_target = extract_received_sdp_rtcp_target(core_trace, sip_start="SIP/2.0 200")
                if core_target:
                    targets["core"] = core_target
            if "peer" in expected and "peer" not in targets:
                peer_trace = self.collect_tmp_messages(peer_pod)
                peer_target = extract_received_sdp_rtcp_target(peer_trace, sip_start="INVITE ")
                if peer_target:
                    targets["peer"] = peer_target
            if expected.issubset(targets):
                self.write_log(
                    bundle,
                    "log.media",
                    "K8S RTCP TARGETS DISCOVERED",
                    " ".join(f"{role}={target.target_ip}:{target.target_port}" for role, target in sorted(targets.items())),
                )
                return targets
            time.sleep(0.2)
        missing = ",".join(sorted(expected - set(targets)))
        raise RuntimeError(f"Could not discover K8s RTCP target(s) from SIPp traces: {missing}")

    def start_rtcp_process(
        self,
        role: str,
        pod: str,
        pod_ip: str,
        target: RtcpTarget,
        profile: SimpleNamespace,
        bundle: Path,
    ) -> subprocess.Popen[str]:
        step_name = f"rtcp-{role}"
        step_dir = bundle / step_name
        step_dir.mkdir(parents=True, exist_ok=True)
        duration = max(1.0, (int(getattr(profile, "hold_ms", 1000)) / 1000.0) - float(getattr(profile, "media_start_delay", 1.0)) - 0.5)
        command_args = [
            "python3",
            "/app/tools/send_rtcp_reports.py",
            "--local-ip",
            pod_ip,
            "--source-port",
            "6001",
            "--target-ip",
            target.target_ip,
            "--target-port",
            str(target.target_port),
            "--ssrc",
            "0xC0DEC0DE",
            "--cname",
            f"{role}@playsbc-k8s",
            "--duration-seconds",
            f"{duration:.3f}",
            "--interval-seconds",
            "5",
        ]
        if bool(getattr(profile, "rtcp_receiver_reports", False)):
            command_args.append("--receiver-report")
        if should_expect_rtcp_reply(profile):
            command_args.append("--expect-reply")
        shell_command = f"cd /tmp && {shlex.join(command_args)}"
        command = [self.args.kubectl_bin, "-n", self.args.namespace, "exec", pod, "--", "sh", "-lc", shell_command]
        (step_dir / "command.txt").write_text(command_text(command) + "\n", encoding="utf-8")
        stdout = (step_dir / "stdout.log").open("w", encoding="utf-8")
        stderr = (step_dir / "stderr.log").open("w", encoding="utf-8")
        process = subprocess.Popen(command, cwd=ROOT, text=True, stdout=stdout, stderr=stderr)
        process._playsbc_stdout = stdout  # type: ignore[attr-defined]
        process._playsbc_stderr = stderr  # type: ignore[attr-defined]
        process._playsbc_step_dir = step_dir  # type: ignore[attr-defined]
        self.write_log(bundle, "log.sipp", f"{step_name.upper()} COMMAND", command_text(command))
        return process

    def start_rtcp_processes(
        self,
        profile: SimpleNamespace,
        core_pod: str,
        core_ip: str,
        peer_pod: str,
        peer_ip: str,
        bundle: Path,
    ) -> list[tuple[str, subprocess.Popen[str]]]:
        if not should_run_k8s_rtcp(profile):
            return []
        targets = self.discover_rtcp_targets(profile, core_pod, peer_pod, bundle)
        processes: list[tuple[str, subprocess.Popen[str]]] = []
        if "core" in targets:
            processes.append(("rtcp-core", self.start_rtcp_process("core", core_pod, core_ip, targets["core"], profile, bundle)))
        if "peer" in targets:
            processes.append(("rtcp-peer", self.start_rtcp_process("peer", peer_pod, peer_ip, targets["peer"], profile, bundle)))
        return processes

    def wait_for_rtcp_processes(self, profile: SimpleNamespace, processes: list[tuple[str, subprocess.Popen[str]]], bundle: Path) -> list[int]:
        returncodes: list[int] = []
        if not processes:
            return returncodes
        lines = [f"expected=True profile={profile.profile}"]
        for name, process in processes:
            try:
                rc = process.wait(timeout=max(10, int(getattr(profile, "hold_ms", 1000)) // 1000 + 15))
            except subprocess.TimeoutExpired:
                process.terminate()
                rc = 124
            finally:
                self.close_process_files(process)
            returncodes.append(int(rc))
            step_dir = bundle / name
            stdout_text = (step_dir / "stdout.log").read_text(encoding="utf-8", errors="replace") if (step_dir / "stdout.log").exists() else ""
            stderr_text = (step_dir / "stderr.log").read_text(encoding="utf-8", errors="replace") if (step_dir / "stderr.log").exists() else ""
            lines.append(f"{name}=returncode:{rc} {stdout_text.strip() or stderr_text.strip() or 'no-output'}")
            self.write_log(bundle, "log.sipp", f"{name.upper()} RESULT", f"returncode={rc}")
        ok = bool(returncodes) and all(code == 0 for code in returncodes)
        self.write_log(
            bundle,
            "log.media",
            "K8S RTCP OBSERVATION",
            "\n".join([f"status={'observed' if ok else 'failed'}", *lines]),
        )
        return returncodes

    def collect_playsbc_pod_evidence(self, bundle: Path, profile_started_at: dt.datetime) -> None:
        result = self.kubectl("get", "pods", "-l", "app.kubernetes.io/name=playsbc", "-o", "json", check=False)
        evidence: list[str] = []
        since_time = kubectl_since_time(profile_started_at)
        try:
            pods = json.loads(result.stdout or "{}").get("items", [])
        except json.JSONDecodeError:
            pods = []
        if result.returncode != 0:
            evidence.append(result.stdout + result.stderr)
        for pod in pods:
            name = pod.get("metadata", {}).get("name", "")
            if not name:
                continue
            evidence.append(f"===== describe pod/{name} =====")
            described = self.kubectl("describe", "pod", str(name), check=False)
            evidence.append(described.stdout + described.stderr)
            previous_values = (False, True) if pod_has_previous_container_logs(pod, "playsbc") else (False,)
            for previous in previous_values:
                title = f"logs pod/{name}" + (" --previous" if previous else "")
                evidence.append(f"===== {title} =====")
                command = [
                    "logs",
                    f"pod/{name}",
                    f"--since-time={since_time}",
                    f"--tail={self.args.deployment_log_tail}",
                ]
                if previous:
                    command.append("--previous")
                logs = self.kubectl(*command, check=False)
                evidence.append(logs.stdout + logs.stderr)
        (bundle / "playsbc-pod-evidence.log").write_text("\n".join(evidence), encoding="utf-8")

    def collect_playsbc_persistent_logs(self, bundle: Path) -> None:
        selector = f"app.kubernetes.io/name=playsbc,app.kubernetes.io/instance={self.args.helm_release}"
        result = self.kubectl("get", "pods", "-l", selector, "-o", "json", check=False)
        try:
            pods = json.loads(result.stdout or "{}").get("items", [])
        except json.JSONDecodeError:
            pods = []
        copy_root = bundle / "playsbc-persistent-logs"
        copy_root.mkdir(parents=True, exist_ok=True)
        copy_log: list[str] = []
        for pod in pods:
            name = str(pod.get("metadata", {}).get("name", ""))
            if not name:
                continue
            destination = copy_root / name
            command = [
                self.args.kubectl_bin,
                "-n",
                self.args.namespace,
                "cp",
                f"{name}:/tmp/playsbc-logs",
                str(destination),
                "-c",
                "playsbc",
            ]
            completed = run_command(command, timeout=30)
            copy_log.append(
                f"{name}=returncode:{completed.returncode} stdout={completed.stdout.strip()} stderr={completed.stderr.strip()}"
            )
            if completed.returncode != 0:
                continue
            for filename in B2BUA_LOG_FILES:
                source = destination / filename
                if not source.exists():
                    continue
                with (bundle / filename).open("a", encoding="utf-8") as handle:
                    handle.write(f"\n===== persistent pod/{name} {filename} =====\n")
                    handle.write(source.read_text(encoding="utf-8", errors="replace").rstrip() + "\n")
        (copy_root / "copy.log").write_text("\n".join(copy_log) + ("\n" if copy_log else ""), encoding="utf-8")

    def collect_rasa_pod_evidence(self, bundle: Path, profile_started_at: dt.datetime) -> None:
        selector = f"app.kubernetes.io/name=playsbc-rasa,app.kubernetes.io/instance={self.args.helm_release}"
        result = self.kubectl("get", "pods", "-l", selector, "-o", "json", check=False)
        evidence: list[str] = []
        since_time = kubectl_since_time(profile_started_at)
        try:
            pods = json.loads(result.stdout or "{}").get("items", [])
        except json.JSONDecodeError:
            pods = []
        if result.returncode != 0:
            evidence.append(result.stdout + result.stderr)
        for pod in pods:
            name = pod.get("metadata", {}).get("name", "")
            if not name:
                continue
            evidence.append(f"===== describe pod/{name} =====")
            described = self.kubectl("describe", "pod", str(name), check=False)
            evidence.append(described.stdout + described.stderr)
            previous_values = (False, True) if pod_has_previous_container_logs(pod, "rasa") else (False,)
            for previous in previous_values:
                title = f"logs pod/{name}" + (" --previous" if previous else "")
                evidence.append(f"===== {title} =====")
                command = [
                    "logs",
                    f"pod/{name}",
                    "-c",
                    "rasa",
                    f"--since-time={since_time}",
                    f"--tail={self.args.deployment_log_tail}",
                ]
                if previous:
                    command.append("--previous")
                logs = self.kubectl(*command, check=False)
                evidence.append(logs.stdout + logs.stderr)
        (bundle / "rasa-pod-evidence.log").write_text("\n".join(evidence), encoding="utf-8")

    def write_log(self, bundle: Path, filename: str, title: str, body: str = "") -> None:
        bundle.mkdir(parents=True, exist_ok=True)
        path = bundle / filename
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} | {title}\n")
            if body:
                handle.write(body.rstrip() + "\n")

    def base_sipp_args(self, pod_ip: str, local_port: int) -> list[str]:
        return [
            "-i",
            pod_ip,
            "-mi",
            pod_ip,
            "-p",
            str(local_port),
            "-trace_msg",
            "-trace_err",
            "-trace_stat",
            "-trace_counts",
            "-trace_logs",
            "-nostdin",
            "-timeout",
            str(self.args.sipp_timeout),
            "-timeout_error",
        ]

    def target(self) -> str:
        return f"{self.args.service}:{self.args.sip_port}"

    def write_text_to_pod(self, pod: str, path: str, text: str) -> None:
        command = [
            self.args.kubectl_bin,
            "-n",
            self.args.namespace,
            "exec",
            "-i",
            pod,
            "--",
            "sh",
            "-lc",
            f"cat > {shlex.quote(path)}",
        ]
        run_command(command, timeout=20, input_text=text, check=True)

    def prepare_profile_scenarios(self, profile: SimpleNamespace, uac_pod: str, uas_pod: str) -> tuple[str, str, str]:
        uac_path = f"/tmp/{short_name(profile.profile)}-uac.xml"
        uas_path = f"/tmp/{short_name(profile.profile)}-uas.xml"
        register_path = f"/tmp/{short_name(profile.profile)}-register.xml"
        self.write_text_to_pod(uac_pod, uac_path, rendered_scenario(profile, "uac"))
        self.write_text_to_pod(uas_pod, uas_path, rendered_scenario(profile, "uas"))
        self.write_text_to_pod(uas_pod, register_path, rendered_scenario(profile, "register"))
        self.write_text_to_pod(uac_pod, register_path, rendered_scenario(profile, "register"))
        return uac_path, uas_path, register_path

    def active_active_ha_config(self, profile: SimpleNamespace) -> dict[str, Any]:
        profile_ha = format_config_value(getattr(profile, "ha", {}) or {}, profile)
        profile_ha = copy.deepcopy(profile_ha) if isinstance(profile_ha, dict) else {}
        if not self.active_active_enabled():
            return profile_ha
        playsbc_replicas = max(1, int(getattr(self.args, "playsbc_replicas", 2)))
        rtpengine_replicas = max(1, int(getattr(self.args, "rtpengine_replicas", 2)))
        rtpengine_headless = f"{self.args.rtpengine_deployment}-headless"
        nodes = [
            {"node_id": f"{self.args.deployment}-{index}", "state": "active", "weight": 100}
            for index in range(playsbc_replicas)
        ]
        rtpengine_pairs = [
            {
                "name": f"rtpengine-pair-{index % rtpengine_replicas}",
                "node_id": f"{self.args.deployment}-{index}",
                "rtpengine_url": (
                    f"udp://{self.args.rtpengine_deployment}-{index % rtpengine_replicas}."
                    f"{rtpengine_headless}:2223"
                ),
            }
            for index in range(playsbc_replicas)
        ]
        default_ha = {
            "enabled": True,
            "cluster_id": getattr(self.args, "ha_cluster_id", "playsbc-aa-lab"),
            "node_id": "$POD_NAME",
            "shared_state_path": getattr(self.args, "ha_shared_state_path", "/var/lib/playsbc/ha-state.sqlite3"),
            "nodes": nodes,
            "load_balancing": {"enabled": True, "policy": "external-lb", "drain_new_calls": True},
            "failover": {
                "dialog_restore": True,
                "mid_call_failover": "dialog-restore-only",
                "rtpengine_session_migration": "planned",
            },
            "rtpengine_pairs": rtpengine_pairs,
        }
        alias_map = {
            "playsbc-a": f"{self.args.deployment}-0",
            "playsbc-b": f"{self.args.deployment}-1",
            "playsbc-0": f"{self.args.deployment}-0",
            "playsbc-1": f"{self.args.deployment}-1",
        }
        if profile_ha.get("node_id") and profile_ha.get("node_id") != "$POD_NAME":
            profile_ha.setdefault("node_aliases", [])
            aliases = profile_ha["node_aliases"]
            if not isinstance(aliases, list):
                aliases = [aliases]
            aliases.append(profile_ha["node_id"])
            profile_ha["node_aliases"] = aliases
            profile_ha["node_id"] = "$POD_NAME"
        if str(profile_ha.get("shared_state_path", "")).startswith("/tmp/"):
            profile_ha.pop("shared_state_path", None)
        if isinstance(profile_ha.get("nodes"), list):
            normalized_nodes: list[dict[str, Any]] = []
            for item in profile_ha["nodes"]:
                if not isinstance(item, dict):
                    continue
                node = copy.deepcopy(item)
                original_id = str(node.get("node_id") or node.get("id") or "")
                mapped_id = alias_map.get(original_id, original_id)
                if mapped_id:
                    node["node_id"] = mapped_id
                aliases = node.get("aliases") or []
                if not isinstance(aliases, list):
                    aliases = [aliases]
                if original_id and original_id != mapped_id:
                    aliases.append(original_id)
                if aliases:
                    node["aliases"] = list(dict.fromkeys(str(alias) for alias in aliases))
                normalized_nodes.append(node)
            profile_ha["nodes"] = normalized_nodes
        if isinstance(profile_ha.get("rtpengine_pairs"), list):
            normalized_pairs: list[dict[str, Any]] = []
            for item in profile_ha["rtpengine_pairs"]:
                if not isinstance(item, dict):
                    continue
                pair = copy.deepcopy(item)
                original_id = str(pair.get("node_id") or pair.get("playsbc_node") or "")
                pair["node_id"] = alias_map.get(original_id, original_id)
                normalized_pairs.append(pair)
            profile_ha["rtpengine_pairs"] = normalized_pairs
        return deep_merge_dict(default_ha, profile_ha)

    def profile_config(self, profile: SimpleNamespace) -> dict[str, object]:
        advertised_ip = "$POD_IP"
        return {
            "sip_ip": "0.0.0.0",
            "log_dir": "/tmp/playsbc-logs",
            "sip_advertised_ip": advertised_ip,
            "b2bua_advertised_ip": advertised_ip,
            "sip_port": self.args.sip_port,
            "tls_port": self.args.tls_port,
            "sip_transport": getattr(profile, "sip_transport", "udp"),
            "rtp_min": getattr(profile, "server_rtp_min", 30000),
            "rtp_max": getattr(profile, "server_rtp_max", 30100),
            "default_codec": getattr(profile, "server_codec", "PCMU"),
            "auth_realm": "playsbc",
            "users": getattr(profile, "users", {}),
            "bridge_rooms": ["bridge"],
            "b2bua_routes": b2bua_routes_for(profile),
            "route_policies": route_policies_for(profile),
            "trunk_groups": format_config_value(getattr(profile, "trunk_groups", []), profile),
            "hunt_groups": format_config_value(getattr(profile, "hunt_groups", []), profile),
            "number_normalization": getattr(profile, "number_normalization", []),
            "header_normalization": getattr(profile, "header_normalization", {}),
            "transport_policies": getattr(profile, "transport_policies", []),
            "call_admission": getattr(profile, "call_admission", {}),
            "overload": getattr(profile, "overload", {}),
            "business_services": format_config_value(
                getattr(profile, "business_services", {}),
                profile,
            ),
            "sip_stream": getattr(profile, "sip_stream", {}),
            "sip_transactions": getattr(profile, "sip_transactions", {}),
            "server_location": getattr(profile, "server_location", {}),
            "b2bua_ladder_logs": getattr(profile, "ladder_enabled", True),
            "b2bua_invite_timeout": getattr(profile, "b2bua_invite_timeout", 10.0),
            "media_backend": getattr(profile, "media_backend", "internal"),
            "rtpengine_url": getattr(profile, "rtpengine_url", f"udp://{self.args.rtpengine_service}:2223"),
            "rtpengine_timeout": getattr(profile, "rtpengine_timeout", 3.0),
            "rtpengine_directions": getattr(profile, "rtpengine_directions", []),
            "rtpengine_interfaces": getattr(profile, "rtpengine_interfaces", []),
            "rtpengine_max_sessions": getattr(profile, "rtpengine_max_sessions", -1),
            "rtpengine_offer_transport_protocol": getattr(profile, "rtpengine_offer_transport_protocol", ""),
            "rtpengine_answer_transport_protocol": getattr(profile, "rtpengine_answer_transport_protocol", ""),
            "rtpengine_g711_only": getattr(profile, "rtpengine_g711_only", False),
            "rtpengine_plain_rtp_sdp": getattr(profile, "rtpengine_plain_rtp_sdp", False),
            "rtpengine_sip_source_address": getattr(profile, "rtpengine_sip_source_address", False),
            "rtpengine_media_handover": getattr(profile, "rtpengine_media_handover", False),
            "rtpengine_nat_wait": getattr(profile, "rtpengine_nat_wait", False),
            "rtpengine_pierce_nat": getattr(profile, "rtpengine_pierce_nat", False),
            "rtpengine_sdes": getattr(profile, "rtpengine_sdes", []),
            "rtpengine_dtls": getattr(profile, "rtpengine_dtls", ""),
            "media_quality": getattr(profile, "media_quality", {}),
            "ai_voice_gateway": getattr(profile, "ai_voice_gateway", {}),
            "ha": self.active_active_ha_config(profile),
            "reject_unknown_routes": getattr(profile, "reject_unknown_routes", False),
            "tls_verify_peer": getattr(profile, "tls_verify_peer", False),
            "tls_server_names": getattr(profile, "tls_server_names", []),
            "tls_require_sni": getattr(profile, "tls_require_sni", False),
            "tls_reload_interval": getattr(profile, "tls_reload_interval", 30.0),
            "debug": True,
        }

    def apply_active_active_values(self, values: dict[str, Any], profile: SimpleNamespace | None = None) -> None:
        topology = values.setdefault("topology", {})
        active_active = topology.setdefault("activeActive", {})
        rtpengine_values = values.setdefault("rtpengine", {})
        if not self.active_active_enabled():
            topology["model"] = "core-peer-single-workload"
            active_active["enabled"] = False
            active_active["useStatefulSet"] = False
            values["replicaCount"] = 1
            rtpengine_values["replicas"] = 1
            rtpengine_values["hostNetwork"] = False
            service_affinity = str(getattr(profile, "k8s_service_session_affinity", "") or "ClientIP")
            values.setdefault("service", {})["sessionAffinity"] = service_affinity
            return
        topology["model"] = "core-peer-active-active"
        active_active["enabled"] = True
        active_active["useStatefulSet"] = True
        active_active["playsbcReplicas"] = max(1, int(getattr(self.args, "playsbc_replicas", 2)))
        active_active["rtpengineReplicas"] = max(1, int(getattr(self.args, "rtpengine_replicas", 2)))
        active_active["clusterId"] = getattr(self.args, "ha_cluster_id", "playsbc-aa-lab")
        active_active.setdefault("loadBalancingPolicy", "external-lb")
        active_active.setdefault(
            "sharedState",
            {
                "enabled": True,
                "mountPath": "/var/lib/playsbc",
                "fileName": "ha-state.sqlite3",
                "accessModes": ["ReadWriteOnce"],
                "size": "1Gi",
                "storageClassName": "",
            },
        )
        active_active.setdefault("rtpenginePairing", {"enabled": True})
        topology.setdefault(
            "realms",
            {
                "core": {"name": "core", "cidr": "172.28.0.0/24", "interface": "net-core"},
                "peer": {"name": "peer", "cidr": "192.168.28.0/24", "interface": "net-peer"},
            },
        )
        multus = topology.setdefault("multus", {})
        multus["enabled"] = bool(getattr(self.args, "multus_enabled", False))
        multus["enforce"] = bool(getattr(self.args, "require_multus", False))
        if getattr(self.args, "multus_enabled", False):
            multus.setdefault("createNetworkAttachmentDefinitions", False)
        rtpengine_values["replicas"] = max(1, int(getattr(self.args, "rtpengine_replicas", 2)))
        rtpengine_values["hostNetwork"] = False
        service_affinity = str(getattr(profile, "k8s_service_session_affinity", "") or "ClientIP")
        values.setdefault("service", {})["sessionAffinity"] = service_affinity

    def cleanup_stale_active_active_workloads(self, bundle: Path) -> None:
        if self.active_active_enabled():
            return
        lines: list[str] = []
        for name in (self.args.deployment, self.args.rtpengine_deployment):
            result = self.kubectl(
                "delete",
                "statefulset",
                name,
                "--ignore-not-found=true",
                check=False,
                timeout=60,
            )
            lines.append(f"statefulset/{name} returncode={result.returncode}")
            detail = (result.stdout + result.stderr).strip()
            if detail:
                lines.append(detail)
        self.write_log(bundle, "log.platform", "SINGLE WORKLOAD STALE STATEFULSET CLEANUP", "\n".join(lines))

    def apply_profile_auth_secret_values(self, values: dict[str, Any], profile: SimpleNamespace) -> None:
        """Keep regression REGISTER auth deterministic even when Helm reuses real-device values."""
        users = getattr(profile, "users", {}) or {}
        auth_secret = values.setdefault("authSecret", {})
        auth_secret["enabled"] = bool(users)
        auth_secret["existingSecret"] = ""
        auth_secret["users"] = copy.deepcopy(users)

    def apply_profile_config(self, profile: SimpleNamespace, bundle: Path, phases: PhaseLog) -> None:
        started = time.monotonic()
        self.capture_original_values()
        values = copy.deepcopy(self.original_values or {})
        advertised_ip = "$POD_IP"
        values.setdefault("playsbc", {})["config"] = self.profile_config(profile)
        self.apply_profile_auth_secret_values(values, profile)
        values.setdefault("rtpengine", {})["enabled"] = profile_enables_rtpengine_deployment(profile, self.args)
        rtpengine_rtp_min = getattr(profile, "rtpengine_rtp_min", None)
        rtpengine_rtp_max = getattr(profile, "rtpengine_rtp_max", None)
        if rtpengine_rtp_min is not None or rtpengine_rtp_max is not None:
            if rtpengine_rtp_min is None or rtpengine_rtp_max is None:
                raise RuntimeError("profile RTPengine range requires both rtpengine_rtp_min and rtpengine_rtp_max")
            if int(rtpengine_rtp_min) > int(rtpengine_rtp_max):
                raise RuntimeError("profile RTPengine range minimum exceeds maximum")
            values["rtpengine"]["rtpMin"] = int(rtpengine_rtp_min)
            values["rtpengine"]["rtpMax"] = int(rtpengine_rtp_max)
        self.apply_active_active_values(values, profile)
        if profile_uses_tls(profile):
            self.ensure_tls_secret(bundle)
            values.setdefault("tls", {})["enabled"] = True
            values.setdefault("tls", {})["existingSecret"] = self.args.tls_secret_name
        elif not (self.original_values or {}).get("tls", {}).get("enabled", False):
            values.setdefault("tls", {})["enabled"] = False
            values.setdefault("tls", {})["existingSecret"] = ""
        if profile_uses_real_rasa(profile):
            rasa_values = values.setdefault("rasa", {})
            rasa_values["enabled"] = True
            rasa_values["project"] = rasa_project_values()
        elif not (self.original_values or {}).get("rasa", {}).get("enabled", False):
            values.setdefault("rasa", {})["enabled"] = False
        values_path = bundle / "helm-profile-values.yaml"
        values_path.write_text(dump_simple_yaml(values), encoding="utf-8")
        helm_started = time.monotonic()
        result = self.helm_upgrade_values(
            values_path,
            bundle / "helm-profile-upgrade.log",
            check=True,
        )
        helm_seconds = time.monotonic() - helm_started
        self.write_log(bundle, "log.platform", "HELM PROFILE UPGRADE", result.stdout + result.stderr)
        self.cleanup_stale_active_active_workloads(bundle)
        restart = self.rollout_restart(self.playsbc_workload_ref())
        self.write_log(bundle, "log.platform", "PLAYSBC ROLLOUT RESTART", restart.stdout + restart.stderr)
        rollout_started = time.monotonic()
        rollout = self.rollout_status(self.playsbc_workload_ref(), check=True)
        rollout_seconds = time.monotonic() - rollout_started
        self.write_log(bundle, "log.platform", "PLAYSBC ROLLOUT READY", rollout.stdout + rollout.stderr)
        rtpengine_detail = "not-required"
        if profile_enables_rtpengine_deployment(profile, self.args):
            rtp_started = time.monotonic()
            rtp_rollout = self.rollout_status(self.rtpengine_workload_ref(), check=True)
            rtpengine_detail = f"{self.rtpengine_workload_ref()} ready in {time.monotonic() - rtp_started:.3f}s"
            self.write_log(bundle, "log.platform", "RTPENGINE ROLLOUT READY", rtp_rollout.stdout + rtp_rollout.stderr)
        rasa_detail = "not-required"
        if profile_uses_real_rasa(profile):
            rasa_deployment = f"{self.args.service}-rasa"
            rasa_started = time.monotonic()
            rasa_rollout = self.kubectl(
                "rollout",
                "status",
                f"deployment/{rasa_deployment}",
                f"--timeout={self.args.rollout_timeout}s",
                check=True,
            )
            rasa_detail = f"{rasa_deployment} ready in {time.monotonic() - rasa_started:.3f}s"
            self.write_log(bundle, "log.platform", "RASA ROLLOUT READY", rasa_rollout.stdout + rasa_rollout.stderr)
        aks_post_detail = "not-required"
        if getattr(self.args, "aks_mode", False):
            aks_post_detail = self.validate_aks_exposure(
                bundle,
                stage="post-profile",
                require_rtpengine_match=profile_enables_rtpengine_deployment(profile, self.args),
            )
        phases.append(
            "Configuration",
            "passed",
            started,
            (
                f"Rendered and applied Helm config for profile={profile.profile}; "
                f"media_backend={getattr(profile, 'media_backend', 'internal')}; "
                f"advertised_ip={advertised_ip}; tls_secret={self.args.tls_secret_name if profile_uses_tls(profile) else 'not-required'}; "
                f"helm_upgrade_seconds={helm_seconds:.3f}; rollout_seconds={rollout_seconds:.3f}; "
                f"rtpengine={rtpengine_detail}; rasa={rasa_detail}; "
                f"aks_post_validation={aks_post_detail}; "
                f"topology={'active-active' if self.active_active_enabled() else 'single-workload'}; "
                f"core_realm=172.28.0.0/24 peer_realm=192.168.28.0/24 multus={'enabled' if getattr(self.args, 'multus_enabled', False) else 'logical'}."
            ),
        )

    def b2bua_base_args(self, profile: SimpleNamespace, pod_ip: str, local_port: int) -> list[str]:
        calls = int(getattr(profile, "calls", 1))
        rate = int(getattr(profile, "rate", 1))
        hold_ms = int(getattr(profile, "hold_ms", self.args.call_hold_ms))
        timeout_seconds = k8s_sipp_timeout_seconds(profile)
        return [
            "-i",
            pod_ip,
            "-mi",
            pod_ip,
            "-p",
            str(local_port),
            "-m",
            str(calls),
            "-l",
            str(call_limit(calls, rate, hold_ms)),
            "-timeout",
            str(timeout_seconds),
            "-timeout_error",
            "-nostdin",
            "-min_rtp_port",
            "6000",
            "-max_rtp_port",
            "6998",
            *trace_args(),
        ]

    def b2bua_uas_args(self, profile: SimpleNamespace, scenario: str, peer_ip: str) -> list[str]:
        args = [
            "-sf",
            scenario,
            "-s",
            str(getattr(profile, "callee", self.args.callee)),
            *self.b2bua_base_args(profile, peer_ip, 5060),
        ]
        if bool(getattr(profile, "uas_srtp", False)):
            args.extend(["-rtpcheck_debug", "-srtpcheck_debug"])
        return [*args, *transport_args(getattr(profile, "uas_transport", "udp"), "server")]

    def b2bua_register_args(self, profile: SimpleNamespace, scenario: str, user: str, pod_ip: str, realm: str) -> list[str]:
        transport_name = getattr(profile, "uas_transport", "udp") if realm == "peer" else getattr(profile, "uac_transport", "udp")
        remote_port = self.args.tls_port if transport_name == "tls" else self.args.sip_port
        return [
            f"{self.args.service}:{remote_port}",
            "-sf",
            scenario,
            "-s",
            user,
            "-key",
            "contact_port",
            "5060",
            "-m",
            "1",
            "-r",
            "1",
            "-i",
            pod_ip,
            "-mi",
            pod_ip,
            "-p",
            "5070",
            "-timeout",
            "15",
            "-timeout_error",
            "-nostdin",
            *trace_args(),
            *transport_args(transport_name, "client"),
        ]

    def b2bua_uac_args(self, profile: SimpleNamespace, scenario: str, core_ip: str) -> list[str]:
        transport_name = getattr(profile, "uac_transport", "udp")
        remote_port = self.args.tls_port if transport_name == "tls" else self.args.sip_port
        calls = int(getattr(profile, "calls", 1))
        rate = int(getattr(profile, "rate", 1))
        hold_ms = int(getattr(profile, "hold_ms", self.args.call_hold_ms))
        args = [
            f"{self.args.service}:{remote_port}",
            "-sf",
            scenario,
            "-s",
            str(getattr(profile, "callee", self.args.callee)),
            "-key",
            "caller",
            str(getattr(profile, "caller", self.args.caller)),
            "-r",
            str(rate),
            "-d",
            str(hold_ms),
            *self.b2bua_base_args(profile, core_ip, 5060),
        ]
        for key, value in getattr(profile, "uac_keys", {}).items():
            args.extend(["-key", str(key), str(value)])
        if bool(getattr(profile, "uac_srtp", False)):
            args.extend(["-rtpcheck_debug", "-srtpcheck_debug"])
        return [*args, *transport_args(transport_name, "client")]

    def run_profile(self, profile: str, output_root: Path) -> ReportRow:
        bundle = output_root / f"{self.run_id}-{profile}"
        bundle.mkdir(parents=True, exist_ok=True)
        phases = PhaseLog()
        command_lines: list[str] = []
        returncodes: list[int] = []
        started_profile = time.monotonic()
        profile_started_at = dt.datetime.now(dt.timezone.utc)
        status = "failed"
        detail = ""
        sip_ladder = ""

        try:
            self.prepare_common(bundle, phases)
            if profile in RASA_NLU_PROFILES:
                returncodes, command_lines, sip_ladder = self.profile_rasa_nlu(profile, bundle, phases)
            elif profile in PROTOCOL_CORE_PROFILES:
                returncodes, command_lines, sip_ladder = self.profile_protocol_core(profile, bundle, phases)
            else:
                self.build_and_load_sipp_image(bundle, phases)
            if profile == "options":
                returncodes, command_lines, sip_ladder = self.profile_options(bundle, phases)
            elif profile == "register-contact":
                returncodes, command_lines, sip_ladder = self.profile_register_contact(bundle, phases)
            elif profile == "b2bua-signalling":
                returncodes, command_lines, sip_ladder = self.profile_b2bua_signalling(bundle, phases)
            elif profile in RASA_NLU_PROFILES:
                pass
            elif profile in PROTOCOL_CORE_PROFILES:
                pass
            elif profile in CATALOG_PROFILES:
                returncodes, command_lines, sip_ladder = self.profile_b2bua_catalog(profile, bundle, phases)
            else:
                raise ValueError(f"Unsupported Kubernetes regression profile: {profile}")
            status = status_from_codes(returncodes)
            detail = f"Executed profile={profile}; step_returncodes={','.join(str(code) for code in returncodes)}."
        except Exception as exc:
            status = "failed"
            detail = f"{type(exc).__name__}: {exc}"
            self.write_log(bundle, "log.platform", "K8S REGRESSION FAILED", detail)
        finally:
            self.stop_mock_rasa()
            teardown_started = time.monotonic()
            if not self.args.keep_pods:
                self.delete_run_pods(bundle)
            phases.append(
                "Test Teardown",
                "passed" if not self.args.keep_pods else "skipped",
                teardown_started,
                "Deleted temporary SIPp regression pods." if not self.args.keep_pods else "Kept temporary SIPp pods for debugging.",
            )
            settle_seconds = float(getattr(self.args, "metrics_settle_seconds", 0.0) or 0.0)
            if settle_seconds > 0:
                settle_started = time.monotonic()
                time.sleep(settle_seconds)
                self.write_log(
                    bundle,
                    "log.platform",
                    "K8S METRICS SCRAPE SETTLE",
                    f"seconds={settle_seconds:.3f} purpose=allow_prometheus_final_profile_scrape",
                )
                phases.append(
                    "Metrics Scrape Settle",
                    "passed",
                    settle_started,
                    f"Waited {settle_seconds:.3f}s before evidence collection and next profile rollout.",
                )
            evidence_started = time.monotonic()
            self.collect_k8s_evidence(bundle, profile, profile_started_at)
            sipmsg_sections = write_combined_sipmsg_log(bundle, profile)
            # Never publish a scenario template as an actual SIP ladder. Captures
            # and SIPp traces are final only after execution, teardown and collection.
            sip_ladder = final_evidence_ladder(profile, bundle, sip_ladder)
            self.write_log(
                bundle,
                "log.sipp",
                "COMBINED SIPMSG LOG",
                f"file=sipmsg.log sections={sipmsg_sections}",
            )
            evidence_failures = validate_k8s_profile_evidence(profile, bundle, sip_ladder)
            if evidence_failures:
                status = "failed"
                if not any(code != 0 for code in returncodes):
                    returncodes.append(1)
                detail = (
                    (detail.rstrip() + " " if detail else "")
                    + "Evidence validation failed: "
                    + "; ".join(evidence_failures)
                    + "."
                )
                self.write_log(
                    bundle,
                    "log.platform",
                    "K8S STRICT EVIDENCE VALIDATION FAILED",
                    "; ".join(evidence_failures),
                )
            else:
                self.write_log(
                    bundle,
                    "log.platform",
                    "K8S STRICT EVIDENCE VALIDATION PASSED",
                    f"profile={profile}",
                )
            phases.append(
                "Evidence Validation",
                "passed" if status == "passed" else "failed",
                evidence_started,
                detail or f"Collected Kubernetes logs and evidence for profile={profile}.",
            )

        returncode = 0 if status == "passed" else next((code for code in returncodes if code != 0), 1)
        suite = profile_suite_label(profile)
        if getattr(self.args, "aks_mode", False):
            suite = f"Azure AKS {suite}"
        return ReportRow(
            suite=suite,
            name=profile_execution_label(profile),
            status=status,
            returncode=returncode,
            duration_seconds=time.monotonic() - started_profile,
            log_path=str(bundle),
            command=" && ".join(command_lines) if command_lines else f"tools/run_k8s_regression.py --profile {profile}",
            phases=phases.phases,
            sip_ladder=sip_ladder,
        )

    def profile_protocol_core(
        self,
        profile_name: str,
        bundle: Path,
        phases: PhaseLog,
    ) -> tuple[list[int], list[str], str]:
        started = time.monotonic()
        module = {
            "protocol-parser-validation": "tests.test_sip_parser",
            "protocol-uri-validation": "tests.test_sip_uri",
            "protocol-header-grammar": "tests.test_sip_parser",
            "protocol-mime-binary-body": "tests.test_sip_parser",
            "protocol-request-policy": "tests.test_sip_parser",
            "protocol-error-responses": "tests.test_mini_call_server.SipParsingTests",
            "protocol-stream-parser-limits": "tests.test_sip_stream",
            "protocol-rfc4475-corpus": "tests.test_sip_rfc4475",
            "protocol-parser-fuzz-resource": "tests.test_sip_parser_fuzz",
            "protocol-server-transactions": "tests.test_sip_transaction",
            "protocol-client-transactions": "tests.test_sip_client_transaction",
        }[profile_name]
        command = [sys.executable, "-m", "unittest", "-v", module]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        output = completed.stdout + completed.stderr
        (bundle / "protocol-core.log").write_text(output, encoding="utf-8")
        phases.append(
            "Protocol Core Regression",
            "passed" if completed.returncode == 0 else "failed",
            started,
            f"Executed deterministic protocol-core suite {module} inside the Kubernetes regression runner.",
        )
        return [completed.returncode], [shlex.join(command)], ""

    def profile_rasa_nlu(self, profile_name: str, bundle: Path, phases: PhaseLog) -> tuple[list[int], list[str], str]:
        setup_started = time.monotonic()
        profile = rasa_nlu_profile_values(profile_name, self.run_id)
        self.apply_profile_config(profile, bundle, phases)
        case_file = RASA_NLU_CASE_FILES[profile_name]
        parse_url = f"http://{self.args.service}-rasa:5005/model/parse"
        phases.append(
            "Test Setup",
            "passed",
            setup_started,
            (
                f"Prepared real Rasa NLU regression profile={profile_name}; "
                f"case_file={case_file}; parse_url={parse_url}; SIPp/RTP pods are not required."
            ),
        )

        execution_started = time.monotonic()
        command = [
            sys.executable,
            str(ROOT / "tools" / "run_rasa_nlu_regression.py"),
            "--url",
            parse_url,
            "--case-file",
            str(case_file),
            "--output-dir",
            str(bundle),
            "--suite",
            profile_name,
            "--timeout",
            "5",
        ]
        result = run_command(command, timeout=self.args.timeout, check=False)
        self.write_log(bundle, "log.platform", "RASA NLU REGRESSION COMMAND", command_text(command))
        self.write_log(bundle, "log.platform", "RASA NLU REGRESSION STDOUT", result.stdout)
        self.write_log(bundle, "log.platform", "RASA NLU REGRESSION STDERR", result.stderr)
        phases.append(
            "Test Execution",
            "passed" if result.returncode == 0 else "failed",
            execution_started,
            (
                f"Ran real Rasa /model/parse chat regression profile={profile_name}; "
                f"case_count={self.rasa_nlu_case_count(bundle)}; returncode={result.returncode}."
            ),
        )
        ladder = self.rasa_nlu_ladder(profile_name)
        return [result.returncode], [command_text(command)], ladder

    def rasa_nlu_case_count(self, bundle: Path) -> int:
        result_path = bundle / "rasa-nlu-results.json"
        try:
            parsed = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        return len(parsed) if isinstance(parsed, list) else 0

    def rasa_nlu_ladder(self, profile_name: str) -> str:
        title = profile_display_title(profile_name)
        participants = ["Chat YAML", "K8s Runner", "PlaySBC Guard", "Rasa NLU", "Rasa Bot", "HTML Report"]
        lane_width = 20
        prefix_width = 6
        total_width = prefix_width + lane_width * len(participants)
        centers = [prefix_width + index * lane_width + lane_width // 2 for index, _name in enumerate(participants)]

        def lane_line(step: str = "") -> list[str]:
            chars = list(f"{step:<5} " + (" " * (total_width - prefix_width)))
            for center in centers:
                chars[center] = "|"
            return chars

        def place_text(chars: list[str], text: str, start: int, end: int) -> None:
            if end <= start:
                return
            clipped = text[: max(0, end - start)]
            for offset, character in enumerate(clipped):
                chars[start + offset] = character

        def message(step: str, src: int, dst: int, label: str) -> list[str]:
            left, right = sorted((centers[src], centers[dst]))
            label_chars = lane_line(step)
            label_start = left + max(1, ((right - left - len(label)) // 2))
            place_text(label_chars, label, label_start, right)

            arrow_chars = lane_line()
            if src < dst:
                for index in range(centers[src] + 1, centers[dst] - 1):
                    arrow_chars[index] = "-"
                arrow_chars[centers[dst] - 1] = ">"
            else:
                for index in range(centers[dst] + 2, centers[src]):
                    arrow_chars[index] = "-"
                arrow_chars[centers[dst] + 1] = "<"
            return ["".join(label_chars).rstrip(), "".join(arrow_chars).rstrip()]

        header = "Step  " + "".join(f"{participant:^{lane_width}}" for participant in participants)
        lines = [
            header,
            "-" * len(header),
            "".join(lane_line()).rstrip(),
        ]
        for step, src, dst, label in [
            ("01", 0, 1, "load YAML cases"),
            ("02", 1, 2, "validate text"),
            ("03", 2, 1, "local guard result"),
            ("04", 1, 3, "POST /model/parse"),
            ("05", 3, 1, "intent + confidence"),
            ("06", 1, 4, "POST /webhook"),
            ("07", 4, 1, "bot reply text"),
            ("08", 1, 5, "write JSON verdicts"),
            ("09", 1, 5, "render chat + ladder"),
        ]:
            lines.extend(message(step, src, dst, label))

        return (
            "NLP CHAT / RASA LADDER\n"
            f"profile={profile_name}\n"
            f"case={title}\n"
            f"mode={profile_mode_detail(profile_name)}\n"
            + "\n".join(lines)
            + "\n"
        )

    def profile_b2bua_catalog(self, profile_name: str, bundle: Path, phases: PhaseLog) -> tuple[list[int], list[str], str]:
        setup_started = time.monotonic()
        profile = profile_values(profile_name, self.run_id)
        stem = short_name(f"{self.run_id}-{profile_name}", limit=48)
        core_pod = f"{stem}-core"
        peer_pod = f"{stem}-peer"
        needs_target_c = profile_name.startswith("rfc5359-") and any(
            token in profile_name
            for token in ("unattended-transfer", "forwarding-on-busy", "forwarding-on-no-answer")
        )
        target_pod = f"{stem}-target" if needs_target_c else ""
        tls_secret = self.args.tls_secret_name if profile_uses_tls(profile) else ""
        if tls_secret:
            self.ensure_tls_secret(bundle)
        core_ip = self.create_agent(core_pod, bundle, realm="core", tls_secret=tls_secret)
        peer_ip = self.create_agent(peer_pod, bundle, realm="peer", tls_secret=tls_secret)
        if bool(getattr(profile, "k8s_dns_service", False)):
            dns_service = short_name(f"{stem}-dns-peer", limit=63)
            self.create_agent_service(dns_service, peer_pod, bundle)
            profile.b2bua_routes = {
                str(profile.callee): f"sip:{profile.callee}@{dns_service};transport={profile.uas_transport}"
            }
        target_ip = (
            self.create_agent(target_pod, bundle, realm="target", tls_secret=tls_secret)
            if target_pod
            else ""
        )
        profile.host = peer_ip
        profile.server_host = self.args.service
        profile.server_port = self.args.sip_port
        profile.uac_port = 5060
        profile.uas_port = 5060
        profile.register_port = 5070
        profile.caller_register_port = 5070
        if getattr(profile, "rtpengine_url", "") == "udp://playsbc-playsbc-rtpengine:2223":
            profile.rtpengine_url = f"udp://{self.args.rtpengine_service}:2223"
        if profile_uses_real_rasa(profile):
            ai_config = dict(getattr(profile, "ai_voice_gateway", {}) or {})
            ai_config["rasa_webhook_url"] = f"http://{self.args.service}-rasa:5005/webhooks/rest/webhook"
            profile.ai_voice_gateway = ai_config
        elif profile_name in {"ai-rasa-lab", "ai-rasa-rtpengine"}:
            ai_config = dict(getattr(profile, "ai_voice_gateway", {}) or {})
            ai_config["rasa_webhook_url"] = self.start_mock_rasa(profile, bundle)
            profile.ai_voice_gateway = ai_config
        phases.append(
            "Test Setup",
            "passed",
            setup_started,
            (
                f"Started Kubernetes dual-realm SIPp pods: core={core_pod} ip={core_ip}, "
                f"peer={peer_pod} ip={peer_ip}"
                f"{f', target={target_pod} ip={target_ip}' if target_pod else ''}. PlaySBC topology="
                f"{'active-active StatefulSet' if self.active_active_enabled() else 'single workload'}; "
                f"RTPengine pairing={'enabled' if profile_enables_rtpengine_deployment(profile, self.args) else 'not-required'}; "
                f"Multus={'enabled' if getattr(self.args, 'multus_enabled', False) else 'logical-dual-realm'}."
            ),
        )

        self.apply_profile_config(profile, bundle, phases)
        uac_scenario, uas_scenario, register_scenario = self.prepare_profile_scenarios(profile, core_pod, peer_pod)
        target_scenario = ""
        transfer_target_uac = ""
        forwarding_target_uac = ""
        if target_pod:
            target_scenario = f"/tmp/{short_name(profile.profile)}-target-uas.xml"
            self.write_text_to_pod(
                target_pod,
                target_scenario,
                (SCENARIO_DIR / "b2bua_uas_forward_target.xml").read_text(encoding="ISO-8859-1"),
            )
            self.write_text_to_pod(target_pod, register_scenario, rendered_scenario(profile, "register"))
        if "unattended-transfer" in profile_name:
            transfer_target_uac = f"/tmp/{short_name(profile.profile)}-target-uac.xml"
            self.write_text_to_pod(
                peer_pod,
                transfer_target_uac,
                (SCENARIO_DIR / "b2bua_uac_transfer_target.xml").read_text(encoding="ISO-8859-1"),
            )
        elif target_pod:
            forwarding_target_uac = f"/tmp/{short_name(profile.profile)}-forward-target-uac.xml"
            self.write_text_to_pod(
                core_pod,
                forwarding_target_uac,
                (SCENARIO_DIR / "b2bua_uac_forward_target.xml").read_text(encoding="ISO-8859-1"),
            )
        self.run_ha_action(profile, bundle, phases, "precall")

        execution_started = time.monotonic()
        returncodes: list[int] = []
        commands: list[str] = []
        processes: list[tuple[str, str, subprocess.Popen[str]]] = []
        rtcp_processes: list[tuple[str, subprocess.Popen[str]]] = []
        srtp_sender: tuple[str, subprocess.Popen[str]] | None = None
        captures: list[CaptureProcess] = []
        capture_ok = True

        try:
            pod_by_role = {"core": core_pod, "peer": peer_pod}
            if target_pod:
                pod_by_role["target"] = target_pod
            capture_pods = [(role, pod_by_role[role]) for role in k8s_pcap_capture_roles(profile)]
            captures = self.start_packet_captures(profile, bundle, capture_pods)
            if getattr(profile, "start_uas", True):
                uas_args = self.b2bua_uas_args(profile, uas_scenario, peer_ip)
                uas_process = self.start_sipp_process(peer_pod, "peer-sipp-b-uas", uas_args, bundle)
                processes.append(("peer-sipp-b-uas", peer_pod, uas_process))
                commands.append(command_text(self.sipp_exec_command(peer_pod, uas_args)))
                self.wait_for_sipp_listener(uas_process, peer_pod, "peer-sipp-b-uas", uas_args, bundle)

            if target_pod:
                target_profile = copy.copy(profile)
                target_profile.callee = "transfer-target" if "unattended-transfer" in profile_name else "forward-target"
                target_args = self.b2bua_uas_args(target_profile, target_scenario, target_ip)
                target_process = self.start_sipp_process(target_pod, "target-sipp-c-uas", target_args, bundle)
                processes.append(("target-sipp-c-uas", target_pod, target_process))
                commands.append(command_text(self.sipp_exec_command(target_pod, target_args)))
                self.wait_for_sipp_listener(target_process, target_pod, "target-sipp-c-uas", target_args, bundle)

                target_register_args = self.b2bua_register_args(
                    target_profile,
                    register_scenario,
                    str(target_profile.callee),
                    target_ip,
                    "peer",
                )
                result = self.run_sipp_step(
                    target_pod, "target-registration-callee", target_register_args, bundle, timeout=30
                )
                returncodes.append(result.returncode)
                commands.append(command_text(result.command))

                if transfer_target_uac:
                    transfer_profile = copy.copy(profile)
                    transfer_profile.callee = "transfer-target"
                    transfer_args = [
                        f"{self.args.service}:{self.args.sip_port}",
                        "-sf", transfer_target_uac,
                        "-s", "transfer-target",
                        *self.b2bua_base_args(transfer_profile, peer_ip, 5080),
                        *transport_args(getattr(profile, "uas_transport", "udp"), "client"),
                    ]
                    transfer_process = self.start_sipp_process(
                        peer_pod, "peer-sipp-b-target-uac", transfer_args, bundle
                    )
                    processes.append(("peer-sipp-b-target-uac", peer_pod, transfer_process))
                    commands.append(command_text(self.sipp_exec_command(peer_pod, transfer_args)))
                elif forwarding_target_uac:
                    forward_profile = copy.copy(profile)
                    forward_profile.callee = "forward-target"
                    forward_args = [
                        f"{self.args.service}:{self.args.sip_port}",
                        "-sf", forwarding_target_uac,
                        "-s", "forward-target",
                        *self.b2bua_base_args(forward_profile, core_ip, 5080),
                        *transport_args(getattr(profile, "uac_transport", "udp"), "client"),
                    ]
                    forward_process = self.start_sipp_process(
                        core_pod, "core-sipp-a-forward-target-uac", forward_args, bundle
                    )
                    processes.append(("core-sipp-a-forward-target-uac", core_pod, forward_process))
                    commands.append(command_text(self.sipp_exec_command(core_pod, forward_args)))

            if getattr(profile, "register_callee", True):
                register_args = self.b2bua_register_args(
                    profile,
                    register_scenario,
                    str(getattr(profile, "callee", self.args.callee)),
                    peer_ip,
                    "peer",
                )
                result = self.run_sipp_step(peer_pod, "peer-registration-callee", register_args, bundle, timeout=30)
                returncodes.append(result.returncode)
                commands.append(command_text(result.command))

            if getattr(profile, "register_caller", False):
                register_args = self.b2bua_register_args(
                    profile,
                    register_scenario,
                    str(getattr(profile, "caller", self.args.caller)),
                    core_ip,
                    "core",
                )
                result = self.run_sipp_step(core_pod, "core-registration-caller", register_args, bundle, timeout=30)
                returncodes.append(result.returncode)
                commands.append(command_text(result.command))

            if getattr(profile, "run_call", True):
                srtp_sender = self.start_srtp_sender(
                    profile,
                    core_pod,
                    core_ip,
                    peer_pod,
                    peer_ip,
                    bundle,
                )
                if srtp_sender is not None:
                    step_name, process = srtp_sender
                    commands.append((bundle / step_name / "command.txt").read_text(encoding="utf-8").strip())
                    if process.poll() is not None:
                        sender_rc = self.wait_for_srtp_sender(srtp_sender, bundle)
                        srtp_sender = None
                        returncodes.append(int(sender_rc if sender_rc is not None else 1))
                uac_args = self.b2bua_uac_args(profile, uac_scenario, core_ip)
                profile_timeout = k8s_sipp_timeout_seconds(profile)
                timeout = max(self.args.sipp_timeout, profile_timeout + 30)
                action = getattr(profile, "k8s_ha_action", {}) or {}
                if isinstance(action, dict) and action.get("phase") == "midcall":
                    uac_process = self.start_sipp_process(core_pod, "core-sipp-a-uac", uac_args, bundle)
                    commands.append(command_text(self.sipp_exec_command(core_pod, uac_args)))
                    time.sleep(float(action.get("delay_seconds", getattr(profile, "media_start_delay", 1.0)) or 1.0))
                    self.run_ha_action(profile, bundle, phases, "midcall")
                    try:
                        uac_rc = uac_process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        uac_process.terminate()
                        uac_rc = 124
                    finally:
                        self.close_process_files(uac_process)
                    returncodes.append(int(uac_rc))
                    self.write_log(bundle, "log.sipp", "CORE-SIPP-A-UAC RESULT", f"returncode={uac_rc}")
                    self.finalize_sipp_step_logs(bundle / "core-sipp-a-uac")
                    self.collect_sipp_traces(core_pod, bundle / "core-sipp-a-uac")
                elif should_run_k8s_rtcp(profile):
                    uac_process = self.start_sipp_process(core_pod, "core-sipp-a-uac", uac_args, bundle)
                    commands.append(command_text(self.sipp_exec_command(core_pod, uac_args)))
                    time.sleep(float(getattr(profile, "media_start_delay", 1.0)))
                    rtcp_processes = self.start_rtcp_processes(profile, core_pod, core_ip, peer_pod, peer_ip, bundle)
                    try:
                        uac_rc = uac_process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        uac_process.terminate()
                        uac_rc = 124
                    finally:
                        self.close_process_files(uac_process)
                    returncodes.append(int(uac_rc))
                    self.write_log(bundle, "log.sipp", "CORE-SIPP-A-UAC RESULT", f"returncode={uac_rc}")
                    self.finalize_sipp_step_logs(bundle / "core-sipp-a-uac")
                    self.collect_sipp_traces(core_pod, bundle / "core-sipp-a-uac")
                else:
                    result = self.run_sipp_step(core_pod, "core-sipp-a-uac", uac_args, bundle, timeout=timeout)
                    returncodes.append(result.returncode)
                    commands.append(command_text(result.command))

            if getattr(profile, "k8s_verify_drain_reject", False):
                reject_args = self.b2bua_uac_args(profile, "/scenarios/b2bua_uac_failed_outbound.xml", core_ip)
                result = self.run_sipp_step(core_pod, "core-drain-reject-uac", reject_args, bundle, timeout=30)
                returncodes.append(result.returncode)
                commands.append(command_text(result.command))

            returncodes.extend(self.wait_for_rtcp_processes(profile, rtcp_processes, bundle))
            rtcp_processes = []

            sender_rc = self.wait_for_srtp_sender(srtp_sender, bundle)
            srtp_sender = None
            if sender_rc is not None:
                returncodes.append(sender_rc)

            for step_name, pod, process in processes:
                try:
                    rc = process.wait(timeout=max(30, min(k8s_sipp_timeout_seconds(profile), 180)))
                except subprocess.TimeoutExpired:
                    process.terminate()
                    rc = 124
                finally:
                    self.close_process_files(process)
                returncodes.append(int(rc))
                self.write_log(bundle, "log.sipp", f"{step_name.upper()} RESULT", f"returncode={rc}")
                self.finalize_sipp_step_logs(bundle / step_name)
                self.collect_sipp_traces(pod, bundle / step_name)
            probe_result = self.run_layer2_socket_probe(profile, core_pod, bundle)
            if probe_result is not None:
                returncodes.append(probe_result.returncode)
                commands.append(command_text(probe_result.command))
            self.run_ha_action(profile, bundle, phases, "postcall")
        finally:
            if srtp_sender is not None:
                _name, process = srtp_sender
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                self.close_process_files(process)
            for _name, process in rtcp_processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                self.close_process_files(process)
            for _step_name, _pod, process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                self.close_process_files(process)
            self.stop_packet_captures(captures)
            capture_ok = self.collect_packet_captures(captures, bundle, profile)

        if not capture_ok:
            returncodes.append(1)

        phases.append(
            "Test Execution",
            status_from_codes(returncodes),
            execution_started,
            (
                f"Ran {profile_execution_label(profile_name)} on Kubernetes logical dual-realm topology; "
                f"mode={profile_mode_detail(profile_name)}; "
                f"description={PROFILE_DESCRIPTIONS.get(profile_name, 'special real-topology profile')}"
            ),
        )
        ladder = "" if int(getattr(profile, "calls", 1)) >= 20 else self.dual_realm_ladder(profile)
        return returncodes or [0], commands, ladder

    def dual_realm_ladder(self, profile: SimpleNamespace) -> str:
        participants = self.ladder_participants(profile)
        flow = B2BUAFlowLog(
            None,
            "k8s-unified",
            str(getattr(profile, "callee", self.args.callee)),
            self.route_for_ladder(profile),
            enabled=False,
            participants=participants,
        )
        flow.enabled = True
        self.add_registration_events(flow, profile)
        if "options" in str(getattr(profile, "profile", "")):
            self.add_setup_only_events(flow, profile)
        elif not getattr(profile, "run_call", True):
            self.add_setup_only_events(flow, profile)
        elif "ai-rasa" in str(getattr(profile, "profile", "")):
            self.add_ai_gateway_events(flow, profile)
        else:
            self.add_call_events(flow, profile)
        return self.render_unified_ladder(flow, profile)

    def ladder_participants(self, profile: SimpleNamespace) -> tuple[str, ...]:
        profile_name = str(getattr(profile, "profile", ""))
        playsbc_nodes = self.ha_ladder_playsbc_nodes(profile)
        rtpengine_nodes = self.ha_ladder_rtpengine_nodes(profile)
        if profile_name == "invalid-bye":
            return ("Core SIPp A", playsbc_nodes[0])
        if "ai-rasa" in profile_name:
            if profile_name in {
                "ai-rasa-rtpengine-speech",
                "ai-rasa-rtpengine-speech-whisper",
                "ai-rasa-long-response-streaming",
                "ai-rasa-contact-center-sales",
                "ai-rasa-contact-center-sales-coqui",
            }:
                stt_node, rasa_node, tts_node = ai_ladder_nodes(profile)
                return ("Core SIPp A", rtpengine_nodes[0], playsbc_nodes[0], stt_node, rasa_node, tts_node)
            participants = ["Core SIPp A", playsbc_nodes[0]]
            if profile_uses_rtpengine(profile):
                participants.extend(rtpengine_nodes)
            participants.extend(ai_ladder_nodes(profile))
            return tuple(participants)
        participants = ["Core SIPp A"]
        if profile_name.startswith("ha-"):
            participants.append("K8s HA")
        participants.extend(playsbc_nodes)
        if profile_uses_rtpengine(profile):
            participants.extend(rtpengine_nodes)
        participants.append("Peer SIPp B")
        if profile_name.startswith("rfc5359-") and any(
            token in profile_name
            for token in ("unattended-transfer", "forwarding-on-busy", "forwarding-on-no-answer")
        ):
            participants.append("Target SIPp C")
        return tuple(participants)

    def ha_ladder_playsbc_nodes(self, profile: SimpleNamespace) -> tuple[str, ...]:
        if str(getattr(profile, "profile", "")).startswith("ha-"):
            return ("PlaySBC-1", "PlaySBC-2")
        return ("PlaySBC",)

    def ha_ladder_rtpengine_nodes(self, profile: SimpleNamespace) -> tuple[str, ...]:
        if str(getattr(profile, "profile", "")).startswith("ha-"):
            return ("RTPengine-1", "RTPengine-2")
        return ("RTPengine",)

    def playsbc_node_for_ladder(self, profile: SimpleNamespace, index: int = 0) -> str:
        nodes = self.ha_ladder_playsbc_nodes(profile)
        return nodes[min(index, len(nodes) - 1)]

    def rtpengine_node_for_ladder(self, profile: SimpleNamespace, index: int = 0) -> str:
        nodes = self.ha_ladder_rtpengine_nodes(profile)
        return nodes[min(index, len(nodes) - 1)]

    def backup_playsbc_node_for_ladder(self, profile: SimpleNamespace) -> str:
        return self.playsbc_node_for_ladder(profile, 1)

    def backup_rtpengine_node_for_ladder(self, profile: SimpleNamespace) -> str:
        return self.rtpengine_node_for_ladder(profile, 1)

    def render_unified_ladder(self, flow: B2BUAFlowLog, profile: SimpleNamespace) -> str:
        profile_name = str(getattr(profile, "profile", "k8s"))
        body = "\n".join(flow.render_ladder_text().splitlines()[1:])
        lines = [
            "KUBERNETES SINGLE LADDER",
            f"profile={profile_name}",
        ]
        if profile_name in RASA_PROFILE_LABELS:
            lines.extend(
                [
                    f"case={profile_display_title(profile_name)}",
                    f"mode={profile_mode_detail(profile_name)}",
                ]
            )
        lines.append(body)
        return "\n".join(lines)

    def add_registration_events(self, flow: B2BUAFlowLog, profile: SimpleNamespace) -> None:
        profile_name = str(getattr(profile, "profile", ""))
        if getattr(profile, "register_callee", True):
            auth_outcome = str(getattr(profile, "registration_auth_expected", "") or "")
            self.add_registration_flow(flow, profile, "Peer SIPp B", auth_outcome)
        if profile_name.startswith("rfc5359-") and any(
                token in profile_name
                for token in ("unattended-transfer", "forwarding-on-busy", "forwarding-on-no-answer")
            ):
            self.add_registration_flow(flow, profile, "Target SIPp C", "")
        if getattr(profile, "register_caller", False):
            self.add_registration_flow(flow, profile, "Core SIPp A", "")

    def add_registration_flow(self, flow: B2BUAFlowLog, profile: SimpleNamespace, endpoint: str, auth_outcome: str) -> None:
        sbc = self.playsbc_node_for_ladder(profile)
        if auth_outcome:
            flow.sip(endpoint, sbc, "REGISTER")
            flow.sip(sbc, endpoint, "401 Unauthorized")
            if auth_outcome == "success":
                flow.sip(endpoint, sbc, "REGISTER + digest")
                flow.sip(sbc, endpoint, "200 OK")
            else:
                flow.sip(endpoint, sbc, "REGISTER + bad digest")
                flow.sip(sbc, endpoint, "401 Unauthorized")
            return
        flow.sip(endpoint, sbc, "REGISTER")
        flow.sip(sbc, endpoint, "200 OK")

    def route_for_ladder(self, profile: SimpleNamespace) -> RouteResult:
        callee = str(getattr(profile, "callee", self.args.callee))
        return RouteResult(
            target=SipUri(callee, "peer-sipp-b", 5060, str(getattr(profile, "uas_transport", "udp"))),
            policy_name="k8s-regression",
            source="kubernetes-dual-realm",
            original_user=callee,
            routed_user=callee,
        )

    def add_setup_only_events(self, flow: B2BUAFlowLog, profile: SimpleNamespace) -> None:
        sbc = self.playsbc_node_for_ladder(profile)
        if "options" in profile.profile:
            flow.sip("Core SIPp A", sbc, "OPTIONS")
            flow.sip(sbc, "Core SIPp A", "200 OK")
        elif not flow.events:
            flow.sip("Core SIPp A", sbc, "profile check")
            flow.sip(sbc, "Core SIPp A", "expected response")

    def add_ai_gateway_events(self, flow: B2BUAFlowLog, profile: SimpleNamespace) -> None:
        stt_node, rasa_node, tts_node = ai_ladder_nodes(profile)
        profile_name = str(getattr(profile, "profile", ""))
        sbc = self.playsbc_node_for_ladder(profile)
        rtpe = self.rtpengine_node_for_ladder(profile)
        flow.sip("Core SIPp A", sbc, "INVITE")
        flow.sip(sbc, "Core SIPp A", "100 Trying")
        flow.sip(sbc, "Core SIPp A", "180 Ringing")
        if profile_uses_rtpengine(profile):
            flow.sip(sbc, rtpe, "OFFER")
            flow.sip(rtpe, sbc, "ok OFFER")
            flow.sip(sbc, rtpe, "ANSWER")
            flow.sip(rtpe, sbc, "ok ANSWER")
        flow.sip(sbc, "Core SIPp A", "200 OK")
        flow.sip("Core SIPp A", sbc, "ACK")
        speech_profiles = {
            "ai-rasa-rtpengine-speech",
            "ai-rasa-rtpengine-speech-whisper",
            "ai-rasa-long-response-streaming",
            "ai-rasa-contact-center-sales",
            "ai-rasa-contact-center-sales-coqui",
        }
        sales_profiles = {"ai-rasa-contact-center-sales", "ai-rasa-contact-center-sales-coqui"}
        if profile_name in speech_profiles:
            flow.sip("Core SIPp A", rtpe, "G.711 speech RTP")
            flow.sip(rtpe, sbc, "anchored RTP")
            flow.sip(sbc, stt_node, "decode WAV")
            if profile_name in sales_profiles:
                transcript = "text: connect me to sales"
            elif profile_name == "ai-rasa-long-response-streaming":
                transcript = "text: detailed support update"
            else:
                transcript = "text: i need support"
            flow.sip(stt_node, sbc, transcript)
        else:
            flow.sip(sbc, stt_node, "scripted STT")
            flow.sip(stt_node, sbc, "intent text")
        if profile_uses_real_rasa(profile):
            flow.sip(sbc, rasa_node, "REST POST /webhook")
            if profile_name in sales_profiles:
                response_label = "REST 200 sales workflow"
            elif profile_name == "ai-rasa-long-response-streaming":
                response_label = "REST 200 long response"
            else:
                response_label = "REST 200 support"
            flow.sip(rasa_node, sbc, response_label)
        elif profile_name == "ai-rasa-rtpengine":
            flow.sip(sbc, rasa_node, "REST POST /mock")
            flow.sip(rasa_node, sbc, "2 replies + transfer")
        else:
            flow.sip(sbc, rasa_node, "REST POST /mock")
            flow.sip(rasa_node, sbc, "single reply")
        if profile_name in speech_profiles:
            if profile_name == "ai-rasa-long-response-streaming":
                flow.sip(sbc, tts_node, "bot text chunks")
                flow.sip(tts_node, sbc, "Piper WAV chunks")
            else:
                flow.sip(sbc, tts_node, "bot text")
                flow.sip(tts_node, sbc, f"{tts_node.replace(' TTS', '')} WAV")
            flow.sip(sbc, rtpe, "G.711 prompt RTP")
        else:
            flow.sip(sbc, tts_node, "text-only TTS")
            flow.sip(tts_node, sbc, "no RTP prompt")
        flow.sip("Core SIPp A", sbc, "BYE")
        flow.sip(sbc, "Core SIPp A", "200 OK")

    def add_call_events(self, flow: B2BUAFlowLog, profile: SimpleNamespace) -> None:
        profile_name = str(getattr(profile, "profile", ""))
        if profile_name == "invalid-bye":
            sbc = self.playsbc_node_for_ladder(profile)
            flow.sip("Core SIPp A", sbc, "BYE (unknown dialog)")
            flow.sip(sbc, "Core SIPp A", "481 No Matching Dialog")
            return
        if profile_name == "ha-node-draining":
            sbc = self.playsbc_node_for_ladder(profile)
            flow.sip("K8s HA", sbc, "mark node draining")
            flow.sip("Core SIPp A", sbc, "INVITE")
            flow.sip(sbc, "Core SIPp A", "503 Node Draining")
            flow.sip("Core SIPp A", sbc, "ACK")
            return
        if profile_name.startswith("rfc5359-unattended-transfer"):
            self.add_unattended_transfer_events(flow, profile)
            return
        if profile_name.startswith("rfc5359-unconditional-forwarding"):
            self.add_unconditional_forwarding_events(flow, profile)
            return
        if profile_name.startswith("rfc5359-forwarding-on-"):
            self.add_conditional_forwarding_events(flow, profile)
            return
        expect_failure = str(getattr(profile, "uac_scenario", "")).endswith("expect_488.xml") or profile.profile in {
            "unknown-route",
            "esbc-call-admission",
            "rtpengine-control-failure",
            "rtpengine-port-exhaustion",
            "rtpengine-interface-failure",
            "tcp-connection-failure",
        }
        cancel_flow = "cancel" in profile.profile
        outbound_failure = "failed-outbound" in profile.profile or "trunk-failure" in profile.profile
        action = getattr(profile, "k8s_ha_action", {}) or {}
        sbc = self.playsbc_node_for_ladder(profile)
        backup_sbc = self.backup_playsbc_node_for_ladder(profile)
        rtpe = self.rtpengine_node_for_ladder(profile)
        backup_rtpe = self.backup_rtpengine_node_for_ladder(profile)
        if isinstance(action, dict) and action.get("phase") == "precall" and "K8s HA" in flow.participants:
            target = rtpe if action.get("target") == "rtpengine" and rtpe in flow.participants else sbc
            flow.sip("K8s HA", target, "pre-call pod delete")
            if target == sbc and backup_sbc in flow.participants:
                sbc = backup_sbc
            if target == rtpe and backup_rtpe in flow.participants:
                rtpe = backup_rtpe
        flow.sip("Core SIPp A", sbc, "INVITE")
        flow.sip(sbc, "Core SIPp A", "100 Trying")
        if expect_failure:
            if profile_uses_rtpengine(profile):
                flow.sip(sbc, rtpe, "OFFER")
                flow.sip(rtpe, sbc, "failed OFFER")
            rejection = {
                "unknown-route": "404 Not Found",
                "esbc-call-admission": "503 Service Unavailable",
                "rtpengine-control-failure": "488 Not Acceptable Here",
                "rtpengine-port-exhaustion": "503 Media Exhausted",
                "rtpengine-interface-failure": "488 Not Acceptable Here",
                "tcp-connection-failure": "480 Temporary Unavailable",
            }.get(profile_name, "488 Not Acceptable Here")
            flow.sip(sbc, "Core SIPp A", rejection)
            flow.sip("Core SIPp A", sbc, "ACK")
            return
        if profile_uses_rtpengine(profile):
            flow.sip(sbc, rtpe, "OFFER")
            flow.sip(rtpe, sbc, "ok OFFER")
        flow.sip(sbc, "Peer SIPp B", "INVITE")
        flow.sip("Peer SIPp B", sbc, "100 Trying")
        flow.sip("Peer SIPp B", sbc, "180 Ringing")
        flow.sip(sbc, "Core SIPp A", "180 Ringing")
        if cancel_flow:
            flow.sip("Core SIPp A", sbc, "CANCEL")
            flow.sip(sbc, "Core SIPp A", "200 OK")
            flow.sip(sbc, "Peer SIPp B", "CANCEL")
            flow.sip("Peer SIPp B", sbc, "200 OK")
            flow.sip("Peer SIPp B", sbc, "487 Request Terminated")
            flow.sip(sbc, "Core SIPp A", "487 Request Terminated")
            flow.sip("Core SIPp A", sbc, "ACK")
            return
        final_label = "503 Service Unavailable" if outbound_failure else "200 OK"
        flow.sip("Peer SIPp B", sbc, final_label)
        if outbound_failure:
            flow.sip(sbc, "Core SIPp A", final_label)
            flow.sip("Core SIPp A", sbc, "ACK")
            return
        if profile_uses_rtpengine(profile):
            flow.sip(sbc, rtpe, "ANSWER")
            flow.sip(rtpe, sbc, "ok ANSWER")
        flow.sip(sbc, "Core SIPp A", "200 OK")
        flow.sip("Core SIPp A", sbc, "ACK")
        flow.sip(sbc, "Peer SIPp B", "ACK")
        if str(getattr(profile, "profile", "")).startswith("rfc5359-call-hold-resume"):
            flow.sip("Core SIPp A", sbc, "re-INVITE (sendonly hold)")
            if profile_uses_rtpengine(profile):
                flow.sip(sbc, rtpe, "UPDATE OFFER (hold)")
                flow.sip(rtpe, sbc, "ok OFFER")
            flow.sip(sbc, "Peer SIPp B", "re-INVITE (sendonly hold)")
            flow.sip("Peer SIPp B", sbc, "200 OK (recvonly)")
            if profile_uses_rtpengine(profile):
                flow.sip(sbc, rtpe, "UPDATE ANSWER (hold)")
                flow.sip(rtpe, sbc, "ok ANSWER")
            flow.sip(sbc, "Core SIPp A", "200 OK (recvonly)")
            flow.sip("Core SIPp A", sbc, "ACK")
            flow.sip(sbc, "Peer SIPp B", "ACK")
            flow.sip("Core SIPp A", sbc, "re-INVITE (sendrecv resume)")
            if profile_uses_rtpengine(profile):
                flow.sip(sbc, rtpe, "UPDATE OFFER (resume)")
                flow.sip(rtpe, sbc, "ok OFFER")
            flow.sip(sbc, "Peer SIPp B", "re-INVITE (sendrecv resume)")
            flow.sip("Peer SIPp B", sbc, "200 OK (sendrecv)")
            if profile_uses_rtpengine(profile):
                flow.sip(sbc, rtpe, "UPDATE ANSWER (resume)")
                flow.sip(rtpe, sbc, "ok ANSWER")
            flow.sip(sbc, "Core SIPp A", "200 OK (sendrecv)")
            flow.sip("Core SIPp A", sbc, "ACK")
            flow.sip(sbc, "Peer SIPp B", "ACK")
        if isinstance(action, dict) and action.get("phase") == "midcall" and "K8s HA" in flow.participants:
            if action.get("operation") == "drain-all":
                flow.sip("K8s HA", sbc, "runtime drain all")
            else:
                target = rtpe if action.get("target") == "rtpengine" and rtpe in flow.participants else sbc
                flow.sip("K8s HA", target, "mid-call pod delete")
                if target == sbc and backup_sbc in flow.participants:
                    flow.sip(backup_sbc, "Core SIPp A", "dialog restore")
                    sbc = backup_sbc
                if target == rtpe and backup_rtpe in flow.participants:
                    flow.sip(sbc, backup_rtpe, "media recovery")
                    rtpe = backup_rtpe
        flow.sip("Core SIPp A", sbc, "BYE")
        flow.sip(sbc, "Core SIPp A", "200 OK")
        flow.sip(sbc, "Peer SIPp B", "BYE")
        flow.sip("Peer SIPp B", sbc, "200 OK")
        if isinstance(action, dict) and action.get("phase") == "postcall" and "K8s HA" in flow.participants:
            target = rtpe if action.get("target") == "rtpengine" and rtpe in flow.participants else sbc
            flow.sip("K8s HA", target, "post-call pod delete")
        if getattr(profile, "k8s_verify_drain_reject", False) and "K8s HA" in flow.participants:
            flow.sip("Core SIPp A", sbc, "new INVITE while draining")
            flow.sip(sbc, "Core SIPp A", "503 Node Draining")

    def add_unattended_transfer_events(self, flow: B2BUAFlowLog, profile: SimpleNamespace) -> None:
        sbc = self.playsbc_node_for_ladder(profile)
        rtpe = self.rtpengine_node_for_ladder(profile)
        for source, target, label in (
            ("Core SIPp A", sbc, "INVITE transfer-b"),
            (sbc, "Core SIPp A", "100 Trying"),
            (sbc, "Peer SIPp B", "INVITE"),
            ("Peer SIPp B", sbc, "100 Trying"),
            ("Peer SIPp B", sbc, "200 OK"),
            (sbc, "Core SIPp A", "200 OK"),
            ("Core SIPp A", sbc, "ACK"),
            (sbc, "Peer SIPp B", "ACK"),
            ("Core SIPp A", sbc, "REFER transfer-target"),
            (sbc, "Peer SIPp B", "REFER transfer-target"),
            ("Peer SIPp B", sbc, "202 Accepted"),
            (sbc, "Core SIPp A", "202 Accepted"),
            ("Peer SIPp B", sbc, "INVITE transfer-target"),
            (sbc, "Target SIPp C", "INVITE transfer-target"),
            ("Target SIPp C", sbc, "200 OK"),
            (sbc, "Peer SIPp B", "200 OK"),
            ("Peer SIPp B", sbc, "NOTIFY sipfrag 200"),
            (sbc, "Core SIPp A", "NOTIFY sipfrag 200"),
            ("Core SIPp A", sbc, "200 OK (NOTIFY)"),
            (sbc, "Peer SIPp B", "200 OK (NOTIFY)"),
            ("Peer SIPp B", sbc, "BYE original dialog"),
            (sbc, "Core SIPp A", "BYE original dialog"),
            ("Core SIPp A", sbc, "200 OK (BYE)"),
            (sbc, "Peer SIPp B", "200 OK (BYE)"),
            ("Peer SIPp B", sbc, "ACK"),
            (sbc, "Target SIPp C", "ACK"),
            ("Peer SIPp B", sbc, "BYE transfer-target"),
            (sbc, "Target SIPp C", "BYE transfer-target"),
            ("Target SIPp C", sbc, "200 OK (BYE)"),
            (sbc, "Peer SIPp B", "200 OK (BYE)"),
        ):
            flow.sip(source, target, label)
        if profile_uses_rtpengine(profile):
            flow.sip(sbc, rtpe, "media session update")

    def add_unconditional_forwarding_events(self, flow: B2BUAFlowLog, profile: SimpleNamespace) -> None:
        sbc = self.playsbc_node_for_ladder(profile)
        flow.sip("Core SIPp A", sbc, "INVITE forward-source")
        flow.sip(sbc, "Core SIPp A", "100 Trying")
        flow.sip(sbc, "Peer SIPp B", "INVITE forward-target")
        flow.sip("Peer SIPp B", sbc, "100 Trying")
        flow.sip("Peer SIPp B", sbc, "180 Ringing")
        flow.sip(sbc, "Core SIPp A", "180 Ringing")
        flow.sip("Peer SIPp B", sbc, "200 OK")
        flow.sip(sbc, "Core SIPp A", "200 OK")
        flow.sip("Core SIPp A", sbc, "ACK")
        flow.sip(sbc, "Peer SIPp B", "ACK")
        flow.sip("Core SIPp A", sbc, "BYE")
        flow.sip(sbc, "Peer SIPp B", "BYE")
        flow.sip("Peer SIPp B", sbc, "200 OK")
        flow.sip(sbc, "Core SIPp A", "200 OK")

    def add_conditional_forwarding_events(self, flow: B2BUAFlowLog, profile: SimpleNamespace) -> None:
        sbc = self.playsbc_node_for_ladder(profile)
        busy = "busy" in str(profile.profile)
        flow.sip("Core SIPp A", sbc, "INVITE original B")
        flow.sip(sbc, "Core SIPp A", "100 Trying")
        flow.sip(sbc, "Peer SIPp B", "INVITE")
        if busy:
            flow.sip("Peer SIPp B", sbc, "486 Busy Here")
            flow.sip(sbc, "Peer SIPp B", "ACK")
        else:
            flow.sip("Peer SIPp B", sbc, "180 Ringing")
            flow.sip(sbc, "Core SIPp A", "180 Ringing")
            flow.sip(sbc, "Peer SIPp B", "CANCEL")
            flow.sip("Peer SIPp B", sbc, "200 OK (CANCEL)")
            flow.sip("Peer SIPp B", sbc, "487 Request Terminated")
            flow.sip(sbc, "Peer SIPp B", "ACK")
        flow.sip(sbc, "Core SIPp A", "302 Contact: forward-target")
        flow.sip("Core SIPp A", sbc, "ACK (302)")
        flow.sip("Core SIPp A", sbc, "INVITE forward-target")
        flow.sip(sbc, "Core SIPp A", "100 Trying")
        flow.sip(sbc, "Target SIPp C", "INVITE")
        flow.sip("Target SIPp C", sbc, "100 Trying")
        flow.sip("Target SIPp C", sbc, "200 OK")
        flow.sip(sbc, "Core SIPp A", "200 OK")
        flow.sip("Core SIPp A", sbc, "ACK")
        flow.sip(sbc, "Target SIPp C", "ACK")
        flow.sip("Core SIPp A", sbc, "BYE")
        flow.sip(sbc, "Target SIPp C", "BYE")
        flow.sip("Target SIPp C", sbc, "200 OK")
        flow.sip(sbc, "Core SIPp A", "200 OK")

    def profile_options(self, bundle: Path, phases: PhaseLog) -> tuple[list[int], list[str], str]:
        setup_started = time.monotonic()
        pod = f"{self.run_id}-options"
        pod_ip = self.create_agent(pod, bundle)
        phases.append("Test Setup", "passed", setup_started, f"Started SIPp agent pod={pod} ip={pod_ip}.")

        execution_started = time.monotonic()
        sipp_args = [
            self.target(),
            "-sf",
            "/scenarios/options.xml",
            "-s",
            self.args.options_user,
            "-m",
            "1",
            "-r",
            "1",
            *self.base_sipp_args(pod_ip, 5060),
        ]
        result = self.run_sipp_step(pod, "options", sipp_args, bundle)
        phases.append("Test Execution", "passed" if result.returncode == 0 else "failed", execution_started, "Sent OPTIONS and expected 200 OK.")
        ladder = (
            "SIP LADDER\n"
            "SIPp Agent              PlaySBC\n"
            "    |                      |\n"
            "01  | OPTIONS              |\n"
            "    |--------------------->|\n"
            "02  | 200 OK               |\n"
            "    |<---------------------|\n"
        )
        return [result.returncode], [command_text(result.command)], ladder

    def profile_register_contact(self, bundle: Path, phases: PhaseLog) -> tuple[list[int], list[str], str]:
        setup_started = time.monotonic()
        pod = f"{self.run_id}-register"
        pod_ip = self.create_agent(pod, bundle)
        phases.append("Test Setup", "passed", setup_started, f"Started SIPp registrar pod={pod} ip={pod_ip}.")

        execution_started = time.monotonic()
        sipp_args = [
            self.target(),
            "-sf",
            "/scenarios/register_contact.xml",
            "-s",
            self.args.register_user,
            "-key",
            "contact_port",
            "5060",
            "-m",
            "1",
            "-r",
            "1",
            *self.base_sipp_args(pod_ip, 5070),
        ]
        result = self.run_sipp_step(pod, "register-contact", sipp_args, bundle)
        phases.append("Test Execution", "passed" if result.returncode == 0 else "failed", execution_started, "Sent REGISTER and expected 200 OK.")
        ladder = (
            "REGISTRATION LADDER\n"
            "SIPp Agent              PlaySBC\n"
            "    |                      |\n"
            "01  | REGISTER             |\n"
            "    |--------------------->|\n"
            "02  | 200 OK               |\n"
            "    |<---------------------|\n"
        )
        return [result.returncode], [command_text(result.command)], ladder

    def profile_b2bua_signalling(self, bundle: Path, phases: PhaseLog) -> tuple[list[int], list[str], str]:
        setup_started = time.monotonic()
        uac_pod = f"{self.run_id}-uac"
        uas_pod = f"{self.run_id}-uas"
        uac_ip = self.create_agent(uac_pod, bundle)
        uas_ip = self.create_agent(uas_pod, bundle)
        phases.append("Test Setup", "passed", setup_started, f"Started UAC pod={uac_pod} ip={uac_ip}; UAS pod={uas_pod} ip={uas_ip}.")

        execution_started = time.monotonic()
        returncodes: list[int] = []
        commands: list[str] = []

        uas_args = [
            "-sf",
            "/scenarios/b2bua_uas_b.xml",
            "-s",
            self.args.callee,
            "-m",
            "1",
            *self.base_sipp_args(uas_ip, 5060),
        ]
        uas_process = self.start_sipp_process(uas_pod, "sipp-b-uas", uas_args, bundle)
        commands.append(command_text(self.sipp_exec_command(uas_pod, uas_args)))
        self.wait_for_sipp_listener(uas_process, uas_pod, "sipp-b-uas", uas_args, bundle)

        register_args = [
            self.target(),
            "-sf",
            "/scenarios/register_contact.xml",
            "-s",
            self.args.callee,
            "-key",
            "contact_port",
            "5060",
            "-m",
            "1",
            "-r",
            "1",
            *self.base_sipp_args(uas_ip, 5070),
        ]
        register_result = self.run_sipp_step(uas_pod, "registration-callee", register_args, bundle)
        returncodes.append(register_result.returncode)
        commands.append(command_text(register_result.command))

        uac_args = [
            self.target(),
            "-sf",
            "/scenarios/b2bua_uac_a.xml",
            "-s",
            self.args.callee,
            "-key",
            "caller",
            self.args.caller,
            "-m",
            "1",
            "-r",
            "1",
            "-d",
            str(self.args.call_hold_ms),
            *self.base_sipp_args(uac_ip, 5060),
        ]
        uac_result = self.run_sipp_step(uac_pod, "sipp-a-uac", uac_args, bundle, timeout=self.args.sipp_timeout + 10)
        returncodes.append(uac_result.returncode)
        commands.append(command_text(uac_result.command))

        try:
            uas_rc = uas_process.wait(timeout=self.args.sipp_timeout + 10)
        except subprocess.TimeoutExpired:
            uas_process.terminate()
            uas_rc = 124
        finally:
            self.close_process_files(uas_process)
        returncodes.append(int(uas_rc))
        self.write_log(bundle, "log.sipp", "SIPP-B-UAS RESULT", f"returncode={uas_rc}")
        self.collect_sipp_traces(uas_pod, bundle / "sipp-b-uas")
        phases.append(
            "Test Execution",
            "passed" if all(code == 0 for code in returncodes) else "failed",
            execution_started,
            "Registered SIPp B, then placed a B2BUA call from SIPp A to the registered callee.",
        )
        ladder = (
            "SIP LADDER\n"
            "SIPp A                  PlaySBC                 SIPp B\n"
            "  |                        |                       |\n"
            "  |                        | REGISTER              |\n"
            "  |                        |<----------------------|\n"
            "  |                        | 200 OK                 |\n"
            "  |                        |---------------------->|\n"
            "  | INVITE                 |                       |\n"
            "  |----------------------->|                       |\n"
            "  | 100 Trying             |                       |\n"
            "  |<-----------------------|                       |\n"
            "  |                        | INVITE                |\n"
            "  |                        |---------------------->|\n"
            "  |                        | 100 Trying            |\n"
            "  |                        |<----------------------|\n"
            "  |                        | 180 Ringing           |\n"
            "  |                        |<----------------------|\n"
            "  | 180 Ringing            |                       |\n"
            "  |<-----------------------|                       |\n"
            "  |                        | 200 OK                |\n"
            "  |                        |<----------------------|\n"
            "  | 200 OK                 |                       |\n"
            "  |<-----------------------|                       |\n"
            "  | ACK                    |                       |\n"
            "  |----------------------->|                       |\n"
            "  |                        | ACK                   |\n"
            "  |                        |---------------------->|\n"
            "  | BYE                    |                       |\n"
            "  |----------------------->|                       |\n"
            "  | 200 OK                 |                       |\n"
            "  |<-----------------------|                       |\n"
            "  |                        | BYE                   |\n"
            "  |                        |---------------------->|\n"
            "  |                        | 200 OK                |\n"
            "  |                        |<----------------------|\n"
        )
        return returncodes, commands, ladder


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="", help="Run/report identifier; defaults to a timestamp")
    parser.add_argument("--namespace", default="playsbc")
    parser.add_argument("--service", default="playsbc-playsbc")
    parser.add_argument("--sip-port", type=int, default=5062)
    parser.add_argument("--tls-port", type=int, default=5061)
    parser.add_argument("--deployment", default="playsbc-playsbc")
    parser.add_argument("--rtpengine-service", default="playsbc-playsbc-rtpengine")
    parser.add_argument("--rtpengine-deployment", default="playsbc-playsbc-rtpengine")
    parser.add_argument("--configmap", default="playsbc-sipp-scenarios")
    parser.add_argument("--sipp-image", default="playsbc-sipp:local")
    parser.add_argument("--image-pull-policy", default="IfNotPresent")
    parser.add_argument("--build-sipp-image", action="store_true", help="Build docker/sipp.Dockerfile before running")
    parser.add_argument("--kind-load-image", action="store_true", help="Load --sipp-image into the kind cluster before running")
    parser.add_argument("--kind-cluster", default="playsbc")
    parser.add_argument("--helm-bin", default="helm")
    parser.add_argument("--helm-release", default="playsbc")
    parser.add_argument("--chart", default=str(ROOT / "charts" / "playsbc"))
    parser.add_argument("--profile", action="append", choices=SELECTABLE_PROFILES)
    parser.add_argument("--all-profiles", action="store_true", help="Run the canonical Kubernetes profile catalog, including SIPp, Rasa voice, and Rasa chat/NLU profiles")
    parser.add_argument("--rasa-profiles", action="store_true", help="Run only the Kubernetes AI/Rasa voice and chat/NLU profiles")
    parser.add_argument("--aks-profiles", action="store_true", help="Run the Azure AKS readiness profile set and collect Azure LoadBalancer evidence")
    parser.add_argument("--aks-mode", action=argparse.BooleanOptionalAction, default=False, help="Collect Azure AKS LoadBalancer exposure evidence in each bundle")
    parser.add_argument("--aks-services-selector", default="playsbc.io/cloud=azure", help="Label selector for Azure-specific LoadBalancer services")
    parser.add_argument("--aks-require-azure-services", action=argparse.BooleanOptionalAction, default=False, help="Fail when the Azure SIP public LoadBalancer service is missing")
    parser.add_argument("--aks-require-static-sip", action=argparse.BooleanOptionalAction, default=False, help="Fail when the Azure SIP public service lacks a static IP annotation")
    parser.add_argument("--aks-require-public-sip-ingress", action=argparse.BooleanOptionalAction, default=False, help="Fail until Azure assigns an external public SIP ingress address")
    parser.add_argument("--aks-require-public-rtp-ingress", action=argparse.BooleanOptionalAction, default=False, help="Fail until Azure assigns an external public RTP ingress address")
    parser.add_argument("--aks-require-rtp-port-range", action=argparse.BooleanOptionalAction, default=False, help="Fail unless the Azure RTP LoadBalancer exposes the expected UDP media range")
    parser.add_argument("--aks-rtp-port-min", type=int, default=30000)
    parser.add_argument("--aks-rtp-port-max", type=int, default=30049)
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--rtpengine-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--active-active-topology",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run Kubernetes profiles with active-active PlaySBC/RTPengine topology; defaults on for local K8s and off for --aks-profiles",
    )
    parser.add_argument("--playsbc-replicas", type=int, default=2)
    parser.add_argument("--rtpengine-replicas", type=int, default=2)
    parser.add_argument("--ha-cluster-id", default="playsbc-aa-lab")
    parser.add_argument("--ha-shared-state-path", default="/var/lib/playsbc/ha-state.sqlite3")
    parser.add_argument("--multus-enabled", action=argparse.BooleanOptionalAction, default=False, help="Annotate PlaySBC/RTPengine pods for Multus core/peer realm networks")
    parser.add_argument("--require-multus", action="store_true", help="Record Multus as mandatory in rendered Helm values; use only after installing Multus CRDs")
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--kubectl-bin", default="kubectl")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--helm-timeout", type=int, default=180)
    parser.add_argument("--rollout-timeout", type=int, default=DEFAULT_ROLLOUT_TIMEOUT)
    parser.add_argument("--sipp-timeout", type=int, default=60)
    parser.add_argument("--pod-ready-timeout", type=int, default=60)
    parser.add_argument("--image-build-timeout", type=int, default=900)
    parser.add_argument("--deployment-log-tail", type=int, default=200)
    parser.add_argument("--tls-secret-name", default=LAB_TLS_SECRET_NAME)
    parser.add_argument("--no-restore-helm-values", action="store_true", help="Leave Helm on the last rendered profile instead of restoring pre-run values")
    parser.add_argument("--skip-namespace-check", action="store_true", help="Skip cluster-scoped namespace lookup, useful for in-cluster Job RBAC")
    parser.add_argument("--keep-old-logs", action="store_true", help="Keep existing Rasa-only logs when --rasa-profiles is used")
    parser.add_argument("--options-user", default="health")
    parser.add_argument("--register-user", default="1001")
    parser.add_argument("--caller", default="1001")
    parser.add_argument("--callee", default="1002")
    parser.add_argument("--call-hold-ms", type=int, default=1000)
    parser.add_argument("--uas-start-delay", type=float, default=1.0)
    parser.add_argument(
        "--sipp-ready-timeout", type=float, default=20.0,
        help="Maximum time to verify each background SIPp UAS listener (portable to slower WSL nodes)",
    )
    parser.add_argument("--keep-pods", action="store_true")
    parser.add_argument("--metrics-settle-seconds", type=float, default=2.0, help="Wait after each profile so Prometheus can scrape final counters before the next rollout")
    args = parser.parse_args(argv)
    if args.rasa_profiles and (args.all_profiles or args.profile):
        raise SystemExit("--rasa-profiles cannot be combined with --all-profiles or --profile")
    if args.aks_profiles and (args.rasa_profiles or args.all_profiles or args.profile):
        raise SystemExit("--aks-profiles cannot be combined with --rasa-profiles, --all-profiles, or --profile")
    if args.rasa_profiles:
        if args.output_root == DEFAULT_OUTPUT_ROOT:
            args.output_root = RASA_OUTPUT_ROOT
        if args.report_dir == DEFAULT_REPORT_DIR:
            args.report_dir = RASA_REPORT_DIR
        if args.rollout_timeout == DEFAULT_ROLLOUT_TIMEOUT:
            args.rollout_timeout = RASA_ROLLOUT_TIMEOUT
    if args.aks_profiles:
        args.aks_mode = True
        args.aks_require_azure_services = True
        args.aks_require_static_sip = True
        args.aks_require_public_sip_ingress = True
        args.aks_require_public_rtp_ingress = True
        args.aks_require_rtp_port_range = True
        if args.output_root == DEFAULT_OUTPUT_ROOT:
            args.output_root = AKS_OUTPUT_ROOT
        if args.report_dir == DEFAULT_REPORT_DIR:
            args.report_dir = AKS_REPORT_DIR
    if args.active_active_topology is None:
        args.active_active_topology = False if args.aks_profiles else True
    if args.aks_rtp_port_min > args.aks_rtp_port_max:
        raise SystemExit("--aks-rtp-port-min must be less than or equal to --aks-rtp-port-max")
    if args.playsbc_replicas < 1:
        raise SystemExit("--playsbc-replicas must be at least 1")
    if args.rtpengine_replicas < 1:
        raise SystemExit("--rtpengine-replicas must be at least 1")
    if args.require_multus and not args.multus_enabled:
        raise SystemExit("--require-multus also requires --multus-enabled")
    return args


def main() -> int:
    args = parse_args()
    ensure_binary(args.kubectl_bin)
    ensure_binary(args.helm_bin)
    if args.list_profiles:
        print("Available Kubernetes regression profiles:")
        for profile in ALL_PROFILES:
            label = profile_execution_label(profile)
            description = PROFILE_DESCRIPTIONS.get(profile) or profile_mode_detail(profile)
            print(f"  {profile}: {label} - {description}")
        print("\nRasa shortcut:")
        for profile in RASA_PROFILES:
            print(f"  --rasa-profiles includes {profile}: {profile_display_title(profile)}")
        print("\nAzure AKS shortcut:")
        for profile in AKS_PROFILES:
            print(f"  --aks-profiles includes {profile}: {PROFILE_DESCRIPTIONS.get(profile, 'AKS readiness profile')}")
        print("\nSmoke aliases:")
        for profile in SMOKE_PROFILES:
            print(f"  {profile}")
        return 0
    profiles = selected_profiles(args)
    run_id = args.run_id or (make_rasa_run_id() if args.rasa_profiles else make_aks_run_id() if args.aks_profiles else make_run_id())
    output_root = Path(args.output_root)
    report_dir = Path(args.report_dir)
    if (args.rasa_profiles or args.aks_profiles) and not args.keep_old_logs:
        shutil.rmtree(output_root, ignore_errors=True)
        if not report_dir.is_relative_to(output_root):
            shutil.rmtree(report_dir, ignore_errors=True)
    output_root.mkdir(parents=True, exist_ok=True)

    runner = K8sRegressionRunner(args, run_id)
    rows: list[ReportRow] = []
    restore_error = ""
    try:
        runner.capture_original_values()
        total_profiles = len(profiles)
        print(f"Regression progress: 0/{total_profiles} started", flush=True)
        for profile_index, profile in enumerate(profiles, start=1):
            row = runner.run_profile(profile, output_root)
            rows.append(row)
            print(
                f"Regression progress: {profile_index}/{total_profiles} completed "
                f"profile={profile} status={row.status}",
                flush=True,
            )
    finally:
        restore_error = runner.restore_original_values(report_dir) or ""
    cleanup_old_reports(report_dir, run_id)
    report_path = write_reports(rows, report_dir, run_id, include_rasa_test_section=args.rasa_profiles)
    print(f"Kubernetes regression report: {report_path}")
    print(f"Latest report: {report_dir / 'latest.html'}")
    for row in rows:
        print(f"{row.suite} / {row.name}: {row.status}")
    if restore_error:
        print(restore_error, file=sys.stderr)
        return 1
    return 1 if any(row.status != "passed" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
