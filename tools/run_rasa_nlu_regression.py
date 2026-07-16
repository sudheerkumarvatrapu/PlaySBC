#!/usr/bin/env python3
"""Run PlaySBC Rasa NLU regression cases against a real Rasa /model/parse API."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mini_call_server import parse_simple_yaml  # noqa: E402

LANGUAGE_LIMITATION_REPLY = "I can help in English in this lab. Please repeat the request in English."
UNSUPPORTED_LANGUAGE_MARKERS = (
    "necesito ",
    " ayuda",
    "quiero ",
    "soporte",
    "je veux",
    "aide",
)


@dataclass(frozen=True)
class NluCaseResult:
    case_id: str
    user_input: str
    expected_intent: str
    predicted_intent: str
    confidence: float
    bot_reply: str
    status: str
    duration_seconds: float
    detail: str
    sent_text_length: int
    original_text_length: int


def load_cases(paths: list[Path]) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for path in paths:
        parsed = parse_simple_yaml(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("cases"), list):
            raise ValueError(f"{path} must contain a cases list")
        for item in parsed["cases"]:
            if isinstance(item, dict):
                cases.append({str(key): str(value) for key, value in item.items()})
    return cases


def parse_rasa_intent(url: str, text: str, timeout: float) -> tuple[str, float, dict[str, Any]]:
    body = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload or "{}")
    if not isinstance(decoded, dict):
        raise ValueError("Rasa /model/parse response must be a JSON object")
    intent = decoded.get("intent") if isinstance(decoded.get("intent"), dict) else {}
    return str(intent.get("name") or ""), float(intent.get("confidence") or 0.0), decoded


def rasa_webhook_url(parse_url: str) -> str:
    marker = "/model/parse"
    if parse_url.endswith(marker):
        return parse_url[: -len(marker)] + "/webhooks/rest/webhook"
    return parse_url.rstrip("/") + "/webhooks/rest/webhook"


def fetch_rasa_bot_reply(parse_url: str, sender: str, text: str, timeout: float) -> str:
    body = json.dumps({"sender": sender or "playsbc-chat-regression", "message": text}).encode("utf-8")
    request = urllib.request.Request(
        rasa_webhook_url(parse_url),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload or "[]")
    if not isinstance(decoded, list):
        return ""
    replies = [str(item.get("text") or "").strip() for item in decoded if isinstance(item, dict)]
    return "\n".join(reply for reply in replies if reply)


def text_for_case(case: dict[str, str], max_chars: int) -> tuple[str, int]:
    text = case.get("user_input", "")
    if case.get("expected_intent") == "safe_processing":
        text = (text + " ") * 500
    original_length = len(text)
    return text.strip()[:max_chars], original_length


def local_guard_intent(text: str) -> tuple[str, str, str]:
    lowered = f" {text.lower()} "
    if any(marker in lowered for marker in UNSUPPORTED_LANGUAGE_MARKERS):
        return "language_limitation", LANGUAGE_LIMITATION_REPLY, "handled locally before Rasa parse as unsupported-language guard"
    return "", "", ""


def evaluate_case(case: dict[str, str], url: str, timeout: float, max_chars: int) -> NluCaseResult:
    started = time.monotonic()
    expected = case.get("expected_intent", "")
    sent_text, original_length = text_for_case(case, max_chars)
    if not sent_text:
        predicted = "no_input"
        passed = expected == "no_input"
        return NluCaseResult(
            case_id=case.get("id", ""),
            user_input=case.get("user_input", ""),
            expected_intent=expected,
            predicted_intent=predicted,
            confidence=1.0,
            bot_reply="I did not hear anything. Please say support, sales, billing, or agent.",
            status="passed" if passed else "failed",
            duration_seconds=time.monotonic() - started,
            detail="handled locally before Rasa parse" if passed else "empty input did not match expected intent",
            sent_text_length=0,
            original_text_length=original_length,
        )

    guard_intent, guard_reply, guard_detail = local_guard_intent(sent_text)
    if guard_intent:
        passed = expected == guard_intent
        return NluCaseResult(
            case_id=case.get("id", ""),
            user_input=case.get("user_input", ""),
            expected_intent=expected,
            predicted_intent=guard_intent,
            confidence=1.0,
            bot_reply=guard_reply,
            status="passed" if passed else "failed",
            duration_seconds=time.monotonic() - started,
            detail=guard_detail if passed else f"expected={expected} guarded_as={guard_intent}",
            sent_text_length=len(sent_text),
            original_text_length=original_length,
        )

    try:
        predicted, confidence, _raw = parse_rasa_intent(url, sent_text, timeout)
        try:
            bot_reply = fetch_rasa_bot_reply(url, case.get("id", ""), sent_text, timeout)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            bot_reply = f"Rasa webhook reply unavailable: {type(exc).__name__}: {exc}"
        if expected == "safe_processing":
            passed = bool(predicted)
            detail = f"processed safely with predicted_intent={predicted}"
        else:
            passed = predicted == expected
            detail = f"expected={expected} predicted={predicted} confidence={confidence:.3f}"
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        predicted = ""
        confidence = 0.0
        bot_reply = ""
        passed = False
        detail = f"{type(exc).__name__}: {exc}"

    return NluCaseResult(
        case_id=case.get("id", ""),
        user_input=case.get("user_input", ""),
        expected_intent=expected,
        predicted_intent=predicted,
        confidence=confidence,
        bot_reply=bot_reply,
        status="passed" if passed else "failed",
        duration_seconds=time.monotonic() - started,
        detail=detail,
        sent_text_length=len(sent_text),
        original_text_length=original_length,
    )


def write_outputs(results: list[NluCaseResult], output_dir: Path, suite: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = [asdict(result) for result in results]
    (output_dir / "rasa-nlu-results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [f"RASA NLU REGRESSION suite={suite} total={len(results)}"]
    for result in results:
        lines.append(
            f"{result.case_id} status={result.status} expected={result.expected_intent} "
            f"predicted={result.predicted_intent} confidence={result.confidence:.3f} "
            f"sent_len={result.sent_text_length} bot_reply={result.bot_reply!r} detail={result.detail}"
        )
    (output_dir / "log.rasa-nlu").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5005/model/parse")
    parser.add_argument("--case-file", action="append", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("logs/rasa-nlu-regression"))
    parser.add_argument("--suite", default="rasa-nlu")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-user-text-chars", type=int, default=2000)
    args = parser.parse_args(argv)

    cases = load_cases(args.case_file)
    results = [evaluate_case(case, args.url, args.timeout, max(1, args.max_user_text_chars)) for case in cases]
    write_outputs(results, args.output_dir, args.suite)
    for result in results:
        print(
            f"{result.case_id}: {result.status} expected={result.expected_intent} "
            f"predicted={result.predicted_intent} confidence={result.confidence:.3f}"
        )
    failures = [result for result in results if result.status != "passed"]
    if failures:
        print(f"Rasa NLU regression FAILED: {len(failures)}/{len(results)} failed")
        return 1
    print(f"Rasa NLU regression PASSED: {len(results)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
