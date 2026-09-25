#!/usr/bin/env python3
"""Launch the full PlaySBC Kubernetes regression from one in-cluster Job pod."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))
from tools.run_k8s_regression import (  # noqa: E402
    ALL_PROFILES,
    AKS_PROFILES,
    RASA_PROFILES,
    SELECTABLE_PROFILES,
    make_aks_run_id,
    make_rasa_run_id,
    make_run_id,
)


DEFAULT_OUTPUT_DIR = str(ROOT / "logs" / "k8s-job")
RASA_OUTPUT_DIR = str(ROOT / "logs" / "RASA-Regression")
DEFAULT_REMOTE_OUTPUT_ROOT = "k8s-Regression"
DEFAULT_REMOTE_REPORT_DIR = "k8s-reports"
RASA_REMOTE_OUTPUT_ROOT = "RASA-Regression"
RASA_REMOTE_REPORT_DIR = "RASA-reports"
AKS_OUTPUT_DIR = str(ROOT / "logs" / "AKS-Regression")
AKS_REMOTE_OUTPUT_ROOT = "AKS-Regression"
AKS_REMOTE_REPORT_DIR = "AKS-reports"
DEFAULT_ROLLOUT_TIMEOUT = 120
RASA_ROLLOUT_TIMEOUT = 600


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    duration_seconds: float
    stdout: str
    stderr: str


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


def start_host_sleep_inhibitor() -> Optional[subprocess.Popen[bytes]]:
    """Keep a macOS-hosted kind/minikube run alive while its Job is active."""
    if sys.platform != "darwin" or not shutil.which("caffeinate"):
        return None
    return subprocess.Popen(
        ["caffeinate", "-dimsu", "-w", str(os.getpid())],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_host_sleep_inhibitor(process: Optional[subprocess.Popen[bytes]]) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def labels(run_id: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": "playsbc-k8s-regression-runner",
        "app.kubernetes.io/part-of": "playsbc",
        "playsbc-regression-run": run_id,
    }


def runner_pod_labels(run_id: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": "playsbc-k8s-regression-runner",
        "app.kubernetes.io/part-of": "playsbc",
        "playsbc-regression-controller-run": run_id,
    }


def service_account_manifest(args: argparse.Namespace) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": args.service_account,
            "namespace": args.namespace,
            "labels": labels(args.run_id),
        },
    }


def role_manifest(args: argparse.Namespace) -> dict[str, object]:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {
            "name": args.rbac_name,
            "namespace": args.namespace,
            "labels": labels(args.run_id),
        },
        "rules": [
            {
                "apiGroups": [""],
                "resources": [
                    "configmaps",
                    "events",
                    "pods",
                    "pods/exec",
                    "pods/log",
                    "persistentvolumeclaims",
                    "secrets",
                    "services",
                ],
                "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"],
            },
            {
                "apiGroups": ["apps"],
                "resources": ["deployments", "replicasets", "statefulsets"],
                "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"],
            },
            {
                "apiGroups": ["batch"],
                "resources": ["jobs"],
                "verbs": ["get", "list", "watch"],
            },
        ],
    }


def role_binding_manifest(args: argparse.Namespace) -> dict[str, object]:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {
            "name": args.rbac_name,
            "namespace": args.namespace,
            "labels": labels(args.run_id),
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": args.service_account,
                "namespace": args.namespace,
            }
        ],
        "roleRef": {
            "kind": "Role",
            "name": args.rbac_name,
            "apiGroup": "rbac.authorization.k8s.io",
        },
    }


def runner_command_args(args: argparse.Namespace) -> list[str]:
    command = [
        "/workspace/tools/run_k8s_regression.py",
        "--run-id",
        args.run_id,
        "--namespace",
        args.namespace,
        "--service",
        args.service,
        "--sip-port",
        str(args.sip_port),
        "--tls-port",
        str(args.tls_port),
        "--deployment",
        args.deployment,
        "--rtpengine-service",
        args.rtpengine_service,
        "--rtpengine-deployment",
        args.rtpengine_deployment,
        "--sipp-image",
        args.sipp_image,
        "--image-pull-policy",
        args.sipp_image_pull_policy,
        "--helm-release",
        args.helm_release,
        "--chart",
        "/workspace/charts/playsbc",
        "--active-active-topology" if args.active_active_topology else "--no-active-active-topology",
        "--playsbc-replicas",
        str(args.playsbc_replicas),
        "--rtpengine-replicas",
        str(args.rtpengine_replicas),
        "--ha-cluster-id",
        args.ha_cluster_id,
        "--ha-shared-state-path",
        args.ha_shared_state_path,
        "--multus-enabled" if args.multus_enabled else "--no-multus-enabled",
        "--timeout",
        str(args.profile_timeout),
        "--helm-timeout",
        str(args.helm_timeout),
        "--rollout-timeout",
        str(args.rollout_timeout),
        "--sipp-timeout",
        str(args.sipp_timeout),
        "--pod-ready-timeout",
        str(args.pod_ready_timeout),
        "--deployment-log-tail",
        str(args.deployment_log_tail),
        "--tls-secret-name",
        args.tls_secret_name,
        "--output-root",
        f"/workspace/logs/{args.remote_output_root_name}",
        "--report-dir",
        f"/workspace/logs/{args.remote_report_dir_name}",
        "--skip-namespace-check",
    ]
    profiles = args.profile or []
    if args.aks_profiles:
        command.append("--aks-profiles")
    elif args.rasa_profiles:
        command.append("--rasa-profiles")
    elif args.all_profiles or not profiles:
        command.append("--all-profiles")
    else:
        for profile in profiles:
            command.extend(["--profile", profile])
    if args.aks_mode:
        command.append("--aks-mode")
    if args.aks_require_azure_services:
        command.append("--aks-require-azure-services")
    if args.aks_require_static_sip:
        command.append("--aks-require-static-sip")
    if args.aks_require_public_sip_ingress:
        command.append("--aks-require-public-sip-ingress")
    if args.aks_require_public_rtp_ingress:
        command.append("--aks-require-public-rtp-ingress")
    if args.aks_require_rtp_port_range:
        command.append("--aks-require-rtp-port-range")
    command.extend(["--aks-rtp-port-min", str(args.aks_rtp_port_min)])
    command.extend(["--aks-rtp-port-max", str(args.aks_rtp_port_max)])
    if args.aks_services_selector != "playsbc.io/cloud=azure":
        command.extend(["--aks-services-selector", args.aks_services_selector])
    if not args.rtpengine_enabled:
        command.append("--no-rtpengine-enabled")
    if args.require_multus:
        command.append("--require-multus")
    if args.keep_sipp_pods:
        command.append("--keep-pods")
    if args.no_restore_helm_values:
        command.append("--no-restore-helm-values")
    return command


def job_manifest(args: argparse.Namespace) -> dict[str, object]:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": args.job_name,
            "namespace": args.namespace,
            "labels": labels(args.run_id),
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": args.active_deadline_seconds,
            "ttlSecondsAfterFinished": args.ttl_seconds_after_finished,
            "template": {
                "metadata": {"labels": runner_pod_labels(args.run_id)},
                "spec": {
                    "serviceAccountName": args.service_account,
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "regression-runner",
                            "image": args.runner_image,
                            "imagePullPolicy": args.runner_image_pull_policy,
                            "workingDir": "/workspace",
                            "command": ["python3"],
                            "args": runner_command_args(args),
                            "env": [
                                {"name": "PYTHONPATH", "value": "/workspace"},
                                {"name": "PYTHONPYCACHEPREFIX", "value": "/tmp/playsbc-pycache"},
                                {
                                    "name": "PLAYSBC_REGRESSION_RUNNER_IP",
                                    "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}},
                                },
                            ],
                            "volumeMounts": [{"name": "regression-logs", "mountPath": "/workspace/logs"}],
                        },
                        {
                            "name": "artifact-holder",
                            "image": args.runner_image,
                            "imagePullPolicy": args.runner_image_pull_policy,
                            "command": ["sh", "-lc"],
                            "args": ["trap 'exit 0' TERM INT; while true; do sleep 30; done"],
                            "volumeMounts": [{"name": "regression-logs", "mountPath": "/workspace/logs"}],
                        }
                    ],
                    "volumes": [{"name": "regression-logs", "emptyDir": {}}],
                },
            },
        },
    }


def apply_manifest(args: argparse.Namespace, manifest: dict[str, object]) -> CommandResult:
    return run_command(
        [args.kubectl_bin, "apply", "-f", "-"],
        timeout=args.kubectl_timeout,
        input_text=json.dumps(manifest),
        check=True,
    )


def build_images(args: argparse.Namespace) -> None:
    if args.build_playsbc_image:
        ensure_binary("docker")
        run_command(
            [
                "docker",
                "build",
                "-f",
                str(ROOT / "docker" / "playsbc.Dockerfile"),
                "-t",
                args.playsbc_image,
                ".",
            ],
            timeout=args.image_build_timeout,
            check=True,
        )
    if args.build_runner_image:
        ensure_binary("docker")
        run_command(
            [
                "docker",
                "build",
                "-f",
                str(ROOT / "docker" / "k8s-regression-runner.Dockerfile"),
                "-t",
                args.runner_image,
                ".",
            ],
            timeout=args.image_build_timeout,
            check=True,
        )
    if args.build_sipp_image:
        ensure_binary("docker")
        run_command(
            ["docker", "build", "-f", str(ROOT / "docker" / "sipp.Dockerfile"), "-t", args.sipp_image, "."],
            timeout=args.image_build_timeout,
            check=True,
        )
    if args.build_rtpengine_image:
        ensure_binary("docker")
        run_command(
            [
                "docker",
                "build",
                "-f",
                str(ROOT / "docker" / "rtpengine.Dockerfile"),
                "-t",
                args.rtpengine_image,
                ".",
            ],
            timeout=args.image_build_timeout,
            check=True,
        )
    if args.kind_load_images:
        ensure_binary("kind")
        images = [args.runner_image]
        if args.load_playsbc_image:
            images.append(args.playsbc_image)
        if args.load_sipp_image:
            images.append(args.sipp_image)
        if args.load_rtpengine_image:
            images.append(args.rtpengine_image)
        run_command(
            ["kind", "load", "docker-image", *images, "--name", args.kind_cluster],
            timeout=args.kubectl_timeout,
            check=True,
        )


def split_image_name(image: str) -> tuple[str, str]:
    if ":" not in image.rsplit("/", 1)[-1]:
        return image, "latest"
    repository, tag = image.rsplit(":", 1)
    return repository, tag


def validate_image_reference(image: str, option: str) -> None:
    value = image.strip()
    if not value or value != image or any(character.isspace() for character in value):
        raise SystemExit(f"Invalid {option} image reference {image!r}: empty or whitespace-containing value")
    if value.startswith((".", "/", ":")) or "://" in value or "$" in value or "{" in value or "}" in value:
        raise SystemExit(
            f"Invalid {option} image reference {image!r}. "
            "Re-export ACR_NAME/ACR_LOGIN_SERVER and PLAYSBC_VERSION before running regression."
        )

    repository, tag = split_image_name(value)
    if not repository or repository.endswith(("/", ".", ":")) or any(
        not component or component in {".", ".."} for component in repository.split("/")
    ):
        raise SystemExit(f"Invalid {option} image repository in {image!r}")
    if not tag or not (tag[0].isalnum() or tag[0] == "_") or any(
        not (character.isalnum() or character in "_.-") for character in tag
    ):
        raise SystemExit(f"Invalid {option} image tag in {image!r}")
    if any(not (character.isalnum() or character in "._-:/") for character in repository):
        raise SystemExit(f"Invalid {option} image repository in {image!r}")


def validate_requested_images(args: argparse.Namespace) -> None:
    requested = [
        (args.runner_image, "--runner-image"),
        (args.sipp_image, "--sipp-image"),
    ]
    if args.set_playsbc_image or args.build_playsbc_image:
        requested.append((args.playsbc_image, "--playsbc-image"))
    if args.set_rtpengine_image or args.build_rtpengine_image:
        requested.append((args.rtpengine_image, "--rtpengine-image"))

    for image, option in requested:
        validate_image_reference(image, option)

    if args.aks_mode and not args.dry_run:
        for image, option in requested:
            if "/" not in split_image_name(image)[0]:
                raise SystemExit(
                    f"AKS requires a registry-qualified {option} value; received {image!r}. "
                    "Use the verified ACR login server or a published GHCR image."
                )


def prepare_playsbc_image_values(args: argparse.Namespace) -> None:
    if not args.set_playsbc_image and not args.set_rtpengine_image:
        return
    command = [
        args.helm_bin,
        "upgrade",
        args.helm_release,
        str(ROOT / "charts" / "playsbc"),
        "--namespace",
        args.namespace,
        "--reuse-values",
    ]
    if args.set_playsbc_image:
        repository, tag = split_image_name(args.playsbc_image)
        command.extend(
            [
                "--set",
                f"image.repository={repository}",
                "--set-string",
                f"image.tag={tag}",
                "--set",
                "image.pullPolicy=IfNotPresent",
            ]
        )
    if args.set_rtpengine_image:
        repository, tag = split_image_name(args.rtpengine_image)
        command.extend(
            [
                "--set",
                f"rtpengine.image.repository={repository}",
                "--set-string",
                f"rtpengine.image.tag={tag}",
                "--set",
                "rtpengine.image.pullPolicy=IfNotPresent",
            ]
        )
    if args.active_active_topology:
        command.extend(
            [
                "--set",
                "topology.activeActive.enabled=true",
                "--set",
                "topology.activeActive.useStatefulSet=true",
                "--set",
                f"topology.activeActive.playsbcReplicas={args.playsbc_replicas}",
                "--set",
                f"topology.activeActive.rtpengineReplicas={args.rtpengine_replicas}",
                "--set",
                f"topology.activeActive.clusterId={args.ha_cluster_id}",
                "--set",
                f"rtpengine.replicas={args.rtpengine_replicas}",
                "--set",
                "rtpengine.hostNetwork=false",
                "--set",
                f"topology.multus.enabled={'true' if args.multus_enabled else 'false'}",
            ]
        )
    else:
        command.extend(
            [
                "--set",
                "topology.activeActive.enabled=false",
                "--set",
                "topology.activeActive.useStatefulSet=false",
                "--set",
                "replicaCount=1",
                "--set",
                "rtpengine.replicas=1",
                "--set",
                "rtpengine.hostNetwork=false",
                "--set",
                f"topology.multus.enabled={'true' if args.multus_enabled else 'false'}",
            ]
        )
    run_command(command, timeout=args.kubectl_timeout, check=True)
    if not args.active_active_topology:
        run_command(
            [
                args.kubectl_bin,
                "-n",
                args.namespace,
                "delete",
                "statefulset",
                args.deployment,
                args.rtpengine_deployment,
                "--ignore-not-found=true",
            ],
            timeout=args.kubectl_timeout,
            check=False,
        )
    workload_ref = f"statefulset/{args.deployment}" if args.active_active_topology else f"deployment/{args.deployment}"
    run_command(
        [
            args.kubectl_bin,
            "-n",
            args.namespace,
            "rollout",
            "status",
            workload_ref,
            f"--timeout={args.rollout_timeout}s",
        ],
        timeout=args.kubectl_timeout,
        check=True,
    )


def job_pod_name(args: argparse.Namespace) -> str:
    pod = job_pod(args)
    if not pod:
        return ""
    return str(pod["metadata"]["name"])


def job_pod(args: argparse.Namespace) -> dict[str, object]:
    result = run_command(
        [
            args.kubectl_bin,
            "-n",
            args.namespace,
            "get",
            "pod",
            "-l",
            f"job-name={args.job_name}",
            "-o",
            "json",
        ],
        timeout=args.kubectl_timeout,
        check=True,
    )
    pod_list = json.loads(result.stdout or "{}")
    items = pod_list.get("items", [])
    if not items:
        return {}
    return items[0]


def collect_job_outputs(args: argparse.Namespace, pod_name: str, logs_text: str) -> Path:
    output_root = Path(args.output_dir) / args.run_id
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "runner.log").write_text(logs_text, encoding="utf-8")
    if not pod_name:
        (output_root / "copy-skipped.log").write_text("Runner pod was not found.\n", encoding="utf-8")
        return output_root
    for remote_name in (args.remote_report_dir_name, args.remote_output_root_name):
        destination = output_root / remote_name
        result = run_command(
            [
                args.kubectl_bin,
                "-n",
                args.namespace,
                "cp",
                f"{pod_name}:/workspace/logs/{remote_name}",
                str(destination),
                "-c",
                "artifact-holder",
            ],
            timeout=args.copy_timeout,
            check=False,
        )
        if result.returncode != 0:
            (output_root / f"{remote_name}-copy-error.log").write_text(
                result.stdout + result.stderr,
                encoding="utf-8",
            )
    return output_root


def create_aks_evidence_archive(args: argparse.Namespace, output_root: Path) -> Optional[tuple[Path, int]]:
    if not args.aks_profiles:
        return None

    archive_path = Path(args.output_dir) / "latest-aks-regression.tgz"
    temp_path = archive_path.with_name(f".{archive_path.name}.tmp")
    manifest_path = output_root / "archive-manifest.txt"
    file_paths = sorted(path for path in output_root.rglob("*") if path.is_file())

    if not file_paths:
        manifest_path.write_text(
            "AKS evidence archive was not created because the collected output folder is empty.\n",
            encoding="utf-8",
        )
        raise RuntimeError(f"AKS evidence archive would be empty: {output_root}")

    manifest_path.write_text(
        "\n".join(
            [
                f"run_id={args.run_id}",
                f"archive={archive_path}",
                f"source={output_root}",
                f"files_before_manifest={len(file_paths)}",
                f"files_in_archive={len(file_paths) + 1}",
                "path_listing=preview",
                f"listed_paths={min(len(file_paths), 200)}",
                f"omitted_paths={max(0, len(file_paths) - 200)}",
                "",
                "Download this .tgz from Cloud Shell. Do not create a manual .tar from an unset RUN variable.",
                "The path list below is capped at 200 entries; files_in_archive is verified from the completed archive.",
                "",
            ]
            + [str(path.relative_to(output_root)) for path in file_paths[:200]]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        temp_path.unlink(missing_ok=True)
        with tarfile.open(temp_path, "w:gz") as archive:
            archive.add(output_root, arcname=output_root.name)
        with tarfile.open(temp_path, "r:gz") as archive:
            member_count = sum(1 for member in archive.getmembers() if member.isfile())
        expected_member_count = len(file_paths) + 1
        if member_count != expected_member_count:
            raise RuntimeError(
                f"AKS evidence archive member mismatch: expected={expected_member_count} "
                f"actual={member_count} archive={temp_path}"
            )
        temp_path.replace(archive_path)
        return archive_path, member_count
    finally:
        temp_path.unlink(missing_ok=True)


def service_name(item: dict[str, object]) -> str:
    metadata = item.get("metadata", {})
    return str(metadata.get("name", "unknown")) if isinstance(metadata, dict) else "unknown"


def service_exposure(item: dict[str, object]) -> str:
    metadata = item.get("metadata", {})
    labels = metadata.get("labels", {}) if isinstance(metadata, dict) else {}
    return str(labels.get("playsbc.io/exposure", "")) if isinstance(labels, dict) else ""


def service_ingress_values(item: dict[str, object]) -> list[str]:
    status = item.get("status", {})
    load_balancer = status.get("loadBalancer", {}) if isinstance(status, dict) else {}
    ingress = load_balancer.get("ingress", []) if isinstance(load_balancer, dict) else []
    values: list[str] = []
    if isinstance(ingress, list):
        for entry in ingress:
            if not isinstance(entry, dict):
                continue
            value = entry.get("ip") or entry.get("hostname")
            if value:
                values.append(str(value))
    return values


def azure_load_balancer_readiness(args: argparse.Namespace) -> tuple[bool, str]:
    result = run_command(
        [args.kubectl_bin, "-n", args.namespace, "get", "svc", "-l", args.aks_services_selector, "-o", "json"],
        timeout=args.kubectl_timeout,
        check=False,
    )
    if result.returncode != 0:
        return False, f"kubectl_get_services_failed={result.stderr.strip() or result.stdout.strip()}"

    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return False, f"invalid_service_json={exc}"

    raw_items = parsed.get("items", []) if isinstance(parsed, dict) else []
    items = [item for item in raw_items if isinstance(item, dict)]
    load_balancers = [
        item
        for item in items
        if isinstance(item.get("spec", {}), dict) and item.get("spec", {}).get("type") == "LoadBalancer"
    ]
    if not load_balancers:
        return False, f"no_loadbalancer_services selector={args.aks_services_selector}"

    required_exposures = {"sip-public", "rtp-public"} if args.aks_require_public_rtp_ingress else {"sip-public"}
    present_exposures = {service_exposure(item) for item in load_balancers}
    missing_exposures = sorted(required_exposures - present_exposures)
    missing = [
        f"{service_name(item)}({service_exposure(item) or 'unlabeled'})"
        for item in load_balancers
        if not service_ingress_values(item)
    ]
    missing.extend(f"missing-{exposure}" for exposure in missing_exposures)
    ready = [
        f"{service_name(item)}({service_exposure(item) or 'unlabeled'})={','.join(service_ingress_values(item))}"
        for item in load_balancers
        if service_ingress_values(item)
    ]
    detail = f"ready={'; '.join(ready) or 'none'} pending={'; '.join(missing) or 'none'}"
    return not missing, detail


def wait_for_aks_load_balancers(args: argparse.Namespace) -> str:
    if not (args.aks_profiles and args.aks_wait_load_balancers and args.aks_require_public_sip_ingress):
        return "aks_loadbalancer_wait=skipped"

    output_root = Path(args.output_dir) / args.run_id
    output_root.mkdir(parents=True, exist_ok=True)
    log_path = output_root / "aks-loadbalancer-preflight.log"
    deadline = time.monotonic() + args.aks_load_balancer_wait_timeout
    attempts: list[str] = []
    last_detail = "not_checked"

    while time.monotonic() <= deadline:
        ready, detail = azure_load_balancer_readiness(args)
        last_detail = detail
        attempts.append(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())} | {detail}")
        log_path.write_text("\n".join(attempts) + "\n", encoding="utf-8")
        if ready:
            return f"aks_loadbalancer_wait=ready {detail}"
        time.sleep(args.aks_load_balancer_poll_interval)

    raise RuntimeError(
        "Timed out waiting for Azure LoadBalancer ingress before AKS regression: "
        f"{last_detail}. See {log_path}"
    )


def should_cleanup_local_logs(args: argparse.Namespace) -> bool:
    if args.keep_old_logs:
        return False
    return bool(args.rasa_profiles or args.aks_profiles or args.all_profiles or not args.profile)


def wait_for_runner(args: argparse.Namespace) -> tuple[str, str, str]:
    deadline = time.monotonic() + args.job_timeout
    pod_name = ""
    last_detail = "Waiting for regression runner pod to start"
    last_progress = ""
    while time.monotonic() < deadline:
        pod = job_pod(args)
        if not pod:
            time.sleep(args.job_poll_interval)
            continue
        pod_name = str(pod["metadata"]["name"])
        statuses = pod.get("status", {}).get("containerStatuses", [])
        for status in statuses:
            if status.get("name") != "regression-runner":
                continue
            state = status.get("state", {})
            if "terminated" in state:
                terminated = state["terminated"]
                exit_code = int(terminated.get("exitCode", 1))
                reason = terminated.get("reason", "")
                detail = f"regression-runner exit_code={exit_code} reason={reason}"
                return ("passed" if exit_code == 0 else "failed"), detail, pod_name
            if "waiting" in state:
                waiting = state["waiting"]
                last_detail = f"regression-runner waiting reason={waiting.get('reason', '')} message={waiting.get('message', '')}"
            elif "running" in state:
                last_detail = "regression-runner running"
                progress_log = run_command(
                    [
                        args.kubectl_bin,
                        "-n",
                        args.namespace,
                        "logs",
                        f"pod/{pod_name}",
                        "-c",
                        "regression-runner",
                        "--tail=20",
                    ],
                    timeout=args.kubectl_timeout,
                    check=False,
                )
                progress_lines = [
                    line
                    for line in progress_log.stdout.splitlines()
                    if line.startswith("Regression progress:")
                ]
                if progress_lines and progress_lines[-1] != last_progress:
                    last_progress = progress_lines[-1]
                    print(last_progress, flush=True)
        time.sleep(args.job_poll_interval)
    return "timeout", f"Timed out after {args.job_timeout}s waiting for regression-runner; last={last_detail}", pod_name


def run_job(args: argparse.Namespace) -> int:
    validate_requested_images(args)
    ensure_binary(args.kubectl_bin)
    manifests = [service_account_manifest(args), role_manifest(args), role_binding_manifest(args), job_manifest(args)]
    if args.dry_run:
        print(json.dumps({"kind": "List", "apiVersion": "v1", "items": manifests}, indent=2))
        print("\nRunner command:")
        print(command_text(["python3", *runner_command_args(args)]))
        return 0

    if should_cleanup_local_logs(args):
        shutil.rmtree(Path(args.output_dir), ignore_errors=True)
    if (
        args.build_playsbc_image
        or args.build_runner_image
        or args.build_sipp_image
        or args.build_rtpengine_image
        or args.kind_load_images
    ):
        build_images(args)
    prepare_playsbc_image_values(args)
    aks_preflight = wait_for_aks_load_balancers(args)

    for manifest in manifests[:-1]:
        apply_manifest(args, manifest)
    run_command(
        [args.kubectl_bin, "-n", args.namespace, "delete", "job", args.job_name, "--ignore-not-found=true"],
        timeout=args.kubectl_timeout,
        check=True,
    )
    apply_manifest(args, manifests[-1])

    job_status, job_detail, pod_name = wait_for_runner(args)
    pod_name = pod_name or job_pod_name(args)
    if pod_name:
        logs = run_command(
            [args.kubectl_bin, "-n", args.namespace, "logs", f"pod/{pod_name}", "-c", "regression-runner", "--tail=-1"],
            timeout=args.kubectl_timeout,
            check=False,
        )
        logs_text = logs.stdout + logs.stderr
    else:
        logs_text = "Runner pod was not found; inspect the Job events for details.\n"
    output_root = collect_job_outputs(args, pod_name, logs_text)
    archive_result: Optional[tuple[Path, int]] = None

    try:
        archive_result = create_aks_evidence_archive(args, output_root)
    finally:
        if not args.keep_job:
            run_command(
                [args.kubectl_bin, "-n", args.namespace, "delete", "job", args.job_name, "--ignore-not-found=true"],
                timeout=args.kubectl_timeout,
                check=False,
            )

    print(f"Kubernetes regression Job: {args.job_name}")
    print(f"Job status: {job_status} ({job_detail})")
    print(f"Runner pod: {pod_name or 'not found'}")
    print(f"Copied outputs: {output_root}")
    if aks_preflight != "aks_loadbalancer_wait=skipped":
        print(f"AKS preflight: {aks_preflight}")
    if archive_result:
        archive_path, member_count = archive_result
        print(f"Evidence archive: {archive_path} ({member_count} files)")
    latest = output_root / args.remote_report_dir_name / "latest.html"
    if latest.exists():
        print(f"Latest report: {latest}")
        print(f'Evidence browser: python3 tools/serve_regression_report.py "{latest}"')
    if job_status != "passed":
        print(job_detail, file=sys.stderr)
        return 1
    if args.print_runner_log:
        print(logs.stdout)
    return 0


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="", help="Run/report identifier; defaults to a timestamp")
    parser.add_argument("--namespace", default="playsbc", help="Fixed PlaySBC namespace; must remain playsbc")
    parser.add_argument("--job-name", default="")
    parser.add_argument("--service-account", default="playsbc-regression-runner")
    parser.add_argument("--rbac-name", default="playsbc-regression-runner")
    parser.add_argument("--runner-image", default="playsbc-k8s-regression:local")
    parser.add_argument("--playsbc-image", default="playsbc:k8s-regression")
    parser.add_argument("--rtpengine-image", default="playsbc/rtpengine:local")
    parser.add_argument(
        "--runner-image-pull-policy",
        choices=("Always", "IfNotPresent", "Never"),
        default=None,
        help="Runner image pull policy; defaults to Always for AKS profiles and IfNotPresent otherwise",
    )
    parser.add_argument("--sipp-image", default="playsbc-sipp:local")
    parser.add_argument(
        "--sipp-image-pull-policy",
        choices=("Always", "IfNotPresent", "Never"),
        default=None,
        help="SIPp image pull policy; defaults to Always for AKS profiles and IfNotPresent otherwise",
    )
    parser.add_argument("--build-playsbc-image", action="store_true")
    parser.add_argument("--build-runner-image", action="store_true")
    parser.add_argument("--build-sipp-image", action="store_true")
    parser.add_argument("--build-rtpengine-image", action="store_true")
    parser.add_argument("--kind-load-images", action="store_true")
    parser.add_argument("--load-sipp-image", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--load-playsbc-image", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--load-rtpengine-image", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--set-playsbc-image", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--set-rtpengine-image", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--kind-cluster", default="playsbc")
    parser.add_argument("--profile", action="append", choices=SELECTABLE_PROFILES)
    parser.add_argument("--all-profiles", action="store_true", help="Run all canonical Kubernetes regression profiles; default when --profile is omitted")
    parser.add_argument("--rasa-profiles", action="store_true", help="Run only the Kubernetes AI/Rasa profiles")
    parser.add_argument("--aks-profiles", action="store_true", help="Run only the Azure AKS readiness profiles")
    parser.add_argument("--aks-mode", action=argparse.BooleanOptionalAction, default=False, help="Collect Azure AKS LoadBalancer evidence in each bundle")
    parser.add_argument("--aks-services-selector", default="playsbc.io/cloud=azure", help="Label selector for Azure-specific LoadBalancer services")
    parser.add_argument("--aks-require-azure-services", action=argparse.BooleanOptionalAction, default=False, help="Fail when the Azure SIP public LoadBalancer service is missing")
    parser.add_argument("--aks-require-static-sip", action=argparse.BooleanOptionalAction, default=False, help="Fail when the Azure SIP public service lacks a static IP annotation")
    parser.add_argument("--aks-require-public-sip-ingress", action=argparse.BooleanOptionalAction, default=False, help="Fail until Azure assigns an external public SIP ingress address")
    parser.add_argument("--aks-require-public-rtp-ingress", action=argparse.BooleanOptionalAction, default=False, help="Fail until Azure assigns an external public RTP ingress address")
    parser.add_argument("--aks-require-rtp-port-range", action=argparse.BooleanOptionalAction, default=False, help="Fail unless the Azure RTP LoadBalancer exposes the expected UDP media range")
    parser.add_argument("--aks-rtp-port-min", type=int, default=30000)
    parser.add_argument("--aks-rtp-port-max", type=int, default=30049)
    parser.add_argument("--aks-wait-load-balancers", action=argparse.BooleanOptionalAction, default=True, help="For AKS profiles, wait for selected Azure LoadBalancer services to receive ingress before starting the Job")
    parser.add_argument("--aks-load-balancer-wait-timeout", type=int, default=1200)
    parser.add_argument("--aks-load-balancer-poll-interval", type=float, default=10.0)
    parser.add_argument("--rtpengine-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--active-active-topology", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--playsbc-replicas", type=int, default=2)
    parser.add_argument("--rtpengine-replicas", type=int, default=2)
    parser.add_argument("--ha-cluster-id", default="playsbc-aa-lab")
    parser.add_argument("--ha-shared-state-path", default="/var/lib/playsbc/ha-state.sqlite3")
    parser.add_argument("--multus-enabled", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--require-multus", action="store_true")
    parser.add_argument("--service", default="playsbc-playsbc")
    parser.add_argument("--sip-port", type=int, default=5062)
    parser.add_argument("--tls-port", type=int, default=5061)
    parser.add_argument("--deployment", default="playsbc-playsbc")
    parser.add_argument("--rtpengine-service", default="playsbc-playsbc-rtpengine")
    parser.add_argument("--rtpengine-deployment", default="playsbc-playsbc-rtpengine")
    parser.add_argument("--helm-release", default="playsbc")
    parser.add_argument("--helm-bin", default="helm")
    parser.add_argument("--kubectl-bin", default="kubectl")
    parser.add_argument("--profile-timeout", type=int, default=180)
    parser.add_argument("--helm-timeout", type=int, default=180)
    parser.add_argument("--rollout-timeout", type=int, default=DEFAULT_ROLLOUT_TIMEOUT)
    parser.add_argument("--sipp-timeout", type=int, default=90)
    parser.add_argument("--pod-ready-timeout", type=int, default=60)
    parser.add_argument("--deployment-log-tail", type=int, default=250)
    parser.add_argument("--tls-secret-name", default="playsbc-regression-tls")
    parser.add_argument(
        "--job-timeout",
        type=int,
        default=28800,
        help="Maximum seconds for the in-cluster regression Job (default: 28800 for catalogs larger than 100 profiles)",
    )
    parser.add_argument("--job-poll-interval", type=float, default=5.0)
    parser.add_argument("--active-deadline-seconds", type=int, default=30000)
    parser.add_argument("--ttl-seconds-after-finished", type=int, default=3600)
    parser.add_argument("--kubectl-timeout", type=int, default=180)
    parser.add_argument("--copy-timeout", type=int, default=600)
    parser.add_argument("--image-build-timeout", type=int, default=1200)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--remote-output-root-name", default=DEFAULT_REMOTE_OUTPUT_ROOT)
    parser.add_argument("--remote-report-dir-name", default=DEFAULT_REMOTE_REPORT_DIR)
    parser.add_argument("--keep-old-logs", action="store_true", help="Keep existing local regression logs before launching")
    parser.add_argument("--no-restore-helm-values", action="store_true", help="Leave Helm on the last profile after the in-cluster run")
    parser.add_argument("--keep-job", action="store_true")
    parser.add_argument("--keep-sipp-pods", action="store_true")
    parser.add_argument("--print-runner-log", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.namespace != "playsbc":
        raise SystemExit("Kubernetes regression Job mode is fixed to the playsbc namespace.")
    if args.rasa_profiles and (args.all_profiles or args.profile):
        raise SystemExit("--rasa-profiles cannot be combined with --all-profiles or --profile")
    if args.aks_profiles and (args.rasa_profiles or args.all_profiles or args.profile):
        raise SystemExit("--aks-profiles cannot be combined with --rasa-profiles, --all-profiles, or --profile")
    if args.rasa_profiles:
        if args.output_dir == DEFAULT_OUTPUT_DIR:
            args.output_dir = RASA_OUTPUT_DIR
        if args.remote_output_root_name == DEFAULT_REMOTE_OUTPUT_ROOT:
            args.remote_output_root_name = RASA_REMOTE_OUTPUT_ROOT
        if args.remote_report_dir_name == DEFAULT_REMOTE_REPORT_DIR:
            args.remote_report_dir_name = RASA_REMOTE_REPORT_DIR
        if args.rollout_timeout == DEFAULT_ROLLOUT_TIMEOUT:
            args.rollout_timeout = RASA_ROLLOUT_TIMEOUT
    if args.aks_profiles:
        args.aks_mode = True
        args.aks_require_azure_services = True
        args.aks_require_static_sip = True
        args.aks_require_public_sip_ingress = True
        args.aks_require_public_rtp_ingress = True
        args.aks_require_rtp_port_range = True
        if args.output_dir == DEFAULT_OUTPUT_DIR:
            args.output_dir = AKS_OUTPUT_DIR
        if args.remote_output_root_name == DEFAULT_REMOTE_OUTPUT_ROOT:
            args.remote_output_root_name = AKS_REMOTE_OUTPUT_ROOT
        if args.remote_report_dir_name == DEFAULT_REMOTE_REPORT_DIR:
            args.remote_report_dir_name = AKS_REMOTE_REPORT_DIR
    if args.runner_image_pull_policy is None:
        args.runner_image_pull_policy = "Always" if args.aks_mode else "IfNotPresent"
    if args.sipp_image_pull_policy is None:
        args.sipp_image_pull_policy = "Always" if args.aks_mode else "IfNotPresent"
    if args.active_active_topology is None:
        args.active_active_topology = False if args.aks_profiles else True
    if args.playsbc_replicas < 1:
        raise SystemExit("--playsbc-replicas must be at least 1")
    if args.rtpengine_replicas < 1:
        raise SystemExit("--rtpengine-replicas must be at least 1")
    if args.aks_rtp_port_min > args.aks_rtp_port_max:
        raise SystemExit("--aks-rtp-port-min must be less than or equal to --aks-rtp-port-max")
    if args.require_multus and not args.multus_enabled:
        raise SystemExit("--require-multus also requires --multus-enabled")
    if args.build_playsbc_image:
        args.set_playsbc_image = True
    if args.build_rtpengine_image:
        args.set_rtpengine_image = True
        args.load_rtpengine_image = True
    args.run_id = args.run_id or (make_rasa_run_id() if args.rasa_profiles else make_aks_run_id() if args.aks_profiles else make_run_id())
    args.job_name = args.job_name or args.run_id
    if len(args.job_name) > 63:
        args.job_name = args.job_name[:63].rstrip("-")
    if args.build_playsbc_image:
        args.set_playsbc_image = True
    return args


def main() -> int:
    args = parse_args()
    if args.rasa_profiles:
        print(f"Launching Kubernetes RASA Regression Job for {len(RASA_PROFILES)} profiles.")
        print(f"Local output directory: {args.output_dir}")
    elif args.aks_profiles:
        print(f"Launching Azure AKS Regression Job for {len(AKS_PROFILES)} profiles.")
        print(f"Local output directory: {args.output_dir}")
    elif args.all_profiles or not args.profile:
        print(f"Launching Kubernetes Job for {len(ALL_PROFILES)} profiles.")
    else:
        print(f"Launching Kubernetes Job for profiles: {', '.join(args.profile)}")
    sleep_inhibitor = None if args.dry_run else start_host_sleep_inhibitor()
    if sleep_inhibitor is not None:
        print("Host sleep inhibition: active (macOS caffeinate)")
    try:
        return run_job(args)
    finally:
        stop_host_sleep_inhibitor(sleep_inhibitor)


if __name__ == "__main__":
    raise SystemExit(main())
