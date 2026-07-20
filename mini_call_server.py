#!/usr/bin/env python3
"""
PlaySBC educational SIP + RTP media server with basic G.711 transcoding.

What it does:
  - Listens for SIP over UDP.
  - Handles REGISTER, OPTIONS, INVITE, ACK, and BYE.
  - Auto-answers calls with SDP.
  - Starts an RTP media session per call.
  - Echoes received RTP audio back to the caller.
  - Can transcode between PCMU (payload type 0) and PCMA (payload type 8)
    when Python's optional audioop module is available.

This is intentionally small and readable. It is useful for local testing and
learning, not for production SIP service.
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import random
import re
import secrets
import socket
import ssl
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from ai_gateway import AiTurnResult, AiVoiceConfig, AiVoiceGateway, BotAction
from ai_gateway.speech import decode_rtp_pcap_to_wav
from rtp.analyzer import RtpAnalyzer
from rtp.packet import RtpPacket
from rtp.rtcp import build_compound_sender_report, parse_compound_rtcp, parse_receiver_reports
from rtp.rtpengine import RtpengineClient, RtpengineError, parse_rtpengine_url
from sip.dialog import CallState, DialogError, DialogManager, SipDialog
from sip.transaction import TransactionManager

try:
    import audioop  # type: ignore
except Exception:  # pragma: no cover - audioop is unavailable in newer Python builds.
    audioop = None


CRLF = "\r\n"
PLAYSBC_VERSION = "1.2.1"
PCMU = 0
PCMA = 8
SUPPORTED_CODECS = (PCMU, PCMA)
CODEC_NAMES = {
    PCMU: "PCMU",
    PCMA: "PCMA",
}
CODEC_PAYLOADS = {
    "PCMU": PCMU,
    "PCMA": PCMA,
}


def ai_stt_ladder_node(provider: str) -> str:
    names = {
        "vosk": "Vosk STT",
        "whisper": "Whisper STT",
        "lab-scripted": "Scripted STT",
        "scripted": "Scripted STT",
    }
    return names.get(str(provider or "").lower(), "STT Adapter")


def ai_tts_ladder_node(provider: str) -> str:
    names = {
        "piper": "Piper TTS",
        "coqui": "Coqui TTS",
        "text-only": "Text TTS",
        "lab-text": "Text TTS",
    }
    return names.get(str(provider or "").lower(), "TTS Adapter")


@dataclass
class ServerConfig:
    sip_ip: str = "0.0.0.0"
    sip_advertised_ip: str = ""
    b2bua_advertised_ip: str = ""
    sip_port: int = 5060
    tls_port: int = 5061
    sip_transport: str = "udp"
    rtp_min: int = 10000
    rtp_max: int = 10100
    log_dir: str = ""
    default_codec: str = "PCMU"
    auth_realm: str = "playsbc"
    users: Dict[str, str] = field(default_factory=dict)
    bridge_rooms: Tuple[str, ...] = ("bridge",)
    b2bua_routes: Dict[str, str] = field(default_factory=dict)
    route_policies: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    trunk_groups: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    hunt_groups: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    number_normalization: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    header_normalization: Dict[str, Any] = field(default_factory=dict)
    transport_policies: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    call_admission: Dict[str, Any] = field(default_factory=dict)
    b2bua_ladder_logs: bool = True
    media_backend: str = "internal"
    rtpengine_url: str = "udp://127.0.0.1:2223"
    rtpengine_timeout: float = 3.0
    rtpengine_directions: Tuple[str, ...] = field(default_factory=tuple)
    rtpengine_interfaces: Tuple[str, ...] = field(default_factory=tuple)
    rtpengine_max_sessions: int = -1
    rtpengine_offer_transport_protocol: str = ""
    rtpengine_answer_transport_protocol: str = ""
    rtpengine_sdes: Tuple[str, ...] = field(default_factory=tuple)
    rtpengine_dtls: str = ""
    media_quality: Dict[str, Any] = field(default_factory=dict)
    ai_voice_gateway: Dict[str, Any] = field(default_factory=dict)
    ha: Dict[str, Any] = field(default_factory=dict)
    reject_unknown_routes: bool = False
    tls_certfile: str = ""
    tls_keyfile: str = ""
    tls_cafile: str = ""
    tls_verify_peer: bool = False
    health_ip: str = "0.0.0.0"
    health_port: int = 8080
    users_file: str = ""
    debug: bool = False

    @property
    def default_payload(self) -> int:
        return codec_payload(self.default_codec)


SERVER_CONFIG_KEYS = {
    "sip_ip",
    "sip_advertised_ip",
    "b2bua_advertised_ip",
    "sip_port",
    "tls_port",
    "sip_transport",
    "rtp_min",
    "rtp_max",
    "log_dir",
    "default_codec",
    "auth_realm",
    "users",
    "bridge_rooms",
    "b2bua_routes",
    "route_policies",
    "trunk_groups",
    "hunt_groups",
    "number_normalization",
    "header_normalization",
    "transport_policies",
    "call_admission",
    "b2bua_ladder_logs",
    "media_backend",
    "rtpengine_url",
    "rtpengine_timeout",
    "rtpengine_directions",
    "rtpengine_interfaces",
    "rtpengine_max_sessions",
    "rtpengine_offer_transport_protocol",
    "rtpengine_answer_transport_protocol",
    "rtpengine_sdes",
    "rtpengine_dtls",
    "media_quality",
    "ai_voice_gateway",
    "ha",
    "reject_unknown_routes",
    "tls_certfile",
    "tls_keyfile",
    "tls_cafile",
    "tls_verify_peer",
    "health_ip",
    "health_port",
    "users_file",
    "debug",
}

MEDIA_BACKENDS = {"internal", "rtpengine"}
SIP_TRANSPORTS = {"udp", "tcp", "tls"}

DTMF_EVENTS = {
    0: "0",
    1: "1",
    2: "2",
    3: "3",
    4: "4",
    5: "5",
    6: "6",
    7: "7",
    8: "8",
    9: "9",
    10: "*",
    11: "#",
    12: "A",
    13: "B",
    14: "C",
    15: "D",
}


@dataclass
class SipMessage:
    start_line: str
    headers: Dict[str, str]
    body: str
    source: Tuple[str, int]
    transport: str = "udp"
    connection: Any = None

    @property
    def method(self) -> str:
        return self.start_line.split(" ", 1)[0].upper()

    @property
    def is_response(self) -> bool:
        return self.start_line.upper().startswith("SIP/2.0 ")

    @property
    def status_code(self) -> int:
        if not self.is_response:
            return 0
        parts = self.start_line.split(" ", 2)
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

    @property
    def reason_phrase(self) -> str:
        if not self.is_response:
            return ""
        parts = self.start_line.split(" ", 2)
        return parts[2] if len(parts) > 2 else ""

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


@dataclass
class SipUri:
    user: str
    host: str
    port: int = 5060
    transport: str = "udp"

    @property
    def uri(self) -> str:
        uri = f"sip:{self.user}@{self.host}:{self.port}"
        if self.transport.lower() != "udp":
            uri += f";transport={self.transport.lower()}"
        return uri

    @property
    def address(self) -> Tuple[str, int]:
        return self.host, self.port


@dataclass
class Registration:
    user: str
    contact_uri: str
    source: Tuple[str, int]
    expires_at: float
    registered_at: float = field(default_factory=time.time)

    def is_expired(self, now: Optional[float] = None) -> bool:
        timestamp = time.time() if now is None else now
        return self.expires_at <= timestamp

    @property
    def target(self) -> SipUri:
        return parse_sip_uri(self.contact_uri)


@dataclass
class RoutePolicy:
    name: str
    match: str = "*"
    target: str = "registration"
    priority: int = 100
    enabled: bool = True

    @classmethod
    def from_config(cls, value: Dict[str, Any]) -> "RoutePolicy":
        return cls(
            name=str(value.get("name") or value.get("match") or "route-policy"),
            match=str(value.get("match", "*")),
            target=str(value.get("target", "registration")),
            priority=int(value.get("priority", 100)),
            enabled=bool(value.get("enabled", True)),
        )

    def matches(self, user: str) -> bool:
        return self.enabled and fnmatch.fnmatchcase(user, self.match)


@dataclass
class RouteResult:
    target: SipUri
    policy_name: str
    source: str
    original_user: str = ""
    routed_user: str = ""
    trunk_name: str = ""
    group_name: str = ""


def infer_realm_label(*values: object, default: str = "peer") -> str:
    text = " ".join(str(value or "") for value in values).lower()
    for realm in ("core", "peer", "ai"):
        if re.search(rf"(^|[^a-z0-9]){realm}([^a-z0-9]|$)", text):
            return realm
    return default


@dataclass
class TrunkRuntime:
    name: str
    uri: str
    priority: int = 100
    realm: str = "peer"
    enabled: bool = True
    healthy: bool = True
    max_calls: int = 0
    active_calls: int = 0
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    options_probe_enabled: bool = False
    options_probe_interval: float = 30.0
    options_probe_timeout: float = 2.0
    options_probe_failure_threshold: int = 3
    options_probe_recovery_threshold: int = 2
    options_probe_successes: int = 0
    options_probe_failures: int = 0
    options_probe_consecutive_successes: int = 0
    options_probe_consecutive_failures: int = 0
    last_probe_at: float = 0.0
    last_probe_status: str = "never"

    @classmethod
    def from_config(cls, value: Dict[str, Any], index: int) -> "TrunkRuntime":
        probe = value.get("options_probe") or value.get("options_ping") or {}
        if not isinstance(probe, dict):
            probe = {}
        return cls(
            name=str(value.get("name") or f"trunk-{index + 1}"),
            uri=str(value.get("uri") or value.get("target") or ""),
            priority=int(value.get("priority", (index + 1) * 10)),
            realm=str(value.get("realm") or infer_realm_label(value.get("name"), value.get("uri"), value.get("target"))),
            enabled=bool(value.get("enabled", True)),
            healthy=str(value.get("state", "up")).lower() not in {"down", "failed", "disabled"},
            max_calls=max(0, int(value.get("max_calls", 0))),
            options_probe_enabled=bool(probe.get("enabled", value.get("probe_enabled", False))),
            options_probe_interval=max(0.1, float(probe.get("interval_seconds", value.get("probe_interval", 30.0)))),
            options_probe_timeout=max(0.05, float(probe.get("timeout_seconds", value.get("probe_timeout", 2.0)))),
            options_probe_failure_threshold=max(
                1,
                int(probe.get("failure_threshold", value.get("probe_failure_threshold", 3))),
            ),
            options_probe_recovery_threshold=max(
                1,
                int(probe.get("recovery_successes", value.get("probe_recovery_successes", 2))),
            ),
        )


class RoutingEngine:
    """Resolve dialed users to outbound SIP targets.

    Policies are intentionally small and readable:
      - target="registration" uses the in-memory registrar location service.
      - target="sip:{user}@host:port" creates a static SIP target from a template.

    The legacy b2bua_routes map is still accepted as an exact static fallback.
    """

    REGISTRATION_TARGETS = {"registration", "registrar", "location"}
    AI_GATEWAY_PREFIXES = ("ai-gateway:", "ai-voice:")

    def __init__(
        self,
        policies: Tuple[Dict[str, Any], ...],
        static_routes: Dict[str, str],
        trunk_groups: Tuple[Dict[str, Any], ...] = (),
        hunt_groups: Tuple[Dict[str, Any], ...] = (),
        number_normalization: Tuple[Dict[str, Any], ...] = (),
        header_normalization: Optional[Dict[str, Any]] = None,
        transport_policies: Tuple[Dict[str, Any], ...] = (),
        call_admission: Optional[Dict[str, Any]] = None,
    ):
        self.policies = sorted(
            (RoutePolicy.from_config(policy) for policy in policies),
            key=lambda policy: (policy.priority, policy.name),
        )
        self.static_routes = static_routes
        self.number_normalization = number_normalization
        self.header_normalization = dict(header_normalization or {})
        self.transport_policies = transport_policies
        self.call_admission = dict(call_admission or {})
        self.active_calls = 0
        self.rejected_calls = 0
        self.trunk_groups: Dict[str, Tuple[str, ...]] = {}
        self.hunt_groups: Dict[str, Tuple[str, ...]] = {}
        self.group_strategies: Dict[str, str] = {}
        self.group_cursors: Dict[str, int] = {}
        self.trunks: Dict[str, TrunkRuntime] = {}
        self._load_groups(trunk_groups, "trunk")
        self._load_groups(hunt_groups, "hunt")

    def _load_groups(self, groups: Tuple[Dict[str, Any], ...], kind: str) -> None:
        destination = self.trunk_groups if kind == "trunk" else self.hunt_groups
        for group_index, group in enumerate(groups):
            group_name = str(group.get("name") or f"{kind}-group-{group_index + 1}")
            members = []
            for member_index, value in enumerate(group.get("members", [])):
                if isinstance(value, str):
                    member = TrunkRuntime(f"{group_name}-{member_index + 1}", value, (member_index + 1) * 10)
                    member.realm = infer_realm_label(group_name, value)
                elif isinstance(value, dict):
                    member = TrunkRuntime.from_config(value, member_index)
                    member.realm = str(value.get("realm") or infer_realm_label(group_name, member.name, member.uri))
                else:
                    continue
                if not member.uri:
                    continue
                unique_name = member.name
                if unique_name in self.trunks:
                    unique_name = f"{group_name}-{member.name}"
                    member.name = unique_name
                self.trunks[unique_name] = member
                members.append(unique_name)
            destination[group_name] = tuple(members)
            self.group_strategies[group_name] = str(group.get("strategy", "priority")).lower()
            self.group_cursors[group_name] = 0

    def normalize_number(self, user: str) -> Tuple[str, str]:
        for value in self.number_normalization:
            if not bool(value.get("enabled", True)):
                continue
            pattern = str(value.get("pattern", ""))
            if pattern and re.search(pattern, user):
                replacement = str(value.get("replacement", ""))
                return re.sub(pattern, replacement, user, count=1), str(value.get("name", pattern))
            match = str(value.get("match", ""))
            if match and fnmatch.fnmatchcase(user, match):
                strip_prefix = str(value.get("strip_prefix", ""))
                normalized = user[len(strip_prefix) :] if strip_prefix and user.startswith(strip_prefix) else user
                return str(value.get("add_prefix", "")) + normalized, str(value.get("name", match))
        return user, ""

    def _transport_target(self, user: str, target: SipUri) -> SipUri:
        for value in self.transport_policies:
            if not bool(value.get("enabled", True)):
                continue
            if fnmatch.fnmatchcase(user, str(value.get("match", "*"))):
                transport = normalize_sip_transport(str(value.get("transport", target.transport)))
                return SipUri(target.user, target.host, target.port, transport)
        return target

    def _resolve_group(self, group_name: str, user: str, source: str) -> Optional[RouteResult]:
        members = self.trunk_groups.get(group_name) if source == "trunk-group" else self.hunt_groups.get(group_name)
        if not members:
            return None
        available = [
            self.trunks[name]
            for name in members
            if self.trunks[name].enabled
            and self.trunks[name].healthy
            and (self.trunks[name].max_calls <= 0 or self.trunks[name].active_calls < self.trunks[name].max_calls)
        ]
        if not available:
            return None
        strategy = self.group_strategies.get(group_name, "priority")
        if strategy in {"round-robin", "round_robin", "rr"}:
            cursor = self.group_cursors[group_name] % len(available)
            member = available[cursor]
            self.group_cursors[group_name] = cursor + 1
        else:
            member = min(available, key=lambda item: (item.priority, item.name))
        target = self._transport_target(user, parse_sip_uri(format_route_target(member.uri, user)))
        return RouteResult(target, group_name, source, routed_user=user, trunk_name=member.name, group_name=group_name)

    def resolve(self, user: str, registrations: Dict[str, Registration]) -> Optional[RouteResult]:
        original_user = user
        user, _normalization_policy = self.normalize_number(user)
        now = time.time()
        for policy in self.policies:
            if not policy.matches(user):
                continue

            target = policy.target.strip()
            if target.lower() in self.REGISTRATION_TARGETS:
                registration = registrations.get(user)
                if registration and not registration.is_expired(now):
                    target = self._transport_target(user, registration.target)
                    return RouteResult(target, policy.name, "registrar", original_user, user)
                continue

            lowered = target.lower()
            if lowered.startswith(self.AI_GATEWAY_PREFIXES):
                bot_name = target.split(":", 1)[1].strip() or user
                return RouteResult(
                    SipUri(bot_name, "ai-gateway.local", 5060),
                    policy.name,
                    "ai-gateway",
                    original_user,
                    user,
                    group_name=bot_name,
                )
            if lowered.startswith("trunk-group:"):
                result = self._resolve_group(target.split(":", 1)[1].strip(), user, "trunk-group")
                if result:
                    result.original_user = original_user
                    result.policy_name = policy.name
                    return result
                continue
            if lowered.startswith("hunt-group:"):
                result = self._resolve_group(target.split(":", 1)[1].strip(), user, "hunt-group")
                if result:
                    result.original_user = original_user
                    result.policy_name = policy.name
                    return result
                continue

            uri = self._transport_target(user, parse_sip_uri(format_route_target(target, user)))
            return RouteResult(uri, policy.name, "policy", original_user, user)

        static_route = self.static_routes.get(user)
        if static_route:
            uri = self._transport_target(user, parse_sip_uri(format_route_target(static_route, user)))
            return RouteResult(uri, "b2bua_routes", "static", original_user, user)

        return None

    def normalize_headers(self, headers: Dict[str, str], route: RouteResult, call_id: str) -> Dict[str, str]:
        normalized = dict(headers)
        for remove_name in self.header_normalization.get("remove", []):
            for existing in tuple(normalized):
                if existing.lower() == str(remove_name).lower():
                    normalized.pop(existing, None)
        context = {
            "user": route.routed_user or route.target.user,
            "original_user": route.original_user or route.target.user,
            "trunk": route.trunk_name,
            "group": route.group_name,
            "call_id": call_id,
        }
        for name, value in dict(self.header_normalization.get("set", {})).items():
            normalized[str(name)] = str(value).format(**context)
        return normalized

    def admit(self, route: RouteResult) -> bool:
        global_limit = max(0, int(self.call_admission.get("max_concurrent_calls", 0)))
        if bool(self.call_admission.get("enabled", False)) and self.active_calls >= global_limit:
            self.rejected_calls += 1
            return False
        trunk = self.trunks.get(route.trunk_name)
        if trunk and trunk.max_calls and trunk.active_calls >= trunk.max_calls:
            trunk.failures += 1
            self.rejected_calls += 1
            return False
        self.active_calls += 1
        if trunk:
            trunk.active_calls += 1
            trunk.attempts += 1
        return True

    def release(self, route: RouteResult) -> None:
        self.active_calls = max(0, self.active_calls - 1)
        trunk = self.trunks.get(route.trunk_name)
        if trunk:
            trunk.active_calls = max(0, trunk.active_calls - 1)

    def record_outcome(self, route: RouteResult, success: bool) -> None:
        trunk = self.trunks.get(route.trunk_name)
        if not trunk:
            return
        if success:
            trunk.successes += 1
            trunk.consecutive_failures = 0
            trunk.healthy = True
            return
        trunk.failures += 1
        trunk.consecutive_failures += 1
        failure_threshold = max(1, int(self.call_admission.get("trunk_failure_threshold", 3)))
        if trunk.consecutive_failures >= failure_threshold:
            trunk.healthy = False

    def record_probe_result(self, trunk_name: str, success: bool, status: str) -> Optional[TrunkRuntime]:
        trunk = self.trunks.get(trunk_name)
        if not trunk:
            return None
        trunk.last_probe_at = time.time()
        trunk.last_probe_status = status
        if success:
            trunk.options_probe_successes += 1
            trunk.options_probe_consecutive_successes += 1
            trunk.options_probe_consecutive_failures = 0
            if not trunk.healthy and trunk.options_probe_consecutive_successes >= trunk.options_probe_recovery_threshold:
                trunk.healthy = True
                trunk.consecutive_failures = 0
            return trunk

        trunk.options_probe_failures += 1
        trunk.options_probe_consecutive_failures += 1
        trunk.options_probe_consecutive_successes = 0
        if trunk.options_probe_consecutive_failures >= trunk.options_probe_failure_threshold:
            trunk.healthy = False
        return trunk

    def metrics(self) -> Dict[str, int]:
        values = {
            "playsbc_active_calls": self.active_calls,
            "playsbc_admission_rejections_total": self.rejected_calls,
        }
        for name, trunk in sorted(self.trunks.items()):
            prefix = "playsbc_trunk_" + re.sub(r"[^a-zA-Z0-9_]", "_", name)
            values[f"{prefix}_healthy"] = int(trunk.healthy)
            values[f"{prefix}_active_calls"] = trunk.active_calls
            values[f"{prefix}_attempts_total"] = trunk.attempts
            values[f"{prefix}_successes_total"] = trunk.successes
            values[f"{prefix}_failures_total"] = trunk.failures
            values[f"{prefix}_options_probe_successes_total"] = trunk.options_probe_successes
            values[f"{prefix}_options_probe_failures_total"] = trunk.options_probe_failures
            values[f"{prefix}_options_probe_consecutive_failures"] = trunk.options_probe_consecutive_failures
        return values


PROMETHEUS_METRIC_META: Dict[str, Tuple[str, str]] = {
    "playsbc_active_calls": ("gauge", "Current active calls admitted by PlaySBC."),
    "playsbc_admission_rejections_total": ("counter", "Total calls rejected by call admission control."),
    "playsbc_sip_requests_total": ("counter", "Total SIP requests observed by PlaySBC."),
    "playsbc_sip_responses_total": ("counter", "Total SIP responses observed by PlaySBC."),
    "playsbc_b2bua_calls_total": ("counter", "Total B2BUA calls attempted by PlaySBC."),
    "playsbc_b2bua_calls_answered_total": ("counter", "Total B2BUA calls answered by PlaySBC."),
    "playsbc_b2bua_calls_completed_total": ("counter", "Total B2BUA calls completed by PlaySBC."),
    "playsbc_b2bua_calls_failed_total": ("counter", "Total B2BUA calls failed before normal completion."),
    "playsbc_media_negotiations_total": ("counter", "Total answered calls with negotiated media codecs."),
    "playsbc_transcoding_sessions_total": ("counter", "Total answered calls where PlaySBC negotiated different inbound and outbound audio codecs."),
    "playsbc_registrations_total": ("counter", "Total successful SIP registrations accepted by PlaySBC."),
    "playsbc_trunk_healthy": ("gauge", "Trunk health state, 1 for healthy and 0 for unhealthy."),
    "playsbc_trunk_active_calls": ("gauge", "Current active calls on a trunk."),
    "playsbc_trunk_attempts_total": ("counter", "Total attempted calls on a trunk."),
    "playsbc_trunk_successes_total": ("counter", "Total successful calls on a trunk."),
    "playsbc_trunk_failures_total": ("counter", "Total failed calls on a trunk."),
    "playsbc_trunk_options_probe_successes_total": ("counter", "Total successful OPTIONS probes by trunk."),
    "playsbc_trunk_options_probe_failures_total": ("counter", "Total failed OPTIONS probes by trunk."),
    "playsbc_trunk_options_probe_consecutive_failures": ("gauge", "Current consecutive failed OPTIONS probes by trunk."),
    "playsbc_realm_info": ("gauge", "Configured PlaySBC lab realm presence."),
    "playsbc_stream_connects_total": ("counter", "Total outbound SIP stream connection attempts."),
    "playsbc_stream_reuses_total": ("counter", "Total outbound SIP stream connection reuses."),
    "playsbc_stream_failures_total": ("counter", "Total outbound SIP stream connection failures."),
    "playsbc_ha_enabled": ("gauge", "Whether HA shared state is enabled."),
    "playsbc_ha_configured_nodes": ("gauge", "Number of configured HA nodes."),
    "playsbc_ha_node_draining": ("gauge", "Whether this PlaySBC node is draining new calls."),
    "playsbc_ha_dialog_restores_total": ("counter", "Total restored dialogs from shared HA state."),
    "playsbc_ha_shared_registrations": ("gauge", "Registrations currently present in shared HA state."),
    "playsbc_ha_shared_dialogs": ("gauge", "Dialogs currently present in shared HA state."),
    "playsbc_ha_shared_answered_dialogs": ("gauge", "Answered dialogs currently present in shared HA state."),
    "playsbc_ai_voice_calls_active": ("gauge", "Current active AI voice calls."),
    "playsbc_ai_voice_calls_total": ("counter", "Total AI voice calls accepted by PlaySBC."),
    "playsbc_ai_voice_turns_total": ("counter", "Total AI voice turns started."),
    "playsbc_ai_voice_turn_failures_total": ("counter", "Total AI voice turns that returned an error or fallback."),
    "playsbc_ai_stt_audio_decodes_total": ("counter", "Total AI voice turns with decoded caller audio."),
    "playsbc_ai_rasa_requests_total": ("counter", "Total Rasa REST turns attempted."),
    "playsbc_ai_rasa_failures_total": ("counter", "Total Rasa REST turns that used fallback because of an error."),
    "playsbc_ai_tts_outputs_total": ("counter", "Total AI TTS output chunks generated or attempted."),
    "playsbc_ai_tts_rtp_prompts_total": ("counter", "Total AI TTS RTP prompt chunks generated."),
    "playsbc_ai_bot_actions_total": ("counter", "Total bot control actions accepted from Rasa."),
    "playsbc_rtpengine_control_requests_total": ("counter", "Total RTPengine control requests attempted by PlaySBC."),
    "playsbc_rtpengine_control_failures_total": ("counter", "Total RTPengine control request failures observed by PlaySBC."),
    "playsbc_rtpengine_media_sessions_active": ("gauge", "Current active PlaySBC calls using RTPengine as media backend."),
}


def prometheus_escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def prometheus_sample(name: str, value: int | float, labels: Optional[Dict[str, str]] = None) -> str:
    if not labels:
        return f"{name} {value}"
    rendered_labels = ",".join(
        f'{key}="{prometheus_escape_label(str(label_value))}"' for key, label_value in sorted(labels.items())
    )
    return f"{name}{{{rendered_labels}}} {value}"


def prometheus_metric_meta(name: str) -> Tuple[str, str]:
    if name in PROMETHEUS_METRIC_META:
        return PROMETHEUS_METRIC_META[name]
    if re.match(r"playsbc_trunk_.+_(attempts|successes|failures|options_probe_successes|options_probe_failures)_total$", name):
        return "counter", f"Legacy per-trunk PlaySBC counter {name}."
    if re.match(r"playsbc_trunk_.+_(healthy|active_calls|options_probe_consecutive_failures)$", name):
        return "gauge", f"Legacy per-trunk PlaySBC gauge {name}."
    return "gauge", f"PlaySBC metric {name}."


def render_prometheus_metrics(samples: Iterable[Tuple[str, int | float, Dict[str, str]]]) -> str:
    lines: List[str] = []
    emitted_meta: set[str] = set()
    for name, value, labels in samples:
        if name not in emitted_meta:
            metric_type, help_text = prometheus_metric_meta(name)
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {metric_type}")
            emitted_meta.add(name)
        lines.append(prometheus_sample(name, value, labels))
    return "\n".join(lines) + "\n"


class SbcLogger:
    CATEGORY_FILES = {
        "sip": "log.sip",
        "media": "log.media",
        "transcoding": "log.transcoding",
        "ai": "log.ai",
        "platform": "log.platform",
        "networking": "log.networking",
        "udp": "log.udp",
        "tcp": "log.tcp",
        "tls": "log.tls",
        "call": "log.call",
        "sipp": "log.sipp",
    }

    def __init__(self, log_dir: Optional[Path]):
        self.log_dir = log_dir
        self.enabled = log_dir is not None
        self.paths = {category: log_dir / filename for category, filename in self.CATEGORY_FILES.items()} if log_dir else {}
        if not self.enabled:
            return
        assert log_dir is not None
        log_dir.mkdir(parents=True, exist_ok=True)
        for category, path in self.paths.items():
            path.write_text("", encoding="utf-8")
            self.write(category, "LOG START", f"file={path.name}")

    def write(self, category: str, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        if not self.enabled:
            return
        category = category.lower()
        path = self.paths.get(category) or self.paths["platform"]
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        parts = [timestamp, event]
        if call_id:
            parts.append(f"call_id={call_id}")
        if leg:
            parts.append(f"leg={leg}")
        if detail:
            parts.append(detail)
        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(" | ".join(parts) + "\n")

    def sip(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("sip", event, detail, call_id=call_id, leg=leg)

    def media(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("media", event, detail, call_id=call_id, leg=leg)

    def transcoding(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("transcoding", event, detail, call_id=call_id, leg=leg)

    def ai(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("ai", event, detail, call_id=call_id, leg=leg)

    def platform(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("platform", event, detail, call_id=call_id, leg=leg)

    def networking(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("networking", event, detail, call_id=call_id, leg=leg)

    def udp(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("udp", event, detail, call_id=call_id, leg=leg)

    def tcp(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("tcp", event, detail, call_id=call_id, leg=leg)

    def tls(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("tls", event, detail, call_id=call_id, leg=leg)

    def call(self, event: str, detail: str = "", call_id: str = "", leg: str = "") -> None:
        self.write("call", event, detail, call_id=call_id, leg=leg)

    def write_block(self, category: str, title: str, block: str, call_id: str = "") -> None:
        if not self.enabled:
            return
        category = category.lower()
        path = self.paths.get(category) or self.paths["platform"]
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        header = f"{timestamp} | {title}"
        if call_id:
            header += f" | call_id={call_id}"
        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(header + "\n")
            log_file.write(block.rstrip() + "\n")


class SharedStateStore:
    def __init__(self, path: str, node_id: str, logger: SbcLogger):
        self.path = path
        self.node_id = node_id
        self.logger = logger
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=2.0, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=2000")
        self._init_schema()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS registrations (
                user TEXT PRIMARY KEY,
                contact_uri TEXT NOT NULL,
                source_host TEXT NOT NULL,
                source_port INTEGER NOT NULL,
                expires_at REAL NOT NULL,
                registered_at REAL NOT NULL,
                owner_node TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dialogs (
                call_id TEXT PRIMARY KEY,
                local_tag TEXT NOT NULL,
                remote_tag TEXT NOT NULL,
                invite_branch TEXT NOT NULL,
                remote_cseq INTEGER NOT NULL,
                local_cseq INTEGER NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                ringing_at REAL,
                answered_at REAL,
                acknowledged_at REAL,
                terminated_at REAL,
                owner_node TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )

    def load_registrations(self, now: Optional[float] = None) -> Dict[str, Registration]:
        timestamp = time.time() if now is None else now
        rows = self.connection.execute(
            "SELECT * FROM registrations WHERE expires_at > ?",
            (timestamp,),
        ).fetchall()
        return {
            str(row["user"]): Registration(
                user=str(row["user"]),
                contact_uri=str(row["contact_uri"]),
                source=(str(row["source_host"]), int(row["source_port"])),
                expires_at=float(row["expires_at"]),
                registered_at=float(row["registered_at"]),
            )
            for row in rows
        }

    def save_registration(self, registration: Registration) -> None:
        now = time.time()
        self.connection.execute(
            """
            INSERT INTO registrations (
                user, contact_uri, source_host, source_port, expires_at, registered_at, owner_node, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user) DO UPDATE SET
                contact_uri=excluded.contact_uri,
                source_host=excluded.source_host,
                source_port=excluded.source_port,
                expires_at=excluded.expires_at,
                registered_at=excluded.registered_at,
                owner_node=excluded.owner_node,
                updated_at=excluded.updated_at
            """,
            (
                registration.user,
                registration.contact_uri,
                registration.source[0],
                registration.source[1],
                registration.expires_at,
                registration.registered_at,
                self.node_id,
                now,
            ),
        )
        self.logger.platform(
            "HA REGISTRATION SYNC",
            f"node={self.node_id} user={registration.user} contact={registration.contact_uri}",
        )

    def delete_registration(self, user: str) -> None:
        self.connection.execute("DELETE FROM registrations WHERE user = ?", (user,))
        self.logger.platform("HA REGISTRATION DELETE", f"node={self.node_id} user={user}")

    def delete_expired_registrations(self, now: Optional[float] = None) -> int:
        timestamp = time.time() if now is None else now
        cursor = self.connection.execute("DELETE FROM registrations WHERE expires_at <= ?", (timestamp,))
        return int(cursor.rowcount or 0)

    def counts(self) -> Dict[str, int]:
        registrations = self.connection.execute("SELECT COUNT(*) FROM registrations").fetchone()[0]
        dialogs = self.connection.execute("SELECT COUNT(*) FROM dialogs").fetchone()[0]
        answered_dialogs = self.connection.execute("SELECT COUNT(*) FROM dialogs WHERE state = 'ANSWERED'").fetchone()[0]
        return {
            "playsbc_ha_shared_registrations": int(registrations),
            "playsbc_ha_shared_dialogs": int(dialogs),
            "playsbc_ha_shared_answered_dialogs": int(answered_dialogs),
        }

    def dialog_owner(self, call_id: str) -> str:
        row = self.connection.execute("SELECT owner_node FROM dialogs WHERE call_id = ?", (call_id,)).fetchone()
        return str(row["owner_node"]) if row else ""

    def save_dialog(self, dialog: SipDialog) -> None:
        self.connection.execute(
            """
            INSERT INTO dialogs (
                call_id, local_tag, remote_tag, invite_branch, remote_cseq, local_cseq, state,
                created_at, ringing_at, answered_at, acknowledged_at, terminated_at, owner_node, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(call_id) DO UPDATE SET
                local_tag=excluded.local_tag,
                remote_tag=excluded.remote_tag,
                invite_branch=excluded.invite_branch,
                remote_cseq=excluded.remote_cseq,
                local_cseq=excluded.local_cseq,
                state=excluded.state,
                ringing_at=excluded.ringing_at,
                answered_at=excluded.answered_at,
                acknowledged_at=excluded.acknowledged_at,
                terminated_at=excluded.terminated_at,
                owner_node=excluded.owner_node,
                updated_at=excluded.updated_at
            """,
            (
                dialog.call_id,
                dialog.local_tag,
                dialog.remote_tag,
                dialog.invite_branch,
                dialog.remote_cseq,
                dialog.local_cseq,
                dialog.state.name,
                dialog.created_at,
                dialog.ringing_at,
                dialog.answered_at,
                dialog.acknowledged_at,
                dialog.terminated_at,
                self.node_id,
                time.time(),
            ),
        )
        self.logger.platform(
            "HA DIALOG SYNC",
            f"node={self.node_id} call_id={dialog.call_id} state={dialog.state.name}",
        )

    def load_dialog(self, call_id: str) -> Optional[SipDialog]:
        row = self.connection.execute("SELECT * FROM dialogs WHERE call_id = ?", (call_id,)).fetchone()
        if not row:
            return None
        try:
            state = CallState[str(row["state"])]
        except KeyError:
            state = CallState.INIT
        return SipDialog(
            call_id=str(row["call_id"]),
            local_tag=str(row["local_tag"]),
            remote_tag=str(row["remote_tag"]),
            invite_branch=str(row["invite_branch"]),
            remote_cseq=int(row["remote_cseq"]),
            local_cseq=int(row["local_cseq"]),
            state=state,
            created_at=float(row["created_at"]),
            ringing_at=float(row["ringing_at"]) if row["ringing_at"] is not None else None,
            answered_at=float(row["answered_at"]) if row["answered_at"] is not None else None,
            acknowledged_at=float(row["acknowledged_at"]) if row["acknowledged_at"] is not None else None,
            terminated_at=float(row["terminated_at"]) if row["terminated_at"] is not None else None,
        )

    def close(self) -> None:
        self.connection.close()


class B2BUAFlowLog:
    DEFAULT_LADDER_PARTICIPANTS = ("SIPp A", "B2BUA", "SIPp B")
    LADDER_STEP_WIDTH = 6
    LADDER_COLUMN_WIDTH = 28

    def __init__(
        self,
        log_dir: Optional[Path],
        inbound_call_id: str,
        target_user: str,
        route: RouteResult,
        enabled: bool = True,
        logger: Optional[SbcLogger] = None,
        participants: Optional[Tuple[str, ...]] = None,
    ):
        self.enabled = enabled
        self.logger = logger
        self.inbound_call_id = inbound_call_id
        self.participants = participants or self.DEFAULT_LADDER_PARTICIPANTS
        self.events: List[Tuple[str, str, str, str]] = []
        self.path = log_dir / "log.call" if enabled and log_dir else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write(
            "CALL START",
            (
                f"inbound_call_id={inbound_call_id} target_user={target_user} "
                f"route={route.target.uri} route_source={route.source} route_policy={route.policy_name}"
            ),
        )

    def write(self, event: str, detail: str = "") -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        line = f"{timestamp} {event}"
        if detail:
            line += f" {detail}"
        if self.path:
            with self.path.open("a", encoding="utf-8") as log_file:
                log_file.write(line + "\n")
        upper_event = event.upper()
        should_emit_structured = self.enabled or "RTPENGINE" in upper_event or upper_event == "MEDIA BACKEND"
        if self.logger and event != "SIP FLOW" and should_emit_structured:
            self.logger.write(
                log_category_for_flow_event(event),
                f"B2BUA {event}",
                detail,
                call_id=self.inbound_call_id,
            )

    def sip(self, sender: str, receiver: str, message: str, detail: str = "") -> None:
        if not self.enabled:
            return
        self.events.append((sender, receiver, message, detail))
        suffix = f" {detail}" if detail else ""
        self.write("SIP FLOW", f"{sender} -> {receiver}: {message}{suffix}")
        if self.logger:
            self.logger.sip(
                "B2BUA SIP FLOW",
                f"{sender} -> {receiver}: {message}{suffix}",
                call_id=self.inbound_call_id,
                leg=f"{sender}->{receiver}",
            )

    def render_ladder(self) -> None:
        if not self.path:
            return
        ladder = self.render_ladder_text()
        with self.path.open("a", encoding="utf-8") as log_file:
            log_file.write("\n" + ladder + "\n")
        if self.logger:
            self.logger.write_block("sip", "B2BUA SIP LADDER", ladder, call_id=self.inbound_call_id)

    def render_ladder_text(self) -> str:
        lines = [
            "SIP LADDER",
            self._ladder_header(),
            self._ladder_separator(),
            self._ladder_lifeline(),
        ]
        for index, (sender, receiver, message, detail) in enumerate(self.events, start=1):
            lines.extend(self._ladder_event(index, sender, receiver, message))
        lines.append(self._ladder_lifeline())
        return "\n".join(lines)

    def _ladder_header(self) -> str:
        columns = [f"{participant:^{self.LADDER_COLUMN_WIDTH}}" for participant in self.participants]
        return f"{'Step':<{self.LADDER_STEP_WIDTH}}" + "".join(columns).rstrip()

    def _ladder_separator(self) -> str:
        return "-" * (self.LADDER_STEP_WIDTH + (self.LADDER_COLUMN_WIDTH * len(self.participants)))

    def _ladder_lifeline(self, step: str = "") -> str:
        row = self._blank_ladder_row(step)
        for position in self._ladder_positions():
            row[position] = "|"
        return "".join(row).rstrip()

    def _ladder_event(self, index: int, sender: str, receiver: str, label: str) -> List[str]:
        if sender not in self.participants or receiver not in self.participants:
            return [f"{index:02d} {sender} -> {receiver}: {label}"]

        sender_index = self.participants.index(sender)
        receiver_index = self.participants.index(receiver)

        return [
            self._ladder_label_line(index, sender_index, receiver_index, label),
            self._ladder_arrow_line(sender_index, receiver_index),
        ]

    def _ladder_label_line(self, index: int, sender_index: int, receiver_index: int, label: str) -> str:
        row = self._blank_ladder_row(f"{index:02d}")
        positions = self._ladder_positions()
        for position in positions:
            row[position] = "|"
        left = min(positions[sender_index], positions[receiver_index])
        right = max(positions[sender_index], positions[receiver_index])
        text_start = left + 2
        text_width = max(1, right - left - 3)
        text = self._short_label(label, text_width)
        label_start = text_start + max(0, (text_width - len(text)) // 2)
        self._put_text(row, label_start, text)
        return "".join(row).rstrip()

    def _ladder_arrow_line(self, sender_index: int, receiver_index: int) -> str:
        row = self._blank_ladder_row("")
        positions = self._ladder_positions()
        position_set = set(positions)
        for position in positions:
            row[position] = "|"

        sender = positions[sender_index]
        receiver = positions[receiver_index]
        if sender < receiver:
            for position in range(sender + 1, receiver - 1):
                row[position] = "+" if position in position_set else "-"
            row[receiver - 1] = ">"
        else:
            row[receiver + 1] = "<"
            for position in range(receiver + 2, sender):
                row[position] = "+" if position in position_set else "-"
        return "".join(row).rstrip()

    def _blank_ladder_row(self, step: str) -> List[str]:
        width = self.LADDER_STEP_WIDTH + (self.LADDER_COLUMN_WIDTH * len(self.participants))
        row = list(" " * width)
        self._put_text(row, 0, f"{step:<{self.LADDER_STEP_WIDTH}}")
        return row

    def _ladder_positions(self) -> List[int]:
        return [
            self.LADDER_STEP_WIDTH + (index * self.LADDER_COLUMN_WIDTH) + (self.LADDER_COLUMN_WIDTH // 2)
            for index, _ in enumerate(self.participants)
        ]

    def _put_text(self, row: List[str], start: int, text: str) -> None:
        for offset, character in enumerate(text):
            position = start + offset
            if 0 <= position < len(row):
                row[position] = character

    def _short_label(self, label: str, limit: int = 14) -> str:
        cleaned = " ".join(label.split())
        return cleaned[:limit]


class AIVoiceFlowLog:
    PARTICIPANTS = ("SIPp A", "PlaySBC", "Scripted STT", "Rasa Bot", "Text TTS")
    STEP_WIDTH = 6
    COLUMN_WIDTH = 24

    def __init__(self, logger: SbcLogger, call_id: str, participants: Optional[Tuple[str, ...]] = None):
        self.logger = logger
        self.call_id = call_id
        self.participants = participants or self.PARTICIPANTS
        self.events: List[Tuple[str, str, str]] = []

    def flow(self, sender: str, receiver: str, message: str) -> None:
        self.events.append((sender, receiver, message))
        ai_tokens = ("Rasa", "Bot", "Agent", "STT", "TTS", "Vosk", "Piper")
        category = "ai" if any(token in sender or token in receiver for token in ai_tokens) else "sip"
        self.logger.write(
            category,
            "AI VOICE FLOW",
            f"{sender} -> {receiver}: {message}",
            call_id=self.call_id,
            leg=f"{sender}->{receiver}",
        )

    def render(self) -> None:
        block = self.render_text()
        self.logger.write_block("sip", "AI VOICE CALL LADDER", block, call_id=self.call_id)
        self.logger.write_block("ai", "AI VOICE CALL LADDER", block, call_id=self.call_id)

    def render_text(self) -> str:
        lines = [
            "AI VOICE CALL LADDER",
            self._header(),
            self._separator(),
            self._lifeline(),
        ]
        for index, (sender, receiver, label) in enumerate(self.events, start=1):
            lines.extend(self._event(index, sender, receiver, label))
        lines.append(self._lifeline())
        return "\n".join(lines)

    def _header(self) -> str:
        columns = [f"{participant:^{self.COLUMN_WIDTH}}" for participant in self.participants]
        return f"{'Step':<{self.STEP_WIDTH}}" + "".join(columns).rstrip()

    def _separator(self) -> str:
        return "-" * (self.STEP_WIDTH + (self.COLUMN_WIDTH * len(self.participants)))

    def _positions(self) -> List[int]:
        return [
            self.STEP_WIDTH + (index * self.COLUMN_WIDTH) + (self.COLUMN_WIDTH // 2)
            for index, _participant in enumerate(self.participants)
        ]

    def _blank(self, step: str = "") -> List[str]:
        row = list(" " * (self.STEP_WIDTH + self.COLUMN_WIDTH * len(self.participants)))
        self._put(row, 0, f"{step:<{self.STEP_WIDTH}}")
        return row

    def _lifeline(self, step: str = "") -> str:
        row = self._blank(step)
        for position in self._positions():
            row[position] = "|"
        return "".join(row).rstrip()

    def _event(self, index: int, sender: str, receiver: str, label: str) -> List[str]:
        if sender not in self.participants or receiver not in self.participants:
            return [f"{index:02d} {sender} -> {receiver}: {label}"]
        sender_index = self.participants.index(sender)
        receiver_index = self.participants.index(receiver)
        return [
            self._label_line(index, sender_index, receiver_index, label),
            self._arrow_line(sender_index, receiver_index),
        ]

    def _label_line(self, index: int, sender_index: int, receiver_index: int, label: str) -> str:
        row = self._blank(f"{index:02d}")
        positions = self._positions()
        for position in positions:
            row[position] = "|"
        left = min(positions[sender_index], positions[receiver_index])
        right = max(positions[sender_index], positions[receiver_index])
        text_width = max(1, right - left - 3)
        text = " ".join(label.split())[:text_width]
        start = left + 2 + max(0, (text_width - len(text)) // 2)
        self._put(row, start, text)
        return "".join(row).rstrip()

    def _arrow_line(self, sender_index: int, receiver_index: int) -> str:
        row = self._blank()
        positions = self._positions()
        position_set = set(positions)
        for position in positions:
            row[position] = "|"
        sender = positions[sender_index]
        receiver = positions[receiver_index]
        if sender < receiver:
            for position in range(sender + 1, receiver - 1):
                row[position] = "+" if position in position_set else "-"
            row[receiver - 1] = ">"
        else:
            row[receiver + 1] = "<"
            for position in range(receiver + 2, sender):
                row[position] = "+" if position in position_set else "-"
        return "".join(row).rstrip()

    def _put(self, row: List[str], start: int, text: str) -> None:
        for offset, character in enumerate(text):
            position = start + offset
            if 0 <= position < len(row):
                row[position] = character


@dataclass
class B2BUACall:
    inbound_call_id: str
    outbound_call_id: str
    outbound_target: SipUri
    outbound_from_header: str
    target_user: str
    route_policy: str
    route_source: str
    flow_log: B2BUAFlowLog
    route_result: Optional[RouteResult] = None
    admission_released: bool = False
    media_backend: str = "internal"
    rtpengine_call_id: str = ""
    rtpengine_from_tag: str = ""
    rtpengine_to_tag: str = ""
    outbound_to_header: str = ""
    outbound_contact_uri: str = ""
    outbound_invite_via_header: str = ""
    outbound_cseq: int = 1
    outbound_bye_sent: bool = False
    outbound_cancel_sent: bool = False
    finalized: bool = False


@dataclass
class AIVoiceCall:
    call_id: str
    target_user: str
    route_result: RouteResult
    flow_log: AIVoiceFlowLog
    selected_payload: int = PCMU
    dtmf_payload_type: Optional[int] = None
    media_backend: str = "internal"
    rtpengine_call_id: str = ""
    rtpengine_from_tag: str = ""
    rtpengine_to_tag: str = ""
    rtpengine_query_observed: bool = False
    rtpengine_query_summary: str = ""
    rtpengine_query_packet_samples: Tuple[int, ...] = field(default_factory=tuple)
    rtpengine_query_retries: int = 0
    bot_actions: List[BotAction] = field(default_factory=list)
    task: Optional[asyncio.Task] = None
    finalized: bool = False


async def retry_rtpengine_control(
    action: str,
    request: Callable[[], Awaitable[Dict[str, Any]]],
    flow_log: B2BUAFlowLog,
    attempts: int = 3,
    base_delay: float = 0.150,
) -> Dict[str, Any]:
    last_error: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return await request()
        except (asyncio.TimeoutError, OSError) as exc:
            last_error = exc
            detail = f"attempt={attempt}/{attempts} error={type(exc).__name__}"
            if str(exc):
                detail += f" detail={exc}"
            if attempt >= attempts:
                flow_log.write(f"RTPENGINE {action} RETRY EXHAUSTED", detail)
                break
            flow_log.write(f"RTPENGINE {action} RETRY", detail)
            await asyncio.sleep(base_delay * attempt)
    assert last_error is not None
    raise last_error


@dataclass
class RtpSession:
    call_id: str
    local_ip: str
    local_port: int
    preferred_payload: int = PCMU
    remote_payloads: Tuple[int, ...] = field(default_factory=tuple)
    dtmf_payload_type: Optional[int] = None
    logger: Optional[SbcLogger] = None
    leg_label: str = ""
    remote_addr: Optional[Tuple[str, int]] = None
    remote_rtcp_addr: Optional[Tuple[str, int]] = None
    transport: Optional[asyncio.DatagramTransport] = None
    rtcp_transport: Optional[asyncio.DatagramTransport] = None
    sequence: int = field(default_factory=lambda: random.randint(0, 65535))
    timestamp: int = field(default_factory=lambda: random.randint(0, 2**32 - 1))
    ssrc: int = field(default_factory=lambda: random.randint(1, 2**32 - 1))
    created_at: float = field(default_factory=time.time)
    acknowledged_at: Optional[float] = None
    last_rtp_at: Optional[float] = None
    packets_received: int = 0
    packets_sent: int = 0
    bytes_received: int = 0
    bytes_sent: int = 0
    payload_types_received: Dict[int, int] = field(default_factory=dict)
    dtmf_events: list = field(default_factory=list)
    dtmf_events_started: set = field(default_factory=set)
    dtmf_events_completed: set = field(default_factory=set)
    dtmf_relay_timestamps: Dict[Tuple[int, int], int] = field(default_factory=dict)
    analyzer: RtpAnalyzer = field(default_factory=RtpAnalyzer)
    media_mode: str = "echo"
    bridge_id: str = ""
    peer_session: Optional["RtpSession"] = None
    relayed_packets: int = 0
    relayed_bytes: int = 0
    rtcp_packets_received: int = 0
    rtcp_packets_sent: int = 0
    rtcp_packets_relayed: int = 0
    quality_loss_warn_percent: float = 1.0
    quality_jitter_warn_ms: float = 30.0
    relay_wait_logged: bool = False
    ai_input_only_logged: bool = False
    closed: bool = False

    def log(self, event: str, detail: str = "", category: Optional[str] = None) -> None:
        if self.logger:
            self.logger.write(
                category or log_category_for_session_event(event),
                event,
                detail,
                call_id=self.call_id,
                leg=self.leg_label or self.media_mode,
            )

    def mark_ack(self) -> None:
        self.acknowledged_at = time.time()
        self.log("ACK RECEIVED")

    def record_rtp_packet(self, packet: RtpPacket, record_audio: bool = True) -> None:
        packet_index = self.packets_received + 1
        if packet_index <= 5 or packet_index % 500 == 0:
            self.log(
                "RTP PACKET RX",
                (
                    f"count={packet_index} seq={packet.sequence} timestamp={packet.timestamp} "
                    f"payload_type={CODEC_NAMES.get(packet.payload_type, packet.payload_type)} "
                    f"marker={int(packet.marker)} payload_bytes={len(packet.payload)}"
                ),
            )
        self.analyzer.observe(packet, record_audio=record_audio)
        self.record_rtp(packet.payload_type, packet.payload, record_audio=record_audio)

    def record_rtp(self, payload_type: int, payload: bytes, record_audio: bool = True) -> None:
        self.packets_received += 1
        self.bytes_received += len(payload)
        self.payload_types_received[payload_type] = self.payload_types_received.get(payload_type, 0) + 1
        self.last_rtp_at = time.time()

    def record_rtp_sent(self, payload: bytes) -> None:
        self.packets_sent += 1
        self.bytes_sent += len(payload)
        self.last_rtp_at = time.time()

    def set_peer(self, peer: "RtpSession") -> None:
        self.peer_session = peer
        event = "B2BUA PAIRED" if self.media_mode == "b2bua" else "BRIDGE PAIRED"
        detail = f"peer_call_id={peer.call_id}"
        if self.bridge_id:
            detail = f"bridge_id={self.bridge_id} {detail}"
        self.log(event, detail)

    def clear_peer(self) -> None:
        if self.peer_session:
            event = "B2BUA PEER LEFT" if self.media_mode == "b2bua" else "BRIDGE PEER LEFT"
            self.log(event, f"peer_call_id={self.peer_session.call_id}")
        self.peer_session = None

    def record_relay(self, payload: bytes) -> None:
        self.relayed_packets += 1
        self.relayed_bytes += len(payload)
        if self.relayed_packets <= 5 or self.relayed_packets % 500 == 0:
            self.log(
                "RTP PACKET RELAY",
                f"count={self.relayed_packets} payload_bytes={len(payload)}",
            )

    def handle_dtmf_payload(self, payload: bytes) -> None:
        event = parse_dtmf_event(payload)
        if event is None:
            self.log("DTMF IGNORED", "reason=short_payload")
            return

        event_id, digit, is_end, duration = event
        if event_id not in self.dtmf_events_started:
            self.dtmf_events_started.add(event_id)
            self.log("DTMF START", f"digit={digit} event={event_id}")

        if is_end:
            completion_key = (event_id, duration)
            if completion_key not in self.dtmf_events_completed:
                self.dtmf_events_completed.add(completion_key)
                self.dtmf_events.append(digit)
                self.log("DTMF END", f"digit={digit} event={event_id} duration={duration}")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.transport:
            self.transport.close()
        if self.rtcp_transport:
            self.rtcp_transport.close()
        duration = time.time() - self.created_at
        payloads = ",".join(
            f"{CODEC_NAMES.get(payload_type, str(payload_type))}:{count}"
            for payload_type, count in sorted(self.payload_types_received.items())
        ) or "none"
        dtmf = "".join(self.dtmf_events) or "none"
        self.log(
            "CALL SUMMARY",
            (
                f"duration_seconds={duration:.3f} "
                f"media_mode={self.media_mode} "
                f"rtp_packets_received={self.packets_received} "
                f"rtp_packets_sent={self.packets_sent} "
                f"rtp_packets_relayed={self.relayed_packets} "
                f"rtp_bytes_received={self.bytes_received} "
                f"rtp_bytes_sent={self.bytes_sent} "
                f"rtp_bytes_relayed={self.relayed_bytes} "
                f"rtcp_packets_received={self.rtcp_packets_received} "
                f"rtcp_packets_sent={self.rtcp_packets_sent} "
                f"rtcp_packets_relayed={self.rtcp_packets_relayed} "
                f"payloads_received={payloads} "
                f"dtmf={dtmf} "
                f"{self.analyzer.summary_text()}"
            ),
        )


class G711Transcoder:
    """Converts RTP payloads between PCMU and PCMA."""

    def __init__(self, logger: Optional[SbcLogger] = None):
        self.logger = logger
        self.logged_pairs: set = set()

    def convert(self, payload: bytes, src_pt: int, dst_pt: int) -> bytes:
        if src_pt == dst_pt:
            return payload

        pair = (src_pt, dst_pt)
        if audioop is None:
            logging.warning(
                "audioop is unavailable; cannot transcode payload type %s to %s",
                src_pt,
                dst_pt,
            )
            if self.logger and pair not in self.logged_pairs:
                self.logger.transcoding(
                    "TRANSCODE BYPASS",
                    f"reason=audioop_unavailable src={CODEC_NAMES.get(src_pt, src_pt)} dst={CODEC_NAMES.get(dst_pt, dst_pt)}",
                )
                self.logged_pairs.add(pair)
            return payload

        if src_pt == PCMU and dst_pt == PCMA:
            self._log_conversion(src_pt, dst_pt)
            linear = audioop.ulaw2lin(payload, 2)
            return audioop.lin2alaw(linear, 2)

        if src_pt == PCMA and dst_pt == PCMU:
            self._log_conversion(src_pt, dst_pt)
            linear = audioop.alaw2lin(payload, 2)
            return audioop.lin2ulaw(linear, 2)

        return payload

    def _log_conversion(self, src_pt: int, dst_pt: int) -> None:
        pair = (src_pt, dst_pt)
        if self.logger and pair not in self.logged_pairs:
            self.logger.transcoding(
                "TRANSCODE ACTIVE",
                f"src={CODEC_NAMES.get(src_pt, src_pt)} dst={CODEC_NAMES.get(dst_pt, dst_pt)}",
            )
            self.logged_pairs.add(pair)


class RtpProtocol(asyncio.DatagramProtocol):
    def __init__(self, session: RtpSession, transcoder: G711Transcoder):
        self.session = session
        self.transcoder = transcoder

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.session.transport = transport  # type: ignore[assignment]
        logging.info("RTP listening on %s:%s", self.session.local_ip, self.session.local_port)
        if self.session.logger:
            self.session.logger.udp(
                "UDP LISTENING",
                f"protocol=rtp local={self.session.local_ip}:{self.session.local_port}",
                call_id=self.session.call_id,
                leg=self.session.leg_label or self.session.media_mode,
            )
        self.session.log("RTP LISTENING", f"local={self.session.local_ip}:{self.session.local_port}")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            packet = RtpPacket.parse(data)
        except ValueError:
            if self.session.logger:
                self.session.logger.networking(
                    "RTP PARSE FAILED",
                    f"source={addr[0]}:{addr[1]} bytes={len(data)}",
                    call_id=self.session.call_id,
                    leg=self.session.leg_label or self.session.media_mode,
                )
            return

        first_packet = self.session.remote_addr is None
        self.session.remote_addr = addr
        if first_packet:
            if self.session.logger:
                self.session.logger.udp(
                    "UDP RX FIRST RTP",
                    f"source={addr[0]}:{addr[1]} bytes={len(data)} payload_type={packet.payload_type}",
                    call_id=self.session.call_id,
                    leg=self.session.leg_label or self.session.media_mode,
                )
            self.session.log(
                "RTP REMOTE",
                f"remote={addr[0]}:{addr[1]} first_payload_type={CODEC_NAMES.get(packet.payload_type, packet.payload_type)}",
            )

        record_audio = self.session.dtmf_payload_type != packet.payload_type
        self.session.record_rtp_packet(packet, record_audio=record_audio)

        if self.session.dtmf_payload_type == packet.payload_type:
            self.session.handle_dtmf_payload(packet.payload)
            if self.session.media_mode in {"bridge", "b2bua"}:
                self._relay_packet(packet)
            return

        if self.session.media_mode == "ai-gateway":
            if not self.session.ai_input_only_logged:
                self.session.log(
                    "AI RTP INPUT ONLY",
                    "reason=real_tts_not_configured action=record_without_echo rtp_prompt_generated=false",
                    category="media",
                )
                self.session.ai_input_only_logged = True
            return

        if self.session.media_mode in {"bridge", "b2bua"}:
            self._relay_packet(packet)
            return

        out_payload_type = self.session.preferred_payload
        out_payload = self.transcoder.convert(packet.payload, packet.payload_type, out_payload_type)

        self.session.sequence = (self.session.sequence + 1) & 0xFFFF
        self.session.timestamp = (self.session.timestamp + len(out_payload)) & 0xFFFFFFFF
        response = RtpPacket.build(
            payload_type=out_payload_type,
            sequence=self.session.sequence,
            timestamp=self.session.timestamp,
            ssrc=self.session.ssrc,
            payload=out_payload,
        )

        if self.session.transport:
            self.session.transport.sendto(response, addr)
            self.session.record_rtp_sent(out_payload)

    def _relay_packet(self, packet: RtpPacket) -> None:
        peer = self.session.peer_session
        if not peer or not peer.transport or not peer.remote_addr:
            if not self.session.relay_wait_logged:
                self.session.log("BRIDGE WAITING", "reason=peer_rtp_not_ready")
                self.session.relay_wait_logged = True
            return

        is_dtmf = self.session.dtmf_payload_type == packet.payload_type
        out_payload_type = (peer.dtmf_payload_type or packet.payload_type) if is_dtmf else peer.preferred_payload
        out_payload = packet.payload if is_dtmf else self.transcoder.convert(packet.payload, packet.payload_type, out_payload_type)
        peer.sequence = (peer.sequence + 1) & 0xFFFF
        event = parse_dtmf_event(packet.payload) if is_dtmf else None
        if event:
            event_id, _digit, is_end, duration = event
            relay_key = (packet.ssrc, event_id)
            if relay_key not in self.session.dtmf_relay_timestamps:
                self.session.dtmf_relay_timestamps[relay_key] = (peer.timestamp + 160) & 0xFFFFFFFF
            out_timestamp = self.session.dtmf_relay_timestamps[relay_key]
            peer.timestamp = (out_timestamp + duration) & 0xFFFFFFFF if is_end else out_timestamp
        else:
            peer.timestamp = (peer.timestamp + len(out_payload)) & 0xFFFFFFFF
            out_timestamp = peer.timestamp
        response = RtpPacket.build(
            payload_type=out_payload_type,
            sequence=peer.sequence,
            timestamp=out_timestamp,
            ssrc=peer.ssrc,
            payload=out_payload,
            marker=packet.marker,
        )
        peer.transport.sendto(response, peer.remote_addr)
        peer.record_rtp_sent(out_payload)
        self.session.record_relay(out_payload)
        if is_dtmf:
            digit = event[1] if event else "unknown"
            self.session.log(
                "DTMF RELAY",
                f"digit={digit} src_payload={packet.payload_type} dst_payload={out_payload_type}",
            )


class RtcpProtocol(asyncio.DatagramProtocol):
    def __init__(self, session: RtpSession):
        self.session = session

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.session.rtcp_transport = transport  # type: ignore[assignment]
        local_port = self.session.local_port + 1
        if self.session.logger:
            self.session.logger.udp(
                "UDP LISTENING",
                f"protocol=rtcp local={self.session.local_ip}:{local_port}",
                call_id=self.session.call_id,
                leg=self.session.leg_label or self.session.media_mode,
            )
        self.session.log("RTCP LISTENING", f"local={self.session.local_ip}:{local_port}")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            packets = parse_compound_rtcp(data)
            receiver_reports = parse_receiver_reports(packets)
        except ValueError as exc:
            if self.session.logger:
                self.session.logger.networking(
                    "RTCP PARSE FAILED",
                    f"source={addr[0]}:{addr[1]} bytes={len(data)} reason={exc}",
                    call_id=self.session.call_id,
                    leg=self.session.leg_label or self.session.media_mode,
                )
            return

        for report in receiver_reports:
            quality = (
                "degraded"
                if report.loss_percent > self.session.quality_loss_warn_percent
                or report.jitter_ms > self.session.quality_jitter_warn_ms
                else "good"
            )
            self.session.log(
                "RTCP RECEIVER QUALITY",
                (
                    f"reporter_ssrc={report.reporter_ssrc} source_ssrc={report.source_ssrc} "
                    f"fraction_lost={report.fraction_lost} cumulative_lost={report.cumulative_lost} "
                    f"loss_percent={report.loss_percent:.3f} jitter={report.jitter} "
                    f"jitter_ms={report.jitter_ms:.3f} highest_sequence={report.highest_sequence} "
                    f"quality={quality}"
                ),
            )

        self.session.remote_rtcp_addr = addr
        self.session.rtcp_packets_received += 1
        count = self.session.rtcp_packets_received
        if count <= 3 or count % 10 == 0:
            packet_types = ",".join(str(packet.packet_type) for packet in packets)
            self.session.log("RTCP PACKET RX", f"count={count} source={addr[0]}:{addr[1]} types={packet_types}")

        peer = self.session.peer_session
        if peer and peer.rtcp_transport and peer.remote_rtcp_addr:
            report = build_compound_sender_report(
                ssrc=peer.ssrc,
                cname=f"playsbc-{safe_filename(peer.call_id)}",
                rtp_timestamp=peer.timestamp,
                packet_count=peer.packets_sent,
                octet_count=peer.bytes_sent,
            )
            peer.rtcp_transport.sendto(report, peer.remote_rtcp_addr)
            peer.rtcp_packets_sent += 1
            self.session.rtcp_packets_relayed += 1
            return
        if self.session.media_mode in {"echo", "ai-gateway"} and self.session.rtcp_transport:
            report = build_compound_sender_report(
                ssrc=self.session.ssrc,
                cname=f"playsbc-{safe_filename(self.session.call_id)}",
                rtp_timestamp=self.session.timestamp,
                packet_count=self.session.packets_sent,
                octet_count=self.session.bytes_sent,
            )
            self.session.rtcp_transport.sendto(report, addr)
            self.session.rtcp_packets_sent += 1


class MediaServer:
    def __init__(
        self,
        local_ip: str,
        port_min: int,
        port_max: int,
        log_dir: Optional[Path],
        logger: SbcLogger,
        media_quality: Optional[Dict[str, Any]] = None,
    ):
        self.local_ip = local_ip
        self.port_min = port_min if port_min % 2 == 0 else port_min + 1
        self.port_max = port_max
        self.log_dir = log_dir
        self.logger = logger
        self.sessions: Dict[str, RtpSession] = {}
        self.bridge_waiting: Dict[str, RtpSession] = {}
        self.transcoder = G711Transcoder(logger)
        quality = dict(media_quality or {})
        self.quality_loss_warn_percent = float(quality.get("loss_warn_percent", 1.0))
        self.quality_jitter_warn_ms = float(quality.get("jitter_warn_ms", 30.0))
        self._next_port = self.port_min

    async def create_session(
        self,
        call_id: str,
        preferred_payload: int,
        remote_payloads: Tuple[int, ...],
        dtmf_payload_type: Optional[int],
        bridge_id: str = "",
        media_mode: str = "echo",
        leg_label: str = "",
    ) -> RtpSession:
        if call_id in self.sessions:
            return self.sessions[call_id]

        loop = asyncio.get_running_loop()
        local_port = self._allocate_port()
        session = RtpSession(
            call_id=call_id,
            local_ip=self.local_ip,
            local_port=local_port,
            preferred_payload=preferred_payload,
            remote_payloads=remote_payloads,
            dtmf_payload_type=dtmf_payload_type,
            logger=self.logger,
            leg_label=leg_label,
            media_mode="bridge" if bridge_id else media_mode,
            bridge_id=bridge_id,
            quality_loss_warn_percent=self.quality_loss_warn_percent,
            quality_jitter_warn_ms=self.quality_jitter_warn_ms,
        )
        await loop.create_datagram_endpoint(
            lambda: RtpProtocol(session, self.transcoder),
            local_addr=(self.local_ip, local_port),
        )
        await loop.create_datagram_endpoint(
            lambda: RtcpProtocol(session),
            local_addr=(self.local_ip, local_port + 1),
        )
        self.sessions[call_id] = session
        if bridge_id:
            self.join_bridge(session)
        return session

    def join_bridge(self, session: RtpSession) -> None:
        waiting = self.bridge_waiting.get(session.bridge_id)
        if waiting and waiting.call_id != session.call_id and not waiting.closed:
            waiting.set_peer(session)
            session.set_peer(waiting)
            self.bridge_waiting.pop(session.bridge_id, None)
            logging.info("Paired bridge %s: %s <-> %s", session.bridge_id, waiting.call_id, session.call_id)
            return

        self.bridge_waiting[session.bridge_id] = session
        session.log("BRIDGE WAITING", f"bridge_id={session.bridge_id} reason=waiting_for_second_leg")

    def get_session(self, call_id: str) -> Optional[RtpSession]:
        return self.sessions.get(call_id)

    def close_session(self, call_id: str) -> None:
        session = self.sessions.pop(call_id, None)
        if session:
            if session.bridge_id and self.bridge_waiting.get(session.bridge_id) is session:
                self.bridge_waiting.pop(session.bridge_id, None)
            if session.peer_session:
                session.peer_session.clear_peer()
            session.close()
            logging.info("Closed RTP session for call-id %s", call_id)

    def _allocate_port(self) -> int:
        start = self._next_port
        while True:
            port = self._next_port
            self._next_port += 2
            if self._next_port > self.port_max:
                self._next_port = self.port_min

            if self._port_is_free(port):
                return port

            if self._next_port == start:
                raise RuntimeError("No RTP ports available")

    def _port_is_free(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            try:
                sock.bind((self.local_ip, port))
            except OSError:
                return False
        return True


class SipTcpConnectionProtocol(asyncio.Protocol):
    def __init__(self, server: "SipServerProtocol", transport_name: str = "tcp"):
        self.server = server
        self.transport_name = normalize_sip_transport(transport_name)
        self.transport: Optional[asyncio.Transport] = None
        self.peer: Tuple[str, int] = ("0.0.0.0", 0)
        self.buffer = bytearray()
        self.closed = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        peer = transport.get_extra_info("peername")
        if isinstance(peer, tuple) and len(peer) >= 2:
            self.peer = (str(peer[0]), int(peer[1]))
        self.server.register_stream_connection(self.transport_name, self.peer, self)
        self.server.logger.write(
            self.transport_name,
            f"{self.transport_name.upper()} CONNECTED",
            f"protocol=sip peer={self.peer[0]}:{self.peer[1]}",
        )

    def data_received(self, data: bytes) -> None:
        self.buffer.extend(data)
        self.server.logger.write(
            self.transport_name,
            f"{self.transport_name.upper()} RX BYTES",
            f"protocol=sip source={self.peer[0]}:{self.peer[1]} bytes={len(data)}",
        )
        for message in self._pop_complete_messages():
            self.server.receive_sip_data(message, self.peer, transport_name=self.transport_name, connection=self)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.closed = True
        self.server.unregister_stream_connection(self.transport_name, self.peer, self)
        detail = f"protocol=sip peer={self.peer[0]}:{self.peer[1]}"
        if exc:
            detail += f" error={exc}"
        self.server.logger.write(self.transport_name, f"{self.transport_name.upper()} DISCONNECTED", detail)

    def send(self, packet: bytes) -> None:
        if not self.transport or self.closed:
            raise ConnectionError(f"{self.transport_name.upper()} connection to {self.peer[0]}:{self.peer[1]} is closed")
        self.transport.write(packet)

    def _pop_complete_messages(self) -> List[bytes]:
        messages: List[bytes] = []
        separator = b"\r\n\r\n"
        while True:
            header_end = self.buffer.find(separator)
            if header_end < 0:
                break

            header_bytes = bytes(self.buffer[:header_end])
            content_length = tcp_content_length(header_bytes)
            message_end = header_end + len(separator) + content_length
            if len(self.buffer) < message_end:
                break

            messages.append(bytes(self.buffer[:message_end]))
            del self.buffer[:message_end]
        return messages


class SipServerProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        local_ip: str,
        local_port: int,
        media: MediaServer,
        logger: SbcLogger,
        default_payload: int,
        auth_realm: str,
        users: Dict[str, str],
        bridge_rooms: Tuple[str, ...],
        b2bua_routes: Dict[str, str],
        route_policies: Tuple[Dict[str, Any], ...],
        b2bua_ladder_logs: bool,
        trunk_groups: Tuple[Dict[str, Any], ...] = (),
        hunt_groups: Tuple[Dict[str, Any], ...] = (),
        number_normalization: Tuple[Dict[str, Any], ...] = (),
        header_normalization: Optional[Dict[str, Any]] = None,
        transport_policies: Tuple[Dict[str, Any], ...] = (),
        call_admission: Optional[Dict[str, Any]] = None,
        media_backend: str = "internal",
        rtpengine_client: Optional[RtpengineClient] = None,
        reject_unknown_routes: bool = False,
        sip_transport: str = "udp",
        sip_advertised_ip: str = "",
        b2bua_advertised_ip: str = "",
        rtpengine_directions: Tuple[str, ...] = (),
        rtpengine_interfaces: Tuple[str, ...] = (),
        rtpengine_max_sessions: int = -1,
        rtpengine_offer_transport_protocol: str = "",
        rtpengine_answer_transport_protocol: str = "",
        rtpengine_sdes: Tuple[str, ...] = (),
        rtpengine_dtls: str = "",
        ai_voice_gateway: Optional[Dict[str, Any]] = None,
        ha: Optional[Dict[str, Any]] = None,
        tls_client_context: Optional[ssl.SSLContext] = None,
        tls_port: int = 5061,
    ):
        self.local_ip = local_ip
        self.sip_advertised_ip = sip_advertised_ip or local_ip
        self.b2bua_advertised_ip = b2bua_advertised_ip or self.sip_advertised_ip
        self.local_port = local_port
        self.sip_transport = sip_transport
        self.media = media
        self.logger = logger
        self.default_payload = default_payload
        self.auth_realm = auth_realm
        self.users = users
        self.bridge_rooms = set(bridge_rooms)
        self.b2bua_routes = b2bua_routes
        self.b2bua_ladder_logs = b2bua_ladder_logs
        self.media_backend = media_backend
        self.rtpengine_client = rtpengine_client
        self.rtpengine_directions = rtpengine_directions
        self.rtpengine_interfaces = frozenset(rtpengine_interfaces)
        self.reject_unknown_routes = reject_unknown_routes
        self.nonces: Dict[str, float] = {}
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.registrations: Dict[str, Registration] = {}
        self.routing_engine = RoutingEngine(
            route_policies,
            b2bua_routes,
            trunk_groups,
            hunt_groups,
            number_normalization,
            header_normalization,
            transport_policies,
            call_admission,
        )
        self.dialogs = DialogManager()
        self.transactions = TransactionManager(self._send_packet)
        self.pending_outbound_responses: Dict[str, asyncio.Queue] = {}
        self.pending_options_probes: Dict[str, asyncio.Future] = {}
        self.b2bua_calls_by_inbound: Dict[str, B2BUACall] = {}
        self.b2bua_calls_by_outbound: Dict[str, B2BUACall] = {}
        self.stream_connections: Dict[Tuple[str, str, int], SipTcpConnectionProtocol] = {}
        self.stream_connects = 0
        self.stream_reuses = 0
        self.stream_failures = 0
        self.rtpengine_max_sessions = rtpengine_max_sessions
        self.rtpengine_offer_transport_protocol = rtpengine_offer_transport_protocol
        self.rtpengine_answer_transport_protocol = rtpengine_answer_transport_protocol
        self.rtpengine_sdes = rtpengine_sdes
        self.rtpengine_dtls = rtpengine_dtls
        self.ai_voice_config = AiVoiceConfig.from_dict(ai_voice_gateway)
        self.ai_voice_gateway = AiVoiceGateway(self.ai_voice_config) if self.ai_voice_config.enabled else None
        self.ai_voice_calls_by_inbound: Dict[str, AIVoiceCall] = {}
        self.ai_voice_calls_total = 0
        self.ai_voice_turns_total = 0
        self.ai_voice_turn_failures_total = 0
        self.ai_stt_audio_decodes_total = 0
        self.ai_rasa_requests_total = 0
        self.ai_rasa_failures_total = 0
        self.ai_tts_outputs_total = 0
        self.ai_tts_rtp_prompts_total = 0
        self.ai_bot_actions_total = 0
        self.rtpengine_control_requests_total = 0
        self.rtpengine_control_failures_total = 0
        self.sip_requests_total: Dict[Tuple[str, str, str, str], int] = {}
        self.sip_responses_total: Dict[Tuple[str, str, str, str], int] = {}
        self.b2bua_calls_total = 0
        self.b2bua_calls_answered_total = 0
        self.b2bua_calls_completed_total = 0
        self.b2bua_calls_failed_total = 0
        self.media_negotiations_total: Dict[Tuple[str, str, str, str, str, str], int] = {}
        self.transcoding_sessions_total: Dict[Tuple[str, str, str, str, str], int] = {}
        self.registrations_total = 0
        self.ha_config = dict(ha or {})
        self.node_id = ha_node_id(self.ha_config) if ha_enabled(self.ha_config) else "standalone"
        self.cluster_id = str(self.ha_config.get("cluster_id") or "playsbc-lab")
        self.ha_nodes = ha_nodes(self.ha_config)
        self.ha_load_balancing_policy = ha_load_balancing_policy(self.ha_config)
        self.ha_node_draining = ha_node_draining(self.ha_config)
        self.ha_dialog_restores = 0
        shared_path = ha_shared_state_path(self.ha_config)
        self.shared_state = SharedStateStore(shared_path, self.node_id, logger) if ha_enabled(self.ha_config) else None
        self.background_tasks: List[asyncio.Task] = []
        if self.shared_state:
            self.registrations.update(self.shared_state.load_registrations())
            self.logger.platform(
                "HA NODE STARTED",
                (
                    f"cluster={self.cluster_id} node={self.node_id} "
                    f"shared_state={shared_path} restored_registrations={len(self.registrations)}"
                ),
            )
            self.logger.platform(
                "HA LOAD BALANCING MODEL",
                (
                    f"cluster={self.cluster_id} node={self.node_id} "
                    f"policy={self.ha_load_balancing_policy} nodes={len(self.ha_nodes)} "
                    f"draining={str(self.ha_node_draining).lower()} "
                    f"rtpengine_session_migration={ha_rtpengine_session_migration(self.ha_config)}"
                ),
            )
            if self.ha_node_draining:
                self.logger.platform("HA NODE DRAINING", f"node={self.node_id} action=reject_new_invites")
        self.tls_client_context = tls_client_context
        self.tls_port = tls_port

    def observe_sip_request(self, method: str, transport: str, direction: str, realm: str) -> None:
        key = (method.upper() or "UNKNOWN", normalize_sip_transport(transport), direction, realm)
        self.sip_requests_total[key] = self.sip_requests_total.get(key, 0) + 1

    def observe_sip_response(self, status: int, transport: str, direction: str, realm: str) -> None:
        status_text = str(status or 0)
        status_class = f"{status_text[:1]}xx" if status_text and status_text[0].isdigit() else "unknown"
        key = (status_text, status_class, normalize_sip_transport(transport), direction, realm)
        self.sip_responses_total[key] = self.sip_responses_total.get(key, 0) + 1

    def b2bua_metric_labels(self) -> Dict[str, str]:
        return {
            "backend": self.media_backend,
            "from_realm": self.rtpengine_directions[0] if len(self.rtpengine_directions) == 2 else "core",
            "to_realm": self.rtpengine_directions[1] if len(self.rtpengine_directions) == 2 else "peer",
        }

    def observe_media_negotiation(self, backend: str, inbound_payload: int, outbound_payload: int) -> None:
        from_realm = self.rtpengine_directions[0] if len(self.rtpengine_directions) == 2 else "core"
        to_realm = self.rtpengine_directions[1] if len(self.rtpengine_directions) == 2 else "peer"
        inbound_codec = CODEC_NAMES.get(inbound_payload, str(inbound_payload))
        outbound_codec = CODEC_NAMES.get(outbound_payload, str(outbound_payload))
        transcoding = "true" if inbound_payload != outbound_payload else "false"
        negotiation_key = (backend, from_realm, to_realm, inbound_codec, outbound_codec, transcoding)
        self.media_negotiations_total[negotiation_key] = self.media_negotiations_total.get(negotiation_key, 0) + 1
        if inbound_payload != outbound_payload:
            transcoding_key = (backend, from_realm, to_realm, inbound_codec, outbound_codec)
            self.transcoding_sessions_total[transcoding_key] = self.transcoding_sessions_total.get(transcoding_key, 0) + 1

    def prometheus_samples(self) -> List[Tuple[str, int | float, Dict[str, str]]]:
        base_labels = {"cluster": self.cluster_id, "node": self.node_id}
        samples: List[Tuple[str, int | float, Dict[str, str]]] = []

        legacy_metrics = self.routing_engine.metrics()
        legacy_metrics.update(
            {
                "playsbc_stream_connects_total": self.stream_connects,
                "playsbc_stream_reuses_total": self.stream_reuses,
                "playsbc_stream_failures_total": self.stream_failures,
                "playsbc_ha_enabled": int(self.shared_state is not None),
                "playsbc_ha_configured_nodes": len(self.ha_nodes),
                "playsbc_ha_node_draining": int(self.ha_node_draining),
                "playsbc_ha_dialog_restores_total": self.ha_dialog_restores,
            }
        )
        if self.shared_state:
            legacy_metrics.update(self.shared_state.counts())
        for name, value in sorted(legacy_metrics.items()):
            samples.append((name, value, {}))

        configured_realms = {"core", "peer"}
        if self.ai_voice_config.enabled:
            configured_realms.add("ai")
        configured_realms.update(trunk.realm for trunk in self.routing_engine.trunks.values() if trunk.realm)
        configured_realms.update(direction for direction in self.rtpengine_directions if direction)
        for realm in sorted(configured_realms):
            samples.append(("playsbc_realm_info", 1, {**base_labels, "realm": realm}))

        samples.extend(
            [
                ("playsbc_active_calls", self.routing_engine.active_calls, base_labels),
                ("playsbc_admission_rejections_total", self.routing_engine.rejected_calls, base_labels),
                ("playsbc_registrations_total", self.registrations_total, base_labels),
                ("playsbc_stream_connects_total", self.stream_connects, {**base_labels, "transport": "stream"}),
                ("playsbc_stream_reuses_total", self.stream_reuses, {**base_labels, "transport": "stream"}),
                ("playsbc_stream_failures_total", self.stream_failures, {**base_labels, "transport": "stream"}),
                ("playsbc_ha_enabled", int(self.shared_state is not None), base_labels),
                ("playsbc_ha_configured_nodes", len(self.ha_nodes), base_labels),
                ("playsbc_ha_node_draining", int(self.ha_node_draining), base_labels),
                ("playsbc_ha_dialog_restores_total", self.ha_dialog_restores, base_labels),
            ]
        )
        for (method, transport, direction, realm), value in sorted(self.sip_requests_total.items()):
            samples.append(
                (
                    "playsbc_sip_requests_total",
                    value,
                    {
                        **base_labels,
                        "method": method,
                        "transport": transport,
                        "direction": direction,
                        "realm": realm,
                    },
                )
            )
        for (status, status_class, transport, direction, realm), value in sorted(self.sip_responses_total.items()):
            samples.append(
                (
                    "playsbc_sip_responses_total",
                    value,
                    {
                        **base_labels,
                        "status": status,
                        "status_class": status_class,
                        "transport": transport,
                        "direction": direction,
                        "realm": realm,
                    },
                )
            )
        b2bua_labels = {**base_labels, **self.b2bua_metric_labels()}
        samples.extend(
            [
                ("playsbc_b2bua_calls_total", self.b2bua_calls_total, b2bua_labels),
                ("playsbc_b2bua_calls_answered_total", self.b2bua_calls_answered_total, b2bua_labels),
                ("playsbc_b2bua_calls_completed_total", self.b2bua_calls_completed_total, b2bua_labels),
                ("playsbc_b2bua_calls_failed_total", self.b2bua_calls_failed_total, b2bua_labels),
            ]
        )
        for (backend, from_realm, to_realm, inbound_codec, outbound_codec, transcoding), value in sorted(
            self.media_negotiations_total.items()
        ):
            samples.append(
                (
                    "playsbc_media_negotiations_total",
                    value,
                    {
                        **base_labels,
                        "backend": backend,
                        "from_realm": from_realm,
                        "to_realm": to_realm,
                        "inbound_codec": inbound_codec,
                        "outbound_codec": outbound_codec,
                        "transcoding": transcoding,
                    },
                )
            )
        for (backend, from_realm, to_realm, inbound_codec, outbound_codec), value in sorted(
            self.transcoding_sessions_total.items()
        ):
            samples.append(
                (
                    "playsbc_transcoding_sessions_total",
                    value,
                    {
                        **base_labels,
                        "backend": backend,
                        "from_realm": from_realm,
                        "to_realm": to_realm,
                        "inbound_codec": inbound_codec,
                        "outbound_codec": outbound_codec,
                    },
                )
            )
        if self.shared_state:
            for name, value in sorted(self.shared_state.counts().items()):
                samples.append((name, value, base_labels))

        for trunk_name, trunk in sorted(self.routing_engine.trunks.items()):
            labels = {**base_labels, "trunk": trunk_name, "realm": trunk.realm or "peer"}
            samples.extend(
                [
                    ("playsbc_trunk_healthy", int(trunk.healthy), labels),
                    ("playsbc_trunk_active_calls", trunk.active_calls, labels),
                    ("playsbc_trunk_attempts_total", trunk.attempts, labels),
                    ("playsbc_trunk_successes_total", trunk.successes, labels),
                    ("playsbc_trunk_failures_total", trunk.failures, labels),
                    ("playsbc_trunk_options_probe_successes_total", trunk.options_probe_successes, labels),
                    ("playsbc_trunk_options_probe_failures_total", trunk.options_probe_failures, labels),
                    (
                        "playsbc_trunk_options_probe_consecutive_failures",
                        trunk.options_probe_consecutive_failures,
                        labels,
                    ),
                ]
            )

        ai_labels = {
            **base_labels,
            "realm": "ai",
            "bot": self.ai_voice_config.bot_name,
            "provider": self.ai_voice_config.provider,
            "stt": self.ai_voice_config.stt_provider,
            "tts": self.ai_voice_config.tts_provider,
        }
        samples.extend(
            [
                ("playsbc_ai_voice_calls_active", len(self.ai_voice_calls_by_inbound), ai_labels),
                ("playsbc_ai_voice_calls_total", self.ai_voice_calls_total, ai_labels),
                ("playsbc_ai_voice_turns_total", self.ai_voice_turns_total, ai_labels),
                ("playsbc_ai_voice_turn_failures_total", self.ai_voice_turn_failures_total, ai_labels),
                ("playsbc_ai_stt_audio_decodes_total", self.ai_stt_audio_decodes_total, ai_labels),
                ("playsbc_ai_rasa_requests_total", self.ai_rasa_requests_total, ai_labels),
                ("playsbc_ai_rasa_failures_total", self.ai_rasa_failures_total, ai_labels),
                ("playsbc_ai_tts_outputs_total", self.ai_tts_outputs_total, ai_labels),
                ("playsbc_ai_tts_rtp_prompts_total", self.ai_tts_rtp_prompts_total, ai_labels),
                ("playsbc_ai_bot_actions_total", self.ai_bot_actions_total, ai_labels),
            ]
        )

        rtpengine_active = sum(
            1 for call in self.b2bua_calls_by_inbound.values() if call.media_backend == "rtpengine"
        )
        rtpengine_active += sum(
            1 for call in self.ai_voice_calls_by_inbound.values() if call.media_backend == "rtpengine"
        )
        rtpengine_url = ""
        if self.rtpengine_client:
            rtpengine_url = f"udp://{self.rtpengine_client.host}:{self.rtpengine_client.port}"
        from_realm = self.rtpengine_directions[0] if len(self.rtpengine_directions) == 2 else "core"
        to_realm = self.rtpengine_directions[1] if len(self.rtpengine_directions) == 2 else "peer"
        rtpengine_labels = {
            **base_labels,
            "from_realm": from_realm,
            "to_realm": to_realm,
            "backend": self.media_backend,
            "url": rtpengine_url,
        }
        samples.extend(
            [
                ("playsbc_rtpengine_control_requests_total", self.rtpengine_control_requests_total, rtpengine_labels),
                ("playsbc_rtpengine_control_failures_total", self.rtpengine_control_failures_total, rtpengine_labels),
                ("playsbc_rtpengine_media_sessions_active", rtpengine_active, rtpengine_labels),
            ]
        )
        return samples

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        logging.info("SIP listening on udp:%s:%s", self.local_ip, self.local_port)
        self.logger.platform("SIP SERVER STARTED", f"transport=udp local={self.local_ip}:{self.local_port}")
        self.logger.udp("UDP LISTENING", f"protocol=sip local={self.local_ip}:{self.local_port}")

    def tcp_server_started(self) -> None:
        logging.info("SIP listening on tcp:%s:%s", self.local_ip, self.local_port)
        self.logger.platform("SIP SERVER STARTED", f"transport=tcp local={self.local_ip}:{self.local_port}")
        self.logger.tcp("TCP LISTENING", f"protocol=sip local={self.local_ip}:{self.local_port}")

    def tls_server_started(self) -> None:
        logging.info("SIP listening on tls:%s:%s", self.local_ip, self.tls_port)
        self.logger.platform("SIP SERVER STARTED", f"transport=tls local={self.local_ip}:{self.tls_port}")
        self.logger.tls("TLS LISTENING", f"protocol=sip local={self.local_ip}:{self.tls_port}")

    def start_background_tasks(self) -> None:
        probed_trunks = [
            trunk.name
            for trunk in self.routing_engine.trunks.values()
            if trunk.options_probe_enabled and trunk.enabled
        ]
        if probed_trunks:
            self.logger.platform(
                "TRUNK OPTIONS PROBING STARTED",
                f"node={self.node_id} trunks={','.join(sorted(probed_trunks))}",
            )
        for trunk_name in probed_trunks:
            self.background_tasks.append(asyncio.create_task(self._trunk_options_probe_loop(trunk_name)))

    async def _trunk_options_probe_loop(self, trunk_name: str) -> None:
        jitter = random.uniform(0, 0.050)
        await asyncio.sleep(jitter)
        while True:
            trunk = self.routing_engine.trunks.get(trunk_name)
            if not trunk:
                return
            try:
                await self.send_trunk_options_probe(trunk)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive logging for background probes.
                self.logger.networking(
                    "TRUNK OPTIONS PROBE ERROR",
                    f"trunk={trunk_name} error_type={type(exc).__name__} error={exc}",
                )
            await asyncio.sleep(trunk.options_probe_interval)

    async def send_trunk_options_probe(self, trunk: TrunkRuntime) -> bool:
        target = parse_sip_uri(format_route_target(trunk.uri, "options"))
        transport_name = target.transport
        call_id = make_call_id()
        headers = {
            "Via": self.make_via_header(transport_name),
            "From": f"PlaySBC Probe <sip:options@{self.b2bua_advertised_ip}:{self.local_port}>;tag={secrets.token_hex(6)}",
            "To": f"<{target.uri}>",
            "Call-ID": call_id,
            "CSeq": "1 OPTIONS",
            "Contact": f"<{self.local_contact_uri(transport_name)}>",
            "Max-Forwards": "70",
            "Accept": "application/sdp",
        }
        packet = build_sip_request("OPTIONS", target.uri, headers)
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self.pending_options_probes[call_id] = future
        self.logger.sip(
            "TRUNK OPTIONS PROBE TX",
            f"node={self.node_id} trunk={trunk.name} target={target.uri} timeout={trunk.options_probe_timeout:.3f}",
            call_id=call_id,
        )
        self._send_packet(packet, target.address, transport_name=transport_name)
        try:
            response = await asyncio.wait_for(future, timeout=trunk.options_probe_timeout)
            status = int(getattr(response, "status_code", 0))
            success = 200 <= status < 300
            state = self.routing_engine.record_probe_result(trunk.name, success, str(status))
            self.log_trunk_probe_result(trunk.name, success, f"status={status}", state, call_id)
            return success
        except asyncio.TimeoutError:
            state = self.routing_engine.record_probe_result(trunk.name, False, "timeout")
            self.log_trunk_probe_result(trunk.name, False, "status=timeout", state, call_id)
            return False
        finally:
            self.pending_options_probes.pop(call_id, None)

    def log_trunk_probe_result(
        self,
        trunk_name: str,
        success: bool,
        detail: str,
        trunk: Optional[TrunkRuntime],
        call_id: str,
    ) -> None:
        if not trunk:
            return
        status = "up" if trunk.healthy else "down"
        result = "success" if success else "failure"
        payload = (
            f"node={self.node_id} trunk={trunk_name} result={result} {detail} health={status} "
            f"successes={trunk.options_probe_successes} failures={trunk.options_probe_failures} "
            f"consecutive_successes={trunk.options_probe_consecutive_successes} "
            f"consecutive_failures={trunk.options_probe_consecutive_failures}"
        )
        self.logger.call("TRUNK OPTIONS PROBE", payload, call_id=call_id)

    def register_stream_connection(
        self,
        transport_name: str,
        peer: Tuple[str, int],
        connection: SipTcpConnectionProtocol,
    ) -> None:
        self.stream_connections[(transport_name, peer[0], peer[1])] = connection

    def unregister_stream_connection(
        self,
        transport_name: str,
        peer: Tuple[str, int],
        connection: SipTcpConnectionProtocol,
    ) -> None:
        key = (transport_name, peer[0], peer[1])
        if self.stream_connections.get(key) is connection:
            self.stream_connections.pop(key, None)

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        self.receive_sip_data(data, addr, transport_name="udp")

    def receive_sip_data(
        self,
        data: bytes,
        addr: Tuple[str, int],
        transport_name: str = "udp",
        connection: Optional[SipTcpConnectionProtocol] = None,
    ) -> None:
        try:
            text = data.decode("utf-8", errors="replace")
            message = parse_sip_message(text, addr, transport_name=transport_name, connection=connection)
        except Exception:
            logging.exception("Could not parse SIP message from %s:%s over %s", addr[0], addr[1], transport_name)
            self.logger.networking("SIP PARSE FAILED", f"transport={transport_name} source={addr[0]}:{addr[1]} bytes={len(data)}")
            return

        self.logger.write(transport_name, f"{transport_name.upper()} RX", f"protocol=sip source={addr[0]}:{addr[1]} bytes={len(data)}")
        if message.is_response:
            self.observe_sip_response(message.status_code or 0, transport_name, "rx", "peer")
            logging.info("SIP response %s from %s:%s", message.status_code, *addr)
            self.logger.sip(
                "SIP RX RESPONSE",
                f"transport={transport_name} status={message.status_code} reason={message.reason_phrase} source={addr[0]}:{addr[1]} cseq={message.header('cseq')}",
                call_id=message.header("call-id"),
            )
            self.handle_response(message)
            return

        logging.info("SIP %s from %s:%s", message.method, *addr)
        self.observe_sip_request(message.method, transport_name, "rx", "core")
        self.logger.sip(
            "SIP RX REQUEST",
            f"transport={transport_name} method={message.method} source={addr[0]}:{addr[1]} target={message.start_line} cseq={message.header('cseq')}",
            call_id=message.header("call-id"),
        )
        asyncio.create_task(self.handle_message(message))

    def handle_response(self, message: SipMessage) -> None:
        call_id = message.header("call-id")
        cseq_method = parse_cseq_method(message.header("cseq"))
        if cseq_method == "OPTIONS":
            future = self.pending_options_probes.get(call_id)
            if future and not future.done():
                future.set_result(message)
                return
        queue = self.pending_outbound_responses.get(call_id)
        if queue and cseq_method == "INVITE":
            queue.put_nowait(message)
            return

        b2bua_call = self.b2bua_calls_by_outbound.get(call_id)
        if b2bua_call:
            logging.info(
                "B2BUA outbound response %s for inbound call %s",
                message.status_code,
                b2bua_call.inbound_call_id,
            )
            if b2bua_call.outbound_cancel_sent and cseq_method == "CANCEL" and message.status_code >= 200:
                b2bua_call.flow_log.sip("SIPp B", "B2BUA", f"{message.status_code} {message.reason_phrase or 'OK'}", "CANCEL")
                return
            if b2bua_call.outbound_bye_sent and message.status_code >= 200:
                b2bua_call.flow_log.sip("SIPp B", "B2BUA", f"{message.status_code} {message.reason_phrase or 'OK'}")
                self.finalize_b2bua_call(b2bua_call, "normal")

    async def handle_message(self, message: SipMessage) -> None:
        method = message.method

        if message.transport == "udp" and method != "ACK":
            _, duplicate = self.transactions.receive_request(
                method,
                message.header("via"),
                message.header("cseq"),
                message.header("call-id"),
                message.source,
            )
            if duplicate:
                logging.info("Replayed cached response for retransmitted %s", method)
                return

        if method == "REGISTER":
            self.cleanup_registrations()
            user = extract_user(message.header("to")) or extract_user(message.header("from")) or "unknown"
            auth_result = self.authenticate_register(message, user)
            if auth_result == "challenge":
                self.send_response(
                    message,
                    401,
                    "Unauthorized",
                    extra_headers={"WWW-Authenticate": self.make_authenticate_header()},
                )
                logging.info("Challenged REGISTER for %s", user)
                return
            if auth_result == "forbidden":
                self.send_response(message, 403, "Forbidden")
                logging.info("Rejected REGISTER for unknown user %s", user)
                return

            expires = parse_register_expires(message.header("expires"), message.header("contact"))
            if expires <= 0:
                self.delete_registration_state(user)
                self.send_response(message, 200, "OK")
                logging.info("Unregistered %s", user)
                return

            contact_uri = extract_sip_uri(message.header("contact")) or f"sip:{user}@{message.source[0]}:{message.source[1]}"
            try:
                parse_sip_uri(contact_uri)
            except ValueError:
                self.send_response(message, 400, "Bad Contact")
                logging.info("Rejected REGISTER for %s with invalid contact %s", user, contact_uri)
                return

            registration = Registration(
                user=user,
                contact_uri=contact_uri,
                source=message.source,
                expires_at=time.time() + expires,
            )
            self.save_registration_state(registration)
            self.registrations_total += 1
            self.send_response(message, 200, "OK")
            logging.info("Registered %s -> %s expires=%s", user, contact_uri, expires)
            return

        if method == "OPTIONS":
            self.send_response(
                message,
                200,
                "OK",
                extra_headers={
                    "Allow": "REGISTER, OPTIONS, INVITE, ACK, BYE, CANCEL",
                    "Accept": "application/sdp",
                },
            )
            return

        if method == "INVITE":
            call_id = message.header("call-id", make_call_id())
            if self.ha_node_draining:
                self.logger.call(
                    "HA NODE DRAINING REJECT",
                    f"node={self.node_id} action=reject_new_invite reason=draining",
                    call_id=call_id,
                )
                self.send_response(message, 503, "Node Draining", to_header=message.header("to"))
                return
            try:
                dialog = self.dialogs.create_invite(
                    call_id,
                    message.header("from"),
                    message.header("via"),
                    message.header("cseq"),
                )
            except DialogError as exc:
                self.send_response(message, 491, "Request Pending")
                logging.info("Rejected INVITE for %s: %s", call_id, exc)
                return

            dialog.mark_ringing()
            self.save_dialog_state(dialog)
            to_header = dialog.to_header(message.header("to"))
            self.send_response(message, 100, "Trying", to_header=message.header("to"))

            remote_payloads = parse_sdp_payloads(message.body)
            dtmf_payload_type = parse_dtmf_payload_type(message.body)
            preferred_payload = choose_payload(remote_payloads, self.default_payload)
            target_user = extract_request_user(message.start_line) or "echo"
            self.cleanup_registrations()
            route = self.routing_engine.resolve(target_user, self.registrations)
            if route:
                routed_user = route.routed_user or target_user
                if routed_user != target_user:
                    self.logger.sip(
                        "NUMBER NORMALIZED",
                        f"original={target_user} normalized={routed_user} route_policy={route.policy_name}",
                        call_id=call_id,
                    )
                if not self.routing_engine.admit(route):
                    self.logger.call(
                        "CALL ADMISSION REJECTED",
                        f"target={routed_user} trunk={route.trunk_name or 'none'} active={self.routing_engine.active_calls}",
                        call_id=call_id,
                    )
                    self.send_response(message, 503, "Service Unavailable", to_header=to_header)
                    return
                if (
                    self.media_backend == "rtpengine"
                    and self.rtpengine_max_sessions >= 0
                    and sum(1 for call in self.b2bua_calls_by_inbound.values() if call.media_backend == "rtpengine")
                    >= self.rtpengine_max_sessions
                ):
                    self.routing_engine.release(route)
                    self.logger.media(
                        "RTPENGINE PORT POOL EXHAUSTED",
                        f"session_limit={self.rtpengine_max_sessions}",
                        call_id=call_id,
                    )
                    self.send_response(message, 503, "Media Capacity Exhausted", to_header=to_header)
                    return
                self.log_policy_metrics("CALL ADMITTED", route, call_id)
                if route.source == "ai-gateway":
                    await self.handle_ai_gateway_invite(
                        message=message,
                        dialog=dialog,
                        to_header=to_header,
                        inbound_call_id=call_id,
                        target_user=routed_user,
                        route=route,
                        preferred_payload=preferred_payload,
                        remote_payloads=remote_payloads,
                        dtmf_payload_type=dtmf_payload_type,
                    )
                    return
                if self.media_backend == "rtpengine":
                    await self.handle_b2bua_invite_rtpengine(
                        message=message,
                        dialog=dialog,
                        to_header=to_header,
                        inbound_call_id=call_id,
                        target_user=routed_user,
                        route=route,
                    )
                    if call_id not in self.b2bua_calls_by_inbound:
                        self.routing_engine.release(route)
                    return
                await self.handle_b2bua_invite(
                    message=message,
                    dialog=dialog,
                    to_header=to_header,
                    inbound_call_id=call_id,
                    target_user=routed_user,
                    route=route,
                    preferred_payload=preferred_payload,
                    remote_payloads=remote_payloads,
                    dtmf_payload_type=dtmf_payload_type,
                )
                if call_id not in self.b2bua_calls_by_inbound:
                    self.routing_engine.release(route)
                return

            if self.reject_unknown_routes:
                self.send_response(message, 404, "Not Found", to_header=to_header)
                logging.info("Rejected INVITE for unknown route target %s", target_user)
                return

            self.send_response(message, 180, "Ringing", to_header=to_header)
            bridge_id = target_user if target_user in self.bridge_rooms else ""
            rtp = await self.media.create_session(
                call_id,
                preferred_payload,
                remote_payloads,
                dtmf_payload_type,
                bridge_id=bridge_id,
            )
            rtp.log(
                "INVITE RECEIVED",
                (
                    f"source={message.source[0]}:{message.source[1]} "
                    f"from={message.header('from')} to={message.header('to')} "
                    f"target_user={target_user} media_mode={rtp.media_mode}"
                ),
            )
            rtp.log(
                "SDP OFFER",
                (
                    f"payloads={format_payloads(remote_payloads)} "
                    f"selected={CODEC_NAMES.get(preferred_payload, preferred_payload)} "
                    f"dtmf_payload={dtmf_payload_type if dtmf_payload_type is not None else 'none'}"
                ),
            )
            rtp.log("SIP RESPONSE", "100 Trying")
            rtp.log("SIP RESPONSE", "180 Ringing")
            sdp = make_sdp(self.sip_advertised_ip, rtp.local_port, preferred_payload)
            rtp.log(
                "SDP ANSWER",
                f"local_rtp={self.local_ip}:{rtp.local_port} payload={CODEC_NAMES.get(preferred_payload, preferred_payload)}",
            )

            self.send_response(
                message,
                200,
                "OK",
                body=sdp,
                to_header=to_header,
                extra_headers={
                    "Contact": f"<{self.inbound_contact_uri(target_user, message.transport)}>",
                    "Content-Type": "application/sdp",
                },
            )
            dialog.mark_answered()
            self.save_dialog_state(dialog)
            rtp.log(
                "DIALOG STATE",
                (
                    f"state={dialog.state.name} local_tag={dialog.local_tag} "
                    f"remote_tag={dialog.remote_tag or 'none'} invite_branch={dialog.invite_branch or 'none'} "
                    f"remote_cseq={dialog.remote_cseq}"
                ),
            )
            rtp.log("SIP RESPONSE", "200 OK")
            return

        if method == "ACK":
            call_id = message.header("call-id")
            session = self.media.get_session(call_id)
            b2bua_call = self.b2bua_calls_by_inbound.get(call_id)
            ai_call = self.ai_voice_calls_by_inbound.get(call_id)
            if session or b2bua_call or ai_call:
                try:
                    dialog = self.acknowledge_dialog(call_id, message.header("cseq"))
                except DialogError as exc:
                    logging.info("Ignored invalid ACK for %s: %s", call_id, exc)
                    return
                if message.transport == "udp":
                    self.transactions.acknowledge_invite(call_id, message.header("cseq"))
                if session:
                    session.mark_ack()
                    session.log("DIALOG STATE", f"state={dialog.state.name} acknowledged=true")
                if b2bua_call:
                    b2bua_call.flow_log.sip("SIPp A", "B2BUA", "ACK")
                    self.send_outbound_ack(b2bua_call)
                if ai_call:
                    ai_call.flow_log.flow("SIPp A", "PlaySBC", "ACK")
                    if not ai_call.task:
                        ai_call.task = asyncio.create_task(
                            self.run_ai_voice_turn(
                                ai_call=ai_call,
                                message=message,
                                selected_payload=ai_call.selected_payload,
                                dtmf_payload_type=ai_call.dtmf_payload_type,
                            )
                        )
            return

        if method == "CANCEL":
            call_id = message.header("call-id")
            b2bua_call = self.b2bua_calls_by_inbound.get(call_id)
            if not b2bua_call:
                self.send_response(message, 481, "Call/Transaction Does Not Exist")
                return
            b2bua_call.flow_log.sip("SIPp A", "B2BUA", "CANCEL")
            self.send_response(message, 200, "OK", to_header=message.header("to"))
            b2bua_call.flow_log.sip("B2BUA", "SIPp A", "200 OK", "CANCEL")
            self.send_outbound_cancel(b2bua_call)
            return

        if method == "BYE":
            call_id = message.header("call-id")
            session = self.media.get_session(call_id)
            try:
                dialog = self.terminate_dialog(
                    call_id,
                    message.header("from"),
                    message.header("to"),
                    message.header("via"),
                    message.header("cseq"),
                )
            except DialogError as exc:
                self.send_response(message, 481, "Call/Transaction Does Not Exist")
                logging.info("Rejected BYE for %s: %s", call_id, exc)
                return
            if session:
                session.log("BYE RECEIVED", f"source={message.source[0]}:{message.source[1]}")
            b2bua_call = self.b2bua_calls_by_inbound.get(call_id)
            if b2bua_call:
                b2bua_call.flow_log.sip("SIPp A", "B2BUA", "BYE")
            ai_call = self.ai_voice_calls_by_inbound.get(call_id)
            if ai_call:
                ai_call.flow_log.flow("SIPp A", "PlaySBC", "BYE")
            self.send_response(message, 200, "OK")
            if b2bua_call:
                b2bua_call.flow_log.sip("B2BUA", "SIPp A", "200 OK", "BYE")
                self.send_outbound_bye(b2bua_call)
                self.media.close_session(b2bua_call.outbound_call_id)
                self.schedule_b2bua_finalizer(b2bua_call)
            if ai_call:
                ai_call.flow_log.flow("PlaySBC", "SIPp A", "200 OK")
                self.finalize_ai_voice_call(ai_call, "normal")
            if session:
                session.log(
                    "DIALOG STATE",
                    (
                        f"state={dialog.state.name} remote_cseq={dialog.remote_cseq} "
                        f"branches={','.join(sorted(dialog.branch_ids)) or 'none'}"
                    ),
                )
                session.log("SIP RESPONSE", "200 OK for BYE")
            self.media.close_session(call_id)
            return

        self.send_response(message, 405, "Method Not Allowed", extra_headers={"Allow": "REGISTER, OPTIONS, INVITE, ACK, BYE, CANCEL"})

    async def handle_ai_gateway_invite(
        self,
        message: SipMessage,
        dialog: Any,
        to_header: str,
        inbound_call_id: str,
        target_user: str,
        route: RouteResult,
        preferred_payload: int,
        remote_payloads: Tuple[int, ...],
        dtmf_payload_type: Optional[int],
    ) -> None:
        stt_node = ai_stt_ladder_node(self.ai_voice_config.stt_provider)
        tts_node = ai_tts_ladder_node(self.ai_voice_config.tts_provider)
        bot_node = self.ai_voice_config.agent_label or "Rasa Bot"
        flow_log = AIVoiceFlowLog(
            self.logger,
            inbound_call_id,
            participants=(
                ("SIPp A", "RTPengine", "PlaySBC", stt_node, bot_node, tts_node)
                if self.media_backend == "rtpengine"
                else ("SIPp A", "PlaySBC", stt_node, bot_node, tts_node)
            ),
        )
        ai_call = AIVoiceCall(
            inbound_call_id,
            target_user,
            route,
            flow_log,
            selected_payload=preferred_payload,
            dtmf_payload_type=dtmf_payload_type,
            media_backend=self.media_backend,
        )
        self.ai_voice_calls_by_inbound[inbound_call_id] = ai_call
        self.ai_voice_calls_total += 1
        flow_log.flow("SIPp A", "PlaySBC", "INVITE")
        flow_log.flow("PlaySBC", "SIPp A", "100 Trying")
        if not self.ai_voice_gateway:
            self.logger.ai(
                "AI VOICE GATEWAY DISABLED",
                f"target_user={target_user} route_policy={route.policy_name}",
                call_id=inbound_call_id,
            )
            flow_log.flow("PlaySBC", "SIPp A", "503 Service Unavailable")
            self.send_response(message, 503, "AI Gateway Disabled", to_header=to_header)
            self.finalize_ai_voice_call(ai_call, "disabled")
            return

        if self.media_backend == "rtpengine":
            await self.handle_ai_gateway_invite_rtpengine(
                message=message,
                dialog=dialog,
                to_header=to_header,
                ai_call=ai_call,
                remote_payloads=remote_payloads,
                preferred_payload=preferred_payload,
                dtmf_payload_type=dtmf_payload_type,
            )
            return

        self.send_response(message, 180, "Ringing", to_header=to_header)
        flow_log.flow("PlaySBC", "SIPp A", "180 Ringing")
        rtp = await self.media.create_session(
            inbound_call_id,
            preferred_payload,
            remote_payloads,
            dtmf_payload_type,
            media_mode="ai-gateway",
            leg_label="ai-gateway",
        )
        rtp.log(
            "AI VOICE INVITE",
            (
                f"source={message.source[0]}:{message.source[1]} from={message.header('from')} "
                f"to={message.header('to')} target_user={target_user} bot={route.group_name or self.ai_voice_config.bot_name}"
            ),
        )
        rtp.log(
            "SDP OFFER",
            (
                f"payloads={format_payloads(remote_payloads)} "
                f"selected={CODEC_NAMES.get(preferred_payload, preferred_payload)} "
                f"dtmf_payload={dtmf_payload_type if dtmf_payload_type is not None else 'none'}"
            ),
        )
        answer_sdp = make_sdp(self.sip_advertised_ip, rtp.local_port, preferred_payload, dtmf_payload_type=dtmf_payload_type)
        self.send_response(
            message,
            200,
            "OK",
            body=answer_sdp,
            to_header=to_header,
            extra_headers={
                "Contact": f"<{self.inbound_contact_uri(target_user, message.transport)}>",
                "Content-Type": "application/sdp",
            },
        )
        dialog.mark_answered()
        self.save_dialog_state(dialog)
        flow_log.flow("PlaySBC", "SIPp A", "200 OK")
        rtp.log(
            "DIALOG STATE",
            (
                f"state={dialog.state.name} local_tag={dialog.local_tag} "
                f"remote_tag={dialog.remote_tag or 'none'} invite_branch={dialog.invite_branch or 'none'} "
                f"remote_cseq={dialog.remote_cseq}"
            ),
        )
        self.logger.ai(
            "AI VOICE CALL START",
            (
                f"provider={self.ai_voice_config.provider} bot={route.group_name or self.ai_voice_config.bot_name} "
                f"target_user={target_user} input_mode={self.ai_voice_config.input_mode} "
                f"stt={self.ai_voice_config.stt_provider} tts={self.ai_voice_config.tts_provider} "
                f"response_mode={self.ai_voice_config.response_mode} rtp_prompt_generated=false "
                f"rasa_webhook={self.ai_voice_config.rasa_webhook_url}"
            ),
            call_id=inbound_call_id,
        )

    async def handle_ai_gateway_invite_rtpengine(
        self,
        message: SipMessage,
        dialog: Any,
        to_header: str,
        ai_call: AIVoiceCall,
        remote_payloads: Tuple[int, ...],
        preferred_payload: int,
        dtmf_payload_type: Optional[int],
    ) -> None:
        if not self.rtpengine_client:
            ai_call.flow_log.flow("PlaySBC", "SIPp A", "500 RTPengine Not Configured")
            self.send_response(message, 500, "RTPengine Not Configured", to_header=to_header)
            self.finalize_ai_voice_call(ai_call, "rtpengine-not-configured")
            return

        self.send_response(message, 180, "Ringing", to_header=to_header)
        ai_call.flow_log.flow("PlaySBC", "SIPp A", "180 Ringing")
        unavailable_interfaces = [
            direction
            for direction in self.rtpengine_directions
            if self.rtpengine_interfaces and direction not in self.rtpengine_interfaces
        ]
        if unavailable_interfaces:
            detail = (
                f"requested={','.join(self.rtpengine_directions)} "
                f"available={','.join(sorted(self.rtpengine_interfaces))} "
                f"unavailable={','.join(unavailable_interfaces)}"
            )
            self.logger.media("AI RTPENGINE INTERFACE UNAVAILABLE", detail, call_id=ai_call.call_id)
            ai_call.flow_log.flow("PlaySBC", "SIPp A", "488 Not Acceptable Here")
            self.send_response(message, 488, "Not Acceptable Here", to_header=to_header)
            self.finalize_ai_voice_call(ai_call, "rtpengine-interface-unavailable")
            return

        from_tag = extract_header_tag(message.header("from")) or dialog.remote_tag or secrets.token_hex(6)
        to_tag = dialog.local_tag or secrets.token_hex(6)
        ai_call.rtpengine_call_id = ai_call.call_id
        ai_call.rtpengine_from_tag = from_tag
        ai_call.rtpengine_to_tag = to_tag
        codec_policy = rtpengine_codec_policy(remote_payloads, preferred_payload)
        self.logger.media(
            "AI RTPENGINE MEDIA",
            (
                f"backend=rtpengine offered={format_payloads(remote_payloads)} "
                f"selected={CODEC_NAMES.get(preferred_payload, preferred_payload)} "
                f"dtmf_payload={dtmf_payload_type if dtmf_payload_type is not None else 'none'} "
                f"direction={','.join(self.rtpengine_directions) or 'default'}"
            ),
            call_id=ai_call.call_id,
        )
        try:
            ai_call.flow_log.flow("PlaySBC", "RTPengine", "OFFER")
            self.rtpengine_control_requests_total += 1
            offer_response = await retry_rtpengine_control(
                "AI OFFER",
                lambda: self.rtpengine_client.offer(
                    call_id=ai_call.call_id,
                    from_tag=from_tag,
                    sdp=message.body,
                    codec=codec_policy,
                    direction=self.rtpengine_directions,
                    transport_protocol=self.rtpengine_offer_transport_protocol,
                    sdes=self.rtpengine_sdes,
                    dtls=self.rtpengine_dtls,
                ),
                B2BUAFlowLog(self.media.log_dir, ai_call.call_id, ai_call.target_user, ai_call.route_result, enabled=False, logger=self.logger),
            )
            ai_call.flow_log.flow("RTPengine", "PlaySBC", f"{offer_response.get('result', 'ok')} OFFER")
            ai_sink_port = self.media.port_max + 2 if self.media.port_max <= 65532 else self.media.port_min
            ai_side_sdp = make_sdp(
                self.b2bua_advertised_ip,
                ai_sink_port,
                preferred_payload,
                dtmf_payload_type=dtmf_payload_type,
            )
            self.logger.media(
                "AI RTPENGINE SINK SDP",
                f"local={self.b2bua_advertised_ip}:{ai_sink_port} purpose=rtpengine_anchor_without_internal_rtp_listener",
                call_id=ai_call.call_id,
            )
            ai_call.flow_log.flow("PlaySBC", "RTPengine", "ANSWER")
            self.rtpengine_control_requests_total += 1
            answer_response = await retry_rtpengine_control(
                "AI ANSWER",
                lambda: self.rtpengine_client.answer(
                    call_id=ai_call.call_id,
                    from_tag=from_tag,
                    to_tag=to_tag,
                    sdp=ai_side_sdp,
                    codec=codec_policy,
                    transport_protocol=self.rtpengine_answer_transport_protocol,
                    sdes=self.rtpengine_sdes,
                    dtls=self.rtpengine_dtls,
                ),
                B2BUAFlowLog(self.media.log_dir, ai_call.call_id, ai_call.target_user, ai_call.route_result, enabled=False, logger=self.logger),
            )
            answer_sdp = str(answer_response.get("sdp") or "")
            if not answer_sdp:
                raise RtpengineError("RTPengine answer response did not include SDP")
            ai_call.flow_log.flow("RTPengine", "PlaySBC", f"{answer_response.get('result', 'ok')} ANSWER")
            self.logger.media(
                "AI RTPENGINE ANSWER",
                (
                    f"status={answer_response.get('result', 'unknown')} "
                    f"rewritten_sdp_bytes={len(answer_sdp.encode('utf-8'))}"
                ),
                call_id=ai_call.call_id,
            )
            await self.query_ai_rtpengine_call(ai_call, attempts=2)
        except (asyncio.TimeoutError, OSError, RtpengineError) as exc:
            self.rtpengine_control_failures_total += 1
            self.logger.media("AI RTPENGINE FAILED", str(exc), call_id=ai_call.call_id)
            ai_call.flow_log.flow("RTPengine", "PlaySBC", "failed")
            ai_call.flow_log.flow("PlaySBC", "SIPp A", "488 Not Acceptable Here")
            self.send_response(message, 488, "Not Acceptable Here", to_header=to_header)
            self.finalize_ai_voice_call(ai_call, "rtpengine-failed")
            return

        self.send_response(
            message,
            200,
            "OK",
            body=answer_sdp,
            to_header=to_header,
            extra_headers={
                "Contact": f"<{self.inbound_contact_uri(ai_call.target_user, message.transport)}>",
                "Content-Type": "application/sdp",
            },
        )
        dialog.mark_answered()
        self.save_dialog_state(dialog)
        ai_call.flow_log.flow("PlaySBC", "SIPp A", "200 OK")
        self.logger.ai(
            "AI VOICE CALL START",
            (
                f"provider={self.ai_voice_config.provider} bot={ai_call.route_result.group_name or self.ai_voice_config.bot_name} "
                f"target_user={ai_call.target_user} input_mode={self.ai_voice_config.input_mode} "
                f"stt={self.ai_voice_config.stt_provider} tts={self.ai_voice_config.tts_provider} "
                f"response_mode={self.ai_voice_config.response_mode} media_backend=rtpengine "
                f"rasa_webhook={self.ai_voice_config.rasa_webhook_url}"
            ),
            call_id=ai_call.call_id,
        )

    async def handle_b2bua_invite(
        self,
        message: SipMessage,
        dialog: Any,
        to_header: str,
        inbound_call_id: str,
        target_user: str,
        route: RouteResult,
        preferred_payload: int,
        remote_payloads: Tuple[int, ...],
        dtmf_payload_type: Optional[int],
    ) -> None:
        target = route.target
        flow_log = B2BUAFlowLog(
            self.media.log_dir,
            inbound_call_id,
            target_user,
            route,
            enabled=self.b2bua_ladder_logs,
            logger=self.logger,
        )
        flow_log.sip("SIPp A", "B2BUA", "INVITE", f"call_id={inbound_call_id} target_user={target_user}")
        flow_log.sip("B2BUA", "SIPp A", "100 Trying")

        inbound_payload = choose_payload(remote_payloads, PCMU)
        outbound_payload = self.default_payload if self.default_payload in SUPPORTED_CODECS else preferred_payload
        outbound_offer_payloads = b2bua_outbound_offer_payloads(remote_payloads, inbound_payload, outbound_payload)
        inbound_rtp = await self.media.create_session(
            inbound_call_id,
            inbound_payload,
            remote_payloads,
            dtmf_payload_type,
            media_mode="b2bua",
            leg_label="inbound",
        )
        inbound_remote = parse_sdp_remote_addr(message.body, message.source[0])
        if inbound_remote:
            inbound_rtp.remote_addr = inbound_remote
            inbound_rtp.remote_rtcp_addr = parse_sdp_remote_rtcp_addr(message.body, inbound_remote)
            inbound_rtp.log("RTP REMOTE", f"remote={inbound_remote[0]}:{inbound_remote[1]} source=sdp")

        outbound_call_id = make_call_id()
        outbound_rtp = await self.media.create_session(
            outbound_call_id,
            outbound_payload,
            outbound_offer_payloads,
            dtmf_payload_type,
            media_mode="b2bua",
            leg_label="outbound",
        )

        outbound_from = f"Mini B2BUA <sip:b2bua@{self.b2bua_advertised_ip}:{self.local_port}>;tag={secrets.token_hex(6)}"
        b2bua_call = B2BUACall(
            inbound_call_id=inbound_call_id,
            outbound_call_id=outbound_call_id,
            outbound_target=target,
            outbound_from_header=outbound_from,
            target_user=target_user,
            route_policy=route.policy_name,
            route_source=route.source,
            flow_log=flow_log,
            route_result=route,
        )
        self.b2bua_calls_total += 1
        self.b2bua_calls_by_inbound[inbound_call_id] = b2bua_call
        self.b2bua_calls_by_outbound[outbound_call_id] = b2bua_call

        response_queue: asyncio.Queue = asyncio.Queue()
        self.pending_outbound_responses[outbound_call_id] = response_queue
        inbound_rtp.log(
            "B2BUA ROUTE",
            (
                f"target_user={target_user} route={target.uri} outbound_call_id={outbound_call_id} "
                f"route_source={route.source} route_policy={route.policy_name}"
            ),
        )

        outbound_body = make_sdp(
            self.b2bua_advertised_ip,
            outbound_rtp.local_port,
            outbound_payload,
            dtmf_payload_type=dtmf_payload_type,
            payloads=outbound_offer_payloads,
        )
        self.send_outbound_invite(b2bua_call, outbound_body)

        try:
            final_response = await self.wait_for_outbound_invite(
                response_queue,
                message,
                to_header,
                inbound_rtp,
                b2bua_call,
            )
        except asyncio.TimeoutError:
            inbound_rtp.log("B2BUA FAILURE", f"route={target.uri} reason=outbound_invite_timeout")
            flow_log.write("B2BUA FAILURE", f"route={target.uri} reason=outbound_invite_timeout")
            self.send_response(message, 480, "Temporarily Unavailable", to_header=to_header)
            self.cleanup_b2bua_call(b2bua_call)
            return
        finally:
            self.pending_outbound_responses.pop(outbound_call_id, None)

        status = final_response.status_code
        reason = final_response.reason_phrase or "Upstream Response"
        if status < 200 or status >= 300:
            self.routing_engine.record_outcome(route, False)
            self.log_policy_metrics("TRUNK FAILURE", route, inbound_call_id)
            b2bua_call.outbound_to_header = final_response.header("to")
            b2bua_call.outbound_contact_uri = extract_sip_uri(final_response.header("contact")) or target.uri
            inbound_rtp.log("B2BUA FAILURE", f"route={target.uri} status={status} reason={reason}")
            flow_log.write("B2BUA FAILURE", f"route={target.uri} status={status} reason={reason}")
            self.send_outbound_ack(b2bua_call, invite_transaction=True)
            flow_log.sip("B2BUA", "SIPp A", f"{status} {reason}")
            self.send_response(message, status, reason, to_header=to_header)
            self.cleanup_b2bua_call(b2bua_call)
            return

        self.routing_engine.record_outcome(route, True)
        self.log_policy_metrics("TRUNK SUCCESS", route, inbound_call_id)

        b2bua_call.outbound_to_header = final_response.header("to")
        b2bua_call.outbound_contact_uri = extract_sip_uri(final_response.header("contact")) or target.uri
        outbound_payloads = parse_sdp_payloads(final_response.body)
        outbound_rtp.remote_payloads = outbound_payloads
        outbound_rtp.preferred_payload = choose_payload(outbound_payloads, outbound_payload)
        outbound_remote = parse_sdp_remote_addr(final_response.body, final_response.source[0])
        if outbound_remote:
            outbound_rtp.remote_addr = outbound_remote
            outbound_rtp.remote_rtcp_addr = parse_sdp_remote_rtcp_addr(final_response.body, outbound_remote)
            outbound_rtp.log("RTP REMOTE", f"remote={outbound_remote[0]}:{outbound_remote[1]} source=sdp")

        inbound_rtp.set_peer(outbound_rtp)
        outbound_rtp.set_peer(inbound_rtp)

        answer_sdp = make_sdp(
            self.sip_advertised_ip,
            inbound_rtp.local_port,
            inbound_rtp.preferred_payload,
            dtmf_payload_type=dtmf_payload_type,
        )
        self.send_response(
            message,
            200,
            "OK",
            body=answer_sdp,
            to_header=to_header,
            extra_headers={
                "Contact": f"<{self.inbound_contact_uri(target_user, message.transport)}>",
                "Content-Type": "application/sdp",
            },
        )
        self.b2bua_calls_answered_total += 1
        self.observe_media_negotiation(
            "internal",
            inbound_rtp.preferred_payload,
            outbound_rtp.preferred_payload,
        )
        flow_log.sip("B2BUA", "SIPp A", "200 OK")
        dialog.mark_answered()
        self.save_dialog_state(dialog)
        inbound_rtp.log(
            "DIALOG STATE",
            (
                f"state={dialog.state.name} local_tag={dialog.local_tag} "
                f"remote_tag={dialog.remote_tag or 'none'} invite_branch={dialog.invite_branch or 'none'} "
                f"remote_cseq={dialog.remote_cseq}"
            ),
        )
        inbound_rtp.log("SIP RESPONSE", "200 OK")
        outbound_rtp.log(
            "B2BUA ANSWERED",
            f"inbound_call_id={inbound_call_id} outbound_payload={CODEC_NAMES.get(outbound_rtp.preferred_payload, outbound_rtp.preferred_payload)}",
        )

    async def handle_b2bua_invite_rtpengine(
        self,
        message: SipMessage,
        dialog: Any,
        to_header: str,
        inbound_call_id: str,
        target_user: str,
        route: RouteResult,
    ) -> None:
        if not self.rtpengine_client:
            self.send_response(message, 500, "RTPengine Not Configured", to_header=to_header)
            return

        target = route.target
        flow_log = B2BUAFlowLog(
            self.media.log_dir,
            inbound_call_id,
            target_user,
            route,
            enabled=self.b2bua_ladder_logs,
            logger=self.logger,
            participants=("SIPp A", "B2BUA", "SIPp B", "RTPengine"),
        )
        flow_log.sip("SIPp A", "B2BUA", "INVITE", f"call_id={inbound_call_id} target_user={target_user}")
        flow_log.sip("B2BUA", "SIPp A", "100 Trying")
        flow_log.write("MEDIA BACKEND", f"backend=rtpengine target={target.uri}")
        unavailable_interfaces = [
            direction
            for direction in self.rtpengine_directions
            if self.rtpengine_interfaces and direction not in self.rtpengine_interfaces
        ]
        if unavailable_interfaces:
            detail = (
                f"requested={','.join(self.rtpengine_directions)} "
                f"available={','.join(sorted(self.rtpengine_interfaces))} "
                f"unavailable={','.join(unavailable_interfaces)}"
            )
            self.logger.media("RTPENGINE INTERFACE UNAVAILABLE", detail, call_id=inbound_call_id)
            flow_log.write("RTPENGINE OFFER FAILED", f"interface_unavailable {detail}")
            flow_log.sip("B2BUA", "SIPp A", "488 Not Acceptable Here")
            self.send_response(message, 488, "Not Acceptable Here", to_header=to_header)
            flow_log.render_ladder()
            return
        if (
            self.rtpengine_offer_transport_protocol
            or self.rtpengine_answer_transport_protocol
            or self.rtpengine_sdes
            or self.rtpengine_dtls
        ):
            flow_log.write(
                "RTPENGINE MEDIA SECURITY",
                (
                    f"offer_transport={self.rtpengine_offer_transport_protocol or 'preserve'} "
                    f"answer_transport={self.rtpengine_answer_transport_protocol or 'preserve'} "
                    f"sdes={','.join(self.rtpengine_sdes) or 'default'} "
                    f"dtls={self.rtpengine_dtls or 'default'}"
                ),
            )

        from_tag = extract_header_tag(message.header("from")) or dialog.remote_tag or secrets.token_hex(6)
        remote_payloads = parse_sdp_payloads(message.body)
        codec_policy = rtpengine_codec_policy(remote_payloads, self.default_payload)
        flow_log.write(
            "RTPENGINE CODEC POLICY",
            (
                f"offered={format_payloads(remote_payloads)} "
                f"target={CODEC_NAMES.get(self.default_payload, self.default_payload)} "
                f"policy={format_rtpengine_codec_policy(codec_policy)}"
            ),
        )
        if codec_policy:
            self.logger.transcoding(
                "RTPENGINE TRANSCODING POLICY",
                (
                    f"offered={format_payloads(remote_payloads)} "
                    f"target={CODEC_NAMES.get(self.default_payload, self.default_payload)} "
                    f"policy={format_rtpengine_codec_policy(codec_policy)} "
                    f"direction={','.join(self.rtpengine_directions) or 'default'}"
                ),
                call_id=inbound_call_id,
            )
        try:
            flow_log.sip("B2BUA", "RTPengine", "OFFER")
            self.rtpengine_control_requests_total += 1
            offer_response = await retry_rtpengine_control(
                "OFFER",
                lambda: self.rtpengine_client.offer(
                    call_id=inbound_call_id,
                    from_tag=from_tag,
                    sdp=message.body,
                    codec=codec_policy,
                    direction=self.rtpengine_directions,
                    transport_protocol=self.rtpengine_offer_transport_protocol,
                    sdes=self.rtpengine_sdes,
                    dtls=self.rtpengine_dtls,
                ),
                flow_log,
            )
            outbound_body = str(offer_response.get("sdp") or "")
            if not outbound_body:
                raise RtpengineError("RTPengine offer response did not include SDP")
            flow_log.write(
                "RTPENGINE OFFER",
                (
                    f"status={offer_response.get('result', 'unknown')} call_id={inbound_call_id} "
                    f"from_tag={from_tag} rewritten_sdp_bytes={len(outbound_body.encode('utf-8'))}"
                ),
            )
            flow_log.sip("RTPengine", "B2BUA", f"{offer_response.get('result', 'ok')} OFFER")
        except (asyncio.TimeoutError, OSError, RtpengineError) as exc:
            self.rtpengine_control_failures_total += 1
            flow_log.write("RTPENGINE OFFER FAILED", str(exc))
            flow_log.sip("RTPengine", "B2BUA", "OFFER failed")
            flow_log.sip("B2BUA", "SIPp A", "488 Not Acceptable Here")
            self.send_response(message, 488, "Not Acceptable Here", to_header=to_header)
            flow_log.render_ladder()
            return

        outbound_call_id = make_call_id()
        outbound_from = f"Mini B2BUA <sip:b2bua@{self.b2bua_advertised_ip}:{self.local_port}>;tag={secrets.token_hex(6)}"
        b2bua_call = B2BUACall(
            inbound_call_id=inbound_call_id,
            outbound_call_id=outbound_call_id,
            outbound_target=target,
            outbound_from_header=outbound_from,
            target_user=target_user,
            route_policy=route.policy_name,
            route_source=route.source,
            flow_log=flow_log,
            route_result=route,
            media_backend="rtpengine",
            rtpengine_call_id=inbound_call_id,
            rtpengine_from_tag=from_tag,
        )
        self.b2bua_calls_total += 1
        self.b2bua_calls_by_inbound[inbound_call_id] = b2bua_call
        self.b2bua_calls_by_outbound[outbound_call_id] = b2bua_call

        response_queue: asyncio.Queue = asyncio.Queue()
        self.pending_outbound_responses[outbound_call_id] = response_queue
        flow_log.write(
            "B2BUA ROUTE",
            f"target_user={target_user} route={target.uri} outbound_call_id={outbound_call_id} route_source={route.source}",
        )
        self.send_outbound_invite(b2bua_call, outbound_body)

        try:
            final_response = await self.wait_for_outbound_invite(
                response_queue,
                message,
                to_header,
                None,
                b2bua_call,
            )
        except asyncio.TimeoutError:
            flow_log.write("B2BUA FAILURE", f"route={target.uri} reason=outbound_invite_timeout")
            self.send_response(message, 480, "Temporarily Unavailable", to_header=to_header)
            self.cleanup_b2bua_call(b2bua_call)
            return
        finally:
            self.pending_outbound_responses.pop(outbound_call_id, None)

        status = final_response.status_code
        reason = final_response.reason_phrase or "Upstream Response"
        b2bua_call.outbound_to_header = final_response.header("to")
        b2bua_call.outbound_contact_uri = extract_sip_uri(final_response.header("contact")) or target.uri
        if status < 200 or status >= 300:
            self.routing_engine.record_outcome(route, False)
            self.log_policy_metrics("TRUNK FAILURE", route, inbound_call_id)
            flow_log.write("B2BUA FAILURE", f"route={target.uri} status={status} reason={reason}")
            self.send_outbound_ack(b2bua_call, invite_transaction=True)
            flow_log.sip("B2BUA", "SIPp A", f"{status} {reason}")
            self.send_response(message, status, reason, to_header=to_header)
            self.cleanup_b2bua_call(b2bua_call)
            return

        self.routing_engine.record_outcome(route, True)
        self.log_policy_metrics("TRUNK SUCCESS", route, inbound_call_id)

        to_tag = extract_header_tag(final_response.header("to")) or secrets.token_hex(6)
        b2bua_call.rtpengine_to_tag = to_tag
        try:
            flow_log.sip("B2BUA", "RTPengine", "ANSWER")
            self.rtpengine_control_requests_total += 1
            answer_response = await retry_rtpengine_control(
                "ANSWER",
                lambda: self.rtpengine_client.answer(
                    call_id=inbound_call_id,
                    from_tag=from_tag,
                    to_tag=to_tag,
                    sdp=final_response.body,
                    codec=codec_policy,
                    transport_protocol=self.rtpengine_answer_transport_protocol,
                    sdes=self.rtpengine_sdes,
                    dtls=self.rtpengine_dtls,
                ),
                flow_log,
            )
            answer_sdp = str(answer_response.get("sdp") or "")
            if not answer_sdp:
                raise RtpengineError("RTPengine answer response did not include SDP")
            flow_log.write(
                "RTPENGINE ANSWER",
                (
                    f"status={answer_response.get('result', 'unknown')} call_id={inbound_call_id} "
                    f"from_tag={from_tag} to_tag={to_tag} rewritten_sdp_bytes={len(answer_sdp.encode('utf-8'))}"
                ),
            )
            flow_log.sip("RTPengine", "B2BUA", f"{answer_response.get('result', 'ok')} ANSWER")
        except (asyncio.TimeoutError, OSError, RtpengineError) as exc:
            self.rtpengine_control_failures_total += 1
            flow_log.write("RTPENGINE ANSWER FAILED", str(exc))
            flow_log.sip("RTPengine", "B2BUA", "ANSWER failed")
            self.send_outbound_ack(b2bua_call)
            self.send_outbound_bye(b2bua_call)
            self.send_response(message, 488, "Not Acceptable Here", to_header=to_header)
            self.cleanup_b2bua_call(b2bua_call)
            return

        inbound_answer_payload = choose_payload(parse_sdp_payloads(answer_sdp), choose_payload(remote_payloads, PCMU))
        outbound_answer_payload = choose_payload(parse_sdp_payloads(final_response.body), self.default_payload)
        self.send_response(
            message,
            200,
            "OK",
            body=answer_sdp,
            to_header=to_header,
            extra_headers={
                "Contact": f"<{self.inbound_contact_uri(target_user, message.transport)}>",
                "Content-Type": "application/sdp",
            },
        )
        self.b2bua_calls_answered_total += 1
        self.observe_media_negotiation("rtpengine", inbound_answer_payload, outbound_answer_payload)
        flow_log.sip("B2BUA", "SIPp A", "200 OK")
        dialog.mark_answered()
        self.save_dialog_state(dialog)

    async def wait_for_outbound_invite(
        self,
        response_queue: asyncio.Queue,
        inbound_request: SipMessage,
        to_header: str,
        inbound_rtp: Optional[RtpSession],
        b2bua_call: B2BUACall,
        timeout: float = 10.0,
    ) -> SipMessage:
        while True:
            response = await asyncio.wait_for(response_queue.get(), timeout=timeout)
            status = response.status_code
            reason = response.reason_phrase or "Upstream Response"
            b2bua_call.flow_log.sip("SIPp B", "B2BUA", f"{status} {reason}")
            if status < 200:
                if status != 100:
                    body = ""
                    if b2bua_call.media_backend != "rtpengine" and response.body:
                        body = response.body
                    extra_headers = {"Content-Type": response.header("content-type")} if body else None
                    b2bua_call.flow_log.sip("B2BUA", "SIPp A", f"{status} {reason}")
                    self.send_response(
                        inbound_request,
                        status,
                        reason,
                        body=body,
                        to_header=to_header,
                        extra_headers=extra_headers,
                    )
                    if inbound_rtp:
                        inbound_rtp.log("SIP RESPONSE", f"{status} {reason} from outbound")
                continue
            return response

    def send_outbound_invite(self, b2bua_call: B2BUACall, body: str) -> None:
        transport_name = b2bua_call.outbound_target.transport
        via_header = self.make_via_header(transport_name)
        b2bua_call.outbound_invite_via_header = via_header
        headers = {
            "Via": via_header,
            "From": b2bua_call.outbound_from_header,
            "To": f"<{b2bua_call.outbound_target.uri}>",
            "Call-ID": b2bua_call.outbound_call_id,
            "CSeq": f"{b2bua_call.outbound_cseq} INVITE",
            "Contact": f"<{self.local_contact_uri(transport_name)}>",
            "Max-Forwards": "69",
            "Subject": f"B2BUA outbound leg for {b2bua_call.inbound_call_id}",
            "Content-Type": "application/sdp",
        }
        if b2bua_call.route_result:
            original_headers = set(headers)
            headers = self.routing_engine.normalize_headers(headers, b2bua_call.route_result, b2bua_call.inbound_call_id)
            self.logger.sip(
                "HEADER NORMALIZATION",
                (
                    f"removed={','.join(sorted(original_headers - set(headers))) or 'none'} "
                    f"added={','.join(sorted(set(headers) - original_headers)) or 'none'}"
                ),
                call_id=b2bua_call.inbound_call_id,
            )
        packet = build_sip_request("INVITE", b2bua_call.outbound_target.uri, headers, body)
        self.observe_sip_request("INVITE", transport_name, "tx", "peer")
        self._send_packet(packet, b2bua_call.outbound_target.address, transport_name=transport_name)
        b2bua_call.flow_log.sip(
            "B2BUA",
            "SIPp B",
            "INVITE",
            f"call_id={b2bua_call.outbound_call_id} target={b2bua_call.outbound_target.uri}",
        )
        session = self.media.get_session(b2bua_call.outbound_call_id)
        if session:
            session.log("B2BUA OUTBOUND INVITE", f"target={b2bua_call.outbound_target.uri}")

    def send_outbound_ack(self, b2bua_call: B2BUACall, invite_transaction: bool = False) -> None:
        request_uri = b2bua_call.outbound_contact_uri or b2bua_call.outbound_target.uri
        transport_name = self.outbound_transport(b2bua_call)
        via_header = b2bua_call.outbound_invite_via_header if invite_transaction else self.make_via_header(transport_name)
        headers = {
            "Via": via_header,
            "From": b2bua_call.outbound_from_header,
            "To": b2bua_call.outbound_to_header,
            "Call-ID": b2bua_call.outbound_call_id,
            "CSeq": f"{b2bua_call.outbound_cseq} ACK",
            "Contact": f"<{self.local_contact_uri(transport_name)}>",
            "Max-Forwards": "69",
        }
        self._send_packet(
            build_sip_request("ACK", request_uri, headers),
            self.outbound_destination(b2bua_call),
            transport_name=transport_name,
        )
        self.observe_sip_request("ACK", transport_name, "tx", "peer")
        b2bua_call.flow_log.sip("B2BUA", "SIPp B", "ACK")
        session = self.media.get_session(b2bua_call.outbound_call_id)
        if session:
            session.mark_ack()
            session.log("B2BUA OUTBOUND ACK")

    def send_outbound_cancel(self, b2bua_call: B2BUACall) -> None:
        request_uri = b2bua_call.outbound_contact_uri or b2bua_call.outbound_target.uri
        b2bua_call.outbound_cancel_sent = True
        transport_name = self.outbound_transport(b2bua_call)
        via_header = b2bua_call.outbound_invite_via_header or self.make_via_header(transport_name)
        headers = {
            "Via": via_header,
            "From": b2bua_call.outbound_from_header,
            "To": b2bua_call.outbound_to_header or f"<{b2bua_call.outbound_target.uri}>",
            "Call-ID": b2bua_call.outbound_call_id,
            "CSeq": f"{b2bua_call.outbound_cseq} CANCEL",
            "Contact": f"<{self.local_contact_uri(transport_name)}>",
            "Max-Forwards": "69",
        }
        self._send_packet(
            build_sip_request("CANCEL", request_uri, headers),
            self.outbound_destination(b2bua_call),
            transport_name=transport_name,
        )
        self.observe_sip_request("CANCEL", transport_name, "tx", "peer")
        b2bua_call.flow_log.sip("B2BUA", "SIPp B", "CANCEL")

    def send_outbound_bye(self, b2bua_call: B2BUACall) -> None:
        request_uri = b2bua_call.outbound_contact_uri or b2bua_call.outbound_target.uri
        b2bua_call.outbound_cseq += 1
        b2bua_call.outbound_bye_sent = True
        transport_name = self.outbound_transport(b2bua_call)
        headers = {
            "Via": self.make_via_header(transport_name),
            "From": b2bua_call.outbound_from_header,
            "To": b2bua_call.outbound_to_header,
            "Call-ID": b2bua_call.outbound_call_id,
            "CSeq": f"{b2bua_call.outbound_cseq} BYE",
            "Contact": f"<{self.local_contact_uri(transport_name)}>",
            "Max-Forwards": "69",
        }
        self._send_packet(
            build_sip_request("BYE", request_uri, headers),
            self.outbound_destination(b2bua_call),
            transport_name=transport_name,
        )
        self.observe_sip_request("BYE", transport_name, "tx", "peer")
        b2bua_call.flow_log.sip("B2BUA", "SIPp B", "BYE")
        session = self.media.get_session(b2bua_call.outbound_call_id)
        if session:
            session.log("B2BUA OUTBOUND BYE", f"target={request_uri}")

    def outbound_destination(self, b2bua_call: B2BUACall) -> Tuple[str, int]:
        if b2bua_call.outbound_contact_uri:
            try:
                return parse_sip_uri(b2bua_call.outbound_contact_uri).address
            except ValueError:
                pass
        return b2bua_call.outbound_target.address

    def outbound_transport(self, b2bua_call: B2BUACall) -> str:
        if b2bua_call.outbound_contact_uri:
            try:
                contact = parse_sip_uri(b2bua_call.outbound_contact_uri)
                if ";transport=" in b2bua_call.outbound_contact_uri.lower() or contact.transport != "udp":
                    return contact.transport
            except ValueError:
                pass
        return b2bua_call.outbound_target.transport

    def cleanup_b2bua_call(self, b2bua_call: B2BUACall) -> None:
        self.finalize_b2bua_call(b2bua_call, "cleanup")
        self.b2bua_calls_by_inbound.pop(b2bua_call.inbound_call_id, None)
        self.b2bua_calls_by_outbound.pop(b2bua_call.outbound_call_id, None)
        self.pending_outbound_responses.pop(b2bua_call.outbound_call_id, None)
        self.media.close_session(b2bua_call.outbound_call_id)
        self.media.close_session(b2bua_call.inbound_call_id)

    async def run_ai_voice_turn(
        self,
        ai_call: AIVoiceCall,
        message: SipMessage,
        selected_payload: int,
        dtmf_payload_type: Optional[int],
    ) -> None:
        if not self.ai_voice_gateway:
            return
        self.ai_voice_turns_total += 1
        caller = extract_user(message.header("from")) or "unknown"
        metadata = {
            "call_id": ai_call.call_id,
            "caller": caller,
            "callee": ai_call.target_user,
            "route_policy": ai_call.route_result.policy_name,
            "route_source": ai_call.route_result.source,
            "bot": ai_call.route_result.group_name or self.ai_voice_config.bot_name,
            "sip_transport": message.transport,
            "codec": CODEC_NAMES.get(selected_payload, str(selected_payload)),
            "dtmf_payload": dtmf_payload_type if dtmf_payload_type is not None else "",
            "source": "playsbc-ai-voice-gateway",
        }
        if self.ai_voice_config.agent_label:
            metadata["agent_label"] = self.ai_voice_config.agent_label
        if self.ai_voice_config.contact_center_queue:
            metadata["contact_center_queue"] = self.ai_voice_config.contact_center_queue
        if self.ai_voice_config.contact_center_skill:
            metadata["contact_center_skill"] = self.ai_voice_config.contact_center_skill
        sender = f"playsbc-{safe_filename(ai_call.call_id)}"
        user_text = self.ai_voice_gateway.initial_user_text()
        speech_pcap_path = resolve_ai_speech_asset(self.ai_voice_config.speech_input_pcap)
        speech_wav_path = ""
        tts_wav_path = ""
        tts_rtp_path = ""
        speech_transcript = self.ai_voice_config.speech_transcript
        if self.ai_voice_config.speech_input_pcap and not speech_pcap_path:
            self.logger.ai(
                "AI SPEECH RTP EXTRACTION FAILED",
                f"pcap={self.ai_voice_config.speech_input_pcap} error_type=FileNotFoundError",
                call_id=ai_call.call_id,
            )
        if speech_pcap_path:
            evidence_dir = self.logger.log_dir or self.media.log_dir
            if evidence_dir:
                call_stem = safe_filename(ai_call.call_id)
                speech_wav = evidence_dir / f"ai-speech-input-{call_stem}.wav"
                try:
                    extraction = decode_rtp_pcap_to_wav(
                        speech_pcap_path,
                        speech_wav,
                        codec=self.ai_voice_config.speech_input_codec,
                        transcript=speech_transcript,
                    )
                    speech_wav_path = extraction.wav_path
                    if extraction.transcript:
                        user_text = extraction.transcript
                    self.ai_stt_audio_decodes_total += 1
                    self.logger.ai(
                        "AI SPEECH RTP EXTRACTED",
                        (
                            f"pcap={speech_pcap_path} wav={extraction.wav_path} codec={extraction.codec} "
                            f"payload_type={extraction.payload_type} packets={extraction.packets} "
                            f"payload_bytes={extraction.payload_bytes} duration_seconds={extraction.duration_seconds:.3f} "
                            f"transcript={json.dumps(user_text)}"
                        ),
                        call_id=ai_call.call_id,
                    )
                    self.logger.media(
                        "AI SPEECH PCAP INPUT",
                        (
                            f"source_pcap={speech_pcap_path} decoded_wav={extraction.wav_path} "
                            f"codec={extraction.codec} packets={extraction.packets} media_backend={ai_call.media_backend}"
                        ),
                        call_id=ai_call.call_id,
                    )
                    tts_wav_path = str(evidence_dir / f"ai-tts-output-{call_stem}.wav")
                    tts_rtp_path = str(evidence_dir / f"ai-tts-rtp-{call_stem}.pcap")
                except Exception as exc:
                    self.logger.ai(
                        "AI SPEECH RTP EXTRACTION FAILED",
                        f"pcap={speech_pcap_path} error_type={type(exc).__name__} error={json.dumps(str(exc))}",
                        call_id=ai_call.call_id,
                    )
        stt_node = ai_stt_ladder_node(self.ai_voice_config.stt_provider)
        tts_node = ai_tts_ladder_node(self.ai_voice_config.tts_provider)
        bot_node = self.ai_voice_config.agent_label or "Rasa Bot"
        if self.ai_voice_config.agent_label != "Rasa Bot" or self.ai_voice_config.contact_center_queue:
            self.logger.ai(
                "AI CONTACT CENTER AGENT",
                (
                    f"agent_label={json.dumps(bot_node)} queue={json.dumps(self.ai_voice_config.contact_center_queue)} "
                    f"skill={json.dumps(self.ai_voice_config.contact_center_skill)} "
                    f"b_side=virtual-rasa-agent target_user={ai_call.target_user}"
                ),
                call_id=ai_call.call_id,
            )
        media_input_label = "RTPengine RTP/RTCP input" if ai_call.media_backend == "rtpengine" else "RTP/media input"
        media_source_node = "RTPengine" if ai_call.media_backend == "rtpengine" and speech_wav_path else "PlaySBC"
        ai_call.flow_log.flow(
            media_source_node,
            "PlaySBC" if media_source_node == "RTPengine" else stt_node,
            "anchored speech RTP" if media_source_node == "RTPengine" and speech_wav_path else media_input_label,
        )
        if media_source_node == "RTPengine":
            ai_call.flow_log.flow("PlaySBC", stt_node, "decode WAV")
        ai_call.flow_log.flow(stt_node, "PlaySBC", self.ai_voice_config.stt_provider)
        self.logger.ai(
            "AI STT INPUT",
            (
                f"adapter={self.ai_voice_config.stt_provider} audio_decoded={str(bool(speech_wav_path)).lower()} "
                f"mode={self.ai_voice_config.input_mode} audio_path={json.dumps(speech_wav_path)} "
                f"text={json.dumps(user_text)} sender={sender}"
            ),
            call_id=ai_call.call_id,
        )
        self.logger.ai(
            "RASA REST REQUEST",
            f"url={self.ai_voice_config.rasa_webhook_url} sender={sender} metadata_keys={','.join(sorted(metadata))}",
            call_id=ai_call.call_id,
        )
        self.ai_rasa_requests_total += 1
        ai_call.flow_log.flow("PlaySBC", bot_node, "Rasa REST POST")
        result: AiTurnResult = await self.ai_voice_gateway.start_turn(
            sender,
            metadata,
            audio_path=speech_wav_path,
            tts_output_path=tts_wav_path,
            tts_rtp_path=tts_rtp_path,
            tts_codec=self.ai_voice_config.tts_output_codec,
        )
        stt = result.stt
        if stt:
            self.logger.ai(
                "AI STT RESULT",
                (
                    f"provider={stt.provider} engine_ready={str(stt.engine_ready).lower()} "
                    f"audio_decoded={str(stt.audio_decoded).lower()} duration_seconds={stt.duration_seconds:.3f} "
                    f"error={json.dumps(stt.error)} text={json.dumps(stt.text)}"
                ),
                call_id=ai_call.call_id,
            )
        if result.error:
            self.ai_rasa_failures_total += 1
            self.ai_voice_turn_failures_total += 1
            self.logger.ai(
                "RASA REST ERROR",
                f"fallback_used=true error={result.error}",
                call_id=ai_call.call_id,
            )
        elif result.fallback_used:
            self.ai_voice_turn_failures_total += 1
        self.logger.ai(
            "RASA REST RESPONSE",
            (
                f"response_count={len(result.bot_responses)} fallback_used={str(result.fallback_used).lower()} "
                f"duration_seconds={result.duration_seconds:.3f} response_mode={result.response_mode} "
                f"tts_chunk_count={result.tts_chunk_count} "
                f"text={json.dumps(result.rendered_text)}"
            ),
            call_id=ai_call.call_id,
        )
        ai_call.flow_log.flow(bot_node, "PlaySBC", "Rasa REST 200")
        for index, response in enumerate(result.bot_responses, start=1):
            if index > 1:
                ai_call.flow_log.flow(bot_node, "PlaySBC", f"response chunk {index}")
        tts_chunks = result.tts_chunks or ([result.tts] if result.tts else [])
        self.ai_tts_outputs_total += len(tts_chunks)
        if len(tts_chunks) > 1:
            self.logger.ai(
                "AI TTS STREAM START",
                (
                    f"mode={result.response_mode} chunk_count={len(tts_chunks)} "
                    f"renderer={self.ai_voice_config.tts_provider} codec={self.ai_voice_config.tts_output_codec}"
                ),
                call_id=ai_call.call_id,
            )
        for chunk in tts_chunks:
            chunk_label = f"bot text chunk {chunk.chunk_index}/{chunk.chunk_count}" if chunk.chunk_count > 1 else "bot text"
            audio_label = (
                f"{self.ai_voice_config.tts_provider} WAV chunk {chunk.chunk_index}/{chunk.chunk_count}"
                if chunk.chunk_count > 1
                else self.ai_voice_config.tts_provider
            )
            ai_call.flow_log.flow("PlaySBC", tts_node, chunk_label)
            ai_call.flow_log.flow(tts_node, "PlaySBC", audio_label)
            if chunk.chunk_count > 1:
                self.logger.ai(
                    "AI TTS STREAM CHUNK",
                    (
                        f"chunk={chunk.chunk_index}/{chunk.chunk_count} renderer={chunk.provider} "
                        f"audio_generated={str(chunk.audio_generated).lower()} "
                        f"rtp_prompt_generated={str(chunk.rtp_prompt_generated).lower()} "
                        f"engine_ready={str(chunk.engine_ready).lower()} error={json.dumps(chunk.error)} "
                        f"audio_path={json.dumps(chunk.audio_path)} rtp_path={json.dumps(chunk.rtp_path)} "
                        f"text={json.dumps(chunk.text)}"
                    ),
                    call_id=ai_call.call_id,
                )
        tts = result.tts
        audio_generated = str(any(chunk.audio_generated for chunk in tts_chunks)).lower() if tts_chunks else "false"
        rtp_prompt_generated = str(any(chunk.rtp_prompt_generated for chunk in tts_chunks)).lower() if tts_chunks else "false"
        tts_provider = tts.provider if tts else self.ai_voice_config.tts_provider
        tts_error = tts.error if tts else ""
        self.logger.ai(
            "AI TTS OUTPUT",
            (
                f"renderer={tts_provider} audio_generated={audio_generated} "
                f"rtp_prompt_generated={rtp_prompt_generated} error={json.dumps(tts_error)} "
                f"chunk_count={len(tts_chunks)} "
                f"audio_path={json.dumps(tts.audio_path if tts else '')} "
                f"rtp_path={json.dumps(tts.rtp_path if tts else '')} "
                f"text={json.dumps(result.rendered_text)}"
            ),
            call_id=ai_call.call_id,
        )
        for chunk in tts_chunks:
            if not chunk.rtp_prompt_generated:
                continue
            self.ai_tts_rtp_prompts_total += 1
            if ai_call.media_backend == "rtpengine":
                ai_call.flow_log.flow("PlaySBC", "RTPengine", "G.711 RTP prompt")
            else:
                ai_call.flow_log.flow(tts_node, "PlaySBC", "G.711 RTP prompt")
            self.logger.media(
                "AI TTS RTP PROMPT",
                (
                    f"status=generated chunk={chunk.chunk_index}/{chunk.chunk_count} "
                    f"rtp_pcap={chunk.rtp_path} audio_path={chunk.audio_path} "
                    f"codec={self.ai_voice_config.tts_output_codec} media_backend={ai_call.media_backend} "
                    "delivery_model=rtpengine_media_anchor_evidence"
                ),
                call_id=ai_call.call_id,
            )
        for action in result.bot_actions:
            self.apply_ai_bot_action(ai_call, action)

    def apply_ai_bot_action(self, ai_call: AIVoiceCall, action: BotAction) -> None:
        ai_call.bot_actions.append(action)
        self.ai_bot_actions_total += 1
        self.logger.ai(
            "AI BOT ACTION",
            (
                f"action={action.action} target={action.target or 'none'} reason={action.reason or 'none'} "
                "status=accepted control_plane_only=true"
            ),
            call_id=ai_call.call_id,
        )
        if action.action == "release":
            self.logger.ai("AI BOT RELEASE", "status=queued reason=bot_requested_release", call_id=ai_call.call_id)
            return
        if action.action == "transfer":
            self.logger.ai(
                "AI BOT TRANSFER",
                f"status=queued target={action.target or 'missing'} note=refer_or_reinvite_future_step",
                call_id=ai_call.call_id,
            )
            return
        if action.action == "join":
            self.logger.ai(
                "AI BOT JOIN",
                f"status=queued target={action.target or 'b2bua-call'} note=conference_bridge_future_step",
                call_id=ai_call.call_id,
            )
            return
        self.logger.ai("AI BOT ACTION IGNORED", f"action={action.action}", call_id=ai_call.call_id)

    def finalize_ai_voice_call(self, ai_call: AIVoiceCall, reason: str) -> None:
        if ai_call.finalized:
            return
        ai_call.finalized = True
        self.routing_engine.release(ai_call.route_result)
        self.log_policy_metrics("CALL RELEASED", ai_call.route_result, ai_call.call_id)
        task_status = "not_started"
        if ai_call.task:
            task_status = "done" if ai_call.task.done() else "running"
        self.logger.ai(
            "AI VOICE CALL END",
            f"reason={reason} task_status={task_status} media_backend={ai_call.media_backend}",
            call_id=ai_call.call_id,
        )
        if ai_call.media_backend == "rtpengine":
            ai_call.flow_log.flow("PlaySBC", "RTPengine", "DELETE")
        ai_call.flow_log.render()
        self.schedule_ai_rtpengine_delete(ai_call)
        self.ai_voice_calls_by_inbound.pop(ai_call.call_id, None)

    def schedule_ai_rtpengine_delete(self, ai_call: AIVoiceCall) -> None:
        if ai_call.media_backend != "rtpengine" or not self.rtpengine_client:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._delete_ai_rtpengine_call(ai_call))

    async def _delete_ai_rtpengine_call(self, ai_call: AIVoiceCall) -> None:
        assert self.rtpengine_client is not None
        await self.query_ai_rtpengine_call(ai_call, allow_cached_on_unknown=True)
        try:
            self.rtpengine_control_requests_total += 1
            await self.rtpengine_client.delete(
                call_id=ai_call.rtpengine_call_id or ai_call.call_id,
                from_tag=ai_call.rtpengine_from_tag,
                to_tag=ai_call.rtpengine_to_tag,
            )
            self.logger.media("B2BUA RTPENGINE DELETE", "status=ok", call_id=ai_call.call_id)
        except (asyncio.TimeoutError, OSError, RtpengineError) as exc:
            self.rtpengine_control_failures_total += 1
            self.logger.media("B2BUA RTPENGINE DELETE FAILED", str(exc), call_id=ai_call.call_id)

    async def query_ai_rtpengine_call(
        self,
        ai_call: AIVoiceCall,
        attempts: int = 4,
        allow_cached_on_unknown: bool = False,
    ) -> bool:
        if ai_call.media_backend != "rtpengine" or not self.rtpengine_client:
            return False
        assert self.rtpengine_client is not None
        try:
            query_timeout = min(max(self.rtpengine_client.timeout, 0.050), 1.0)
            self.rtpengine_control_requests_total += 1
            query_response, packet_samples, retry_count = await query_rtpengine_until_stable(
                lambda: asyncio.wait_for(
                    self.rtpengine_client.query(
                        call_id=ai_call.rtpengine_call_id or ai_call.call_id,
                        from_tag=ai_call.rtpengine_from_tag,
                        to_tag=ai_call.rtpengine_to_tag,
                    ),
                    timeout=query_timeout,
                )
            )
            summary = {
                key: query_response[key]
                for key in sorted(query_response)
                if key in {"result", "created", "last signal", "totals", "tags"}
            }
            summary["source"] = "live"
            detail = json.dumps(summary, sort_keys=True)
            ai_call.rtpengine_query_observed = True
            ai_call.rtpengine_query_summary = detail
            ai_call.rtpengine_query_packet_samples = packet_samples
            ai_call.rtpengine_query_retries = retry_count
            self.logger.media(
                "B2BUA RTPENGINE QUERY",
                detail,
                call_id=ai_call.call_id,
            )
            self.logger.media(
                "AI RTPENGINE QUERY SUMMARY",
                f"query_packet_samples={','.join(str(value) for value in packet_samples)} query_retry_count={retry_count}",
                call_id=ai_call.call_id,
            )
            return True
        except (asyncio.TimeoutError, OSError, RtpengineError) as exc:
            if (
                allow_cached_on_unknown
                and isinstance(exc, RtpengineError)
                and "Unknown call-id" in str(exc)
                and ai_call.rtpengine_query_observed
            ):
                self.logger.media(
                    "AI RTPENGINE QUERY CACHED",
                    (
                        f"source=post_answer_snapshot final_query=unknown_call_id "
                        f"cached_summary={ai_call.rtpengine_query_summary}"
                    ),
                    call_id=ai_call.call_id,
                )
                self.logger.media(
                    "AI RTPENGINE QUERY SUMMARY",
                    (
                        f"query_packet_samples={','.join(str(value) for value in ai_call.rtpengine_query_packet_samples)} "
                        f"query_retry_count={ai_call.rtpengine_query_retries} source=post_answer_snapshot"
                    ),
                    call_id=ai_call.call_id,
                )
                return True
            self.rtpengine_control_failures_total += 1
            self.logger.media(
                "B2BUA RTPENGINE QUERY FAILED",
                f"error_type={type(exc).__name__} error={str(exc) or 'no additional detail'}",
                call_id=ai_call.call_id,
            )
            return False

    def schedule_b2bua_finalizer(self, b2bua_call: B2BUACall, delay: float = 2.0) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._finalize_b2bua_later(b2bua_call, delay))

    async def _finalize_b2bua_later(self, b2bua_call: B2BUACall, delay: float) -> None:
        await asyncio.sleep(delay)
        self.finalize_b2bua_call(b2bua_call, "timer")

    def finalize_b2bua_call(self, b2bua_call: B2BUACall, reason: str) -> None:
        if b2bua_call.finalized:
            return
        b2bua_call.finalized = True
        if reason == "cleanup":
            self.b2bua_calls_failed_total += 1
        else:
            self.b2bua_calls_completed_total += 1
        if b2bua_call.route_result and not b2bua_call.admission_released:
            self.routing_engine.release(b2bua_call.route_result)
            b2bua_call.admission_released = True
            self.log_policy_metrics("CALL RELEASED", b2bua_call.route_result, b2bua_call.inbound_call_id)
        if b2bua_call.media_backend == "rtpengine":
            b2bua_call.flow_log.sip("B2BUA", "RTPengine", "DELETE")
        b2bua_call.flow_log.write("CALL END", f"reason={reason}")
        b2bua_call.flow_log.render_ladder()
        self.schedule_rtpengine_delete(b2bua_call)
        self.b2bua_calls_by_inbound.pop(b2bua_call.inbound_call_id, None)
        self.b2bua_calls_by_outbound.pop(b2bua_call.outbound_call_id, None)
        self.pending_outbound_responses.pop(b2bua_call.outbound_call_id, None)

    def log_policy_metrics(self, event: str, route: RouteResult, call_id: str) -> None:
        metrics = self.routing_engine.metrics()
        detail = (
            f"trunk={route.trunk_name or 'none'} group={route.group_name or 'none'} "
            f"active_calls={metrics.get('playsbc_active_calls', 0)} "
            f"admission_rejections={metrics.get('playsbc_admission_rejections_total', 0)}"
        )
        if route.trunk_name:
            prefix = "playsbc_trunk_" + re.sub(r"[^a-zA-Z0-9_]", "_", route.trunk_name)
            detail += (
                f" healthy={metrics.get(prefix + '_healthy', 0)}"
                f" attempts={metrics.get(prefix + '_attempts_total', 0)}"
                f" successes={metrics.get(prefix + '_successes_total', 0)}"
                f" failures={metrics.get(prefix + '_failures_total', 0)}"
            )
        detail += " " + " ".join(f"{name}={value}" for name, value in sorted(metrics.items()))
        self.logger.call(event, detail, call_id=call_id)

    def schedule_rtpengine_delete(self, b2bua_call: B2BUACall) -> None:
        if b2bua_call.media_backend != "rtpengine" or not self.rtpengine_client:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._delete_rtpengine_call(b2bua_call))

    async def _delete_rtpengine_call(self, b2bua_call: B2BUACall) -> None:
        assert self.rtpengine_client is not None
        try:
            query_timeout = min(max(self.rtpengine_client.timeout, 0.050), 1.0)
            self.rtpengine_control_requests_total += 1
            query_response, packet_samples, retry_count = await query_rtpengine_until_stable(
                lambda: asyncio.wait_for(
                    self.rtpengine_client.query(
                        call_id=b2bua_call.rtpengine_call_id or b2bua_call.inbound_call_id,
                        from_tag=b2bua_call.rtpengine_from_tag,
                        to_tag=b2bua_call.rtpengine_to_tag,
                    ),
                    timeout=query_timeout,
                )
            )
            summary = {
                key: query_response[key]
                for key in sorted(query_response)
                if key in {"result", "created", "last signal", "totals", "tags"}
            }
            detail = json.dumps(summary, sort_keys=True)
            if not b2bua_call.flow_log.enabled:
                rtp_totals = query_response.get("totals", {}).get("RTP", {})
                rtcp_totals = query_response.get("totals", {}).get("RTCP", {})
                detail = (
                    f"result={query_response.get('result', 'unknown')} "
                    f"rtp_packets_total={rtp_totals.get('packets', 0)} "
                    f"rtp_bytes_total={rtp_totals.get('bytes', 0)} "
                    f"rtp_errors_total={rtp_totals.get('errors', 0)} "
                    f"rtcp_packets_total={rtcp_totals.get('packets', 0)} "
                    f"rtcp_bytes_total={rtcp_totals.get('bytes', 0)} "
                    f"rtcp_errors_total={rtcp_totals.get('errors', 0)} "
                    f"query_packet_samples={','.join(str(value) for value in packet_samples)} "
                    f"query_retry_count={retry_count}"
            )
            b2bua_call.flow_log.write("RTPENGINE QUERY", detail)
        except (asyncio.TimeoutError, OSError, RtpengineError) as exc:
            self.rtpengine_control_failures_total += 1
            error = str(exc) or "no additional detail"
            b2bua_call.flow_log.write("RTPENGINE QUERY FAILED", f"error_type={type(exc).__name__} error={error}")
        try:
            self.rtpengine_control_requests_total += 1
            await self.rtpengine_client.delete(
                call_id=b2bua_call.rtpengine_call_id or b2bua_call.inbound_call_id,
                from_tag=b2bua_call.rtpengine_from_tag,
                to_tag=b2bua_call.rtpengine_to_tag,
            )
            b2bua_call.flow_log.write("RTPENGINE DELETE", "status=ok")
        except (asyncio.TimeoutError, OSError, RtpengineError) as exc:
            self.rtpengine_control_failures_total += 1
            b2bua_call.flow_log.write("RTPENGINE DELETE FAILED", str(exc))

    def cleanup_registrations(self) -> None:
        now = time.time()
        if self.shared_state:
            expired = self.shared_state.delete_expired_registrations(now)
            if expired:
                self.logger.platform("HA REGISTRATION EXPIRE", f"node={self.node_id} expired={expired}")
            self.registrations = self.shared_state.load_registrations(now)
            return
        expired = [user for user, registration in self.registrations.items() if registration.is_expired(now)]
        for user in expired:
            self.registrations.pop(user, None)
            logging.info("Expired registration for %s", user)

    def save_registration_state(self, registration: Registration) -> None:
        self.registrations[registration.user] = registration
        if self.shared_state:
            self.shared_state.save_registration(registration)

    def delete_registration_state(self, user: str) -> None:
        self.registrations.pop(user, None)
        if self.shared_state:
            self.shared_state.delete_registration(user)

    def save_dialog_state(self, dialog: SipDialog) -> None:
        if self.shared_state:
            self.shared_state.save_dialog(dialog)

    def restore_dialog_state(self, call_id: str) -> Optional[SipDialog]:
        if not self.shared_state:
            return None
        dialog = self.shared_state.load_dialog(call_id)
        if not dialog:
            return None
        self.dialogs.dialogs[call_id] = dialog
        self.ha_dialog_restores += 1
        self.logger.platform(
            "HA DIALOG RESTORED",
            f"node={self.node_id} call_id={call_id} state={dialog.state.name} restore_count={self.ha_dialog_restores}",
        )
        return dialog

    def acknowledge_dialog(self, call_id: str, cseq_header: str) -> SipDialog:
        try:
            dialog = self.dialogs.acknowledge(call_id, cseq_header)
        except DialogError:
            if not self.restore_dialog_state(call_id):
                raise
            dialog = self.dialogs.acknowledge(call_id, cseq_header)
        self.save_dialog_state(dialog)
        return dialog

    def terminate_dialog(
        self,
        call_id: str,
        from_header: str,
        to_header: str,
        via_header: str,
        cseq_header: str,
    ) -> SipDialog:
        try:
            dialog = self.dialogs.terminate(call_id, from_header, to_header, via_header, cseq_header)
        except DialogError:
            if not self.restore_dialog_state(call_id):
                raise
            dialog = self.dialogs.terminate(call_id, from_header, to_header, via_header, cseq_header)
        self.save_dialog_state(dialog)
        return dialog

    def make_via_header(self, transport_name: str = "udp") -> str:
        transport_name = normalize_sip_transport(transport_name)
        port = self.tls_port if transport_name == "tls" else self.local_port
        return f"SIP/2.0/{transport_name.upper()} {self.b2bua_advertised_ip}:{port};branch=z9hG4bK-{secrets.token_hex(8)}"

    def local_contact_uri(self, transport_name: str = "udp") -> str:
        transport_name = normalize_sip_transport(transport_name)
        port = self.tls_port if transport_name == "tls" else self.local_port
        return SipUri("b2bua", self.b2bua_advertised_ip, port, transport_name).uri

    def inbound_contact_uri(self, target_user: str, transport_name: str = "udp") -> str:
        transport_name = normalize_sip_transport(transport_name)
        port = self.tls_port if transport_name == "tls" else self.local_port
        return SipUri(target_user, self.sip_advertised_ip, port, transport_name).uri

    def authenticate_register(self, message: SipMessage, user: str) -> str:
        if not self.users:
            return "ok"
        if user not in self.users:
            return "forbidden"

        authorization = message.header("authorization")
        if not authorization:
            return "challenge"

        digest = parse_digest_header(authorization)
        nonce = digest.get("nonce", "")
        if (
            digest.get("username") != user
            or digest.get("realm") != self.auth_realm
            or nonce not in self.nonces
            or time.time() - self.nonces[nonce] > 300
        ):
            return "challenge"

        expected = make_digest_response(
            username=user,
            realm=self.auth_realm,
            password=self.users[user],
            method=message.method,
            uri=digest.get("uri", ""),
            nonce=nonce,
            nc=digest.get("nc"),
            cnonce=digest.get("cnonce"),
            qop=digest.get("qop"),
        )
        return "ok" if secrets.compare_digest(expected, digest.get("response", "")) else "challenge"

    def make_authenticate_header(self) -> str:
        nonce = secrets.token_hex(16)
        self.nonces[nonce] = time.time()
        return f'Digest realm="{self.auth_realm}", nonce="{nonce}", algorithm=MD5, qop="auth"'

    def send_response(
        self,
        request: SipMessage,
        status: int,
        reason: str,
        body: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
        to_header: Optional[str] = None,
    ) -> None:
        headers = {
            "Via": request.header("via"),
            "From": request.header("from"),
            "To": to_header or ensure_tag(request.header("to")),
            "Call-ID": request.header("call-id"),
            "CSeq": request.header("cseq"),
            "Server": f"PlaySBC/{PLAYSBC_VERSION}",
            "Content-Length": str(len(body.encode("utf-8"))),
        }
        if extra_headers:
            headers.update(extra_headers)

        lines = [f"SIP/2.0 {status} {reason}"]
        lines.extend(f"{name}: {value}" for name, value in headers.items() if value)
        packet = (CRLF.join(lines) + CRLF + CRLF + body).encode("utf-8")
        self.logger.sip(
            "SIP TX RESPONSE",
            f"transport={request.transport} status={status} reason={reason} destination={request.source[0]}:{request.source[1]} cseq={request.header('cseq')}",
            call_id=request.header("call-id"),
        )
        self.observe_sip_response(status, request.transport, "tx", "core")
        self._send_packet(packet, request.source, transport_name=request.transport, connection=request.connection)
        if request.transport == "udp":
            self.transactions.cache_response(
                request.method,
                request.header("via"),
                request.header("cseq"),
                request.header("call-id"),
                packet,
                request.source,
                status,
            )

    def _send_packet(
        self,
        packet: bytes,
        destination: Tuple[str, int],
        transport_name: str = "udp",
        connection: Optional[SipTcpConnectionProtocol] = None,
    ) -> None:
        transport_name = normalize_sip_transport(transport_name)
        if transport_name in {"tcp", "tls"}:
            if connection:
                try:
                    connection.send(packet)
                    self.logger.write(
                        transport_name,
                        f"{transport_name.upper()} TX",
                        f"protocol=sip destination={destination[0]}:{destination[1]} bytes={len(packet)}",
                    )
                    return
                except ConnectionError as exc:
                    self.logger.networking(
                        f"{transport_name.upper()} TX FAILED",
                        f"destination={destination[0]}:{destination[1]} error={exc}",
                    )
            asyncio.create_task(self._send_stream_packet(packet, destination, transport_name))
            return

        if self.transport:
            self.logger.udp("UDP TX", f"protocol=sip destination={destination[0]}:{destination[1]} bytes={len(packet)}")
            self.transport.sendto(packet, destination)

    async def _send_stream_packet(
        self,
        packet: bytes,
        destination: Tuple[str, int],
        transport_name: str,
    ) -> None:
        key = (transport_name, destination[0], destination[1])
        connection = self.stream_connections.get(key)
        try:
            if connection and not connection.closed:
                self.stream_reuses += 1
                self.logger.write(
                    transport_name,
                    f"{transport_name.upper()} CONNECTION REUSED",
                    f"destination={destination[0]}:{destination[1]} reuse_count={self.stream_reuses}",
                )
            else:
                loop = asyncio.get_running_loop()
                ssl_context = self.tls_client_context if transport_name == "tls" else None
                _transport, protocol = await loop.create_connection(
                    lambda: SipTcpConnectionProtocol(self, transport_name),
                    destination[0],
                    destination[1],
                    ssl=ssl_context,
                    server_hostname=destination[0] if ssl_context else None,
                )
                connection = protocol  # type: ignore[assignment]
                self.stream_connects += 1
            connection.send(packet)
            self.logger.write(
                transport_name,
                f"{transport_name.upper()} TX",
                f"protocol=sip destination={destination[0]}:{destination[1]} bytes={len(packet)}",
            )
        except (OSError, ConnectionError, ssl.SSLError) as exc:
            self.stream_failures += 1
            self.stream_connections.pop(key, None)
            self.logger.networking(
                f"{transport_name.upper()} TX FAILED",
                f"destination={destination[0]}:{destination[1]} failures={self.stream_failures} error={exc}",
            )

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.transactions.close()
        for task in self.background_tasks:
            task.cancel()
        if self.shared_state:
            self.shared_state.close()


def parse_sip_message(
    text: str,
    source: Tuple[str, int],
    transport_name: str = "udp",
    connection: Optional[SipTcpConnectionProtocol] = None,
) -> SipMessage:
    head, _, body = text.partition(CRLF + CRLF)
    lines = head.splitlines()
    start_line = lines[0].strip()
    headers: Dict[str, str] = {}

    current_name = ""
    for line in lines[1:]:
        if line.startswith((" ", "\t")) and current_name:
            headers[current_name] += " " + line.strip()
            continue

        name, _, value = line.partition(":")
        current_name = normalize_header_name(name.strip())
        headers[current_name] = value.strip()

    return SipMessage(
        start_line=start_line,
        headers=headers,
        body=body,
        source=source,
        transport=normalize_sip_transport(transport_name),
        connection=connection,
    )


def normalize_header_name(name: str) -> str:
    compact = name.lower()
    return {
        "i": "call-id",
        "f": "from",
        "t": "to",
        "v": "via",
        "m": "contact",
        "l": "content-length",
        "c": "content-type",
    }.get(compact, compact)


def ensure_tag(to_header: str) -> str:
    if "tag=" in to_header.lower():
        return to_header
    return f"{to_header};tag={random.randint(100000, 999999)}"


def extract_header_tag(header_value: str) -> str:
    match = re.search(r"(?:^|;)\s*tag=([^;\s>]+)", header_value, re.IGNORECASE)
    return match.group(1) if match else ""


def extract_user(header_value: str) -> Optional[str]:
    match = re.search(r"sip:([^@;>]+)", header_value)
    return match.group(1) if match else None


def extract_request_user(start_line: str) -> Optional[str]:
    match = re.search(r"^\S+\s+sip:([^@;:\s>]+)", start_line, re.IGNORECASE)
    return match.group(1) if match else None


def parse_cseq_method(cseq_header: str) -> str:
    parts = cseq_header.strip().split()
    return parts[1].upper() if len(parts) >= 2 else ""


def extract_sip_uri(value: str) -> str:
    bracketed = re.search(r"<\s*(sip:[^>\s]+)\s*>", value, re.IGNORECASE)
    if bracketed:
        return bracketed.group(1)
    match = re.search(r"sip:[^,>\s]+", value, re.IGNORECASE)
    return match.group(0) if match else ""


def parse_sip_uri(value: str) -> SipUri:
    match = re.search(r"sip:([^@;>\s]+)@([^;:>\s]+)(?::(\d+))?((?:;[^>\s,]+)*)", value, re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid SIP URI {value!r}")

    port = int(match.group(3)) if match.group(3) else 5060
    if port <= 0 or port > 65535:
        raise ValueError(f"Invalid SIP URI port {port}")
    params = parse_uri_params(match.group(4) or "")
    transport = normalize_sip_transport(params.get("transport", "udp"))
    return SipUri(user=match.group(1), host=match.group(2), port=port, transport=transport)


def parse_uri_params(value: str) -> Dict[str, str]:
    params: Dict[str, str] = {}
    for raw_param in value.split(";"):
        if not raw_param:
            continue
        key, _, raw_value = raw_param.partition("=")
        params[key.strip().lower()] = raw_value.strip()
    return params


def normalize_sip_transport(value: str) -> str:
    transport = (value or "udp").strip().lower()
    if transport not in SIP_TRANSPORTS:
        raise ValueError(f"Unsupported SIP transport {value!r}. Supported values: {', '.join(sorted(SIP_TRANSPORTS))}")
    return transport


def parse_sip_transport_set(value: str) -> Tuple[str, ...]:
    transports = tuple(dict.fromkeys(normalize_sip_transport(item) for item in re.split(r"[,/+\s]+", value) if item.strip()))
    return transports or ("udp",)


def tcp_content_length(headers: bytes) -> int:
    for line in headers.decode("utf-8", errors="replace").splitlines():
        name, _, value = line.partition(":")
        if normalize_header_name(name.strip()) == "content-length":
            stripped = value.strip()
            return int(stripped) if stripped.isdigit() else 0
    return 0


def format_route_target(target: str, user: str) -> str:
    return target.replace("{user}", user)


def parse_register_expires(expires_header: str, contact_header: str, default: int = 300) -> int:
    contact_match = re.search(r"(?:^|;)\s*expires=([0-9]+)", contact_header, re.IGNORECASE)
    if contact_match:
        return int(contact_match.group(1))
    if expires_header.strip().isdigit():
        return int(expires_header.strip())
    return default


def parse_sdp_payloads(sdp: str) -> Tuple[int, ...]:
    match = re.search(r"^m=audio\s+\d+\s+\S+\s+(.+)$", sdp, re.MULTILINE)
    if not match:
        return SUPPORTED_CODECS

    payloads = []
    for token in match.group(1).split():
        try:
            payloads.append(int(token))
        except ValueError:
            continue
    return tuple(payloads)


def parse_sdp_remote_addr(sdp: str, fallback_ip: str = "") -> Optional[Tuple[str, int]]:
    media_match = re.search(r"^m=audio\s+(\d+)\s+RTP/AVP\b", sdp, re.MULTILINE)
    if not media_match:
        return None

    connection_match = re.search(r"^c=IN\s+IP[46]\s+([^\s]+)", sdp, re.MULTILINE)
    host = connection_match.group(1) if connection_match else fallback_ip
    if not host:
        return None

    port = int(media_match.group(1))
    if port <= 0 or port > 65535:
        return None
    return host, port


def parse_sdp_remote_rtcp_addr(sdp: str, rtp_addr: Tuple[str, int]) -> Tuple[str, int]:
    match = re.search(r"^a=rtcp:(\d+)(?:\s+IN\s+IP[46]\s+([^\s]+))?", sdp, re.MULTILINE | re.IGNORECASE)
    if not match:
        return rtp_addr[0], rtp_addr[1] + 1
    port = int(match.group(1))
    host = match.group(2) or rtp_addr[0]
    return host, port


def parse_dtmf_payload_type(sdp: str) -> Optional[int]:
    for match in re.finditer(r"^a=rtpmap:(\d+)\s+telephone-event/8000", sdp, re.IGNORECASE | re.MULTILINE):
        return int(match.group(1))
    return None


def choose_payload(remote_payloads: Tuple[int, ...], default_payload: int = PCMU) -> int:
    if default_payload in SUPPORTED_CODECS and default_payload in remote_payloads:
        return default_payload

    for payload in remote_payloads:
        if payload in SUPPORTED_CODECS:
            return payload
    return PCMU


def b2bua_outbound_offer_payloads(
    remote_payloads: Tuple[int, ...],
    inbound_payload: int,
    outbound_payload: int,
) -> Tuple[int, ...]:
    if outbound_payload != inbound_payload:
        return (outbound_payload,)
    return remote_payloads


def rtpengine_codec_policy(remote_payloads: Tuple[int, ...], target_payload: int) -> Dict[str, List[str]]:
    target_codec = CODEC_NAMES.get(target_payload)
    if not target_codec:
        return {}

    offered_codecs = [CODEC_NAMES[payload] for payload in remote_payloads if payload in CODEC_NAMES]
    if not offered_codecs or target_codec in offered_codecs:
        return {}

    return {
        "mask": offered_codecs,
        "transcode": [target_codec],
    }


def format_rtpengine_codec_policy(policy: Dict[str, List[str]]) -> str:
    if not policy:
        return "none"
    return " ".join(f"{key}={','.join(values)}" for key, values in sorted(policy.items()))


def codec_payload(codec_name: str) -> int:
    codec = codec_name.upper()
    if codec not in CODEC_PAYLOADS:
        supported = ", ".join(sorted(CODEC_PAYLOADS))
        raise ValueError(f"Unsupported default_codec {codec_name!r}. Supported values: {supported}")
    return CODEC_PAYLOADS[codec]


def format_payloads(payloads: Tuple[int, ...]) -> str:
    return ",".join(CODEC_NAMES.get(payload, str(payload)) for payload in payloads) or "none"


def log_category_for_session_event(event: str) -> str:
    upper = event.upper()
    if upper.startswith("AI "):
        return "ai"
    if upper.startswith(("SIP", "SDP", "INVITE", "BYE", "ACK", "DIALOG")):
        return "sip"
    if "TRANSCOD" in upper:
        return "transcoding"
    if upper.startswith("RTP") or "DTMF" in upper or "BRIDGE" in upper or "B2BUA" in upper or "CALL SUMMARY" in upper:
        return "media"
    if "NETWORK" in upper:
        return "networking"
    return "platform"


def log_category_for_flow_event(event: str) -> str:
    upper = event.upper()
    if "RTPENGINE" in upper or "MEDIA" in upper:
        return "media"
    if "CALL" in upper or "ROUTE" in upper or "FAILURE" in upper:
        return "sip"
    return "platform"


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "call"


def resolve_ai_speech_asset(value: str) -> Optional[Path]:
    if not value:
        return None
    if value.startswith("/scenarios/"):
        relative = value.removeprefix("/")
        for root in (Path("/app/sipp"), Path(__file__).resolve().parent / "sipp", Path.cwd() / "sipp"):
            path = root / relative
            if path.exists():
                return path
    candidate = Path(value)
    roots = []
    if candidate.is_absolute():
        roots.append(Path("/"))
    else:
        roots.extend([Path.cwd(), Path(__file__).resolve().parent])

    for root in roots:
        path = candidate if candidate.is_absolute() else root / candidate
        if path.exists():
            return path
    return candidate if candidate.exists() else None


def parse_dtmf_event(payload: bytes) -> Optional[Tuple[int, str, bool, int]]:
    if len(payload) < 4:
        return None
    event_id = payload[0]
    digit = DTMF_EVENTS.get(event_id, str(event_id))
    is_end = bool(payload[1] & 0x80)
    duration = struct.unpack("!H", payload[2:4])[0]
    return event_id, digit, is_end, duration


def parse_digest_header(value: str) -> Dict[str, str]:
    value = value.strip()
    if value.lower().startswith("digest "):
        value = value[7:].strip()

    fields: Dict[str, str] = {}
    for part in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', value):
        key, _, raw_value = part.strip().partition("=")
        if not key:
            continue
        parsed_value = raw_value.strip()
        if parsed_value.startswith('"') and parsed_value.endswith('"'):
            parsed_value = parsed_value[1:-1]
        fields[key.lower()] = parsed_value
    return fields


def make_digest_response(
    username: str,
    realm: str,
    password: str,
    method: str,
    uri: str,
    nonce: str,
    nc: Optional[str] = None,
    cnonce: Optional[str] = None,
    qop: Optional[str] = None,
) -> str:
    ha1 = md5_hex(f"{username}:{realm}:{password}")
    ha2 = md5_hex(f"{method}:{uri}")
    if qop:
        return md5_hex(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return md5_hex(f"{ha1}:{nonce}:{ha2}")


def md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def make_sdp(
    local_ip: str,
    rtp_port: int,
    payload_type: int,
    dtmf_payload_type: Optional[int] = None,
    payloads: Optional[Tuple[int, ...]] = None,
) -> str:
    offered_payloads = []
    if payloads:
        offered_payloads.extend(payload for payload in payloads if payload in SUPPORTED_CODECS)
    offered_payloads = [payload for payload in offered_payloads if payload != payload_type]
    offered_payloads.insert(0, payload_type)

    if dtmf_payload_type is not None and dtmf_payload_type not in offered_payloads:
        offered_payloads.append(dtmf_payload_type)

    codecs = {
        PCMU: "a=rtpmap:0 PCMU/8000",
        PCMA: "a=rtpmap:8 PCMA/8000",
    }
    lines = [
        "v=0",
        f"o=playsbc {int(time.time())} 1 IN IP4 {local_ip}",
        "s=PlaySBC",
        f"c=IN IP4 {local_ip}",
        "t=0 0",
        f"m=audio {rtp_port} RTP/AVP {' '.join(str(payload) for payload in offered_payloads)}",
        f"a=rtcp:{rtp_port + 1}",
    ]
    for payload in offered_payloads:
        if payload in codecs:
            lines.append(codecs[payload])
        elif payload == dtmf_payload_type:
            lines.append(f"a=rtpmap:{payload} telephone-event/8000")
            lines.append(f"a=fmtp:{payload} 0-16")
    lines.extend(["a=sendrecv", ""])
    return CRLF.join(lines)


def rtpengine_packet_total(response: Dict[str, Any]) -> int:
    return int(response.get("totals", {}).get("RTP", {}).get("packets", 0) or 0)


async def query_rtpengine_until_stable(
    request: Callable[[], Awaitable[Dict[str, Any]]],
    attempts: int = 4,
    delay: float = 0.050,
) -> Tuple[Dict[str, Any], Tuple[int, ...], int]:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    samples: List[int] = []
    response: Dict[str, Any] = {}
    retry_count = 0
    for attempt in range(attempts):
        try:
            response = await request()
        except (asyncio.TimeoutError, OSError, RtpengineError):
            retry_count += 1
            if attempt + 1 >= attempts:
                raise
            await asyncio.sleep(delay * retry_count)
            continue
        samples.append(rtpengine_packet_total(response))
        if len(samples) >= 2 and samples[-1] == samples[-2]:
            break
        if attempt + 1 < attempts:
            await asyncio.sleep(delay)
    return response, tuple(samples), retry_count


def build_sip_request(method: str, request_uri: str, headers: Dict[str, str], body: str = "") -> bytes:
    lines = [f"{method} {request_uri} SIP/2.0"]
    headers_with_length = dict(headers)
    headers_with_length["Content-Length"] = str(len(body.encode("utf-8")))
    lines.extend(f"{name}: {value}" for name, value in headers_with_length.items() if value)
    return (CRLF.join(lines) + CRLF + CRLF + body).encode("utf-8")


def make_call_id() -> str:
    return hashlib.sha1(str(random.random()).encode("ascii")).hexdigest()


def load_config_file(path: Optional[str]) -> ServerConfig:
    config = ServerConfig()
    if not path:
        return config

    config_path = Path(path)
    try:
        config_text = config_path.read_text(encoding="utf-8")
        if config_path.suffix.lower() in {".yaml", ".yml"}:
            raw_config = parse_simple_yaml(config_text)
        else:
            raw_config = json.loads(config_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON config {config_path}: {exc}") from exc

    if not isinstance(raw_config, dict):
        raise ValueError(f"Config {config_path} must contain a JSON object")

    for key, value in raw_config.items():
        if key not in SERVER_CONFIG_KEYS:
            raise ValueError(f"Unknown config key {key!r} in {config_path}")
        setattr(config, key, coerce_config_value(key, value))

    validate_config(config)
    return config


def parse_simple_yaml(text: str) -> Any:
    """Parse the small YAML subset used by PlaySBC config files.

    The project intentionally has no Python package dependency file yet, so
    this keeps `--config server.yaml` usable with only the standard library.
    It is not a general YAML parser.
    """

    lines: List[Tuple[int, str]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
            raise ValueError(f"YAML indentation must use spaces, line {line_number}")
        stripped = raw_line.strip()
        if not stripped or stripped in {"---", "..."}:
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        content = strip_yaml_comment(raw_line[indent:]).strip()
        if content:
            lines.append((indent, content))

    if not lines:
        return {}
    value, index = parse_yaml_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError(f"Could not parse YAML near: {lines[index][1]}")
    return value


def strip_yaml_comment(value: str) -> str:
    quote = ""
    escaped = False
    for index, character in enumerate(value):
        if quote:
            if escaped:
                escaped = False
                continue
            if character == "\\" and quote == '"':
                escaped = True
                continue
            if character == quote:
                quote = ""
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character == "#":
            return value[:index]
    return value


def parse_yaml_block(lines: List[Tuple[int, str]], index: int, indent: int) -> Tuple[Any, int]:
    if index >= len(lines):
        return None, index
    current_indent, content = lines[index]
    if current_indent < indent:
        return None, index
    if content.startswith("- "):
        return parse_yaml_list(lines, index, current_indent)
    return parse_yaml_map(lines, index, current_indent)


def parse_yaml_map(lines: List[Tuple[int, str]], index: int, indent: int) -> Tuple[Dict[str, Any], int]:
    result: Dict[str, Any] = {}
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"Unexpected YAML indentation near: {content}")
        if content.startswith("- "):
            break

        key, raw_value = split_yaml_key_value(content)
        index += 1
        if raw_value:
            raw_value, index = collect_yaml_scalar_continuations(lines, index, current_indent, raw_value)
            result[key] = parse_yaml_scalar(raw_value)
            continue
        if index < len(lines) and lines[index][0] == current_indent and lines[index][1].startswith("- "):
            result[key], index = parse_yaml_list(lines, index, current_indent)
            continue
        if index < len(lines) and lines[index][0] > current_indent:
            result[key], index = parse_yaml_block(lines, index, lines[index][0])
        else:
            result[key] = None
    return result, index


def parse_yaml_list(lines: List[Tuple[int, str]], index: int, indent: int) -> Tuple[List[Any], int]:
    result: List[Any] = []
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"Unexpected YAML indentation near: {content}")
        if not content.startswith("- "):
            break

        item = content[2:].strip()
        index += 1
        key_value = maybe_split_yaml_key_value(item)
        if key_value:
            key, raw_value = key_value
            parsed_item: Dict[str, Any] = {}
            if raw_value:
                parsed_item[key] = parse_yaml_scalar(raw_value)
            elif index < len(lines) and lines[index][0] > current_indent:
                parsed_item[key], index = parse_yaml_block(lines, index, lines[index][0])
            else:
                parsed_item[key] = None
            if index < len(lines) and lines[index][0] > current_indent:
                child, index = parse_yaml_block(lines, index, lines[index][0])
                if isinstance(child, dict):
                    parsed_item.update(child)
                else:
                    raise ValueError(f"YAML list item mapping expected near: {item}")
            result.append(parsed_item)
            continue

        if item:
            item, index = collect_yaml_scalar_continuations(lines, index, current_indent, item)
            result.append(parse_yaml_scalar(item))
            continue
        if index < len(lines) and lines[index][0] > current_indent:
            child, index = parse_yaml_block(lines, index, lines[index][0])
            result.append(child)
        else:
            result.append(None)
    return result, index


def collect_yaml_scalar_continuations(
    lines: List[Tuple[int, str]],
    index: int,
    parent_indent: int,
    value: str,
) -> Tuple[str, int]:
    """Fold Helm/toYaml wrapped plain scalar continuation lines.

    Helm may render long command strings as valid YAML plain scalars split over
    multiple indented lines. PlaySBC only needs the folded form for config
    strings, so continuation lines are joined with spaces while nested lists or
    key/value children are left for the normal block parser.
    """

    parts = [value]
    while index < len(lines):
        continuation_indent, continuation = lines[index]
        if continuation_indent <= parent_indent:
            break
        if continuation.startswith("- ") or maybe_split_yaml_key_value(continuation):
            break
        parts.append(continuation)
        index += 1
    return " ".join(part for part in parts if part), index


def split_yaml_key_value(content: str) -> Tuple[str, str]:
    parsed = maybe_split_yaml_key_value(content)
    if not parsed:
        raise ValueError(f"Expected YAML key/value near: {content}")
    return parsed


def maybe_split_yaml_key_value(content: str) -> Optional[Tuple[str, str]]:
    quote = ""
    escaped = False
    for index, character in enumerate(content):
        if quote:
            if escaped:
                escaped = False
                continue
            if character == "\\" and quote == '"':
                escaped = True
                continue
            if character == quote:
                quote = ""
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character == ":" and (index + 1 == len(content) or content[index + 1].isspace()):
            key = parse_yaml_key(content[:index].strip())
            return key, content[index + 1 :].strip()
    return None


def parse_yaml_key(value: str) -> str:
    parsed = parse_yaml_scalar(value)
    return str(parsed)


def parse_yaml_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"{}", "[]"}:
        return {} if value == "{}" else []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_yaml_scalar(item) for item in split_yaml_inline_items(inner)]
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        if value.startswith('"'):
            return bytes(value[1:-1], "utf-8").decode("unicode_escape")
        return value[1:-1].replace("''", "'")
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "~"}:
        return None
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    if re.fullmatch(r"[-+]?\d+\.\d+", value):
        return float(value)
    return value


def split_yaml_inline_items(value: str) -> List[str]:
    items = []
    quote = ""
    escaped = False
    start = 0
    for index, character in enumerate(value):
        if quote:
            if escaped:
                escaped = False
                continue
            if character == "\\" and quote == '"':
                escaped = True
                continue
            if character == quote:
                quote = ""
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character == ",":
            items.append(value[start:index].strip())
            start = index + 1
    items.append(value[start:].strip())
    return items


def coerce_config_value(key: str, value: Any) -> Any:
    if key == "rtpengine_dtls" and isinstance(value, bool):
        return "off" if not value else "active"
    if key in {"sip_port", "tls_port", "rtp_min", "rtp_max", "health_port", "rtpengine_max_sessions"}:
        return int(value)
    if key == "rtpengine_timeout":
        return float(value)
    if key in {"debug", "b2bua_ladder_logs", "reject_unknown_routes", "tls_verify_peer"}:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if key == "users":
        if not isinstance(value, dict):
            raise ValueError("users must be a JSON object mapping usernames to passwords")
        return {str(username): str(password) for username, password in value.items()}
    if key == "b2bua_routes":
        if not isinstance(value, dict):
            raise ValueError("b2bua_routes must be a JSON object mapping dialed users to SIP URIs")
        return {str(username): str(uri) for username, uri in value.items()}
    if key in {"route_policies", "trunk_groups", "hunt_groups", "number_normalization", "transport_policies"}:
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list of policy objects")
        policies = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError(f"each {key} entry must be a JSON object")
            policies.append(dict(item))
        return tuple(policies)
    if key in {"header_normalization", "call_admission", "media_quality", "ai_voice_gateway", "ha"}:
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be a JSON object")
        return dict(value)
    if key in {"rtpengine_directions", "rtpengine_interfaces", "rtpengine_sdes"}:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, list):
            return tuple(str(item) for item in value)
        raise ValueError(f"{key} must be a string or list of strings")
    if key == "bridge_rooms":
        if isinstance(value, str):
            return (value,)
        if isinstance(value, list):
            return tuple(str(room) for room in value)
        raise ValueError("bridge_rooms must be a string or list of strings")
    if key == "sip_transport":
        return ",".join(parse_sip_transport_set(str(value)))
    if key in {
        "sip_ip",
        "sip_advertised_ip",
        "b2bua_advertised_ip",
        "log_dir",
        "default_codec",
        "auth_realm",
        "media_backend",
        "rtpengine_url",
        "tls_certfile",
        "tls_keyfile",
        "tls_cafile",
        "health_ip",
        "users_file",
        "rtpengine_offer_transport_protocol",
        "rtpengine_answer_transport_protocol",
        "rtpengine_dtls",
    }:
        return str(value)
    return value


def resolve_runtime_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: resolve_runtime_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_runtime_value(item) for item in value]
    if not isinstance(value, str):
        return value
    if value == "pod-ip":
        return os.environ.get("POD_IP", "")
    replacements = {
        "$POD_IP": os.environ.get("POD_IP", ""),
        "${POD_IP}": os.environ.get("POD_IP", ""),
        "$POD_NAME": os.environ.get("POD_NAME", ""),
        "${POD_NAME}": os.environ.get("POD_NAME", ""),
        "$POD_NAMESPACE": os.environ.get("POD_NAMESPACE", ""),
        "${POD_NAMESPACE}": os.environ.get("POD_NAMESPACE", ""),
        "$NODE_NAME": os.environ.get("NODE_NAME", ""),
        "${NODE_NAME}": os.environ.get("NODE_NAME", ""),
    }
    for token, replacement in replacements.items():
        value = value.replace(token, replacement)
    return value


def resolve_runtime_config(config: ServerConfig) -> ServerConfig:
    config.sip_advertised_ip = str(resolve_runtime_value(config.sip_advertised_ip))
    config.b2bua_advertised_ip = str(resolve_runtime_value(config.b2bua_advertised_ip))
    config.rtpengine_url = str(resolve_runtime_value(config.rtpengine_url))
    config.ha = resolve_runtime_value(config.ha)
    config.ai_voice_gateway = resolve_runtime_value(config.ai_voice_gateway)
    return config


def apply_cli_overrides(config: ServerConfig, args: argparse.Namespace) -> ServerConfig:
    overrides = {
        "sip_ip": getattr(args, "sip_ip", None),
        "sip_port": getattr(args, "sip_port", None),
        "tls_port": getattr(args, "tls_port", None),
        "sip_transport": getattr(args, "sip_transport", None),
        "rtp_min": getattr(args, "rtp_min", None),
        "rtp_max": getattr(args, "rtp_max", None),
        "log_dir": getattr(args, "log_dir", None),
        "default_codec": getattr(args, "default_codec", None),
        "auth_realm": getattr(args, "auth_realm", None),
        "media_backend": getattr(args, "media_backend", None),
        "rtpengine_url": getattr(args, "rtpengine_url", None),
        "rtpengine_timeout": getattr(args, "rtpengine_timeout", None),
        "debug": getattr(args, "debug", None),
    }
    for key, value in overrides.items():
        if value is not None:
            setattr(config, key, coerce_config_value(key, value))
    resolve_runtime_config(config)
    validate_config(config)
    return config


def ha_enabled(ha: Dict[str, Any]) -> bool:
    return bool(ha.get("enabled", False))


def ha_node_id(ha: Dict[str, Any]) -> str:
    return str(ha.get("node_id") or "playsbc-a")


def ha_shared_state_path(ha: Dict[str, Any]) -> str:
    return str(ha.get("shared_state_path") or ha.get("state_db") or "")


def ha_rtpengine_pairs(ha: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = ha.get("rtpengine_pairs") or ha.get("rtpengine_nodes") or []
    return [dict(item) for item in raw if isinstance(item, dict)]


def ha_nodes(ha: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = ha.get("nodes") or ha.get("playsbc_nodes") or []
    return [dict(item) for item in raw if isinstance(item, dict)]


def ha_local_node(ha: Dict[str, Any]) -> Dict[str, Any]:
    node_id = ha_node_id(ha)
    for node in ha_nodes(ha):
        if str(node.get("node_id") or node.get("id") or "") == node_id:
            return node
    return {}


def ha_node_draining(ha: Dict[str, Any]) -> bool:
    node = ha_local_node(ha)
    state = str(node.get("state") or ha.get("node_state") or "active").lower()
    return bool(node.get("draining", ha.get("draining", False))) or state == "draining"


def ha_load_balancing_policy(ha: Dict[str, Any]) -> str:
    load_balancing = ha.get("load_balancing") if isinstance(ha.get("load_balancing"), dict) else {}
    return str(load_balancing.get("policy") or ha.get("load_balancing_policy") or "local-preferred")


def ha_rtpengine_session_migration(ha: Dict[str, Any]) -> str:
    failover = ha.get("failover") if isinstance(ha.get("failover"), dict) else {}
    return str(failover.get("rtpengine_session_migration") or ha.get("rtpengine_session_migration") or "planned")


def select_ha_rtpengine_url(ha: Dict[str, Any], default_url: str) -> Tuple[str, str]:
    if not ha_enabled(ha):
        return default_url, ""
    node_id = ha_node_id(ha)
    pairs = ha_rtpengine_pairs(ha)
    for pair in pairs:
        pair_node = str(pair.get("node_id") or pair.get("playsbc_node") or "")
        if pair_node == node_id:
            return str(pair.get("rtpengine_url") or pair.get("url") or default_url), str(pair.get("name") or pair_node)
    if pairs:
        first = pairs[0]
        return str(first.get("rtpengine_url") or first.get("url") or default_url), str(first.get("name") or "default")
    return default_url, ""


def validate_config(config: ServerConfig) -> None:
    config.default_codec = config.default_codec.upper()
    codec_payload(config.default_codec)
    config.sip_transport = ",".join(parse_sip_transport_set(config.sip_transport))
    if config.sip_port <= 0 or config.sip_port > 65535:
        raise ValueError("sip_port must be between 1 and 65535")
    if config.tls_port <= 0 or config.tls_port > 65535:
        raise ValueError("tls_port must be between 1 and 65535")
    if config.health_port <= 0 or config.health_port > 65535:
        raise ValueError("health_port must be between 1 and 65535")
    if config.rtp_min <= 0 or config.rtp_max > 65535 or config.rtp_min > config.rtp_max:
        raise ValueError("RTP port range must be within 1-65535 and rtp_min must be <= rtp_max")
    if not config.auth_realm:
        raise ValueError("auth_realm must not be empty")
    config.media_backend = config.media_backend.lower()
    if config.media_backend not in MEDIA_BACKENDS:
        raise ValueError(f"media_backend must be one of {', '.join(sorted(MEDIA_BACKENDS))}")
    if config.rtpengine_timeout <= 0:
        raise ValueError("rtpengine_timeout must be greater than 0")
    if config.media_backend == "rtpengine":
        parse_rtpengine_url(config.rtpengine_url)
    ai_config = AiVoiceConfig.from_dict(config.ai_voice_gateway)
    config.ai_voice_gateway = ai_config.to_dict()
    if ai_config.enabled:
        if ai_config.provider != "rasa":
            raise ValueError("ai_voice_gateway.provider must be rasa")
        if not ai_config.rasa_webhook_url.startswith(("http://", "https://")):
            raise ValueError("ai_voice_gateway.rasa_webhook_url must be an HTTP or HTTPS URL")
        if ai_config.rasa_timeout <= 0:
            raise ValueError("ai_voice_gateway.rasa_timeout must be greater than zero")
        if ai_config.stt_provider not in {"lab-scripted", "scripted", "whisper", "vosk"}:
            raise ValueError("ai_voice_gateway.stt_provider must be lab-scripted, whisper, or vosk")
        if ai_config.tts_provider not in {"text-only", "lab-text", "piper", "coqui"}:
            raise ValueError("ai_voice_gateway.tts_provider must be text-only, piper, or coqui")
        if ai_config.speech_input_codec not in {"PCMU", "PCMA"}:
            raise ValueError("ai_voice_gateway.speech_input_codec must be PCMU or PCMA")
        if ai_config.tts_output_codec not in {"PCMU", "PCMA"}:
            raise ValueError("ai_voice_gateway.tts_output_codec must be PCMU or PCMA")
        if ai_config.response_mode not in {"rest", "callback", "streaming"}:
            raise ValueError("ai_voice_gateway.response_mode must be rest, callback, or streaming")
    if config.ha:
        config.ha = dict(config.ha)
    if ha_enabled(config.ha):
        node_id = ha_node_id(config.ha)
        if not node_id:
            raise ValueError("ha.node_id must not be empty when HA is enabled")
        shared_path = ha_shared_state_path(config.ha)
        if not shared_path:
            raise ValueError("ha.shared_state_path is required when HA is enabled")
        for node in ha_nodes(config.ha):
            node_name = str(node.get("node_id") or node.get("id") or "")
            node_state = str(node.get("state") or "active").lower()
            if not node_name:
                raise ValueError("each ha.nodes entry requires node_id")
            if node_state not in {"active", "standby", "draining", "down"}:
                raise ValueError("ha.nodes state must be active, standby, draining, or down")
        if ha_load_balancing_policy(config.ha) not in {"local-preferred", "round-robin", "least-calls", "external-lb"}:
            raise ValueError("ha.load_balancing.policy must be local-preferred, round-robin, least-calls, or external-lb")
        for pair in ha_rtpengine_pairs(config.ha):
            pair_node = str(pair.get("node_id") or pair.get("playsbc_node") or "")
            pair_url = str(pair.get("rtpengine_url") or pair.get("url") or "")
            if not pair_node:
                raise ValueError("each ha.rtpengine_pairs entry requires node_id")
            if pair_url:
                parse_rtpengine_url(pair_url)
    if len(config.rtpengine_directions) not in {0, 2}:
        raise ValueError("rtpengine_directions must contain exactly two interface names")
    if any(not direction.strip() for direction in config.rtpengine_directions):
        raise ValueError("rtpengine_directions interface names must not be empty")
    if any(not interface.strip() for interface in config.rtpengine_interfaces):
        raise ValueError("rtpengine_interfaces names must not be empty")
    if config.rtpengine_max_sessions < -1:
        raise ValueError("rtpengine_max_sessions must be -1 (unlimited) or greater")
    supported_media_transports = {"", "RTP/AVP", "RTP/SAVP", "RTP/AVPF", "RTP/SAVPF"}
    for media_transport in (
        config.rtpengine_offer_transport_protocol,
        config.rtpengine_answer_transport_protocol,
    ):
        if media_transport not in supported_media_transports:
            raise ValueError(f"Unsupported RTPengine media transport protocol {media_transport!r}")
    if config.rtpengine_dtls not in {"", "off", "no", "disable", "active", "passive"}:
        raise ValueError(f"Unsupported RTPengine DTLS policy {config.rtpengine_dtls!r}")
    if "tls" in parse_sip_transport_set(config.sip_transport):
        if not config.tls_certfile or not config.tls_keyfile:
            raise ValueError("tls_certfile and tls_keyfile are required when SIP TLS is enabled")
    for user, route_uri in config.b2bua_routes.items():
        if not user:
            raise ValueError("b2bua_routes keys must not be empty")
        parse_sip_uri(route_uri)
    for policy_config in config.route_policies:
        policy = RoutePolicy.from_config(policy_config)
        if not policy.name:
            raise ValueError("route policy name must not be empty")
        if (
            policy.target.lower() not in RoutingEngine.REGISTRATION_TARGETS
            and not policy.target.lower().startswith(("trunk-group:", "hunt-group:"))
            and not policy.target.lower().startswith(RoutingEngine.AI_GATEWAY_PREFIXES)
        ):
            parse_sip_uri(format_route_target(policy.target, "test-user"))
    engine = RoutingEngine(
        config.route_policies,
        config.b2bua_routes,
        config.trunk_groups,
        config.hunt_groups,
        config.number_normalization,
        config.header_normalization,
        config.transport_policies,
        config.call_admission,
    )
    referenced_groups = [
        policy.target.split(":", 1)[1].strip()
        for policy in engine.policies
        if policy.target.lower().startswith(("trunk-group:", "hunt-group:"))
    ]
    known_groups = set(engine.trunk_groups) | set(engine.hunt_groups)
    missing = sorted(set(referenced_groups) - known_groups)
    if missing:
        raise ValueError(f"route policies reference unknown groups: {', '.join(missing)}")


def resolve_log_dir(config: ServerConfig) -> Optional[Path]:
    return Path(config.log_dir) if config.log_dir else None


def load_secret_users(path: str) -> Dict[str, str]:
    if not path:
        return {}
    secret_path = Path(path)
    text = secret_path.read_text(encoding="utf-8")
    payload = parse_simple_yaml(text) if secret_path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if isinstance(payload, dict) and isinstance(payload.get("users"), dict):
        payload = payload["users"]
    if not isinstance(payload, dict):
        raise ValueError(f"SIP users file {secret_path} must contain a username/password object")
    return {str(username): str(password) for username, password in payload.items()}


def create_tls_contexts(config: ServerConfig) -> Tuple[Optional[ssl.SSLContext], Optional[ssl.SSLContext]]:
    if "tls" not in parse_sip_transport_set(config.sip_transport):
        return None, None
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.minimum_version = ssl.TLSVersion.TLSv1_2
    server_context.load_cert_chain(config.tls_certfile, config.tls_keyfile)
    if config.tls_verify_peer:
        server_context.verify_mode = ssl.CERT_REQUIRED
        if config.tls_cafile:
            server_context.load_verify_locations(config.tls_cafile)

    client_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=config.tls_cafile or None)
    client_context.minimum_version = ssl.TLSVersion.TLSv1_2
    client_context.load_cert_chain(config.tls_certfile, config.tls_keyfile)
    if not config.tls_verify_peer:
        client_context.check_hostname = False
        client_context.verify_mode = ssl.CERT_NONE
    return server_context, client_context


async def handle_health_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    protocol: SipServerProtocol,
) -> None:
    try:
        request_line = (await asyncio.wait_for(reader.readline(), timeout=2.0)).decode("ascii", errors="replace")
        path = request_line.split(" ", 2)[1] if len(request_line.split(" ", 2)) >= 2 else "/healthz"
        if path == "/metrics":
            body = render_prometheus_metrics(protocol.prometheus_samples())
        else:
            body = "ready\n" if path == "/readyz" else "ok\n"
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain; version=0.0.4\r\n"
            f"Content-Length: {len(body.encode('utf-8'))}\r\n"
            "Connection: close\r\n\r\n"
            + body
        )
        writer.write(response.encode("utf-8"))
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def main() -> None:
    parser = argparse.ArgumentParser(description="Small SIP/RTP call server")
    parser.add_argument("--config", help="Path to a JSON or YAML config file")
    parser.add_argument("--ip", dest="sip_ip", help="IP address to bind and advertise")
    parser.add_argument("--sip-port", type=int, help="SIP port")
    parser.add_argument("--sip-transport", help="SIP transport to listen on: udp, tcp, tls, or a comma-separated set")
    parser.add_argument("--tls-port", type=int, help="SIP TLS listener port")
    parser.add_argument("--rtp-min", type=int, help="First RTP UDP port")
    parser.add_argument("--rtp-max", type=int, help="Last RTP UDP port")
    parser.add_argument("--log-dir", help="Directory for per-call log files")
    parser.add_argument("--default-codec", type=str.upper, choices=sorted(CODEC_PAYLOADS), help="Preferred answer codec")
    parser.add_argument("--auth-realm", help="SIP digest authentication realm")
    parser.add_argument("--media-backend", choices=sorted(MEDIA_BACKENDS), help="B2BUA media backend")
    parser.add_argument("--rtpengine-url", help="RTPengine NG control URL, for example udp://127.0.0.1:2223")
    parser.add_argument("--rtpengine-timeout", type=float, help="RTPengine control timeout in seconds")
    parser.add_argument("--debug", action="store_true", default=None, help="Enable debug logging")
    args = parser.parse_args()

    try:
        config = apply_cli_overrides(load_config_file(args.config), args)
        if config.users_file:
            config.users.update(load_secret_users(config.users_file))
        tls_server_context, tls_client_context = create_tls_contexts(config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    logging.basicConfig(
        level=logging.DEBUG if config.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    log_dir = resolve_log_dir(config)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.info("Writing call logs under %s", log_dir)
    else:
        logging.info("Persistent call logs disabled")
    sbc_logger = SbcLogger(log_dir)
    sbc_logger.platform(
        "SERVER CONFIG",
        (
            f"sip_bind={config.sip_ip}:{config.sip_port} "
            f"sip_advertised={config.sip_advertised_ip or config.sip_ip}:{config.sip_port} "
            f"b2bua_advertised={config.b2bua_advertised_ip or config.sip_advertised_ip or config.sip_ip}:{config.sip_port} "
            f"sip_transport={config.sip_transport} rtp_range={config.rtp_min}-{config.rtp_max} "
            f"media_backend={config.media_backend}"
        ),
    )

    media = MediaServer(
        config.sip_ip,
        config.rtp_min,
        config.rtp_max,
        log_dir,
        sbc_logger,
        config.media_quality,
    )
    rtpengine_client = None
    if config.media_backend == "rtpengine":
        selected_url, selected_pair = select_ha_rtpengine_url(config.ha, config.rtpengine_url)
        if selected_url != config.rtpengine_url:
            config.rtpengine_url = selected_url
        if selected_pair:
            sbc_logger.platform(
                "HA RTPENGINE PAIR SELECTED",
                f"cluster={config.ha.get('cluster_id', 'playsbc-lab')} node={ha_node_id(config.ha)} pair={selected_pair} url={config.rtpengine_url}",
            )
        rtpengine_client = RtpengineClient(config.rtpengine_url, timeout=config.rtpengine_timeout)
        logging.info("Using RTPengine media backend at %s", config.rtpengine_url)
        sbc_logger.platform("RTPENGINE BACKEND ENABLED", f"url={config.rtpengine_url} timeout={config.rtpengine_timeout}")
    if config.ai_voice_gateway.get("enabled"):
        sbc_logger.ai(
            "AI VOICE GATEWAY ENABLED",
            (
                f"provider={config.ai_voice_gateway.get('provider')} "
                f"bot={config.ai_voice_gateway.get('bot_name')} "
                f"input_mode={config.ai_voice_gateway.get('input_mode')} "
                f"rasa_webhook={config.ai_voice_gateway.get('rasa_webhook_url')}"
            ),
        )
    sip_protocol = SipServerProtocol(
        config.sip_ip,
        config.sip_port,
        media,
        sbc_logger,
        config.default_payload,
        config.auth_realm,
        config.users,
        config.bridge_rooms,
        config.b2bua_routes,
        config.route_policies,
        config.b2bua_ladder_logs,
        trunk_groups=config.trunk_groups,
        hunt_groups=config.hunt_groups,
        number_normalization=config.number_normalization,
        header_normalization=config.header_normalization,
        transport_policies=config.transport_policies,
        call_admission=config.call_admission,
        media_backend=config.media_backend,
        rtpengine_client=rtpengine_client,
        reject_unknown_routes=config.reject_unknown_routes,
        sip_transport=config.sip_transport,
        sip_advertised_ip=config.sip_advertised_ip,
        b2bua_advertised_ip=config.b2bua_advertised_ip,
        rtpengine_directions=config.rtpengine_directions,
        rtpengine_interfaces=config.rtpengine_interfaces,
        rtpengine_max_sessions=config.rtpengine_max_sessions,
        rtpengine_offer_transport_protocol=config.rtpengine_offer_transport_protocol,
        rtpengine_answer_transport_protocol=config.rtpengine_answer_transport_protocol,
        rtpengine_sdes=config.rtpengine_sdes,
        rtpengine_dtls=config.rtpengine_dtls,
        ai_voice_gateway=config.ai_voice_gateway,
        ha=config.ha,
        tls_client_context=tls_client_context,
        tls_port=config.tls_port,
    )
    loop = asyncio.get_running_loop()
    sip_transports = parse_sip_transport_set(config.sip_transport)
    sip_listeners: List[Any] = []
    if "udp" in sip_transports:
        udp_transport, _protocol = await loop.create_datagram_endpoint(
            lambda: sip_protocol,
            local_addr=(config.sip_ip, config.sip_port),
        )
        sip_listeners.append(udp_transport)
    if "tcp" in sip_transports:
        tcp_server = await loop.create_server(
            lambda: SipTcpConnectionProtocol(sip_protocol, "tcp"),
            config.sip_ip,
            config.sip_port,
        )
        sip_listeners.append(tcp_server)
        sip_protocol.tcp_server_started()

    if "tls" in sip_transports:
        tls_server = await loop.create_server(
            lambda: SipTcpConnectionProtocol(sip_protocol, "tls"),
            config.sip_ip,
            config.tls_port,
            ssl=tls_server_context,
        )
        sip_listeners.append(tls_server)
        sip_protocol.tls_server_started()

    health_server = await asyncio.start_server(
        lambda reader, writer: handle_health_request(reader, writer, sip_protocol),
        config.health_ip,
        config.health_port,
    )
    sip_listeners.append(health_server)
    sbc_logger.platform("HEALTH SERVER STARTED", f"local={config.health_ip}:{config.health_port}")
    sip_protocol.start_background_tasks()

    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
