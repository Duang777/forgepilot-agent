from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

# Ensure local package imports work when running via `python scripts/...`.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from forgepilot_api.services.codex_config_service import load_codex_runtime_config


def _read_sse_events(response: httpx.Response) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in response.iter_lines():
        if not line:
            continue
        text = line.decode() if isinstance(line, bytes) else line
        if not text.startswith("data: "):
            continue
        try:
            payload = json.loads(text[6:])
        except json.JSONDecodeError:
            continue
        events.append(payload)
    return events


def _resolve_model_config(args: argparse.Namespace) -> dict[str, str] | None:
    explicit_key = (args.api_key or "").strip()
    explicit_model = (args.model or "").strip()
    explicit_base_url = (args.base_url or "").strip()
    explicit_api_type = (args.api_type or "").strip()

    if explicit_key and explicit_model:
        return {
            "apiKey": explicit_key,
            "model": explicit_model,
            "baseUrl": explicit_base_url,
            "apiType": explicit_api_type or ("anthropic-messages" if "claude" in explicit_model.lower() else "openai-completions"),
        }

    codex_cfg = load_codex_runtime_config()
    api_key = str(codex_cfg.get("apiKey") or "").strip()
    model = str(codex_cfg.get("model") or "").strip()
    base_url = str(codex_cfg.get("baseUrl") or "").strip()
    api_type = str(codex_cfg.get("apiType") or "").strip()
    if not api_key or not model:
        return None
    return {
        "apiKey": api_key,
        "model": model,
        "baseUrl": base_url,
        "apiType": api_type or ("anthropic-messages" if "claude" in model.lower() else "openai-completions"),
    }


def _contains_error(events: list[dict[str, Any]]) -> bool:
    return any(event.get("type") == "error" for event in events)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run API smoke checks for ForgePilot Agent.")
    parser.add_argument("--base-url", default="http://127.0.0.1:2026", help="API base URL.")
    parser.add_argument("--timeout-sec", type=int, default=90, help="Request timeout in seconds.")
    parser.add_argument("--api-key", default="", help="Override API key for smoke run.")
    parser.add_argument("--model", default="", help="Override model for smoke run.")
    parser.add_argument("--base-url-override", default="", help="Override provider base URL for smoke run.")
    parser.add_argument(
        "--api-type",
        default="",
        choices=["", "openai-completions", "anthropic-messages"],
        help="Override API type for smoke run.",
    )
    parser.add_argument("--require-model", action="store_true", help="Fail if no model config is available.")
    parser.add_argument("--require-plan", action="store_true", help="Fail if /agent/plan does not return a plan event.")
    args = parser.parse_args()

    timeout = httpx.Timeout(timeout=args.timeout_sec)
    base_url = args.base_url.rstrip("/")
    model_cfg = _resolve_model_config(
        argparse.Namespace(
            api_key=args.api_key,
            model=args.model,
            base_url=args.base_url_override,
            api_type=args.api_type,
        )
    )

    if args.require_model and not model_cfg:
        raise SystemExit("[ForgePilot Smoke] model config is required but unavailable.")

    with httpx.Client(timeout=timeout) as client:
        health = client.get(f"{base_url}/health")
        health.raise_for_status()
        print(f"[ForgePilot Smoke] health OK: {health.status_code}")

        plan_payload: dict[str, Any] = {"prompt": "Build a small coding task with at least two concrete execution steps."}
        if model_cfg:
            plan_payload["modelConfig"] = {
                "apiKey": model_cfg["apiKey"],
                "model": model_cfg["model"],
                "apiType": model_cfg["apiType"],
                "baseUrl": model_cfg["baseUrl"],
            }

        with client.stream("POST", f"{base_url}/agent/plan", json=plan_payload) as response:
            response.raise_for_status()
            plan_events = _read_sse_events(response)
        plan_types = [event.get("type") for event in plan_events]
        print(f"[ForgePilot Smoke] /agent/plan events: {plan_types}")

        if not plan_events:
            raise SystemExit("[ForgePilot Smoke] no SSE events from /agent/plan.")
        if plan_events[-1].get("type") != "done":
            raise SystemExit("[ForgePilot Smoke] /agent/plan stream did not terminate with done.")

        if _contains_error(plan_events):
            first_error = next(event for event in plan_events if event.get("type") == "error")
            message = str(first_error.get("message") or "")
            if message == "__MODEL_NOT_CONFIGURED__" and not args.require_model:
                print("[ForgePilot Smoke] model not configured; skipping execute-chain check.")
                return
            raise SystemExit(f"[ForgePilot Smoke] /agent/plan error: {message}")

        plan_event = next((event for event in plan_events if event.get("type") == "plan"), None)
        if not plan_event:
            if args.require_plan:
                raise SystemExit("[ForgePilot Smoke] /agent/plan did not emit a plan event.")
            print("[ForgePilot Smoke] no plan event returned (direct answer mode); smoke check passed.")
            return

        plan = plan_event.get("plan") or {}
        plan_id = str(plan.get("id") or "").strip()
        if not plan_id:
            raise SystemExit("[ForgePilot Smoke] plan event missing plan.id")

        execute_payload: dict[str, Any] = {
            "planId": plan_id,
            "prompt": "Execute the approved plan.",
            "taskId": f"smoke-task-{uuid4().hex[:8]}",
        }
        if model_cfg:
            execute_payload["modelConfig"] = {
                "apiKey": model_cfg["apiKey"],
                "model": model_cfg["model"],
                "apiType": model_cfg["apiType"],
                "baseUrl": model_cfg["baseUrl"],
            }

        with client.stream("POST", f"{base_url}/agent/execute", json=execute_payload) as response:
            response.raise_for_status()
            exec_events = _read_sse_events(response)

        exec_types = [event.get("type") for event in exec_events]
        print(f"[ForgePilot Smoke] /agent/execute events: {exec_types}")

        if not exec_events:
            raise SystemExit("[ForgePilot Smoke] no SSE events from /agent/execute.")
        if exec_events[0].get("type") != "session":
            raise SystemExit("[ForgePilot Smoke] /agent/execute did not start with session event.")
        if exec_events[-1].get("type") != "done":
            raise SystemExit("[ForgePilot Smoke] /agent/execute stream did not terminate with done.")
        if _contains_error(exec_events):
            first_error = next(event for event in exec_events if event.get("type") == "error")
            raise SystemExit(f"[ForgePilot Smoke] /agent/execute error: {first_error.get('message')}")

        print("[ForgePilot Smoke] plan -> execute -> SSE chain passed.")


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as exc:
        raise SystemExit(f"[ForgePilot Smoke] HTTP error: {exc}") from exc

