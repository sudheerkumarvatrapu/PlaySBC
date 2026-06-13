#!/usr/bin/env python3
"""Run PlaySBC SIPp smoke and B2BUA regressions, then write an HTML report."""

from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional


ROOT = Path(__file__).resolve().parents[1]
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
    "registered-inbound",
    "registered-outbound",
    "load-5cps-60s",
    "load-5cps-60s-rtpengine-transcoding",
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
    rows = []
    for line in stdout.splitlines():
        if ": " not in line:
            continue
        name, status = line.split(": ", 1)
        status = status.strip()
        if status not in {"passed", "failed", "dry-run"}:
            continue
        rows.append(
            ReportRow(
                suite=f"B2BUA {profile}",
                name=name.strip(),
                status=status,
                returncode=0 if status == "passed" else None,
                duration_seconds=0.0,
                log_path=str(log_path),
                command=command,
            )
        )
    if not rows:
        rows.append(
            ReportRow(
                suite=f"B2BUA {profile}",
                name=profile,
                status=status_from_returncode(returncode),
                returncode=returncode,
                duration_seconds=duration,
                log_path=str(log_path),
                command=command,
            )
        )
    return rows


def render_html(rows: List[ReportRow], generated_at: str, run_id: str) -> str:
    passed = sum(1 for row in rows if row.status == "passed")
    failed = sum(1 for row in rows if row.status != "passed")
    summary_class = "pass" if failed == 0 else "fail"
    row_html = []
    for row in rows:
        status_class = "pass" if row.status == "passed" else "fail"
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
    .summary.fail {{ background: #fef2f2; border: 1px solid #dc2626; }}
    table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 10px; text-align: left; vertical-align: top; word-wrap: break-word; }}
    th {{ background: #f9fafb; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: #374151; }}
    code {{ font-size: 12px; white-space: pre-wrap; }}
    .badge {{ display: inline-block; min-width: 68px; text-align: center; border-radius: 999px; padding: 4px 8px; font-weight: 700; font-size: 12px; }}
    .badge.pass {{ color: #166534; background: #dcfce7; border: 1px solid #16a34a; }}
    .badge.fail {{ color: #991b1b; background: #fee2e2; border: 1px solid #dc2626; }}
  </style>
</head>
<body>
  <h1>PlaySBC Regression Report</h1>
  <p class="meta">Run ID: <code>{html.escape(run_id)}</code> | Generated: {html.escape(generated_at)}</p>
  <div class="summary {summary_class}">
    <strong>Total: {len(rows)}</strong>
    <strong>Passed: {passed}</strong>
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
    parser.add_argument("--skip-sipp-smoke", action="store_true")
    parser.add_argument("--skip-b2bua", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    run_id = args.run_id or make_run_id()
    rows: List[ReportRow] = []

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
        b2bua_log_path = ROOT / "logs" / args.b2bua_log_folder
        for profile in profiles:
            command = [
                sys.executable,
                str(ROOT / "tools" / "run_b2bua_sipp_smoke.py"),
                "--profile",
                profile,
                "--run-id",
                f"{run_id}-{profile}",
                "--log-folder",
                args.b2bua_log_folder,
            ]
            if args.b2bua_media_driver:
                command.extend(["--media-driver", args.b2bua_media_driver])
            if args.b2bua_sipp_pcap_sudo:
                command.append("--sipp-pcap-sudo")
            command_text = " ".join(command)
            returncode, duration, stdout, stderr = run_command(command, args.timeout)
            rows.extend(parse_b2bua_stdout(profile, stdout, returncode, duration, b2bua_log_path, command_text))
            profile_log = b2bua_log_path / f"{profile}-{run_id}-runner.log"
            profile_log.write_text(stdout + ("\n--- stderr ---\n" + stderr if stderr.strip() else ""), encoding="utf-8")

    report_path = write_reports(rows, Path(args.report_dir), run_id)
    failed = [row for row in rows if row.status != "passed"]
    print(f"Regression report: {report_path}")
    print(f"Latest report: {Path(args.report_dir) / 'latest.html'}")
    for row in rows:
        print(f"{row.suite} / {row.name}: {row.status}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
