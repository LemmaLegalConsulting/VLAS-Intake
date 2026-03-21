#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=True)


def _json_request(
    url: str, method: str, bearer_token: str, body: dict[str, Any] | None = None
) -> tuple[int, str]:
    data = None
    headers = {"Authorization": f"Bearer {bearer_token}"}
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            return response.getcode(), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _fetch_pinless_config(
    daily_api_key: str, phone_number: str | None
) -> dict[str, Any]:
    status, body = _json_request(
        "https://api.daily.co/v1/domain-dialin-config",
        method="GET",
        bearer_token=daily_api_key,
    )
    if status == 200:
        payload = json.loads(body)
        candidates = payload.get("data", [])
    else:
        candidates = []

    if not candidates:
        status, body = _json_request(
            "https://api.daily.co/v1",
            method="GET",
            bearer_token=daily_api_key,
        )
        if status != 200:
            raise SystemExit(f"Failed to fetch Daily config: HTTP {status}\n{body}")
        payload = json.loads(body)
        candidates = payload.get("config", {}).get("pinless_dialin", [])

    normalized_phone = _normalize_phone(phone_number) if phone_number else None
    matching_configs = []
    for candidate in candidates:
        config = candidate.get("config", candidate)
        candidate_phone = _normalize_phone(config.get("phone_number"))
        if normalized_phone is None or candidate_phone == normalized_phone:
            matching_configs.append(config)

    if not matching_configs:
        if normalized_phone:
            raise SystemExit(
                f"No pinless dial-in config found for phone number {phone_number}."
            )
        raise SystemExit(
            "No pinless dial-in config found in Daily domain configuration."
        )

    if len(matching_configs) > 1 and normalized_phone is None:
        phones = ", ".join(
            sorted(
                config.get("phone_number", "<unknown>") for config in matching_configs
            )
        )
        raise SystemExit(
            f"Multiple pinless dial-in configs found ({phones}). Pass --phone-number to select one."
        )

    return matching_configs[0]


def _normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


def _derive_call_domain(
    config: dict[str, Any], explicit_call_domain: str | None
) -> str:
    if explicit_call_domain:
        return explicit_call_domain

    sip_uri = config.get("sip_uri")
    if isinstance(sip_uri, str) and "@" in sip_uri:
        return sip_uri.split("@", 1)[0]

    raise SystemExit(
        "Could not determine callDomain from pinless dial-in config. Pass --call-domain explicitly."
    )


def _build_payload(
    config: dict[str, Any], from_number: str, call_id: str | None, call_domain: str
) -> str:
    payload = {
        "To": config["phone_number"],
        "From": from_number,
        "callId": call_id or str(uuid.uuid4()),
        "callDomain": call_domain,
    }
    return json.dumps(payload, separators=(",", ":"))


def _sign_payload(secret_b64: str, timestamp: str, body: str) -> str:
    secret = base64.b64decode(secret_b64)
    digest = hmac.new(
        secret, f"{timestamp}.{body}".encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _post_probe(
    webhook_url: str, timestamp: str, signature: str, body: str
) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(
        webhook_url,
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-pinless-timestamp": timestamp,
            "x-pinless-signature": signature,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            response_headers = {key: value for key, value in response.headers.items()}
            return response.getcode(), response_headers, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        response_headers = {key: value for key, value in exc.headers.items()}
        return exc.code, response_headers, exc.read().decode("utf-8")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Probe the Pipecat Cloud Daily dial-in webhook using the current Daily pinless config."
    )
    parser.add_argument(
        "--daily-api-key",
        help="Daily REST API key. Defaults to DAILY_API_KEY from the environment or .env.",
    )
    parser.add_argument(
        "--env-file",
        default=str(repo_root / ".env"),
        help="Path to a .env file to read when DAILY_API_KEY is not already exported.",
    )
    parser.add_argument(
        "--phone-number",
        help="Phone number to probe. Required only if the Daily domain has multiple pinless dial-in configs.",
    )
    parser.add_argument(
        "--from-number",
        default="+15555550123",
        help="Synthetic caller number to include in the probe payload.",
    )
    parser.add_argument(
        "--call-id",
        help="Explicit callId to use. Defaults to a random UUID.",
    )
    parser.add_argument(
        "--call-domain",
        help="Explicit callDomain to use. Defaults to the local part of the configured sip_uri.",
    )
    parser.add_argument(
        "--webhook-url",
        help="Override the webhook URL. Defaults to room_creation_api from the current Daily pinless config.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved request details without sending the probe.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    daily_api_key = os.getenv("DAILY_API_KEY", default=args.daily_api_key)
    if not daily_api_key:
        raise SystemExit(
            "Daily API key must be provided ('--daily-api-key') or available in the environment ('DAILY_API_KEY')."
        )

    config = _fetch_pinless_config(daily_api_key, args.phone_number)

    webhook_url = args.webhook_url or config.get("room_creation_api")
    if not webhook_url:
        raise SystemExit("Pinless config does not include room_creation_api.")

    hmac_secret = config.get("hmac")
    if not hmac_secret:
        raise SystemExit("Pinless config does not include an hmac secret.")

    call_domain = _derive_call_domain(config, args.call_domain)
    body = _build_payload(config, args.from_number, args.call_id, call_domain)
    timestamp = str(int(time.time()))
    signature = _sign_payload(hmac_secret, timestamp, body)

    print("Resolved pinless dial-in config:")
    print(
        json.dumps(
            {
                "phone_number": config.get("phone_number"),
                "sip_uri": config.get("sip_uri"),
                "room_creation_api": webhook_url,
                "callDomain": call_domain,
            },
            indent=2,
        )
    )
    print()
    print("Probe request:")
    print(
        json.dumps(
            {
                "timestamp": timestamp,
                "signature": signature,
                "body": json.loads(body),
            },
            indent=2,
        )
    )

    if args.dry_run:
        return 0

    print()
    status, headers, response_body = _post_probe(
        webhook_url, timestamp, signature, body
    )
    print(f"Response status: {status}")
    print("Response headers:")
    print(json.dumps(headers, indent=2))
    print("Response body:")
    print(response_body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
