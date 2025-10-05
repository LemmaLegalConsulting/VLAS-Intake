from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from intake_bot.intake_arg_models import Assets, HouseholdIncome
from intake_bot.intake_nodes import (
    caller_ended_conversation,
    conflict_check,
    continue_intake,
    end_conversation,
    node_caller_ended_conversation,
    node_end_conversation,
    record_assets_list,
    record_assets_receives_benefits,
    record_case_type,
    record_citizenship,
    record_domestic_violence,
    record_emergency,
    record_income,
    record_name,
    record_phone_number,
    record_service_area,
    system_phone_number,
)
from intake_bot.intake_results import (
    Status,
)


@pytest.fixture
def flow_manager():
    fm = MagicMock()
    fm.state = {}
    return fm


@pytest.fixture(autouse=True)
def patch_validator(monkeypatch):
    validator_mock = MagicMock()
    monkeypatch.setattr("intake_bot.intake_nodes.validator", validator_mock)
    return validator_mock


@pytest.fixture(autouse=True)
def patch_prompts(monkeypatch):
    prompts_mock = MagicMock()
    prompts_mock.get.side_effect = lambda k: {f"{k}_prompt": True}
    monkeypatch.setattr("intake_bot.intake_nodes.prompts", prompts_mock)
    return prompts_mock


@pytest.mark.asyncio
async def test_system_phone_number_with_phone(flow_manager):
    flow_manager.state["phone"] = "+18665345243"
    result, next_node = await system_phone_number(flow_manager)
    assert isinstance(result, dict)
    assert result["phone_number"] == "+18665345243"
    assert "record_phone_number_prompt" in next_node


@pytest.mark.asyncio
async def test_system_phone_number_without_phone(flow_manager):
    result, next_node = await system_phone_number(flow_manager)
    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "record_phone_number_prompt" in next_node


@pytest.mark.asyncio
async def test_record_phone_number_valid(flow_manager, patch_validator):
    patch_validator.check_phone_number = AsyncMock(return_value=(True, "(866) 534-5243"))
    result, next_node = await record_phone_number(flow_manager, "+18665345243")
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["phone"]["is_valid"] is True
    assert flow_manager.state["phone"]["phone_number"] == "(866) 534-5243"
    assert "record_name_prompt" in next_node


@pytest.mark.asyncio
async def test_record_phone_number_invalid(flow_manager, patch_validator):
    patch_validator.check_phone_number = AsyncMock(return_value=(False, "bad"))
    result, next_node = await record_phone_number(flow_manager, "bad")
    assert result["status"] == Status.ERROR
    assert flow_manager.state["phone"]["is_valid"] is False
    assert next_node is None


@pytest.mark.asyncio
async def test_record_name_valid(flow_manager):
    result, next_node = await record_name(flow_manager, "John", "Q", "Public")
    assert isinstance(result, dict)
    assert result["first"] == "John"
    assert result["middle"] == "Q"
    assert result["last"] == "Public"
    assert flow_manager.state["name"]["first"] == "John"
    assert flow_manager.state["name"]["middle"] == "Q"
    assert flow_manager.state["name"]["last"] == "Public"
    assert "record_service_area_prompt" in next_node


@pytest.mark.asyncio
async def test_record_name_invalid(flow_manager):
    result, next_node = await record_name(flow_manager, "", "", "")
    assert result["status"] == Status.ERROR
    assert next_node is None


@pytest.mark.asyncio
async def test_record_service_area_eligible(flow_manager, patch_validator):
    patch_validator.check_service_area = AsyncMock(return_value="Amelia County")
    result, next_node = await record_service_area(flow_manager, "Amelia County")
    assert isinstance(result, dict)
    assert result["is_eligible"] is True
    assert flow_manager.state["service_area"]["location"] == "Amelia County"
    assert "record_case_type_prompt" in next_node


@pytest.mark.asyncio
async def test_record_service_area_ineligible_with_match(flow_manager, patch_validator):
    patch_validator.check_service_area = AsyncMock(return_value="Shelbyville")
    result, next_node = await record_service_area(flow_manager, "Springfield")
    assert result["status"] == Status.ERROR
    assert "meant Shelbyville" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_service_area_ineligible_no_match(flow_manager, patch_validator):
    patch_validator.check_service_area = AsyncMock(return_value=None)
    patch_validator.get_alternative_providers = AsyncMock(return_value="AltProvider")
    result, next_node = await record_service_area(flow_manager, "Nowhere")
    assert "Alternate providers" in result["error"]
    assert "ineligible_prompt" in next_node


@pytest.mark.asyncio
async def test_record_case_type_eligible(flow_manager, patch_validator):
    patch_validator.check_case_type = AsyncMock(return_value=True)
    result, next_node = await record_case_type(flow_manager, "divorce")
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["case_type"]["case_type"] == "divorce"
    assert "conflict_check_prompt" in next_node


@pytest.mark.asyncio
async def test_record_case_type_ineligible(flow_manager, patch_validator):
    patch_validator.check_case_type = AsyncMock(return_value=False)
    patch_validator.get_alternative_providers = AsyncMock(return_value="AltProvider")
    result, next_node = await record_case_type(flow_manager, "aliens")
    assert "Alternate providers" in result["error"]
    assert "ineligible_prompt" in next_node


@pytest.mark.asyncio
async def test_conflict_check_no_conflict(flow_manager, patch_validator):
    patch_validator.check_conflict_of_interest = AsyncMock(return_value=False)
    result, next_node = await conflict_check(flow_manager, ["Bob"])
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert "record_domestic_violence_prompt" in next_node


@pytest.mark.asyncio
async def test_conflict_check_conflict(flow_manager, patch_validator):
    patch_validator.check_conflict_of_interest = AsyncMock(return_value=True)
    patch_validator.get_alternative_providers = AsyncMock(return_value="AltProvider")
    result, next_node = await conflict_check(flow_manager, ["Bob"])
    assert "Alternate providers" in result["error"]
    assert "ineligible_prompt" in next_node


@pytest.mark.asyncio
async def test_record_domestic_violence_true(flow_manager):
    result, next_node = await record_domestic_violence(flow_manager, ["Jack the Ripper"])
    assert isinstance(result, dict)
    assert flow_manager.state["domestic_violence"]["is_experiencing"] is True
    assert "Jack the Ripper" in flow_manager.state["domestic_violence"]["perpetrators"]
    assert "record_income_prompt" in next_node


@pytest.mark.asyncio
async def test_record_domestic_violence_false(flow_manager):
    result, next_node = await record_domestic_violence(flow_manager, [])
    assert isinstance(result, dict)
    assert flow_manager.state["domestic_violence"]["is_experiencing"] is False
    assert flow_manager.state["domestic_violence"]["perpetrators"] == []
    assert "record_income_prompt" in next_node


@pytest.mark.asyncio
async def test_record_income_valid_eligible_with_dummy_model(flow_manager, patch_validator):
    patch_validator.check_income = AsyncMock(return_value=(True, 1000))
    with patch("intake_bot.intake_nodes.HouseholdIncome", HouseholdIncome):
        income = {
            "John Doe": {
                "wages": {"amount": 1000, "period": "month"},
            },
        }
        result, next_node = await record_income(flow_manager, income)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["income"]["is_eligible"] is True
    assert flow_manager.state["income"]["monthly_amount"] == 1000
    assert "record_assets_receives_benefits_prompt" in next_node


@pytest.mark.asyncio
async def test_record_income_multiple_members(flow_manager, patch_validator):
    patch_validator.check_income = AsyncMock(return_value=(True, 3200))
    with patch("intake_bot.intake_nodes.HouseholdIncome", HouseholdIncome):
        income = {
            "John Doe": {
                "wages": {"amount": 3200, "period": "month"},
            },
        }
    result, next_node = await record_income(flow_manager, income)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["income"]["is_eligible"] is True
    assert flow_manager.state["income"]["monthly_amount"] == 3200
    assert flow_manager.state["income"]["listing"] == income
    assert "record_assets_receives_benefits_prompt" in next_node


@pytest.mark.asyncio
async def test_record_income_valid_ineligible(flow_manager, patch_validator):
    patch_validator.get_alternative_providers = AsyncMock(return_value="AltProvider")
    patch_validator.check_income = AsyncMock(return_value=(False, 6000))
    with patch("intake_bot.intake_nodes.HouseholdIncome", HouseholdIncome):
        income = {
            "John Doe": {
                "wages": {"amount": 6000, "period": "month"},
            },
        }
    result, next_node = await record_income(flow_manager, income)
    assert isinstance(result, dict)
    assert result["status"] == Status.ERROR
    assert "Alternate providers" in result["error"]
    assert flow_manager.state["income"]["is_eligible"] is False
    assert flow_manager.state["income"]["monthly_amount"] == 6000
    assert flow_manager.state["income"]["listing"] == income
    assert "confirm_income_over_limit_prompt" in next_node


@pytest.mark.asyncio
async def test_record_income_invalid(flow_manager):
    with patch("intake_bot.intake_nodes.HouseholdIncome", HouseholdIncome):
        income = {"bad": "data"}
    result, next_node = await record_income(flow_manager, income)
    assert isinstance(result, dict)
    assert result["status"] == Status.ERROR
    assert "error" in result
    assert "validating the `income`" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_assets_receives_benefits_true(flow_manager):
    result, next_node = await record_assets_receives_benefits(flow_manager, True)
    assert isinstance(result, dict)
    assert result["is_eligible"] is True
    assert flow_manager.state["assets"]["is_eligible"] is True
    assert flow_manager.state["assets"]["listing"] == []
    assert flow_manager.state["assets"]["total_value"] == 0
    assert flow_manager.state["assets"]["receives_benefits"] is True
    assert "record_citizenship_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_receives_benefits_false(flow_manager):
    result, next_node = await record_assets_receives_benefits(flow_manager, False)
    assert result is None
    assert "record_assets_list_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_list_valid_eligible(flow_manager, patch_validator):
    patch_validator.check_assets = AsyncMock(return_value=(True, 7000))
    with patch("intake_bot.intake_nodes.Assets", Assets):
        assets = [{"car": 5000}, {"savings": 2000}]
    result, next_node = await record_assets_list(flow_manager, assets)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["assets"]["is_eligible"] is True
    assert flow_manager.state["assets"]["listing"] == assets
    assert flow_manager.state["assets"]["total_value"] == 7000
    assert flow_manager.state["assets"]["receives_benefits"] is False
    assert "record_citizenship_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_list_valid_ineligible(flow_manager, patch_validator):
    patch_validator.get_alternative_providers = AsyncMock(return_value="AltProvider")
    patch_validator.check_assets = AsyncMock(return_value=(False, 12000))
    with patch("intake_bot.intake_nodes.Assets", Assets):
        assets = [{"car": 5000}, {"savings": 7000}]
    result, next_node = await record_assets_list(flow_manager, assets)
    assert isinstance(result, dict)
    assert result["status"] == Status.ERROR
    assert flow_manager.state["assets"]["is_eligible"] is False
    assert flow_manager.state["assets"]["listing"] == assets
    assert flow_manager.state["assets"]["total_value"] == 12000
    assert flow_manager.state["assets"]["receives_benefits"] is False
    assert "Alternate providers" in result["error"]
    assert "confirm_assets_over_limit_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_list_invalid(flow_manager):
    with patch("intake_bot.intake_nodes.Assets", Assets):
        assets = [{"bad": "data"}]
    result, next_node = await record_assets_list(flow_manager, assets)
    assert result["status"] == Status.ERROR
    assert next_node is None


@pytest.mark.asyncio
async def test_record_citizenship(flow_manager):
    result, next_node = await record_citizenship(flow_manager, True)
    assert isinstance(result, dict)
    assert flow_manager.state["citizenship"]["is_citizen"] is True
    assert "record_emergency_prompt" in next_node


@pytest.mark.asyncio
async def test_record_emergency(flow_manager):
    result, next_node = await record_emergency(flow_manager, True)
    assert isinstance(result, dict)
    assert flow_manager.state["emergency"]["is_emergency"] is True
    assert "complete_intake_prompt" in next_node


@pytest.mark.asyncio
async def test_continue_intake_valid(flow_manager):
    with patch("intake_bot.intake_nodes.prompts.get", return_value={"record_name": True}):
        result, next_node = await continue_intake(flow_manager, "record_name")
    assert result is None
    assert "record_name" in next_node


@pytest.mark.asyncio
async def test_continue_intake_invalid(flow_manager):
    with pytest.raises(ValueError):
        await continue_intake(flow_manager, "not_a_function")


@pytest.mark.asyncio
async def test_end_conversation(flow_manager):
    result, node = await end_conversation(flow_manager)
    assert result is None
    assert "end_prompt" in node


@pytest.mark.asyncio
async def test_caller_ended_conversation(flow_manager):
    result, node = await caller_ended_conversation(flow_manager)
    assert result is None
    assert "caller_ended_conversation_prompt" in node


def test_node_end_conversation():
    node = node_end_conversation()
    assert "end_prompt" in node
    assert "post_actions" in node


def test_node_caller_ended_conversation():
    node = node_caller_ended_conversation()
    assert "caller_ended_conversation_prompt" in node
    assert "post_actions" in node
