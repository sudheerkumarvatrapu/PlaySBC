#!/usr/bin/env python3
"""Run PlaySBC Kubernetes SIPp regression profiles and write an HTML report."""

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
SCENARIO_DIR = ROOT / "sipp" / "scenarios"
DEFAULT_PROFILES = ("options", "register-contact", "b2bua-signalling")
ALL_PROFILES = DEFAULT_PROFILES

sys.path.insert(0, str(ROOT))
from tools.run_regression_suite import cleanup_old_reports, ReportPhase, ReportRow, write_reports  # noqa: E402


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


def pod_manifest(name: str, image: str, pull_policy: str, configmap: str, run_id: str) -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "labels": {
                "app.kubernetes.io/name": "playsbc-k8s-regression",
                "app.kubernetes.io/part-of": "playsbc",
                "playsbc-regression-run": run_id,
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "sipp-agent",
                    "image": image,
                    "imagePullPolicy": pull_policy,
                    "command": ["sleep", "3600"],
                    "volumeMounts": [{"name": "scenarios", "mountPath": "/scenarios", "readOnly": True}],
                }
            ],
            "volumes": [{"name": "scenarios", "configMap": {"name": configmap}}],
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

    def kubectl(self, *parts: str, timeout: Optional[int] = None, input_text: Optional[str] = None, check: bool = False) -> CommandResult:
        command = [self.args.kubectl_bin]
        if self.args.namespace:
            command.extend(["-n", self.args.namespace])
        command.extend(parts)
        return run_command(command, timeout=timeout or self.args.timeout, input_text=input_text, check=check)

    def kubectl_cluster(self, *parts: str, timeout: Optional[int] = None, input_text: Optional[str] = None, check: bool = False) -> CommandResult:
        command = [self.args.kubectl_bin, *parts]
        return run_command(command, timeout=timeout or self.args.timeout, input_text=input_text, check=check)

    def prepare_common(self, bundle: Path, phases: PhaseLog) -> None:
        started = time.monotonic()
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
                f"and applied ConfigMap={self.args.configmap} with SIPp XML scenarios."
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

    def create_agent(self, name: str, bundle: Path) -> str:
        manifest = pod_manifest(name, self.args.sipp_image, self.args.image_pull_policy, self.args.configmap, self.run_id)
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
            else:
                raise ValueError(f"Unsupported Kubernetes regression profile: {profile}")
            status = "passed" if returncodes and all(code == 0 for code in returncodes) else "failed"
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
    parser.add_argument("--deployment", default="playsbc-playsbc")
    parser.add_argument("--rtpengine-service", default="playsbc-playsbc-rtpengine")
    parser.add_argument("--rtpengine-deployment", default="playsbc-playsbc-rtpengine")
    parser.add_argument("--configmap", default="playsbc-sipp-scenarios")
    parser.add_argument("--sipp-image", default="playsbc-sipp:local")
    parser.add_argument("--image-pull-policy", default="IfNotPresent")
    parser.add_argument("--build-sipp-image", action="store_true", help="Build docker/sipp.Dockerfile before running")
    parser.add_argument("--kind-load-image", action="store_true", help="Load --sipp-image into the kind cluster before running")
    parser.add_argument("--kind-cluster", default="playsbc")
    parser.add_argument("--profile", action="append", choices=ALL_PROFILES)
    parser.add_argument("--all-profiles", action="store_true")
    parser.add_argument("--output-root", default=str(ROOT / "logs" / "k8s-Regression"))
    parser.add_argument("--report-dir", default=str(ROOT / "logs" / "k8s-reports"))
    parser.add_argument("--kubectl-bin", default="kubectl")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--sipp-timeout", type=int, default=60)
    parser.add_argument("--pod-ready-timeout", type=int, default=60)
    parser.add_argument("--image-build-timeout", type=int, default=900)
    parser.add_argument("--deployment-log-tail", type=int, default=200)
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
    profiles = ALL_PROFILES if args.all_profiles else tuple(args.profile or DEFAULT_PROFILES)
    run_id = args.run_id or make_run_id()
    output_root = Path(args.output_root)
    report_dir = Path(args.report_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    runner = K8sRegressionRunner(args, run_id)
    rows = [runner.run_profile(profile, output_root) for profile in profiles]
    cleanup_old_reports(report_dir, run_id)
    report_path = write_reports(rows, report_dir, run_id)
    print(f"Kubernetes regression report: {report_path}")
    print(f"Latest report: {report_dir / 'latest.html'}")
    for row in rows:
        print(f"{row.suite} / {row.name}: {row.status}")
    return 1 if any(row.status != "passed" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
