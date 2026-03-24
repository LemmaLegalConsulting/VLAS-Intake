#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


def _load_env(env_file: str | None) -> None:
    if env_file:
        load_dotenv(env_file, override=False)
        return

    repo_root = Path(__file__).resolve().parents[1]
    default_env = repo_root / ".env"
    if default_env.exists():
        load_dotenv(default_env, override=False)


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def _validate_phone_number(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not E164_PATTERN.fullmatch(normalized):
        raise SystemExit(
            f"{field_name} must be in E.164 format like +14155551234. Got: {value!r}"
        )
    return normalized


def _post_json(
    url: str, api_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, str], str]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request) as response:
            headers = {key: value for key, value in response.headers.items()}
            return response.getcode(), headers, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        headers = {key: value for key, value in exc.headers.items()}
        return exc.code, headers, exc.read().decode("utf-8")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Send a test SMS through Dialpad using DIALPAD_API_KEY and "
            "DIALPAD_SMS_NUMBER from the local .env file."
        )
    )
    parser.add_argument(
        "to_number",
        help="Destination phone number in E.164 format, for example +14155551234.",
    )
    parser.add_argument("text", help="Message body to send.")
    parser.add_argument(
        "--from-number",
        help="Override DIALPAD_SMS_NUMBER from the environment.",
    )
    parser.add_argument(
        "--base-url",
        default="https://dialpad.com",
        help="Dialpad API base URL. Defaults to https://dialpad.com.",
    )
    parser.add_argument(
        "--infer-country-code",
        action="store_true",
        help="Set infer_country_code=true on the Dialpad request.",
    )
    parser.add_argument(
        "--env-file",
        default=str(repo_root / ".env"),
        help="Path to the .env file to load. Defaults to intake-bot/.env.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request payload without sending the SMS.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _load_env(args.env_file)

    from_number = args.from_number or _require_env("DIALPAD_SMS_NUMBER")

    from_number = _validate_phone_number(from_number, "from_number")
    to_number = _validate_phone_number(args.to_number, "to_number")
    text = args.text.strip()
    if not text:
        raise SystemExit("text must not be empty")

    payload: dict[str, Any] = {
        "from_number": from_number,
        "to_numbers": [to_number],
        "text": text,
    }
    if args.infer_country_code:
        payload["infer_country_code"] = True

    if args.dry_run:
        print("Dry run. Dialpad request payload:")
        print(json.dumps(payload, indent=2))
        print(f"POST {args.base_url.rstrip('/')}/api/v2/sms")
        return 0

    api_key = _require_env("DIALPAD_API_KEY")
    status, headers, body = _post_json(
        f"{args.base_url.rstrip('/')}/api/v2/sms",
        api_key=api_key,
        payload=payload,
    )

    print(f"HTTP {status}")
    content_type = headers.get("Content-Type", "")
    if body:
        if "json" in content_type.lower():
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                print(body)
            else:
                print(json.dumps(parsed, indent=2, sort_keys=True))
        else:
            print(body)

    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
