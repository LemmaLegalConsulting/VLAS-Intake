import re
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
    text_en: str
    text_es: str
    sms_en: str
    sms_es: str

    def spoken_text(self, language: str) -> str:
        return (
            self.spoken_es if language.strip().lower() == "spanish" else self.spoken_en
        )

    def text_delivery_text(self, language: str) -> str:
        return self.text_es if language.strip().lower() == "spanish" else self.text_en

    def sms_text(self, language: str) -> str:
        return self.sms_es if language.strip().lower() == "spanish" else self.sms_en


def _load_referral_content(path: Path | None = None) -> dict[str, ReferralContent]:
    referral_path = path or (Path(DATA_DIR) / "referral_content.yml")
    with open(referral_path, encoding="utf-8") as handle:
        raw_content: dict[str, dict[str, str]] = yaml.safe_load(handle)

    return {key: ReferralContent(**value) for key, value in raw_content.items()}


_REFERRAL_CONTENT = _load_referral_content()

GENERAL_REFERRAL = _REFERRAL_CONTENT["general_referral"]
CASE_TYPE_REFERRAL = _REFERRAL_CONTENT["case_type_referral"]
OVER_LIMIT_REFERRAL = _REFERRAL_CONTENT["over_limit_referral"]


class SMS:
    E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")

    def __init__(
        self,
        api_key: str | None = None,
        from_number: str | None = None,
        base_url: str = "https://dialpad.com",
    ):
        self.api_key = (api_key or get_ev("DIALPAD_API_KEY")).strip()
        self.from_number = (from_number or get_ev("DIALPAD_SMS_NUMBER")).strip()
        self.base_url = base_url.rstrip("/")

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

        async with aiohttp.ClientSession() as session:
            async with session.post(
                request["url"],
                json=request["payload"],
                headers=request["headers"],
            ) as response:
                content_type = response.headers.get("Content-Type", "")
                if "json" in content_type.lower():
                    body: dict[str, Any] | str = await response.json(content_type=None)
                else:
                    body = await response.text()

                if response.status >= 400:
                    raise RuntimeError(
                        f"Dialpad SMS failed with HTTP {response.status}: {body}"
                    )

                return {"status": response.status, "body": body}
