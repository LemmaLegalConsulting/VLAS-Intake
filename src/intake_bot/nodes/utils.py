import asyncio
import copy
import json
import os
from functools import wraps

import aiofiles
from loguru import logger
from pydantic import ValidationError

from intake_bot.models.intake_flow_result import (
    IntakeFlowResult,
    ServiceAreaResult,
    Status,
)
from intake_bot.utils.ev import ev_is_true

_SAFE_VALIDATION_LOCATION_PARTS = {
    "active",
    "address",
    "adverse_parties",
    "amount",
    "assets",
    "city",
    "county",
    "date_of_birth",
    "dob",
    "exclude",
    "first",
    "last",
    "listing",
    "middle",
    "notes",
    "number",
    "period",
    "phone_business",
    "phone_fax",
    "phone_home",
    "phone_mobile",
    "phones",
    "state",
    "street",
    "street_2",
    "suffix",
    "type",
    "zip",
}


def _safe_validation_location(location: tuple[object, ...]) -> str:
    parts = []
    for part in location:
        if isinstance(part, int):
            parts.append("[]")
        elif part in _SAFE_VALIDATION_LOCATION_PARTS:
            parts.append(str(part))
        else:
            parts.append("<key>")
    return ".".join(parts) or "<root>"


def clean_pydantic_error_message(error: ValidationError) -> str:
    details = error.errors(
        include_input=False,
        include_url=False,
        include_context=False,
    )
    return (
        "; ".join(
            f"{_safe_validation_location(detail['loc'])}: {detail['type']}"
            for detail in details
        )
        or "validation failed"
    )


def log_pydantic_validation_error(subject: str, error: ValidationError) -> None:
    logger.debug(
        "Validation failed for {}: {}",
        subject,
        clean_pydantic_error_message(error),
    )


def convert_and_log_result(state_key: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(flow_manager, *args, **kwargs):
            _had_key = state_key in flow_manager.state
            _previous_value = (
                copy.deepcopy(flow_manager.state.get(state_key)) if _had_key else None
            )
            try:
                result, next_node = await func(flow_manager, *args, **kwargs)
            except BaseException:
                # Restore exact pre-call state on any exception (including CancelledError)
                if _had_key:
                    flow_manager.state[state_key] = _previous_value
                else:
                    flow_manager.state.pop(state_key, None)
                raise
            if isinstance(result, IntakeFlowResult):
                if result.status == Status.SUCCESS:
                    flow_manager.state[state_key] = result.model_dump(
                        exclude={"status", "error"}, exclude_none=True, mode="json"
                    )
                else:
                    if _had_key:
                        flow_manager.state[state_key] = _previous_value
                    else:
                        flow_manager.state.pop(state_key, None)
                if isinstance(result, ServiceAreaResult):
                    result = result.model_dump(mode="json")
                else:
                    result = result.model_dump(exclude_none=True, mode="json")
            return result, next_node

        return wrapper

    return decorator


_save_state_lock = asyncio.Lock()


async def save_state_to_json(state: dict) -> None:
    if not ev_is_true("ENABLE_DEV_STATE_PERSISTENCE"):
        return

    try:
        call_id = state.get("call_id")
        if not call_id:
            return

        results_file = "logs/flow_manager_state.json"
        os.makedirs("logs", exist_ok=True, mode=0o750)

        async with _save_state_lock:
            results_data = {}
            if os.path.exists(results_file):
                try:
                    async with aiofiles.open(results_file, "r") as f:
                        content = await f.read()
                        if content:
                            results_data = json.loads(content)
                except (OSError, json.JSONDecodeError):
                    pass

            results_data[call_id] = state

            async with aiofiles.open(results_file, "w") as f:
                await f.write(json.dumps(results_data, indent=2))

    except Exception:  # noqa: BLE001 - development persistence is best effort
        logger.error("Error saving state")


def status_helper(status: bool) -> Status:
    return Status.SUCCESS if status else Status.ERROR
