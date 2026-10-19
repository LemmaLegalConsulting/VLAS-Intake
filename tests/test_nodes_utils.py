from types import SimpleNamespace
from unittest.mock import patch

import pytest
from loguru import logger
from pydantic import BaseModel, RootModel, ValidationError, field_validator

from intake_bot.models.intake_flow_result import Status
from intake_bot.nodes.nodes import record_assets_list, record_income, record_name
from intake_bot.nodes.utils import clean_pydantic_error_message


class _SensitiveValueModel(BaseModel):
    first: str

    @field_validator("first")
    @classmethod
    def reject_value(cls, value: str) -> str:
        raise ValueError(f"rejected caller value: {value}")


class _AmountModel(BaseModel):
    amount: int


class _CallerKeyedAmounts(RootModel[dict[str, _AmountModel]]):
    pass


def test_clean_pydantic_error_excludes_input_and_validator_context():
    sentinel = "CALLER-SECRET-VALIDATION-9f4a"
    with pytest.raises(ValidationError) as captured:
        _SensitiveValueModel.model_validate({"first": sentinel})

    cleaned = clean_pydantic_error_message(captured.value)

    assert cleaned == "first: value_error"
    assert sentinel not in cleaned
    assert "input_value" not in cleaned
    assert "errors.pydantic.dev" not in cleaned


def test_clean_pydantic_error_replaces_caller_supplied_mapping_keys():
    sentinel = "CALLER-NAME-WITH-NEWLINE\nFORGED-LOG"
    with pytest.raises(ValidationError) as captured:
        _CallerKeyedAmounts.model_validate({sentinel: {"amount": "invalid"}})

    cleaned = clean_pydantic_error_message(captured.value)

    assert cleaned == "<key>.amount: int_parsing"
    assert sentinel not in cleaned
    assert "FORGED-LOG" not in cleaned


@pytest.mark.asyncio
async def test_node_validation_log_and_result_exclude_rejected_input():
    sentinel = "CALLER-SECRET-NAME-31b7"
    captured_logs: list[str] = []
    sink_id = logger.add(
        lambda message: captured_logs.append(str(message)), level="DEBUG"
    )
    flow_manager = SimpleNamespace(state={})
    try:
        with patch("intake_bot.nodes.nodes.CallerName", _SensitiveValueModel):
            result, next_node = await record_name(flow_manager, sentinel, "", "Public")
    finally:
        logger.remove(sink_id)

    combined_logs = "\n".join(captured_logs)
    assert result["status"] == Status.ERROR
    assert next_node is None
    assert "first: value_error" in result["error"]
    assert "Validation failed for name: first: value_error" in combined_logs
    assert sentinel not in result["error"]
    assert sentinel not in combined_logs
    assert "input_value" not in combined_logs


@pytest.mark.asyncio
async def test_plain_value_error_text_is_not_logged_or_returned():
    sentinel = "CALLER-SECRET-ASSET-c82e"
    captured_logs: list[str] = []
    sink_id = logger.add(
        lambda message: captured_logs.append(str(message)), level="DEBUG"
    )
    flow_manager = SimpleNamespace(state={})
    try:
        with patch(
            "intake_bot.nodes.nodes.IntakeValidator.assets_filter_countable_entries",
            side_effect=ValueError(sentinel),
        ):
            result, next_node = await record_assets_list(flow_manager, [])
    finally:
        logger.remove(sink_id)

    combined_logs = "\n".join(captured_logs)
    assert result["status"] == Status.ERROR
    assert next_node is None
    assert result["error"] == "Error validating assets."
    assert "Validation failed for assets: value_error" in combined_logs
    assert sentinel not in result["error"]
    assert sentinel not in combined_logs


@pytest.mark.asyncio
async def test_income_mapping_key_is_not_logged_or_returned():
    sentinel = "CALLER-SECRET-HOUSEHOLD-MEMBER-d104"
    captured_logs: list[str] = []
    sink_id = logger.add(
        lambda message: captured_logs.append(str(message)), level="DEBUG"
    )
    flow_manager = SimpleNamespace(
        state={
            "household_composition": {"number_of_adults": 1, "number_of_children": 0}
        }
    )
    try:
        result, next_node = await record_income(
            flow_manager,
            {sentinel: {"Employment": {"amount": "invalid", "period": "Monthly"}}},
        )
    finally:
        logger.remove(sink_id)

    combined_logs = "\n".join(captured_logs)
    assert result["status"] == Status.ERROR
    assert next_node is None
    assert sentinel not in result["error"]
    assert sentinel not in combined_logs
    assert "Validation failed for income" in combined_logs
