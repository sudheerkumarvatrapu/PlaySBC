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
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "sipp" / "scenarios"
CRLF = "\r\n"


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


def build_uas_command(args: argparse.Namespace, sipp_binary: str) -> List[str]:
    return [
        sipp_binary,
        "-sf",
        str(SCENARIO_DIR / "b2bua_uas_b.xml"),
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


def build_uac_command(args: argparse.Namespace, sipp_binary: str) -> List[str]:
    return [
        sipp_binary,
        f"{args.host}:{args.server_port}",
        "-sf",
        str(SCENARIO_DIR / "b2bua_uac_a.xml"),
        "-s",
        args.callee,
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


def build_server_command(args: argparse.Namespace, run_dir: Path) -> List[str]:
    config_path = write_dynamic_config(args, run_dir)
    return [
        sys.executable,
        str(ROOT / "mini_call_server.py"),
        "--config",
        str(config_path),
        "--artifact-root",
        str(run_dir / "server-artifacts"),
        "--run-id",
        "server",
        "--debug",
    ]


def write_dynamic_config(args: argparse.Namespace, run_dir: Path) -> Path:
    config = {
        "sip_ip": args.host,
        "sip_port": args.server_port,
        "rtp_min": args.server_rtp_min,
        "rtp_max": args.server_rtp_max,
        "log_dir": "logs",
        "recording_dir": "recordings",
        "artifact_root": "artifacts",
        "run_id": "",
        "default_codec": "PCMU",
        "auth_realm": "mini-call-server",
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
        "debug": True,
    }
    config_path = run_dir / "server-config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def start_process(command: List[str], cwd: Path, stdout_path: Path) -> subprocess.Popen:
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


def register_endpoint(args: argparse.Namespace, run_dir: Path) -> int:
    branch = f"z9hG4bK-register-{int(time.time() * 1000)}"
    call_id = f"register-{args.callee}-{int(time.time())}@{args.host}"
    packet = CRLF.join(
        [
            f"REGISTER sip:{args.host}:{args.server_port} SIP/2.0",
            f"Via: SIP/2.0/UDP {args.host}:{args.register_port};branch={branch}",
            f"From: <sip:{args.callee}@{args.host}>;tag=register-{args.callee}",
            f"To: <sip:{args.callee}@{args.host}>",
            f"Call-ID: {call_id}",
            "CSeq: 1 REGISTER",
            f"Contact: <sip:{args.callee}@{args.host}:{args.uas_port}>",
            "Max-Forwards: 70",
            "Expires: 300",
            "Content-Length: 0",
            "",
            "",
        ]
    ).encode("utf-8")

    transcript = run_dir / "registration.log"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.bind((args.host, args.register_port))
        sock.sendto(packet, (args.host, args.server_port))
        response, _ = sock.recvfrom(4096)

    text = response.decode("utf-8", errors="replace")
    transcript.write_text(
        packet.decode("utf-8", errors="replace") + "\n--- response ---\n" + text,
        encoding="utf-8",
    )
    return 0 if "SIP/2.0 200" in text else 1


def write_summary(run_dir: Path, args: argparse.Namespace, results: List[SmokeResult]) -> None:
    flow_logs = sorted(str(path) for path in (run_dir / "server-artifacts").glob("**/logs/b2bua_*.log"))
    payload = {
        "run_dir": str(run_dir),
        "callee": args.callee,
        "calls": args.calls,
        "rate": args.rate,
        "hold_ms": args.hold_ms,
        "flow_logs": flow_logs,
        "results": [asdict(result) for result in results],
    }
    (run_dir / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run registrar-backed SIPp B2BUA smoke/load tests")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=25062)
    parser.add_argument("--uac-port", type=int, default=25081)
    parser.add_argument("--uas-port", type=int, default=25082)
    parser.add_argument("--register-port", type=int, default=25083)
    parser.add_argument("--server-rtp-min", type=int, default=25100)
    parser.add_argument("--server-rtp-max", type=int, default=25400)
    parser.add_argument("--uac-rtp-min", type=int, default=26000)
    parser.add_argument("--uac-rtp-max", type=int, default=26200)
    parser.add_argument("--uas-rtp-min", type=int, default=27000)
    parser.add_argument("--uas-rtp-max", type=int, default=27200)
    parser.add_argument("--callee", default="callee")
    parser.add_argument("--calls", type=int, default=1)
    parser.add_argument("--rate", type=int, default=1)
    parser.add_argument("--hold-ms", type=int, default=1000)
    parser.add_argument("--output-root", default=str(ROOT / "artifacts" / "sipp"))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--sipp-bin", default="sipp")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.output_root) / (args.run_id or make_run_id())
    if run_dir.exists():
        raise SystemExit(f"Run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    for name in ("server", "sipp-a-uac", "sipp-b-uas"):
        (run_dir / name).mkdir()

    sipp_binary = resolve_binary(args.sipp_bin)
    if not sipp_binary and not args.dry_run:
        raise SystemExit("SIPp executable not found")
    sipp = sipp_binary or args.sipp_bin
    server_command = build_server_command(args, run_dir)
    uas_command = build_uas_command(args, sipp)
    uac_command = build_uac_command(args, sipp)

    (run_dir / "server-command.txt").write_text(shlex.join(server_command) + "\n", encoding="utf-8")
    (run_dir / "uas-command.txt").write_text(shlex.join(uas_command) + "\n", encoding="utf-8")
    (run_dir / "uac-command.txt").write_text(shlex.join(uac_command) + "\n", encoding="utf-8")

    results: List[SmokeResult] = []
    server_process: Optional[subprocess.Popen] = None
    uas_process: Optional[subprocess.Popen] = None
    try:
        if args.dry_run:
            results.append(SmokeResult("server", server_command, None, "dry-run", 0.0))
            results.append(SmokeResult("sipp-b-uas", uas_command, None, "dry-run", 0.0))
            results.append(SmokeResult("sipp-a-uac", uac_command, None, "dry-run", 0.0))
            returncode = 0
            return returncode

        server_process = start_process(server_command, ROOT, run_dir / "server" / "stdout.log")
        time.sleep(0.75)
        if server_process.poll() is not None:
            raise RuntimeError(f"Mini call server exited early. See {run_dir / 'server/stdout.log'}")

        uas_process = start_process(uas_command, run_dir / "sipp-b-uas", run_dir / "sipp-b-uas" / "stdout.log")
        time.sleep(0.75)

        started = time.monotonic()
        registration_rc = register_endpoint(args, run_dir)
        results.append(SmokeResult("registration", [], registration_rc, "passed" if registration_rc == 0 else "failed", time.monotonic() - started))

        started = time.monotonic()
        completed = subprocess.run(uac_command, cwd=run_dir / "sipp-a-uac", text=True, capture_output=True)
        (run_dir / "sipp-a-uac" / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (run_dir / "sipp-a-uac" / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        results.append(SmokeResult("sipp-a-uac", uac_command, completed.returncode, "passed" if completed.returncode == 0 else "failed", time.monotonic() - started))

        started = time.monotonic()
        uas_rc = uas_process.wait(timeout=max(30, int(args.hold_ms / 1000) + 30))
        results.append(SmokeResult("sipp-b-uas", uas_command, uas_rc, "passed" if uas_rc == 0 else "failed", time.monotonic() - started))
        uas_process = None
    finally:
        stop_process(uas_process)
        stop_process(server_process)
        write_summary(run_dir, args, results)

    print(f"B2BUA SIPp artifacts: {run_dir}")
    for result in results:
        print(f"{result.name}: {result.status}")
    failed = [result for result in results if result.status == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
