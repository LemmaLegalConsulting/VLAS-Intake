import re
from functools import wraps

from loguru import logger
from pipecat_flows import FlowManager
from pydantic import BaseModel, ValidationError


def clean_pydantic_error_message(error: ValidationError) -> str:
    """
    Clean up Pydantic ValidationError message to remove the documentation URL.

    Converts from:
        "1 validation error for PotentialConflicts\n0.phones.0.number\n  Value error, Invalid US phone number: 111-111-1111 [type=value_error, input_value='111-111-1111', input_type=str]\n    For further information visit https://errors.pydantic.dev/2.12/v/value_error"

    To:
        "1 validation error for PotentialConflicts\n0.phones.0.number\n  Value error, Invalid US phone number: 111-111-1111 [type=value_error, input_value='111-111-1111', input_type=str]"
    """
    error_message = str(error)
    cleaned = re.sub(r"""\n\s*For further information visit https://[^\s]+""", "", error_message)
    return cleaned


def log_flow_manager_state(flow_manager: FlowManager):
    logger.debug("----------------------------------------")
    logger.debug("flow_manager.state:")
    for key, value in flow_manager.state.items():
        if isinstance(value, dict):
            logger.debug(f"{key}:")
            for sub_key, sub_value in value.items():
                logger.debug(f"  {sub_key}: {sub_value}")
        else:
            logger.debug(f"{key}: {value}")
    logger.debug("----------------------------------------")


def convert_and_log_result(state_key: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(flow_manager, *args, **kwargs):
            result, next_node = await func(flow_manager, *args, **kwargs)
            if isinstance(result, BaseModel):
                flow_manager.state[state_key] = result.model_dump(exclude={"status", "error"})
                log_flow_manager_state(flow_manager)
                result = result.model_dump()
            return result, next_node

        return wrapper

    return decorator
