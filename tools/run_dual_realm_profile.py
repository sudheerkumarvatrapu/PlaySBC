#!/usr/bin/env python3
"""Run one PlaySBC SIPp profile on the real core/peer Docker topology."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_b2bua_sipp_smoke import (  # noqa: E402
    BASE_DEFAULTS,
    B2BUA_PROFILES,
    MEDIA_PCAPS,
    SCENARIO_DIR,
    SmokeResult,
    append_commands,
    append_dtmf_observation,
    append_media_observation,
    append_registration_auth_observation,
    append_registration_ladders,
    append_results,
    append_rtcp_observation,
    append_transcoding_observation,
    call_limit,
    collect_work_logs,
    dialog_remote_target_errors,
    dump_simple_yaml,
    effective_b2bua_routes,
    effective_route_policies,
    extract_helm_server_yaml,
    initialize_log_dir,
    is_load_like_run,
    prepare_media_scenarios,
    prepare_registration_scenario,
    prepare_transport_scenario,
    render_harness_config_templates,
    rtpengine_anchor_ports,
    rtpengine_load_media_complete,
    rtpengine_query_stats,
    sipp_timeout_seconds,
    sipp_trace_args,
    should_run_rtcp,
    uas_media_codec,
    wait_for_rtpengine_load_queries,
)
from tools.run_real_topology import (  # noqa: E402
    CHART,
    COMPOSE_FILE,
    TOPOLOGY_IMAGES,
    merge_pcaps,
    pcap_records,
    rtp_payload_types,
    topology_images_available,
)


CORE_UA_IP = "172.28.0.10"
CORE_SBC_IP = "172.28.0.20"
CORE_RTPENGINE_IP = "172.28.0.40"
PEER_SBC_IP = "192.168.28.20"
PEER_UA_IP = "192.168.28.30"
PEER_RTPENGINE_IP = "192.168.28.40"
CORE_SUBNET = "172.28.0.0/24"
PEER_SUBNET = "192.168.28.0/24"
SIP_PORT = 5060
TLS_PORT = 5061
REGISTER_PORT = 5070
UA_RTP_MIN = 6000
UA_RTP_MAX = 7998
SERVER_RTP_MIN = 30000
SERVER_RTP_MAX = 32000
REAL_TOPOLOGY_PROFILE = "real-topology-rtpengine-transcoding"
ROBOT_PHASE_PREFIX = "ROBOT_PHASE_JSON="
ROBOT_PHASES = (
    "Setup Preparation",
    "Configuration",
    "Test Setup",
    "Test Execution",
    "Test Teardown",
    "Evidence Validation",
)


def command_text(command: Iterable[str]) -> str:
    return " ".join(str(part) for part in command)


def compose_command(*parts: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), *parts]


def run(
    command: list[str],
    *,
    env: dict[str, str],
    check: bool = True,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Command failed ({completed.returncode}): {command_text(command)}\n{detail}")
    return completed


def append_log(log_dir: Path, filename: str, title: str, body: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with (log_dir / filename).open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} | {title}\n")
        if body:
            handle.write(body.rstrip() + "\n")


def append_robot_phase(
    bundle: Path,
    records: list[dict[str, object]],
    name: str,
    status: str,
    started: float,
    detail: str,
) -> None:
    record: dict[str, object] = {
        "name": name,
        "status": status,
        "duration_seconds": round(max(0.0, time.monotonic() - started), 6),
        "detail": detail,
    }
    records.append(record)


def flush_robot_phases(bundle: Path, records: list[dict[str, object]]) -> None:
    for record in records:
        append_log(
            bundle,
            "log.platform",
            "ROBOT EXECUTION PHASE",
            ROBOT_PHASE_PREFIX + json.dumps(record, sort_keys=True),
        )


def append_skipped_robot_phases(bundle: Path, records: list[dict[str, object]]) -> None:
    recorded = {str(record.get("name")) for record in records}
    for name in ROBOT_PHASES:
        if name in recorded:
            continue
        append_robot_phase(
            bundle,
            records,
            name,
            "skipped",
            time.monotonic(),
            "Not executed because an earlier lifecycle phase failed.",
        )


def execution_detail(args: SimpleNamespace) -> str:
    codec_path = f"{args.media_codec or 'signalling-only'} -> {uas_media_codec(args) if args.media_enabled else 'signalling-only'}"
    return (
        f"Run SIPp A -> PlaySBC -> SIPp B; profile={args.profile}; calls={args.calls}; "
        f"rate={args.rate} cps; hold={args.hold_ms / 1000:.3f} s; "
        f"transport=A/{args.uac_transport.upper()}-B/{args.uas_transport.upper()}; "
        f"media_backend={args.media_backend}; codec_path={codec_path}; "
        f"register_caller={args.register_caller}; register_callee={args.register_callee}."
    )


def profile_args(profile: str, run_id: str, log_folder: str) -> SimpleNamespace:
    values = dict(BASE_DEFAULTS)
    if profile == REAL_TOPOLOGY_PROFILE:
        values.update(B2BUA_PROFILES["rtpengine-transcoding"])
        values.update({"caller": "core-a", "callee": "peer-b"})
    else:
        values.update(B2BUA_PROFILES[profile])
    values.update(
        {
            "profile": profile,
            "resolved_run_id": run_id,
            "log_folder": log_folder,
            "host": PEER_UA_IP,
            "server_port": SIP_PORT,
            "uac_port": SIP_PORT,
            "uas_port": SIP_PORT,
            "register_port": REGISTER_PORT,
            "caller_register_port": REGISTER_PORT,
            "server_rtp_min": SERVER_RTP_MIN,
            "server_rtp_max": SERVER_RTP_MAX,
            "uac_rtp_min": UA_RTP_MIN,
            "uac_rtp_max": UA_RTP_MAX,
            "uas_rtp_min": UA_RTP_MIN,
            "uas_rtp_max": UA_RTP_MAX,
            "media_driver": "sipp-pcap",
            "sipp_pcap_sudo": False,
            "pcap_topology": "dual-realm",
            "pcap_uac_ip": CORE_UA_IP,
            "pcap_server_ip": CORE_SBC_IP,
            "pcap_uas_ip": PEER_UA_IP,
            "pcap_rtpengine_ip": CORE_RTPENGINE_IP,
            "dry_run": False,
        }
    )
    if values.get("rtpengine_url") == BASE_DEFAULTS["rtpengine_url"]:
        values["rtpengine_url"] = f"udp://{CORE_RTPENGINE_IP}:2223"
    args = SimpleNamespace(**values)
    transports = [value.strip() for value in str(args.sip_transport).split(",") if value.strip()]
    args.uac_transport = args.uac_transport or (transports[0] if len(transports) == 1 else "udp")
    args.uas_transport = args.uas_transport or (transports[0] if len(transports) == 1 else "udp")
    args.media_enabled = bool(args.media_codec)
    args.media_pcap = args.media_pcap or (MEDIA_PCAPS[args.media_codec] if args.media_codec else "")
    args.server_codec = args.server_codec or args.media_codec or "PCMU"
    args.ladder_enabled = args.ladder if args.ladder is not None else (args.calls == 1 and args.rate == 1)
    return args


def container_path(path_value: object, bundle: Path) -> str:
    path = Path(path_value).resolve()
    try:
        return f"/scenarios/{path.relative_to(SCENARIO_DIR.resolve()).as_posix()}"
    except ValueError:
        pass
    try:
        return f"/output/{path.relative_to(bundle.resolve()).as_posix()}"
    except ValueError as exc:
        raise ValueError(f"Scenario is outside mounted topology paths: {path}") from exc


def prepare_scenarios(args: SimpleNamespace, bundle: Path, work_dir: Path) -> tuple[str, str, str]:
    prepare_registration_scenario(args, work_dir)
    prepare_media_scenarios(args, work_dir)
    prepare_transport_scenario(args, work_dir)
    for scenario in work_dir.rglob("*.xml"):
        text = scenario.read_text(encoding="ISO-8859-1")
        text = text.replace(str(SCENARIO_DIR.resolve()), "/scenarios")
        scenario.write_text(text, encoding="ISO-8859-1")
    registration = container_path(
        getattr(args, "registration_scenario", "register_contact.xml")
        if Path(str(getattr(args, "registration_scenario", ""))).is_absolute()
        else SCENARIO_DIR / str(getattr(args, "registration_scenario", "register_contact.xml")),
        bundle,
    )
    return container_path(args.uac_scenario, bundle), container_path(args.uas_scenario, bundle), registration


def render_helm_config(args: SimpleNamespace, work_dir: Path, env: dict[str, str]) -> Path:
    config = {
        "sip_ip": "0.0.0.0",
        "sip_advertised_ip": CORE_SBC_IP,
        "b2bua_advertised_ip": PEER_SBC_IP,
        "sip_port": SIP_PORT,
        "tls_port": TLS_PORT,
        "sip_transport": args.sip_transport,
        "rtp_min": SERVER_RTP_MIN,
        "rtp_max": SERVER_RTP_MAX,
        "log_dir": "/var/log/playsbc",
        "default_codec": args.server_codec,
        "auth_realm": "playsbc",
        "users": args.users,
        "bridge_rooms": ["bridge"],
        "b2bua_routes": effective_b2bua_routes(args),
        "route_policies": effective_route_policies(args),
        "trunk_groups": render_harness_config_templates(args.trunk_groups, args),
        "hunt_groups": render_harness_config_templates(args.hunt_groups, args),
        "number_normalization": args.number_normalization,
        "header_normalization": args.header_normalization,
        "transport_policies": args.transport_policies,
        "call_admission": args.call_admission,
        "b2bua_ladder_logs": args.ladder_enabled,
        "media_backend": args.media_backend,
        "rtpengine_url": args.rtpengine_url,
        "rtpengine_timeout": args.rtpengine_timeout,
        "rtpengine_directions": args.rtpengine_directions,
        "rtpengine_interfaces": args.rtpengine_interfaces,
        "rtpengine_max_sessions": args.rtpengine_max_sessions,
        "rtpengine_offer_transport_protocol": args.rtpengine_offer_transport_protocol,
        "rtpengine_answer_transport_protocol": args.rtpengine_answer_transport_protocol,
        "rtpengine_sdes": args.rtpengine_sdes,
        "rtpengine_dtls": args.rtpengine_dtls,
        "media_quality": args.media_quality,
        "reject_unknown_routes": args.reject_unknown_routes,
        "tls_certfile": "/etc/playsbc/tls/tls.crt" if "tls" in str(args.sip_transport).split(",") else "",
        "tls_keyfile": "/etc/playsbc/tls/tls.key" if "tls" in str(args.sip_transport).split(",") else "",
        "tls_cafile": "/etc/playsbc/tls/ca.crt" if "tls" in str(args.sip_transport).split(",") else "",
        "tls_verify_peer": args.tls_verify_peer,
        "debug": True,
    }
    values_path = work_dir / "helm-values.yaml"
    values_path.write_text(dump_simple_yaml({"playsbc": {"config": config}}), encoding="utf-8")
    rendered = run(
        [
            "helm",
            "template",
            "playsbc-dual-realm",
            str(CHART),
            "-f",
            str(values_path),
            "--show-only",
            "templates/configmap.yaml",
        ],
        env=env,
    )
    config_path = work_dir / "server-config.yaml"
    config_path.write_text(extract_helm_server_yaml(rendered.stdout), encoding="utf-8")
    return config_path


def transport_args(args: SimpleNamespace, role: str, transport_name: str) -> list[str]:
    transport = str(transport_name).lower()
    if transport == "tls":
        mode = "l1" if role == "server" else "ln"
        return ["-t", mode]
    if transport != "tcp":
        return []
    if role == "server":
        return ["-t", "t1"]
    return ["-t", "tn", "-max_socket", "1024"]


def uas_command(args: SimpleNamespace, scenario: str) -> list[str]:
    command = [
        "sipp",
        "-sf",
        scenario,
        "-s",
        args.callee,
        "-i",
        PEER_UA_IP,
        "-mi",
        PEER_UA_IP,
        "-p",
        str(SIP_PORT),
        "-m",
        str(args.calls),
        "-l",
        str(call_limit(args.calls, args.rate, args.hold_ms)),
        "-timeout",
        str(sipp_timeout_seconds(args.calls, args.rate, args.hold_ms)),
        "-timeout_error",
        "-nostdin",
    ]
    if args.uas_srtp:
        command.append("-srtpcheck_debug")
    return command + sipp_trace_args(args) + transport_args(args, "server", args.uas_transport)


def uac_command(args: SimpleNamespace, scenario: str) -> list[str]:
    command = [
        "sipp",
        f"{CORE_SBC_IP}:{TLS_PORT if args.uac_transport == 'tls' else SIP_PORT}",
        "-sf",
        scenario,
        "-s",
        args.callee,
        "-key",
        "caller",
        args.caller,
        "-i",
        CORE_UA_IP,
        "-mi",
        CORE_UA_IP,
        "-p",
        str(SIP_PORT),
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
    ]
    if args.uac_srtp:
        command.append("-srtpcheck_debug")
    return command + sipp_trace_args(args) + transport_args(args, "client", args.uac_transport)


def register_command(args: SimpleNamespace, scenario: str, user: str, realm: str) -> list[str]:
    local_ip = PEER_UA_IP if realm == "peer" else CORE_UA_IP
    server_ip = PEER_SBC_IP if realm == "peer" else CORE_SBC_IP
    transport_name = args.uas_transport if realm == "peer" else args.uac_transport
    remote_port = TLS_PORT if transport_name == "tls" else SIP_PORT
    command = [
        "sipp",
        f"{server_ip}:{remote_port}",
        "-sf",
        scenario,
        "-s",
        user,
        "-i",
        local_ip,
        "-mi",
        local_ip,
        "-p",
        str(REGISTER_PORT),
        "-key",
        "contact_port",
        str(SIP_PORT),
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
    return command + transport_args(args, "client", transport_name)


def exec_command(service: str, work_path: str, command: list[str]) -> list[str]:
    return compose_command("exec", "-T", "-w", work_path, service, *command)


def start_process(
    command: list[str],
    *,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> subprocess.Popen[str]:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout = stdout_path.open("w", encoding="utf-8")
    stderr = stderr_path.open("w", encoding="utf-8")
    process = subprocess.Popen(command, cwd=ROOT, env=env, text=True, stdout=stdout, stderr=stderr)
    process.stdout_file = stdout  # type: ignore[attr-defined]
    process.stderr_file = stderr  # type: ignore[attr-defined]
    return process


def close_process_files(process: Optional[subprocess.Popen[str]]) -> None:
    if not process:
        return
    for name in ("stdout_file", "stderr_file"):
        handle = getattr(process, name, None)
        if handle:
            handle.close()


def stop_process(process: Optional[subprocess.Popen[str]]) -> None:
    if not process:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    close_process_files(process)


def run_step(
    service: str,
    work_path: str,
    command: list[str],
    *,
    env: dict[str, str],
    output_dir: Path,
) -> tuple[int, float, list[str]]:
    wrapped = exec_command(service, work_path, command)
    started = time.monotonic()
    completed = run(wrapped, env=env, check=False)
    duration = time.monotonic() - started
    (output_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (output_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    return completed.returncode, duration, wrapped


def wait_for_server(env: dict[str, str], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        completed = run(compose_command("ps", "--status", "running", "-q", "playsbc"), env=env, check=False)
        if completed.stdout.strip():
            time.sleep(0.5)
            return
        time.sleep(0.5)
    raise RuntimeError("PlaySBC did not become ready on the dual-realm topology")


def wait_for_rtpengine(env: dict[str, str], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    command = exec_command(
        "playsbc",
        "/app",
        [
            "python3",
            "/app/tools/check_rtpengine.py",
            "--url",
            f"udp://{CORE_RTPENGINE_IP}:2223",
            "--timeout",
            "1",
        ],
    )
    while time.monotonic() < deadline:
        if run(command, env=env, check=False).returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("RTPengine did not become ready inside the core realm")


def capture_services(args: SimpleNamespace) -> list[str]:
    services = ["capture-signalling"]
    if not args.media_enabled:
        return services
    if args.media_backend == "rtpengine":
        services.append("capture-media-ring" if is_load_like_run(args) else "capture-media")
    else:
        services.append("capture-internal-media-ring" if is_load_like_run(args) else "capture-internal-media")
    return services


def rtcp_target_ports(work_dir: Path, args: SimpleNamespace, timeout: float = 1.5) -> tuple[int, int]:
    if args.media_backend != "rtpengine":
        return SERVER_RTP_MIN + 1, SERVER_RTP_MIN + 3
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        a_port, b_port = rtpengine_anchor_ports(work_dir)
        if a_port and b_port:
            return a_port + 1, b_port + 1
        time.sleep(0.05)
    # Every profile restarts RTPengine, so the first call deterministically owns
    # RTP 30000 / RTCP 30001 on both named interfaces. SIPp can buffer its trace
    # until exit inside Docker, making live SDP discovery unavailable.
    return SERVER_RTP_MIN + 1, SERVER_RTP_MIN + 1


def rtcp_command(
    service: str,
    source_ip: str,
    source_port: int,
    target_ip: str,
    target_port: int,
    duration: float,
    cname: str,
    receiver_report: bool,
) -> list[str]:
    command = [
        "python3",
        "/app/tools/send_rtcp_reports.py",
        "--local-ip",
        source_ip,
        "--source-port",
        str(source_port),
        "--target-ip",
        target_ip,
        "--target-port",
        str(target_port),
        "--ssrc",
        "0xC0DEC0DE",
        "--cname",
        cname,
        "--duration-seconds",
        f"{duration:.3f}",
        "--interval-seconds",
        "5",
        "--expect-reply",
    ]
    if receiver_report:
        command.append("--receiver-report")
    return exec_command(
        service,
        "/app",
        command,
    )


def start_rtcp_processes(
    args: SimpleNamespace,
    work_dir: Path,
    env: dict[str, str],
) -> tuple[list[tuple[str, list[str], subprocess.Popen[str], float]], list[tuple[str, list[str]]]]:
    if not should_run_rtcp(args):
        return [], []
    core_port, peer_port = rtcp_target_ports(work_dir, args)
    core_target = CORE_RTPENGINE_IP if args.media_backend == "rtpengine" else CORE_SBC_IP
    peer_target = PEER_RTPENGINE_IP if args.media_backend == "rtpengine" else PEER_SBC_IP
    duration = max(1.0, (args.hold_ms / 1000.0) - args.media_start_delay)
    commands = [
        (
            "rtcp-a",
            rtcp_command(
                "core-tools", CORE_UA_IP, UA_RTP_MIN + 1, core_target, core_port, duration,
                "core-a@playsbc", args.rtcp_receiver_reports,
            ),
        ),
        (
            "rtcp-b",
            rtcp_command(
                "peer-tools", PEER_UA_IP, UA_RTP_MIN + 1, peer_target, peer_port, duration,
                "peer-b@playsbc", args.rtcp_receiver_reports,
            ),
        ),
    ]
    processes = []
    for name, command in commands:
        started = time.monotonic()
        process = start_process(
            command,
            env=env,
            stdout_path=work_dir / f"{name}.log",
            stderr_path=work_dir / f"{name}.stderr.log",
        )
        processes.append((name, command, process, started))
    return processes, commands


def stop_captures(services: list[str], env: dict[str, str]) -> None:
    if services:
        run(compose_command("kill", "-s", "SIGINT", *services), env=env, check=False)
        time.sleep(1)


def cleanup_work_dir(work_dir: Path, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while work_dir.exists():
        try:
            shutil.rmtree(work_dir)
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
            continue
        if not work_dir.exists():
            return True
    return True


def prepare_tls_assets(work_dir: Path, env: dict[str, str], enabled: bool) -> Path:
    tls_dir = work_dir / "tls"
    tls_dir.mkdir(parents=True, exist_ok=True)
    env["PLAYSBC_TOPOLOGY_TLS_DIR"] = str(tls_dir.resolve())
    if not enabled:
        return tls_dir
    if not shutil.which("openssl"):
        raise RuntimeError("openssl is required for the TLS SIPp regression profile")
    openssl_config = tls_dir / "openssl.cnf"
    openssl_config.write_text(
        "\n".join(
            [
                "[req]",
                "prompt = no",
                "distinguished_name = subject",
                "x509_extensions = extensions",
                "[subject]",
                "CN = playsbc-lab",
                "[extensions]",
                "subjectAltName = @alt_names",
                "extendedKeyUsage = serverAuth,clientAuth",
                "[alt_names]",
                f"IP.1 = {CORE_SBC_IP}",
                f"IP.2 = {PEER_SBC_IP}",
                f"IP.3 = {CORE_UA_IP}",
                f"IP.4 = {PEER_UA_IP}",
                "DNS.1 = playsbc",
            ]
        )
        + "\n",
        encoding="ascii",
    )
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-keyout",
            str(tls_dir / "tls.key"),
            "-out",
            str(tls_dir / "tls.crt"),
            "-config",
            str(openssl_config),
        ],
        env=env,
    )
    shutil.copyfile(tls_dir / "tls.crt", tls_dir / "ca.crt")
    for role_dir in (
        work_dir / "registration-callee",
        work_dir / "registration-caller",
        work_dir / "sipp-a-uac",
        work_dir / "sipp-b-uas",
    ):
        shutil.copyfile(tls_dir / "tls.crt", role_dir / "cacert.pem")
        shutil.copyfile(tls_dir / "tls.key", role_dir / "cakey.pem")
    return tls_dir


def validate_expected_log_markers(bundle: Path, args: SimpleNamespace) -> list[str]:
    failures = []
    markers = getattr(args, "expected_log_markers", {}) or {}
    for filename, expected_values in markers.items():
        path = bundle / str(filename)
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        for expected in expected_values:
            if str(expected) not in text:
                failures.append(f"{filename} missing expected marker: {expected}")
    secure_media = bool(getattr(args, "uac_srtp", False) or getattr(args, "uas_srtp", False))
    secure_packets = 0
    secure_errors = 0
    if secure_media:
        secure_stats = rtpengine_query_stats(bundle)
        secure_packets = int(secure_stats["rtp_packets_total"])
        secure_errors = int(secure_stats["rtp_errors_total"])
        if secure_packets <= 0:
            failures.append("SRTP/RTP interworking profile has no RTPengine media packets")
        if secure_errors:
            failures.append(f"SRTP/RTP interworking profile has {secure_errors} RTPengine media errors")
    append_log(
        bundle,
        "log.platform",
        "PROFILE EVIDENCE MARKERS",
        "\n".join(
            [
                f"status={'passed' if not failures else 'failed'}",
                f"marker_file_count={len(markers)}",
                f"secure_media={str(secure_media).lower()}",
                f"rtpengine_media_packets={secure_packets}",
                f"rtpengine_media_errors={secure_errors}",
                *[f"failure={failure}" for failure in failures],
            ]
        ),
    )
    return failures


def merge_capture_files(bundle: Path, args: SimpleNamespace) -> tuple[int, int]:
    candidates = []
    for pattern in ("signalling.pcap", "media.pcap", "media-ring.pcap*", "internal-media.pcap", "internal-media-ring.pcap*"):
        candidates.extend(path for path in bundle.glob(pattern) if path.is_file() and path.stat().st_size > 24)
    candidates = sorted(set(candidates))
    if not candidates:
        raise RuntimeError("Dual-realm tcpdump produced no capture files")
    output = bundle / "capture.pcap"
    packet_count = merge_pcaps(candidates, output)
    for path in candidates:
        path.unlink(missing_ok=True)
    append_log(
        bundle,
        "log.platform",
        "PCAP GENERATION",
        "\n".join(
            [
                "source=live_docker_tcpdump",
                "scope=real_core_peer_wire_evidence",
                "file=capture.pcap",
                f"packet_count={packet_count}",
                f"segment_count={len(candidates)}",
                f"capture_bytes={output.stat().st_size}",
                f"bounded_media_ring={str(is_load_like_run(args) and args.media_enabled).lower()}",
                f"core_realm={CORE_SUBNET}",
                f"peer_realm={PEER_SUBNET}",
            ]
        ),
    )
    return packet_count, len(candidates)


def pcap_realm_packet_counts(path: Path) -> tuple[int, int]:
    _major, _minor, linktype, records = pcap_records(path)
    ip_offset = {1: 14, 276: 20}.get(linktype)
    if ip_offset is None:
        raise ValueError(f"Unsupported dual-realm PCAP link type: {linktype}")
    core = 0
    peer = 0
    for record in records:
        frame = record.data
        if len(frame) < ip_offset + 20 or frame[ip_offset] >> 4 != 4:
            continue
        src = socket.inet_ntoa(frame[ip_offset + 12 : ip_offset + 16])
        dst = socket.inet_ntoa(frame[ip_offset + 16 : ip_offset + 20])
        if src.startswith("172.28.0.") or dst.startswith("172.28.0."):
            core += 1
        if src.startswith("192.168.28.") or dst.startswith("192.168.28."):
            peer += 1
    return core, peer


def validate_topology(bundle: Path, work_dir: Path, args: SimpleNamespace) -> list[str]:
    failures = []
    config_text = (work_dir / "server-config.yaml").read_text(encoding="utf-8", errors="replace")
    expected_config_values = [
        f"sip_advertised_ip: {CORE_SBC_IP}",
        f"b2bua_advertised_ip: {PEER_SBC_IP}",
        *[f"- {direction}" for direction in args.rtpengine_directions],
    ]
    for expected in expected_config_values:
        if expected not in config_text:
            failures.append(f"Helm config missing dual-realm value: {expected}")
    capture = bundle / "capture.pcap"
    if not capture.exists():
        failures.append("Unified dual-realm capture is missing")
        return failures
    core_packets, peer_packets = pcap_realm_packet_counts(capture)
    core_expected = bool(args.run_call or args.register_caller)
    peer_expected = bool(args.register_callee or args.start_uas)
    if core_expected and core_packets <= 0:
        failures.append("Capture has no core-realm packets")
    if peer_expected and peer_packets <= 0:
        failures.append("Capture has no peer-realm packets")
    if args.media_enabled:
        payloads = rtp_payload_types(capture)
        if (CORE_UA_IP, PEER_UA_IP) in payloads or (PEER_UA_IP, CORE_UA_IP) in payloads:
            failures.append("Capture contains direct core/peer RTP bypassing PlaySBC or RTPengine")
    append_log(
        bundle,
        "log.networking",
        "DUAL REALM TOPOLOGY VALIDATION",
        "\n".join(
            [
                f"status={'passed' if not failures else 'failed'}",
                f"core_realm={CORE_SUBNET} core_ua={CORE_UA_IP} core_sbc={CORE_SBC_IP} core_rtpengine={CORE_RTPENGINE_IP}",
                f"peer_realm={PEER_SUBNET} peer_sbc={PEER_SBC_IP} peer_ua={PEER_UA_IP} peer_rtpengine={PEER_RTPENGINE_IP}",
                f"core_packets={core_packets}",
                f"peer_packets={peer_packets}",
                "placement=uac:core playsbc:dual-homed uas:peer",
                *[f"failure={failure}" for failure in failures],
            ]
        ),
    )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=sorted((*B2BUA_PROFILES, REAL_TOPOLOGY_PROFILE)))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output-root", default=str(ROOT / "logs" / "b2bua-Regression"))
    parser.add_argument("--log-folder", default="b2bua-Regression")
    parser.add_argument("--hold-ms", type=int, default=0, help="Optional focused-run hold-time override")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    args_cli = parser.parse_args()

    for binary in ("docker", "helm"):
        if not shutil.which(binary):
            parser.error(f"{binary} is required for dual-realm regression")

    run_id = args_cli.run_id or time.strftime(f"dual-realm-{args_cli.profile}-%Y%m%d-%H%M%S")
    bundle = Path(args_cli.output_root) / run_id
    if bundle.exists():
        parser.error(f"Evidence bundle already exists: {bundle}")
    bundle.mkdir(parents=True)
    initialize_log_dir(bundle)
    work_dir = bundle / "work"
    for name in ("server", "registration-callee", "registration-caller", "sipp-a-uac", "sipp-b-uas"):
        (work_dir / name).mkdir(parents=True)

    profile = profile_args(args_cli.profile, run_id, args_cli.log_folder)
    if args_cli.hold_ms > 0:
        profile.hold_ms = args_cli.hold_ms
    env = os.environ.copy()
    env["PLAYSBC_TOPOLOGY_OUTPUT"] = str(bundle.resolve())
    uac_scenario = uas_scenario = registration_scenario = ""
    results: list[SmokeResult] = []
    commands: list[tuple[str, list[str]]] = []
    uas_process: Optional[subprocess.Popen[str]] = None
    uac_process: Optional[subprocess.Popen[str]] = None
    rtcp_processes: list[tuple[str, list[str], subprocess.Popen[str], float]] = []
    captures: list[str] = []
    failure: Optional[BaseException] = None
    phase_records: list[dict[str, object]] = []
    active_phase = "Setup Preparation"
    phase_started = time.monotonic()
    phase_detail = (
        "Create the isolated evidence workspace and prepare profile-specific SIPp UAC, UAS, REGISTER, "
        "transport, and media scenarios for the core and peer realms."
    )

    try:
        prepare_tls_assets(work_dir, env, "tls" in str(profile.sip_transport).split(","))
        uac_scenario, uas_scenario, registration_scenario = prepare_scenarios(profile, bundle, work_dir)
        append_robot_phase(bundle, phase_records, active_phase, "passed", phase_started, phase_detail)

        active_phase = "Configuration"
        phase_started = time.monotonic()
        phase_detail = (
            f"Render charts/playsbc for profile={profile.profile}; transport={profile.sip_transport.upper()}; "
            f"media_backend={profile.media_backend}; default_codec={profile.server_codec}; write server-config.yaml; "
            "validate docker-compose.topology.yml."
        )
        config_path = render_helm_config(profile, work_dir, env)
        env["PLAYSBC_TOPOLOGY_CONFIG"] = str(config_path.resolve())
        run(compose_command("config", "--quiet"), env=env)
        append_robot_phase(bundle, phase_records, active_phase, "passed", phase_started, phase_detail)

        active_phase = "Test Setup"
        phase_started = time.monotonic()
        phase_detail = (
            "Remove stale topology containers; verify or build images; start dual-homed PlaySBC, RTPengine, "
            "core/peer SIPp agents, RTCP helpers, and live packet capture; wait for readiness."
        )
        run(compose_command("down", "--remove-orphans"), env=env, check=False)
        images_available = topology_images_available(env)
        if args_cli.skip_build and not images_available:
            raise RuntimeError("Dual-realm images are missing; rerun without --skip-build")
        if args_cli.rebuild or not images_available:
            run(compose_command("build", "rtpengine", "playsbc", "sipp-a"), env=env)

        captures = capture_services(profile)
        services = ["rtpengine", "playsbc", "core-agent", "peer-agent", "core-tools", "peer-tools", *captures]
        run(compose_command("up", "-d", *services), env=env)
        wait_for_server(env)
        if profile.media_backend == "rtpengine":
            wait_for_rtpengine(env)
        append_robot_phase(bundle, phase_records, active_phase, "passed", phase_started, phase_detail)

        active_phase = "Test Execution"
        phase_started = time.monotonic()
        phase_detail = execution_detail(profile)

        if profile.register_callee:
            command = register_command(profile, registration_scenario, profile.callee, "peer")
            rc, duration, wrapped = run_step(
                "peer-agent",
                "/output/work/registration-callee",
                command,
                env=env,
                output_dir=work_dir / "registration-callee",
            )
            commands.append(("registration-callee", wrapped))
            results.append(SmokeResult("registration", wrapped, rc, "passed" if rc == 0 else "failed", duration))

        if profile.start_uas:
            command = uas_command(profile, uas_scenario)
            wrapped = exec_command("peer-agent", "/output/work/sipp-b-uas", command)
            commands.append(("sipp-b-uas", wrapped))
            uas_started = time.monotonic()
            uas_process = start_process(
                wrapped,
                env=env,
                stdout_path=work_dir / "sipp-b-uas" / "stdout.log",
                stderr_path=work_dir / "sipp-b-uas" / "stderr.log",
            )
            time.sleep(0.5)

        if profile.register_caller:
            command = register_command(profile, registration_scenario, profile.caller, "core")
            rc, duration, wrapped = run_step(
                "core-agent",
                "/output/work/registration-caller",
                command,
                env=env,
                output_dir=work_dir / "registration-caller",
            )
            commands.append(("registration-caller", wrapped))
            results.append(SmokeResult("caller-registration", wrapped, rc, "passed" if rc == 0 else "failed", duration))

        if profile.run_call:
            command = uac_command(profile, uac_scenario)
            wrapped = exec_command("core-agent", "/output/work/sipp-a-uac", command)
            commands.append(("sipp-a-uac", wrapped))
            uac_started = time.monotonic()
            uac_process = start_process(
                wrapped,
                env=env,
                stdout_path=work_dir / "sipp-a-uac" / "stdout.log",
                stderr_path=work_dir / "sipp-a-uac" / "stderr.log",
            )
            if should_run_rtcp(profile):
                time.sleep(profile.media_start_delay)
                rtcp_processes, rtcp_commands = start_rtcp_processes(profile, work_dir, env)
                commands.extend(rtcp_commands)
            uac_rc = uac_process.wait(timeout=sipp_timeout_seconds(profile.calls, profile.rate, profile.hold_ms) + 30)
            close_process_files(uac_process)
            uac_process = None
            results.append(
                SmokeResult("sipp-a-uac", wrapped, uac_rc, "passed" if uac_rc == 0 else "failed", time.monotonic() - uac_started)
            )

        for name, command, process, started in rtcp_processes:
            rc = process.wait(timeout=max(10, int(profile.hold_ms / 1000) + 15))
            close_process_files(process)
            results.append(SmokeResult(name, command, rc, "passed" if rc == 0 else "failed", time.monotonic() - started))
        rtcp_processes = []

        if uas_process is not None:
            uas_rc = uas_process.wait(timeout=sipp_timeout_seconds(profile.calls, profile.rate, profile.hold_ms) + 30)
            close_process_files(uas_process)
            uas_process = None
            results.append(
                SmokeResult("sipp-b-uas", [], uas_rc, "passed" if uas_rc == 0 else "failed", time.monotonic() - uas_started)
            )

        if profile.profile == "load-5cps-60s-rtpengine-transcoding":
            observed, duration = wait_for_rtpengine_load_queries(bundle, profile.calls)
            append_log(
                bundle,
                "log.platform",
                "RTPENGINE LOAD QUERY DRAIN",
                f"expected_queries={profile.calls} observed_query_results={observed} duration_seconds={duration:.3f}",
            )
        execution_status = "failed" if any(result.status == "failed" for result in results) else "passed"
        append_robot_phase(bundle, phase_records, active_phase, execution_status, phase_started, phase_detail)
        active_phase = ""
    except BaseException as exc:  # cleanup must run for Docker and capture processes
        failure = exc
        if active_phase:
            append_robot_phase(
                bundle,
                phase_records,
                active_phase,
                "failed",
                phase_started,
                f"{phase_detail} Failure: {type(exc).__name__}: {exc}",
            )
            active_phase = ""
        results.append(SmokeResult("dual-realm-runner", [], 1, "failed", 0.0))
    finally:
        teardown_started = time.monotonic()
        teardown_status = "passed"
        teardown_detail = (
            "Stop SIPp and RTCP processes; stop packet capture; collect combined PlaySBC container output; "
            "remove the isolated Docker topology."
        )
        try:
            stop_process(uac_process)
            stop_process(uas_process)
            for _name, _command, process, _started in rtcp_processes:
                stop_process(process)
            if env.get("PLAYSBC_TOPOLOGY_CONFIG"):
                stop_captures(captures, env)
                server_logs = run(compose_command("logs", "--no-color", "playsbc"), env=env, check=False)
                (work_dir / "server" / "stdout.log").write_text(server_logs.stdout + server_logs.stderr, encoding="utf-8")
                run(compose_command("down", "--remove-orphans"), env=env, check=False)
        except BaseException as exc:
            teardown_status = "failed"
            teardown_detail += f" Failure: {type(exc).__name__}: {exc}"
            if failure is None:
                failure = exc
            results.append(SmokeResult("dual-realm-teardown", [], 1, "failed", 0.0))
        append_robot_phase(bundle, phase_records, "Test Teardown", teardown_status, teardown_started, teardown_detail)

    evidence_started = time.monotonic()
    evidence_detail = (
        "Merge capture segments into capture.pcap; combine both SIP legs into SBC-style logs; validate digest, "
        "dialog targets, ladders, RTP/RTCP, DTMF, transcoding, load completeness, and dual-realm topology; "
        "remove temporary work files."
    )
    try:
        try:
            merge_capture_files(bundle, profile)
        except (OSError, RuntimeError, ValueError) as exc:
            if failure is None:
                failure = exc
                results.append(SmokeResult("dual-realm-pcap", [], 1, "failed", 0.0))

        collect_work_logs(bundle, work_dir, profile)
        append_commands(bundle, commands)
        append_registration_auth_observation(bundle, work_dir, profile, results)
        remote_target_errors = dialog_remote_target_errors(work_dir)
        if remote_target_errors:
            append_log(bundle, "log.sip", "DIALOG REMOTE TARGET FAILED", "\n".join(remote_target_errors))
            results.append(SmokeResult("sip-dialog-remote-target", [], 1, "failed", 0.0))
        append_registration_ladders(bundle, profile, results)
        append_media_observation(bundle, profile)
        if not append_rtcp_observation(bundle, work_dir, profile, results):
            results.append(SmokeResult("rtcp-validation", [], 1, "failed", 0.0))
        append_dtmf_observation(bundle, profile, results)
        append_transcoding_observation(bundle, profile)
        if not rtpengine_load_media_complete(bundle, profile):
            results.append(SmokeResult("rtpengine-load-completeness", [], 1, "failed", 0.0))
        topology_failures = validate_topology(bundle, work_dir, profile)
        if topology_failures:
            results.append(SmokeResult("dual-realm-validation", [], 1, "failed", 0.0))
        marker_failures = validate_expected_log_markers(bundle, profile)
        if marker_failures:
            results.append(SmokeResult("profile-evidence-markers", [], 1, "failed", 0.0))
        if failure is not None:
            append_log(bundle, "log.platform", "DUAL REALM RUNNER FAILURE", f"{type(failure).__name__}: {failure}")
    except BaseException as exc:
        if failure is None:
            failure = exc
        results.append(SmokeResult("evidence-validation", [], 1, "failed", 0.0))
        evidence_detail += f" Failure: {type(exc).__name__}: {exc}"
    finally:
        if not cleanup_work_dir(work_dir):
            results.append(SmokeResult("evidence-work-cleanup", [], 1, "failed", 0.0))
            append_log(bundle, "log.platform", "EVIDENCE WORK CLEANUP FAILED", f"path={work_dir}")
        evidence_status = "failed" if failure is not None or any(result.status == "failed" for result in results) else "passed"
        append_robot_phase(bundle, phase_records, "Evidence Validation", evidence_status, evidence_started, evidence_detail)

    append_skipped_robot_phases(bundle, phase_records)
    flush_robot_phases(bundle, phase_records)
    append_results(bundle, profile, results)

    print(f"B2BUA SIPp logs: {bundle}")
    for result in results:
        print(f"{result.name}: {result.status}")
    return 1 if failure is not None or any(result.status == "failed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
