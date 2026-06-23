#!/usr/bin/env python3
"""Run PlaySBC SIPp smoke and B2BUA regressions, then write an HTML report."""

from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_B2BUA_PROFILES = (
    "basic-signalling",
    "basic-media",
    "transcoding",
    "registered-inbound",
    "registered-outbound",
)
ALL_B2BUA_PROFILES = (
    "basic-signalling",
    "basic-media",
    "transcoding",
    "rtpengine",
    "rtpengine-media",
    "rtpengine-transcoding",
    "tcp-rtpengine-transcoding",
    "registered-inbound",
    "registered-outbound",
    "invalid-bye",
    "unknown-route",
    "failed-outbound",
    "cancel",
    "retransmission",
    "esbc-options-keepalive",
    "esbc-static-trunk-route",
    "esbc-e164-route-policy",
    "esbc-trunk-failure",
    "small-load-2cps-10s",
    "soak-1cps-30s",
    "load-5cps-60s",
    "load-5cps-60s-rtpengine-transcoding",
)
RTPENGINE_B2BUA_PROFILES = (
    "rtpengine",
    "rtpengine-media",
    "rtpengine-transcoding",
    "tcp-rtpengine-transcoding",
    "load-5cps-60s-rtpengine-transcoding",
)
B2BUA_LOG_FILES = (
    "log.sip",
    "log.media",
    "log.transcoding",
    "log.platform",
    "log.networking",
    "log.udp",
    "log.tcp",
    "log.tls",
    "log.call",
    "log.sipp",
)


@dataclass
class ReportRow:
    suite: str
    name: str
    status: str
    returncode: Optional[int]
    duration_seconds: float
    log_path: str
    command: str


def make_run_id() -> str:
    return time.strftime("regression-%Y%m%d-%H%M%S", time.localtime())


def run_command(command: List[str], timeout: int) -> tuple[int, float, str, str]:
    started = time.monotonic()
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    return completed.returncode, time.monotonic() - started, completed.stdout, completed.stderr


def status_from_returncode(returncode: int) -> str:
    return "passed" if returncode == 0 else "failed"


class SudoKeepalive:
    """Refresh cached sudo credentials so late SIPp PCAP profiles can still run."""

    def __init__(self, interval_seconds: float = 60.0):
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> tuple[bool, str]:
        ok, detail = self.refresh()
        if not ok:
            return False, detail
        self._thread = threading.Thread(target=self._run, name="playsbc-sudo-keepalive", daemon=True)
        self._thread.start()
        return True, detail

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def refresh(self) -> tuple[bool, str]:
        completed = subprocess.run(["sudo", "-n", "-v"], text=True, capture_output=True)
        detail = (completed.stderr.strip() or completed.stdout.strip() or f"returncode={completed.returncode}").strip()
        if completed.returncode == 0:
            return True, "sudo credentials refreshed"
        return False, detail

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.refresh()


def probe_rtpengine(url: str, timeout: float) -> tuple[bool, str]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "check_rtpengine.py"),
        "--url",
        url,
        "--timeout",
        str(timeout),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=max(timeout + 1.0, 2.0),
        )
    except subprocess.TimeoutExpired:
        return False, "tools/check_rtpengine.py timed out"

    detail = (completed.stdout.strip() or completed.stderr.strip() or f"returncode={completed.returncode}").strip()
    return completed.returncode == 0, detail


def rtpengine_blocked_row(profile: str, url: str, detail: str, duration: float, log_path: Path, command: str) -> ReportRow:
    return ReportRow(
        suite=f"B2BUA {profile}",
        name="rtpengine-preflight",
        status="blocked",
        returncode=None,
        duration_seconds=duration,
        log_path=str(log_path),
        command=f"{command} # blocked: RTPengine not reachable at {url}: {detail}",
    )


def initialize_b2bua_log_bundle(log_path: Path) -> None:
    log_path.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    for filename in B2BUA_LOG_FILES:
        (log_path / filename).write_text(f"{timestamp} | LOG START | file={filename}\n", encoding="utf-8")


def append_bundle_log(log_path: Path, filename: str, title: str, body: str = "") -> None:
    initialize = not (log_path / filename).exists()
    if initialize:
        initialize_b2bua_log_bundle(log_path)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with (log_path / filename).open("a", encoding="utf-8") as log_file:
        log_file.write(f"{timestamp} | {title}\n")
        if body:
            log_file.write(body.rstrip() + "\n")


def extract_b2bua_log_path(stdout: str, fallback: Path) -> Path:
    for line in stdout.splitlines():
        if line.startswith("B2BUA SIPp logs: "):
            return Path(line.split(": ", 1)[1].strip())
    return fallback


def summarize_statuses(statuses: List[str]) -> str:
    normalized = [status for status in statuses if status]
    if not normalized:
        return "unknown"
    if any(status == "failed" for status in normalized):
        return "failed"
    if any(status == "blocked" for status in normalized):
        return "blocked"
    if all(status in {"passed", "dry-run"} for status in normalized):
        return "passed"
    return "unknown"


def report_statuses_by_log_path(report_dir: Path) -> dict[str, List[str]]:
    statuses: dict[str, List[str]] = {}
    if not report_dir.exists():
        return statuses
    for report in sorted(report_dir.glob("*.json")):
        try:
            rows = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("suite", "")).startswith("B2BUA"):
                continue
            log_path = str(row.get("log_path", ""))
            status = str(row.get("status", ""))
            if log_path and status:
                statuses.setdefault(log_path, []).append(status)
    return statuses


def b2bua_bundle_status(log_path: Path, report_statuses: dict[str, List[str]]) -> str:
    report_status = summarize_statuses(report_statuses.get(str(log_path), []))
    if report_status != "unknown":
        return report_status

    platform = log_path / "log.platform"
    if not platform.exists():
        return "unknown"
    text = platform.read_text(encoding="utf-8", errors="replace")
    if "RTPENGINE PREFLIGHT BLOCKED" in text:
        return "blocked"
    statuses = []
    for line in text.splitlines():
        if ": " not in line:
            continue
        _name, status_text = line.split(": ", 1)
        status = status_text.split(maxsplit=1)[0].strip()
        if status in {"passed", "failed", "dry-run", "blocked"}:
            statuses.append(status)
    return summarize_statuses(statuses)


def cleanup_non_failed_b2bua_log_bundles(log_root: Path, report_dir: Path) -> List[Path]:
    if not log_root.exists():
        return []
    report_statuses = report_statuses_by_log_path(report_dir)
    deleted = []
    for candidate in sorted(log_root.iterdir()):
        if not candidate.is_dir():
            continue
        if b2bua_bundle_status(candidate, report_statuses) not in {"passed", "blocked"}:
            continue
        shutil.rmtree(candidate)
        deleted.append(candidate)
    return deleted


def cleanup_old_reports(report_dir: Path, current_run_id: str) -> List[Path]:
    if not report_dir.exists():
        return []

    keep = {"latest.html", f"{current_run_id}.html", f"{current_run_id}.json"}
    deleted = []
    for candidate in sorted(report_dir.iterdir()):
        if not candidate.is_file() or candidate.name in keep:
            continue
        if candidate.suffix.lower() not in {".html", ".json"}:
            continue
        candidate.unlink()
        deleted.append(candidate)
    return deleted


def parse_sipp_smoke_summary(summary_path: Path, fallback_command: str) -> List[ReportRow]:
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = []
    for result in payload.get("results", []):
        command = result.get("command") or fallback_command
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        rows.append(
            ReportRow(
                suite="SIPp Smoke",
                name=str(result.get("scenario", "")),
                status=str(result.get("status", "failed")),
                returncode=result.get("returncode"),
                duration_seconds=float(result.get("duration_seconds") or 0),
                log_path=str(summary_path.parent),
                command=str(command),
            )
        )
    return rows


def parse_b2bua_stdout(profile: str, stdout: str, returncode: int, duration: float, log_path: Path, command: str) -> List[ReportRow]:
    log_path = extract_b2bua_log_path(stdout, log_path)
    statuses = []
    names = []
    for line in stdout.splitlines():
        if ": " not in line:
            continue
        name, status = line.split(": ", 1)
        status = status.strip()
        if status not in {"passed", "failed", "dry-run", "blocked"}:
            continue
        names.append(name.strip())
        statuses.append(status)

    aggregate_status = summarize_statuses(statuses)
    if aggregate_status == "unknown":
        aggregate_status = status_from_returncode(returncode)
    elif returncode != 0 and aggregate_status in {"passed", "dry-run"}:
        aggregate_status = "failed"

    aggregate_command = command
    if names:
        aggregate_command = f"{command} # steps: {', '.join(f'{name}={status}' for name, status in zip(names, statuses))}"

    aggregate_returncode = 0 if aggregate_status in {"passed", "dry-run"} and returncode == 0 else returncode
    return [
        ReportRow(
            suite=f"B2BUA {profile}",
            name=profile,
            status=aggregate_status,
            returncode=aggregate_returncode,
            duration_seconds=duration,
            log_path=str(log_path),
            command=aggregate_command,
        )
    ]


def render_html(rows: List[ReportRow], generated_at: str, run_id: str) -> str:
    passed = sum(1 for row in rows if row.status == "passed")
    blocked = sum(1 for row in rows if row.status == "blocked")
    failed = sum(1 for row in rows if row.status not in {"passed", "blocked"})
    summary_class = "pass" if failed == 0 and blocked == 0 else "blocked" if failed == 0 else "fail"
    row_html = []
    for row in rows:
        status_class = "pass" if row.status == "passed" else "blocked" if row.status == "blocked" else "fail"
        row_html.append(
            "<tr>"
            f"<td>{html.escape(row.suite)}</td>"
            f"<td>{html.escape(row.name)}</td>"
            f"<td><span class=\"badge {status_class}\">{html.escape(row.status.upper())}</span></td>"
            f"<td>{'' if row.returncode is None else row.returncode}</td>"
            f"<td>{row.duration_seconds:.3f}</td>"
            f"<td><code>{html.escape(row.log_path)}</code></td>"
            f"<td><code>{html.escape(row.command)}</code></td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>PlaySBC Regression Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2937; }}
    h1 {{ margin-bottom: 4px; }}
    .meta {{ color: #4b5563; margin: 0 0 20px; }}
    .summary {{ display: inline-flex; gap: 16px; padding: 12px 14px; border-radius: 8px; margin-bottom: 22px; }}
    .summary.pass {{ background: #ecfdf5; border: 1px solid #16a34a; }}
    .summary.blocked {{ background: #fffbeb; border: 1px solid #f59e0b; }}
    .summary.fail {{ background: #fef2f2; border: 1px solid #dc2626; }}
    table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 10px; text-align: left; vertical-align: top; word-wrap: break-word; }}
    th {{ background: #f9fafb; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: #374151; }}
    code {{ font-size: 12px; white-space: pre-wrap; }}
    .badge {{ display: inline-block; min-width: 68px; text-align: center; border-radius: 999px; padding: 4px 8px; font-weight: 700; font-size: 12px; }}
    .badge.pass {{ color: #166534; background: #dcfce7; border: 1px solid #16a34a; }}
    .badge.blocked {{ color: #92400e; background: #fef3c7; border: 1px solid #f59e0b; }}
    .badge.fail {{ color: #991b1b; background: #fee2e2; border: 1px solid #dc2626; }}
  </style>
</head>
<body>
  <h1>PlaySBC Regression Report</h1>
  <p class="meta">Run ID: <code>{html.escape(run_id)}</code> | Generated: {html.escape(generated_at)}</p>
  <div class="summary {summary_class}">
    <strong>Total: {len(rows)}</strong>
    <strong>Passed: {passed}</strong>
    <strong>Blocked: {blocked}</strong>
    <strong>Failed: {failed}</strong>
  </div>
  <table>
    <thead>
      <tr>
        <th>Suite</th>
        <th>Scenario</th>
        <th>Status</th>
        <th>Return</th>
        <th>Seconds</th>
        <th>Logs</th>
        <th>Command</th>
      </tr>
    </thead>
    <tbody>
      {''.join(row_html)}
    </tbody>
  </table>
</body>
</html>
"""


def write_reports(rows: List[ReportRow], report_dir: Path, run_id: str) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())
    html_text = render_html(rows, generated_at, run_id)
    report_path = report_dir / f"{run_id}.html"
    latest_path = report_dir / "latest.html"
    json_path = report_dir / f"{run_id}.json"
    report_path.write_text(html_text, encoding="utf-8")
    latest_path.write_text(html_text, encoding="utf-8")
    json_path.write_text(json.dumps([asdict(row) for row in rows], indent=2) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PlaySBC regression suites and write an HTML report")
    parser.add_argument("--run-id", default="", help="Report/run identifier; defaults to a timestamp")
    parser.add_argument("--report-dir", default=str(ROOT / "logs" / "reports"))
    parser.add_argument("--sipp-smoke-root", default=str(ROOT / "logs" / "sipp-smoke-Regression"))
    parser.add_argument("--b2bua-log-folder", default="b2bua-Regression")
    parser.add_argument("--b2bua-profile", action="append", choices=ALL_B2BUA_PROFILES, help="B2BUA profile to run; repeatable")
    parser.add_argument("--all-b2bua-profiles", action="store_true", help="Run all B2BUA profiles, including load and RTPengine profiles")
    parser.add_argument("--b2bua-media-driver", choices=("python", "sipp-pcap"), default="", help="Override B2BUA media driver for media-enabled profiles")
    parser.add_argument("--b2bua-sipp-pcap-sudo", action="store_true", help="Pass --sipp-pcap-sudo to B2BUA profile runs")
    parser.add_argument("--b2bua-rtpengine-url", default="udp://127.0.0.1:2223", help="RTPengine NG control URL for RTPengine-backed B2BUA profiles")
    parser.add_argument("--rtpengine-preflight-timeout", type=float, default=1.0, help="Seconds to wait for RTPengine preflight ping")
    parser.add_argument("--skip-rtpengine-preflight", action="store_true", help="Run RTPengine-backed profiles without checking RTPengine availability first")
    parser.add_argument("--skip-sipp-smoke", action="store_true")
    parser.add_argument("--skip-b2bua", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    run_id = args.run_id or make_run_id()
    report_dir = Path(args.report_dir)
    rows: List[ReportRow] = []
    sudo_keepalive: Optional[SudoKeepalive] = None

    if not args.skip_b2bua:
        b2bua_log_root = ROOT / "logs" / args.b2bua_log_folder
        deleted_bundles = cleanup_non_failed_b2bua_log_bundles(b2bua_log_root, report_dir)
        if deleted_bundles:
            print(f"Deleted {len(deleted_bundles)} passed/blocked B2BUA log bundle(s) before this run.")

    deleted_reports = cleanup_old_reports(report_dir, run_id)
    if deleted_reports:
        print(f"Deleted {len(deleted_reports)} old regression report file(s) before this run.")

    if args.b2bua_sipp_pcap_sudo and not args.skip_b2bua:
        sudo_keepalive = SudoKeepalive()
        sudo_ready, sudo_detail = sudo_keepalive.start()
        if not sudo_ready:
            raise SystemExit(
                "SIPp PCAP sudo mode requires cached sudo credentials before the suite starts. "
                "Run `sudo -v` in your terminal, then rerun the regression. "
                f"sudo_check={sudo_detail}"
            )
        print("SIPp PCAP sudo keepalive started.")

    try:
        if not args.skip_sipp_smoke:
            smoke_run_id = f"{run_id}-sipp-smoke"
            smoke_root = Path(args.sipp_smoke_root)
            if (smoke_root / smoke_run_id).exists():
                raise SystemExit(f"SIPp smoke run directory already exists: {smoke_root / smoke_run_id}")
            command = [
                sys.executable,
                str(ROOT / "tools" / "run_sipp_regression.py"),
                "--start-server",
                "--output-root",
                str(smoke_root),
                "--run-id",
                smoke_run_id,
            ]
            command_text = " ".join(command)
            returncode, duration, stdout, stderr = run_command(command, args.timeout)
            summary_path = smoke_root / smoke_run_id / "summary.json"
            if summary_path.exists():
                rows.extend(parse_sipp_smoke_summary(summary_path, command_text))
            else:
                rows.append(
                    ReportRow("SIPp Smoke", "suite", status_from_returncode(returncode), returncode, duration, str(smoke_root / smoke_run_id), command_text)
                )
            if stderr.strip():
                (smoke_root / smoke_run_id / "stderr.log").write_text(stderr, encoding="utf-8")
            if stdout.strip():
                (smoke_root / smoke_run_id / "stdout.log").write_text(stdout, encoding="utf-8")

        if not args.skip_b2bua:
            profiles = ALL_B2BUA_PROFILES if args.all_b2bua_profiles else tuple(args.b2bua_profile or DEFAULT_B2BUA_PROFILES)
            for profile in profiles:
                profile_run_id = f"{run_id}-{profile}"
                profile_log_path = b2bua_log_root / profile_run_id
                command = [
                    sys.executable,
                    str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                    "--profile",
                    profile,
                    "--run-id",
                    profile_run_id,
                    "--log-folder",
                    args.b2bua_log_folder,
                ]
                if args.b2bua_media_driver:
                    command.extend(["--media-driver", args.b2bua_media_driver])
                if args.b2bua_sipp_pcap_sudo:
                    command.append("--sipp-pcap-sudo")
                if profile in RTPENGINE_B2BUA_PROFILES:
                    command.extend(["--rtpengine-url", args.b2bua_rtpengine_url])
                command_text = " ".join(command)
                if profile in RTPENGINE_B2BUA_PROFILES and not args.skip_rtpengine_preflight:
                    started = time.monotonic()
                    ready, detail = probe_rtpengine(args.b2bua_rtpengine_url, args.rtpengine_preflight_timeout)
                    duration = time.monotonic() - started
                    if not ready:
                        initialize_b2bua_log_bundle(profile_log_path)
                        append_bundle_log(
                            profile_log_path,
                            "log.platform",
                            "RTPENGINE PREFLIGHT BLOCKED",
                            f"rtpengine_url={args.b2bua_rtpengine_url}\nreason={detail}",
                        )
                        rows.append(rtpengine_blocked_row(profile, args.b2bua_rtpengine_url, detail, duration, profile_log_path, command_text))
                        continue
                returncode, duration, stdout, stderr = run_command(command, args.timeout)
                actual_log_path = extract_b2bua_log_path(stdout, profile_log_path)
                if stderr.strip():
                    append_bundle_log(actual_log_path, "log.platform", "RUNNER STDERR", stderr)
                if returncode != 0 and stdout.strip():
                    append_bundle_log(actual_log_path, "log.platform", "RUNNER STDOUT", stdout)
                rows.extend(parse_b2bua_stdout(profile, stdout, returncode, duration, actual_log_path, command_text))

        report_path = write_reports(rows, report_dir, run_id)
        failed = [row for row in rows if row.status != "passed"]
        print(f"Regression report: {report_path}")
        print(f"Latest report: {report_dir / 'latest.html'}")
        for row in rows:
            print(f"{row.suite} / {row.name}: {row.status}")
        return 1 if failed else 0
    finally:
        if sudo_keepalive:
            sudo_keepalive.stop()


if __name__ == "__main__":
    raise SystemExit(main())
