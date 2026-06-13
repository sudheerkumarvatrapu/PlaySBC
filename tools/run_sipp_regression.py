#!/usr/bin/env python3
"""Run SIPp regression scenarios against PlaySBC."""

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
from typing import Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "sipp" / "scenarios"
DEFAULT_SCENARIOS = (
    "smoke_register_digest",
    "smoke_transaction_cache",
    "smoke_invalid_bye",
    "smoke_basic_call_media",
    "smoke_bridge_two_leg",
)
PCAP_DIR = SCENARIO_DIR / "pcap"
SCENARIO_PLANS = {
    "options": {
        "mode": "single",
        "steps": [{"name": "options", "xml": "options.xml", "service": "echo"}],
    },
    "register_digest": {
        "mode": "single",
        "steps": [{"name": "register_digest", "xml": "register_digest.xml", "service": "1001", "local_port": 25062}],
    },
    "call_echo": {
        "mode": "single",
        "steps": [{"name": "call_echo", "xml": "call_echo.xml", "service": "echo", "local_port": 25061, "rtp_min": 26000, "rtp_max": 26020}],
    },
    "invalid_bye": {
        "mode": "single",
        "steps": [{"name": "invalid_bye", "xml": "invalid_bye.xml", "service": "echo", "local_port": 25063}],
    },
    "smoke_register_digest": {
        "mode": "single",
        "steps": [{"name": "register", "xml": "smoke_register_digest.xml", "service": "1001", "local_port": 25062}],
    },
    "smoke_transaction_cache": {
        "mode": "single",
        "steps": [
            {
                "name": "options-replay",
                "xml": "smoke_transaction_cache.xml",
                "service": "echo",
                "local_port": 25063,
                "extra_args": ("-nr",),
            }
        ],
    },
    "smoke_invalid_bye": {
        "mode": "single",
        "steps": [{"name": "invalid-bye", "xml": "smoke_invalid_bye.xml", "service": "echo", "local_port": 25064}],
    },
    "smoke_basic_call_media": {
        "mode": "single",
        "steps": [
            {
                "name": "basic-call-media",
                "xml": "smoke_basic_call_media.xml",
                "service": "echo",
                "local_port": 25061,
                "rtp_min": 26000,
                "rtp_max": 26020,
            }
        ],
        "sidecars": [
            {
                "name": "media-pcap",
                "type": "rtp-pcap",
                "pcap": str(PCAP_DIR / "g711u_60s.pcap"),
                "source_port": 0,
                "rtp_offset": 0,
                "duration_ms": 500,
                "delay_seconds": 0.5,
                "expect_echo": True,
            }
        ],
    },
    "smoke_bridge_two_leg": {
        "mode": "parallel",
        "steps": [
            {
                "name": "bridge-a",
                "xml": "smoke_bridge_leg.xml",
                "service": "bridge",
                "local_port": 25064,
                "rtp_min": 26010,
                "rtp_max": 26020,
                "keys": {"bridge_leg": "bridge-a"},
            },
            {
                "name": "bridge-b",
                "xml": "smoke_bridge_leg.xml",
                "service": "bridge",
                "local_port": 25065,
                "rtp_min": 26030,
                "rtp_max": 26040,
                "keys": {"bridge_leg": "bridge-b"},
            },
        ],
    },
}
SCENARIO_SERVICES = {name: plan["steps"][0]["service"] for name, plan in SCENARIO_PLANS.items() if plan["mode"] == "single"}


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


def build_sipp_step_command(
    sipp_binary: str,
    step: Dict[str, object],
    host: str,
    port: int,
    calls: int,
    rate: int,
    scenario_dir: Optional[Path] = None,
) -> List[str]:
    scenario_file = SCENARIO_DIR / str(step["xml"])
    if not scenario_file.exists():
        raise ValueError(f"Unknown SIPp scenario XML: {scenario_file}")
    keys = dict(step.get("keys", {}))
    resolved_keys = set(step.get("resolve_keys", ()))
    if scenario_dir and resolved_keys:
        scenario_file = resolve_scenario_xml(scenario_file, scenario_dir, keys, resolved_keys)

    command = [
        sipp_binary,
        f"{host}:{port}",
        "-sf",
        str(scenario_file),
        "-s",
        str(step["service"]),
        "-m",
        str(calls),
        "-r",
        str(rate),
    ]
    local_port = step.get("local_port")
    if local_port:
        command.extend(["-i", host, "-mi", host, "-p", str(local_port)])

    rtp_min = step.get("rtp_min")
    rtp_max = step.get("rtp_max")
    if rtp_min and rtp_max:
        command.extend(["-min_rtp_port", str(rtp_min), "-max_rtp_port", str(rtp_max)])

    for key, value in keys.items():
        if key in resolved_keys and scenario_dir:
            continue
        command.extend(["-key", str(key), str(value)])

    command.extend(str(arg) for arg in step.get("extra_args", ()))
    command.extend(["-trace_err", "-trace_msg", "-trace_stat", "-trace_counts", "-trace_logs"])
    return command


def resolve_scenario_xml(source: Path, scenario_dir: Path, keys: Dict[str, object], resolved_keys: set[str]) -> Path:
    text = source.read_text(encoding="ISO-8859-1")
    for key in resolved_keys:
        if key not in keys:
            raise ValueError(f"Cannot resolve missing SIPp key [{key}] for {source.name}")
        text = text.replace(f"[{key}]", str(keys[key]))

    resolved = scenario_dir / f"{source.stem}_resolved.xml"
    resolved.write_text(text, encoding="ISO-8859-1")
    return resolved


def build_sipp_commands(
    sipp_binary: str,
    scenario: str,
    host: str,
    port: int,
    calls: int,
    rate: int,
    scenario_dir: Optional[Path] = None,
) -> List[Tuple[str, List[str]]]:
    plan = SCENARIO_PLANS.get(scenario)
    if not plan:
        raise ValueError(f"Unknown SIPp scenario: {scenario}")

    return [
        (str(step["name"]), build_sipp_step_command(sipp_binary, step, host, port, calls, rate, scenario_dir))
        for step in plan["steps"]
    ]


def build_sipp_command(
    sipp_binary: str,
    scenario: str,
    host: str,
    port: int,
    calls: int,
    rate: int,
) -> List[str]:
    commands = build_sipp_commands(sipp_binary, scenario, host, port, calls, rate)
    if len(commands) != 1:
        raise ValueError(f"Scenario {scenario} has multiple SIPp steps")
    return commands[0][1]


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


def write_step_commands(scenario_dir: Path, commands: List[Tuple[str, List[str]]]) -> None:
    if len(commands) == 1:
        (scenario_dir / "command.txt").write_text(shlex.join(commands[0][1]) + "\n", encoding="utf-8")
        return

    for step_name, command in commands:
        (scenario_dir / f"{step_name}-command.txt").write_text(shlex.join(command) + "\n", encoding="utf-8")


def build_sidecar_commands(scenario: str, args: argparse.Namespace) -> List[Tuple[str, List[str], float]]:
    plan = SCENARIO_PLANS.get(scenario)
    if not plan:
        raise ValueError(f"Unknown SIPp scenario: {scenario}")

    commands: List[Tuple[str, List[str], float]] = []
    for sidecar in plan.get("sidecars", ()):
        sidecar_type = sidecar.get("type")
        if sidecar_type != "rtp-pcap":
            raise ValueError(f"Unsupported sidecar type for {scenario}: {sidecar_type}")

        rtp_port = args.rtp_min + int(sidecar.get("rtp_offset", 0))
        command = [
            sys.executable,
            str(ROOT / "tools" / "play_g711_pcap_rtp.py"),
            "--pcap",
            str(sidecar["pcap"]),
            "--host",
            args.host,
            "--port",
            str(rtp_port),
            "--duration-ms",
            str(sidecar.get("duration_ms", 0)),
            "--source-port",
            str(sidecar.get("source_port", 0)),
        ]
        if sidecar.get("expect_echo"):
            command.append("--expect-echo")
        commands.append((str(sidecar["name"]), command, float(sidecar.get("delay_seconds", 0))))
    return commands


def write_sidecar_commands(scenario_dir: Path, sidecar_commands: List[Tuple[str, List[str], float]]) -> None:
    for sidecar_name, command, delay_seconds in sidecar_commands:
        command_text = f"# delay_seconds={delay_seconds}\n{shlex.join(command)}\n"
        (scenario_dir / f"{sidecar_name}-command.txt").write_text(command_text, encoding="utf-8")


def run_single_step(
    scenario_dir: Path,
    command: List[str],
    sidecar_commands: Optional[List[Tuple[str, List[str], float]]] = None,
) -> Tuple[int, float]:
    started = time.monotonic()
    sidecar_commands = sidecar_commands or []
    sidecar_processes = []
    open_files = []
    try:
        stdout = (scenario_dir / "stdout.log").open("w", encoding="utf-8")
        stderr = (scenario_dir / "stderr.log").open("w", encoding="utf-8")
        open_files.extend([stdout, stderr])
        process = subprocess.Popen(command, cwd=scenario_dir, stdout=stdout, stderr=stderr, text=True)

        for sidecar_name, sidecar_command, delay_seconds in sidecar_commands:
            remaining_delay = delay_seconds - (time.monotonic() - started)
            if remaining_delay > 0:
                time.sleep(remaining_delay)
            sidecar_stdout = (scenario_dir / f"{sidecar_name}-stdout.log").open("w", encoding="utf-8")
            sidecar_stderr = (scenario_dir / f"{sidecar_name}-stderr.log").open("w", encoding="utf-8")
            open_files.extend([sidecar_stdout, sidecar_stderr])
            sidecar_processes.append(
                (
                    sidecar_name,
                    subprocess.Popen(
                        sidecar_command,
                        cwd=scenario_dir,
                        stdout=sidecar_stdout,
                        stderr=sidecar_stderr,
                        text=True,
                    ),
                )
            )

        returncode = process.wait()
        sidecar_returncodes = [sidecar.wait() for _name, sidecar in sidecar_processes]
    finally:
        for _sidecar_name, sidecar in sidecar_processes:
            if sidecar.poll() is None:
                sidecar.terminate()
                try:
                    sidecar.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    sidecar.kill()
                    sidecar.wait(timeout=3)
        if "process" in locals() and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for handle in open_files:
            handle.close()

    duration = time.monotonic() - started
    status = 0 if returncode == 0 and all(code == 0 for code in sidecar_returncodes) else 1
    return status, duration


def run_parallel_steps(scenario_dir: Path, commands: List[Tuple[str, List[str]]]) -> Tuple[int, float]:
    started = time.monotonic()
    processes = []
    open_files = []
    try:
        for step_name, command in commands:
            step_dir = scenario_dir / step_name
            step_dir.mkdir()
            stdout = (step_dir / "stdout.log").open("w", encoding="utf-8")
            stderr = (step_dir / "stderr.log").open("w", encoding="utf-8")
            open_files.extend([stdout, stderr])
            processes.append((step_name, subprocess.Popen(command, cwd=step_dir, stdout=stdout, stderr=stderr, text=True)))
            time.sleep(0.1)

        returncodes = []
        for _step_name, process in processes:
            returncodes.append(process.wait())
    finally:
        for _step_name, process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        for handle in open_files:
            handle.close()

    duration = time.monotonic() - started
    return (0 if all(returncode == 0 for returncode in returncodes) else 1), duration


def run_scenario_steps(
    scenario: str,
    scenario_dir: Path,
    commands: List[Tuple[str, List[str]]],
    sidecar_commands: Optional[List[Tuple[str, List[str], float]]] = None,
) -> Tuple[int, float]:
    if len(commands) == 1:
        return run_single_step(scenario_dir, commands[0][1], sidecar_commands)
    if SCENARIO_PLANS[scenario]["mode"] == "parallel":
        return run_parallel_steps(scenario_dir, commands)
    raise ValueError(f"Unsupported scenario execution mode for {scenario}")


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
    parser.add_argument("--scenario", action="append", choices=tuple(SCENARIO_PLANS), help="Scenario to run; repeat as needed")
    parser.add_argument("--output-root", default="", help="Optional parent directory for persistent regression output")
    parser.add_argument("--run-id", default="", help="Run directory name; defaults to a timestamp")
    parser.add_argument("--sipp-bin", default="sipp", help="SIPp executable name or path")
    parser.add_argument("--start-server", action="store_true", help="Start and stop PlaySBC around the run")
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
            commands = build_sipp_commands(
                sipp_binary or args.sipp_bin,
                scenario,
                args.host,
                args.port,
                args.calls,
                args.rate,
                scenario_dir,
            )
            sidecar_commands = build_sidecar_commands(scenario, args)
            write_step_commands(scenario_dir, commands)
            write_sidecar_commands(scenario_dir, sidecar_commands)
            summary_command = commands[0][1]
            if args.dry_run:
                results.append(ScenarioResult(scenario, summary_command, None, 0.0, "dry-run"))
                continue

            returncode, duration = run_scenario_steps(scenario, scenario_dir, commands, sidecar_commands)
            status = "passed" if returncode == 0 else "failed"
            results.append(ScenarioResult(scenario, summary_command, returncode, duration, status))
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
