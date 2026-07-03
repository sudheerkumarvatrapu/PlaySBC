#!/usr/bin/env python3
"""Run one real-address, dual-realm PlaySBC/RTPengine SIPp call."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from tools.run_b2bua_sipp_smoke import extract_helm_server_yaml
except ModuleNotFoundError:
    from run_b2bua_sipp_smoke import extract_helm_server_yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.topology.yml"
HELM_VALUES = ROOT / "configs" / "topology" / "helm-values.yaml"
CHART = ROOT / "charts" / "playsbc"
TOPOLOGY_IPS = ("172.28.0.10", "172.28.0.20", "172.28.0.40", "192.168.28.20", "192.168.28.30", "192.168.28.40")


@dataclass(frozen=True)
class PcapRecord:
    timestamp: float
    data: bytes
    original_length: int


def command_text(command: Iterable[str]) -> str:
    return " ".join(command)


def run(command: list[str], *, env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"+ {command_text(command)}", flush=True)
    completed = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    if completed.stdout.strip():
        print(completed.stdout.rstrip())
    if completed.stderr.strip():
        print(completed.stderr.rstrip(), file=sys.stderr)
    if check and completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {command_text(command)}")
    return completed


def compose_command(*args: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), *args]


def render_helm_config(bundle: Path, env: dict[str, str]) -> Path:
    rendered = run(
        [
            "helm",
            "template",
            "playsbc-topology",
            str(CHART),
            "-f",
            str(HELM_VALUES),
            "--show-only",
            "templates/configmap.yaml",
        ],
        env=env,
    )
    config_path = bundle / "server-config.yaml"
    config_path.write_text(extract_helm_server_yaml(rendered.stdout), encoding="utf-8")
    return config_path


def pcap_records(path: Path) -> tuple[int, int, int, list[PcapRecord]]:
    data = path.read_bytes()
    if len(data) < 24:
        raise ValueError(f"PCAP is too short: {path}")
    magic = data[:4]
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
        b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
        b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
        b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
    }
    if magic not in formats:
        raise ValueError(f"Unsupported PCAP magic in {path}")
    endian, fraction_scale = formats[magic]
    _magic, major, minor, _zone, _sigfigs, _snaplen, linktype = struct.unpack(f"{endian}IHHIIII", data[:24])
    records: list[PcapRecord] = []
    offset = 24
    while offset + 16 <= len(data):
        seconds, fraction, captured, original = struct.unpack(f"{endian}IIII", data[offset : offset + 16])
        offset += 16
        packet = data[offset : offset + captured]
        if len(packet) != captured:
            raise ValueError(f"Truncated PCAP record in {path}")
        offset += captured
        records.append(PcapRecord(seconds + (fraction / fraction_scale), packet, original))
    return major, minor, linktype, records


def merge_pcaps(inputs: list[Path], output: Path) -> int:
    headers = []
    records: list[PcapRecord] = []
    for path in inputs:
        if not path.exists() or path.stat().st_size <= 24:
            continue
        major, minor, linktype, source_records = pcap_records(path)
        headers.append((major, minor, linktype))
        records.extend(source_records)
    if not headers:
        raise ValueError("No packet capture data was produced")
    if len(set(headers)) != 1:
        raise ValueError(f"PCAP link types differ: {headers}")
    major, minor, linktype = headers[0]
    with output.open("wb") as handle:
        handle.write(struct.pack("<IHHIIII", 0xA1B2C3D4, major, minor, 0, 0, 65535, linktype))
        for record in sorted(records, key=lambda item: item.timestamp):
            seconds = int(record.timestamp)
            microseconds = int((record.timestamp - seconds) * 1_000_000)
            handle.write(struct.pack("<IIII", seconds, microseconds, len(record.data), record.original_length))
            handle.write(record.data)
    return len(records)


def rtp_payload_types(path: Path) -> dict[tuple[str, str], set[int]]:
    _major, _minor, linktype, records = pcap_records(path)
    ip_offset_by_linktype = {1: 14, 276: 20}
    if linktype not in ip_offset_by_linktype:
        raise ValueError(f"Unsupported PCAP link type for RTP validation: {linktype}")
    ip_offset = ip_offset_by_linktype[linktype]
    flows: dict[tuple[str, str], set[int]] = {}
    for record in records:
        frame = record.data
        if len(frame) < ip_offset + 28 or frame[ip_offset] >> 4 != 4:
            continue
        header_length = (frame[ip_offset] & 0x0F) * 4
        if frame[ip_offset + 9] != 17:
            continue
        udp_offset = ip_offset + header_length
        rtp_offset = udp_offset + 8
        if len(frame) < rtp_offset + 12 or frame[rtp_offset] >> 6 != 2:
            continue
        src_ip = socket.inet_ntoa(frame[ip_offset + 12 : ip_offset + 16])
        dst_ip = socket.inet_ntoa(frame[ip_offset + 16 : ip_offset + 20])
        flows.setdefault((src_ip, dst_ip), set()).add(frame[rtp_offset + 1] & 0x7F)
    return flows


def wait_for_rtpengine(env: dict[str, str], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    command = compose_command(
        "exec",
        "-T",
        "playsbc",
        "python3",
        "/app/tools/check_rtpengine.py",
        "--url",
        "udp://172.28.0.40:2223",
        "--timeout",
        "1",
    )
    while time.monotonic() < deadline:
        completed = run(command, env=env, check=False)
        if completed.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("RTPengine did not become ready inside the core network")


def wait_service_exit(service: str, env: dict[str, str]) -> int:
    completed = run(compose_command("wait", service), env=env, check=False)
    for line in reversed(completed.stdout.splitlines()):
        if line.strip().isdigit():
            return int(line.strip())
    return completed.returncode


def validate(bundle: Path, uac_rc: int, uas_rc: int, packet_count: int) -> list[str]:
    failures = []
    if uac_rc != 0:
        failures.append(f"SIPp A exited with {uac_rc}")
    if uas_rc != 0:
        failures.append(f"SIPp B exited with {uas_rc}")
    media_log = (bundle / "log.media").read_text(encoding="utf-8", errors="replace")
    transcode_log = (bundle / "log.transcoding").read_text(encoding="utf-8", errors="replace")
    if "RTPENGINE OFFER" not in media_log or "RTPENGINE ANSWER" not in media_log:
        failures.append("PlaySBC did not log a complete RTPengine offer/answer exchange")
    if "RTPENGINE TRANSCODING POLICY" not in transcode_log:
        failures.append("PlaySBC did not activate the PCMU-to-PCMA transcoding policy")
    capture = (bundle / "capture.pcap").read_bytes()
    for ip in TOPOLOGY_IPS:
        if socket.inet_aton(ip) not in capture and ip.encode("ascii") not in capture:
            failures.append(f"Capture does not contain topology address {ip}")
    if packet_count <= 0:
        failures.append("Unified capture contains no packets")
    payloads = rtp_payload_types(bundle / "capture.pcap")
    expected_rtp = {
        ("172.28.0.10", "172.28.0.40"): 0,
        ("192.168.28.40", "192.168.28.30"): 8,
        ("192.168.28.30", "192.168.28.40"): 8,
        ("172.28.0.40", "172.28.0.10"): 0,
    }
    for flow, payload_type in expected_rtp.items():
        if payload_type not in payloads.get(flow, set()):
            failures.append(f"Missing RTP payload {payload_type} on {flow[0]} -> {flow[1]}")
    if ("172.28.0.10", "192.168.28.30") in payloads or ("192.168.28.30", "172.28.0.10") in payloads:
        failures.append("Capture contains direct SIPp A/SIPp B RTP, bypassing RTPengine")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hold-ms", type=int, default=60000, help="Call hold time; the bundled RTP PCAP is 60 seconds")
    parser.add_argument("--skip-build", action="store_true", help="Reuse existing local Docker images")
    args = parser.parse_args()

    for binary in ("docker", "helm"):
        if not shutil.which(binary):
            parser.error(f"{binary} is required")
    if args.hold_ms < 1000:
        parser.error("--hold-ms must be at least 1000")

    run_id = time.strftime("real-topology-%Y%m%d-%H%M%S")
    bundle = ROOT / "logs" / "real-topology" / run_id
    (bundle / "sipp-a").mkdir(parents=True)
    (bundle / "sipp-b").mkdir(parents=True)
    env = os.environ.copy()
    env["PLAYSBC_TOPOLOGY_OUTPUT"] = str(bundle.resolve())
    env["SIPP_HOLD_MS"] = str(args.hold_ms)
    config_path = render_helm_config(bundle, env)
    env["PLAYSBC_TOPOLOGY_CONFIG"] = str(config_path.resolve())

    run(compose_command("config", "--quiet"), env=env)
    run(compose_command("down", "--remove-orphans"), env=env, check=False)
    uac_rc = 1
    uas_rc = 1
    packet_count = 0
    try:
        if not args.skip_build:
            run(compose_command("build"), env=env)
        run(
            compose_command(
                "up",
                "-d",
                "rtpengine",
                "playsbc",
                "capture-signalling",
                "capture-media",
                "sipp-b",
            ),
            env=env,
        )
        wait_for_rtpengine(env)
        time.sleep(1)
        uac = run(compose_command("run", "--rm", "sipp-a"), env=env, check=False)
        uac_rc = uac.returncode
        uas_rc = wait_service_exit("sipp-b", env)
        time.sleep(2)
    finally:
        run(compose_command("kill", "-s", "SIGINT", "capture-signalling", "capture-media"), env=env, check=False)
        time.sleep(1)
        topology_logs = run(compose_command("logs", "--no-color", "rtpengine", "playsbc"), env=env, check=False)
        (bundle / "topology.log").write_text(topology_logs.stdout + topology_logs.stderr, encoding="utf-8")
        run(compose_command("down", "--remove-orphans"), env=env, check=False)

    partials = [bundle / "signalling.pcap", bundle / "media.pcap"]
    packet_count = merge_pcaps(partials, bundle / "capture.pcap")
    for partial in partials:
        partial.unlink(missing_ok=True)
    failures = validate(bundle, uac_rc, uas_rc, packet_count)
    result = "PASSED" if not failures else "FAILED"
    summary = [
        f"result={result}",
        "topology=core:172.28.0.0/24 peer:192.168.28.0/24",
        f"sipp_a_returncode={uac_rc}",
        f"sipp_b_returncode={uas_rc}",
        f"capture_packets={packet_count}",
        *[f"failure={failure}" for failure in failures],
    ]
    (bundle / "result.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(f"Real topology result: {result}")
    print(f"Evidence bundle: {bundle}")
    for failure in failures:
        print(f"- {failure}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
