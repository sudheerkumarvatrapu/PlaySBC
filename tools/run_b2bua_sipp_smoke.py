#!/usr/bin/env python3
"""Run a registrar-backed SIPp B2BUA smoke or small load test."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "sipp" / "scenarios"
MEDIA_PCAPS = {
    "PCMU": "pcap/g711u_60s.pcap",
    "PCMA": "pcap/g711a_60s.pcap",
}
CRLF = "\r\n"
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
BASE_DEFAULTS = {
    "host": "127.0.0.1",
    "server_port": 25062,
    "uac_port": 25081,
    "uas_port": 25082,
    "register_port": 25083,
    "caller_register_port": 25084,
    "server_rtp_min": 25100,
    "server_rtp_max": 25400,
    "uac_rtp_min": 26000,
    "uac_rtp_max": 26200,
    "uas_rtp_min": 27000,
    "uas_rtp_max": 27200,
    "caller": "sipp-a",
    "callee": "callee",
    "register_caller": False,
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
    "dry_run": False,
}
B2BUA_PROFILES = {
    "basic-signalling": {
        "callee": "basic-sig",
    },
    "basic-media": {
        "callee": "basic-media",
        "media_codec": "PCMU",
    },
    "transcoding": {
        "callee": "transcode-user",
        "media_codec": "PCMU",
        "server_codec": "PCMA",
    },
    "rtpengine": {
        "callee": "rtpengine-user",
        "media_backend": "rtpengine",
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
    "load-5cps-60s": {
        "callee": "load-user",
        "calls": 5,
        "rate": 5,
        "hold_ms": 60000,
        "ladder": False,
    },
    "load-5cps-60s-rtpengine-transcoding": {
        "callee": "load-rtpengine-transcode",
        "calls": 5,
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
    "basic-media": "One registered B2BUA call with PCMU RTP replay.",
    "transcoding": "One registered B2BUA media call with PCMU media and PCMA server codec preference.",
    "rtpengine": "One registered B2BUA signalling call using RTPengine as the media backend.",
    "registered-inbound": "Register SIPp B, then call that registered number through the B2BUA.",
    "registered-outbound": "Register SIPp A and SIPp B, then originate from the registered SIPp A user.",
    "load-5cps-60s": "Basic 5 cps, 60 second CHT load shape with ladder disabled.",
    "load-5cps-60s-rtpengine-transcoding": "5 cps, 60 second CHT profile with RTPengine backend and PCMU-to-PCMA transcoding intent.",
}


@dataclass
class SmokeResult:
    name: str
    command: List[str]
    returncode: Optional[int]
    status: str
    duration_seconds: float


def make_run_id(prefix: str = "b2bua") -> str:
    return time.strftime(f"{prefix}-%Y%m%d-%H%M%S", time.localtime())


def resolve_binary(candidate: str) -> Optional[str]:
    if os.sep in candidate:
        return candidate if Path(candidate).exists() else None
    return shutil.which(candidate)


def call_limit(calls: int, rate: int, hold_ms: int) -> int:
    estimated_concurrent = int((rate * max(hold_ms, 1)) / 1000) + rate + 2
    return max(calls, estimated_concurrent, 3)


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


def ensure_sudo_ready_for_sipp_pcap(args: argparse.Namespace) -> None:
    if not should_sudo_sipp_pcap(args) or args.dry_run:
        return
    completed = subprocess.run(["sudo", "-n", "true"], text=True, capture_output=True)
    if completed.returncode != 0:
        raise SystemExit(
            "SIPp PCAP sudo mode requires cached sudo credentials. "
            "Run `sudo -v` in your terminal, then retry with `--sipp-pcap-sudo`."
        )


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
        str(max(30, int(args.hold_ms / 1000) + 30)),
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
        str(max(30, int(args.hold_ms / 1000) + 30)),
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
    return maybe_sudo_sipp_pcap(args, command)


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

    if args.media_driver == "python":
        args.uac_scenario = SCENARIO_DIR / "b2bua_uac_a.xml"
        args.uas_scenario = SCENARIO_DIR / "b2bua_uas_b.xml"
        return

    replacements = {
        "uac_scenario": (SCENARIO_DIR / "b2bua_uac_a_media.xml", run_dir / "sipp-a-uac" / "b2bua_uac_a_media_resolved.xml"),
        "uas_scenario": (SCENARIO_DIR / "b2bua_uas_b_media.xml", run_dir / "sipp-b-uas" / "b2bua_uas_b_media_resolved.xml"),
    }
    for attr_name, (template, destination) in replacements.items():
        text = template.read_text(encoding="ISO-8859-1").replace("[media_pcap]", str(pcap_path))
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


def build_register_command(args: argparse.Namespace, sipp_binary: str, user: str, contact_port: int) -> List[str]:
    return [
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


def append_results(log_dir: Path, args: argparse.Namespace, results: List[SmokeResult]) -> None:
    transcoding_expected = bool(args.media_codec and args.server_codec and args.media_codec != args.server_codec)
    lines = [
        f"run_id={args.resolved_run_id}",
        f"log_folder={args.log_folder}",
        f"profile={args.profile or 'custom'}",
        f"caller={args.caller}",
        f"callee={args.callee}",
        f"register_caller={args.register_caller}",
        f"registration_driver={args.registration_driver}",
        f"calls={args.calls}",
        f"rate={args.rate}",
        f"hold_ms={args.hold_ms}",
        f"server_codec={args.server_codec}",
        f"media_enabled={args.media_enabled}",
        f"media_codec={args.media_codec or ''}",
        f"media_driver={args.media_driver if args.media_enabled else ''}",
        f"sipp_pcap_sudo={args.sipp_pcap_sudo if args.media_enabled and args.media_driver == 'sipp-pcap' else False}",
        f"media_pcap={args.media_pcap_resolved if args.media_enabled else ''}",
        f"media_backend={args.media_backend}",
        f"rtpengine_url={args.rtpengine_url if args.media_backend == 'rtpengine' else ''}",
        f"transcoding_expected={transcoding_expected}",
        f"transcoding_owner={'rtpengine' if transcoding_expected and args.media_backend == 'rtpengine' else 'internal' if transcoding_expected else ''}",
        f"ladder_enabled={args.ladder_enabled}",
        "",
    ]
    for result in results:
        code = "" if result.returncode is None else f" returncode={result.returncode}"
        lines.append(f"{result.name}: {result.status}{code} duration_seconds={result.duration_seconds:.3f}")
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
    if args.output_root:
        return Path(args.output_root) / log_folder, True
    if args.dry_run:
        return Path(tempfile.mkdtemp(prefix=f"{run_id}-")) / log_folder, True
    return ROOT / "logs" / log_folder, True


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
    parser.add_argument("--register-caller", action="store_true", default=BASE_DEFAULTS["register_caller"], help="REGISTER the SIPp A caller before originating")
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
    parser.add_argument("--registration-driver", choices=("sipp", "python"), default=BASE_DEFAULTS["registration_driver"])
    parser.add_argument("--uac-scenario", default=BASE_DEFAULTS["uac_scenario"], help="Override SIPp UAC scenario XML")
    parser.add_argument("--uas-scenario", default=BASE_DEFAULTS["uas_scenario"], help="Override SIPp UAS scenario XML")
    parser.add_argument("--ladder", dest="ladder", action="store_true", default=BASE_DEFAULTS["ladder"], help="Force unified B2BUA ladder logs on")
    parser.add_argument("--no-ladder", dest="ladder", action="store_false", help="Force unified B2BUA ladder logs off")
    parser.add_argument("--output-root", default=BASE_DEFAULTS["output_root"])
    parser.add_argument(
        "--log-folder",
        default=BASE_DEFAULTS["log_folder"],
        help="Single folder name used under the log root for consolidated B2BUA regression logs",
    )
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
        ensure_sudo_ready_for_sipp_pcap(args)
        server_command = build_server_command(args, work_dir, log_dir)
        uas_command = build_uas_command(args, sipp)
        uac_command = build_uac_command(args, sipp)
        media_commands = build_media_player_commands(args)
        callee_register_command = build_register_command(args, sipp, args.callee, args.uas_port)
        caller_register_command = build_register_command(args, sipp, args.caller, args.uac_port) if args.register_caller else []
        all_commands = [("server", server_command)]
        if args.registration_driver == "sipp":
            all_commands.append(("registration-callee", callee_register_command))
        all_commands.append(("sipp-b-uas", uas_command))
        if args.registration_driver == "sipp" and caller_register_command:
            all_commands.append(("registration-caller", caller_register_command))
        all_commands.append(("sipp-a-uac", uac_command))
        all_commands.extend(media_commands)

        server_process: Optional[subprocess.Popen] = None
        uas_process: Optional[subprocess.Popen] = None
        media_processes: List[Tuple[str, List[str], subprocess.Popen, float]] = []
        try:
            if args.dry_run:
                results.append(SmokeResult("server", server_command, None, "dry-run", 0.0))
                if args.registration_driver == "sipp":
                    results.append(SmokeResult("registration-callee", callee_register_command, None, "dry-run", 0.0))
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
            if args.registration_driver == "sipp":
                registration_rc = run_sipp_registration(callee_register_command, work_dir, "registration-callee")
            else:
                registration_rc = register_endpoint(args, log_dir)
            results.append(SmokeResult("registration", [], registration_rc, "passed" if registration_rc == 0 else "failed", time.monotonic() - started))

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

            started = time.monotonic()
            uas_rc = uas_process.wait(timeout=max(30, int(args.hold_ms / 1000) + 30))
            results.append(SmokeResult("sipp-b-uas", uas_command, uas_rc, "passed" if uas_rc == 0 else "failed", time.monotonic() - started))
            uas_process = None
        finally:
            for _name, _command, process, _started in media_processes:
                stop_process(process)
            stop_process(uas_process)
            stop_process(server_process)
            append_commands(log_dir, all_commands)
            collect_work_logs(log_dir, work_dir)
            append_results(log_dir, args, results)

    print(f"B2BUA SIPp logs: {log_dir}")
    for result in results:
        print(f"{result.name}: {result.status}")
    failed = [result for result in results if result.status == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
