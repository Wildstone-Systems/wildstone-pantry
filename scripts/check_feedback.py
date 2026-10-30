#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import requests


def _default_token() -> str:
    for key in ("PANTRY_AUTH_TOKEN", "AUTH_TOKEN", "SMOKE_AUTH_TOKEN"):
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return ""


def _extract_summary(message: str) -> str:
    if not message:
        return ""

    if "Summary:" in message:
        after = message.split("Summary:", 1)[1].lstrip()
        for line in after.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if cleaned == "---":
                break
            return cleaned

    return (message.splitlines()[0] if message.splitlines() else "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="List feedback items from the PantryOS API.")
    parser.add_argument("--base-url", default=os.getenv("API_BASE_URL", "http://localhost:8000").strip())
    parser.add_argument("--token", default=_default_token())
    parser.add_argument("--status", default="open", help='Filter by status (e.g. "open", "resolved", "all")')
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--json", action="store_true", help="Print raw JSON response")
    parser.add_argument("--require-api", action="store_true", help="Fail if the API is unreachable")
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    headers: dict[str, str] = {}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    params: dict[str, Any] = {"limit": max(1, min(int(args.limit), 200))}
    if args.status and args.status.strip().lower() != "all":
        params["status"] = args.status.strip()

    url = f"{args.base_url.rstrip('/')}/api/feedback"
    try:
        res = requests.get(url, headers=headers, params=params, timeout=args.timeout)
    except Exception as exc:
        msg = f"[FAIL] Unable to reach API at {args.base_url}: {exc}"
        if args.require_api:
            print(msg, file=sys.stderr)
            return 2
        print(f"{msg} (skipping)", file=sys.stderr)
        return 0

    if res.status_code == 401:
        print(
            "[FAIL] 401 Unauthorized. Provide `--token` (an admin bearer token) if auth is enabled.",
            file=sys.stderr,
        )
        return 1
    if res.status_code != 200:
        print(f"[FAIL] {res.status_code}: {res.text}", file=sys.stderr)
        return 1

    payload = res.json()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    feedback = payload.get("feedback") or []
    status_label = args.status.strip().lower() if args.status else "all"
    if status_label and status_label != "all":
        # Filter locally too so the script still works against older API versions.
        feedback = [
            row
            for row in feedback
            if str(row.get("status") or "").strip().lower() == status_label
        ]

    print(f"Feedback items: {len(feedback)} (status={status_label})")
    if not feedback:
        return 0

    for row in feedback:
        fid = row.get("id")
        category = row.get("category")
        route = row.get("route") or "-"
        status = row.get("status")
        created_at = row.get("created_at")
        summary = _extract_summary((row.get("message") or "").strip())

        print(f"- {created_at} | {status} | {category} | {route} | {fid}")
        if summary:
            print(f"  {summary}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
