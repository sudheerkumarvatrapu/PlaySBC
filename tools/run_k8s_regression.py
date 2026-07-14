#!/usr/bin/env python3
"""Run PlaySBC Kubernetes SIPp regression profiles and write an HTML report."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "sipp" / "scenarios"
SMOKE_PROFILES = ("options", "register-contact", "b2bua-signalling")

sys.path.insert(0, str(ROOT))
from tools.run_b2bua_sipp_smoke import (  # noqa: E402
    BASE_DEFAULTS,
    B2BUA_PROFILES,
    MEDIA_PCAPS,
    MEDIA_PAYLOAD_TYPES,
    MEDIA_RTPMAP_LINES,
    PROFILE_DESCRIPTIONS,
    call_limit,
    dump_simple_yaml,
    is_transcoding_profile,
    registration_ladder_text,
    render_harness_config_templates,
    sipp_timeout_seconds,
    uas_media_codec,
)
from tools.run_regression_suite import (  # noqa: E402
    ALL_B2BUA_PROFILES,
    REAL_TOPOLOGY_PROFILE,
    cleanup_old_reports,
    ReportPhase,
    ReportRow,
    write_reports,
)
from mini_call_server import B2BUAFlowLog, RouteResult, SipUri  # noqa: E402

DEFAULT_PROFILES = ("basic-signalling", "basic-media", "transcoding", "registered-inbound", "registered-outbound")
ALL_PROFILES = ALL_B2BUA_PROFILES
SELECTABLE_PROFILES = (*SMOKE_PROFILES, *ALL_PROFILES)
LAB_TLS_SECRET_NAME = "playsbc-regression-tls"


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    duration_seconds: float
    stdout: str
    stderr: str


def make_run_id() -> str:
    return time.strftime("k8s-regression-%Y%m%d-%H%M%S", time.localtime())


def command_text(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def run_command(
    command: list[str],
    *,
    timeout: int,
    input_text: Optional[str] = None,
    check: bool = False,
) -> CommandResult:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        input=input_text,
        capture_output=True,
        timeout=timeout,
    )
    result = CommandResult(
        command=command,
        returncode=completed.returncode,
        duration_seconds=time.monotonic() - started,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command_text(command)}\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def ensure_binary(name: str) -> None:
    if not shutil.which(name):
        raise SystemExit(f"{name} executable not found in PATH")


def short_name(text: str, limit: int = 44) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return cleaned[:limit].strip("-") or "profile"


def status_from_codes(returncodes: list[int]) -> str:
    return "passed" if returncodes and all(code == 0 for code in returncodes) else "failed"


def profile_values(profile: str, run_id: str) -> SimpleNamespace:
    values: dict[str, Any] = dict(BASE_DEFAULTS)
    if profile == REAL_TOPOLOGY_PROFILE:
        values.update(B2BUA_PROFILES["rtpengine-transcoding"])
        values.update({"caller": "core-a", "callee": "peer-b"})
    else:
        values.update(B2BUA_PROFILES[profile])
    values.update(
        {
            "profile": profile,
            "resolved_run_id": run_id,
            "host": "",
            "server_host": "",
            "server_port": 5062,
            "uac_port": 5060,
            "uas_port": 5060,
            "register_port": 5070,
            "caller_register_port": 5070,
            "sipp_pcap_sudo": False,
            "dry_run": False,
            "pcap_topology": "kubernetes-dual-realm",
        }
    )
    if values.get("rtpengine_url") == BASE_DEFAULTS["rtpengine_url"]:
        values["rtpengine_url"] = "udp://playsbc-playsbc-rtpengine:2223"
    transports = [item.strip() for item in str(values.get("sip_transport", "udp")).split(",") if item.strip()]
    values["uac_transport"] = values.get("uac_transport") or (transports[0] if len(transports) == 1 else "udp")
    values["uas_transport"] = values.get("uas_transport") or (transports[0] if len(transports) == 1 else "udp")
    values["media_enabled"] = bool(values.get("media_codec"))
    values["media_pcap"] = values.get("media_pcap") or (MEDIA_PCAPS[values["media_codec"]] if values.get("media_codec") else "")
    values["server_codec"] = values.get("server_codec") or values.get("media_codec") or "PCMU"
    values["ladder_enabled"] = values.get("ladder") if values.get("ladder") is not None else (values.get("calls", 1) == 1 and values.get("rate", 1) == 1)
    return SimpleNamespace(**values)


def format_config_value(value: object, profile: SimpleNamespace) -> object:
    rendered = render_harness_config_templates(value, profile)
    return rendered


def route_policies_for(profile: SimpleNamespace) -> list[dict[str, object]]:
    policies = getattr(profile, "route_policies", None) or [
        {"name": "registered-endpoints", "match": "*", "target": "registration", "priority": 10}
    ]
    rendered = format_config_value(policies, profile)
    return rendered if isinstance(rendered, list) else []


def b2bua_routes_for(profile: SimpleNamespace) -> dict[str, object]:
    rendered = format_config_value(getattr(profile, "b2bua_routes", {}) or {}, profile)
    return rendered if isinstance(rendered, dict) else {}


def transport_args(transport: str, role: str) -> list[str]:
    name = str(transport or "udp").lower()
    tls_material = [
        "-tls_cert",
        "/tmp/playsbc-tls/tls.crt",
        "-tls_key",
        "/tmp/playsbc-tls/tls.key",
        "-tls_ca",
        "/tmp/playsbc-tls/ca.crt",
    ]
    if name == "tcp":
        return ["-t", "t1"] if role == "server" else ["-t", "tn", "-max_socket", "1024"]
    if name == "tls":
        return [*(["-t", "l1"] if role == "server" else ["-t", "ln"]), *tls_material]
    return []


def trace_args() -> list[str]:
    return ["-trace_msg", "-trace_err", "-trace_stat", "-trace_counts", "-trace_logs"]


def sdp_payloads(profile: SimpleNamespace, role: str) -> tuple[str, str]:
    if is_transcoding_profile(profile):
        codec = uas_media_codec(profile) if role == "uas" else str(getattr(profile, "media_codec", "PCMU")).upper()
        payload_type = MEDIA_PAYLOAD_TYPES[codec]
        return f"{payload_type} 101", MEDIA_RTPMAP_LINES[codec]
    return "0 8 101", "\n      ".join(MEDIA_RTPMAP_LINES[codec] for codec in ("PCMU", "PCMA"))


def media_pcap_path(profile: SimpleNamespace, role: str) -> str:
    codec = uas_media_codec(profile) if role == "uas" else str(getattr(profile, "media_codec", "PCMU")).upper()
    relative = MEDIA_PCAPS.get(codec, MEDIA_PCAPS["PCMU"])
    return f"/scenarios/{relative}"


def scenario_source(profile: SimpleNamespace, role: str) -> Path:
    if role == "register":
        return SCENARIO_DIR / str(getattr(profile, "registration_scenario", "register_contact.xml"))
    if role == "uac":
        configured = str(getattr(profile, "uac_scenario", "") or "")
        if configured:
            return SCENARIO_DIR / configured
        return SCENARIO_DIR / ("b2bua_uac_a_media.xml" if getattr(profile, "media_enabled", False) else "b2bua_uac_a.xml")
    configured = str(getattr(profile, "uas_scenario", "") or "")
    if configured:
        return SCENARIO_DIR / configured
    return SCENARIO_DIR / ("b2bua_uas_b_media.xml" if getattr(profile, "media_enabled", False) else "b2bua_uas_b.xml")


def rendered_scenario(profile: SimpleNamespace, role: str) -> str:
    source = scenario_source(profile, role)
    text = source.read_text(encoding="ISO-8859-1")
    if role == "register" and str(getattr(profile, "registration_auth_expected", "") or ""):
        username = str(getattr(profile, "registration_username", "") or getattr(profile, "callee", ""))
        password = str(getattr(profile, "registration_password", ""))
        text = text.replace("__AUTH_USERNAME__", username).replace("__AUTH_PASSWORD__", password)
    if "[media_pcap]" in text:
        text = text.replace("[media_pcap]", media_pcap_path(profile, "uas" if role == "uas" else "uac"))
    if "[uac_sdp_payloads]" in text:
        payloads, rtpmaps = sdp_payloads(profile, "uac")
        text = text.replace("[uac_sdp_payloads]", payloads).replace("[uac_sdp_rtpmaps]", rtpmaps)
    if "[uas_sdp_payloads]" in text:
        payloads, rtpmaps = sdp_payloads(profile, "uas")
        text = text.replace("[uas_sdp_payloads]", payloads).replace("[uas_sdp_rtpmaps]", rtpmaps)
    return text


def is_load_profile(profile: SimpleNamespace) -> bool:
    return int(getattr(profile, "calls", 1)) > 1


def profile_transport_tokens(profile: SimpleNamespace) -> set[str]:
    tokens: set[str] = set()
    for attr in ("sip_transport", "uac_transport", "uas_transport"):
        for token in str(getattr(profile, attr, "") or "").split(","):
            token = token.strip().lower()
            if token:
                tokens.add(token)
    return tokens


def profile_uses_tls(profile: SimpleNamespace) -> bool:
    return "tls" in profile_transport_tokens(profile)


def profile_uses_rtpengine(profile: SimpleNamespace) -> bool:
    return str(getattr(profile, "media_backend", "internal")) == "rtpengine"


def scenario_configmap_manifest(name: str) -> dict[str, object]:
    data = {}
    for path in sorted(SCENARIO_DIR.glob("*.xml")):
        data[path.name] = path.read_text(encoding="ISO-8859-1")
    if not data:
        raise SystemExit(f"No SIPp XML scenarios found in {SCENARIO_DIR}")
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "labels": {
                "app.kubernetes.io/name": "playsbc-k8s-regression",
                "app.kubernetes.io/part-of": "playsbc",
            },
        },
        "data": data,
    }


def pod_manifest(
    name: str,
    image: str,
    pull_policy: str,
    configmap: str,
    run_id: str,
    realm: str = "",
    tls_secret: str = "",
) -> dict[str, object]:
    labels = {
        "app.kubernetes.io/name": "playsbc-k8s-regression",
        "app.kubernetes.io/part-of": "playsbc",
        "playsbc-regression-run": run_id,
    }
    if realm:
        labels["playsbc.openai.com/realm"] = realm
    volume_mounts = [{"name": "scenario-overrides", "mountPath": "/scenario-overrides", "readOnly": True}]
    volumes: list[dict[str, object]] = [{"name": "scenario-overrides", "configMap": {"name": configmap}}]
    if tls_secret:
        volume_mounts.append({"name": "tls-secret", "mountPath": "/tmp/playsbc-tls", "readOnly": True})
        volumes.append({"name": "tls-secret", "secret": {"secretName": tls_secret}})
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "sipp-agent",
                    "image": image,
                    "imagePullPolicy": pull_policy,
                    "command": ["sleep", "3600"],
                    "volumeMounts": volume_mounts,
                    "securityContext": {
                        "capabilities": {"add": ["NET_RAW"]},
                    },
                }
            ],
            "volumes": volumes,
        },
    }


class PhaseLog:
    def __init__(self) -> None:
        self.phases: list[ReportPhase] = []

    def append(self, name: str, status: str, started: float, detail: str) -> None:
        self.phases.append(
            ReportPhase(
                name=name,
                status=status,
                duration_seconds=time.monotonic() - started,
                detail=detail,
            )
        )


class K8sRegressionRunner:
    def __init__(self, args: argparse.Namespace, run_id: str) -> None:
        self.args = args
        self.run_id = run_id
        self.image_prepared = False
        self.original_values: Optional[dict[str, Any]] = None
        self.tls_secret_prepared = False

    def kubectl(self, *parts: str, timeout: Optional[int] = None, input_text: Optional[str] = None, check: bool = False) -> CommandResult:
        command = [self.args.kubectl_bin]
        if self.args.namespace:
            command.extend(["-n", self.args.namespace])
        command.extend(parts)
        return run_command(command, timeout=timeout or self.args.timeout, input_text=input_text, check=check)

    def kubectl_cluster(self, *parts: str, timeout: Optional[int] = None, input_text: Optional[str] = None, check: bool = False) -> CommandResult:
        command = [self.args.kubectl_bin, *parts]
        return run_command(command, timeout=timeout or self.args.timeout, input_text=input_text, check=check)

    def helm_values(self) -> dict[str, Any]:
        result = run_command(
            [
                self.args.helm_bin,
                "get",
                "values",
                self.args.helm_release,
                "--namespace",
                self.args.namespace,
                "--all",
                "-o",
                "json",
            ],
            timeout=self.args.helm_timeout,
            check=True,
        )
        return json.loads(result.stdout or "{}")

    def capture_original_values(self) -> None:
        if self.original_values is None:
            self.original_values = self.helm_values()

    def restore_original_values(self, report_dir: Path) -> Optional[str]:
        if self.args.no_restore_helm_values or self.original_values is None:
            return None
        restore_path = report_dir / f"{self.run_id}-helm-restore-values.json"
        restore_path.parent.mkdir(parents=True, exist_ok=True)
        restore_path.write_text(json.dumps(self.original_values, indent=2, sort_keys=True), encoding="utf-8")
        result = run_command(
            [
                self.args.helm_bin,
                "upgrade",
                self.args.helm_release,
                self.args.chart,
                "--namespace",
                self.args.namespace,
                "-f",
                str(restore_path),
            ],
            timeout=self.args.helm_timeout,
            check=False,
        )
        (report_dir / f"{self.run_id}-helm-restore.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode != 0:
            return f"Helm restore failed: {result.stderr.strip() or result.stdout.strip()}"
        rollout = self.kubectl(
            "rollout",
            "status",
            f"deployment/{self.args.deployment}",
            f"--timeout={self.args.rollout_timeout}s",
            check=False,
        )
        (report_dir / f"{self.run_id}-helm-restore-rollout.log").write_text(
            rollout.stdout + rollout.stderr,
            encoding="utf-8",
        )
        if rollout.returncode != 0:
            return f"Helm restore rollout failed: {rollout.stderr.strip() or rollout.stdout.strip()}"
        return None

    def ensure_tls_secret(self, bundle: Path) -> None:
        if self.tls_secret_prepared:
            return
        cert_text, key_text = self.generate_lab_tls_pair(bundle)
        manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": self.args.tls_secret_name,
                "labels": {
                    "app.kubernetes.io/name": "playsbc-k8s-regression",
                    "app.kubernetes.io/part-of": "playsbc",
                    "playsbc-regression-run": self.run_id,
                },
            },
            "type": "kubernetes.io/tls",
            "stringData": {
                "tls.crt": cert_text,
                "tls.key": key_text,
                "ca.crt": cert_text,
            },
        }
        result = self.kubectl("apply", "-f", "-", input_text=json.dumps(manifest), check=True)
        self.write_log(bundle, "log.platform", "TLS REGRESSION SECRET READY", result.stdout + result.stderr)
        self.tls_secret_prepared = True

    def generate_lab_tls_pair(self, bundle: Path) -> tuple[str, str]:
        ensure_binary("openssl")
        with tempfile.TemporaryDirectory(prefix="playsbc-k8s-tls-") as tmp:
            tmp_path = Path(tmp)
            cert_path = tmp_path / "tls.crt"
            key_path = tmp_path / "tls.key"
            san = (
                f"DNS:{self.args.service},"
                f"DNS:{self.args.service}.{self.args.namespace}.svc,"
                "DNS:localhost,IP:127.0.0.1"
            )
            result = run_command(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key_path),
                    "-out",
                    str(cert_path),
                    "-days",
                    "30",
                    "-subj",
                    f"/CN={self.args.service}",
                    "-addext",
                    f"subjectAltName={san}",
                ],
                timeout=30,
                check=True,
            )
            self.write_log(bundle, "log.platform", "TLS REGRESSION CERT GENERATED", result.stdout + result.stderr)
            return cert_path.read_text(encoding="utf-8"), key_path.read_text(encoding="utf-8")

    def prepare_common(self, bundle: Path, phases: PhaseLog) -> None:
        started = time.monotonic()
        if not self.args.skip_namespace_check:
            self.kubectl_cluster("get", "namespace", self.args.namespace, check=True)
        self.kubectl("get", "service", self.args.service, check=True)
        self.kubectl("get", "service", self.args.rtpengine_service, check=False)
        manifest = scenario_configmap_manifest(self.args.configmap)
        result = self.kubectl("apply", "-f", "-", input_text=json.dumps(manifest), check=True)
        self.write_log(bundle, "log.platform", "K8S REGRESSION PREPARED", result.stdout or "scenario configmap applied")
        phases.append(
            "Setup Preparation",
            "passed",
            started,
            (
                f"Verified namespace={self.args.namespace}, service={self.args.service}:{self.args.sip_port}, "
                f"namespace_check={not self.args.skip_namespace_check}, and applied ConfigMap={self.args.configmap} "
                "with SIPp XML scenarios."
            ),
        )

    def build_and_load_sipp_image(self, bundle: Path, phases: PhaseLog) -> None:
        if not self.args.build_sipp_image and not self.args.kind_load_image:
            return
        started = time.monotonic()
        if self.image_prepared:
            phases.append(
                "Configuration",
                "passed",
                started,
                f"Reused SIPp image preparation from an earlier profile in this run: image={self.args.sipp_image}.",
            )
            return
        if self.args.build_sipp_image:
            ensure_binary("docker")
            result = run_command(
                ["docker", "build", "-f", str(ROOT / "docker" / "sipp.Dockerfile"), "-t", self.args.sipp_image, "."],
                timeout=self.args.image_build_timeout,
                check=True,
            )
            self.write_log(bundle, "log.platform", "SIPP IMAGE BUILD", result.stdout + result.stderr)
        if self.args.kind_load_image:
            ensure_binary("kind")
            result = run_command(
                ["kind", "load", "docker-image", self.args.sipp_image, "--name", self.args.kind_cluster],
                timeout=self.args.timeout,
                check=True,
            )
            self.write_log(bundle, "log.platform", "SIPP IMAGE KIND LOAD", result.stdout + result.stderr)
        phases.append(
            "Configuration",
            "passed",
            started,
            f"Prepared SIPp image={self.args.sipp_image}; build={self.args.build_sipp_image}; kind_load={self.args.kind_load_image}.",
        )
        self.image_prepared = True

    def create_agent(self, name: str, bundle: Path, realm: str = "", tls_secret: str = "") -> str:
        manifest = pod_manifest(
            name,
            self.args.sipp_image,
            self.args.image_pull_policy,
            self.args.configmap,
            self.run_id,
            realm=realm,
            tls_secret=tls_secret,
        )
        self.kubectl("apply", "-f", "-", input_text=json.dumps(manifest), check=True)
        self.kubectl("wait", "--for=condition=Ready", f"pod/{name}", f"--timeout={self.args.pod_ready_timeout}s", check=True)
        ip_result = self.kubectl("get", "pod", name, "-o", "jsonpath={.status.podIP}", check=True)
        pod_ip = ip_result.stdout.strip()
        if not pod_ip:
            describe = self.kubectl("describe", "pod", name, check=False)
            self.write_log(bundle, "log.platform", f"POD {name} DESCRIBE", describe.stdout + describe.stderr)
            raise RuntimeError(f"Pod {name} did not receive an IP address")
        self.write_log(bundle, "log.platform", f"POD {name} READY", f"pod_ip={pod_ip}")
        return pod_ip

    def delete_run_pods(self, bundle: Path) -> CommandResult:
        selector = f"playsbc-regression-run={self.run_id}"
        result = self.kubectl("delete", "pod", "-l", selector, "--ignore-not-found=true", check=False)
        self.write_log(bundle, "log.platform", "K8S REGRESSION POD CLEANUP", result.stdout + result.stderr)
        return result

    def sipp_exec_command(self, pod: str, sipp_args: list[str]) -> list[str]:
        shell_command = f"cd /tmp && {shlex.join(['sipp', *sipp_args])}"
        return [self.args.kubectl_bin, "-n", self.args.namespace, "exec", pod, "--", "sh", "-lc", shell_command]

    def run_sipp_step(self, pod: str, step_name: str, sipp_args: list[str], bundle: Path, timeout: Optional[int] = None) -> CommandResult:
        command = self.sipp_exec_command(pod, sipp_args)
        result = run_command(command, timeout=timeout or self.args.sipp_timeout)
        step_dir = bundle / step_name
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "command.txt").write_text(command_text(command) + "\n", encoding="utf-8")
        (step_dir / "stdout.log").write_text(result.stdout, encoding="utf-8")
        (step_dir / "stderr.log").write_text(result.stderr, encoding="utf-8")
        self.write_log(bundle, "log.sipp", f"{step_name.upper()} COMMAND", command_text(command))
        self.write_log(
            bundle,
            "log.sipp",
            f"{step_name.upper()} RESULT",
            f"returncode={result.returncode} duration_seconds={result.duration_seconds:.3f}",
        )
        self.collect_sipp_traces(pod, step_dir)
        return result

    def start_sipp_process(self, pod: str, step_name: str, sipp_args: list[str], bundle: Path) -> subprocess.Popen[str]:
        command = self.sipp_exec_command(pod, sipp_args)
        step_dir = bundle / step_name
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "command.txt").write_text(command_text(command) + "\n", encoding="utf-8")
        stdout = (step_dir / "stdout.log").open("w", encoding="utf-8")
        stderr = (step_dir / "stderr.log").open("w", encoding="utf-8")
        self.write_log(bundle, "log.sipp", f"{step_name.upper()} COMMAND", command_text(command))
        process = subprocess.Popen(command, cwd=ROOT, text=True, stdout=stdout, stderr=stderr)
        process._playsbc_stdout = stdout  # type: ignore[attr-defined]
        process._playsbc_stderr = stderr  # type: ignore[attr-defined]
        process._playsbc_step_dir = step_dir  # type: ignore[attr-defined]
        return process

    def close_process_files(self, process: subprocess.Popen[str]) -> None:
        for attr in ("_playsbc_stdout", "_playsbc_stderr"):
            handle = getattr(process, attr, None)
            if handle:
                handle.close()

    def collect_sipp_traces(self, pod: str, step_dir: Path) -> None:
        trace_command = [
            self.args.kubectl_bin,
            "-n",
            self.args.namespace,
            "exec",
            pod,
            "--",
            "sh",
            "-lc",
            (
                "for f in /tmp/*_messages.log /tmp/*_errors.log /tmp/*_statistics.log "
                "/tmp/*_counts.csv /tmp/*_logs.log; do "
                "[ -e \"$f\" ] && echo \"===== $f =====\" && cat \"$f\"; "
                "done"
            ),
        ]
        result = run_command(trace_command, timeout=20)
        (step_dir / "sipp-traces.log").write_text(result.stdout + result.stderr, encoding="utf-8")

    def collect_k8s_evidence(self, bundle: Path) -> None:
        commands = {
            "kubectl-pods.log": ["get", "pods", "-o", "wide"],
            "kubectl-services.log": ["get", "svc", "-o", "wide"],
            "kubectl-events.log": ["get", "events", "--sort-by=.lastTimestamp"],
            "playsbc.log": ["logs", f"deployment/{self.args.deployment}", f"--tail={self.args.deployment_log_tail}"],
            "rtpengine.log": ["logs", f"deployment/{self.args.rtpengine_deployment}", f"--tail={self.args.deployment_log_tail}"],
        }
        for filename, parts in commands.items():
            result = self.kubectl(*parts, check=False)
            (bundle / filename).write_text(result.stdout + result.stderr, encoding="utf-8")
        self.collect_playsbc_pod_evidence(bundle)

    def collect_playsbc_pod_evidence(self, bundle: Path) -> None:
        result = self.kubectl("get", "pods", "-l", "app.kubernetes.io/name=playsbc", "-o", "json", check=False)
        evidence: list[str] = []
        try:
            pods = json.loads(result.stdout or "{}").get("items", [])
        except json.JSONDecodeError:
            pods = []
        if result.returncode != 0:
            evidence.append(result.stdout + result.stderr)
        for pod in pods:
            name = pod.get("metadata", {}).get("name", "")
            if not name:
                continue
            evidence.append(f"===== describe pod/{name} =====")
            described = self.kubectl("describe", "pod", str(name), check=False)
            evidence.append(described.stdout + described.stderr)
            for previous in (False, True):
                title = f"logs pod/{name}" + (" --previous" if previous else "")
                evidence.append(f"===== {title} =====")
                command = ["logs", f"pod/{name}", f"--tail={self.args.deployment_log_tail}"]
                if previous:
                    command.append("--previous")
                logs = self.kubectl(*command, check=False)
                evidence.append(logs.stdout + logs.stderr)
        (bundle / "playsbc-pod-evidence.log").write_text("\n".join(evidence), encoding="utf-8")

    def write_log(self, bundle: Path, filename: str, title: str, body: str = "") -> None:
        bundle.mkdir(parents=True, exist_ok=True)
        path = bundle / filename
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} | {title}\n")
            if body:
                handle.write(body.rstrip() + "\n")

    def base_sipp_args(self, pod_ip: str, local_port: int) -> list[str]:
        return [
            "-i",
            pod_ip,
            "-mi",
            pod_ip,
            "-p",
            str(local_port),
            "-trace_msg",
            "-trace_err",
            "-trace_stat",
            "-trace_counts",
            "-trace_logs",
            "-nostdin",
            "-timeout",
            str(self.args.sipp_timeout),
            "-timeout_error",
        ]

    def target(self) -> str:
        return f"{self.args.service}:{self.args.sip_port}"

    def write_text_to_pod(self, pod: str, path: str, text: str) -> None:
        command = [
            self.args.kubectl_bin,
            "-n",
            self.args.namespace,
            "exec",
            "-i",
            pod,
            "--",
            "sh",
            "-lc",
            f"cat > {shlex.quote(path)}",
        ]
        run_command(command, timeout=20, input_text=text, check=True)

    def prepare_profile_scenarios(self, profile: SimpleNamespace, uac_pod: str, uas_pod: str) -> tuple[str, str, str]:
        uac_path = f"/tmp/{short_name(profile.profile)}-uac.xml"
        uas_path = f"/tmp/{short_name(profile.profile)}-uas.xml"
        register_path = f"/tmp/{short_name(profile.profile)}-register.xml"
        self.write_text_to_pod(uac_pod, uac_path, rendered_scenario(profile, "uac"))
        self.write_text_to_pod(uas_pod, uas_path, rendered_scenario(profile, "uas"))
        self.write_text_to_pod(uas_pod, register_path, rendered_scenario(profile, "register"))
        self.write_text_to_pod(uac_pod, register_path, rendered_scenario(profile, "register"))
        return uac_path, uas_path, register_path

    def profile_config(self, profile: SimpleNamespace) -> dict[str, object]:
        advertised_ip = "$POD_IP" if profile_uses_rtpengine(profile) else self.args.service
        return {
            "sip_ip": "0.0.0.0",
            "sip_advertised_ip": advertised_ip,
            "b2bua_advertised_ip": advertised_ip,
            "sip_port": self.args.sip_port,
            "tls_port": self.args.tls_port,
            "sip_transport": getattr(profile, "sip_transport", "udp"),
            "rtp_min": getattr(profile, "server_rtp_min", 30000),
            "rtp_max": getattr(profile, "server_rtp_max", 30100),
            "default_codec": getattr(profile, "server_codec", "PCMU"),
            "auth_realm": "playsbc",
            "users": getattr(profile, "users", {}),
            "bridge_rooms": ["bridge"],
            "b2bua_routes": b2bua_routes_for(profile),
            "route_policies": route_policies_for(profile),
            "trunk_groups": format_config_value(getattr(profile, "trunk_groups", []), profile),
            "hunt_groups": format_config_value(getattr(profile, "hunt_groups", []), profile),
            "number_normalization": getattr(profile, "number_normalization", []),
            "header_normalization": getattr(profile, "header_normalization", {}),
            "transport_policies": getattr(profile, "transport_policies", []),
            "call_admission": getattr(profile, "call_admission", {}),
            "b2bua_ladder_logs": getattr(profile, "ladder_enabled", True),
            "media_backend": getattr(profile, "media_backend", "internal"),
            "rtpengine_url": getattr(profile, "rtpengine_url", f"udp://{self.args.rtpengine_service}:2223"),
            "rtpengine_timeout": getattr(profile, "rtpengine_timeout", 3.0),
            "rtpengine_directions": getattr(profile, "rtpengine_directions", []),
            "rtpengine_interfaces": getattr(profile, "rtpengine_interfaces", []),
            "rtpengine_max_sessions": getattr(profile, "rtpengine_max_sessions", -1),
            "rtpengine_offer_transport_protocol": getattr(profile, "rtpengine_offer_transport_protocol", ""),
            "rtpengine_answer_transport_protocol": getattr(profile, "rtpengine_answer_transport_protocol", ""),
            "rtpengine_sdes": getattr(profile, "rtpengine_sdes", []),
            "rtpengine_dtls": getattr(profile, "rtpengine_dtls", ""),
            "media_quality": getattr(profile, "media_quality", {}),
            "ai_voice_gateway": getattr(profile, "ai_voice_gateway", {}),
            "ha": format_config_value(getattr(profile, "ha", {}), profile),
            "reject_unknown_routes": getattr(profile, "reject_unknown_routes", False),
            "debug": True,
        }

    def apply_profile_config(self, profile: SimpleNamespace, bundle: Path, phases: PhaseLog) -> None:
        started = time.monotonic()
        self.capture_original_values()
        values = copy.deepcopy(self.original_values or {})
        advertised_ip = "$POD_IP" if profile_uses_rtpengine(profile) else self.args.service
        values.setdefault("playsbc", {})["config"] = self.profile_config(profile)
        values.setdefault("rtpengine", {})["enabled"] = bool(
            self.args.rtpengine_enabled
            and getattr(profile, "media_backend", "internal") == "rtpengine"
            and profile.profile != "rtpengine-control-failure"
        )
        if profile_uses_tls(profile):
            self.ensure_tls_secret(bundle)
            values.setdefault("tls", {})["enabled"] = True
            values.setdefault("tls", {})["existingSecret"] = self.args.tls_secret_name
        elif not (self.original_values or {}).get("tls", {}).get("enabled", False):
            values.setdefault("tls", {})["enabled"] = False
            values.setdefault("tls", {})["existingSecret"] = ""
        values_path = bundle / "helm-profile-values.yaml"
        values_path.write_text(dump_simple_yaml(values), encoding="utf-8")
        helm_started = time.monotonic()
        result = run_command(
            [
                self.args.helm_bin,
                "upgrade",
                self.args.helm_release,
                self.args.chart,
                "--namespace",
                self.args.namespace,
                "-f",
                str(values_path),
            ],
            timeout=self.args.helm_timeout,
            check=True,
        )
        helm_seconds = time.monotonic() - helm_started
        self.write_log(bundle, "log.platform", "HELM PROFILE UPGRADE", result.stdout + result.stderr)
        restart = self.kubectl("rollout", "restart", f"deployment/{self.args.deployment}", check=True)
        self.write_log(bundle, "log.platform", "PLAYSBC ROLLOUT RESTART", restart.stdout + restart.stderr)
        rollout_started = time.monotonic()
        rollout = self.kubectl("rollout", "status", f"deployment/{self.args.deployment}", f"--timeout={self.args.rollout_timeout}s", check=True)
        rollout_seconds = time.monotonic() - rollout_started
        self.write_log(bundle, "log.platform", "PLAYSBC ROLLOUT READY", rollout.stdout + rollout.stderr)
        phases.append(
            "Configuration",
            "passed",
            started,
            (
                f"Rendered and applied Helm config for profile={profile.profile}; "
                f"media_backend={getattr(profile, 'media_backend', 'internal')}; "
                f"advertised_ip={advertised_ip}; tls_secret={self.args.tls_secret_name if profile_uses_tls(profile) else 'not-required'}; "
                f"helm_upgrade_seconds={helm_seconds:.3f}; rollout_seconds={rollout_seconds:.3f}; "
                f"core_realm=pod-label:core peer_realm=pod-label:peer."
            ),
        )

    def b2bua_base_args(self, profile: SimpleNamespace, pod_ip: str, local_port: int) -> list[str]:
        calls = int(getattr(profile, "calls", 1))
        rate = int(getattr(profile, "rate", 1))
        hold_ms = int(getattr(profile, "hold_ms", self.args.call_hold_ms))
        return [
            "-i",
            pod_ip,
            "-mi",
            pod_ip,
            "-p",
            str(local_port),
            "-m",
            str(calls),
            "-l",
            str(call_limit(calls, rate, hold_ms)),
            "-timeout",
            str(sipp_timeout_seconds(calls, rate, hold_ms)),
            "-timeout_error",
            "-nostdin",
            "-min_rtp_port",
            "6000",
            "-max_rtp_port",
            "6998",
            *trace_args(),
        ]

    def b2bua_uas_args(self, profile: SimpleNamespace, scenario: str, peer_ip: str) -> list[str]:
        return [
            "-sf",
            scenario,
            "-s",
            str(getattr(profile, "callee", self.args.callee)),
            *self.b2bua_base_args(profile, peer_ip, 5060),
            *transport_args(getattr(profile, "uas_transport", "udp"), "server"),
        ]

    def b2bua_register_args(self, profile: SimpleNamespace, scenario: str, user: str, pod_ip: str, realm: str) -> list[str]:
        transport_name = getattr(profile, "uas_transport", "udp") if realm == "peer" else getattr(profile, "uac_transport", "udp")
        remote_port = self.args.tls_port if transport_name == "tls" else self.args.sip_port
        return [
            f"{self.args.service}:{remote_port}",
            "-sf",
            scenario,
            "-s",
            user,
            "-key",
            "contact_port",
            "5060",
            "-m",
            "1",
            "-r",
            "1",
            "-i",
            pod_ip,
            "-mi",
            pod_ip,
            "-p",
            "5070",
            "-timeout",
            "15",
            "-timeout_error",
            "-nostdin",
            *trace_args(),
            *transport_args(transport_name, "client"),
        ]

    def b2bua_uac_args(self, profile: SimpleNamespace, scenario: str, core_ip: str) -> list[str]:
        transport_name = getattr(profile, "uac_transport", "udp")
        remote_port = self.args.tls_port if transport_name == "tls" else self.args.sip_port
        calls = int(getattr(profile, "calls", 1))
        rate = int(getattr(profile, "rate", 1))
        hold_ms = int(getattr(profile, "hold_ms", self.args.call_hold_ms))
        return [
            f"{self.args.service}:{remote_port}",
            "-sf",
            scenario,
            "-s",
            str(getattr(profile, "callee", self.args.callee)),
            "-key",
            "caller",
            str(getattr(profile, "caller", self.args.caller)),
            "-r",
            str(rate),
            "-d",
            str(hold_ms),
            *self.b2bua_base_args(profile, core_ip, 5060),
            *transport_args(transport_name, "client"),
        ]

    def run_profile(self, profile: str, output_root: Path) -> ReportRow:
        bundle = output_root / f"{self.run_id}-{profile}"
        bundle.mkdir(parents=True, exist_ok=True)
        phases = PhaseLog()
        command_lines: list[str] = []
        returncodes: list[int] = []
        started_profile = time.monotonic()
        status = "failed"
        detail = ""
        sip_ladder = ""

        try:
            self.prepare_common(bundle, phases)
            self.build_and_load_sipp_image(bundle, phases)
            if profile == "options":
                returncodes, command_lines, sip_ladder = self.profile_options(bundle, phases)
            elif profile == "register-contact":
                returncodes, command_lines, sip_ladder = self.profile_register_contact(bundle, phases)
            elif profile == "b2bua-signalling":
                returncodes, command_lines, sip_ladder = self.profile_b2bua_signalling(bundle, phases)
            elif profile in ALL_PROFILES:
                returncodes, command_lines, sip_ladder = self.profile_b2bua_catalog(profile, bundle, phases)
            else:
                raise ValueError(f"Unsupported Kubernetes regression profile: {profile}")
            status = status_from_codes(returncodes)
            detail = f"Executed profile={profile}; step_returncodes={','.join(str(code) for code in returncodes)}."
        except Exception as exc:
            status = "failed"
            detail = f"{type(exc).__name__}: {exc}"
            self.write_log(bundle, "log.platform", "K8S REGRESSION FAILED", detail)
        finally:
            teardown_started = time.monotonic()
            if not self.args.keep_pods:
                self.delete_run_pods(bundle)
            phases.append(
                "Test Teardown",
                "passed" if not self.args.keep_pods else "skipped",
                teardown_started,
                "Deleted temporary SIPp regression pods." if not self.args.keep_pods else "Kept temporary SIPp pods for debugging.",
            )
            evidence_started = time.monotonic()
            self.collect_k8s_evidence(bundle)
            phases.append(
                "Evidence Validation",
                "passed" if status == "passed" else "failed",
                evidence_started,
                detail or f"Collected Kubernetes logs and evidence for profile={profile}.",
            )

        returncode = 0 if status == "passed" else next((code for code in returncodes if code != 0), 1)
        return ReportRow(
            suite=f"Kubernetes {profile}",
            name=profile,
            status=status,
            returncode=returncode,
            duration_seconds=time.monotonic() - started_profile,
            log_path=str(bundle),
            command=" && ".join(command_lines) if command_lines else f"tools/run_k8s_regression.py --profile {profile}",
            phases=phases.phases,
            sip_ladder=sip_ladder,
        )

    def profile_b2bua_catalog(self, profile_name: str, bundle: Path, phases: PhaseLog) -> tuple[list[int], list[str], str]:
        setup_started = time.monotonic()
        profile = profile_values(profile_name, self.run_id)
        stem = short_name(f"{self.run_id}-{profile_name}", limit=48)
        core_pod = f"{stem}-core"
        peer_pod = f"{stem}-peer"
        tls_secret = self.args.tls_secret_name if profile_uses_tls(profile) else ""
        if tls_secret:
            self.ensure_tls_secret(bundle)
        core_ip = self.create_agent(core_pod, bundle, realm="core", tls_secret=tls_secret)
        peer_ip = self.create_agent(peer_pod, bundle, realm="peer", tls_secret=tls_secret)
        profile.host = peer_ip
        profile.server_host = self.args.service
        profile.server_port = self.args.sip_port
        profile.uac_port = 5060
        profile.uas_port = 5060
        profile.register_port = 5070
        profile.caller_register_port = 5070
        if getattr(profile, "rtpengine_url", "") == "udp://playsbc-playsbc-rtpengine:2223":
            profile.rtpengine_url = f"udp://{self.args.rtpengine_service}:2223"
        phases.append(
            "Test Setup",
            "passed",
            setup_started,
            (
                f"Started Kubernetes dual-realm SIPp pods: core={core_pod} ip={core_ip}, "
                f"peer={peer_pod} ip={peer_ip}. The realms are logical Kubernetes pods/labels, "
                "not Multus-backed secondary subnets."
            ),
        )

        self.apply_profile_config(profile, bundle, phases)
        uac_scenario, uas_scenario, register_scenario = self.prepare_profile_scenarios(profile, core_pod, peer_pod)

        execution_started = time.monotonic()
        returncodes: list[int] = []
        commands: list[str] = []
        processes: list[tuple[str, str, subprocess.Popen[str]]] = []

        try:
            if getattr(profile, "start_uas", True):
                uas_args = self.b2bua_uas_args(profile, uas_scenario, peer_ip)
                uas_process = self.start_sipp_process(peer_pod, "peer-sipp-b-uas", uas_args, bundle)
                processes.append(("peer-sipp-b-uas", peer_pod, uas_process))
                commands.append(command_text(self.sipp_exec_command(peer_pod, uas_args)))
                time.sleep(self.args.uas_start_delay)

            if getattr(profile, "register_callee", True):
                register_args = self.b2bua_register_args(
                    profile,
                    register_scenario,
                    str(getattr(profile, "callee", self.args.callee)),
                    peer_ip,
                    "peer",
                )
                result = self.run_sipp_step(peer_pod, "peer-registration-callee", register_args, bundle, timeout=30)
                returncodes.append(result.returncode)
                commands.append(command_text(result.command))

            if getattr(profile, "register_caller", False):
                register_args = self.b2bua_register_args(
                    profile,
                    register_scenario,
                    str(getattr(profile, "caller", self.args.caller)),
                    core_ip,
                    "core",
                )
                result = self.run_sipp_step(core_pod, "core-registration-caller", register_args, bundle, timeout=30)
                returncodes.append(result.returncode)
                commands.append(command_text(result.command))

            if getattr(profile, "run_call", True):
                uac_args = self.b2bua_uac_args(profile, uac_scenario, core_ip)
                timeout = max(self.args.sipp_timeout, sipp_timeout_seconds(int(getattr(profile, "calls", 1)), int(getattr(profile, "rate", 1)), int(getattr(profile, "hold_ms", self.args.call_hold_ms))) + 30)
                result = self.run_sipp_step(core_pod, "core-sipp-a-uac", uac_args, bundle, timeout=timeout)
                returncodes.append(result.returncode)
                commands.append(command_text(result.command))

            for step_name, pod, process in processes:
                try:
                    rc = process.wait(timeout=max(30, self.args.sipp_timeout + 30))
                except subprocess.TimeoutExpired:
                    process.terminate()
                    rc = 124
                finally:
                    self.close_process_files(process)
                returncodes.append(int(rc))
                self.write_log(bundle, "log.sipp", f"{step_name.upper()} RESULT", f"returncode={rc}")
                self.collect_sipp_traces(pod, bundle / step_name)
        finally:
            for _step_name, _pod, process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                self.close_process_files(process)

        phases.append(
            "Test Execution",
            status_from_codes(returncodes),
            execution_started,
            (
                f"Ran canonical profile={profile_name} on Kubernetes logical dual-realm topology; "
                f"description={PROFILE_DESCRIPTIONS.get(profile_name, 'special real-topology profile')}"
            ),
        )
        ladder = "" if is_load_profile(profile) else self.dual_realm_ladder(profile)
        return returncodes or [0], commands, ladder

    def dual_realm_ladder(self, profile: SimpleNamespace) -> str:
        sections: list[str] = []
        if getattr(profile, "register_callee", True):
            auth_outcome = str(getattr(profile, "registration_auth_expected", "") or "")
            sections.append(registration_ladder_text("Peer SIPp B", str(getattr(profile, "callee", self.args.callee)), auth_outcome))
        if getattr(profile, "register_caller", False):
            sections.append(registration_ladder_text("Core SIPp A", str(getattr(profile, "caller", self.args.caller))))
        if not getattr(profile, "run_call", True):
            sections.append(self.setup_only_ladder(profile))
        elif "ai-rasa" in str(getattr(profile, "profile", "")):
            sections.append(self.ai_gateway_ladder(profile))
        else:
            sections.append(self.call_ladder(profile))
        return "\n\n".join(section for section in sections if section)

    def route_for_ladder(self, profile: SimpleNamespace) -> RouteResult:
        callee = str(getattr(profile, "callee", self.args.callee))
        return RouteResult(
            target=SipUri(callee, "peer-sipp-b", 5060, str(getattr(profile, "uas_transport", "udp"))),
            policy_name="k8s-regression",
            source="kubernetes-dual-realm",
            original_user=callee,
            routed_user=callee,
        )

    def setup_only_ladder(self, profile: SimpleNamespace) -> str:
        flow = B2BUAFlowLog(None, "k8s-setup", str(getattr(profile, "callee", self.args.callee)), self.route_for_ladder(profile), enabled=False)
        flow.enabled = True
        flow.sip("Core SIPp A", "PlaySBC", "OPTIONS" if "options" in profile.profile else "profile check")
        if "options" in profile.profile:
            flow.sip("PlaySBC", "Core SIPp A", "200 OK")
        else:
            flow.sip("PlaySBC", "Core SIPp A", "expected response")
        return "KUBERNETES DUAL-REALM CONTROL LADDER\n" + "\n".join(flow.render_ladder_text().splitlines()[1:])

    def ai_gateway_ladder(self, profile: SimpleNamespace) -> str:
        flow = B2BUAFlowLog(None, "k8s-ai", str(getattr(profile, "callee", "ai-bot")), self.route_for_ladder(profile), enabled=False, participants=("Core SIPp A", "PlaySBC", "RTPengine"))
        flow.enabled = True
        flow.sip("Core SIPp A", "PlaySBC", "INVITE")
        flow.sip("PlaySBC", "Core SIPp A", "100 Trying")
        flow.sip("PlaySBC", "Core SIPp A", "180 Ringing")
        if profile_uses_rtpengine(profile):
            flow.sip("PlaySBC", "RTPengine", "OFFER")
            flow.sip("RTPengine", "PlaySBC", "ok OFFER")
            flow.sip("PlaySBC", "RTPengine", "ANSWER")
            flow.sip("RTPengine", "PlaySBC", "ok ANSWER")
        flow.sip("PlaySBC", "Core SIPp A", "200 OK")
        flow.sip("Core SIPp A", "PlaySBC", "ACK")
        flow.sip("Core SIPp A", "PlaySBC", "BYE")
        flow.sip("PlaySBC", "Core SIPp A", "200 OK")
        sections = ["AI VOICE SIP/RTPENGINE LADDER\n" + "\n".join(flow.render_ladder_text().splitlines()[1:])]

        for title, peer, request, response in (
            ("AI VOICE STT ADAPTER LADDER", "STT Adapter", "scripted STT", "intent text"),
            ("AI VOICE RASA ADAPTER LADDER", "Rasa Bot", "REST turn", "bot response"),
            ("AI VOICE TTS ADAPTER LADDER", "TTS Adapter", "scripted TTS", "speech frame"),
        ):
            adapter_flow = B2BUAFlowLog(
                None,
                f"k8s-ai-{peer}",
                str(getattr(profile, "callee", "ai-bot")),
                self.route_for_ladder(profile),
                enabled=False,
                participants=("PlaySBC", peer),
            )
            adapter_flow.enabled = True
            adapter_flow.sip("PlaySBC", peer, request)
            adapter_flow.sip(peer, "PlaySBC", response)
            sections.append(title + "\n" + "\n".join(adapter_flow.render_ladder_text().splitlines()[1:]))
        return "\n\n".join(sections)

    def call_ladder(self, profile: SimpleNamespace) -> str:
        flow = B2BUAFlowLog(None, "k8s-call", str(getattr(profile, "callee", self.args.callee)), self.route_for_ladder(profile), enabled=False, participants=("Core SIPp A", "PlaySBC", "Peer SIPp B"))
        flow.enabled = True
        expect_failure = str(getattr(profile, "uac_scenario", "")).endswith("expect_488.xml") or profile.profile in {
            "unknown-route",
            "esbc-call-admission",
            "rtpengine-control-failure",
            "rtpengine-port-exhaustion",
            "rtpengine-interface-failure",
        }
        cancel_flow = "cancel" in profile.profile
        outbound_failure = "failed-outbound" in profile.profile or "trunk-failure" in profile.profile
        flow.sip("Core SIPp A", "PlaySBC", "INVITE")
        flow.sip("PlaySBC", "Core SIPp A", "100 Trying")
        if expect_failure:
            flow.sip("PlaySBC", "Core SIPp A", "final rejection")
            flow.sip("Core SIPp A", "PlaySBC", "ACK")
            sections = ["KUBERNETES DUAL-REALM SIP LADDER\n" + "\n".join(flow.render_ladder_text().splitlines()[1:])]
            if profile_uses_rtpengine(profile):
                sections.append(self.rtpengine_control_ladder(profile, failed=True))
            return "\n\n".join(sections)
        flow.sip("PlaySBC", "Peer SIPp B", "INVITE")
        flow.sip("Peer SIPp B", "PlaySBC", "100 Trying")
        flow.sip("Peer SIPp B", "PlaySBC", "180 Ringing")
        flow.sip("PlaySBC", "Core SIPp A", "180 Ringing")
        if cancel_flow:
            flow.sip("Core SIPp A", "PlaySBC", "CANCEL")
            flow.sip("PlaySBC", "Core SIPp A", "200 OK")
            flow.sip("PlaySBC", "Peer SIPp B", "CANCEL")
            flow.sip("Peer SIPp B", "PlaySBC", "200 OK")
            flow.sip("Peer SIPp B", "PlaySBC", "487 Request Terminated")
            flow.sip("PlaySBC", "Core SIPp A", "487 Request Terminated")
            flow.sip("Core SIPp A", "PlaySBC", "ACK")
            return "KUBERNETES DUAL-REALM CANCEL LADDER\n" + "\n".join(flow.render_ladder_text().splitlines()[1:])
        final_label = "503 Service Unavailable" if outbound_failure else "200 OK"
        flow.sip("Peer SIPp B", "PlaySBC", final_label)
        if outbound_failure:
            flow.sip("PlaySBC", "Core SIPp A", final_label)
            flow.sip("Core SIPp A", "PlaySBC", "ACK")
            return "KUBERNETES DUAL-REALM FAILURE LADDER\n" + "\n".join(flow.render_ladder_text().splitlines()[1:])
        flow.sip("PlaySBC", "Core SIPp A", "200 OK")
        flow.sip("Core SIPp A", "PlaySBC", "ACK")
        flow.sip("PlaySBC", "Peer SIPp B", "ACK")
        flow.sip("Core SIPp A", "PlaySBC", "BYE")
        flow.sip("PlaySBC", "Core SIPp A", "200 OK")
        flow.sip("PlaySBC", "Peer SIPp B", "BYE")
        flow.sip("Peer SIPp B", "PlaySBC", "200 OK")
        sections = ["KUBERNETES DUAL-REALM SIP LADDER\n" + "\n".join(flow.render_ladder_text().splitlines()[1:])]
        if profile_uses_rtpengine(profile):
            sections.append(self.rtpengine_control_ladder(profile))
        return "\n\n".join(sections)

    def rtpengine_control_ladder(self, profile: SimpleNamespace, failed: bool = False) -> str:
        flow = B2BUAFlowLog(
            None,
            "k8s-rtpengine",
            str(getattr(profile, "callee", self.args.callee)),
            self.route_for_ladder(profile),
            enabled=False,
            participants=("PlaySBC", "RTPengine"),
        )
        flow.enabled = True
        flow.sip("PlaySBC", "RTPengine", "OFFER")
        flow.sip("RTPengine", "PlaySBC", "failed OFFER" if failed else "ok OFFER")
        if not failed:
            flow.sip("PlaySBC", "RTPengine", "ANSWER")
            flow.sip("RTPengine", "PlaySBC", "ok ANSWER")
        return "KUBERNETES RTPENGINE CONTROL LADDER\n" + "\n".join(flow.render_ladder_text().splitlines()[1:])

    def profile_options(self, bundle: Path, phases: PhaseLog) -> tuple[list[int], list[str], str]:
        setup_started = time.monotonic()
        pod = f"{self.run_id}-options"
        pod_ip = self.create_agent(pod, bundle)
        phases.append("Test Setup", "passed", setup_started, f"Started SIPp agent pod={pod} ip={pod_ip}.")

        execution_started = time.monotonic()
        sipp_args = [
            self.target(),
            "-sf",
            "/scenarios/options.xml",
            "-s",
            self.args.options_user,
            "-m",
            "1",
            "-r",
            "1",
            *self.base_sipp_args(pod_ip, 5060),
        ]
        result = self.run_sipp_step(pod, "options", sipp_args, bundle)
        phases.append("Test Execution", "passed" if result.returncode == 0 else "failed", execution_started, "Sent OPTIONS and expected 200 OK.")
        ladder = (
            "SIP LADDER\n"
            "SIPp Agent              PlaySBC\n"
            "    |                      |\n"
            "01  | OPTIONS              |\n"
            "    |--------------------->|\n"
            "02  | 200 OK               |\n"
            "    |<---------------------|\n"
        )
        return [result.returncode], [command_text(result.command)], ladder

    def profile_register_contact(self, bundle: Path, phases: PhaseLog) -> tuple[list[int], list[str], str]:
        setup_started = time.monotonic()
        pod = f"{self.run_id}-register"
        pod_ip = self.create_agent(pod, bundle)
        phases.append("Test Setup", "passed", setup_started, f"Started SIPp registrar pod={pod} ip={pod_ip}.")

        execution_started = time.monotonic()
        sipp_args = [
            self.target(),
            "-sf",
            "/scenarios/register_contact.xml",
            "-s",
            self.args.register_user,
            "-key",
            "contact_port",
            "5060",
            "-m",
            "1",
            "-r",
            "1",
            *self.base_sipp_args(pod_ip, 5070),
        ]
        result = self.run_sipp_step(pod, "register-contact", sipp_args, bundle)
        phases.append("Test Execution", "passed" if result.returncode == 0 else "failed", execution_started, "Sent REGISTER and expected 200 OK.")
        ladder = (
            "REGISTRATION LADDER\n"
            "SIPp Agent              PlaySBC\n"
            "    |                      |\n"
            "01  | REGISTER             |\n"
            "    |--------------------->|\n"
            "02  | 200 OK               |\n"
            "    |<---------------------|\n"
        )
        return [result.returncode], [command_text(result.command)], ladder

    def profile_b2bua_signalling(self, bundle: Path, phases: PhaseLog) -> tuple[list[int], list[str], str]:
        setup_started = time.monotonic()
        uac_pod = f"{self.run_id}-uac"
        uas_pod = f"{self.run_id}-uas"
        uac_ip = self.create_agent(uac_pod, bundle)
        uas_ip = self.create_agent(uas_pod, bundle)
        phases.append("Test Setup", "passed", setup_started, f"Started UAC pod={uac_pod} ip={uac_ip}; UAS pod={uas_pod} ip={uas_ip}.")

        execution_started = time.monotonic()
        returncodes: list[int] = []
        commands: list[str] = []

        uas_args = [
            "-sf",
            "/scenarios/b2bua_uas_b.xml",
            "-s",
            self.args.callee,
            "-m",
            "1",
            *self.base_sipp_args(uas_ip, 5060),
        ]
        uas_process = self.start_sipp_process(uas_pod, "sipp-b-uas", uas_args, bundle)
        commands.append(command_text(self.sipp_exec_command(uas_pod, uas_args)))
        time.sleep(self.args.uas_start_delay)

        register_args = [
            self.target(),
            "-sf",
            "/scenarios/register_contact.xml",
            "-s",
            self.args.callee,
            "-key",
            "contact_port",
            "5060",
            "-m",
            "1",
            "-r",
            "1",
            *self.base_sipp_args(uas_ip, 5070),
        ]
        register_result = self.run_sipp_step(uas_pod, "registration-callee", register_args, bundle)
        returncodes.append(register_result.returncode)
        commands.append(command_text(register_result.command))

        uac_args = [
            self.target(),
            "-sf",
            "/scenarios/b2bua_uac_a.xml",
            "-s",
            self.args.callee,
            "-key",
            "caller",
            self.args.caller,
            "-m",
            "1",
            "-r",
            "1",
            "-d",
            str(self.args.call_hold_ms),
            *self.base_sipp_args(uac_ip, 5060),
        ]
        uac_result = self.run_sipp_step(uac_pod, "sipp-a-uac", uac_args, bundle, timeout=self.args.sipp_timeout + 10)
        returncodes.append(uac_result.returncode)
        commands.append(command_text(uac_result.command))

        try:
            uas_rc = uas_process.wait(timeout=self.args.sipp_timeout + 10)
        except subprocess.TimeoutExpired:
            uas_process.terminate()
            uas_rc = 124
        finally:
            self.close_process_files(uas_process)
        returncodes.append(int(uas_rc))
        self.write_log(bundle, "log.sipp", "SIPP-B-UAS RESULT", f"returncode={uas_rc}")
        self.collect_sipp_traces(uas_pod, bundle / "sipp-b-uas")
        phases.append(
            "Test Execution",
            "passed" if all(code == 0 for code in returncodes) else "failed",
            execution_started,
            "Registered SIPp B, then placed a B2BUA call from SIPp A to the registered callee.",
        )
        ladder = (
            "SIP LADDER\n"
            "SIPp A                  PlaySBC                 SIPp B\n"
            "  |                        |                       |\n"
            "  |                        | REGISTER              |\n"
            "  |                        |<----------------------|\n"
            "  |                        | 200 OK                 |\n"
            "  |                        |---------------------->|\n"
            "  | INVITE                 |                       |\n"
            "  |----------------------->|                       |\n"
            "  | 100 Trying             |                       |\n"
            "  |<-----------------------|                       |\n"
            "  |                        | INVITE                |\n"
            "  |                        |---------------------->|\n"
            "  |                        | 100 Trying            |\n"
            "  |                        |<----------------------|\n"
            "  |                        | 180 Ringing           |\n"
            "  |                        |<----------------------|\n"
            "  | 180 Ringing            |                       |\n"
            "  |<-----------------------|                       |\n"
            "  |                        | 200 OK                |\n"
            "  |                        |<----------------------|\n"
            "  | 200 OK                 |                       |\n"
            "  |<-----------------------|                       |\n"
            "  | ACK                    |                       |\n"
            "  |----------------------->|                       |\n"
            "  |                        | ACK                   |\n"
            "  |                        |---------------------->|\n"
            "  | BYE                    |                       |\n"
            "  |----------------------->|                       |\n"
            "  | 200 OK                 |                       |\n"
            "  |<-----------------------|                       |\n"
            "  |                        | BYE                   |\n"
            "  |                        |---------------------->|\n"
            "  |                        | 200 OK                |\n"
            "  |                        |<----------------------|\n"
        )
        return returncodes, commands, ladder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="", help="Run/report identifier; defaults to a timestamp")
    parser.add_argument("--namespace", default="playsbc")
    parser.add_argument("--service", default="playsbc-playsbc")
    parser.add_argument("--sip-port", type=int, default=5062)
    parser.add_argument("--tls-port", type=int, default=5061)
    parser.add_argument("--deployment", default="playsbc-playsbc")
    parser.add_argument("--rtpengine-service", default="playsbc-playsbc-rtpengine")
    parser.add_argument("--rtpengine-deployment", default="playsbc-playsbc-rtpengine")
    parser.add_argument("--configmap", default="playsbc-sipp-scenarios")
    parser.add_argument("--sipp-image", default="playsbc-sipp:local")
    parser.add_argument("--image-pull-policy", default="IfNotPresent")
    parser.add_argument("--build-sipp-image", action="store_true", help="Build docker/sipp.Dockerfile before running")
    parser.add_argument("--kind-load-image", action="store_true", help="Load --sipp-image into the kind cluster before running")
    parser.add_argument("--kind-cluster", default="playsbc")
    parser.add_argument("--helm-bin", default="helm")
    parser.add_argument("--helm-release", default="playsbc")
    parser.add_argument("--chart", default=str(ROOT / "charts" / "playsbc"))
    parser.add_argument("--profile", action="append", choices=SELECTABLE_PROFILES)
    parser.add_argument("--all-profiles", action="store_true", help="Run the canonical 47 B2BUA profiles on Kubernetes")
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--rtpengine-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-root", default=str(ROOT / "logs" / "k8s-Regression"))
    parser.add_argument("--report-dir", default=str(ROOT / "logs" / "k8s-reports"))
    parser.add_argument("--kubectl-bin", default="kubectl")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--helm-timeout", type=int, default=180)
    parser.add_argument("--rollout-timeout", type=int, default=120)
    parser.add_argument("--sipp-timeout", type=int, default=60)
    parser.add_argument("--pod-ready-timeout", type=int, default=60)
    parser.add_argument("--image-build-timeout", type=int, default=900)
    parser.add_argument("--deployment-log-tail", type=int, default=200)
    parser.add_argument("--tls-secret-name", default=LAB_TLS_SECRET_NAME)
    parser.add_argument("--no-restore-helm-values", action="store_true", help="Leave Helm on the last rendered profile instead of restoring pre-run values")
    parser.add_argument("--skip-namespace-check", action="store_true", help="Skip cluster-scoped namespace lookup, useful for in-cluster Job RBAC")
    parser.add_argument("--options-user", default="health")
    parser.add_argument("--register-user", default="1001")
    parser.add_argument("--caller", default="1001")
    parser.add_argument("--callee", default="1002")
    parser.add_argument("--call-hold-ms", type=int, default=1000)
    parser.add_argument("--uas-start-delay", type=float, default=1.0)
    parser.add_argument("--keep-pods", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_binary(args.kubectl_bin)
    ensure_binary(args.helm_bin)
    if args.list_profiles:
        print("Available Kubernetes B2BUA profiles:")
        for profile in ALL_PROFILES:
            print(f"  {profile}: {PROFILE_DESCRIPTIONS.get(profile, 'Real dual-realm RTPengine transcoding topology profile.')}")
        print("\nSmoke aliases:")
        for profile in SMOKE_PROFILES:
            print(f"  {profile}")
        return 0
    profiles = ALL_PROFILES if args.all_profiles else tuple(args.profile or DEFAULT_PROFILES)
    run_id = args.run_id or make_run_id()
    output_root = Path(args.output_root)
    report_dir = Path(args.report_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    runner = K8sRegressionRunner(args, run_id)
    rows: list[ReportRow] = []
    restore_error = ""
    try:
        runner.capture_original_values()
        rows = [runner.run_profile(profile, output_root) for profile in profiles]
    finally:
        restore_error = runner.restore_original_values(report_dir) or ""
    cleanup_old_reports(report_dir, run_id)
    report_path = write_reports(rows, report_dir, run_id)
    print(f"Kubernetes regression report: {report_path}")
    print(f"Latest report: {report_dir / 'latest.html'}")
    for row in rows:
        print(f"{row.suite} / {row.name}: {row.status}")
    if restore_error:
        print(restore_error, file=sys.stderr)
        return 1
    return 1 if any(row.status != "passed" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
