from functools import wraps

from loguru import logger
from pipecat_flows import FlowManager
from pydantic import BaseModel


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
