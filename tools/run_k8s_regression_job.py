#!/usr/bin/env python3
"""Launch the full PlaySBC Kubernetes regression from one in-cluster Job pod."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))
from tools.run_k8s_regression import (  # noqa: E402
    ALL_PROFILES,
    RASA_PROFILES,
    SELECTABLE_PROFILES,
    make_rasa_run_id,
    make_run_id,
)


DEFAULT_OUTPUT_DIR = str(ROOT / "logs" / "k8s-job")
RASA_OUTPUT_DIR = str(ROOT / "logs" / "RASA-Regression")
DEFAULT_REMOTE_OUTPUT_ROOT = "k8s-Regression"
DEFAULT_REMOTE_REPORT_DIR = "k8s-reports"
RASA_REMOTE_OUTPUT_ROOT = "RASA-Regression"
RASA_REMOTE_REPORT_DIR = "RASA-reports"
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
                    "secrets",
                    "services",
                ],
                "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"],
            },
            {
                "apiGroups": ["apps"],
                "resources": ["deployments", "replicasets"],
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
    if args.rasa_profiles:
        command.append("--rasa-profiles")
    elif args.all_profiles or not profiles:
        command.append("--all-profiles")
    else:
        for profile in profiles:
            command.extend(["--profile", profile])
    if not args.rtpengine_enabled:
        command.append("--no-rtpengine-enabled")
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
    if args.kind_load_images:
        ensure_binary("kind")
        images = [args.runner_image]
        if args.load_playsbc_image:
            images.append(args.playsbc_image)
        if args.load_sipp_image:
            images.append(args.sipp_image)
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


def prepare_playsbc_image_values(args: argparse.Namespace) -> None:
    if not args.set_playsbc_image:
        return
    repository, tag = split_image_name(args.playsbc_image)
    run_command(
        [
            args.helm_bin,
            "upgrade",
            args.helm_release,
            str(ROOT / "charts" / "playsbc"),
            "--namespace",
            args.namespace,
            "--reuse-values",
            "--set",
            f"image.repository={repository}",
            "--set-string",
            f"image.tag={tag}",
            "--set",
            "image.pullPolicy=IfNotPresent",
        ],
        timeout=args.kubectl_timeout,
        check=True,
    )
    run_command(
        [
            args.kubectl_bin,
            "-n",
            args.namespace,
            "rollout",
            "status",
            f"deployment/{args.deployment}",
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


def should_cleanup_local_logs(args: argparse.Namespace) -> bool:
    if args.keep_old_logs:
        return False
    return bool(args.rasa_profiles or args.all_profiles or not args.profile)


def wait_for_runner(args: argparse.Namespace) -> tuple[str, str, str]:
    deadline = time.monotonic() + args.job_timeout
    pod_name = ""
    last_detail = "Waiting for regression runner pod to start"
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
        time.sleep(args.job_poll_interval)
    return "timeout", f"Timed out after {args.job_timeout}s waiting for regression-runner; last={last_detail}", pod_name


def run_job(args: argparse.Namespace) -> int:
    ensure_binary(args.kubectl_bin)
    if should_cleanup_local_logs(args):
        shutil.rmtree(Path(args.output_dir), ignore_errors=True)
    if args.build_playsbc_image or args.build_runner_image or args.build_sipp_image or args.kind_load_images:
        build_images(args)
    prepare_playsbc_image_values(args)

    manifests = [service_account_manifest(args), role_manifest(args), role_binding_manifest(args), job_manifest(args)]
    if args.dry_run:
        print(json.dumps({"kind": "List", "apiVersion": "v1", "items": manifests}, indent=2))
        print("\nRunner command:")
        print(command_text(["python3", *runner_command_args(args)]))
        return 0

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
    latest = output_root / args.remote_report_dir_name / "latest.html"
    if latest.exists():
        print(f"Latest report: {latest}")
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
    parser.add_argument("--runner-image-pull-policy", default="IfNotPresent")
    parser.add_argument("--sipp-image", default="playsbc-sipp:local")
    parser.add_argument("--sipp-image-pull-policy", default="IfNotPresent")
    parser.add_argument("--build-playsbc-image", action="store_true")
    parser.add_argument("--build-runner-image", action="store_true")
    parser.add_argument("--build-sipp-image", action="store_true")
    parser.add_argument("--kind-load-images", action="store_true")
    parser.add_argument("--load-sipp-image", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--load-playsbc-image", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--set-playsbc-image", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--kind-cluster", default="playsbc")
    parser.add_argument("--profile", action="append", choices=SELECTABLE_PROFILES)
    parser.add_argument("--all-profiles", action="store_true", help="Run all canonical B2BUA profiles; default when --profile is omitted")
    parser.add_argument("--rasa-profiles", action="store_true", help="Run only the Kubernetes AI/Rasa profiles")
    parser.add_argument("--rtpengine-enabled", action=argparse.BooleanOptionalAction, default=True)
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
    parser.add_argument("--job-timeout", type=int, default=10800)
    parser.add_argument("--job-poll-interval", type=float, default=5.0)
    parser.add_argument("--active-deadline-seconds", type=int, default=12000)
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
    if args.rasa_profiles:
        if args.output_dir == DEFAULT_OUTPUT_DIR:
            args.output_dir = RASA_OUTPUT_DIR
        if args.remote_output_root_name == DEFAULT_REMOTE_OUTPUT_ROOT:
            args.remote_output_root_name = RASA_REMOTE_OUTPUT_ROOT
        if args.remote_report_dir_name == DEFAULT_REMOTE_REPORT_DIR:
            args.remote_report_dir_name = RASA_REMOTE_REPORT_DIR
        if args.rollout_timeout == DEFAULT_ROLLOUT_TIMEOUT:
            args.rollout_timeout = RASA_ROLLOUT_TIMEOUT
    args.run_id = args.run_id or (make_rasa_run_id() if args.rasa_profiles else make_run_id())
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
    elif args.all_profiles or not args.profile:
        print(f"Launching Kubernetes Job for {len(ALL_PROFILES)} B2BUA profiles.")
    else:
        print(f"Launching Kubernetes Job for profiles: {', '.join(args.profile)}")
    return run_job(args)


if __name__ == "__main__":
    raise SystemExit(main())
