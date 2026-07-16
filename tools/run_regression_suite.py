#!/usr/bin/env python3
"""Run PlaySBC SIPp smoke and B2BUA regressions, then write an HTML report."""

from __future__ import annotations

import argparse
import base64
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
    "dtmf-rfc4733",
    "ai-rasa-lab",
    "ai-rasa-rtpengine",
    "ai-rasa-real-lab",
    "ai-rasa-rtpengine-speech",
    "ai-rasa-contact-center-sales",
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
    "ha-node-draining",
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
    "ai-rasa-contact-center-sales",
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
    "ai-rasa-contact-center-sales": {
        "title": "Contact-Center Sales Bot",
        "proves": "Virtual SIPp B bot-agent sales flow.",
        "flow": "K8s Runner -> SIPp A -> PlaySBC virtual SIPp B Bot Agent -> RTPengine -> Vosk STT -> Real Rasa sales workflow -> Piper TTS -> HTML Report.",
        "evidence": "Speech WAVs, contact-center ladder, log.ai.",
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
    "ai-rasa-contact-center-sales",
    "rtpengine-control-failure",
    "rtpengine-port-exhaustion",
    "rtpengine-interface-failure",
    "tls-srtp-to-udp-rtp",
    "tls-srtp-to-tcp-rtp",
    "udp-rtp-to-tls-srtp",
    "ha-shared-state-rtpengine",
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
        file_src = audio_src_for_report(path, report_dir)
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
        phase_html = []
        for phase in row.phases or fallback_execution_phases(
            row.status,
            row.duration_seconds,
            "Execute the reported test case.",
        ):
            phase_class = "pass" if phase.status == "passed" else "blocked" if phase.status in {"blocked", "skipped"} else "fail"
            phase_html.append(
                "<tr>"
                f"<td><span class=\"keyword\">{html.escape(phase.name)}</span></td>"
                f"<td><span class=\"badge {phase_class}\">{html.escape(phase.status.upper())}</span></td>"
                f"<td class=\"elapsed\">{phase.duration_seconds:.3f} s</td>"
                f"<td>{html.escape(phase.detail)}</td>"
                "</tr>"
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
            else:
                is_ai_ladder = "AI VOICE" in row.sip_ladder or "ai-rasa" in row.name
                ladder_title = "Unified SIP/RTP/AI Ladder" if is_ai_ladder else "Unified SIP Ladder"
                ladder_note = (
                    "Single ordered ladder for the test case. AI speech profiles show RTPengine, Vosk STT, Rasa, and Piper TTS in the same call flow."
                    if is_ai_ladder
                    else "Single ordered SIP ladder for the test case."
                )
            ladder_html = (
                f"<section class=\"ladder\"><h2>{html.escape(ladder_title)}</h2>"
                f"<p>{html.escape(ladder_note)}</p>"
                f"<pre>{html.escape(row.sip_ladder)}</pre></section>"
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
            f"<div><span>Runner command</span><code>{html.escape(row.command)}</code></div>"
            "</div>"
            "<table class=\"phases\"><thead><tr>"
            "<th>Keyword / Phase</th><th>Status</th><th>Elapsed</th><th>Execution Detail</th>"
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
  <title>PlaySBC Robot-Style Regression Report</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f4f6f8; color: #1f2937; }}
    main {{ width: min(1500px, calc(100% - 32px)); margin: 24px auto 48px; }}
    .eyebrow {{ color: #2563eb; font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }}
    h1 {{ margin: 4px 0; font-size: 28px; }}
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
    summary {{ display: grid; grid-template-columns: minmax(220px, 1.3fr) minmax(220px, 1fr) 100px 92px; align-items: center; gap: 14px; padding: 13px 15px; cursor: pointer; list-style-position: inside; }}
    summary:hover {{ background: #f9fafb; }}
    .test-name {{ font-weight: 750; }}
    .suite {{ color: #4b5563; font-size: 13px; }}
    .total-time {{ color: #374151; font-variant-numeric: tabular-nums; text-align: right; }}
    .test-body {{ border-top: 1px solid #e5e7eb; padding: 14px; }}
    .metadata {{ display: grid; grid-template-columns: 140px 1fr; gap: 8px 14px; margin-bottom: 14px; font-size: 12px; }}
    .metadata div {{ display: contents; }}
    .metadata span {{ color: #6b7280; font-weight: 700; text-transform: uppercase; }}
    table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 10px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ background: #f9fafb; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: #374151; }}
    .phases th:nth-child(1) {{ width: 19%; }}
    .phases th:nth-child(2) {{ width: 10%; }}
    .phases th:nth-child(3) {{ width: 10%; }}
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
    .badge {{ display: inline-block; min-width: 68px; text-align: center; border-radius: 999px; padding: 4px 8px; font-weight: 700; font-size: 12px; }}
    .badge.pass {{ color: #166534; background: #dcfce7; border: 1px solid #16a34a; }}
    .badge.blocked {{ color: #92400e; background: #fef3c7; border: 1px solid #f59e0b; }}
    .badge.fail {{ color: #991b1b; background: #fee2e2; border: 1px solid #dc2626; }}
    @media (max-width: 760px) {{
      main {{ width: min(100% - 18px, 1500px); margin-top: 12px; }}
      summary {{ grid-template-columns: 1fr auto; }}
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
    <div class="eyebrow">Robot-style execution log</div>
    <h1>PlaySBC Regression Report</h1>
    <p class="meta">Run ID: <code>{html.escape(run_id)}</code> | Generated: {html.escape(generated_at)}</p>
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
