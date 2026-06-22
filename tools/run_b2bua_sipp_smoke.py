#!/usr/bin/env python3
"""Run a registrar-backed SIPp B2BUA smoke or small load test."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "sipp" / "scenarios"
MEDIA_PCAPS = {
    "PCMU": "pcap/g711u_60s.pcap",
    "PCMA": "pcap/g711a_60s.pcap",
}
MEDIA_PAYLOAD_TYPES = {
    "PCMU": 0,
    "PCMA": 8,
}
MEDIA_RTPMAP_LINES = {
    "PCMU": "a=rtpmap:0 PCMU/8000",
    "PCMA": "a=rtpmap:8 PCMA/8000",
}
CRLF = "\r\n"
DIAGNOSTIC_PCAP_PORT = 65530
LOG_FILES = (
    "log.sip",
    "log.media",
    "log.transcoding",
    "log.platform",
    "log.networking",
    "log.udp",
    "log.tcp",
    "log.tls",
    "log.call",
    "log.sipp",
)
DEFAULT_LOG_FOLDER = "b2bua-Regression"
SIPP_PCAP_SUDO_BLOCKED_DETAIL = (
    "SIPp PCAP sudo mode requires cached sudo credentials. "
    "Run `sudo -v` in your terminal, then retry with `--sipp-pcap-sudo`."
)
BASE_DEFAULTS = {
    "host": "127.0.0.1",
    "server_port": 25062,
    "sip_transport": "udp",
    "uac_port": 25081,
    "uas_port": 25082,
    "register_port": 25083,
    "caller_register_port": 25084,
    "server_rtp_min": 25100,
    "server_rtp_max": 25400,
    "uac_rtp_min": 36000,
    "uac_rtp_max": 36200,
    "uas_rtp_min": 27000,
    "uas_rtp_max": 27200,
    "caller": "sipp-a",
    "callee": "callee",
    "register_callee": True,
    "register_caller": False,
    "start_uas": True,
    "reject_unknown_routes": False,
    "calls": 1,
    "rate": 1,
    "hold_ms": 1000,
    "media_codec": None,
    "media_pcap": None,
    "media_driver": "python",
    "sipp_pcap_sudo": False,
    "media_start_delay": 1.0,
    "server_codec": None,
    "media_backend": "internal",
    "rtpengine_url": "udp://127.0.0.1:2223",
    "rtpengine_timeout": 3.0,
    "registration_driver": "sipp",
    "uac_scenario": "",
    "uas_scenario": "",
    "ladder": None,
    "output_root": "",
    "log_folder": DEFAULT_LOG_FOLDER,
    "run_id": "",
    "sipp_bin": "sipp",
    "pcap_topology": "logical",
    "pcap_uac_ip": "10.10.10.10",
    "pcap_server_ip": "10.10.10.20",
    "pcap_uas_ip": "10.10.10.30",
    "pcap_rtpengine_ip": "10.10.10.40",
    "dry_run": False,
}
B2BUA_PROFILES = {
    "basic-signalling": {
        "callee": "basic-sig",
    },
    "basic-media": {
        "callee": "basic-media",
        "media_codec": "PCMU",
        "hold_ms": 60000,
    },
    "transcoding": {
        "callee": "transcode-user",
        "media_codec": "PCMU",
        "server_codec": "PCMA",
        "hold_ms": 60000,
    },
    "rtpengine": {
        "callee": "rtpengine-user",
        "media_backend": "rtpengine",
    },
    "rtpengine-media": {
        "callee": "rtpengine-media-user",
        "media_backend": "rtpengine",
        "media_codec": "PCMU",
        "media_driver": "sipp-pcap",
        "hold_ms": 60000,
    },
    "rtpengine-transcoding": {
        "callee": "rtpengine-transcode-user",
        "media_backend": "rtpengine",
        "media_codec": "PCMU",
        "media_driver": "sipp-pcap",
        "server_codec": "PCMA",
        "hold_ms": 60000,
    },
    "tcp-rtpengine-transcoding": {
        "caller": "tcp-rtpengine-a",
        "callee": "tcp-rtpengine-b",
        "sip_transport": "tcp",
        "media_backend": "rtpengine",
        "media_codec": "PCMU",
        "media_driver": "sipp-pcap",
        "server_codec": "PCMA",
        "hold_ms": 60000,
    },
    "registered-inbound": {
        "caller": "reg-inbound-a",
        "callee": "registered-b",
        "uac_scenario": "uac-reg-inbound.xml",
        "uas_scenario": "uas-reg-inbound.xml",
    },
    "registered-outbound": {
        "caller": "registered-a",
        "callee": "registered-b",
        "register_caller": True,
        "uac_scenario": "uac-reg-outbound.xml",
        "uas_scenario": "uas-reg-outbound.xml",
    },
    "invalid-bye": {
        "callee": "invalid-bye-b",
        "uac_scenario": "invalid_bye.xml",
        "start_uas": False,
        "register_callee": False,
        "reject_unknown_routes": False,
    },
    "unknown-route": {
        "callee": "unknown-route-user",
        "uac_scenario": "b2bua_uac_unknown_route.xml",
        "start_uas": False,
        "register_callee": False,
        "reject_unknown_routes": True,
    },
    "failed-outbound": {
        "callee": "failed-outbound-b",
        "uac_scenario": "b2bua_uac_failed_outbound.xml",
        "uas_scenario": "b2bua_uas_failed_outbound.xml",
    },
    "cancel": {
        "callee": "cancel-b",
        "uac_scenario": "b2bua_uac_cancel.xml",
        "uas_scenario": "b2bua_uas_cancel.xml",
    },
    "retransmission": {
        "callee": "retransmit-b",
        "uac_scenario": "b2bua_uac_retransmit_invite.xml",
    },
    "small-load-2cps-10s": {
        "callee": "small-load-user",
        "calls": 20,
        "rate": 2,
        "hold_ms": 10000,
        "server_rtp_max": 25600,
        "ladder": False,
    },
    "soak-1cps-30s": {
        "callee": "soak-user",
        "calls": 30,
        "rate": 1,
        "hold_ms": 30000,
        "server_rtp_max": 25600,
        "ladder": False,
    },
    "load-5cps-60s": {
        "callee": "load-user",
        "calls": 300,
        "rate": 5,
        "hold_ms": 60000,
        "server_rtp_max": 26500,
        "ladder": False,
    },
    "load-5cps-60s-rtpengine-transcoding": {
        "callee": "load-rtpengine-transcode",
        "calls": 300,
        "rate": 5,
        "hold_ms": 60000,
        "media_codec": "PCMU",
        "media_driver": "sipp-pcap",
        "server_codec": "PCMA",
        "media_backend": "rtpengine",
        "ladder": False,
    },
}
PROFILE_DESCRIPTIONS = {
    "basic-signalling": "One SIPp A -> B2BUA -> registered SIPp B call without RTP replay.",
    "basic-media": "One registered 60 second B2BUA call with PCMU RTP replay.",
    "transcoding": "One registered 60 second B2BUA media call with PCMU media and PCMA server codec preference.",
    "rtpengine": "One registered B2BUA signalling call using RTPengine as the media backend.",
    "rtpengine-media": "One registered 60 second B2BUA G.711u media call anchored by RTPengine.",
    "rtpengine-transcoding": "One registered 60 second B2BUA call with PCMU on A leg, PCMA on B leg, and RTPengine transcoding intent.",
    "tcp-rtpengine-transcoding": "One TCP REGISTER plus TCP B2BUA call with PCMU-to-PCMA transcoding and RTPengine media anchoring.",
    "registered-inbound": "Register SIPp B, then call that registered number through the B2BUA.",
    "registered-outbound": "Register SIPp A and SIPp B, then originate from the registered SIPp A user.",
    "invalid-bye": "Send a BYE outside any dialog and expect PlaySBC to reject it.",
    "unknown-route": "Call an unregistered user with unknown-route rejection enabled and expect 404.",
    "failed-outbound": "Register SIPp B, have the outbound leg reject INVITE, and verify PlaySBC propagates failure.",
    "cancel": "Cancel an in-progress B2BUA INVITE and verify CANCEL/487 handling across both legs.",
    "retransmission": "Replay the same inbound INVITE branch/CSeq and verify transaction cache behavior.",
    "small-load-2cps-10s": "Small B2BUA load profile at 2 cps with 10 second CHT.",
    "soak-1cps-30s": "Short soak-style B2BUA profile at 1 cps with 30 second CHT.",
    "load-5cps-60s": "Basic 5 cps for 60 seconds with 60 second CHT and ladder disabled.",
    "load-5cps-60s-rtpengine-transcoding": "5 cps for 60 seconds with 60 second CHT, RTPengine backend, and PCMU-to-PCMA transcoding intent.",
}


@dataclass
class SmokeResult:
    name: str
    command: List[str]
    returncode: Optional[int]
    status: str
    duration_seconds: float


@dataclass
class PcapPacket:
    timestamp: float
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    payload: bytes
    protocol: str = "udp"


def make_run_id(prefix: str = "b2bua") -> str:
    return time.strftime(f"{prefix}-%Y%m%d-%H%M%S", time.localtime())


def resolve_binary(candidate: str) -> Optional[str]:
    if os.sep in candidate:
        return candidate if Path(candidate).exists() else None
    return shutil.which(candidate)


def call_limit(calls: int, rate: int, hold_ms: int) -> int:
    estimated_concurrent = int((rate * max(hold_ms, 1)) / 1000) + rate + 2
    return max(calls, estimated_concurrent, 3)


def sipp_timeout_seconds(calls: int, rate: int, hold_ms: int) -> int:
    safe_rate = max(rate, 1)
    traffic_seconds = (max(calls, 1) + safe_rate - 1) // safe_rate
    hold_seconds = max(hold_ms, 0) // 1000
    return max(30, traffic_seconds + hold_seconds + 60)


def resolve_scenario_path(value: str, fallback: Path) -> Path:
    if not value:
        return fallback
    path = Path(value)
    return path if path.is_absolute() else SCENARIO_DIR / path


def should_sudo_sipp_pcap(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "sipp_pcap_sudo", False)
        and getattr(args, "media_enabled", False)
        and getattr(args, "media_driver", "") == "sipp-pcap"
    )


def maybe_sudo_sipp_pcap(args: argparse.Namespace, command: List[str]) -> List[str]:
    if should_sudo_sipp_pcap(args):
        return ["sudo", "-n", *command]
    return command


def check_sudo_ready_for_sipp_pcap(args: argparse.Namespace) -> Tuple[bool, str]:
    if not should_sudo_sipp_pcap(args) or args.dry_run:
        return True, ""
    completed = subprocess.run(["sudo", "-n", "-v"], text=True, capture_output=True)
    detail = (completed.stderr.strip() or completed.stdout.strip() or f"returncode={completed.returncode}").strip()
    if completed.returncode == 0:
        return True, "sudo credentials are cached"
    return False, f"{SIPP_PCAP_SUDO_BLOCKED_DETAIL} sudo_check={detail}"


def check_rtpengine_preflight(url: str, timeout: float) -> Tuple[bool, str]:
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


def append_rtpengine_blocked_observations(log_dir: Path, args: argparse.Namespace, detail: str, duration: float) -> None:
    append_log_section(
        log_dir,
        "log.platform",
        "RTPENGINE PREFLIGHT BLOCKED",
        f"rtpengine_url={args.rtpengine_url}\nreason={detail}\nduration_seconds={duration:.3f}",
    )
    append_log_section(
        log_dir,
        "log.media",
        "MEDIA OBSERVATION",
        "\n".join(
            [
                f"expected_rtp={bool(args.media_enabled)} status=blocked",
                "media_backend=rtpengine",
                f"rtpengine_url={args.rtpengine_url}",
                f"reason={detail}",
            ]
        ),
    )
    transcoding_expected = bool(args.media_codec and args.server_codec and args.media_codec != args.server_codec)
    append_log_section(
        log_dir,
        "log.transcoding",
        "TRANSCODING OBSERVATION",
        "\n".join(
            [
                f"expected={transcoding_expected} status=blocked",
                "owner=rtpengine",
                f"reason={detail}",
            ]
        ),
    )


def append_sipp_pcap_sudo_blocked_observations(log_dir: Path, args: argparse.Namespace, detail: str, duration: float) -> None:
    append_log_section(
        log_dir,
        "log.platform",
        "SIPP PCAP SUDO PREFLIGHT BLOCKED",
        "\n".join(
            [
                f"reason={detail}",
                f"duration_seconds={duration:.3f}",
                "no_sipp_traffic_attempted=true",
                "next_step=run sudo -v in the same terminal before rerunning --sipp-pcap-sudo profiles",
            ]
        ),
    )
    append_log_section(
        log_dir,
        "log.media",
        "MEDIA OBSERVATION",
        "\n".join(
            [
                f"expected_rtp={bool(args.media_enabled)} status=blocked",
                f"media_backend={args.media_backend}",
                "reason=sipp_pcap_sudo_credentials_not_cached",
                "no_sipp_or_rtpengine_traffic_attempted=true",
            ]
        ),
    )
    transcoding_expected = bool(args.media_codec and args.server_codec and args.media_codec != args.server_codec)
    append_log_section(
        log_dir,
        "log.transcoding",
        "TRANSCODING OBSERVATION",
        "\n".join(
            [
                f"expected={transcoding_expected} status=blocked",
                f"owner={'rtpengine' if args.media_backend == 'rtpengine' else 'internal'}",
                "reason=sipp_pcap_sudo_credentials_not_cached",
            ]
        ),
    )


def is_transcoding_profile(args: argparse.Namespace) -> bool:
    media_codec = str(getattr(args, "media_codec", "") or "").upper()
    server_codec = str(getattr(args, "server_codec", "") or "").upper()
    return bool(media_codec and server_codec and media_codec != server_codec)


def uas_media_codec(args: argparse.Namespace) -> str:
    media_codec = str(getattr(args, "media_codec", "") or "PCMU").upper()
    server_codec = str(getattr(args, "server_codec", "") or media_codec).upper()
    return server_codec if is_transcoding_profile(args) else media_codec


def uac_sdp_payloads(args: argparse.Namespace) -> Tuple[str, str]:
    if is_transcoding_profile(args):
        codec = str(getattr(args, "media_codec", "") or "PCMU").upper()
        payload_type = MEDIA_PAYLOAD_TYPES[codec]
        return f"{payload_type} 101", MEDIA_RTPMAP_LINES[codec]
    return "0 8 101", "\n      ".join(MEDIA_RTPMAP_LINES[codec] for codec in ("PCMU", "PCMA"))


def uas_sdp_payloads(args: argparse.Namespace) -> Tuple[str, str]:
    if is_transcoding_profile(args):
        codec = uas_media_codec(args)
        payload_type = MEDIA_PAYLOAD_TYPES[codec]
        return f"{payload_type} 101", MEDIA_RTPMAP_LINES[codec]
    return "0 8 101", "\n      ".join(MEDIA_RTPMAP_LINES[codec] for codec in ("PCMU", "PCMA"))


def build_uas_command(args: argparse.Namespace, sipp_binary: str) -> List[str]:
    scenario = getattr(args, "uas_scenario", SCENARIO_DIR / ("b2bua_uas_b_media.xml" if args.media_enabled else "b2bua_uas_b.xml"))
    command = [
        sipp_binary,
        "-sf",
        str(scenario),
        "-s",
        args.callee,
        "-i",
        args.host,
        "-mi",
        args.host,
        "-p",
        str(args.uas_port),
        "-m",
        str(args.calls),
        "-l",
        str(call_limit(args.calls, args.rate, args.hold_ms)),
        "-timeout",
        str(sipp_timeout_seconds(args.calls, args.rate, args.hold_ms)),
        "-timeout_error",
        "-nostdin",
        "-min_rtp_port",
        str(args.uas_rtp_min),
        "-max_rtp_port",
        str(args.uas_rtp_max),
        "-trace_err",
        "-trace_msg",
        "-trace_stat",
        "-trace_counts",
        "-trace_logs",
    ]
    command.extend(sipp_transport_args(args, role="server"))
    return maybe_sudo_sipp_pcap(args, command)


def build_uac_command(args: argparse.Namespace, sipp_binary: str) -> List[str]:
    scenario = getattr(args, "uac_scenario", SCENARIO_DIR / ("b2bua_uac_a_media.xml" if args.media_enabled else "b2bua_uac_a.xml"))
    command = [
        sipp_binary,
        f"{args.host}:{args.server_port}",
        "-sf",
        str(scenario),
        "-s",
        args.callee,
        "-key",
        "caller",
        getattr(args, "caller", "sipp-a"),
        "-i",
        args.host,
        "-mi",
        args.host,
        "-p",
        str(args.uac_port),
        "-m",
        str(args.calls),
        "-r",
        str(args.rate),
        "-d",
        str(args.hold_ms),
        "-l",
        str(call_limit(args.calls, args.rate, args.hold_ms)),
        "-timeout",
        str(sipp_timeout_seconds(args.calls, args.rate, args.hold_ms)),
        "-timeout_error",
        "-nostdin",
        "-min_rtp_port",
        str(args.uac_rtp_min),
        "-max_rtp_port",
        str(args.uac_rtp_max),
        "-trace_err",
        "-trace_msg",
        "-trace_stat",
        "-trace_counts",
        "-trace_logs",
    ]
    command.extend(sipp_transport_args(args))
    return maybe_sudo_sipp_pcap(args, command)


def sipp_transport_args(args: argparse.Namespace, role: str = "client") -> List[str]:
    if str(getattr(args, "sip_transport", "udp")).lower() != "tcp":
        return []
    if role == "server":
        return ["-t", "t1"]
    return ["-t", "tn", "-max_socket", str(sipp_max_socket_limit(args))]


def sipp_max_socket_limit(args: argparse.Namespace) -> int:
    calls = int(getattr(args, "calls", BASE_DEFAULTS["calls"]))
    rate = int(getattr(args, "rate", BASE_DEFAULTS["rate"]))
    hold_ms = int(getattr(args, "hold_ms", BASE_DEFAULTS["hold_ms"]))
    return min(max(call_limit(calls, rate, hold_ms) + 16, 128), 1024)


def build_server_command(args: argparse.Namespace, work_dir: Path, log_dir: Path) -> List[str]:
    config_path = write_dynamic_config(args, work_dir, log_dir)
    return [
        sys.executable,
        str(ROOT / "mini_call_server.py"),
        "--config",
        str(config_path),
        "--debug",
    ]


def write_dynamic_config(args: argparse.Namespace, work_dir: Path, log_dir: Path) -> Path:
    config = {
        "sip_ip": args.host,
        "sip_port": args.server_port,
        "sip_transport": args.sip_transport,
        "rtp_min": args.server_rtp_min,
        "rtp_max": args.server_rtp_max,
        "log_dir": str(log_dir),
        "default_codec": args.server_codec,
        "auth_realm": "playsbc",
        "users": {},
        "bridge_rooms": ["bridge"],
        "b2bua_routes": {},
        "route_policies": [
            {
                "name": "registered-endpoints",
                "match": "*",
                "target": "registration",
                "priority": 10,
            }
        ],
        "b2bua_ladder_logs": args.ladder_enabled,
        "media_backend": args.media_backend,
        "rtpengine_url": args.rtpengine_url,
        "rtpengine_timeout": args.rtpengine_timeout,
        "reject_unknown_routes": args.reject_unknown_routes,
        "debug": True,
    }
    config_path = work_dir / "server-config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def prepare_media_scenarios(args: argparse.Namespace, run_dir: Path) -> None:
    if not args.media_enabled:
        args.uac_scenario = resolve_scenario_path(args.uac_scenario, SCENARIO_DIR / "b2bua_uac_a.xml")
        args.uas_scenario = resolve_scenario_path(args.uas_scenario, SCENARIO_DIR / "b2bua_uas_b.xml")
        args.media_pcap_resolved = ""
        return

    pcap_path = Path(args.media_pcap)
    if not pcap_path.is_absolute():
        pcap_path = SCENARIO_DIR / pcap_path
    if not pcap_path.exists():
        raise SystemExit(f"Media PCAP not found: {pcap_path}")
    args.media_pcap_resolved = str(pcap_path)
    uas_pcap_path = media_pcap_for_codec(uas_media_codec(args), pcap_path)
    args.uas_media_pcap_resolved = str(uas_pcap_path)

    if args.media_driver == "python":
        args.uac_scenario = SCENARIO_DIR / "b2bua_uac_a.xml"
        args.uas_scenario = SCENARIO_DIR / "b2bua_uas_b.xml"
        return

    replacements = {
        "uac_scenario": (SCENARIO_DIR / "b2bua_uac_a_media.xml", run_dir / "sipp-a-uac" / "b2bua_uac_a_media_resolved.xml"),
        "uas_scenario": (SCENARIO_DIR / "b2bua_uas_b_media.xml", run_dir / "sipp-b-uas" / "b2bua_uas_b_media_resolved.xml"),
    }
    for attr_name, (template, destination) in replacements.items():
        scenario_pcap = uas_pcap_path if attr_name == "uas_scenario" else pcap_path
        text = template.read_text(encoding="ISO-8859-1").replace("[media_pcap]", str(scenario_pcap))
        if attr_name == "uac_scenario":
            uac_payloads, uac_rtpmaps = uac_sdp_payloads(args)
            text = text.replace("[uac_sdp_payloads]", uac_payloads)
            text = text.replace("[uac_sdp_rtpmaps]", uac_rtpmaps)
        if attr_name == "uas_scenario":
            uas_payloads, uas_rtpmaps = uas_sdp_payloads(args)
            text = text.replace("[uas_sdp_payloads]", uas_payloads)
            text = text.replace("[uas_sdp_rtpmaps]", uas_rtpmaps)
        destination.write_text(text, encoding="ISO-8859-1")
        setattr(args, attr_name, destination.resolve())


def build_media_player_commands(args: argparse.Namespace) -> List[Tuple[str, List[str]]]:
    if not args.media_enabled or args.media_driver != "python":
        return []

    player = str(ROOT / "tools" / "play_g711_pcap_rtp.py")
    base = [
        sys.executable,
        player,
        "--pcap",
        args.media_pcap_resolved,
        "--host",
        args.host,
        "--duration-ms",
        str(args.hold_ms),
    ]
    return [
        ("media-a-to-b2bua", base + ["--port", str(args.server_rtp_min)]),
        ("media-b-to-b2bua", base + ["--port", str(args.server_rtp_min + 2)]),
    ]


def build_register_command(
    args: argparse.Namespace,
    sipp_binary: str,
    user: str,
    contact_port: int,
    local_port: Optional[int] = None,
) -> List[str]:
    bind_port = contact_port if local_port is None else local_port
    command = [
        sipp_binary,
        f"{args.host}:{args.server_port}",
        "-sf",
        str(SCENARIO_DIR / "register_contact.xml"),
        "-s",
        user,
        "-i",
        args.host,
        "-mi",
        args.host,
        "-p",
        str(bind_port),
        "-key",
        "contact_port",
        str(contact_port),
        "-m",
        "1",
        "-r",
        "1",
        "-timeout",
        "10",
        "-timeout_error",
        "-nostdin",
        "-trace_err",
        "-trace_msg",
        "-trace_stat",
        "-trace_counts",
        "-trace_logs",
    ]
    command.extend(sipp_transport_args(args))
    return command


def start_process(command: List[str], cwd: Path, stdout_path: Path) -> subprocess.Popen:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout = stdout_path.open("w", encoding="utf-8")
    process = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=subprocess.STDOUT)
    process.stdout_file = stdout  # type: ignore[attr-defined]
    return process


def stop_process(process: Optional[subprocess.Popen]) -> None:
    if not process:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    stdout = getattr(process, "stdout_file", None)
    if stdout:
        stdout.close()


def initialize_log_dir(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    for filename in LOG_FILES:
        path = log_dir / filename
        if not path.exists():
            path.write_text(f"{timestamp} | LOG START | file={filename}\n", encoding="utf-8")


def append_log_section(log_dir: Path, filename: str, title: str, body: str = "") -> None:
    initialize_log_dir(log_dir)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with (log_dir / filename).open("a", encoding="utf-8") as log_file:
        log_file.write(f"{timestamp} | {title}\n")
        if body:
            log_file.write(body.rstrip() + "\n")


def append_file_section(log_dir: Path, filename: str, title: str, path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.strip():
        append_log_section(log_dir, filename, title, text)


def append_commands(log_dir: Path, commands: List[Tuple[str, List[str]]]) -> None:
    lines = []
    for name, command in commands:
        lines.append(f"{name}: {shlex.join(command)}")
    append_log_section(log_dir, "log.sipp", "B2BUA SIPP COMMANDS", "\n".join(lines))


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return (~total) & 0xFFFF


def ethernet_ipv4_udp_packet(packet: PcapPacket, packet_id: int) -> bytes:
    payload = packet.payload
    src_ip = socket.inet_aton(packet.src_ip)
    dst_ip = socket.inet_aton(packet.dst_ip)
    udp_length = 8 + len(payload)
    total_length = 20 + udp_length
    ip_header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total_length, packet_id & 0xFFFF, 0, 64, 17, 0, src_ip, dst_ip)
    ip_header = ip_header[:10] + struct.pack("!H", checksum(ip_header)) + ip_header[12:]
    udp_header = struct.pack("!HHHH", packet.src_port & 0xFFFF, packet.dst_port & 0xFFFF, udp_length, 0)
    ethernet_header = b"\x02\x00\x00\x00\x00\x02" + b"\x02\x00\x00\x00\x00\x01" + struct.pack("!H", 0x0800)
    return ethernet_header + ip_header + udp_header + payload


def tcp_checksum(src_ip: bytes, dst_ip: bytes, tcp_segment: bytes) -> int:
    pseudo_header = src_ip + dst_ip + struct.pack("!BBH", 0, 6, len(tcp_segment))
    return checksum(pseudo_header + tcp_segment)


def ethernet_ipv4_tcp_packet(packet: PcapPacket, packet_id: int, seq: int, ack: int) -> bytes:
    payload = packet.payload
    src_ip = socket.inet_aton(packet.src_ip)
    dst_ip = socket.inet_aton(packet.dst_ip)
    tcp_offset_words = 5
    tcp_flags = 0x18  # PSH + ACK; synthetic captures preserve payload timing, not handshake setup.
    tcp_header = struct.pack(
        "!HHIIBBHHH",
        packet.src_port & 0xFFFF,
        packet.dst_port & 0xFFFF,
        seq & 0xFFFFFFFF,
        ack & 0xFFFFFFFF,
        tcp_offset_words << 4,
        tcp_flags,
        65535,
        0,
        0,
    )
    tcp_header = tcp_header[:16] + struct.pack("!H", tcp_checksum(src_ip, dst_ip, tcp_header + payload)) + tcp_header[18:]
    total_length = 20 + len(tcp_header) + len(payload)
    ip_header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total_length, packet_id & 0xFFFF, 0, 64, 6, 0, src_ip, dst_ip)
    ip_header = ip_header[:10] + struct.pack("!H", checksum(ip_header)) + ip_header[12:]
    ethernet_header = b"\x02\x00\x00\x00\x00\x02" + b"\x02\x00\x00\x00\x00\x01" + struct.pack("!H", 0x0800)
    return ethernet_header + ip_header + tcp_header + payload


def write_udp_pcap(path: Path, packets: List[PcapPacket]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tcp_sequence_by_flow: dict[Tuple[str, int, str, int], int] = {}
    with path.open("wb") as fh:
        fh.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for index, packet in enumerate(sorted(packets, key=lambda item: item.timestamp), start=1):
            if packet.protocol.lower() == "tcp":
                flow = (packet.src_ip, packet.src_port, packet.dst_ip, packet.dst_port)
                reverse_flow = (packet.dst_ip, packet.dst_port, packet.src_ip, packet.src_port)
                seq = tcp_sequence_by_flow.setdefault(flow, 1_000_000 + (len(tcp_sequence_by_flow) * 100_000))
                ack = tcp_sequence_by_flow.get(reverse_flow, 1)
                frame = ethernet_ipv4_tcp_packet(packet, index, seq, ack)
                tcp_sequence_by_flow[flow] = seq + max(len(packet.payload), 1)
            else:
                frame = ethernet_ipv4_udp_packet(packet, index)
            timestamp_seconds = int(packet.timestamp)
            timestamp_microseconds = int((packet.timestamp - timestamp_seconds) * 1_000_000)
            fh.write(struct.pack("<IIII", timestamp_seconds, timestamp_microseconds, len(frame), len(frame)))
            fh.write(frame)


def extract_rtp_payload(frame: bytes) -> bytes:
    if len(frame) < 14:
        return b""
    ether_type = struct.unpack("!H", frame[12:14])[0]
    if ether_type != 0x0800:
        return b""

    ip_offset = 14
    if len(frame) < ip_offset + 20:
        return b""
    version_ihl = frame[ip_offset]
    if version_ihl >> 4 != 4:
        return b""
    ihl = (version_ihl & 0x0F) * 4
    if frame[ip_offset + 9] != 17:
        return b""

    udp_offset = ip_offset + ihl
    rtp_offset = udp_offset + 8
    if len(frame) < rtp_offset + 12:
        return b""
    return frame[rtp_offset:]


def rtp_packets_from_pcap(path: Path, max_seconds: float) -> List[Tuple[float, bytes]]:
    if not path.exists():
        return []

    data = path.read_bytes()
    if len(data) < 24:
        return []

    magic = data[:4]
    if magic == b"\xd4\xc3\xb2\xa1":
        endian = "<"
    elif magic == b"\xa1\xb2\xc3\xd4":
        endian = ">"
    else:
        return []

    packets = []
    first_timestamp: Optional[float] = None
    offset = 24
    while offset + 16 <= len(data):
        ts_sec, ts_usec, included_len, _original_len = struct.unpack(f"{endian}IIII", data[offset : offset + 16])
        offset += 16
        frame = data[offset : offset + included_len]
        offset += included_len
        rtp = extract_rtp_payload(frame)
        if not rtp:
            continue

        timestamp = ts_sec + (ts_usec / 1_000_000)
        if first_timestamp is None:
            first_timestamp = timestamp
        relative_timestamp = timestamp - first_timestamp
        if max_seconds > 0 and relative_timestamp > max_seconds:
            break
        packets.append((relative_timestamp, rtp))
    return packets


def media_pcap_for_codec(codec: str, fallback: Path) -> Path:
    relative = MEDIA_PCAPS.get(codec.upper())
    if not relative:
        return fallback
    path = SCENARIO_DIR / relative
    return path if path.exists() else fallback


def media_capture_start_timestamp(log_dir: Path) -> float:
    media_log = log_dir / "log.media"
    if not media_log.exists():
        return time.time()
    for line in media_log.read_text(encoding="utf-8", errors="replace").splitlines():
        if "RTP PACKET RX" in line or "B2BUA ANSWERED" in line:
            return parse_log_timestamp(line)
    return time.time()


def is_invite_ack_payload(payload: bytes) -> bool:
    start_line = payload.split(b"\r\n", 1)[0].upper()
    if not start_line.startswith(b"ACK "):
        return False
    return re.search(rb"(?im)^CSeq\s*:\s*\d+\s+ACK\s*$", payload) is not None


def sip_ack_media_start_timestamp(work_dir: Path) -> Optional[float]:
    ack_timestamps = []
    for leg in ("sipp-a-uac", "sipp-b-uas"):
        for trace in sorted((work_dir / leg).glob("*_messages.log")):
            for timestamp, _direction, payload in sipp_trace_messages(trace):
                if is_invite_ack_payload(payload):
                    ack_timestamps.append(timestamp)
    if not ack_timestamps:
        return None
    return max(ack_timestamps) + 0.001


def with_rtp_payload_type(rtp: bytes, codec: str) -> bytes:
    payload_type = {"PCMU": 0, "PCMA": 8}.get(codec.upper())
    if payload_type is None or len(rtp) < 2:
        return rtp
    rewritten = bytearray(rtp)
    rewritten[1] = (rewritten[1] & 0x80) | payload_type
    return bytes(rewritten)


def with_rtp_stream_identity(rtp: bytes, codec: str, sequence: int, timestamp: int, ssrc: int) -> bytes:
    rewritten = bytearray(with_rtp_payload_type(rtp, codec))
    if len(rewritten) < 12:
        return bytes(rewritten)
    rewritten[2:4] = struct.pack("!H", sequence & 0xFFFF)
    rewritten[4:8] = struct.pack("!I", timestamp & 0xFFFFFFFF)
    rewritten[8:12] = struct.pack("!I", ssrc & 0xFFFFFFFF)
    return bytes(rewritten)


def sdp_audio_port(payload: bytes) -> Optional[int]:
    match = re.search(rb"(?im)^m=audio\s+(\d+)\s+RTP/AVP\b", payload)
    if not match:
        return None
    port = int(match.group(1))
    return port if 0 < port <= 65535 else None


def rtpengine_anchor_ports(work_dir: Path) -> Tuple[Optional[int], Optional[int]]:
    a_leg_port = None
    b_leg_port = None
    for trace in sorted((work_dir / "sipp-a-uac").glob("*_messages.log")):
        for _timestamp, direction, payload in sipp_trace_messages(trace):
            if direction == "received" and payload.startswith(b"SIP/2.0 200"):
                a_leg_port = sdp_audio_port(payload) or a_leg_port
    for trace in sorted((work_dir / "sipp-b-uas").glob("*_messages.log")):
        for _timestamp, direction, payload in sipp_trace_messages(trace):
            if direction == "received" and payload.startswith(b"INVITE "):
                b_leg_port = sdp_audio_port(payload) or b_leg_port
    return a_leg_port, b_leg_port


def rtpengine_anchor_port_set(work_dir: Path) -> Tuple[int, ...]:
    return tuple(port for port in rtpengine_anchor_ports(work_dir) if port is not None)


def rtp_media_packets(log_dir: Path, work_dir: Path, args: argparse.Namespace) -> List[PcapPacket]:
    if not getattr(args, "media_enabled", False):
        return []

    media_backend = str(getattr(args, "media_backend", BASE_DEFAULTS["media_backend"]))
    if media_backend != "rtpengine" and total_logged_rtp_packets(log_dir) <= 0:
        return []
    if media_backend == "rtpengine":
        media_text = (log_dir / "log.media").read_text(encoding="utf-8", errors="replace") if (log_dir / "log.media").exists() else ""
        if "RTPENGINE ANSWER" not in media_text:
            return []

    media_pcap = Path(str(getattr(args, "media_pcap_resolved", "") or ""))
    if not media_pcap.exists():
        return []

    media_codec = str(getattr(args, "media_codec", "") or "PCMU").upper()
    server_codec = str(getattr(args, "server_codec", "") or media_codec).upper()
    b_leg_codec = uas_media_codec(args)
    max_seconds = max(float(getattr(args, "hold_ms", 0) or 0) / 1000.0, 0.0) + 0.100
    endpoint_rtp = rtp_packets_from_pcap(media_pcap, max_seconds)
    if not endpoint_rtp:
        return []

    rtp_by_codec = {media_codec: endpoint_rtp}
    a_anchor_port = int(getattr(args, "server_rtp_min", BASE_DEFAULTS["server_rtp_min"]))
    b_anchor_port = a_anchor_port + 2
    if media_backend == "rtpengine":
        parsed_a_anchor, parsed_b_anchor = rtpengine_anchor_ports(work_dir)
        a_anchor_port = parsed_a_anchor or a_anchor_port
        b_anchor_port = parsed_b_anchor or b_anchor_port

    def samples_for_codec(codec: str) -> List[Tuple[float, bytes]]:
        normalized_codec = codec.upper()
        if normalized_codec not in rtp_by_codec:
            codec_samples = rtp_packets_from_pcap(media_pcap_for_codec(normalized_codec, media_pcap), max_seconds)
            rtp_by_codec[normalized_codec] = codec_samples or endpoint_rtp
        return rtp_by_codec[normalized_codec]

    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    media_anchor_ip = pcap_rtpengine_ip(args) if media_backend == "rtpengine" else server_ip
    endpoint_streams = [
        (media_codec, uac_ip, int(getattr(args, "uac_rtp_min", BASE_DEFAULTS["uac_rtp_min"])), media_anchor_ip, a_anchor_port, 0xA10A0001, 1000, 16000),
        (b_leg_codec, uas_ip, int(getattr(args, "uas_rtp_min", BASE_DEFAULTS["uas_rtp_min"])), media_anchor_ip, b_anchor_port, 0xB10B0002, 3000, 48000),
    ]
    server_streams = [
        (media_codec, media_anchor_ip, a_anchor_port, uac_ip, int(getattr(args, "uac_rtp_min", BASE_DEFAULTS["uac_rtp_min"])), 0xC10C0003, 5000, 80000),
        (server_codec, media_anchor_ip, b_anchor_port, uas_ip, int(getattr(args, "uas_rtp_min", BASE_DEFAULTS["uas_rtp_min"])), 0xD10D0004, 7000, 112000),
    ]

    base_time = sip_ack_media_start_timestamp(work_dir)
    if base_time is None:
        base_time = media_capture_start_timestamp(log_dir)

    packets = []
    for stream_codec, src_ip, src_port, dst_ip, dst_port, ssrc, sequence_base, timestamp_base in endpoint_streams:
        for index, (relative_timestamp, rtp) in enumerate(samples_for_codec(stream_codec)):
            payload_step = max(len(rtp) - 12, 160)
            payload = with_rtp_stream_identity(rtp, stream_codec, sequence_base + index, timestamp_base + (index * payload_step), ssrc)
            packets.append(PcapPacket(base_time + relative_timestamp, src_ip, src_port, dst_ip, dst_port, payload))
    for stream_codec, src_ip, src_port, dst_ip, dst_port, ssrc, sequence_base, timestamp_base in server_streams:
        for index, (relative_timestamp, rtp) in enumerate(samples_for_codec(stream_codec)):
            payload_step = max(len(rtp) - 12, 160)
            payload = with_rtp_stream_identity(rtp, stream_codec, sequence_base + index, timestamp_base + (index * payload_step), ssrc)
            packets.append(PcapPacket(base_time + relative_timestamp, src_ip, src_port, dst_ip, dst_port, payload))
    return packets


def parse_iso_timestamp(value: str) -> float:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%dT%H:%M:%S.%f").timestamp()
    except ValueError:
        return time.time()


def parse_log_timestamp(line: str) -> float:
    try:
        return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return time.time()


def sipp_trace_protocol_messages(path: Path) -> List[Tuple[float, str, str, bytes]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        r"^-{10,}\s+([0-9T:.\-]+)\n(UDP|TCP) message (sent|received) \[(\d+)\] bytes:\n\n",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    messages = []
    for index, match in enumerate(matches):
        payload_start = match.end()
        payload_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        payload = normalize_sip_payload(text[payload_start:payload_end])
        if not payload:
            continue
        messages.append((parse_iso_timestamp(match.group(1)), match.group(2).lower(), match.group(3), payload))
    return messages


def sipp_trace_messages(path: Path) -> List[Tuple[float, str, bytes]]:
    return [(timestamp, direction, payload) for timestamp, _protocol, direction, payload in sipp_trace_protocol_messages(path)]


def normalize_sip_payload(payload_text: str) -> bytes:
    normalized = payload_text.replace("\r\n", "\n").strip("\n")
    if not normalized.strip():
        return b""

    if "\n\n" in normalized:
        headers, body = normalized.split("\n\n", 1)
        body = body.rstrip("\n")
    else:
        headers, body = normalized, ""

    body_bytes = body.replace("\n", "\r\n").encode("utf-8")
    if re.search(r"(?im)^Content-Length\s*:", headers):
        headers = re.sub(
            r"(?im)^Content-Length\s*:\s*\d+",
            f"Content-Length: {len(body_bytes)}",
            headers,
            count=1,
        )
    header_bytes = headers.replace("\n", "\r\n").encode("utf-8")
    return header_bytes + b"\r\n\r\n" + body_bytes


def sipp_leg_port(args: argparse.Namespace, leg: str) -> Optional[int]:
    ports = {
        "registration-callee": getattr(args, "register_port", BASE_DEFAULTS["register_port"]),
        "registration-caller": getattr(args, "caller_register_port", BASE_DEFAULTS["caller_register_port"]),
        "sipp-a-uac": getattr(args, "uac_port", BASE_DEFAULTS["uac_port"]),
        "sipp-b-uas": getattr(args, "uas_port", BASE_DEFAULTS["uas_port"]),
    }
    return ports.get(leg)


def pcap_topology_ips(args: argparse.Namespace) -> Tuple[str, str, str]:
    if getattr(args, "pcap_topology", BASE_DEFAULTS["pcap_topology"]) == "runtime":
        host = getattr(args, "host", BASE_DEFAULTS["host"])
        return host, host, host
    return (
        getattr(args, "pcap_uac_ip", BASE_DEFAULTS["pcap_uac_ip"]),
        getattr(args, "pcap_server_ip", BASE_DEFAULTS["pcap_server_ip"]),
        getattr(args, "pcap_uas_ip", BASE_DEFAULTS["pcap_uas_ip"]),
    )


def pcap_rtpengine_ip(args: argparse.Namespace) -> str:
    if getattr(args, "pcap_topology", BASE_DEFAULTS["pcap_topology"]) == "runtime":
        return getattr(args, "host", BASE_DEFAULTS["host"])
    return getattr(args, "pcap_rtpengine_ip", BASE_DEFAULTS["pcap_rtpengine_ip"])


def pcap_leg_ip(args: argparse.Namespace, leg: str) -> str:
    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    leg_ips = {
        "registration-callee": uas_ip,
        "registration-caller": uac_ip,
        "sipp-a-uac": uac_ip,
        "sipp-b-uas": uas_ip,
    }
    return leg_ips.get(leg, server_ip)


def pcap_endpoint_ip(args: argparse.Namespace, endpoint: Optional[Tuple[str, int]]) -> str:
    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    if not endpoint:
        return server_ip

    _host, port = endpoint
    uac_port = int(getattr(args, "uac_port", BASE_DEFAULTS["uac_port"]))
    uas_port = int(getattr(args, "uas_port", BASE_DEFAULTS["uas_port"]))
    register_port = int(getattr(args, "register_port", BASE_DEFAULTS["register_port"]))
    caller_register_port = int(getattr(args, "caller_register_port", BASE_DEFAULTS["caller_register_port"]))
    server_port = int(getattr(args, "server_port", BASE_DEFAULTS["server_port"]))
    server_rtp_min = int(getattr(args, "server_rtp_min", BASE_DEFAULTS["server_rtp_min"]))
    server_rtp_max = int(getattr(args, "server_rtp_max", BASE_DEFAULTS["server_rtp_max"]))
    uac_rtp_min = int(getattr(args, "uac_rtp_min", BASE_DEFAULTS["uac_rtp_min"]))
    uac_rtp_max = int(getattr(args, "uac_rtp_max", BASE_DEFAULTS["uac_rtp_max"]))
    uas_rtp_min = int(getattr(args, "uas_rtp_min", BASE_DEFAULTS["uas_rtp_min"]))
    uas_rtp_max = int(getattr(args, "uas_rtp_max", BASE_DEFAULTS["uas_rtp_max"]))

    if port in {uac_port, caller_register_port}:
        return uac_ip
    if port in {uas_port, register_port}:
        return uas_ip
    if uac_rtp_min <= port <= uac_rtp_max:
        return uac_ip
    if uas_rtp_min <= port <= uas_rtp_max:
        return uas_ip
    if port == server_port or server_rtp_min <= port <= server_rtp_max:
        return server_ip
    if getattr(args, "pcap_topology", BASE_DEFAULTS["pcap_topology"]) == "runtime":
        return endpoint[0]
    return server_ip


def pcap_media_ip_for_port(args: argparse.Namespace, port: int, rtpengine_ports: Tuple[int, ...] = ()) -> Optional[str]:
    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    server_rtp_min = int(getattr(args, "server_rtp_min", BASE_DEFAULTS["server_rtp_min"]))
    server_rtp_max = int(getattr(args, "server_rtp_max", BASE_DEFAULTS["server_rtp_max"]))
    uac_rtp_min = int(getattr(args, "uac_rtp_min", BASE_DEFAULTS["uac_rtp_min"]))
    uac_rtp_max = int(getattr(args, "uac_rtp_max", BASE_DEFAULTS["uac_rtp_max"]))
    uas_rtp_min = int(getattr(args, "uas_rtp_min", BASE_DEFAULTS["uas_rtp_min"]))
    uas_rtp_max = int(getattr(args, "uas_rtp_max", BASE_DEFAULTS["uas_rtp_max"]))

    if port in rtpengine_ports:
        return pcap_rtpengine_ip(args)
    if uac_rtp_min <= port <= uac_rtp_max:
        return uac_ip
    if uas_rtp_min <= port <= uas_rtp_max:
        return uas_ip
    if server_rtp_min <= port <= server_rtp_max:
        return server_ip
    return None


def rewrite_sdp_topology_ip(body: str, args: argparse.Namespace, rtpengine_ports: Tuple[int, ...] = ()) -> str:
    media_match = re.search(r"(?m)^m=audio\s+(\d+)\s+RTP/AVP\b", body)
    if not media_match:
        return body

    media_ip = pcap_media_ip_for_port(args, int(media_match.group(1)), rtpengine_ports=rtpengine_ports)
    if not media_ip:
        return body

    body = re.sub(r"(?m)^c=IN\s+IP4\s+\S+", f"c=IN IP4 {media_ip}", body)
    return re.sub(
        r"(?m)^(o=\S+\s+\S+\s+\S+\s+IN\s+IP4\s+)\S+",
        lambda match: f"{match.group(1)}{media_ip}",
        body,
    )


def sip_topology_host_port_replacements(args: argparse.Namespace) -> List[Tuple[str, str]]:
    if getattr(args, "pcap_topology", BASE_DEFAULTS["pcap_topology"]) == "runtime":
        return []

    runtime_host = getattr(args, "host", BASE_DEFAULTS["host"])
    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    port_ips = {
        int(getattr(args, "uac_port", BASE_DEFAULTS["uac_port"])): uac_ip,
        int(getattr(args, "caller_register_port", BASE_DEFAULTS["caller_register_port"])): uac_ip,
        int(getattr(args, "uas_port", BASE_DEFAULTS["uas_port"])): uas_ip,
        int(getattr(args, "register_port", BASE_DEFAULTS["register_port"])): uas_ip,
        int(getattr(args, "server_port", BASE_DEFAULTS["server_port"])): server_ip,
        int(getattr(args, "server_rtp_min", BASE_DEFAULTS["server_rtp_min"])): server_ip,
        int(getattr(args, "server_rtp_min", BASE_DEFAULTS["server_rtp_min"])) + 2: server_ip,
        int(getattr(args, "uac_rtp_min", BASE_DEFAULTS["uac_rtp_min"])): uac_ip,
        int(getattr(args, "uas_rtp_min", BASE_DEFAULTS["uas_rtp_min"])): uas_ip,
    }
    return [(f"{runtime_host}:{port}", f"{logical_ip}:{port}") for port, logical_ip in sorted(port_ips.items())]


def rewrite_sip_headers_topology(headers: str, args: argparse.Namespace) -> str:
    rewritten = headers
    for runtime_endpoint, logical_endpoint in sip_topology_host_port_replacements(args):
        rewritten = rewritten.replace(runtime_endpoint, logical_endpoint)
    return rewritten


def logical_identity_ip_for_sip_message(args: argparse.Namespace, src_port: int, dst_port: int) -> str:
    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    uac_ports = {
        int(getattr(args, "uac_port", BASE_DEFAULTS["uac_port"])),
        int(getattr(args, "caller_register_port", BASE_DEFAULTS["caller_register_port"])),
    }
    uas_ports = {
        int(getattr(args, "uas_port", BASE_DEFAULTS["uas_port"])),
        int(getattr(args, "register_port", BASE_DEFAULTS["register_port"])),
    }
    if src_port in uas_ports or dst_port in uas_ports:
        return uas_ip
    if src_port in uac_ports or dst_port in uac_ports:
        return uac_ip
    return server_ip


def rewrite_bare_sip_identity_hosts(headers: str, args: argparse.Namespace, src_port: int, dst_port: int) -> str:
    if getattr(args, "pcap_topology", BASE_DEFAULTS["pcap_topology"]) == "runtime":
        return headers

    runtime_host = re.escape(str(getattr(args, "host", BASE_DEFAULTS["host"])))
    logical_ip = logical_identity_ip_for_sip_message(args, src_port, dst_port)
    bare_host_pattern = re.compile(rf"@{runtime_host}(?=([>;,\s]|$))")
    rewritten_lines = []
    for line in headers.split(CRLF):
        if line.lower().startswith("call-id:"):
            rewritten_lines.append(line)
        else:
            rewritten_lines.append(bare_host_pattern.sub(f"@{logical_ip}", line))
    return CRLF.join(rewritten_lines)


def rewrite_sip_payload_for_pcap(
    payload: bytes,
    args: argparse.Namespace,
    src_port: int,
    dst_port: int,
    rtpengine_ports: Tuple[int, ...] = (),
) -> bytes:
    separator = b"\r\n\r\n"
    if separator not in payload:
        return payload

    headers_bytes, body_bytes = payload.split(separator, 1)
    headers = headers_bytes.decode("utf-8", errors="replace")
    body = body_bytes.decode("utf-8", errors="replace")
    rewritten_headers = rewrite_sip_headers_topology(headers, args)
    rewritten_headers = rewrite_bare_sip_identity_hosts(rewritten_headers, args, src_port, dst_port)
    rewritten_body = rewrite_sdp_topology_ip(body, args, rtpengine_ports=rtpengine_ports) if "m=audio" in body else body
    if rewritten_headers == headers and rewritten_body == body:
        return payload

    rewritten_body_bytes = rewritten_body.encode("utf-8")
    if re.search(r"(?im)^Content-Length\s*:", rewritten_headers):
        rewritten_headers = re.sub(
            r"(?im)^Content-Length\s*:\s*\d+",
            f"Content-Length: {len(rewritten_body_bytes)}",
            rewritten_headers,
            count=1,
        )
    return rewritten_headers.encode("utf-8") + separator + rewritten_body_bytes


def sipp_trace_packets(work_dir: Path, args: argparse.Namespace) -> List[PcapPacket]:
    packets = []
    _uac_ip, server_ip, _uas_ip = pcap_topology_ips(args)
    rtpengine_ports = rtpengine_anchor_port_set(work_dir)
    for leg in ("registration-callee", "registration-caller", "sipp-a-uac", "sipp-b-uas"):
        local_port = sipp_leg_port(args, leg)
        if local_port is None:
            continue
        local_ip = pcap_leg_ip(args, leg)
        for trace in sorted((work_dir / leg).glob("*_messages.log")):
            for timestamp, protocol, direction, payload in sipp_trace_protocol_messages(trace):
                if direction == "sent":
                    src_port, dst_port = local_port, args.server_port
                    src_ip, dst_ip = local_ip, server_ip
                else:
                    src_port, dst_port = args.server_port, local_port
                    src_ip, dst_ip = server_ip, local_ip
                payload = rewrite_sip_payload_for_pcap(payload, args, src_port, dst_port, rtpengine_ports=rtpengine_ports)
                packets.append(PcapPacket(timestamp, src_ip, src_port, dst_ip, dst_port, payload, protocol=protocol))
    return packets


def parse_endpoint(text: str, key: str) -> Optional[Tuple[str, int]]:
    match = re.search(rf"{key}=([0-9.]+):(\d+)", text)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def protocol_event_packets(log_dir: Path, args: argparse.Namespace) -> List[PcapPacket]:
    packets = []
    _uac_ip, server_ip, _uas_ip = pcap_topology_ips(args)
    for filename in ("log.udp", "log.networking", "log.tcp", "log.tls"):
        path = log_dir / filename
        if not path.exists():
            continue
        protocol = "tcp" if filename in {"log.tcp", "log.tls"} else "udp"
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "LOG START" in line or not line.strip():
                continue
            timestamp = parse_log_timestamp(line)
            source = parse_endpoint(line, "source")
            destination = parse_endpoint(line, "destination")
            local = parse_endpoint(line, "local")
            if " RX " in line and source:
                src_ip = pcap_endpoint_ip(args, source)
                dst_ip = server_ip
            elif " TX " in line and destination:
                src_ip = server_ip
                dst_ip = pcap_endpoint_ip(args, destination)
            elif local:
                src_ip = pcap_endpoint_ip(args, local)
                dst_ip = src_ip
            else:
                src_ip = dst_ip = server_ip
            payload = f"PlaySBC diagnostic event | {line}\n".encode("utf-8")
            packets.append(PcapPacket(timestamp, src_ip, DIAGNOSTIC_PCAP_PORT, dst_ip, DIAGNOSTIC_PCAP_PORT, payload, protocol=protocol))
    return packets


def should_generate_pcap_artifacts(args: argparse.Namespace) -> bool:
    profile = str(getattr(args, "profile", "") or "")
    if profile.startswith("load-"):
        return False
    return args.calls == 1 and args.rate == 1


def generate_pcap_artifacts(log_dir: Path, work_dir: Path, args: argparse.Namespace) -> List[Path]:
    if args.dry_run or not should_generate_pcap_artifacts(args):
        return []

    sip_packets = sipp_trace_packets(work_dir, args)
    diagnostic_packets = protocol_event_packets(log_dir, args)
    rtp_packets = rtp_media_packets(log_dir, work_dir, args)
    packets = sip_packets + diagnostic_packets + rtp_packets
    if not packets:
        return []

    uac_ip, server_ip, uas_ip = pcap_topology_ips(args)
    rtpengine_ip = pcap_rtpengine_ip(args)
    pcap_path = log_dir / "capture.pcap"
    write_udp_pcap(pcap_path, packets)
    append_log_section(
        log_dir,
        "log.platform",
        "PCAP GENERATION",
        "\n".join(
            [
                "source=diagnostic_logs",
                "scope=non_load_b2bua_profile",
                "file=capture.pcap",
                f"packet_count={len(packets)}",
                f"sip_packets={len(sip_packets)}",
                f"rtp_packets={len(rtp_packets)}",
                f"diagnostic_packets={len(diagnostic_packets)}",
                f"udp_packets={sum(1 for packet in packets if packet.protocol.lower() == 'udp')}",
                f"tcp_packets={sum(1 for packet in packets if packet.protocol.lower() == 'tcp')}",
                f"topology={getattr(args, 'pcap_topology', BASE_DEFAULTS['pcap_topology'])}",
                f"topology_uac_ip={uac_ip}",
                f"topology_server_ip={server_ip}",
                f"topology_uas_ip={uas_ip}",
                f"topology_rtpengine_ip={rtpengine_ip}",
                "note=Single PCAP is generated from SIPp SIP traces, RTP media replay samples, and PlaySBC protocol logs after the call completes",
            ]
        ),
    )
    return [pcap_path]


def registration_ladder_text(participant: str, user: str) -> str:
    step_width = 6
    column_width = 28

    def row(step: str = "") -> List[str]:
        text = list(" " * (step_width + (column_width * 2)))
        for offset, char in enumerate(f"{step:<{step_width}}"):
            text[offset] = char
        for position in positions:
            text[position] = "|"
        return text

    def put(text: List[str], start: int, value: str) -> None:
        for offset, char in enumerate(value):
            position = start + offset
            if 0 <= position < len(text):
                text[position] = char

    positions = [step_width + (column_width // 2), step_width + column_width + (column_width // 2)]
    header = f"{'Step':<{step_width}}{participant:^{column_width}}{'B2BUA':^{column_width}}".rstrip()
    separator = "-" * (step_width + (column_width * 2))
    lines = ["REGISTRATION LADDER", f"user={user}", header, separator, "".join(row()).rstrip()]

    label = row("01")
    put(label, positions[0] + 2, "REGISTER")
    lines.append("".join(label).rstrip())
    arrow = row()
    for position in range(positions[0] + 1, positions[1] - 1):
        arrow[position] = "-"
    arrow[positions[1] - 1] = ">"
    lines.append("".join(arrow).rstrip())

    label = row("02")
    put(label, positions[0] + 2, "200 OK")
    lines.append("".join(label).rstrip())
    arrow = row()
    arrow[positions[0] + 1] = "<"
    for position in range(positions[0] + 2, positions[1]):
        arrow[position] = "-"
    lines.append("".join(arrow).rstrip())
    lines.append("".join(row()).rstrip())
    return "\n".join(lines)


def append_registration_ladders(log_dir: Path, args: argparse.Namespace, results: List[SmokeResult]) -> None:
    if not args.ladder_enabled:
        return
    statuses = {result.name: result.status for result in results}
    if statuses.get("registration") == "passed":
        append_log_section(
            log_dir,
            "log.sip",
            "CALLEE REGISTRATION LADDER",
            registration_ladder_text("SIPp B", args.callee),
        )
    if args.register_caller and statuses.get("caller-registration") == "passed":
        append_log_section(
            log_dir,
            "log.sip",
            "CALLER REGISTRATION LADDER",
            registration_ladder_text("SIPp A", args.caller),
        )


def total_logged_rtp_packets(log_dir: Path) -> int:
    path = log_dir / "log.media"
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    return sum(int(value) for value in re.findall(r"rtp_packets_received=(\d+)", text))


def media_summary_stats(log_dir: Path) -> dict:
    path = log_dir / "log.media"
    stats = {
        "summary_count": 0,
        "duration_seconds_max": 0.0,
        "rtp_packets_received_total": 0,
        "rtp_packets_sent_total": 0,
        "rtp_packets_relayed_total": 0,
    }
    if not path.exists():
        return stats

    pending_summary = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "CALL SUMMARY" in line:
            pending_summary = True
        if not pending_summary:
            continue
        stats["summary_count"] += 1
        duration = re.search(r"duration_seconds=([0-9.]+)", line)
        received = re.search(r"rtp_packets_received=(\d+)", line)
        sent = re.search(r"rtp_packets_sent=(\d+)", line)
        relayed = re.search(r"rtp_packets_relayed=(\d+)", line)
        if not any((duration, received, sent, relayed)):
            stats["summary_count"] -= 1
            continue
        pending_summary = False
        if duration:
            stats["duration_seconds_max"] = max(stats["duration_seconds_max"], float(duration.group(1)))
        if received:
            stats["rtp_packets_received_total"] += int(received.group(1))
        if sent:
            stats["rtp_packets_sent_total"] += int(sent.group(1))
        if relayed:
            stats["rtp_packets_relayed_total"] += int(relayed.group(1))
    return stats


def rtpengine_query_stats(log_dir: Path) -> dict:
    path = log_dir / "log.media"
    stats = {
        "query_count": 0,
        "rtp_packets_total": 0,
        "rtp_bytes_total": 0,
        "rtp_errors_total": 0,
    }
    if not path.exists():
        return stats

    def parse_query_detail(detail: str) -> bool:
        compact_packets = re.search(r"\brtp_packets_total=(\d+)", detail)
        compact_bytes = re.search(r"\brtp_bytes_total=(\d+)", detail)
        compact_errors = re.search(r"\brtp_errors_total=(\d+)", detail)
        if compact_packets:
            stats["rtp_packets_total"] += int(compact_packets.group(1))
            stats["rtp_bytes_total"] += int(compact_bytes.group(1)) if compact_bytes else 0
            stats["rtp_errors_total"] += int(compact_errors.group(1)) if compact_errors else 0
            return True

        if not detail.startswith("{"):
            return False
        try:
            decoded = json.loads(detail)
        except json.JSONDecodeError:
            return False
        rtp_totals = decoded.get("totals", {}).get("RTP", {}) if isinstance(decoded, dict) else {}
        stats["rtp_packets_total"] += int(rtp_totals.get("packets") or 0)
        stats["rtp_bytes_total"] += int(rtp_totals.get("bytes") or 0)
        stats["rtp_errors_total"] += int(rtp_totals.get("errors") or 0)
        return True

    pending_query = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "RTPENGINE QUERY" not in line:
            if pending_query:
                parse_query_detail(line)
                pending_query = False
            continue
        stats["query_count"] += 1

        _prefix, separator, payload = line.rpartition(" | ")
        if not separator or not parse_query_detail(payload):
            pending_query = True
    return stats


def append_media_observation(log_dir: Path, args: argparse.Namespace) -> None:
    if not args.media_enabled:
        append_log_section(
            log_dir,
            "log.media",
            "MEDIA OBSERVATION",
            "expected_rtp=False reason=media_disabled",
        )
        return

    if args.media_backend == "rtpengine":
        media_text = (log_dir / "log.media").read_text(encoding="utf-8", errors="replace") if (log_dir / "log.media").exists() else ""
        query_stats = rtpengine_query_stats(log_dir)
        rtpengine_answered = "RTPENGINE ANSWER" in media_text
        status = "rtpengine_media_anchored" if rtpengine_answered or query_stats["rtp_packets_total"] > 0 else "rtpengine_media_not_confirmed"
        append_log_section(
            log_dir,
            "log.media",
            "MEDIA OBSERVATION",
            "\n".join(
                [
                    f"expected_rtp=True status={status}",
                    "media_backend=rtpengine",
                    f"media_driver={args.media_driver}",
                    f"media_codec={args.media_codec}",
                    f"media_pcap={args.media_pcap_resolved}",
                    f"hold_ms={args.hold_ms}",
                    f"rtpengine_query_count={query_stats['query_count']}",
                    f"rtpengine_rtp_packets_total={query_stats['rtp_packets_total']}",
                    f"rtpengine_rtp_bytes_total={query_stats['rtp_bytes_total']}",
                    f"rtpengine_rtp_errors_total={query_stats['rtp_errors_total']}",
                    "server_rtp_received_packets_total=0",
                    "note=RTPengine anchors RTP externally, so PlaySBC internal RTP counters remain zero",
                ]
            ),
        )
        return

    packets = total_logged_rtp_packets(log_dir)
    status = "rtp_observed" if packets > 0 else "no_rtp_observed"
    append_log_section(
        log_dir,
        "log.media",
        "MEDIA OBSERVATION",
        "\n".join(
            [
                f"expected_rtp=True status={status}",
                f"media_driver={args.media_driver}",
                f"media_codec={args.media_codec}",
                f"media_pcap={args.media_pcap_resolved}",
                f"hold_ms={args.hold_ms}",
                f"server_rtp_received_packets_total={packets}",
            ]
        ),
    )


def append_transcoding_observation(log_dir: Path, args: argparse.Namespace) -> None:
    transcoding_expected = bool(args.media_codec and args.server_codec and args.media_codec != args.server_codec)
    if not transcoding_expected:
        append_log_section(
            log_dir,
            "log.transcoding",
            "TRANSCODING OBSERVATION",
            "expected=False reason=codec_match_or_media_disabled",
        )
        return

    path = log_dir / "log.transcoding"
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    media_text = (log_dir / "log.media").read_text(encoding="utf-8", errors="replace") if (log_dir / "log.media").exists() else ""
    if args.media_backend == "rtpengine":
        query_stats = rtpengine_query_stats(log_dir)
        delegated = "RTPENGINE CODEC POLICY" in media_text and "RTPENGINE ANSWER" in media_text
        if delegated and query_stats["rtp_packets_total"] > 0:
            status = "delegated_and_media_confirmed"
        elif delegated:
            status = "delegated"
        else:
            status = "not_confirmed"
        append_log_section(
            log_dir,
            "log.transcoding",
            "TRANSCODING OBSERVATION",
            "\n".join(
                [
                    f"expected=True status={status}",
                    f"src={args.media_codec} dst={args.server_codec}",
                    "owner=rtpengine",
                    f"rtpengine_query_count={query_stats['query_count']}",
                    f"rtpengine_rtp_packets_total={query_stats['rtp_packets_total']}",
                    f"rtpengine_rtp_bytes_total={query_stats['rtp_bytes_total']}",
                    f"rtpengine_rtp_errors_total={query_stats['rtp_errors_total']}",
                    "server_rtp_received_packets_total=0",
                    "note=RTPengine performs transcoding externally; validate media stats with RTPengine query/PCAP when available",
                ]
            ),
        )
        return

    active = "TRANSCODE ACTIVE" in text
    bypass = "TRANSCODE BYPASS" in text
    packets = total_logged_rtp_packets(log_dir)
    if active:
        status = "active"
    elif bypass:
        status = "bypassed"
    else:
        status = "not_observed"
    append_log_section(
        log_dir,
        "log.transcoding",
        "TRANSCODING OBSERVATION",
        "\n".join(
            [
                f"expected=True status={status}",
                f"src={args.media_codec} dst={args.server_codec}",
                f"owner={'rtpengine' if args.media_backend == 'rtpengine' else 'internal'}",
                f"server_rtp_received_packets_total={packets}",
                "note=TRANSCODE ACTIVE appears only after RTP packets arrive with a payload type that must be converted",
            ]
        ),
    )


def append_results(log_dir: Path, args: argparse.Namespace, results: List[SmokeResult]) -> None:
    transcoding_expected = bool(args.media_codec and args.server_codec and args.media_codec != args.server_codec)
    media_stats = media_summary_stats(log_dir) if args.media_enabled else None
    lines = [
        f"run_id={args.resolved_run_id}",
        f"log_folder={args.log_folder}",
        f"profile={args.profile or 'custom'}",
        f"caller={args.caller}",
        f"callee={args.callee}",
        f"register_callee={args.register_callee}",
        f"register_caller={args.register_caller}",
        f"start_uas={args.start_uas}",
        f"sip_transport={getattr(args, 'sip_transport', BASE_DEFAULTS['sip_transport'])}",
        f"reject_unknown_routes={args.reject_unknown_routes}",
        f"registration_driver={args.registration_driver}",
        f"calls={args.calls}",
        f"rate={args.rate}",
        f"hold_ms={args.hold_ms}",
        f"server_codec={args.server_codec}",
        f"media_enabled={args.media_enabled}",
        f"media_codec={args.media_codec or ''}",
        f"uas_media_codec={uas_media_codec(args) if args.media_enabled else ''}",
        f"media_driver={args.media_driver if args.media_enabled else ''}",
        f"sipp_pcap_sudo={args.sipp_pcap_sudo if args.media_enabled and args.media_driver == 'sipp-pcap' else False}",
        f"media_pcap={getattr(args, 'media_pcap_resolved', getattr(args, 'media_pcap', '') or '') if args.media_enabled else ''}",
        f"uas_media_pcap={getattr(args, 'uas_media_pcap_resolved', '') if args.media_enabled else ''}",
        f"media_backend={args.media_backend}",
        f"rtpengine_url={args.rtpengine_url if args.media_backend == 'rtpengine' else ''}",
        f"transcoding_expected={transcoding_expected}",
        f"transcoding_owner={'rtpengine' if transcoding_expected and args.media_backend == 'rtpengine' else 'internal' if transcoding_expected else ''}",
        f"pcap_topology={getattr(args, 'pcap_topology', BASE_DEFAULTS['pcap_topology'])}",
        f"pcap_uac_ip={pcap_topology_ips(args)[0]}",
        f"pcap_server_ip={pcap_topology_ips(args)[1]}",
        f"pcap_uas_ip={pcap_topology_ips(args)[2]}",
        f"pcap_rtpengine_ip={pcap_rtpengine_ip(args)}",
        f"ladder_enabled={args.ladder_enabled}",
        "",
    ]
    for result in results:
        code = "" if result.returncode is None else f" returncode={result.returncode}"
        duration_label = "process_lifetime_seconds" if result.name == "sipp-b-uas" else "duration_seconds"
        lines.append(f"{result.name}: {result.status}{code} {duration_label}={result.duration_seconds:.3f}")
    if media_stats and media_stats["summary_count"] > 0:
        lines.extend(
            [
                "",
                "MEDIA DURATION SUMMARY",
                f"media_call_summary_count={media_stats['summary_count']}",
                f"media_call_duration_seconds_max={media_stats['duration_seconds_max']:.3f}",
                f"media_rtp_packets_received_total={media_stats['rtp_packets_received_total']}",
                f"media_rtp_packets_sent_total={media_stats['rtp_packets_sent_total']}",
                f"media_rtp_packets_relayed_total={media_stats['rtp_packets_relayed_total']}",
                "media_duration_source=log.media CALL SUMMARY",
            ]
        )
    append_log_section(log_dir, "log.platform", "B2BUA SIPP RUN RESULT", "\n".join(lines))


def collect_work_logs(log_dir: Path, work_dir: Path) -> None:
    append_file_section(log_dir, "log.platform", "SERVER STDOUT", work_dir / "server" / "stdout.log")

    for leg in ("registration-callee", "registration-caller", "sipp-a-uac", "sipp-b-uas"):
        leg_dir = work_dir / leg
        append_file_section(log_dir, "log.sipp", f"{leg.upper()} STDOUT", leg_dir / "stdout.log")
        append_file_section(log_dir, "log.sipp", f"{leg.upper()} STDERR", leg_dir / "stderr.log")
        for trace in sorted(leg_dir.glob("*_messages.log")):
            append_file_section(log_dir, "log.sip", f"{leg.upper()} SIP TRACE {trace.name}", trace)
        for trace in sorted(leg_dir.glob("*_logs.log")):
            append_file_section(log_dir, "log.sipp", f"{leg.upper()} EVENT TRACE {trace.name}", trace)

    for media_log in sorted(work_dir.glob("media-*.log")):
        append_file_section(log_dir, "log.media", f"MEDIA PLAYER {media_log.name}", media_log)


def register_user(
    args: argparse.Namespace,
    log_dir: Path,
    user: str,
    contact_port: int,
    bind_port: int,
    label: str,
) -> int:
    branch = f"z9hG4bK-register-{int(time.time() * 1000)}"
    call_id = f"register-{user}-{int(time.time())}@{args.host}"
    packet = CRLF.join(
        [
            f"REGISTER sip:{args.host}:{args.server_port} SIP/2.0",
            f"Via: SIP/2.0/UDP {args.host}:{bind_port};branch={branch}",
            f"From: <sip:{user}@{args.host}>;tag=register-{user}",
            f"To: <sip:{user}@{args.host}>",
            f"Call-ID: {call_id}",
            "CSeq: 1 REGISTER",
            f"Contact: <sip:{user}@{args.host}:{contact_port}>",
            "Max-Forwards: 70",
            "Expires: 300",
            "Content-Length: 0",
            "",
            "",
        ]
    ).encode("utf-8")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.bind((args.host, bind_port))
        sock.sendto(packet, (args.host, args.server_port))
        response, _ = sock.recvfrom(4096)

    text = response.decode("utf-8", errors="replace")
    append_log_section(
        log_dir,
        "log.sip",
        f"DYNAMIC {label.upper()} REGISTER",
        packet.decode("utf-8", errors="replace") + "\n--- response ---\n" + text,
    )
    return 0 if "SIP/2.0 200" in text else 1


def register_endpoint(args: argparse.Namespace, log_dir: Path) -> int:
    return register_user(args, log_dir, args.callee, args.uas_port, args.register_port, "callee")


def register_caller(args: argparse.Namespace, log_dir: Path) -> int:
    return register_user(args, log_dir, args.caller, args.uac_port, args.caller_register_port, "caller")


def run_sipp_registration(command: List[str], work_dir: Path, label: str) -> int:
    step_dir = work_dir / label
    step_dir.mkdir(exist_ok=True)
    completed = subprocess.run(command, cwd=step_dir, text=True, capture_output=True)
    (step_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (step_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    return completed.returncode


def resolve_log_dir(args: argparse.Namespace, run_id: str) -> Tuple[Path, bool]:
    log_folder = args.log_folder or DEFAULT_LOG_FOLDER
    bundle_name = run_id.replace(os.sep, "-")
    if args.output_root:
        return Path(args.output_root) / log_folder / bundle_name, True
    if args.dry_run:
        return Path(tempfile.mkdtemp(prefix=f"{run_id}-")) / log_folder / bundle_name, True
    return ROOT / "logs" / log_folder / bundle_name, True


def apply_profile(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.profile:
        return
    defaults = parser.get_default
    for key, value in B2BUA_PROFILES[args.profile].items():
        if getattr(args, key, defaults(key)) == defaults(key):
            setattr(args, key, value)


def print_profiles() -> None:
    print("Available B2BUA SIPp profiles:")
    for name, description in PROFILE_DESCRIPTIONS.items():
        print(f"  {name}: {description}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run registrar-backed SIPp B2BUA smoke/load tests")
    parser.add_argument("--profile", choices=sorted(B2BUA_PROFILES), default="", help="Named B2BUA SIPp test profile")
    parser.add_argument("--list-profiles", action="store_true", help="List named B2BUA SIPp test profiles")
    parser.add_argument("--host", default=BASE_DEFAULTS["host"])
    parser.add_argument("--server-port", type=int, default=BASE_DEFAULTS["server_port"])
    parser.add_argument("--sip-transport", choices=("udp", "tcp", "udp,tcp"), default=BASE_DEFAULTS["sip_transport"], help="PlaySBC SIP listener transport for this run")
    parser.add_argument("--uac-port", type=int, default=BASE_DEFAULTS["uac_port"])
    parser.add_argument("--uas-port", type=int, default=BASE_DEFAULTS["uas_port"])
    parser.add_argument("--register-port", type=int, default=BASE_DEFAULTS["register_port"])
    parser.add_argument("--caller-register-port", type=int, default=BASE_DEFAULTS["caller_register_port"])
    parser.add_argument("--server-rtp-min", type=int, default=BASE_DEFAULTS["server_rtp_min"])
    parser.add_argument("--server-rtp-max", type=int, default=BASE_DEFAULTS["server_rtp_max"])
    parser.add_argument("--uac-rtp-min", type=int, default=BASE_DEFAULTS["uac_rtp_min"])
    parser.add_argument("--uac-rtp-max", type=int, default=BASE_DEFAULTS["uac_rtp_max"])
    parser.add_argument("--uas-rtp-min", type=int, default=BASE_DEFAULTS["uas_rtp_min"])
    parser.add_argument("--uas-rtp-max", type=int, default=BASE_DEFAULTS["uas_rtp_max"])
    parser.add_argument("--caller", default=BASE_DEFAULTS["caller"], help="SIP user used by SIPp A in From/Contact")
    parser.add_argument("--callee", default=BASE_DEFAULTS["callee"])
    parser.add_argument("--register-callee", dest="register_callee", action="store_true", default=BASE_DEFAULTS["register_callee"], help="REGISTER SIPp B before the UAC call")
    parser.add_argument("--no-register-callee", dest="register_callee", action="store_false", help="Skip callee registration")
    parser.add_argument("--register-caller", action="store_true", default=BASE_DEFAULTS["register_caller"], help="REGISTER the SIPp A caller before originating")
    parser.add_argument("--start-uas", dest="start_uas", action="store_true", default=BASE_DEFAULTS["start_uas"], help="Start the SIPp B UAS leg")
    parser.add_argument("--no-start-uas", dest="start_uas", action="store_false", help="Skip SIPp B UAS startup")
    parser.add_argument("--reject-unknown-routes", action="store_true", default=BASE_DEFAULTS["reject_unknown_routes"], help="Make PlaySBC reject unrouted INVITEs with 404 instead of echo mode")
    parser.add_argument("--calls", type=int, default=BASE_DEFAULTS["calls"])
    parser.add_argument("--rate", type=int, default=BASE_DEFAULTS["rate"])
    parser.add_argument("--hold-ms", type=int, default=BASE_DEFAULTS["hold_ms"])
    parser.add_argument("--media-codec", choices=sorted(MEDIA_PCAPS), default=BASE_DEFAULTS["media_codec"], help="Play 60s RTP PCAP media using this G.711 codec")
    parser.add_argument("--media-pcap", default=BASE_DEFAULTS["media_pcap"], help="Override the RTP PCAP file used with --media-codec")
    parser.add_argument("--media-driver", choices=("python", "sipp-pcap"), default=BASE_DEFAULTS["media_driver"], help="Use Python UDP replay or SIPp play_pcap_audio for media")
    parser.add_argument(
        "--sipp-pcap-sudo",
        action="store_true",
        default=BASE_DEFAULTS["sipp_pcap_sudo"],
        help="Temporary macOS workaround: run SIPp play_pcap_audio processes with sudo -n",
    )
    parser.add_argument("--media-start-delay", type=float, default=BASE_DEFAULTS["media_start_delay"], help="Seconds to wait after starting SIPp A before Python media replay starts")
    parser.add_argument("--server-codec", choices=sorted(MEDIA_PCAPS), default=BASE_DEFAULTS["server_codec"], help="Server preferred G.711 codec; set different from media codec to exercise transcoding")
    parser.add_argument("--media-backend", choices=("internal", "rtpengine"), default=BASE_DEFAULTS["media_backend"])
    parser.add_argument("--rtpengine-url", default=BASE_DEFAULTS["rtpengine_url"])
    parser.add_argument("--rtpengine-timeout", type=float, default=BASE_DEFAULTS["rtpengine_timeout"])
    parser.add_argument("--skip-rtpengine-preflight", action="store_true", help="Start the profile without checking RTPengine NG readiness first")
    parser.add_argument("--registration-driver", choices=("sipp", "python"), default=BASE_DEFAULTS["registration_driver"])
    parser.add_argument("--uac-scenario", default=BASE_DEFAULTS["uac_scenario"], help="Override SIPp UAC scenario XML")
    parser.add_argument("--uas-scenario", default=BASE_DEFAULTS["uas_scenario"], help="Override SIPp UAS scenario XML")
    parser.add_argument("--ladder", dest="ladder", action="store_true", default=BASE_DEFAULTS["ladder"], help="Force unified B2BUA ladder logs on")
    parser.add_argument("--no-ladder", dest="ladder", action="store_false", help="Force unified B2BUA ladder logs off")
    parser.add_argument("--output-root", default=BASE_DEFAULTS["output_root"])
    parser.add_argument(
        "--log-folder",
        default=BASE_DEFAULTS["log_folder"],
        help="Folder name used under logs/ as the parent for per-testcase B2BUA log bundles",
    )
    parser.add_argument(
        "--pcap-topology",
        choices=("logical", "runtime"),
        default=BASE_DEFAULTS["pcap_topology"],
        help="Use logical SIPp A/PlaySBC/SIPp B IPs in capture.pcap, or preserve runtime bind IPs",
    )
    parser.add_argument("--pcap-uac-ip", default=BASE_DEFAULTS["pcap_uac_ip"], help="Logical SIPp A IP written to capture.pcap")
    parser.add_argument("--pcap-server-ip", default=BASE_DEFAULTS["pcap_server_ip"], help="Logical PlaySBC IP written to capture.pcap")
    parser.add_argument("--pcap-uas-ip", default=BASE_DEFAULTS["pcap_uas_ip"], help="Logical SIPp B IP written to capture.pcap")
    parser.add_argument("--pcap-rtpengine-ip", default=BASE_DEFAULTS["pcap_rtpengine_ip"], help="Logical RTPengine media-anchor IP written to capture.pcap")
    parser.add_argument("--run-id", default=BASE_DEFAULTS["run_id"])
    parser.add_argument("--sipp-bin", default=BASE_DEFAULTS["sipp_bin"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.list_profiles:
        print_profiles()
        return 0
    apply_profile(args, parser)
    args.ladder_enabled = args.ladder if args.ladder is not None else (args.calls == 1 and args.rate == 1)
    args.media_enabled = bool(args.media_codec)
    args.media_pcap = args.media_pcap or (MEDIA_PCAPS[args.media_codec] if args.media_codec else "")
    args.server_codec = args.server_codec or args.media_codec or "PCMU"

    run_prefix = args.profile or ("b2bua-media" if args.media_enabled else "b2bua-signalling")
    run_id = args.run_id or make_run_id(run_prefix)
    args.resolved_run_id = run_id
    log_dir, needs_create = resolve_log_dir(args, run_id)
    if needs_create:
        log_dir.mkdir(parents=True, exist_ok=True)
    initialize_log_dir(log_dir)

    if args.media_backend == "rtpengine" and not args.skip_rtpengine_preflight and not args.dry_run:
        started = time.monotonic()
        ready, detail = check_rtpengine_preflight(args.rtpengine_url, args.rtpengine_timeout)
        duration = time.monotonic() - started
        if not ready:
            result = SmokeResult("rtpengine-preflight", [], None, "blocked", duration)
            append_rtpengine_blocked_observations(log_dir, args, detail, duration)
            append_results(log_dir, args, [result])
            print(f"B2BUA SIPp logs: {log_dir}")
            print(f"{result.name}: {result.status}")
            return 2

    started = time.monotonic()
    sudo_ready, sudo_detail = check_sudo_ready_for_sipp_pcap(args)
    sudo_duration = time.monotonic() - started
    if not sudo_ready:
        result = SmokeResult("sipp-pcap-sudo-preflight", [], None, "blocked", sudo_duration)
        append_sipp_pcap_sudo_blocked_observations(log_dir, args, sudo_detail, sudo_duration)
        append_results(log_dir, args, [result])
        print(f"B2BUA SIPp logs: {log_dir}")
        print(f"{result.name}: {result.status}")
        return 2
    if should_sudo_sipp_pcap(args):
        append_log_section(
            log_dir,
            "log.platform",
            "SIPP PCAP SUDO PREFLIGHT OK",
            f"detail={sudo_detail}\nduration_seconds={sudo_duration:.3f}",
        )

    results: List[SmokeResult] = []
    with tempfile.TemporaryDirectory(prefix=f"{run_id}-work-") as work_tmp:
        work_dir = Path(work_tmp)
        for name in ("server", "sipp-a-uac", "sipp-b-uas"):
            (work_dir / name).mkdir()
        prepare_media_scenarios(args, work_dir)

        sipp_binary = resolve_binary(args.sipp_bin)
        if not sipp_binary and not args.dry_run:
            raise SystemExit("SIPp executable not found")
        sipp = sipp_binary or args.sipp_bin
        server_command = build_server_command(args, work_dir, log_dir)
        uas_command = build_uas_command(args, sipp)
        uac_command = build_uac_command(args, sipp)
        media_commands = build_media_player_commands(args)
        callee_register_command = build_register_command(args, sipp, args.callee, args.uas_port, args.register_port)
        caller_register_command = (
            build_register_command(args, sipp, args.caller, args.uac_port, args.caller_register_port)
            if args.register_caller
            else []
        )
        all_commands = [("server", server_command)]
        if args.registration_driver == "sipp" and args.register_callee:
            all_commands.append(("registration-callee", callee_register_command))
        if args.start_uas:
            all_commands.append(("sipp-b-uas", uas_command))
        if args.registration_driver == "sipp" and caller_register_command:
            all_commands.append(("registration-caller", caller_register_command))
        all_commands.append(("sipp-a-uac", uac_command))
        all_commands.extend(media_commands)

        server_process: Optional[subprocess.Popen] = None
        uas_process: Optional[subprocess.Popen] = None
        uas_started: Optional[float] = None
        media_processes: List[Tuple[str, List[str], subprocess.Popen, float]] = []
        try:
            if args.dry_run:
                results.append(SmokeResult("server", server_command, None, "dry-run", 0.0))
                if args.registration_driver == "sipp" and args.register_callee:
                    results.append(SmokeResult("registration-callee", callee_register_command, None, "dry-run", 0.0))
                if args.start_uas:
                    results.append(SmokeResult("sipp-b-uas", uas_command, None, "dry-run", 0.0))
                if args.registration_driver == "sipp" and caller_register_command:
                    results.append(SmokeResult("registration-caller", caller_register_command, None, "dry-run", 0.0))
                results.append(SmokeResult("sipp-a-uac", uac_command, None, "dry-run", 0.0))
                for name, command in media_commands:
                    results.append(SmokeResult(name, command, None, "dry-run", 0.0))
                print(f"B2BUA SIPp logs: {log_dir}")
                for result in results:
                    print(f"{result.name}: {result.status}")
                return 0

            server_process = start_process(server_command, ROOT, work_dir / "server" / "stdout.log")
            time.sleep(0.75)
            if server_process.poll() is not None:
                raise RuntimeError(f"Mini call server exited early. See {log_dir / 'log.platform'}")

            started = time.monotonic()
            if args.register_callee:
                if args.registration_driver == "sipp":
                    registration_rc = run_sipp_registration(callee_register_command, work_dir, "registration-callee")
                else:
                    registration_rc = register_endpoint(args, log_dir)
                results.append(SmokeResult("registration", [], registration_rc, "passed" if registration_rc == 0 else "failed", time.monotonic() - started))

            if args.start_uas:
                uas_started = time.monotonic()
                uas_process = start_process(uas_command, work_dir / "sipp-b-uas", work_dir / "sipp-b-uas" / "stdout.log")
                time.sleep(0.75)

            if args.register_caller:
                started = time.monotonic()
                if args.registration_driver == "sipp":
                    caller_registration_rc = run_sipp_registration(caller_register_command, work_dir, "registration-caller")
                else:
                    caller_registration_rc = register_caller(args, log_dir)
                results.append(
                    SmokeResult(
                        "caller-registration",
                        [],
                        caller_registration_rc,
                        "passed" if caller_registration_rc == 0 else "failed",
                        time.monotonic() - started,
                    )
                )

            started = time.monotonic()
            uac_stdout = (work_dir / "sipp-a-uac" / "stdout.log").open("w", encoding="utf-8")
            uac_stderr = (work_dir / "sipp-a-uac" / "stderr.log").open("w", encoding="utf-8")
            try:
                uac_process = subprocess.Popen(uac_command, cwd=work_dir / "sipp-a-uac", stdout=uac_stdout, stderr=uac_stderr, text=True)
                if media_commands:
                    time.sleep(args.media_start_delay)
                    for name, command in media_commands:
                        media_started = time.monotonic()
                        process = start_process(command, ROOT, work_dir / f"{name}.log")
                        media_processes.append((name, command, process, media_started))
                uac_rc = uac_process.wait()
            finally:
                uac_stdout.close()
                uac_stderr.close()
            results.append(SmokeResult("sipp-a-uac", uac_command, uac_rc, "passed" if uac_rc == 0 else "failed", time.monotonic() - started))

            for name, command, process, media_started in media_processes:
                try:
                    media_rc = process.wait(timeout=max(5, int(args.hold_ms / 1000) + 10))
                except subprocess.TimeoutExpired:
                    stop_process(process)
                    media_rc = process.returncode if process.returncode is not None else 1
                results.append(SmokeResult(name, command, media_rc, "passed" if media_rc == 0 else "failed", time.monotonic() - media_started))
            media_processes = []

            if uas_process is not None:
                uas_rc = uas_process.wait(timeout=max(30, int(args.hold_ms / 1000) + 30))
                uas_duration = time.monotonic() - uas_started if uas_started is not None else 0.0
                results.append(SmokeResult("sipp-b-uas", uas_command, uas_rc, "passed" if uas_rc == 0 else "failed", uas_duration))
                uas_process = None
        finally:
            for _name, _command, process, _started in media_processes:
                stop_process(process)
            stop_process(uas_process)
            stop_process(server_process)
            append_commands(log_dir, all_commands)
            collect_work_logs(log_dir, work_dir)
            append_registration_ladders(log_dir, args, results)
            append_media_observation(log_dir, args)
            append_transcoding_observation(log_dir, args)
            generate_pcap_artifacts(log_dir, work_dir, args)
            append_results(log_dir, args, results)

    print(f"B2BUA SIPp logs: {log_dir}")
    for result in results:
        print(f"{result.name}: {result.status}")
    failed = [result for result in results if result.status == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
