import asyncio
import math
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import yaml
from intake_bot.utils.ev import get_ev
from intake_bot.utils.globals import DATA_DIR


@dataclass(frozen=True)
class ReferralContent:
    spoken_en: str
    spoken_es: str
    phone_en: str
    phone_es: str
    text_en: str
    text_es: str
    sms_en: str
    sms_es: str

    def spoken_text(self, language: str) -> str:
        return (
            self.spoken_es if language.strip().lower() == "spanish" else self.spoken_en
        )

    def phone_delivery_text(self, language: str) -> str:
        return self.phone_es if language.strip().lower() == "spanish" else self.phone_en

    def text_delivery_text(self, language: str) -> str:
        return self.text_es if language.strip().lower() == "spanish" else self.text_en

    def sms_text(self, language: str) -> str:
        return self.sms_es if language.strip().lower() == "spanish" else self.sms_en


def _load_referral_content(path: Path | None = None) -> ReferralContent:
    referral_path = path or (Path(DATA_DIR) / "referral_content.yml")
    with open(referral_path, encoding="utf-8") as handle:
        raw_content: dict[str, dict[str, str]] = yaml.safe_load(handle)

    return ReferralContent(**raw_content["referral"])


REFERRAL = _load_referral_content()


class SMS:
    E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
    REQUEST_TIMEOUT = 15.0
    MAX_RETRIES = 3
    BASE_RETRY_DELAY = 1.0
    MAX_TOTAL_DURATION = 30.0
    MAX_RETRY_AFTER = 30.0

    def __init__(
        self,
        api_key: str | None = None,
        from_number: str | None = None,
        base_url: str = "https://dialpad.com",
        _sleep=None,
        _now=None,
    ):
        self.api_key = (
            (get_ev("DIALPAD_API_KEY") or "").strip()
            if api_key is None
            else api_key.strip()
        )
        self.from_number = (
            (get_ev("DIALPAD_SMS_NUMBER") or "").strip()
            if from_number is None
            else from_number.strip()
        )
        self.base_url = (base_url or "").rstrip("/")
        self._sleep = _sleep if _sleep is not None else asyncio.sleep
        self._now = _now if _now is not None else time.monotonic

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.from_number)

    def _validate_phone_number(self, value: str, field_name: str) -> str:
        normalized = value.strip()
        if not self.E164_PATTERN.fullmatch(normalized):
            raise ValueError(
                f"{field_name} must be in E.164 format like +14155551234. Got: {value!r}"
            )
        return normalized

    def _build_request(
        self,
        to_number: str,
        text: str,
        *,
        infer_country_code: bool = False,
    ) -> dict[str, Any]:
        if not self.is_configured:
            raise ValueError(
                "Dialpad SMS is not configured. DIALPAD_API_KEY and DIALPAD_SMS_NUMBER must both be set."
            )

        from_number = self._validate_phone_number(self.from_number, "from_number")
        validated_to_number = self._validate_phone_number(to_number, "to_number")
        message_text = text.strip()
        if not message_text:
            raise ValueError("SMS text must not be empty.")

        payload: dict[str, Any] = {
            "from_number": from_number,
            "to_numbers": [validated_to_number],
            "text": message_text,
        }
        if infer_country_code:
            payload["infer_country_code"] = True

        return {
            "url": f"{self.base_url}/api/v2/sms",
            "payload": payload,
            "headers": {
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        }

    async def send(
        self,
        to_number: str,
        text: str,
        *,
        infer_country_code: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        request = self._build_request(
            to_number,
            text,
            infer_country_code=infer_country_code,
        )

        if dry_run:
            return {"dry_run": True, **request}

        deadline = self._now() + self.MAX_TOTAL_DURATION
        last_exception: Exception | None = None

        for attempt in range(self.MAX_RETRIES):
            remaining = deadline - self._now()
            if remaining <= 0:
                raise RuntimeError(
                    f"Dialpad SMS retry deadline exceeded after {self.MAX_TOTAL_DURATION}s"
                )

            request_timeout = min(self.REQUEST_TIMEOUT, remaining)
            timeout = aiohttp.ClientTimeout(total=request_timeout)

            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        request["url"],
                        json=request["payload"],
                        headers=request["headers"],
                    ) as response:
                        # Retry on 429 (rate limit) and transient 5xx
                        if response.status == 429:
                            response_body = await response.text()
                            if attempt < self.MAX_RETRIES - 1:
                                delay = self.BASE_RETRY_DELAY * (
                                    2**attempt
                                ) + random.uniform(0, 0.5)
                                retry_after = response.headers.get("Retry-After")
                                if retry_after is not None:
                                    try:
                                        server_delay = float(retry_after)
                                        if server_delay > 0 and math.isfinite(
                                            server_delay
                                        ):
                                            capped = min(
                                                server_delay, self.MAX_RETRY_AFTER
                                            )
                                            delay = max(
                                                capped + random.uniform(0, 0.5),
                                                delay,
                                            )
                                    except (ValueError, TypeError):
                                        pass
                                remaining = deadline - self._now()
                                if remaining <= 0:
                                    raise RuntimeError(
                                        "Dialpad SMS retry deadline exceeded"
                                    )
                                delay = min(delay, remaining)
                                await self._sleep(delay)
                                continue
                            raise RuntimeError(
                                f"Dialpad SMS failed with HTTP {response.status}: {response_body}"
                            )

                        if response.status >= 500:
                            response_body = await response.text()
                            if attempt < self.MAX_RETRIES - 1:
                                delay = self.BASE_RETRY_DELAY * (
                                    2**attempt
                                ) + random.uniform(0, 0.5)
                                remaining = deadline - self._now()
                                if remaining <= 0:
                                    raise RuntimeError(
                                        "Dialpad SMS retry deadline exceeded"
                                    )
                                delay = min(delay, remaining)
                                await self._sleep(delay)
                                continue
                            raise RuntimeError(
                                f"Dialpad SMS failed with HTTP {response.status}: {response_body}"
                            )

                        # 3xx redirects — treat as immediate failure
                        if 300 <= response.status < 400:
                            response_body = await response.text()
                            raise RuntimeError(
                                f"Dialpad SMS failed with HTTP {response.status}: {response_body}"
                            )

                        content_type = response.headers.get("Content-Type", "")
                        if "json" in content_type.lower():
                            body: dict[str, Any] | str = await response.json(
                                content_type=None
                            )
                        else:
                            body = await response.text()

                        if response.status >= 400:
                            raise RuntimeError(
                                f"Dialpad SMS failed with HTTP {response.status}: {body}"
                            )

                        return {"status": response.status, "body": body}

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.BASE_RETRY_DELAY * (2**attempt) + random.uniform(
                        0, 0.5
                    )
                    remaining = deadline - self._now()
                    if remaining <= 0:
                        raise RuntimeError("Dialpad SMS retry deadline exceeded") from e
                    delay = min(delay, remaining)
                    await self._sleep(delay)
                    continue
                raise RuntimeError(
                    f"Dialpad SMS failed after {self.MAX_RETRIES} attempts: {last_exception}"
                ) from last_exception
