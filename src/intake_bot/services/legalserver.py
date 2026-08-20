import asyncio
import json
import random
import re
import time as _time_module
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Hashable, Optional

import aiohttp
from intake_bot.models.legalserver import (
    AdditionalNamePayload,
    AdversePartyPayload,
    IncomePayload,
    LegalServerCreateMatterPayload,
    LegalServerOverall,
    LegalServerPersistenceResult,
    MatterLookupOutcome,
    MatterLookupResult,
    NotePayload,
    OperationKind,
    OperationOutcome,
    RecordResult,
)
from intake_bot.utils.ev import ev_is_true, require_ev
from intake_bot.utils.globals import PROJECT_ROOT
from loguru import logger
from pydantic import ValidationError


def _now() -> float:
    return _time_module.monotonic()


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


LEGALSERVER_TIMEOUT = 300  # 5 minutes absolute deadline


def _legalserver_api_base_url() -> str:
    return f"""https://{require_ev("LEGAL_SERVER_SUBDOMAIN")}.legalserver.org/api/v2"""


def _legalserver_headers() -> dict[str, str]:
    return {
        "Authorization": f"""Bearer {require_ev("LEGAL_SERVER_BEARER_TOKEN")}""",
        "Content-Type": "application/json",
        "Accept": "application/json, text/html",
    }


def _finalize_response(response: aiohttp.ClientResponse) -> None:
    response.release()


def _is_transient(status: int) -> bool:
    return status in (408, 429) or status >= 500


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _retry_delay(attempt: int, retry_after: float | None = None) -> float:
    if retry_after is not None:
        return max(1.0, min(retry_after + random.uniform(0, 1.0), 60.0))
    delay = min(1.0 * (2**attempt) + random.uniform(0, 0.5), 60.0)
    return delay


class _HttpOutcome(str, Enum):
    SUCCESS = "success"
    DETERMINISTIC_FAILURE = "deterministic_failure"
    TRANSIENT = "transient"
    MALFORMED = "malformed"


@dataclass
class _HttpResult:
    outcome: _HttpOutcome
    data: dict[str, Any] | None = None
    status: int | None = None
    retry_after: float | None = None


async def _request_once(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    deadline: float,
    require_json: bool = True,
) -> _HttpResult:
    """Perform one deadline-bounded request without logging sensitive data."""
    if _now() >= deadline:
        return _HttpResult(_HttpOutcome.TRANSIENT)
    remaining = deadline - _now()
    request_timeout = min(30.0, remaining)
    try:
        async with session.request(
            method,
            url,
            json=json,
            params=params,
            headers=_legalserver_headers(),
            timeout=aiohttp.ClientTimeout(total=request_timeout),
        ) as response:
            status = response.status
            if 200 <= status < 300:
                if not require_json:
                    return _HttpResult(_HttpOutcome.SUCCESS, status=status)
                try:
                    data = await response.json(content_type=None)
                except Exception:
                    return _HttpResult(_HttpOutcome.MALFORMED, status=status)
                if not isinstance(data, dict):
                    return _HttpResult(_HttpOutcome.MALFORMED, status=status)
                return _HttpResult(_HttpOutcome.SUCCESS, data=data, status=status)
            if _is_transient(status):
                return _HttpResult(
                    _HttpOutcome.TRANSIENT,
                    status=status,
                    retry_after=_parse_retry_after(response.headers.get("Retry-After")),
                )
            return _HttpResult(_HttpOutcome.DETERMINISTIC_FAILURE, status=status)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return _HttpResult(_HttpOutcome.TRANSIENT)


async def _sleep_for_retry(
    attempt: int, deadline: float, retry_after: float | None = None
) -> bool:
    remaining = deadline - _now()
    if remaining <= 0:
        return False
    delay = min(_retry_delay(attempt, retry_after), remaining)
    if delay <= 0:
        return False
    await _sleep(delay)
    return _now() < deadline


async def _request_with_retry(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    deadline: float,
    require_json: bool = True,
) -> _HttpResult:
    attempt = 0
    while _now() < deadline:
        result = await _request_once(
            session,
            method,
            url,
            json=json,
            params=params,
            deadline=deadline,
            require_json=require_json,
        )
        if result.outcome != _HttpOutcome.TRANSIENT:
            return result
        if not await _sleep_for_retry(attempt, deadline, result.retry_after):
            break
        attempt += 1
    return _HttpResult(_HttpOutcome.TRANSIENT)


async def _post_once(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    json: dict | None = None,
    deadline: float,
) -> tuple[Any | None, bool]:
    """Compatibility wrapper for callers that still need one JSON attempt."""
    result = await _request_once(
        session, method, url, json=json, deadline=deadline, require_json=True
    )
    if result.outcome == _HttpOutcome.SUCCESS:
        return result.data, False
    return None, result.outcome in (_HttpOutcome.TRANSIENT, _HttpOutcome.MALFORMED)


async def _find_matter_by_external_id(
    session: aiohttp.ClientSession, external_id: str, deadline: float
) -> MatterLookupOutcome:
    """Look up a matter by external_id.

    Returns:
      FOUND + UUID  – matter exists and UUID was extracted.
      NOT_FOUND     – API returned a valid explicitly empty collection
                      (200 with ``{"data": []}`` or equivalent).
      INDETERMINATE – lookup itself failed, response was malformed,
                      data was missing/wrong type, or entries lacked
                      matter_uuid. Caller must NOT treat this as absence.
    """
    url = f"{_legalserver_api_base_url()}/matters"
    params = {"external_id": external_id, "page_size": 1, "results": "full"}
    result = await _request_with_retry(
        session, "GET", url, params=params, deadline=deadline
    )
    if result.outcome != _HttpOutcome.SUCCESS or result.data is None:
        return MatterLookupOutcome(MatterLookupResult.INDETERMINATE)

    raw = result.data.get("data")
    if not isinstance(raw, list):
        return MatterLookupOutcome(MatterLookupResult.INDETERMINATE)
    if not raw:
        total_records = result.data.get("total_records")
        if isinstance(total_records, int) and total_records > 0:
            return MatterLookupOutcome(MatterLookupResult.INDETERMINATE)
        return MatterLookupOutcome(MatterLookupResult.NOT_FOUND)

    for entry in raw:
        if not isinstance(entry, dict):
            return MatterLookupOutcome(MatterLookupResult.INDETERMINATE)
        returned_external_id = entry.get("external_id")
        if returned_external_id is not None and returned_external_id != external_id:
            continue
        matter_uuid = entry.get("matter_uuid")
        if isinstance(matter_uuid, str) and matter_uuid:
            return MatterLookupOutcome(MatterLookupResult.FOUND, matter_uuid)
    return MatterLookupOutcome(MatterLookupResult.INDETERMINATE)


async def _reconcile_matter(
    session: aiohttp.ClientSession,
    call_id: str | None,
    deadline: float,
) -> str | None:
    if not call_id:
        return None
    lo = await _find_matter_by_external_id(session, call_id, deadline)
    if lo.result == MatterLookupResult.FOUND:
        return lo.matter_uuid
    return None


async def _create_matter_guarded(
    session: aiohttp.ClientSession,
    payload: dict,
    call_id: str | None,
    deadline: float,
) -> str | None:
    if not call_id:
        return None
    lo = await _find_matter_by_external_id(session, call_id, deadline)
    if lo.result == MatterLookupResult.FOUND:
        return lo.matter_uuid
    if lo.result == MatterLookupResult.INDETERMINATE:
        return None

    create_url = f"{_legalserver_api_base_url()}/matters"

    attempt = 0
    while _now() < deadline:
        result = await _request_once(
            session,
            "POST",
            create_url,
            json=payload,
            deadline=deadline,
            require_json=True,
        )
        if result.outcome == _HttpOutcome.SUCCESS and result.data is not None:
            matter_data = result.data.get("data", result.data)
            matter_uuid = (
                matter_data.get("matter_uuid")
                if isinstance(matter_data, dict)
                else None
            )
            if isinstance(matter_uuid, str) and matter_uuid:
                return matter_uuid

        lookup = await _find_matter_by_external_id(session, call_id, deadline)
        if lookup.result == MatterLookupResult.FOUND:
            return lookup.matter_uuid
        if lookup.result == MatterLookupResult.INDETERMINATE:
            return None
        if result.outcome == _HttpOutcome.DETERMINISTIC_FAILURE:
            return None
        if not await _sleep_for_retry(attempt, deadline, result.retry_after):
            break
        attempt += 1

    return await _reconcile_matter(session, call_id, deadline)


class _ChildPresence(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    INDETERMINATE = "indeterminate"


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).casefold()


def _normalize_amount(value: Any) -> str:
    if value is None:
        return ""
    cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    try:
        return str(Decimal(cleaned).normalize())
    except (InvalidOperation, ValueError):
        return _normalize_text(value)


def _normalize_phone(value: Any) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


def _normalize_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes"}
    return bool(value)


def _lookup_key(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        return "name", _normalize_text(value)
    for key in ("lookup_value_name", "name"):
        if value.get(key) is not None:
            return "name", _normalize_text(value[key])
    for key in ("lookup_value_uuid", "uuid", "lookup_value_id", "id"):
        if value.get(key) is not None:
            return "id", _normalize_text(value[key])
    return "name", ""


def _income_fingerprint(record: dict[str, Any]) -> Hashable:
    return (
        _lookup_key(record.get("type")),
        _normalize_amount(record.get("amount")),
        _normalize_text(record.get("period")),
        _normalize_bool(record.get("exclude"), False),
        _normalize_text(record.get("notes")),
    )


def _additional_name_fingerprint(record: dict[str, Any]) -> Hashable:
    return (
        _normalize_text(record.get("first")),
        _normalize_text(record.get("middle")),
        _normalize_text(record.get("last")),
        _normalize_text(record.get("suffix")),
        _lookup_key(record.get("type")),
    )


def _adverse_party_fingerprint(record: dict[str, Any]) -> Hashable:
    return (
        _normalize_text(record.get("first")),
        _normalize_text(record.get("middle")),
        _normalize_text(record.get("last")),
        _normalize_text(record.get("suffix")),
        _normalize_text(record.get("organization_name")),
        _normalize_text(record.get("date_of_birth") or record.get("dob")),
        _normalize_phone(record.get("phone_home")),
        _normalize_phone(record.get("phone_business")),
        _normalize_phone(record.get("phone_mobile")),
        _normalize_phone(record.get("phone_fax")),
        _normalize_bool(record.get("active"), True),
    )


def _note_fingerprint(record: dict[str, Any]) -> Hashable:
    body = str(record.get("body") or "").replace("\r\n", "\n").replace("\r", "\n")
    return (
        _normalize_text(record.get("subject")),
        body.strip(),
        _lookup_key(record.get("note_type")),
        _normalize_bool(record.get("is_html"), False),
        _normalize_bool(record.get("allow_etransfer"), True),
        _normalize_bool(record.get("active"), True),
    )


_CHILD_FINGERPRINTS: dict[str, Callable[[dict[str, Any]], Hashable]] = {
    "incomes": _income_fingerprint,
    "additional_names": _additional_name_fingerprint,
    "adverse_parties": _adverse_party_fingerprint,
    "notes": _note_fingerprint,
}


async def _list_child_records(
    session: aiohttp.ClientSession,
    matter_uuid: str,
    resource: str,
    deadline: float,
) -> list[dict[str, Any]] | None:
    """Return an authoritative full collection, or None when it cannot be proven."""
    url = f"{_legalserver_api_base_url()}/matters/{matter_uuid}/{resource}"
    page_number = 1
    page_size = 100
    records: list[dict[str, Any]] = []
    seen_full_pages: set[str] = set()
    while _now() < deadline:
        result = await _request_with_retry(
            session,
            "GET",
            url,
            params={"page_number": page_number, "page_size": page_size},
            deadline=deadline,
        )
        if result.outcome != _HttpOutcome.SUCCESS or result.data is None:
            return None
        page = result.data.get("data")
        if not isinstance(page, list) or any(
            not isinstance(item, dict) for item in page
        ):
            return None
        records.extend(page)

        total_pages = result.data.get("total_number_of_pages")
        if isinstance(total_pages, int):
            if total_pages < 0 or page_number >= total_pages:
                return records
        elif len(page) < page_size:
            return records

        page_signature = json.dumps(page, sort_keys=True, default=str)
        if page_signature in seen_full_pages:
            return None
        seen_full_pages.add(page_signature)
        page_number += 1
    return None


@dataclass
class _CollectionState:
    records: list[dict[str, Any]]
    consumed: Counter[Hashable] = field(default_factory=Counter)


class _ChildCollectionCache:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        matter_uuid: str,
        deadline: float,
    ) -> None:
        self.session = session
        self.matter_uuid = matter_uuid
        self.deadline = deadline
        self._collections: dict[str, _CollectionState] = {}

    async def _load(
        self, resource: str, *, refresh: bool = False
    ) -> _CollectionState | None:
        if not refresh and resource in self._collections:
            return self._collections[resource]
        records = await _list_child_records(
            self.session, self.matter_uuid, resource, self.deadline
        )
        if records is None:
            return None
        consumed = self._collections.get(resource, _CollectionState([])).consumed
        state = _CollectionState(records=records, consumed=consumed)
        self._collections[resource] = state
        return state

    async def claim(
        self,
        resource: str,
        payload: dict[str, Any],
        *,
        refresh: bool = False,
    ) -> _ChildPresence:
        state = await self._load(resource, refresh=refresh)
        if state is None:
            return _ChildPresence.INDETERMINATE
        fingerprint = _CHILD_FINGERPRINTS[resource](payload)
        available = sum(
            1
            for record in state.records
            if _CHILD_FINGERPRINTS[resource](record) == fingerprint
        )
        if available > state.consumed[fingerprint]:
            state.consumed[fingerprint] += 1
            return _ChildPresence.FOUND
        return _ChildPresence.NOT_FOUND

    def record_created(self, resource: str, payload: dict[str, Any]) -> None:
        state = self._collections.get(resource)
        if state is None:
            state = _CollectionState([])
            self._collections[resource] = state
        fingerprint = _CHILD_FINGERPRINTS[resource](payload)
        state.records.append(payload)
        state.consumed[fingerprint] += 1

    def has_upsert_conflict(
        self,
        resource: str,
        payload: dict[str, Any],
        match_fields: tuple[str, ...],
    ) -> bool:
        state = self._collections.get(resource)
        if state is None:
            return False
        selector_fields = tuple(
            name for name in match_fields if payload.get(name) is not None
        )
        target_match = _upsert_key(resource, payload, selector_fields)
        target_fingerprint = _CHILD_FINGERPRINTS[resource](payload)
        return any(
            _upsert_key(resource, record, selector_fields) == target_match
            and _CHILD_FINGERPRINTS[resource](record) != target_fingerprint
            for record in state.records
        )


def _canonical_match_value(value: Any) -> Hashable:
    if isinstance(value, dict):
        return tuple(
            sorted((key, _canonical_match_value(item)) for key, item in value.items())
        )
    if isinstance(value, list):
        return tuple(_canonical_match_value(item) for item in value)
    if isinstance(value, str):
        return _normalize_text(value)
    return value


_PERIOD_COUNTS = {
    "annually": 1,
    "quarterly": 4,
    "monthly": 12,
    "semi-monthly": 24,
    "biweekly": 26,
    "weekly": 52,
    "1": 1,
    "4": 4,
    "12": 12,
    "24": 24,
    "26": 26,
    "52": 52,
}


def _period_count(value: Any) -> int | None:
    return _PERIOD_COUNTS.get(_normalize_text(value))


def _selector_value(resource: str, name: str, value: Any) -> Any:
    if resource == "incomes" and name == "amount":
        normalized = _normalize_amount(value)
        try:
            amount = Decimal(normalized)
        except InvalidOperation:
            return value
        return int(amount) if amount == amount.to_integral() else float(amount)
    if resource == "incomes" and name == "period":
        return _period_count(value)
    if (resource, name) in {
        ("incomes", "type"),
        ("additional_names", "type"),
        ("notes", "note_type"),
    }:
        if isinstance(value, dict):
            for key in (
                "lookup_value_name",
                "name",
                "lookup_value_uuid",
                "uuid",
                "lookup_value_id",
                "id",
            ):
                if value.get(key) is not None:
                    return str(value[key])
        return str(value)
    return value


def _match_key_value(resource: str, name: str, value: Any) -> Hashable:
    if resource == "incomes" and name == "amount":
        return _normalize_amount(value)
    if resource == "incomes" and name == "period":
        return _period_count(value)
    if (resource, name) in {
        ("incomes", "type"),
        ("additional_names", "type"),
        ("notes", "note_type"),
    }:
        return _lookup_key(value)
    if name.startswith("phone_"):
        return _normalize_phone(value)
    if name == "active":
        return _normalize_bool(value, True)
    if isinstance(value, str):
        return _normalize_text(value)
    return _canonical_match_value(value)


def _upsert_key(
    resource: str, payload: dict[str, Any], fields: tuple[str, ...]
) -> Hashable:
    return tuple(
        (name, _match_key_value(resource, name, payload[name]))
        for name in fields
        if payload.get(name) is not None
    )


def _with_upsert(
    resource: str, payload: dict[str, Any], match_fields: tuple[str, ...]
) -> dict[str, Any]:
    match = {
        name: _selector_value(resource, name, payload[name])
        for name in match_fields
        if payload.get(name) is not None
    }
    match = {name: value for name, value in match.items() if value is not None}
    if not match:
        return payload
    return {**payload, "update": match, "update_data": dict(payload)}


def _is_resolved(outcome: OperationOutcome) -> bool:
    return outcome in (
        OperationOutcome.SUCCESS,
        OperationOutcome.SKIPPED,
        OperationOutcome.RECOVERED,
    )


async def _ensure_child_record(
    cache: _ChildCollectionCache,
    resource: str,
    payload: dict[str, Any],
    kind: OperationKind,
    description: str,
    fallback_content: str,
    match_fields: tuple[str, ...],
    *,
    allow_upsert: bool = True,
    update_on_conflict: bool = False,
) -> RecordResult:
    presence = await cache.claim(resource, payload)
    if presence == _ChildPresence.FOUND:
        return RecordResult(kind, OperationOutcome.RECOVERED, description)
    if presence == _ChildPresence.INDETERMINATE:
        logger.warning("LS child collection is indeterminate: resource={}", resource)
        return RecordResult(
            kind,
            OperationOutcome.AMBIGUOUS,
            description,
            _fallback_content=fallback_content,
        )

    if (
        cache.has_upsert_conflict(resource, payload, match_fields)
        and not update_on_conflict
    ):
        allow_upsert = False
    request_payload = (
        _with_upsert(resource, payload, match_fields) if allow_upsert else payload
    )
    url = f"{_legalserver_api_base_url()}/matters/{cache.matter_uuid}/{resource}"
    attempt = 0
    while _now() < cache.deadline:
        result = await _request_once(
            cache.session,
            "POST",
            url,
            json=request_payload,
            deadline=cache.deadline,
            require_json=False,
        )
        if result.outcome == _HttpOutcome.SUCCESS:
            cache.record_created(resource, payload)
            return RecordResult(kind, OperationOutcome.SUCCESS, description)
        if result.outcome == _HttpOutcome.DETERMINISTIC_FAILURE:
            return RecordResult(
                kind,
                OperationOutcome.FAILED,
                description,
                _fallback_content=fallback_content,
            )

        presence = await cache.claim(resource, payload, refresh=True)
        if presence == _ChildPresence.FOUND:
            return RecordResult(kind, OperationOutcome.RECOVERED, description)
        if presence == _ChildPresence.INDETERMINATE:
            logger.warning("LS child reconciliation failed: resource={}", resource)
            return RecordResult(
                kind,
                OperationOutcome.AMBIGUOUS,
                description,
                _fallback_content=fallback_content,
            )
        if not allow_upsert:
            logger.warning(
                "LS child retry is unsafe without a unique match: resource={}", resource
            )
            return RecordResult(
                kind,
                OperationOutcome.AMBIGUOUS,
                description,
                _fallback_content=fallback_content,
            )
        if not await _sleep_for_retry(attempt, cache.deadline, result.retry_after):
            break
        attempt += 1

    return RecordResult(
        kind,
        OperationOutcome.AMBIGUOUS,
        description,
        _fallback_content=fallback_content,
    )


def _cache_for(
    session: aiohttp.ClientSession,
    matter_uuid: str,
    deadline: float,
    cache: _ChildCollectionCache | None,
) -> _ChildCollectionCache:
    return cache or _ChildCollectionCache(session, matter_uuid, deadline)


async def _post_fallback_note(
    session: aiohttp.ClientSession,
    matter_uuid: str,
    unresolved_content: list[str],
    deadline: float,
    cache: _ChildCollectionCache | None = None,
) -> bool:
    if not unresolved_content:
        return False
    body = "\n\n".join(unresolved_content)
    payload = NotePayload(
        subject="Unsaved intake sections",
        body=body,
        note_type={"lookup_value_name": "General Notes"},
    ).model_dump(exclude_none=True)
    payload["active"] = True
    result = await _ensure_child_record(
        _cache_for(session, matter_uuid, deadline, cache),
        "notes",
        payload,
        OperationKind.FALLBACK_NOTE,
        "fallback note",
        "",
        ("subject", "note_type"),
        update_on_conflict=True,
    )
    return _is_resolved(result.outcome)


async def _save_income_records(
    session: aiohttp.ClientSession,
    matter_uuid: str,
    income_data: Dict[str, Any],
    deadline: float,
    cache: _ChildCollectionCache | None = None,
) -> list[RecordResult]:
    if not isinstance(income_data, dict):
        return []
    listing = income_data.get("listing", {})
    if not isinstance(listing, dict) or not listing:
        return []

    prepared: list[tuple[dict[str, Any], str]] = []
    results: list[RecordResult] = []
    for person_name, income_info in listing.items():
        if not isinstance(income_info, dict):
            continue
        for income_category_name, amount_info in income_info.items():
            fallback = (
                f"{person_name} - {income_category_name}: "
                f"{json.dumps(amount_info, default=str)}"
            )
            try:
                if not isinstance(amount_info, dict):
                    raise ValueError
                payload = IncomePayload(
                    type={"lookup_value_name": income_category_name},
                    amount=amount_info.get("amount"),
                    period=amount_info.get("period"),
                ).model_dump(exclude_none=True)
                prepared.append((payload, fallback))
            except Exception:
                results.append(
                    RecordResult(
                        OperationKind.INCOME,
                        OperationOutcome.FAILED,
                        "income validation failed",
                        _fallback_content=fallback,
                    )
                )

    match_fields = ("amount", "period", "type")
    key_counts = Counter(
        _upsert_key("incomes", payload, match_fields) for payload, _ in prepared
    )
    child_cache = _cache_for(session, matter_uuid, deadline, cache)
    for payload, fallback in prepared:
        results.append(
            await _ensure_child_record(
                child_cache,
                "incomes",
                payload,
                OperationKind.INCOME,
                "income",
                fallback,
                match_fields,
                allow_upsert=(
                    key_counts[_upsert_key("incomes", payload, match_fields)] == 1
                ),
            )
        )
    return results


async def _save_additional_names(
    session: aiohttp.ClientSession,
    matter_uuid: str,
    names_list: list[Dict[str, Any]],
    deadline: float,
    cache: _ChildCollectionCache | None = None,
) -> list[RecordResult]:
    if not names_list or len(names_list) <= 1:
        return []
    prepared: list[tuple[dict[str, Any], str]] = []
    results: list[RecordResult] = []
    match_fields = ("first", "last", "middle", "suffix", "type")
    for name in names_list[1:]:
        fallback = json.dumps(name, default=str)
        try:
            payload = AdditionalNamePayload(
                first=name.get("first"),
                last=name.get("last"),
                middle=name.get("middle"),
                suffix=name.get("suffix"),
                type={"lookup_value_name": name.get("type", "Former Name")},
            ).model_dump(exclude_none=True)
            prepared.append((payload, fallback))
        except Exception:
            results.append(
                RecordResult(
                    OperationKind.ALIAS,
                    OperationOutcome.FAILED,
                    "alias validation failed",
                    _fallback_content=fallback,
                )
            )
            continue

    collision_fields = ("first", "last", "type")
    key_counts = Counter(
        _upsert_key("additional_names", payload, collision_fields)
        for payload, _ in prepared
    )
    child_cache = _cache_for(session, matter_uuid, deadline, cache)
    for payload, fallback in prepared:
        results.append(
            await _ensure_child_record(
                child_cache,
                "additional_names",
                payload,
                OperationKind.ALIAS,
                "alias",
                fallback,
                match_fields,
                allow_upsert=(
                    key_counts[
                        _upsert_key("additional_names", payload, collision_fields)
                    ]
                    == 1
                ),
            )
        )
    return results


async def _save_adverse_parties(
    session: aiohttp.ClientSession,
    matter_uuid: str,
    adverse_parties_data: Dict[str, Any],
    deadline: float,
    cache: _ChildCollectionCache | None = None,
) -> list[RecordResult]:
    if not isinstance(adverse_parties_data, dict):
        return []
    parties = adverse_parties_data.get("adverse_parties", [])
    if not isinstance(parties, list) or not parties:
        return []

    prepared: list[tuple[dict[str, Any], str]] = []
    results: list[RecordResult] = []
    for party in parties:
        fallback = json.dumps(party, default=str)
        try:
            payload_data = {
                "first": party.get("first"),
                "last": party.get("last"),
                "middle": party.get("middle"),
                "suffix": party.get("suffix"),
                "organization_name": party.get("organization_name"),
                "date_of_birth": party.get("dob"),
            }
            for phone in party.get("phones", []):
                number = phone.get("number")
                phone_type = (phone.get("type") or "").lower()
                if number and phone_type in {"home", "business", "mobile", "fax"}:
                    payload_data[f"phone_{phone_type}"] = number
            payload = AdversePartyPayload(**payload_data).model_dump(exclude_none=True)
            payload["active"] = True
            prepared.append((payload, fallback))
        except Exception:
            results.append(
                RecordResult(
                    OperationKind.ADVERSE_PARTY,
                    OperationOutcome.FAILED,
                    "adverse party validation failed",
                    _fallback_content=fallback,
                )
            )

    match_fields = (
        "first",
        "last",
        "organization_name",
        "phone_business",
        "active",
    )
    collision_fields = ("first", "last", "organization_name", "active")
    key_counts = Counter(
        _upsert_key("adverse_parties", payload, collision_fields)
        for payload, _ in prepared
    )
    child_cache = _cache_for(session, matter_uuid, deadline, cache)
    for payload, fallback in prepared:
        results.append(
            await _ensure_child_record(
                child_cache,
                "adverse_parties",
                payload,
                OperationKind.ADVERSE_PARTY,
                "adverse party",
                fallback,
                match_fields,
                allow_upsert=(
                    key_counts[
                        _upsert_key("adverse_parties", payload, collision_fields)
                    ]
                    == 1
                ),
            )
        )
    return results


async def _save_note(
    session: aiohttp.ClientSession,
    matter_uuid: str,
    deadline: float,
    payload: NotePayload,
    kind: OperationKind,
    description: str,
    fallback_content: str,
    cache: _ChildCollectionCache | None,
) -> RecordResult:
    payload_data = payload.model_dump(exclude_none=True)
    payload_data["active"] = True
    return await _ensure_child_record(
        _cache_for(session, matter_uuid, deadline, cache),
        "notes",
        payload_data,
        kind,
        description,
        fallback_content,
        ("subject", "note_type", "body"),
    )


async def _save_case_description_note(
    session: aiohttp.ClientSession,
    matter_uuid: str,
    case_type_data: Dict[str, Any],
    deadline: float,
    cache: _ChildCollectionCache | None = None,
) -> RecordResult:
    if not isinstance(case_type_data, dict):
        return RecordResult(OperationKind.CASE_DESCRIPTION, OperationOutcome.SKIPPED)
    case_description = case_type_data.get("case_description")
    if not case_description:
        return RecordResult(OperationKind.CASE_DESCRIPTION, OperationOutcome.SKIPPED)
    try:
        payload = NotePayload(
            subject="Case Description",
            body=case_description,
            note_type={"lookup_value_name": "General Notes"},
        )
    except Exception:
        return RecordResult(
            OperationKind.CASE_DESCRIPTION,
            OperationOutcome.FAILED,
            _fallback_content=str(case_description),
        )
    return await _save_note(
        session,
        matter_uuid,
        deadline,
        payload,
        OperationKind.CASE_DESCRIPTION,
        "case description",
        str(case_description),
        cache,
    )


def _format_assets_note(listing: Any, total_value: Any) -> str:
    lines: list[str] = []
    if isinstance(listing, list):
        for asset in listing:
            if not isinstance(asset, dict):
                continue
            for asset_type, amount in asset.items():
                try:
                    lines.append(f"{asset_type}: ${float(amount):,.2f}")
                except (TypeError, ValueError):
                    lines.append(f"{asset_type}: {amount}")
    try:
        if Decimal(str(total_value)) > 0:
            lines.append(f"Total Assets: ${float(total_value):,.2f}")
    except (InvalidOperation, TypeError, ValueError):
        pass
    return "\n".join(lines)


async def _save_assets_note(
    session: aiohttp.ClientSession,
    matter_uuid: str,
    assets_data: Dict[str, Any],
    deadline: float,
    cache: _ChildCollectionCache | None = None,
) -> RecordResult:
    if not isinstance(assets_data, dict):
        return RecordResult(OperationKind.ASSETS, OperationOutcome.SKIPPED)
    listing = assets_data.get("listing", [])
    total_value = assets_data.get("total_value", 0)
    formatted = _format_assets_note(listing, total_value)
    body = formatted or "No assets recorded"
    try:
        payload = NotePayload(
            subject="Assets",
            body=body,
            note_type={"lookup_value_name": "General Notes"},
        )
    except Exception:
        return RecordResult(
            OperationKind.ASSETS,
            OperationOutcome.FAILED,
            _fallback_content=f"Assets: {json.dumps(listing, default=str)} (total: {total_value})",
        )
    return await _save_note(
        session,
        matter_uuid,
        deadline,
        payload,
        OperationKind.ASSETS,
        "assets",
        formatted or body,
        cache,
    )


async def _save_rejection_note(
    session: aiohttp.ClientSession,
    matter_uuid: str,
    rejection_reason_name: str,
    deadline: float,
    cache: _ChildCollectionCache | None = None,
) -> RecordResult:
    payload = NotePayload(
        subject="Automatic Rejection",
        body="This intake was automatically rejected.",
        note_type={"lookup_value_name": "General Notes"},
    )
    return await _save_note(
        session,
        matter_uuid,
        deadline,
        payload,
        OperationKind.REJECTION_REASON,
        "rejection reason",
        rejection_reason_name,
        cache,
    )


async def save_intake_legalserver(state: dict) -> LegalServerPersistenceResult:
    if ev_is_true("LEGALSERVER_TESTING_DISABLE_CONNECTION"):
        logger.debug("LegalServer connection disabled")
        return LegalServerPersistenceResult(
            overall=LegalServerOverall.SKIPPED,
            message="LegalServer connection disabled",
        )
    if not state.get("call_id"):
        logger.warning("LS save: no call_id")
        return LegalServerPersistenceResult(
            overall=LegalServerOverall.FAILED,
            message="No call_id provided",
        )
    overall_deadline = _now() + LEGALSERVER_TIMEOUT
    ops: list[RecordResult] = []
    rejection_reason_name: str | None = None
    try:
        timeout = aiohttp.ClientTimeout(total=min(30, LEGALSERVER_TIMEOUT))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = _build_matter_payload(state)
            if payload is None:
                logger.warning("Cannot create matter: required fields missing")
                return LegalServerPersistenceResult(
                    overall=LegalServerOverall.FAILED,
                    message="Cannot create matter",
                )
            if payload.get("rejected") and payload.get("rejection_reason"):
                rejection_reason_name = payload["rejection_reason"].get(
                    "lookup_value_name"
                )
            matter_uuid = await _create_matter_guarded(
                session, payload, state["call_id"], overall_deadline
            )
            if matter_uuid is None:
                return LegalServerPersistenceResult(
                    overall=LegalServerOverall.FAILED,
                    message="Matter creation failed",
                )

            cache = _ChildCollectionCache(session, matter_uuid, overall_deadline)
            if "income" in state:
                ops.extend(
                    await _save_income_records(
                        session,
                        matter_uuid,
                        state["income"],
                        overall_deadline,
                        cache,
                    )
                )
            if "adverse_parties" in state:
                ops.extend(
                    await _save_adverse_parties(
                        session,
                        matter_uuid,
                        state["adverse_parties"],
                        overall_deadline,
                        cache,
                    )
                )
            if "case_type" in state and bool(
                state["case_type"].get("case_description")
            ):
                ops.append(
                    await _save_case_description_note(
                        session,
                        matter_uuid,
                        state["case_type"],
                        overall_deadline,
                        cache,
                    )
                )
            if "assets" in state:
                ops.append(
                    await _save_assets_note(
                        session,
                        matter_uuid,
                        state["assets"],
                        overall_deadline,
                        cache,
                    )
                )
            names_list = state.get("names", {}).get("names", [])
            if len(names_list) > 1:
                ops.extend(
                    await _save_additional_names(
                        session,
                        matter_uuid,
                        names_list,
                        overall_deadline,
                        cache,
                    )
                )
            if rejection_reason_name:
                ops.append(
                    await _save_rejection_note(
                        session,
                        matter_uuid,
                        rejection_reason_name,
                        overall_deadline,
                        cache,
                    )
                )

            has_unresolved = any(not _is_resolved(result.outcome) for result in ops)
            if has_unresolved and _now() < overall_deadline:
                fallback_parts = _collect_fallback_content(ops, rejection_reason_name)
                if not fallback_parts:
                    return LegalServerPersistenceResult(
                        overall=LegalServerOverall.FAILED,
                        matter_uuid=matter_uuid,
                        operations=ops,
                        message="No renderable fallback content",
                    )
                fallback_ok = await _post_fallback_note(
                    session,
                    matter_uuid,
                    fallback_parts,
                    overall_deadline,
                    cache,
                )
                ops.append(
                    RecordResult(
                        OperationKind.FALLBACK_NOTE,
                        OperationOutcome.SUCCESS
                        if fallback_ok
                        else OperationOutcome.FAILED,
                        "fallback note",
                    )
                )
                if not fallback_ok:
                    return LegalServerPersistenceResult(
                        overall=LegalServerOverall.FAILED,
                        matter_uuid=matter_uuid,
                        operations=ops,
                        message="Fallback note failed",
                    )
                for result in ops:
                    if result.outcome in (
                        OperationOutcome.FAILED,
                        OperationOutcome.AMBIGUOUS,
                    ):
                        result.outcome = OperationOutcome.FALLBACK_PRESERVED
                return LegalServerPersistenceResult(
                    overall=LegalServerOverall.DEGRADED,
                    matter_uuid=matter_uuid,
                    operations=ops,
                    message="Fallback preserved unresolved data",
                )
            if has_unresolved:
                return LegalServerPersistenceResult(
                    overall=LegalServerOverall.FAILED,
                    matter_uuid=matter_uuid,
                    operations=ops,
                    message="Deadline exhausted before fallback",
                )
            return LegalServerPersistenceResult(
                overall=LegalServerOverall.COMPLETE,
                matter_uuid=matter_uuid,
                operations=ops,
                message="All sections saved successfully",
            )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception:
        logger.warning("LS save error")
        return LegalServerPersistenceResult(
            overall=LegalServerOverall.FAILED,
            message="Unexpected error",
        )


def _collect_fallback_content(
    ops: list[RecordResult],
    rejection_reason_name: str | None,
) -> list[str]:
    parts: list[str] = []
    income_lines: list[str] = []
    alias_lines: list[str] = []
    ap_lines: list[str] = []
    case_desc = ""
    assets_line = ""
    rejection_line = ""
    for r in ops:
        if r.outcome in (OperationOutcome.SUCCESS, OperationOutcome.SKIPPED):
            continue
        if r.kind == OperationKind.INCOME and r._fallback_content:
            income_lines.append(r._fallback_content)
        elif r.kind == OperationKind.ALIAS and r._fallback_content:
            alias_lines.append(r._fallback_content)
        elif r.kind == OperationKind.ADVERSE_PARTY and r._fallback_content:
            ap_lines.append(r._fallback_content)
        elif r.kind == OperationKind.CASE_DESCRIPTION and r._fallback_content:
            case_desc = r._fallback_content
        elif r.kind == OperationKind.ASSETS and r._fallback_content:
            assets_line = r._fallback_content
        elif r.kind == OperationKind.REJECTION_REASON and r._fallback_content:
            rejection_line = r._fallback_content
    if income_lines:
        parts.append("Income:\n" + "\n".join(income_lines))
    if alias_lines:
        parts.append("Additional names:\n" + "\n".join(alias_lines))
    if ap_lines:
        parts.append("Adverse parties:\n" + "\n".join(ap_lines))
    if case_desc:
        parts.append(f"Case description: {case_desc}")
    if assets_line:
        parts.append(
            assets_line
            if assets_line.startswith("Assets:")
            else f"Assets: {assets_line}"
        )
    if rejection_line:
        parts.append(f"Rejection reason: {rejection_line}")
    elif rejection_reason_name and not any(
        r.kind == OperationKind.REJECTION_REASON
        and r.outcome not in (OperationOutcome.SUCCESS, OperationOutcome.SKIPPED)
        for r in ops
    ):
        parts.append(f"Rejection reason: {rejection_reason_name}")
    return parts


def _build_matter_payload(state: Dict[str, Any]) -> Dict[str, Any] | None:
    names_list = state.get("names", {}).get("names", [])
    if not names_list:
        logger.warning("Cannot create matter: names not found or empty in state")
        return None

    primary_name = names_list[0]

    payload = {**primary_name}

    call_id = state.get("call_id")
    if call_id:
        payload["external_id"] = call_id

    if primary_name.get("type") == "Legal Name":
        custom_fields = payload.get("custom_fields")
        if not isinstance(custom_fields, dict):
            custom_fields = {}
        custom_fields.setdefault("is_this_the_client_s_legal_name__1065", True)
        payload["custom_fields"] = custom_fields

    if isinstance(state.get("phone"), dict):
        phone_number = state["phone"].get("phone_number")
        phone_type = state["phone"].get("phone_type", "mobile")
        if phone_number and phone_type:
            payload[f"""{phone_type}_phone"""] = phone_number

    if isinstance(state.get("case_type"), dict):
        legal_problem_code = state["case_type"].get("legal_problem_code")
        if isinstance(legal_problem_code, str):
            normalized_code = legal_problem_code.strip()
            if normalized_code and not normalized_code.startswith("00"):
                payload["legal_problem_code"] = normalized_code

    if isinstance(state.get("service_area"), dict):
        if fips_code := state["service_area"].get("fips_code"):
            payload["county_of_dispute"] = {"county_FIPS": str(fips_code)}

    if isinstance(state.get("income"), dict):
        payload["income_eligible"] = state["income"].get("is_eligible")
        payload["number_of_adults"] = state["income"].get("household_size")

    if isinstance(state.get("household_composition"), dict):
        payload["number_of_adults"] = state["household_composition"].get(
            "number_of_adults"
        )
        payload["number_of_children"] = state["household_composition"].get(
            "number_of_children"
        )

    if isinstance(state.get("assets"), dict):
        payload["asset_eligible"] = state["assets"].get("is_eligible")

    if isinstance(state.get("citizenship"), dict):
        payload["citizenship"] = state["citizenship"].get("is_citizen")

    if isinstance(state.get("ssn_last_4"), dict):
        payload["ssn"] = state["ssn_last_4"].get("ssn_last_4")

    if isinstance(state.get("date_of_birth"), dict):
        payload["date_of_birth"] = state["date_of_birth"].get("date_of_birth")

    raw_address = state.get("address") or {}
    address = raw_address.get("address") if isinstance(raw_address, dict) else {}
    if isinstance(address, dict):
        street = address.get("street")
        if street:
            payload["home_street"] = street
        street_2 = address.get("street_2")
        if street_2:
            payload["home_apt_num"] = street_2
        city = address.get("city")
        if city:
            payload["home_city"] = city
        state_abbr = address.get("state")
        if state_abbr:
            payload["home_state"] = state_abbr
        zip_code = address.get("zip")
        if zip_code:
            payload["home_zip"] = zip_code

        county_name = address.get("county")
        if isinstance(county_name, str) and isinstance(state_abbr, str):
            county_name = county_name.strip()
            state_abbr_val = state_abbr.strip().upper()
            if county_name and state_abbr_val:
                payload["county_of_residence"] = {
                    "county_name": county_name,
                    "county_state": state_abbr_val,
                }

    if isinstance(state.get("domestic_violence"), dict):
        payload["victim_of_domestic_violence"] = state["domestic_violence"].get(
            "is_experiencing"
        )

    rejection_reason_name = None

    if isinstance(state.get("service_area"), dict):
        if not state["service_area"].get("is_eligible", True):
            rejection_reason_name = "Out of Service Area"

    if not rejection_reason_name and isinstance(state.get("case_type"), dict):
        if not state["case_type"].get("is_eligible", True):
            rejection_reason_name = "Not LSC-Permissible"

    if not rejection_reason_name and isinstance(state.get("income"), dict):
        if not state["income"].get("is_eligible", True):
            rejection_reason_name = "Over Income"

    if not rejection_reason_name and isinstance(state.get("assets"), dict):
        if not state["assets"].get("is_eligible", True):
            rejection_reason_name = "Over Asset"

    if not rejection_reason_name and "address" not in state:
        rejection_reason_name = "Other"

    if rejection_reason_name:
        payload["case_disposition"] = "Rejected"
        payload["rejected"] = True
        payload["date_rejected"] = date.today().isoformat()
        payload["rejection_reason"] = {"lookup_value_name": rejection_reason_name}

    try:
        validated = LegalServerCreateMatterPayload(**payload)
        return validated.model_dump(mode="json", exclude_none=True)
    except ValidationError:
        logger.warning("Cannot create matter: validation failed")
        return None


async def get_common_lookup_types() -> list[str] | None:
    common_lookup_types = [
        "alias_type",
        "citizenship",
        "country_of_origin",
        "current_living_situation",
        "employment_status",
        "ethnicity",
        "how_referred",
        "immigration_status",
        "income_type",
        "language",
        "legal_problem_category",
        "marital_status",
        "military_service",
        "military_status",
        "note_type",
        "race",
    ]
    return common_lookup_types


async def get_custom_lookups() -> Dict[str, Any] | None:
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            all_lookups = []
            page_number = 1
            total_pages = None

            while total_pages is None or page_number <= total_pages:
                response = await session.get(
                    f"""{_legalserver_api_base_url()}/custom_lookups?page_number={page_number}""",
                    headers=_legalserver_headers(),
                )

                if response.status not in (200, 201):
                    logger.error(
                        f"""Failed to query custom lookups page {page_number}: {response.status}"""
                    )
                    return None

                data = await response.json(content_type=None)
                logger.debug(
                    f"""Custom lookups page {page_number} response keys: {data.keys()}"""
                )

                if total_pages is None:
                    total_pages = data.get("total_number_of_pages", 1)

                page_data = data.get("data", [])
                all_lookups.extend(page_data)

                page_number += 1

            return {
                "total_records": data.get("total_records", len(all_lookups)),
                "total_pages": total_pages,
                "data": all_lookups,
            }

    except aiohttp.ClientError as e:
        logger.error(f"""HTTP Request failed: {e}""")
        return None
    except Exception as e:
        logger.error(f"""Unexpected error querying custom lookups: {e}""")
        return None


async def query_lookup_values(
    lookup_identifier: str, is_custom: bool = False
) -> Dict[str, Any] | None:
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if is_custom:
                base_url = f"""{_legalserver_api_base_url()}/custom_lookups/{lookup_identifier}"""
            else:
                base_url = (
                    f"""{_legalserver_api_base_url()}/lookups/{lookup_identifier}"""
                )

            all_values = []
            page_number = 1
            total_pages = None
            final_data = None

            while total_pages is None or page_number <= total_pages:
                response = await session.get(
                    base_url,
                    headers=_legalserver_headers(),
                    params={"page_number": page_number},
                )

                if response.status not in (200, 201):
                    lookup_type = "custom lookup" if is_custom else "lookup table"
                    logger.error(
                        f"""Failed to query {lookup_type} '{lookup_identifier}' page {page_number}: {response.status}"""
                    )
                    return None

                data = await response.json(content_type=None)
                final_data = data

                if "total_number_of_pages" in data and "data" in data:
                    if total_pages is None:
                        total_pages = data.get("total_number_of_pages", 1)

                    page_data = data.get("data", [])
                    if isinstance(page_data, list):
                        all_values.extend(page_data)

                    page_number += 1
                else:
                    lookup_values = data.get("data", data)
                    if isinstance(lookup_values, list):
                        all_values = lookup_values
                    else:
                        all_values = [lookup_values] if lookup_values else []
                    break

            return {
                "lookup_type": lookup_identifier,
                "is_custom": is_custom,
                "values": all_values,
                "raw_response": final_data,
            }

    except aiohttp.ClientError as e:
        logger.error(f"""HTTP Request failed: {e}""")
        return None
    except Exception as e:
        logger.error(f"""Unexpected error querying lookup values: {e}""")
        return None


async def find_lookup_by_id(lookup_value_id: int) -> Dict[str, Any] | None:
    common_types = await get_common_lookup_types()
    if not common_types:
        return None

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for lookup_type in common_types:
                url = f"""{_legalserver_api_base_url()}/lookups/{lookup_type}"""

                try:
                    response = await session.get(
                        url,
                        headers=_legalserver_headers(),
                        timeout=aiohttp.ClientTimeout(total=10),
                    )
                    try:
                        if response.status not in (200, 201):
                            continue

                        data = await response.json(content_type=None)
                        values = data.get("data", [])

                        if isinstance(values, list):
                            for item in values:
                                if (
                                    isinstance(item, dict)
                                    and item.get("id") == lookup_value_id
                                ):
                                    return {
                                        "lookup_type": lookup_type,
                                        "lookup_value": item,
                                    }
                        elif isinstance(values, dict):
                            if values.get("id") == lookup_value_id:
                                return {
                                    "lookup_type": lookup_type,
                                    "lookup_value": values,
                                }
                    finally:
                        _finalize_response(response)
                except Exception as e:
                    logger.debug(f"""Error querying {lookup_type}: {e}""")
                    continue

            return None

    except Exception as e:
        logger.error(f"""Error searching for lookup ID: {e}""")
        return None


async def get_fips_code(county_name: str, state_abbrev: str = "VA") -> Optional[str]:
    result = await query_lookup_values("county")
    if not result or not result.get("values"):
        return None

    if county_name.lower().endswith(" county"):
        county_name = county_name[: -len(" county")].strip()

    target_name = county_name.lower()
    target_state = state_abbrev.upper()

    for item in result["values"]:
        if not isinstance(item, dict):
            continue

        item_name = (item.get("name") or "").lower()
        item_state = (item.get("county_state") or "").upper()

        if item_name == target_name and item_state == target_state:
            return str(item.get("fips"))

    return None


if __name__ == "__main__":

    def load_state_by_call_id(call_id: str) -> Optional[Dict[str, Any]]:
        state_file = Path(PROJECT_ROOT) / "logs/flow_manager_state.json"

        if not state_file.exists():
            logger.error(f"""State file not found: {state_file}""")
            return None

        try:
            with open(state_file, "r") as f:
                all_states = json.load(f)

            if call_id not in all_states:
                logger.error(f"""No state found for call_id: {call_id}""")
                return None

            state = all_states[call_id]
            logger.info(f"""Loaded state for call_id: {call_id}""")
            return state

        except json.JSONDecodeError as e:
            logger.error(f"""Failed to parse state file: {e}""")
            return None
        except Exception as e:
            logger.error(f"""Error loading state: {e}""")
            return None

    def upload_call(call_id: str):
        state = load_state_by_call_id(call_id)
        if state is None:
            logger.error(f"""Failed to load state for call_id: {call_id}""")
            sys.exit(1)
        asyncio.run(save_intake_legalserver(state))

    def query_lookup(lookup_type: str, is_custom: bool = False):
        result = asyncio.run(query_lookup_values(lookup_type, is_custom=is_custom))
        if result is None:
            lookup_kind = "custom lookup" if is_custom else "lookup type"
            logger.error(f"""Failed to query {lookup_kind}: {lookup_type}""")
            sys.exit(1)

        lookup_kind = "Custom Lookup" if result["is_custom"] else "Lookup Type"
        print(f"""{lookup_kind}: {result["lookup_type"]}""")
        print(f"""Total Values: {len(result["values"])}""")
        print("\nLookup Values:")
        print(json.dumps(result["values"], indent=2))

    def list_available_lookups():
        lookup_types = asyncio.run(get_common_lookup_types())
        if lookup_types is None:
            logger.error("Failed to retrieve available lookup types from LegalServer")
            sys.exit(1)

        print("=" * 60)
        print("Common System Lookup Types (queryable via LegalServer API)")
        print("=" * 60)
        for lookup_type in lookup_types:
            print(f"""  - {lookup_type}""")
        print("\nNote: Not all system lookups in the API documentation may be directly")
        print("queryable. If you get a 404 error, the lookup type may use a different")
        print("name or may not be accessible via this endpoint in your instance.")
        print("\nFor a comprehensive list of system lookup types, see:")
        print(
            "https://www.apidocs.legalserver.org/docs/ls-apis/c829022494710-search-lookup-general"
        )
        print("\n" + "=" * 60)
        print("Usage:")
        print("=" * 60)
        print("Query a specific lookup type:")
        print("  python legalserver.py --query-lookup alias_type")
        print("\nDiscover custom lookups in your instance:")
        print("  python legalserver.py --query-custom-lookups")
        print("\nThen query a custom lookup by its UUID:")
        print("  python legalserver.py --query-custom-lookup <uuid>")

    def query_custom_lookups():
        result = asyncio.run(get_custom_lookups())
        if result is None:
            logger.error("Failed to retrieve custom lookups from LegalServer")
            sys.exit(1)

        print(json.dumps(result, indent=2))

    if len(sys.argv) < 2:
        print(
            "Usage: python legalserver.py <command> [args]\n"
            "Commands:\n"
            "  --upload <call_id>              Upload intake data for a call to LegalServer\n"
            "  --query-lookup [type]           Query system lookup values by type\n"
            "                                  Omit type to see common lookup types\n"
            "                                  Examples: alias_type, income_type, note_type\n"
            "  --query-custom-lookup <uuid>    Query a specific custom lookup by UUID\n"
            "  --query-custom-lookups          List all custom lookup tables\n"
            "                                  in your LegalServer instance"
        )
        sys.exit(1)

    command = sys.argv[1]

    if command == "--upload":
        if len(sys.argv) < 3:
            print("Usage: python legalserver.py --upload <call_id>")
            sys.exit(1)
        call_id = sys.argv[2]
        upload_call(call_id)
    elif command == "--query-lookup":
        if len(sys.argv) < 3:
            list_available_lookups()
        else:
            lookup_type = sys.argv[2]
            query_lookup(lookup_type, is_custom=False)
    elif command == "--query-custom-lookup":
        if len(sys.argv) < 3:
            print("Usage: python legalserver.py --query-custom-lookup <uuid>")
            sys.exit(1)
        lookup_uuid = sys.argv[2]
        query_lookup(lookup_uuid, is_custom=True)
    elif command == "--query-custom-lookups":
        query_custom_lookups()
    elif command == "--lookup-fips":
        if len(sys.argv) < 3:
            print(
                "Usage: python legalserver.py --lookup-fips <county_name> [state_abbrev]"
            )
            sys.exit(1)
        county_name = sys.argv[2]
        state_abbrev = sys.argv[3] if len(sys.argv) > 3 else "VA"

        fips = asyncio.run(get_fips_code(county_name, state_abbrev))
        if fips:
            print(f"""FIPS code for {county_name}, {state_abbrev}: {fips}""")
        else:
            print(f"""FIPS code not found for {county_name}, {state_abbrev}""")
            sys.exit(1)
    else:
        print(f"""Unknown command: {command}""")
        print(
            "Usage: python legalserver.py <command> [args]\n"
            "Commands:\n"
            "  --upload <call_id>              Upload intake data for a call to LegalServer\n"
            "  --query-lookup [type]           Query system lookup values by type\n"
            "  --query-custom-lookup <uuid>    Query a specific custom lookup by UUID\n"
            "  --query-custom-lookups          List all custom lookup tables\n"
            "  --lookup-fips <county> [state]  Look up FIPS code for a county"
        )
        sys.exit(1)
