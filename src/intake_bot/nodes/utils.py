import asyncio
import json
import os
import re
from functools import wraps

import aiofiles
from intake_bot.models.intake_flow_result import Status
from intake_bot.utils.ev import ev_is_true
from intake_bot.utils.globals import DEBUG
from loguru import logger
from pipecat_flows import FlowManager
from pydantic import BaseModel, ValidationError


def clean_pydantic_error_message(error: ValidationError) -> str:
    """
    Clean up Pydantic ValidationError message to remove the documentation URL.

    Converts from:
        "1 validation error for PotentialConflicts
        0.phones.0.number
          Value error, Invalid US phone number: 111-111-1111 [type=value_error, input_value='111-111-1111', input_type=str]
              For further information visit https://errors.pydantic.dev/2.12/v/value_error"

    To:
        "1 validation error for PotentialConflicts
        0.phones.0.number
          Value error, Invalid US phone number: 111-111-1111 [type=value_error, input_value='111-111-1111', input_type=str]"
    """
    error_message = str(error)
    cleaned = re.sub(
        r"""\n\s*For further information visit https://[^\s]+""", "", error_message
    )
    return cleaned


def convert_and_log_result(state_key: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(flow_manager, *args, **kwargs):
            result, next_node = await func(flow_manager, *args, **kwargs)
            if isinstance(result, BaseModel):
                flow_manager.state[state_key] = result.model_dump(
                    exclude={"status", "error"}, exclude_none=True, mode="json"
                )
                if DEBUG:
                    log_flow_manager_state(flow_manager)
                result = result.model_dump(exclude_none=True, mode="json")
            return result, next_node

        return wrapper

    return decorator


def _serialize_for_logging(value):
    """
    Recursively serialize values for logging, converting enums to their string values.
    """
    from enum import Enum

    if isinstance(value, Enum):
        return value.value
    elif isinstance(value, dict):
        return {k: _serialize_for_logging(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        return [_serialize_for_logging(v) for v in value]
    else:
        return value


def log_flow_manager_state(flow_manager: FlowManager):
    logger.debug("----------------------------------------")
    logger.debug("flow_manager.state:")
    for key, value in flow_manager.state.items():
        serialized_value = _serialize_for_logging(value)
        if isinstance(serialized_value, dict):
            logger.debug(f"""{key}:""")
            for sub_key, sub_value in serialized_value.items():
                logger.debug(f"""  {sub_key}: {sub_value}""")
        else:
            logger.debug(f"""{key}: {serialized_value}""")
    logger.debug("----------------------------------------")


_save_state_lock = asyncio.Lock()


async def save_state_to_json(state: dict) -> None:
    """
    Save the state (flow_manager.state) to a JSON file, using call_id as the primary key.

    Creates or appends to a logs/flow_manager_state.json file where each entry is keyed by call_id.
    This enables tracking and programmatic evaluation of call results.
    """
    if not ev_is_true("LOG_TO_FILE"):
        return

    try:
        call_id = state.get("call_id")
        if not call_id:
            logger.warning("call_id not found in flow_manager.state; state not saved")
            return

        results_file = "logs/flow_manager_state.json"

        async with _save_state_lock:
            results_data = {}

            # Load existing results if file exists
            if os.path.exists(results_file):
                try:
                    async with aiofiles.open(results_file, "r") as f:
                        content = await f.read()
                        if content:
                            results_data = json.loads(content)
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(
                        f"""Error reading {results_file}: {e}. Starting with empty results."""
                    )

            # Serialize the state, converting enums to their values
            serialized_state = _serialize_for_logging(state)

            # Add or update the current call's state
            results_data[call_id] = serialized_state

            # Write the updated results back to file
            async with aiofiles.open(results_file, "w") as f:
                await f.write(json.dumps(results_data, indent=2))

        logger.info(f"""State for call_id {call_id} saved to {results_file}""")

    except Exception as e:
        logger.error(f"""Error saving state to JSON: {e}""")


def status_helper(status: bool) -> Status:
    """Helper for FlowResult's `status` value."""
    return Status.SUCCESS if status else Status.ERROR
