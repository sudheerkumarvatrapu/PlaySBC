#!/usr/bin/env python3
"""Run PlaySBC SIPp smoke and B2BUA regressions, then write an HTML report."""

from __future__ import annotations

import argparse
import base64
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_B2BUA_PROFILES = (
    "basic-signalling",
    "basic-media",
    "transcoding",
    "registered-inbound",
    "registered-outbound",
)
ALL_B2BUA_PROFILES = (
    "basic-signalling",
    "protocol-core-layer1-live-call",
    "protocol-core-layer2-live-tcp-call",
    "protocol-core-layer2-live-tls-call",
    "protocol-core-layer2-live-dns-srv-call",
    "protocol-core-layer2-live-dns-udp-call",
    "protocol-core-layer2-live-rport-call",
    "protocol-core-layer2-live-idle-timeout",
    "protocol-core-layer2-live-half-close",
    "protocol-core-layer2-live-pool-limit",
    "protocol-core-layer2-live-tls-sni",
    "protocol-core-layer2-live-tls-rotation",
    "protocol-core-layer3-live-client-transactions",
    "protocol-core-layer3-live-non2xx-ack",
    "protocol-core-layer3-live-cancel",
    "protocol-core-layer3-live-retransmission",
    "protocol-core-layer3-live-transport-error",
    "protocol-core-layer4-live-route-set",
    "protocol-core-layer4-live-strict-route",
    "protocol-core-layer4-live-prack",
    "protocol-core-layer4-live-session-timer",
    "protocol-core-layer4-live-session-expiry",
    "protocol-core-layer4-live-min-se",
    "protocol-core-layer4-live-update-target",
    "protocol-core-layer4-live-update-offer",
    "protocol-core-layer4-live-fork-cleanup",
    "protocol-core-layer4-live-update-glare",
    "protocol-core-layer5-live-multi-contact",
    "protocol-core-layer5-live-wildcard-expiry",
    "protocol-core-layer5-live-path-outbound",
    "protocol-core-layer5-live-max-forwards",
    "protocol-core-layer5-live-digest-replay",
    "protocol-core-layer6-live-options-storm",
    "protocol-core-layer6-live-register-storm",
    "protocol-core-layer6-live-source-limit",
    "protocol-core-layer6-live-priority-bypass",
    "protocol-core-layer6-live-recovery",
    "evidence-b2bua-two-leg-pcap",
    "basic-media",
    "transcoding",
    "rtpengine",
    "rtpengine-media",
    "rtpengine-transcoding",
    "tcp-rtpengine-transcoding",
    "real-topology-rtpengine-transcoding",
    "registered-inbound",
    "registered-outbound",
    "register-auth-success",
    "register-auth-failure",
    "register-auth-tcp",
    "register-auth-tls",
    "dtmf-rfc4733",
    "ai-rasa-lab",
    "ai-rasa-rtpengine",
    "ai-rasa-real-lab",
    "ai-rasa-rtpengine-speech",
    "ai-rasa-rtpengine-speech-whisper",
    "ai-rasa-long-response-streaming",
    "ai-rasa-contact-center-sales",
    "ai-rasa-contact-center-sales-coqui",
    "invalid-bye",
    "unknown-route",
    "failed-outbound",
    "cancel",
    "retransmission",
    "esbc-options-keepalive",
    "esbc-static-trunk-route",
    "esbc-e164-route-policy",
    "esbc-trunk-failure",
    "esbc-trunk-failover",
    "esbc-header-normalization",
    "esbc-e164-normalization",
    "esbc-hunt-group",
    "esbc-call-admission",
    "esbc-trunk-metrics",
    "ha-shared-state-rtpengine",
    "ha-options-health-recovery",
    "rfc5359-call-hold-resume",
    "rfc5359-call-hold-resume-rtpengine",
    "rfc5359-call-hold-resume-tcp",
    "rfc5359-call-hold-resume-tls",
    "rfc5359-unattended-transfer",
    "rfc5359-unconditional-forwarding",
    "rfc5359-forwarding-on-busy",
    "rfc5359-forwarding-on-no-answer",
    "rfc5359-unattended-transfer-rtpengine",
    "rfc5359-unconditional-forwarding-rtpengine",
    "rfc5359-forwarding-on-busy-rtpengine",
    "rfc5359-forwarding-on-no-answer-rtpengine",
    "ha-node-draining",
    "ha-playsbc-precall-failover",
    "ha-playsbc-midcall-failover",
    "ha-playsbc-postcall-failover",
    "ha-rtpengine-precall-failover",
    "ha-rtpengine-midcall-recovery",
    "ha-node-drain-active-calls",
    "ha-active-active-load-distribution",
    "ha-shared-registrar-dialog-restore",
    "tls-transport-policy",
    "tcp-connection-reuse",
    "tcp-connection-failure",
    "rtpengine-control-failure",
    "rtpengine-port-exhaustion",
    "rtpengine-interface-failure",
    "rtcp-receiver-quality",
    "tls-srtp-to-udp-rtp",
    "tls-srtp-to-tcp-rtp",
    "udp-rtp-to-tls-srtp",
    "small-load-2cps-10s",
    "soak-1cps-30s",
    "load-5cps-60s",
    "load-5cps-60s-rtpengine-transcoding",
)
RASA_B2BUA_PROFILES = (
    "ai-rasa-lab",
    "ai-rasa-rtpengine",
    "ai-rasa-real-lab",
    "ai-rasa-rtpengine-speech",
    "ai-rasa-rtpengine-speech-whisper",
    "ai-rasa-long-response-streaming",
    "ai-rasa-contact-center-sales",
    "ai-rasa-contact-center-sales-coqui",
)
RASA_NLU_PROFILES = (
    "ai-rasa-chat-nlu",
    "ai-rasa-chat-negative",
)
RASA_TEST_PROFILES = (*RASA_B2BUA_PROFILES, *RASA_NLU_PROFILES)
RASA_TEST_FLOWS = {
    "ai-rasa-lab": {
        "title": "Mock Rasa REST",
        "proves": "AI route and Rasa adapter sanity.",
        "flow": "K8s Runner -> profile config -> SIPp A -> PlaySBC AI callee -> scripted STT/media -> Mock Rasa REST -> log.ai -> HTML Report.",
        "evidence": "log.ai, SIP/media logs, mock ladder.",
    },
    "ai-rasa-rtpengine": {
        "title": "Mock Rasa + RTPengine",
        "proves": "AI call with RTPengine media anchor.",
        "flow": "K8s Runner -> profile config -> SIPp A -> PlaySBC -> RTPengine -> Mock Rasa REST/action -> RTPengine evidence -> HTML Report.",
        "evidence": "RTPengine query, log.ai, media log.",
    },
    "ai-rasa-real-lab": {
        "title": "Real Rasa Pod",
        "proves": "Real Rasa deploy/train/webhook path.",
        "flow": "K8s Runner -> Helm/Rasa config -> Real Rasa Pod train/start -> SIPp A -> PlaySBC/RTPengine -> Rasa webhook -> HTML Report.",
        "evidence": "Rasa rollout logs, log.ai, SIP/media logs.",
    },
    "ai-rasa-rtpengine-speech": {
        "title": "Real Speech STT/TTS",
        "proves": "G.711 speech to STT/Rasa/TTS.",
        "flow": "K8s Runner -> SIPp A speech PCAP -> RTPengine -> PlaySBC WAV decode -> Vosk STT -> Real Rasa -> Piper TTS -> RTP prompt/WAV evidence -> HTML Report.",
        "evidence": "WAV players, RTPengine evidence, AI ladder.",
    },
    "ai-rasa-rtpengine-speech-whisper": {
        "title": "Whisper STT Speech Variant",
        "proves": "Whisper adapter compatibility beside Vosk.",
        "flow": "K8s Runner -> SIPp A speech PCAP -> RTPengine -> PlaySBC WAV decode -> Whisper STT adapter -> Real Rasa -> Piper TTS -> RTP prompt/WAV evidence -> HTML Report.",
        "evidence": "log.ai provider=whisper, WAV/RTP prompt artifacts, RTPengine evidence.",
    },
    "ai-rasa-long-response-streaming": {
        "title": "Long Bot Response Streaming",
        "proves": "Long bot responses are split into ordered TTS chunks.",
        "flow": "K8s Runner -> SIPp A speech PCAP -> RTPengine -> PlaySBC -> Real Rasa long response -> chunked Piper TTS -> per-chunk RTP prompt evidence -> HTML Report.",
        "evidence": "AI TTS STREAM START/CHUNK logs, chunked WAV/RTP prompt artifacts, AI ladder.",
    },
    "ai-rasa-contact-center-sales": {
        "title": "Contact-Center Sales Bot",
        "proves": "Virtual SIPp B bot-agent sales flow.",
        "flow": "K8s Runner -> SIPp A -> PlaySBC virtual SIPp B Bot Agent -> RTPengine -> Vosk STT -> Real Rasa sales workflow -> Piper TTS -> HTML Report.",
        "evidence": "Speech WAVs, contact-center ladder, log.ai.",
    },
    "ai-rasa-contact-center-sales-coqui": {
        "title": "Contact-Center Sales Bot + Coqui",
        "proves": "Coqui adapter compatibility beside Piper.",
        "flow": "K8s Runner -> SIPp A -> PlaySBC virtual SIPp B Bot Agent -> RTPengine -> Vosk STT -> Real Rasa sales workflow -> Coqui TTS -> RTP prompt/WAV evidence -> HTML Report.",
        "evidence": "log.ai renderer=coqui, speech WAVs, RTP prompt artifacts, contact-center ladder.",
    },
    "ai-rasa-chat-nlu": {
        "title": "Chat Intent Matrix",
        "proves": "Positive chat intent routing.",
        "flow": "Chat YAML -> K8s Runner -> PlaySBC Guard -> Rasa NLU /model/parse -> Rasa Bot Webhook -> JSON verdict/chat window -> HTML Report.",
        "evidence": "rasa-nlu-results.json, log.rasa-nlu, Rasa chat window, NLP Chat/Rasa ladder.",
    },
    "ai-rasa-chat-negative": {
        "title": "Negative Chat / Guardrails",
        "proves": "Negative chat and safety guardrails.",
        "flow": "Negative Chat YAML -> K8s Runner -> PlaySBC no-input/language guards -> Rasa NLU/webhook when valid -> JSON verdict/guardrail chat window -> HTML Report.",
        "evidence": "rasa-nlu-results.json, log.rasa-nlu, guardrail chat window, NLP ladder.",
    },
}
OPTIONAL_B2BUA_PROFILES: tuple[str, ...] = ()
SELECTABLE_B2BUA_PROFILES = ALL_B2BUA_PROFILES
RTPENGINE_B2BUA_PROFILES = (
    "rtpengine",
    "rtpengine-media",
    "rtpengine-transcoding",
    "tcp-rtpengine-transcoding",
    "ai-rasa-rtpengine",
    "ai-rasa-real-lab",
    "ai-rasa-rtpengine-speech",
    "ai-rasa-rtpengine-speech-whisper",
    "ai-rasa-long-response-streaming",
    "ai-rasa-contact-center-sales",
    "ai-rasa-contact-center-sales-coqui",
    "rtpengine-control-failure",
    "rtpengine-port-exhaustion",
    "rtpengine-interface-failure",
    "tls-srtp-to-udp-rtp",
    "tls-srtp-to-tcp-rtp",
    "udp-rtp-to-tls-srtp",
    "ha-shared-state-rtpengine",
    "rfc5359-call-hold-resume-rtpengine",
    "rfc5359-unattended-transfer-rtpengine",
    "rfc5359-unconditional-forwarding-rtpengine",
    "rfc5359-forwarding-on-busy-rtpengine",
    "rfc5359-forwarding-on-no-answer-rtpengine",
    "ha-playsbc-precall-failover",
    "ha-playsbc-midcall-failover",
    "ha-playsbc-postcall-failover",
    "ha-rtpengine-precall-failover",
    "ha-rtpengine-midcall-recovery",
    "ha-node-drain-active-calls",
    "ha-active-active-load-distribution",
    "ha-shared-registrar-dialog-restore",
    "load-5cps-60s-rtpengine-transcoding",
)
REAL_TOPOLOGY_PROFILE = "real-topology-rtpengine-transcoding"
B2BUA_LOG_FILES = (
    "log.sip",
    "log.media",
    "log.transcoding",
    "log.ai",
    "log.platform",
    "log.networking",
    "log.udp",
    "log.tcp",
    "log.tls",
    "log.call",
    "log.sipp",
    "sipmsg.log",
)
ROBOT_PHASE_PREFIX = "ROBOT_PHASE_JSON="
ROBOT_PHASE_ORDER = (
    "Setup Preparation",
    "Configuration",
    "Test Setup",
    "Test Execution",
    "Test Teardown",
    "Evidence Validation",
)
AUDIO_EVIDENCE_PREFIXES = ("ai-speech-input", "ai-tts-output")
AUDIO_EMBED_MAX_BYTES = 2_000_000
REPORT_ARTIFACTS = (
    ("Setup values", "helm-profile-values.yaml"),
    ("Helm execution", "helm-profile-upgrade.log"),
    ("Pod snapshot", "kubectl-pods.log"),
    ("StatefulSet snapshot", "kubectl-statefulsets.log"),
    ("Service snapshot", "kubectl-services.log"),
    ("Kubernetes events", "kubectl-events.log"),
    ("Platform lifecycle", "log.platform"),
    ("SIPp execution", "log.sipp"),
    ("SIP decisions", "log.sip"),
    ("SIP messages", "sipmsg.log"),
    ("Call decisions", "log.call"),
    ("Media decisions", "log.media"),
    ("AI decisions", "log.ai"),
    ("Network evidence", "log.networking"),
    ("UDP transport", "log.udp"),
    ("TCP transport", "log.tcp"),
    ("TLS transport", "log.tls"),
    ("Transcoding evidence", "log.transcoding"),
    ("PlaySBC workload", "playsbc.log"),
    ("RTPengine workload", "rtpengine.log"),
    ("Rasa workload", "rasa.log"),
    ("Combined packet capture", "capture.pcap"),
    ("PCAP leg certification", "pcap-legs.json"),
    ("Rasa NLU results", "rasa-nlu-results.json"),
)
TEXT_REPORT_ARTIFACT_SUFFIXES = {
    ".call",
    ".csv",
    ".json",
    ".log",
    ".media",
    ".networking",
    ".platform",
    ".sip",
    ".sipp",
    ".stats",
    ".transcoding",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
PHASE_ARTIFACTS = {
    "Setup Preparation": ("helm-profile-values.yaml", "kubectl-services.log"),
    "Configuration": ("helm-profile-upgrade.log", "helm-profile-values.yaml"),
    "Test Setup": ("kubectl-pods.log", "kubectl-statefulsets.log", "kubectl-services.log"),
    "Test Execution": (
        "log.sipp", "sipmsg.log", "log.sip", "log.call", "log.ai",
        "log.udp", "log.tcp", "log.tls",
    ),
    "Test Teardown": ("kubectl-events.log", "log.platform"),
    "Metrics Scrape Settle": ("log.platform",),
    "Evidence Validation": ("log.platform", "log.media", "capture.pcap", "pcap-legs.json"),
}


@dataclass
class ReportPhase:
    name: str
    status: str
    duration_seconds: float
    detail: str


@dataclass
class ReportRow:
    suite: str
    name: str
    status: str
    returncode: Optional[int]
    duration_seconds: float
    log_path: str
    command: str
    phases: List[ReportPhase] = field(default_factory=list)
    sip_ladder: str = ""


@dataclass
class AudioEvidence:
    label: str
    path: str
    src: str
    file_src: str
    embedded: bool = False


def make_run_id() -> str:
    return time.strftime("regression-%Y%m%d-%H%M%S", time.localtime())


def audio_evidence_label(path: Path) -> str:
    name = path.name.lower()
    if name.startswith("ai-speech-input"):
        return "Caller speech input"
    if name.startswith("ai-tts-output"):
        return "Piper TTS output"
    return "Audio evidence"


def audio_src_for_report(path: Path, report_dir: Optional[Path]) -> str:
    try:
        if report_dir:
            source = os.path.relpath(path.resolve(), report_dir.resolve()).replace(os.sep, "/")
        else:
            source = path.resolve().as_uri()
    except (OSError, ValueError):
        source = str(path)
    return urllib.parse.quote(source, safe="/:._~%-")


def static_evidence_viewer(path: Path, report_dir: Path) -> str:
    raw_href = f"{urllib.parse.quote(path.name, safe='._~%-')}?raw=1"
    back_href = urllib.parse.quote(
        os.path.relpath(report_dir / "latest.html", path.parent).replace(os.sep, "/"),
        safe="/:._~%-",
    )
    size = path.stat().st_size
    if path.suffix.lower() in TEXT_REPORT_ARTIFACT_SUFFIXES:
        content = f"<pre>{html.escape(path.read_text(encoding='utf-8', errors='replace'))}</pre>"
    else:
        content = (
            "<p>This binary evidence cannot be rendered as text in a browser. "
            "Use the download link above to open it with the appropriate application.</p>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(path.name)}</title>
  <style>
    body {{ margin: 0; color: #202b33; background: #f5f8fa; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ position: sticky; top: 0; display: flex; gap: 18px; padding: 14px 24px; background: #16324f; color: white; }}
    header a {{ color: #d9efff; text-decoration: none; }}
    main {{ max-width: 1180px; margin: 20px auto; padding: 0 20px 40px; }}
    .meta {{ color: #5a6975; overflow-wrap: anywhere; }}
    pre {{ padding: 18px; border: 1px solid #c9d4dd; background: white; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.5 SFMono-Regular, Consolas, monospace; }}
  </style>
</head>
<body>
  <header><a href="{back_href}">Back to report</a><a href="{raw_href}" download>Download raw file</a><span>{html.escape(path.name)}</span></header>
  <main><p class="meta"><strong>Evidence file:</strong> {html.escape(path.name)}<br><strong>Size:</strong> {size} bytes</p>{content}</main>
</body>
</html>
"""


def stage_evidence_for_report(
    path: Path,
    bundle_root: Path,
    report_dir: Optional[Path],
    *,
    with_viewer: bool = False,
) -> str:
    """Copy linked evidence beside the HTML report so file:// links stay usable."""
    if report_dir is None:
        return audio_src_for_report(path, report_dir)

    try:
        relative_path = path.resolve().relative_to(bundle_root.resolve())
        bundle_name = re.sub(r"[^A-Za-z0-9._-]+", "_", bundle_root.name).strip("._") or "bundle"
        evidence_root = report_dir / "evidence"
        target = evidence_root / bundle_name / relative_path
        target.resolve().relative_to(evidence_root.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() != path.resolve():
            shutil.copy2(path, target)
        linked_target = target
        if with_viewer:
            linked_target = target.with_name(f"{target.name}.html")
            linked_target.write_text(static_evidence_viewer(target, report_dir), encoding="utf-8")
        source = linked_target.relative_to(report_dir).as_posix()
    except (OSError, ValueError):
        return audio_src_for_report(path, report_dir)
    return urllib.parse.quote(source, safe="/:._~%-")


def embedded_audio_src(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > AUDIO_EMBED_MAX_BYTES:
            return None
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:audio/wav;base64,{encoded}"


def evidence_bundle_candidates(log_path: str, report_dir: Optional[Path] = None) -> List[Path]:
    path = Path(log_path)
    candidates = [path]
    if report_dir is not None:
        run_root = report_dir.parent
        bundle_name = path.name
        candidates.extend(
            [
                run_root / bundle_name,
                run_root / "RASA-Regression" / bundle_name,
                run_root / "k8s-Regression" / bundle_name,
                run_root / "b2bua-Regression" / bundle_name,
            ]
        )
    unique: List[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique


def resolve_evidence_bundle(log_path: str, report_dir: Optional[Path] = None) -> Optional[Path]:
    return next(
        (candidate for candidate in evidence_bundle_candidates(log_path, report_dir) if candidate.is_dir()),
        None,
    )


def discover_report_artifacts(log_path: str, report_dir: Optional[Path] = None) -> List[dict]:
    root = resolve_evidence_bundle(log_path, report_dir)
    if root is None:
        return []
    artifacts = []
    for label, name in REPORT_ARTIFACTS:
        path = root / name
        if not path.is_file():
            continue
        artifacts.append(
            {
                "label": label,
                "name": name,
                "path": str(path),
                "href": stage_evidence_for_report(path, root, report_dir, with_viewer=True),
                "bytes": path.stat().st_size,
            }
        )
    return artifacts


def discover_audio_evidence(log_path: str, report_dir: Optional[Path] = None) -> List[AudioEvidence]:
    root = next((candidate for candidate in evidence_bundle_candidates(log_path, report_dir) if candidate.is_dir()), Path(log_path))
    if not root.is_dir():
        return []
    wav_files = [
        path
        for path in root.rglob("*.wav")
        if path.is_file() and path.name.lower().startswith(AUDIO_EVIDENCE_PREFIXES)
    ]
    wav_files.sort(key=lambda path: (0 if path.name.lower().startswith("ai-speech-input") else 1, str(path)))
    evidence = []
    for path in wav_files[:6]:
        file_src = stage_evidence_for_report(path, root, report_dir)
        data_src = embedded_audio_src(path)
        evidence.append(AudioEvidence(audio_evidence_label(path), str(path), data_src or file_src, file_src, bool(data_src)))
    return evidence


def discover_chat_nlu_evidence(log_path: str, report_dir: Optional[Path] = None) -> List[dict]:
    result_path = next(
        (
            candidate / "rasa-nlu-results.json"
            for candidate in evidence_bundle_candidates(log_path, report_dir)
            if (candidate / "rasa-nlu-results.json").is_file()
        ),
        Path(log_path) / "rasa-nlu-results.json",
    )
    if not result_path.is_file():
        return []
    try:
        parsed = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def rasa_profile_from_row(row: "ReportRow") -> str:
    row_text = f"{row.name} {row.suite} {row.command}"
    for profile in RASA_TEST_PROFILES:
        if f"[{profile}]" in row.name or re.search(rf"(^|[^a-z0-9-]){re.escape(profile)}([^a-z0-9-]|$)", row_text):
            return profile
    return ""


def render_rasa_test_section(rows: List["ReportRow"]) -> str:
    ordered_profiles: List[str] = []
    status_by_profile: dict[str, str] = {}
    for row in rows:
        profile = rasa_profile_from_row(row)
        if profile and profile not in ordered_profiles:
            ordered_profiles.append(profile)
        if profile:
            status_by_profile[profile] = row.status
    if not ordered_profiles:
        return ""

    cards = []
    for profile in ordered_profiles:
        flow = RASA_TEST_FLOWS.get(profile, {})
        status = status_by_profile.get(profile, "unknown")
        status_class = "pass" if status == "passed" else "blocked" if status == "blocked" else "fail"
        cards.append(
            "<article class=\"rasa-card\">"
            "<div class=\"rasa-card-head\">"
            f"<h3>{html.escape(str(flow.get('title') or profile))}</h3>"
            f"<span class=\"badge {status_class}\">{html.escape(status.upper())}</span>"
            "</div>"
            f"<code>{html.escape(profile)}</code>"
            f"<p><strong>Purpose:</strong> {html.escape(str(flow.get('proves') or 'RASA profile validation.'))}</p>"
            f"<p><strong>Flow:</strong> {html.escape(str(flow.get('flow') or 'See per-test evidence.'))}</p>"
            "</article>"
        )

    common_flow = (
        "Kubernetes RASA regression prepares the profile config, rolls PlaySBC/RTPengine/Rasa components as needed, "
        "runs either SIPp voice traffic or direct chat/NLU inputs, captures PlaySBC AI evidence, validates the expected "
        "Rasa outcome, and renders the result in the HTML report."
    )
    return (
        "<section class=\"rasa-section\">"
        "<div class=\"rasa-section-head\">"
        "<span class=\"eyebrow\">RASA test section</span>"
        "<h2>AI/Rasa End-to-End Regression Flow</h2>"
        f"<p>{html.escape(common_flow)}</p>"
        "</div>"
        f"<div class=\"rasa-grid\">{''.join(cards)}</div>"
        "</section>"
    )


def run_command(command: List[str], timeout: int) -> tuple[int, float, str, str]:
    started = time.monotonic()
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    return completed.returncode, time.monotonic() - started, completed.stdout, completed.stderr


def status_from_returncode(returncode: int) -> str:
    return "passed" if returncode == 0 else "failed"


def real_topology_command(profile_run_id: str, log_root: Path) -> List[str]:
    return [
        sys.executable,
        str(ROOT / "tools" / "run_real_topology.py"),
        "--run-id",
        profile_run_id,
        "--output-root",
        str(log_root),
    ]


def dual_realm_command(profile: str, profile_run_id: str, log_root: Path, *, rebuild: bool = False) -> List[str]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "run_dual_realm_profile.py"),
        "--profile",
        profile,
        "--run-id",
        profile_run_id,
        "--output-root",
        str(log_root),
    ]
    if rebuild:
        command.append("--rebuild")
    else:
        command.append("--skip-build")
    return command


class SudoKeepalive:
    """Refresh cached sudo credentials so late SIPp PCAP profiles can still run."""

    def __init__(self, interval_seconds: float = 60.0):
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> tuple[bool, str]:
        ok, detail = self.refresh()
        if not ok:
            return False, detail
        self._thread = threading.Thread(target=self._run, name="playsbc-sudo-keepalive", daemon=True)
        self._thread.start()
        return True, detail

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def refresh(self) -> tuple[bool, str]:
        completed = subprocess.run(["sudo", "-n", "-v"], text=True, capture_output=True)
        detail = (completed.stderr.strip() or completed.stdout.strip() or f"returncode={completed.returncode}").strip()
        if completed.returncode == 0:
            return True, "sudo credentials refreshed"
        return False, detail

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.refresh()


def probe_rtpengine(url: str, timeout: float) -> tuple[bool, str]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "check_rtpengine.py"),
        "--url",
        url,
        "--timeout",
        str(timeout),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=max(timeout + 1.0, 2.0),
        )
    except subprocess.TimeoutExpired:
        return False, "tools/check_rtpengine.py timed out"

    detail = (completed.stdout.strip() or completed.stderr.strip() or f"returncode={completed.returncode}").strip()
    return completed.returncode == 0, detail


def rtpengine_blocked_row(profile: str, url: str, detail: str, duration: float, log_path: Path, command: str) -> ReportRow:
    return ReportRow(
        suite=f"B2BUA {profile}",
        name="rtpengine-preflight",
        status="blocked",
        returncode=None,
        duration_seconds=duration,
        log_path=str(log_path),
        command=f"{command} # blocked: RTPengine not reachable at {url}: {detail}",
        phases=[ReportPhase("Preflight", "blocked", duration, f"RTPengine readiness check failed: {detail}")],
    )


def initialize_b2bua_log_bundle(log_path: Path) -> None:
    log_path.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    for filename in B2BUA_LOG_FILES:
        (log_path / filename).write_text(f"{timestamp} | LOG START | file={filename}\n", encoding="utf-8")


def append_bundle_log(log_path: Path, filename: str, title: str, body: str = "") -> None:
    initialize = not (log_path / filename).exists()
    if initialize:
        initialize_b2bua_log_bundle(log_path)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with (log_path / filename).open("a", encoding="utf-8") as log_file:
        log_file.write(f"{timestamp} | {title}\n")
        if body:
            log_file.write(body.rstrip() + "\n")


def extract_b2bua_log_path(stdout: str, fallback: Path) -> Path:
    for line in stdout.splitlines():
        if line.startswith("B2BUA SIPp logs: "):
            return Path(line.split(": ", 1)[1].strip())
    return fallback


def summarize_statuses(statuses: List[str]) -> str:
    normalized = [status for status in statuses if status]
    if not normalized:
        return "unknown"
    if any(status == "failed" for status in normalized):
        return "failed"
    if any(status == "blocked" for status in normalized):
        return "blocked"
    if all(status in {"passed", "dry-run"} for status in normalized):
        return "passed"
    return "unknown"


def report_statuses_by_log_path(report_dir: Path) -> dict[str, List[str]]:
    statuses: dict[str, List[str]] = {}
    if not report_dir.exists():
        return statuses
    for report in sorted(report_dir.glob("*.json")):
        try:
            rows = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("suite", "")).startswith("B2BUA"):
                continue
            log_path = str(row.get("log_path", ""))
            status = str(row.get("status", ""))
            if log_path and status:
                statuses.setdefault(log_path, []).append(status)
    return statuses


def b2bua_bundle_status(log_path: Path, report_statuses: dict[str, List[str]]) -> str:
    report_status = summarize_statuses(report_statuses.get(str(log_path), []))
    if report_status != "unknown":
        return report_status

    platform = log_path / "log.platform"
    if not platform.exists():
        return "unknown"
    text = platform.read_text(encoding="utf-8", errors="replace")
    if "RTPENGINE PREFLIGHT BLOCKED" in text:
        return "blocked"
    statuses = []
    for line in text.splitlines():
        if ": " not in line:
            continue
        _name, status_text = line.split(": ", 1)
        status = status_text.split(maxsplit=1)[0].strip()
        if status in {"passed", "failed", "dry-run", "blocked"}:
            statuses.append(status)
    return summarize_statuses(statuses)


def cleanup_non_failed_b2bua_log_bundles(log_root: Path, report_dir: Path) -> List[Path]:
    if not log_root.exists():
        return []
    report_statuses = report_statuses_by_log_path(report_dir)
    deleted = []
    for candidate in sorted(log_root.iterdir()):
        if not candidate.is_dir():
            continue
        if b2bua_bundle_status(candidate, report_statuses) not in {"passed", "blocked"}:
            continue
        shutil.rmtree(candidate)
        deleted.append(candidate)
    return deleted


def cleanup_old_reports(report_dir: Path, current_run_id: str) -> List[Path]:
    if not report_dir.exists():
        return []

    keep = {"latest.html", f"{current_run_id}.html", f"{current_run_id}.json"}
    deleted = []
    for candidate in sorted(report_dir.iterdir()):
        if not candidate.is_file() or candidate.name in keep:
            continue
        if candidate.suffix.lower() not in {".html", ".json"}:
            continue
        candidate.unlink()
        deleted.append(candidate)
    return deleted


def fallback_execution_phases(status: str, duration: float, detail: str) -> List[ReportPhase]:
    return [
        ReportPhase(
            name="Test Execution",
            status=status,
            duration_seconds=duration,
            detail=detail,
        )
    ]


def read_execution_phases(log_path: Path) -> List[ReportPhase]:
    platform_log = log_path / "log.platform"
    if not platform_log.exists():
        return []

    phases = []
    for line in platform_log.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(ROBOT_PHASE_PREFIX):
            continue
        try:
            payload = json.loads(line[len(ROBOT_PHASE_PREFIX) :])
            phases.append(
                ReportPhase(
                    name=str(payload["name"]),
                    status=str(payload["status"]),
                    duration_seconds=float(payload.get("duration_seconds") or 0),
                    detail=str(payload.get("detail") or ""),
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    order = {name: index for index, name in enumerate(ROBOT_PHASE_ORDER)}
    return sorted(phases, key=lambda phase: order.get(phase.name, len(order)))


def read_sip_ladder(log_path: Path) -> str:
    sip_log = log_path / "log.sip"
    if not sip_log.exists():
        return ""
    lines = sip_log.read_text(encoding="utf-8", errors="replace").splitlines()
    titles = (
        "B2BUA SIP LADDER",
        "CALLEE REGISTRATION LADDER",
        "CALLER REGISTRATION LADDER",
        "AI VOICE CALL LADDER",
    )
    sections = []
    for index, line in enumerate(lines):
        if not any(f" | {title}" in line for title in titles):
            continue
        title = line.split("|", 2)[1].strip() if "|" in line else "SIP LADDER"
        ladder = []
        for candidate in lines[index + 1 :]:
            if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| ", candidate):
                break
            ladder.append(candidate.rstrip())
        body = "\n".join(ladder).strip()
        if body:
            sections.append(f"{title}\n{body}")
    return "\n\n".join(sections)


def parse_sipp_smoke_summary(summary_path: Path, fallback_command: str) -> List[ReportRow]:
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = []
    for result in payload.get("results", []):
        command = result.get("command") or fallback_command
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        status = str(result.get("status", "failed"))
        duration = float(result.get("duration_seconds") or 0)
        rows.append(
            ReportRow(
                suite="SIPp Smoke",
                name=str(result.get("scenario", "")),
                status=status,
                returncode=result.get("returncode"),
                duration_seconds=duration,
                log_path=str(summary_path.parent),
                command=str(command),
                phases=fallback_execution_phases(
                    status,
                    duration,
                    "Execute the SIPp smoke scenario and collect its scenario summary.",
                ),
            )
        )
    return rows


def parse_b2bua_stdout(profile: str, stdout: str, returncode: int, duration: float, log_path: Path, command: str) -> List[ReportRow]:
    log_path = extract_b2bua_log_path(stdout, log_path)
    statuses = []
    names = []
    for line in stdout.splitlines():
        if ": " not in line:
            continue
        name, status = line.split(": ", 1)
        status = status.strip()
        if status not in {"passed", "failed", "dry-run", "blocked"}:
            continue
        names.append(name.strip())
        statuses.append(status)

    aggregate_status = summarize_statuses(statuses)
    if aggregate_status == "unknown":
        aggregate_status = status_from_returncode(returncode)
    elif returncode != 0 and aggregate_status in {"passed", "dry-run"}:
        aggregate_status = "failed"

    aggregate_command = command
    if names:
        aggregate_command = f"{command} # steps: {', '.join(f'{name}={status}' for name, status in zip(names, statuses))}"

    aggregate_returncode = 0 if aggregate_status in {"passed", "dry-run"} and returncode == 0 else returncode
    phases = read_execution_phases(log_path)
    if not phases:
        phases = fallback_execution_phases(
            aggregate_status,
            duration,
            "Execute the B2BUA profile. Detailed lifecycle timings were not emitted by this runner.",
        )
    return [
        ReportRow(
            suite=f"B2BUA {profile}",
            name=profile,
            status=aggregate_status,
            returncode=aggregate_returncode,
            duration_seconds=duration,
            log_path=str(log_path),
            command=aggregate_command,
            phases=phases,
            sip_ladder=read_sip_ladder(log_path),
        )
    ]


def observed_endpoint_roles(bundle: Optional[Path]) -> dict[str, str]:
    """Map retained Kubernetes endpoint IPs to concise participant roles."""
    roles: dict[str, str] = {}
    if bundle is None:
        return roles
    platform = bundle / "log.platform"
    if platform.is_file():
        text = platform.read_text(encoding="utf-8", errors="replace")
        for pod, ip in re.findall(r"POD\s+(\S+)\s+READY\s*\npod_ip=(\S+)", text):
            lowered = pod.lower()
            role = "SIPp Target" if "target" in lowered else "SIPp Peer" if "peer" in lowered or "uas" in lowered else "SIPp Core"
            roles[ip] = role
        for pod, ip in re.findall(
            r"===== persistent pod/(\S+) log\.platform =====.*?SERVER CONFIG.*?sip_advertised=([^:\s]+)",
            text,
            re.DOTALL,
        ):
            roles[ip] = "PlaySBC" + (f" {pod.rsplit('-', 1)[-1]}" if re.search(r"-\d+$", pod) else "")
    pods = bundle / "kubectl-pods.log"
    if pods.is_file():
        for line in pods.read_text(encoding="utf-8", errors="replace").splitlines():
            columns = line.split()
            if len(columns) < 7 or not re.match(r"^\d+\.\d+\.\d+\.\d+$", columns[5]):
                continue
            name, ip = columns[0], columns[5]
            if "rtpengine" in name:
                roles[ip] = "RTPengine" + (f" {name.rsplit('-', 1)[-1]}" if re.search(r"-\d+$", name) else "")
            elif re.search(r"playsbc-playsbc-\d+$", name):
                roles[ip] = f"PlaySBC {name.rsplit('-', 1)[-1]}"
    services = bundle / "kubectl-services.log"
    if services.is_file():
        for line in services.read_text(encoding="utf-8", errors="replace").splitlines():
            columns = line.split()
            if len(columns) >= 3 and columns[0] == "playsbc-playsbc" and re.match(r"^\d+\.\d+\.\d+\.\d+$", columns[2]):
                roles[columns[2]] = "PlaySBC Service"
    return roles


def observed_rtpengine_events(bundle: Optional[Path], roles: dict[str, str]) -> list[tuple[float, str, str, str, str]]:
    """Read RTPengine control and packet-verdict events retained in log.media."""
    if bundle is None or not (bundle / "log.media").is_file():
        return []
    text = (bundle / "log.media").read_text(encoding="utf-8", errors="replace")
    rtpengine_ips = [ip for ip, role in roles.items() if role.startswith("RTPengine")]
    if not rtpengine_ips:
        rtpengine_ips = re.findall(r"RTPENGINE PORT ALLOCATION.*?rtp=(\d+\.\d+\.\d+\.\d+):", text)
        for ip in rtpengine_ips:
            roles.setdefault(ip, "RTPengine")
    if not rtpengine_ips:
        return []
    events = []
    current_playsbc_ip = ""
    pod_ips = {role: ip for ip, role in roles.items() if role.startswith("PlaySBC ")}
    for line in text.splitlines():
        heading = re.match(r"===== persistent pod/(\S+) log\.media =====", line)
        if heading:
            suffix = heading.group(1).rsplit("-", 1)[-1]
            current_playsbc_ip = pod_ips.get(f"PlaySBC {suffix}", "")
            continue
        match = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| B2BUA RTPENGINE (OFFER|ANSWER|QUERY|DELETE|PACKET VERDICT)\b", line)
        if not match or not current_playsbc_ip:
            continue
        timestamp_text, action = match.groups()
        timestamp = datetime.datetime.strptime(timestamp_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
        rtpe_ip = rtpengine_ips[0]
        if action == "PACKET VERDICT":
            message = "RTP/RTCP media packets observed in both directions"
            events.append((timestamp + 0.001, rtpe_ip, current_playsbc_ip, message, "media"))
        else:
            events.append((timestamp, current_playsbc_ip, rtpe_ip, f"RTPengine {action.title()}", "media"))
    return events


def render_observed_pcap_ladder(ladder: str, bundle: Optional[Path] = None) -> str:
    """Render observed SIP and RTPengine evidence as a directional SVG ladder."""
    lines = ladder.splitlines()
    if not lines or not lines[0].startswith("KUBERNETES OBSERVED PCAP SIP LADDER"):
        return ""

    roles = observed_endpoint_roles(bundle)
    events: list[tuple[float, str, str, str, str]] = []
    participants = []
    for line in lines[2:]:
        match = re.match(r"^(\d+(?:\.\d+)?)\s+(\S+)\s+(\S+)\s+(.+)$", line.strip())
        if not match:
            continue
        timestamp, source, destination, message = match.groups()
        source = source.rsplit(":", 1)[0]
        destination = destination.rsplit(":", 1)[0]
        events.append((float(timestamp), source, destination, message, "sip"))
        for endpoint in (source, destination):
            if endpoint not in participants:
                participants.append(endpoint)
    events.extend(observed_rtpengine_events(bundle, roles))
    events.sort(key=lambda item: item[0])
    for _timestamp, source, destination, _message, _kind in events:
        for endpoint in (source, destination):
            if endpoint not in participants:
                participants.append(endpoint)
    if not events or not participants:
        return ""

    left_margin = 150
    lane_width = 230
    header_height = 66
    row_height = 46
    bottom_margin = 18
    width = left_margin + max(2, len(participants)) * lane_width
    height = header_height + len(events) * row_height + bottom_margin
    x_positions = {
        endpoint: left_margin + lane_width // 2 + index * lane_width
        for index, endpoint in enumerate(participants)
    }
    marker_id = "sip-arrow-" + re.sub(r"[^a-zA-Z0-9_-]+", "-", lines[0]).strip("-")

    svg = [
        f'<div class="sip-ladder-diagram" role="img" aria-label="{html.escape(lines[0])}">',
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        "<defs>",
        f'<marker id="{marker_id}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">',
        '<path d="M0,0 L8,4 L0,8 z" fill="#2563eb"/></marker>',
        "</defs>",
        '<text class="ladder-time-heading" x="12" y="32">Time (epoch)</text>',
    ]
    for endpoint in participants:
        x = x_positions[endpoint]
        svg.extend(
            [
                f'<rect class="ladder-participant" x="{x - 92}" y="8" width="184" height="38" rx="6"/>',
                f'<text class="ladder-participant-label" x="{x}" y="25">{html.escape(roles.get(endpoint, "SIP endpoint"))}'
                f'<tspan x="{x}" dy="14">{html.escape(endpoint)}</tspan></text>',
                f'<line class="ladder-lifeline" x1="{x}" y1="46" x2="{x}" y2="{height - 8}"/>',
            ]
        )
    for index, (timestamp, source, destination, message, kind) in enumerate(events):
        y = header_height + index * row_height + 22
        source_x = x_positions[source]
        destination_x = x_positions[destination]
        svg.append(f'<text class="ladder-timestamp" x="12" y="{y + 4}">{timestamp:.6f}</text>')
        if source_x == destination_x:
            svg.append(
                f'<path class="ladder-arrow" d="M {source_x} {y} h 45 v 20 h -45" marker-end="url(#{marker_id})"/>'
            )
            label_x = source_x + 52
            label_anchor = "start"
        else:
            inset = 7 if destination_x > source_x else -7
            svg.append(
                f'<line class="ladder-arrow {kind}" x1="{source_x}" y1="{y}" x2="{destination_x - inset}" y2="{y}" marker-end="url(#{marker_id})"/>'
            )
            label_x = (source_x + destination_x) / 2
            label_anchor = "middle"
        svg.append(
            f'<text class="ladder-message" x="{label_x}" y="{y - 7}" text-anchor="{label_anchor}">{html.escape(message)}</text>'
        )
    svg.extend(["</svg>", "</div>"])
    return "".join(svg)


def render_html(
    rows: List[ReportRow],
    generated_at: str,
    run_id: str,
    report_dir: Optional[Path] = None,
    include_rasa_test_section: bool = False,
) -> str:
    passed = sum(1 for row in rows if row.status == "passed")
    blocked = sum(1 for row in rows if row.status == "blocked")
    failed = sum(1 for row in rows if row.status not in {"passed", "blocked"})
    summary_class = "pass" if failed == 0 and blocked == 0 else "blocked" if failed == 0 else "fail"
    rasa_section_html = render_rasa_test_section(rows) if include_rasa_test_section else ""
    row_html = []
    for row in rows:
        status_class = "pass" if row.status == "passed" else "blocked" if row.status == "blocked" else "fail"
        artifacts = discover_report_artifacts(row.log_path, report_dir)
        artifacts_by_name = {artifact["name"]: artifact for artifact in artifacts}
        phase_html = []
        for phase in row.phases or fallback_execution_phases(
            row.status,
            row.duration_seconds,
            "Execute the reported test case.",
        ):
            phase_class = "pass" if phase.status == "passed" else "blocked" if phase.status in {"blocked", "skipped"} else "fail"
            phase_links = []
            for artifact_name in PHASE_ARTIFACTS.get(phase.name, ()):
                artifact = artifacts_by_name.get(artifact_name)
                if artifact:
                    phase_links.append(
                        f'<a href="{html.escape(artifact["href"])}">{html.escape(artifact["label"])}</a>'
                    )
            phase_evidence = (
                " ".join(phase_links)
                if phase_links
                else '<span class="muted">Inline verdict</span>'
            )
            phase_html.append(
                "<tr>"
                f"<td><span class=\"keyword\">{html.escape(phase.name)}</span></td>"
                f"<td><span class=\"badge {phase_class}\">{html.escape(phase.status.upper())}</span></td>"
                f"<td class=\"elapsed\">{phase.duration_seconds:.3f} s</td>"
                f"<td>{html.escape(phase.detail)}</td>"
                f'<td class="phase-links">{phase_evidence}</td>'
                "</tr>"
            )
        artifact_html = ""
        if artifacts:
            artifact_links = []
            for artifact in artifacts:
                size = int(artifact["bytes"])
                if size >= 1024 * 1024:
                    size_label = f"{size / (1024 * 1024):.1f} MB"
                elif size >= 1024:
                    size_label = f"{size / 1024:.1f} KB"
                else:
                    size_label = f"{size} B"
                artifact_links.append(
                    f'<a class="artifact-link" href="{html.escape(artifact["href"])}">'
                    f'<strong>{html.escape(artifact["label"])}</strong>'
                    f'<span>{html.escape(artifact["name"])} - {html.escape(size_label)}</span></a>'
                )
            artifact_html = (
                '<nav class="artifact-nav" aria-label="Test evidence files">'
                '<h2>Evidence Files</h2><p>Open the retained source evidence directly from this report.</p>'
                f'<div class="artifact-grid">{"".join(artifact_links)}</div></nav>'
            )
        chat_nlu_evidence = discover_chat_nlu_evidence(row.log_path, report_dir)
        is_chat_nlu = bool(chat_nlu_evidence)
        ladder_html = ""
        if row.sip_ladder:
            if is_chat_nlu:
                ladder_title = "NLP Chat/Rasa Ladder"
                ladder_note = (
                    "Chat-specific ladder for the Rasa NLU regression path. This is not a SIP/RTP ladder."
                )
            elif "PROTOCOL CORE EVIDENCE LADDER" in row.sip_ladder:
                ladder_title = "Protocol Core Evidence Ladder"
                ladder_note = "Observed post-execution protocol-core test verdicts retained in the evidence bundle."
            elif "REGRESSION EVIDENCE LADDER" in row.sip_ladder:
                ladder_title = "Regression Evidence Ladder"
                ladder_note = "Post-execution lifecycle and retained evidence for a case with no SIP packet flow."
            else:
                is_ai_ladder = "AI VOICE" in row.sip_ladder or "ai-rasa" in row.name
                ladder_title = "Unified SIP/RTP/AI Ladder" if is_ai_ladder else "Unified SIP Ladder"
                ladder_note = (
                    "Single ordered ladder for the test case. AI speech profiles show RTPengine, Vosk STT, Rasa, and Piper TTS in the same call flow."
                    if is_ai_ladder
                    else "Single ordered SIP ladder for the test case."
                )
            evidence_bundle = resolve_evidence_bundle(row.log_path, report_dir)
            observed_diagram = render_observed_pcap_ladder(row.sip_ladder, evidence_bundle)
            ladder_content = observed_diagram or f"<pre>{html.escape(row.sip_ladder)}</pre>"
            raw_observed = (
                '<details class="ladder-source"><summary>Observed packet trace</summary>'
                f'<pre>{html.escape(row.sip_ladder)}</pre></details>'
                if observed_diagram else ""
            )
            ladder_html = (
                f"<section class=\"ladder\"><h2>{html.escape(ladder_title)}</h2>"
                f"<p>{html.escape(ladder_note)}</p>"
                f"{ladder_content}{raw_observed}</section>"
            )
        audio_html = ""
        audio_evidence = [] if is_chat_nlu else discover_audio_evidence(row.log_path, report_dir)
        if audio_evidence:
            players = []
            for evidence in audio_evidence:
                source_note = "embedded WAV" if evidence.embedded else "linked WAV"
                players.append(
                    "<div class=\"audio-item\">"
                    f"<div><strong>{html.escape(evidence.label)}</strong>"
                    f"<span>{html.escape(source_note)}</span>"
                    f"<code>{html.escape(evidence.path)}</code>"
                    f"<a href=\"{html.escape(evidence.file_src)}\" download>Open WAV file</a></div>"
                    f"<audio controls preload=\"none\" src=\"{html.escape(evidence.src)}\"></audio>"
                    "</div>"
                )
            audio_html = (
                "<section class=\"audio-evidence\"><h2>AI Speech Audio Evidence</h2>"
                "<p>Replay the decoded caller speech WAV and generated Piper response WAV directly from this report.</p>"
                f"{''.join(players)}</section>"
            )
        chat_nlu_html = ""
        if chat_nlu_evidence:
            chat_turns = []
            for item in chat_nlu_evidence:
                case_status = str(item.get("status", "failed"))
                case_class = "pass" if case_status == "passed" else "fail"
                user_input = str(item.get("user_input", ""))
                if not user_input:
                    user_input = "(empty message)"
                bot_reply = str(item.get("bot_reply", "")).strip() or "No bot response was captured for this chat turn."
                chat_turns.append(
                    f"<article class=\"chat-turn {case_class}\">"
                    "<div class=\"chat-head\">"
                    f"<span class=\"keyword\">{html.escape(str(item.get('case_id', '')))}</span>"
                    f"<span class=\"badge {case_class}\">{html.escape(case_status.upper())}</span>"
                    "</div>"
                    "<div class=\"chat-message user\">"
                    "<strong>User</strong>"
                    f"<p>{html.escape(user_input)}</p>"
                    "</div>"
                    "<div class=\"chat-message bot\">"
                    "<strong>Rasa Bot</strong>"
                    f"<p>{html.escape(bot_reply)}</p>"
                    "<div class=\"intent-line\">"
                    f"<span>Expected <b>{html.escape(str(item.get('expected_intent', '')))}</b></span>"
                    f"<span>Predicted <b>{html.escape(str(item.get('predicted_intent', '')))}</b></span>"
                    f"<span>Confidence <b>{float(item.get('confidence') or 0.0):.3f}</b></span>"
                    "</div>"
                    f"<small>{html.escape(str(item.get('detail', '')))}</small>"
                    "</div>"
                    "</article>"
                )
            chat_nlu_html = (
                "<section class=\"chat-evidence\"><h2>Rasa Chat Window</h2>"
                "<p>Each message is sent to real Rasa /model/parse and, when input is present, the REST webhook reply is captured as the bot response.</p>"
                f"<div class=\"chat-window\">{''.join(chat_turns)}</div></section>"
            )
        open_attr = " open" if row.status != "passed" and not is_chat_nlu else ""
        row_html.append(
            f"<details class=\"test-case {status_class}\"{open_attr}>"
            "<summary>"
            f"<span class=\"test-name\">{html.escape(row.name)}</span>"
            f"<span class=\"suite\">{html.escape(row.suite)}</span>"
            f"<span class=\"total-time\">{row.duration_seconds:.3f} s</span>"
            f"<span class=\"badge {status_class}\">{html.escape(row.status.upper())}</span>"
            "</summary>"
            "<div class=\"test-body\">"
            "<div class=\"metadata\">"
            f"<div><span>Return code</span><strong>{'-' if row.returncode is None else row.returncode}</strong></div>"
            f"<div><span>Evidence bundle</span><code>{html.escape(row.log_path)}</code></div>"
            "</div>"
            "<details class=\"command-details\"><summary>Runner command</summary>"
            f"<code>{html.escape(row.command)}</code></details>"
            f"{artifact_html}"
            "<table class=\"phases\"><thead><tr>"
            "<th>Phase</th><th>Status</th><th>Elapsed</th><th>Execution Detail</th><th>Evidence</th>"
            "</tr></thead><tbody>"
            f"{''.join(phase_html)}"
            "</tbody></table>"
            f"{chat_nlu_html}"
            f"{audio_html}"
            f"{ladder_html}"
            "</div></details>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>PlaySBC Regression Evidence Report</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f4f6f8; color: #1f2937; }}
    main {{ width: min(1500px, calc(100% - 32px)); margin: 24px auto 48px; }}
    .eyebrow {{ color: #2563eb; font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }}
    h1 {{ margin: 4px 0; font-size: 28px; }}
    .browser-note {{ margin: 10px 0 14px; padding: 10px 12px; border: 1px solid #bfdbfe; border-radius: 6px; background: #eff6ff; color: #334155; font-size: 13px; }}
    .browser-note code {{ overflow-wrap: anywhere; color: #1e3a5f; }}
    .meta {{ color: #4b5563; margin: 0 0 18px; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: 18px; padding: 12px 14px; border-radius: 6px; margin-bottom: 18px; }}
    .summary.pass {{ background: #ecfdf5; border: 1px solid #16a34a; }}
    .summary.blocked {{ background: #fffbeb; border: 1px solid #f59e0b; }}
    .summary.fail {{ background: #fef2f2; border: 1px solid #dc2626; }}
    .rasa-section {{ background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 16px; margin: 18px 0; }}
    .rasa-section-head h2 {{ margin: 4px 0 6px; font-size: 20px; }}
    .rasa-section-head p {{ margin: 0 0 14px; color: #4b5563; line-height: 1.45; }}
    .rasa-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
    .rasa-card {{ border: 1px solid #d1d5db; border-radius: 8px; padding: 12px; background: #f8fafc; }}
    .rasa-card-head {{ display: flex; justify-content: space-between; align-items: start; gap: 8px; margin-bottom: 6px; }}
    .rasa-card h3 {{ margin: 0; font-size: 15px; color: #111827; }}
    .rasa-card p {{ margin: 8px 0 0; font-size: 13px; color: #374151; line-height: 1.42; }}
    .test-case {{ background: #fff; border: 1px solid #d1d5db; border-left: 5px solid #16a34a; border-radius: 6px; margin: 10px 0; overflow: hidden; }}
    .test-case.blocked {{ border-left-color: #f59e0b; }}
    .test-case.fail {{ border-left-color: #dc2626; }}
    .test-case > summary {{ display: grid; grid-template-columns: minmax(220px, 1.3fr) minmax(220px, 1fr) 100px 92px; align-items: center; gap: 14px; padding: 13px 15px; cursor: pointer; list-style-position: inside; }}
    .test-case > summary:hover {{ background: #f9fafb; }}
    .test-name {{ font-weight: 750; }}
    .suite {{ color: #4b5563; font-size: 13px; }}
    .total-time {{ color: #374151; font-variant-numeric: tabular-nums; text-align: right; }}
    .test-body {{ border-top: 1px solid #e5e7eb; padding: 14px; }}
    .metadata {{ display: grid; grid-template-columns: 140px 1fr; gap: 8px 14px; margin-bottom: 14px; font-size: 12px; }}
    .metadata div {{ display: contents; }}
    .metadata span {{ color: #6b7280; font-weight: 700; text-transform: uppercase; }}
    .command-details {{ margin: 0 0 14px; border: 1px solid #dbe3ea; border-radius: 5px; background: #f8fafc; }}
    .command-details summary {{ cursor: pointer; padding: 8px 10px; color: #334155; font-size: 12px; font-weight: 750; }}
    .command-details code {{ display: block; padding: 0 10px 10px; overflow-wrap: anywhere; color: #334155; }}
    .artifact-nav {{ margin: 0 0 16px; padding: 12px; border: 1px solid #bfdbfe; border-radius: 7px; background: #f8fbff; }}
    .artifact-nav h2 {{ margin: 0 0 3px; font-size: 14px; color: #1e3a5f; }}
    .artifact-nav p {{ margin: 0 0 10px; color: #5b6875; font-size: 12px; }}
    .artifact-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 7px; }}
    .artifact-link {{ display: block; padding: 8px 9px; border: 1px solid #dbe3ea; border-radius: 5px; background: #fff; color: #1d4ed8; text-decoration: none; }}
    .artifact-link:hover {{ border-color: #60a5fa; background: #eff6ff; }}
    .artifact-link strong {{ display: block; font-size: 12px; }}
    .artifact-link span {{ display: block; margin-top: 2px; color: #64748b; font: 10px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 10px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ background: #f9fafb; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: #374151; }}
    .phases th:nth-child(1) {{ width: 15%; }}
    .phases th:nth-child(2) {{ width: 9%; }}
    .phases th:nth-child(3) {{ width: 9%; }}
    .phases th:nth-child(5) {{ width: 17%; }}
    .phase-links {{ font-size: 11px; line-height: 1.55; }}
    .phase-links a {{ display: block; color: #1d4ed8; font-weight: 650; text-decoration: none; }}
    .phase-links a:hover {{ text-decoration: underline; }}
    .muted {{ color: #94a3b8; font-size: 11px; }}
    code {{ font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; }}
    .keyword {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 700; color: #1d4ed8; }}
    .elapsed {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .audio-evidence, .chat-evidence, .ladder {{ margin-top: 16px; }}
    .audio-evidence h2, .chat-evidence h2, .ladder h2 {{ margin: 0 0 8px; font-size: 14px; text-transform: uppercase; color: #374151; }}
    .audio-evidence p, .chat-evidence p, .ladder p {{ margin: 0 0 10px; color: #4b5563; font-size: 13px; }}
    .audio-item {{ display: grid; grid-template-columns: minmax(240px, 1fr) minmax(260px, 420px); align-items: center; gap: 12px; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; background: #f9fafb; margin-top: 8px; }}
    .audio-item strong {{ display: block; margin-bottom: 4px; color: #111827; }}
    .audio-item span {{ display: inline-block; margin: 0 0 4px; color: #166534; font-size: 12px; font-weight: 750; }}
    .audio-item a {{ display: inline-block; margin-top: 6px; color: #2563eb; font-size: 12px; font-weight: 700; }}
    .audio-item audio {{ width: 100%; }}
    .chat-window {{ display: grid; gap: 12px; border: 1px solid #d1d5db; border-radius: 8px; padding: 14px; background: #f8fafc; }}
    .chat-turn {{ display: grid; gap: 8px; padding: 12px; border-radius: 8px; background: #fff; border: 1px solid #dbe4ef; }}
    .chat-turn.fail {{ border-color: #fecaca; background: #fff7f7; }}
    .chat-head {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; }}
    .chat-message {{ width: min(760px, 92%); padding: 10px 12px; border-radius: 8px; }}
    .chat-message strong {{ display: block; margin-bottom: 4px; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    .chat-message p {{ margin: 0; line-height: 1.45; }}
    .chat-message.user {{ justify-self: start; background: #dbeafe; border: 1px solid #60a5fa; color: #172554; }}
    .chat-message.bot {{ justify-self: end; background: #ecfdf5; border: 1px solid #34d399; color: #064e3b; }}
    .chat-turn.fail .chat-message.bot {{ background: #fee2e2; border-color: #f87171; color: #7f1d1d; }}
    .intent-line {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
    .intent-line span {{ padding: 4px 7px; border-radius: 999px; background: rgba(255,255,255,.72); border: 1px solid rgba(15,23,42,.12); font-size: 12px; }}
    .chat-message small {{ display: block; margin-top: 8px; color: inherit; opacity: .78; }}
    .ladder pre {{ margin: 0; padding: 14px; overflow-x: auto; border: 1px solid #d1d5db; background: #111827; color: #e5e7eb; font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .sip-ladder-diagram {{ overflow-x: auto; border: 1px solid #cbd5e1; border-radius: 7px; background: #fff; }}
    .sip-ladder-diagram svg {{ display: block; max-width: none; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .ladder-participant {{ fill: #eff6ff; stroke: #2563eb; stroke-width: 1.5; }}
    .ladder-participant-label {{ fill: #172554; font-size: 12px; font-weight: 700; text-anchor: middle; }}
    .ladder-lifeline {{ stroke: #94a3b8; stroke-width: 1; stroke-dasharray: 5 5; }}
    .ladder-arrow {{ fill: none; stroke: #2563eb; stroke-width: 1.6; }}
    .ladder-arrow.media {{ stroke: #7c3aed; stroke-dasharray: 6 3; }}
    .ladder-message {{ fill: #111827; font-size: 11px; font-weight: 650; paint-order: stroke; stroke: #fff; stroke-width: 4px; stroke-linejoin: round; }}
    .ladder-timestamp, .ladder-time-heading {{ fill: #475569; font-size: 11px; font-variant-numeric: tabular-nums; }}
    .ladder-time-heading {{ font-weight: 750; }}
    .ladder-source {{ margin-top: 8px; border: 1px solid #d1d5db; border-radius: 5px; }}
    .ladder-source summary {{ cursor: pointer; padding: 8px 10px; color: #334155; font-size: 12px; font-weight: 700; }}
    .badge {{ display: inline-block; min-width: 68px; text-align: center; border-radius: 999px; padding: 4px 8px; font-weight: 700; font-size: 12px; }}
    .badge.pass {{ color: #166534; background: #dcfce7; border: 1px solid #16a34a; }}
    .badge.blocked {{ color: #92400e; background: #fef3c7; border: 1px solid #f59e0b; }}
    .badge.fail {{ color: #991b1b; background: #fee2e2; border: 1px solid #dc2626; }}
    @media (max-width: 760px) {{
      main {{ width: min(100% - 18px, 1500px); margin-top: 12px; }}
      .test-case > summary {{ grid-template-columns: 1fr auto; }}
      .suite {{ grid-column: 1; }}
      .total-time {{ grid-column: 2; grid-row: 1; }}
      .metadata {{ grid-template-columns: 1fr; }}
      .metadata span {{ margin-top: 6px; }}
      .audio-item {{ grid-template-columns: 1fr; }}
      .phases {{ table-layout: auto; }}
      .phases th:nth-child(3), .phases td:nth-child(3) {{ display: none; }}
      .chat-message {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">Execution and evidence</div>
    <h1>PlaySBC Regression Evidence Report</h1>
    <p class="browser-note"><strong>Evidence links:</strong> linked files are packaged in this report's <code>evidence/</code> directory and open directly, including with <code>file://</code>. Text files open in a browser viewer; binary files open a metadata page with a raw download link. No local server is required; <code>tools/serve_regression_report.py</code> remains available for optional localhost viewing.</p>
    <p class="meta">Run <code>{html.escape(run_id)}</code> | Generated {html.escape(generated_at)} | Expand a test to open its measured phases, logs, ladder, and packet evidence.</p>
    <div class="summary {summary_class}">
      <strong>Total: {len(rows)}</strong>
      <strong>Passed: {passed}</strong>
      <strong>Blocked: {blocked}</strong>
      <strong>Failed: {failed}</strong>
      <strong>Total time: {sum(row.duration_seconds for row in rows):.3f} s</strong>
    </div>
    {rasa_section_html}
    <section aria-label="Regression test cases">{''.join(row_html)}</section>
  </main>
</body>
</html>
"""


def write_reports(rows: List[ReportRow], report_dir: Path, run_id: str, include_rasa_test_section: bool = False) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = report_dir / "evidence"
    if evidence_dir.exists():
        shutil.rmtree(evidence_dir)
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())
    html_text = render_html(
        rows,
        generated_at,
        run_id,
        report_dir=report_dir,
        include_rasa_test_section=include_rasa_test_section,
    )
    report_path = report_dir / f"{run_id}.html"
    latest_path = report_dir / "latest.html"
    json_path = report_dir / f"{run_id}.json"
    report_path.write_text(html_text, encoding="utf-8")
    latest_path.write_text(html_text, encoding="utf-8")
    json_path.write_text(json.dumps([asdict(row) for row in rows], indent=2) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PlaySBC regression suites and write an HTML report")
    parser.add_argument("--run-id", default="", help="Report/run identifier; defaults to a timestamp")
    parser.add_argument("--report-dir", default=str(ROOT / "logs" / "reports"))
    parser.add_argument("--sipp-smoke-root", default=str(ROOT / "logs" / "sipp-smoke-Regression"))
    parser.add_argument("--b2bua-log-folder", default="b2bua-Regression")
    parser.add_argument("--b2bua-profile", action="append", choices=SELECTABLE_B2BUA_PROFILES, help="B2BUA profile to run; repeatable")
    parser.add_argument("--all-b2bua-profiles", action="store_true", help="Run all B2BUA profiles, including load and RTPengine profiles")
    parser.add_argument("--rasa-profiles", action="store_true", help="Run only the Rasa/AI B2BUA profile group")
    parser.add_argument("--b2bua-media-driver", choices=("python", "sipp-pcap"), default="", help="Override B2BUA media driver for media-enabled profiles")
    parser.add_argument("--b2bua-sipp-pcap-sudo", action="store_true", help="Pass --sipp-pcap-sudo to B2BUA profile runs")
    parser.add_argument("--b2bua-rtpengine-url", default="udp://127.0.0.1:2223", help="RTPengine NG control URL for RTPengine-backed B2BUA profiles")
    parser.add_argument("--rtpengine-preflight-timeout", type=float, default=1.0, help="Seconds to wait for RTPengine preflight ping")
    parser.add_argument("--skip-rtpengine-preflight", action="store_true", help="Run RTPengine-backed profiles without checking RTPengine availability first")
    parser.add_argument("--skip-sipp-smoke", action="store_true")
    parser.add_argument("--skip-b2bua", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if args.rasa_profiles and (args.all_b2bua_profiles or args.b2bua_profile):
        raise SystemExit("--rasa-profiles cannot be combined with --all-b2bua-profiles or --b2bua-profile")

    run_id = args.run_id or make_run_id()
    report_dir = Path(args.report_dir)
    rows: List[ReportRow] = []
    sudo_keepalive: Optional[SudoKeepalive] = None

    if not args.skip_b2bua:
        b2bua_log_root = ROOT / "logs" / args.b2bua_log_folder
        deleted_bundles = cleanup_non_failed_b2bua_log_bundles(b2bua_log_root, report_dir)
        if deleted_bundles:
            print(f"Deleted {len(deleted_bundles)} passed/blocked B2BUA log bundle(s) before this run.")

    deleted_reports = cleanup_old_reports(report_dir, run_id)
    if deleted_reports:
        print(f"Deleted {len(deleted_reports)} old regression report file(s) before this run.")

    if args.b2bua_sipp_pcap_sudo and not args.skip_b2bua:
        print("SIPp PCAP sudo is not required by dual-realm Docker regression; option ignored.")

    try:
        if not args.skip_sipp_smoke:
            smoke_run_id = f"{run_id}-sipp-smoke"
            smoke_root = Path(args.sipp_smoke_root)
            if (smoke_root / smoke_run_id).exists():
                raise SystemExit(f"SIPp smoke run directory already exists: {smoke_root / smoke_run_id}")
            command = [
                sys.executable,
                str(ROOT / "tools" / "run_sipp_regression.py"),
                "--start-server",
                "--output-root",
                str(smoke_root),
                "--run-id",
                smoke_run_id,
            ]
            command_text = " ".join(command)
            returncode, duration, stdout, stderr = run_command(command, args.timeout)
            summary_path = smoke_root / smoke_run_id / "summary.json"
            if summary_path.exists():
                rows.extend(parse_sipp_smoke_summary(summary_path, command_text))
            else:
                rows.append(
                    ReportRow("SIPp Smoke", "suite", status_from_returncode(returncode), returncode, duration, str(smoke_root / smoke_run_id), command_text)
                )
            if stderr.strip():
                (smoke_root / smoke_run_id / "stderr.log").write_text(stderr, encoding="utf-8")
            if stdout.strip():
                (smoke_root / smoke_run_id / "stdout.log").write_text(stdout, encoding="utf-8")

        if not args.skip_b2bua:
            if args.rasa_profiles:
                profiles = RASA_B2BUA_PROFILES
            elif args.all_b2bua_profiles:
                profiles = ALL_B2BUA_PROFILES
            else:
                profiles = tuple(args.b2bua_profile or DEFAULT_B2BUA_PROFILES)
            for profile_index, profile in enumerate(profiles):
                profile_run_id = f"{run_id}-{profile}"
                profile_log_path = b2bua_log_root / profile_run_id
                command = dual_realm_command(
                    profile,
                    profile_run_id,
                    b2bua_log_root,
                    rebuild=profile_index == 0,
                )
                command_text = " ".join(command)
                returncode, duration, stdout, stderr = run_command(command, args.timeout)
                actual_log_path = extract_b2bua_log_path(stdout, profile_log_path)
                if stderr.strip():
                    append_bundle_log(actual_log_path, "log.platform", "RUNNER STDERR", stderr)
                if returncode != 0 and stdout.strip():
                    append_bundle_log(actual_log_path, "log.platform", "RUNNER STDOUT", stdout)
                rows.extend(parse_b2bua_stdout(profile, stdout, returncode, duration, actual_log_path, command_text))

        report_path = write_reports(rows, report_dir, run_id, include_rasa_test_section=args.rasa_profiles)
        failed = [row for row in rows if row.status != "passed"]
        print(f"Regression report: {report_path}")
        print(f"Latest report: {report_dir / 'latest.html'}")
        for row in rows:
            print(f"{row.suite} / {row.name}: {row.status}")
        return 1 if failed else 0
    finally:
        if sudo_keepalive:
            sudo_keepalive.stop()


if __name__ == "__main__":
    raise SystemExit(main())
