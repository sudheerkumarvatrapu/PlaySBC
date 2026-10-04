#!/usr/bin/env python3
"""Verify the dedicated kind real-device lab before placing a LAN call."""

from __future__ import annotations

import argparse
import ipaddress
import json
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any

PORT_RANGES = ((30000, 30023), (30024, 30049))


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def command_text(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def run_command(command: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout)


def required_bindings(rtp_min: int, rtp_max: int) -> set[tuple[int, str]]:
    bindings = {(5062, "tcp"), (5062, "udp"), (5061, "tcp")}
    bindings.update((port, "udp") for port in range(rtp_min, rtp_max + 1))
    return bindings


def published_bindings(ports: dict[str, Any]) -> set[tuple[int, str]]:
    published: set[tuple[int, str]] = set()
    for container_key, host_entries in ports.items():
        try:
            _container_port, protocol = str(container_key).split("/", 1)
        except ValueError:
            continue
        if not isinstance(host_entries, list):
            continue
        for entry in host_entries:
            if not isinstance(entry, dict):
                continue
            try:
                host_port = int(entry.get("HostPort", 0))
            except (TypeError, ValueError):
                continue
            if host_port:
                published.add((host_port, protocol.lower()))
    return published


def workload_checks(
    workload: dict[str, Any],
    *,
    name: str,
    container: str,
    expected_version: str,
) -> list[CheckResult]:
    spec = workload.get("spec", {})
    pod_spec = spec.get("template", {}).get("spec", {})
    containers = pod_spec.get("containers", [])
    target = next(
        (item for item in containers if isinstance(item, dict) and item.get("name") == container),
        {},
    )
    replicas = int(spec.get("replicas", 0) or 0)
    ready = int(workload.get("status", {}).get("readyReplicas", 0) or 0)
    image = str(target.get("image", ""))
    return [
        CheckResult(f"{name}-active-active", replicas == 2 and ready == 2, f"desired={replicas} ready={ready}"),
        CheckResult(f"{name}-network", pod_spec.get("hostNetwork") is (name == "rtpengine"), str(pod_spec.get("hostNetwork"))),
        CheckResult(
            f"{name}-cluster-dns",
            pod_spec.get("dnsPolicy") == ("ClusterFirstWithHostNet" if name == "rtpengine" else "ClusterFirst"),
            str(pod_spec.get("dnsPolicy", "")),
        ),
        CheckResult(
            f"{name}-image",
            not expected_version or image.endswith(f":{expected_version}"),
            image or "missing",
        ),
    ]


def config_checks(config: str, lan_ip: str, rtp_min: int, rtp_max: int, release: str = "playsbc") -> list[CheckResult]:
    expected = {
        "sip-advertised-ip": f"sip_advertised_ip: {lan_ip}",
        "b2bua-advertised-ip": f"b2bua_advertised_ip: {lan_ip}",
        "sip-transports": "sip_transport: udp,tcp,tls",
        "rtp-min": f"rtp_min: {rtp_min}",
        "rtp-max": f"rtp_max: {rtp_max}",
        "rtpengine-backend": "media_backend: rtpengine",
        "rtpengine-pair-0": f"{release}-playsbc-rtpengine-0.{release}-playsbc-rtpengine-headless:2223",
        "rtpengine-pair-1": f"{release}-playsbc-rtpengine-1.{release}-playsbc-rtpengine-headless:2224",
    }
    return [CheckResult(name, marker in config, marker) for name, marker in expected.items()]


def rtpengine_command_checks(
    workload: dict[str, Any], lan_ip: str, rtp_min: int, rtp_max: int
) -> list[CheckResult]:
    containers = workload.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    container = next(
        (item for item in containers if isinstance(item, dict) and item.get("name") == "rtpengine"),
        {},
    )
    rendered = " ".join(str(value) for value in [*container.get("command", []), *container.get("args", [])])
    markers = {
        "rtpengine-advertised-ip": f"!{lan_ip}",
        "rtpengine-first-range": f"media_min={rtp_min}; media_max={PORT_RANGES[0][1]}; ng_port=2223",
        "rtpengine-second-range": f"media_min={PORT_RANGES[1][0]}; media_max={rtp_max}; ng_port=2224",
    }
    return [CheckResult(name, marker in rendered, marker) for name, marker in markers.items()]


def service_checks(service: dict[str, Any]) -> list[CheckResult]:
    spec = service.get("spec", {})
    ports = {(item.get("name"), item.get("protocol")): item.get("nodePort") for item in spec.get("ports", [])}
    return [
        CheckResult("sip-nodeport", spec.get("type") == "NodePort" and
                    ports.get(("sip-udp", "UDP")) == 32062 and
                    ports.get(("sip-tcp", "TCP")) == 32062, str(ports)),
        CheckResult("tls-nodeport", ports.get(("sip-tls", "TCP")) == 32061, str(ports)),
        CheckResult("sip-affinity", spec.get("sessionAffinity") == "ClientIP", str(spec.get("sessionAffinity"))),
    ]


def rtpengine_service_checks(service: dict[str, Any], release: str) -> list[CheckResult]:
    selector = service.get("spec", {}).get("selector", {})
    primary = f"{release}-playsbc-rtpengine-0"
    return [CheckResult("rtpengine-primary-service", selector.get("statefulset.kubernetes.io/pod-name") == primary, str(selector))]


def ready_rtpengine_pods(payload: dict[str, Any], release: str, node_ip: str) -> CheckResult:
    pods = {pod.get("metadata", {}).get("name"): pod for pod in payload.get("items", [])}
    expected = [f"{release}-playsbc-rtpengine-{index}" for index in range(2)]
    details = []
    valid = True
    for name in expected:
        pod = pods.get(name, {})
        status = pod.get("status", {})
        container_statuses = status.get("containerStatuses", [])
        ready = status.get("phase") == "Running" and bool(container_statuses) and all(
            item.get("ready") for item in container_statuses
        )
        valid = valid and ready and status.get("podIP") == node_ip
        details.append(f"{name}:ready={ready},ip={status.get('podIP', 'missing')}")
    return CheckResult("rtpengine-pods-ready-on-kind-node", valid, "; ".join(details))


def parse_json_result(result: subprocess.CompletedProcess[str], description: str) -> dict[str, Any]:
    if result.returncode != 0:
        raise RuntimeError(f"{description} failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{description} returned invalid JSON: {exc}") from exc
    return parsed if isinstance(parsed, dict) else {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", default="playsbc-real-device-aa")
    parser.add_argument("--context", default="kind-playsbc-real-device-aa")
    parser.add_argument("--namespace", default="playsbc")
    parser.add_argument("--release", default="playsbc")
    parser.add_argument("--lan-ip", required=True)
    parser.add_argument("--expected-version", default="")
    parser.add_argument("--rtp-min", type=int, default=30000)
    parser.add_argument("--rtp-max", type=int, default=30049)
    parser.add_argument("--kind-bin", default="kind")
    parser.add_argument("--kubectl-bin", default="kubectl")
    parser.add_argument("--docker-bin", default="docker")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    lan_ip = str(ipaddress.IPv4Address(args.lan_ip))
    if ipaddress.ip_address(lan_ip).is_loopback:
        raise SystemExit("--lan-ip must be the Mac LAN address, not loopback")

    node_result = run_command([args.kind_bin, "get", "nodes", "--name", args.cluster])
    if node_result.returncode != 0:
        raise SystemExit(node_result.stderr.strip() or f"kind cluster {args.cluster!r} was not found")
    nodes = [line.strip() for line in node_result.stdout.splitlines() if line.strip()]
    if len(nodes) != 1:
        raise SystemExit(f"expected one dedicated kind node, found {nodes}")

    inspect = run_command(
        [args.docker_bin, "inspect", nodes[0], "--format", "{{json .NetworkSettings.Ports}}"]
    )
    ports = parse_json_result(inspect, "docker port inspection")
    expected_ports = required_bindings(args.rtp_min, args.rtp_max)
    actual_ports = published_bindings(ports)
    missing_ports = sorted(expected_ports - actual_ports)

    prefix = [args.kubectl_bin, "--context", args.context, "-n", args.namespace]
    sbc_name = f"{args.release}-playsbc"
    rtpengine_name = f"{args.release}-playsbc-rtpengine"
    sbc = parse_json_result(
        run_command([*prefix, "get", "statefulset", sbc_name, "-o", "json"]),
        f"statefulset/{sbc_name}",
    )
    rtpengine = parse_json_result(
        run_command([*prefix, "get", "statefulset", rtpengine_name, "-o", "json"]),
        f"statefulset/{rtpengine_name}",
    )
    service = parse_json_result(run_command([*prefix, "get", "service", sbc_name, "-o", "json"]), "SIP Service")
    rtp_service = parse_json_result(run_command([*prefix, "get", "service", rtpengine_name, "-o", "json"]), "RTPengine Service")
    pods = parse_json_result(run_command([*prefix, "get", "pods", "-l", f"app.kubernetes.io/name=playsbc-rtpengine,app.kubernetes.io/instance={args.release}", "-o", "json"]), "RTPengine pods")
    node = parse_json_result(run_command([*prefix, "get", "node", nodes[0], "-o", "json"]), "kind node")
    node_ip = next((address.get("address", "") for address in node.get("status", {}).get("addresses", []) if address.get("type") == "InternalIP"), "")
    config_result = run_command([*prefix, "get", "configmap", f"{args.release}-playsbc-config", "-o", "json"])
    config_map = parse_json_result(config_result, "PlaySBC ConfigMap")
    config = str(config_map.get("data", {}).get("server.yaml", ""))

    checks = [
        CheckResult(
            "docker-port-mappings",
            not missing_ports,
            "complete" if not missing_ports else ",".join(f"{port}/{protocol}" for port, protocol in missing_ports),
        ),
        *workload_checks(sbc, name="playsbc", container="playsbc", expected_version=args.expected_version),
        *workload_checks(
            rtpengine,
            name="rtpengine",
            container="rtpengine",
            expected_version=args.expected_version,
        ),
        *config_checks(config, lan_ip, args.rtp_min, args.rtp_max, args.release),
        *rtpengine_command_checks(rtpengine, lan_ip, args.rtp_min, args.rtp_max),
        *service_checks(service),
        *rtpengine_service_checks(rtp_service, args.release),
        ready_rtpengine_pods(pods, args.release, node_ip),
    ]

    for check in checks:
        print(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
    failed = [check for check in checks if not check.passed]
    if failed:
        print(f"kind real-device preflight failed: {len(failed)} check(s)")
        return 1
    print(
        f"kind real-device preflight passed: context={args.context} lan_ip={lan_ip} "
        f"sip=5062/udp+tcp,5061/tcp rtp={args.rtp_min}-{args.rtp_max}/udp"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
