#!/usr/bin/env python3
"""Run SIPp regression scenarios against the mini call server."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "sipp" / "scenarios"
DEFAULT_SCENARIOS = ("options", "register_digest", "call_echo", "invalid_bye")
SCENARIO_SERVICES = {
    "options": "echo",
    "register_digest": "1001",
    "call_echo": "echo",
    "invalid_bye": "echo",
}


@dataclass
class ScenarioResult:
    scenario: str
    command: List[str]
    returncode: Optional[int]
    duration_seconds: float
    status: str


def make_run_id(prefix: str = "sipp") -> str:
    return time.strftime(f"{prefix}-%Y%m%d-%H%M%S", time.localtime())


def resolve_sipp_binary(candidate: str) -> Optional[str]:
    if os.sep in candidate:
        path = Path(candidate)
        return str(path) if path.exists() else None
    return shutil.which(candidate)


def build_sipp_command(
    sipp_binary: str,
    scenario: str,
    host: str,
    port: int,
    calls: int,
    rate: int,
) -> List[str]:
    scenario_file = SCENARIO_DIR / f"{scenario}.xml"
    if scenario not in SCENARIO_SERVICES or not scenario_file.exists():
        raise ValueError(f"Unknown SIPp scenario: {scenario}")

    return [
        sipp_binary,
        f"{host}:{port}",
        "-sf",
        str(scenario_file),
        "-s",
        SCENARIO_SERVICES[scenario],
        "-m",
        str(calls),
        "-r",
        str(rate),
        "-trace_err",
        "-trace_msg",
        "-trace_stat",
        "-trace_counts",
        "-trace_logs",
    ]


def start_server(args: argparse.Namespace, run_dir: Path) -> subprocess.Popen:
    server_dir = run_dir / "server"
    server_dir.mkdir(exist_ok=True)
    server_log = (server_dir / "stdout.log").open("w", encoding="utf-8")
    command = [
        sys.executable,
        str(ROOT / "mini_call_server.py"),
        "--config",
        str(ROOT / "config.example.json"),
        "--ip",
        args.host,
        "--sip-port",
        str(args.port),
        "--rtp-min",
        str(args.rtp_min),
        "--rtp-max",
        str(args.rtp_max),
    ]
    if args.debug_server:
        command.append("--debug")
    (run_dir / "server-command.txt").write_text(shlex.join(command) + "\n", encoding="utf-8")
    process = subprocess.Popen(command, cwd=ROOT, stdout=server_log, stderr=subprocess.STDOUT)
    process.server_log = server_log  # type: ignore[attr-defined]
    time.sleep(0.5)
    if process.poll() is not None:
        raise RuntimeError(f"Mini call server exited early. See {server_dir / 'stdout.log'}")
    return process


def stop_server(process: Optional[subprocess.Popen]) -> None:
    if not process:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    server_log = getattr(process, "server_log", None)
    if server_log:
        server_log.close()


def write_summary(run_dir: Path, args: argparse.Namespace, results: List[ScenarioResult]) -> None:
    payload: Dict[str, object] = {
        "run_dir": str(run_dir),
        "target": f"{args.host}:{args.port}",
        "calls": args.calls,
        "rate": args.rate,
        "dry_run": args.dry_run,
        "results": [asdict(result) for result in results],
    }
    (run_dir / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SIPp regression scenarios with fresh logs")
    parser.add_argument("--host", default="127.0.0.1", help="SIP server host")
    parser.add_argument("--port", type=int, default=15062, help="SIP server UDP port")
    parser.add_argument("--rtp-min", type=int, default=12000, help="Server RTP range start when --start-server is used")
    parser.add_argument("--rtp-max", type=int, default=12100, help="Server RTP range end when --start-server is used")
    parser.add_argument("--calls", type=int, default=1, help="Calls per scenario")
    parser.add_argument("--rate", type=int, default=1, help="New calls per second")
    parser.add_argument("--scenario", action="append", choices=DEFAULT_SCENARIOS, help="Scenario to run; repeat as needed")
    parser.add_argument("--output-root", default="", help="Optional parent directory for persistent regression output")
    parser.add_argument("--run-id", default="", help="Run directory name; defaults to a timestamp")
    parser.add_argument("--sipp-bin", default="sipp", help="SIPp executable name or path")
    parser.add_argument("--start-server", action="store_true", help="Start and stop the mini call server around the run")
    parser.add_argument("--debug-server", action="store_true", help="Enable debug logging for the managed server")
    parser.add_argument("--dry-run", action="store_true", help="Write commands and validate scenarios without running SIPp")
    args = parser.parse_args()

    run_id = args.run_id or make_run_id()
    if args.output_root:
        run_dir = Path(args.output_root) / run_id
        if run_dir.exists():
            raise SystemExit(f"Run directory already exists: {run_dir}")
        run_dir.mkdir(parents=True)
    else:
        run_dir = Path(tempfile.mkdtemp(prefix=f"{run_id}-"))

    sipp_binary = resolve_sipp_binary(args.sipp_bin)
    if not sipp_binary and not args.dry_run:
        raise SystemExit(
            "SIPp executable not found. Install it first, for example with `brew install sipp`, "
            "or run with --dry-run to validate the harness."
        )

    scenarios = args.scenario or list(DEFAULT_SCENARIOS)
    results: List[ScenarioResult] = []
    server_process: Optional[subprocess.Popen] = None
    try:
        if args.start_server and not args.dry_run:
            server_process = start_server(args, run_dir)

        for scenario in scenarios:
            scenario_dir = run_dir / scenario
            scenario_dir.mkdir()
            command = build_sipp_command(sipp_binary or args.sipp_bin, scenario, args.host, args.port, args.calls, args.rate)
            (scenario_dir / "command.txt").write_text(shlex.join(command) + "\n", encoding="utf-8")
            if args.dry_run:
                results.append(ScenarioResult(scenario, command, None, 0.0, "dry-run"))
                continue

            started = time.monotonic()
            completed = subprocess.run(command, cwd=scenario_dir, text=True, capture_output=True)
            duration = time.monotonic() - started
            (scenario_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
            (scenario_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
            status = "passed" if completed.returncode == 0 else "failed"
            results.append(ScenarioResult(scenario, command, completed.returncode, duration, status))
    finally:
        stop_server(server_process)
        write_summary(run_dir, args, results)

    failed = [result for result in results if result.status == "failed"]
    print(f"SIPp regression output: {run_dir}")
    for result in results:
        print(f"{result.scenario}: {result.status}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
