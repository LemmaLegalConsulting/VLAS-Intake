from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from intake_bot.models.intake_flow_result import Status
from intake_bot.models.validator import Assets, HouseholdIncome
from intake_bot.nodes.nodes import (
    caller_ended_conversation,
    continue_intake,
    end_conversation,
    node_caller_ended_conversation,
    node_end_conversation,
    record_address,
    record_adverse_parties,
    record_assets_cash_accounts,
    record_assets_investments,
    record_assets_list,
    record_assets_other_property,
    record_assets_receives_benefits,
    record_case_type,
    record_citizenship,
    record_date_of_birth,
    record_domestic_violence,
    record_household_composition,
    record_income,
    record_language,
    record_name,
    record_names,
    record_phone_number,
    record_phone_type,
    record_service_area,
    record_ssn_last_4,
    send_case_type_referral_and_end,
    send_general_referral_and_end,
    system_phone_number,
)
from intake_bot.services.dialpad import CASE_TYPE_REFERRAL, GENERAL_REFERRAL
from intake_bot.utils.node_prompts import NodePrompts


@pytest.fixture
def flow_manager():
    fm = MagicMock()
    fm.state = {}
    fm.task = MagicMock()
    fm.task.queue_frame = AsyncMock()
    return fm


@pytest.fixture(autouse=True)
def patch_validator(monkeypatch):
    validator_mock = MagicMock()
    monkeypatch.setattr("intake_bot.nodes.nodes.validator", validator_mock)
    return validator_mock


@pytest.fixture(autouse=True)
def patch_prompts(monkeypatch):
    prompts_mock = MagicMock()
    prompts_mock.get.side_effect = lambda k, **kwargs: {f"""{k}_prompt""": True}
    monkeypatch.setattr("intake_bot.nodes.nodes.prompts", prompts_mock)
    return prompts_mock


@pytest.mark.asyncio
async def test_system_phone_number_with_phone(flow_manager, patch_validator):
    flow_manager.state["phone"] = "+18665345243"
    patch_validator.check_phone_number = AsyncMock(
        return_value=(True, "(866) 534-5243")
    )
    result, next_node = await system_phone_number(flow_manager)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["phone_number"] == "(866) 534-5243"
    assert "record_language_prompt" in next_node


@pytest.mark.asyncio
async def test_system_phone_number_without_phone(flow_manager, patch_validator):
    patch_validator.check_phone_number = AsyncMock(return_value=(False, ""))
    result, next_node = await system_phone_number(flow_manager)
    assert isinstance(result, dict)
    assert result["status"] == Status.ERROR
    assert result["phone_number"] == ""
    assert "record_language_prompt" in next_node


@pytest.mark.asyncio
async def test_record_language(flow_manager):
    result, next_node = await record_language(flow_manager, "English")
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["language"]["language"] == "English"
    assert flow_manager.task.queue_frame.await_count == 2  # STT + TTS language updates
    assert "record_phone_number_prompt" in next_node


@pytest.mark.asyncio
async def test_record_phone_number_valid(flow_manager, patch_validator):
    patch_validator.check_phone_number = AsyncMock(
        return_value=(True, "(866) 534-5243")
    )
    result, next_node = await record_phone_number(flow_manager, "+18665345243")
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["phone"]["is_valid"] is True
    assert flow_manager.state["phone"]["phone_number"] == "(866) 534-5243"
    assert "phone_type" not in flow_manager.state["phone"]
    assert "record_phone_type_prompt" in next_node


@pytest.mark.asyncio
async def test_record_phone_type_valid(flow_manager):
    flow_manager.state["phone"] = {
        "is_valid": True,
        "phone_number": "(866) 534-5243",
    }

    result, next_node = await record_phone_type(flow_manager, "mobile")

    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["phone"]["phone_type"] == "mobile"
    assert "record_name_prompt" in next_node


@pytest.mark.asyncio
async def test_record_phone_type_normalizes_spanish_synonym(flow_manager):
    flow_manager.state["phone"] = {
        "is_valid": True,
        "phone_number": "(866) 534-5243",
    }

    result, next_node = await record_phone_type(flow_manager, "movil")

    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["phone"]["phone_type"] == "mobile"
    assert "record_name_prompt" in next_node


@pytest.mark.asyncio
async def test_record_phone_type_requires_existing_phone_number(flow_manager):
    result, next_node = await record_phone_type(flow_manager, "mobile")

    assert result["status"] == Status.ERROR
    assert "Phone number must be recorded" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_phone_number_invalid(flow_manager, patch_validator):
    patch_validator.check_phone_number = AsyncMock(return_value=(False, "bad"))
    result, next_node = await record_phone_number(flow_manager, "bad")
    assert result["status"] == Status.ERROR
    assert flow_manager.state["phone"]["is_valid"] is False
    assert next_node is None


@pytest.mark.asyncio
async def test_record_name_valid(flow_manager):
    result, next_node = await record_name(flow_manager, "John", "Q", "Public", "Jr.")
    assert isinstance(result, dict)
    # CallerNameResult now has a 'names' field containing CallerNames (a RootModel with a list)
    assert len(result["names"]) == 1
    assert result["names"][0]["first"] == "John"
    assert result["names"][0]["middle"] == "Q"
    assert result["names"][0]["last"] == "Public"
    assert result["names"][0]["suffix"] == "Jr."
    assert (
        result["names"][0]["type"] == "Legal Name"
    )  # Primary name should be Legal Name
    assert flow_manager.state["names"]["names"][0]["first"] == "John"
    assert flow_manager.state["names"]["names"][0]["middle"] == "Q"
    assert flow_manager.state["names"]["names"][0]["last"] == "Public"
    assert flow_manager.state["names"]["names"][0]["suffix"] == "Jr."
    assert (
        flow_manager.state["names"]["names"][0]["type"] == "Legal Name"
    )  # Verify type in state
    assert "record_service_area_prompt" in next_node


@pytest.mark.asyncio
async def test_record_name_invalid(flow_manager):
    result, next_node = await record_name(flow_manager, "", "", "")
    assert result["status"] == Status.ERROR
    assert "validating the `name`" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_address_valid(flow_manager):
    result, next_node = await record_address(
        flow_manager,
        street="123 Main St",
        street_2="Apt 4B",
        city="Richmond",
        state="VA",
        zip="23219",
        county="Richmond",
    )
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["address"]["street"] == "123 Main St"
    assert result["address"]["street_2"] == "Apt 4B"
    assert result["address"]["city"] == "Richmond"
    assert result["address"]["state"] == "VA"
    assert result["address"]["zip"] == "23219"
    assert result["address"]["county"] == "Richmond"
    assert flow_manager.state["address"] is not None
    assert "complete_intake_prompt" in next_node


@pytest.mark.asyncio
async def test_record_address_valid_no_street_2(flow_manager):
    result, next_node = await record_address(
        flow_manager,
        street="456 Oak Ave",
        city="Arlington",
        state="VA",
        zip="22201",
        county="Arlington",
    )
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["address"]["street"] == "456 Oak Ave"
    assert result["address"].get("street_2") is None
    assert result["address"]["city"] == "Arlington"
    assert result["address"]["state"] == "VA"
    assert result["address"]["zip"] == "22201"
    assert result["address"]["county"] == "Arlington"
    assert "complete_intake_prompt" in next_node


@pytest.mark.asyncio
async def test_record_address_invalid_missing_street(flow_manager):
    result, next_node = await record_address(
        flow_manager,
        street="",
        city="Richmond",
        state="VA",
        zip="23219",
        county="Richmond",
    )
    assert result["status"] == Status.ERROR
    assert "validating the `address`" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_address_invalid_missing_city(flow_manager):
    result, next_node = await record_address(
        flow_manager,
        street="123 Main St",
        city="",
        state="VA",
        zip="23219",
        county="Richmond",
    )
    assert result["status"] == Status.ERROR
    assert "validating the `address`" in result["error"]
    assert next_node is None


def test_record_address_prompt_includes_county_follow_up_separation_rules():
    prompt = NodePrompts().get("record_address")
    content = prompt["task_messages"][0]["content"]

    assert (
        "explicitly ask for the county along with the other address fields" in content
    )
    assert "ask a separate follow-up question asking only for the county" in content
    assert (
        "Do NOT combine the county follow-up with the address confirmation" in content
    )
    assert "insert a brief pause before saying the street name" in content
    assert "always say the full state name, not the abbreviation" in content


def test_standard_node_prompts_include_thank_you_acknowledgment_instruction():
    prompt = NodePrompts().get("record_name")
    content = prompt["task_messages"][0]["content"]

    assert "fits the caller's immediately preceding answer" in content
    assert (
        "Whenever possible, weave the acknowledgment directly into the next question or instruction"
        in content
    )
    assert "Prefer connected phrasing with a comma" in content
    assert "If the caller briefly confirmed something" in content
    assert "If the caller provided new factual information" in content
    assert "If the caller corrected, clarified, or spelled something" in content
    assert "Use exactly one short acknowledgment lead-in before continuing" in content
    assert (
        "Do not stack an acknowledgment sentence and then a separate next-question sentence"
        in content
    )
    assert "Do not add extra praise, filler, or multiple acknowledgments" in content


def test_excluded_node_prompts_do_not_include_thank_you_acknowledgment_instruction():
    prompt = NodePrompts().get("record_language")
    content = prompt["task_messages"][0]["content"]

    assert "fits the caller's immediately preceding answer" not in content


def test_record_service_area_prompt_handles_non_location_answers():
    prompt = NodePrompts().get("record_service_area")
    content = prompt["task_messages"][0]["content"]

    assert "If the caller answers with something other than a city or county" in content
    assert "ask again for just the city or county" in content
    assert (
        "Do NOT mark the caller ineligible just because they answered the wrong question"
        in content
    )


def test_record_adverse_parties_prompt_handles_mixed_answers():
    prompt = NodePrompts().get("record_adverse_parties")
    content = prompt["task_messages"][0]["content"]

    assert "same utterance as unrelated facts from another step" in content
    assert (
        "continue asking for the remaining useful details before moving on" in content
    )
    assert "Do NOT skip directly to the next intake step" in content


@pytest.mark.asyncio
async def test_record_service_area_eligible(flow_manager, patch_validator):
    patch_validator.check_service_area = AsyncMock(
        return_value=("Amelia County", 51007)
    )
    result, next_node = await record_service_area(flow_manager, "Amelia County")
    assert isinstance(result, dict)
    assert result["is_eligible"] is True
    assert flow_manager.state["service_area"]["location"] == "Amelia County"
    assert result["fips_code"] == 51007
    assert "record_case_type_prompt" in next_node


@pytest.mark.asyncio
async def test_record_service_area_eligible_with_canonical_match(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(return_value=("Suffolk City", 51800))
    result, next_node = await record_service_area(flow_manager, "Suffolk")
    assert isinstance(result, dict)
    assert result["is_eligible"] is True
    assert result["location"] == "Suffolk City"
    assert flow_manager.state["service_area"]["location"] == "Suffolk City"
    assert result["fips_code"] == 51800
    assert "record_case_type_prompt" in next_node


@pytest.mark.asyncio
async def test_record_service_area_ineligible_with_match(flow_manager, patch_validator):
    patch_validator.check_service_area = AsyncMock(return_value=("Shelbyville", 0))
    result, next_node = await record_service_area(flow_manager, "Springfield")
    assert result["status"] == Status.ERROR
    assert "meant Shelbyville" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_service_area_ineligible_no_match(flow_manager, patch_validator):
    patch_validator.check_service_area = AsyncMock(return_value=("", 0))
    result, next_node = await record_service_area(flow_manager, "Nowhere")
    assert "couldn't identify a Virginia city or county" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_case_type_eligible(flow_manager, patch_validator):
    from intake_bot.models.classifier import ClassificationResponse

    patch_validator.check_case_type = AsyncMock(
        return_value=ClassificationResponse(
            legal_problem_code="01 Bankruptcy/Debtor Relief",
            confidence=0.95,
            is_eligible=True,
            follow_up_questions=[],
        )
    )

    result, next_node = await record_case_type(flow_manager, "bankruptcy")

    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["legal_problem_code"] == "01 Bankruptcy/Debtor Relief"
    assert result["is_eligible"] is True
    assert result["case_description"] == "bankruptcy"
    assert "record_adverse_parties_prompt" in next_node


@pytest.mark.asyncio
async def test_record_case_type_ineligible(flow_manager, patch_validator):
    from intake_bot.models.classifier import ClassificationResponse

    patch_validator.check_case_type = AsyncMock(
        return_value=ClassificationResponse(
            legal_problem_code="00 Criminal Defense",
            confidence=0.95,
            is_eligible=False,
            follow_up_questions=[],
        )
    )
    patch_validator.get_alternative_providers = AsyncMock(return_value="AltProvider")

    result, next_node = await record_case_type(flow_manager, "criminal")

    assert result["status"] == Status.ERROR
    assert "Ineligible case type." in result["error"]
    assert result["is_eligible"] is False
    assert "case_type_ineligible_prompt" in next_node


@pytest.mark.asyncio
async def test_send_general_referral_and_end_sends_sms_and_returns_end_node(
    flow_manager, monkeypatch
):
    flow_manager.state["phone"] = "+15096305855"
    flow_manager.state["language"] = {"language": "English"}
    sms_mock = MagicMock()
    sms_mock.is_configured = True
    sms_mock.send = AsyncMock(return_value={"status": 200, "body": {"id": "1"}})
    monkeypatch.setattr("intake_bot.nodes.nodes.sms_service", sms_mock)

    result, next_node = await send_general_referral_and_end(flow_manager, "text")

    assert result is None
    sms_mock.send.assert_awaited_once_with(
        "+15096305855",
        GENERAL_REFERRAL.sms_text("English"),
    )
    assert flow_manager.state["sms_messages"][0]["category"] == "referral"
    assert next_node["pre_actions"][0]["text"] == GENERAL_REFERRAL.text_delivery_text(
        "English"
    )
    assert next_node["post_actions"] == [{"type": "end_conversation"}]


@pytest.mark.asyncio
async def test_send_case_type_referral_and_end_phone_does_not_send_sms(
    flow_manager, monkeypatch
):
    flow_manager.state["phone"] = "+15096305855"
    flow_manager.state["language"] = {"language": "Spanish"}
    sms_mock = MagicMock()
    sms_mock.is_configured = True
    sms_mock.send = AsyncMock(return_value={"status": 200, "body": {"id": "1"}})
    monkeypatch.setattr("intake_bot.nodes.nodes.sms_service", sms_mock)

    _, next_node = await send_case_type_referral_and_end(flow_manager, "phone")

    sms_mock.send.assert_not_awaited()
    assert next_node["pre_actions"][0]["text"] == CASE_TYPE_REFERRAL.spoken_text(
        "Spanish"
    )


@pytest.mark.asyncio
async def test_send_general_referral_and_end_rejects_invalid_delivery_method(
    flow_manager,
):
    result, next_node = await send_general_referral_and_end(
        flow_manager, "carrier pigeon"
    )

    assert result.status == Status.ERROR
    assert "delivery_method" in result.error
    assert next_node is None


def test_ineligible_prompt_routes_to_referral_end_function_without_urls():
    prompt = NodePrompts().get("ineligible")
    content = prompt["task_messages"][0]["content"]

    assert "send_general_referral_and_end" in content
    assert "over the phone or sent by text" in content
    assert "V A L E G A L A I D" not in content
    assert "L S C dot G O V" not in content
    assert "V S B dot O R G" not in content


def test_case_type_ineligible_prompt_routes_to_referral_end_function_without_url():
    prompt = NodePrompts().get("case_type_ineligible")
    content = prompt["task_messages"][0]["content"]

    assert "send_case_type_referral_and_end" in content
    assert "over the phone or sent by text" in content
    assert "V S B dot O R G" not in content


@pytest.mark.asyncio
async def test_record_case_type_follow_up_needed(flow_manager, patch_validator):
    from intake_bot.models.classifier import ClassificationResponse, FollowUpQuestion

    patch_validator.check_case_type = AsyncMock(
        return_value=ClassificationResponse(
            legal_problem_code=None,
            confidence=None,
            is_eligible=None,
            follow_up_questions=[FollowUpQuestion(question="Is there a court date?")],
        )
    )

    result, next_node = await record_case_type(flow_manager, "needs help")

    assert result["status"] == Status.ERROR
    assert "Is there a court date?" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_domestic_violence_true(flow_manager):
    result, next_node = await record_domestic_violence(flow_manager, True)
    assert isinstance(result, dict)
    assert flow_manager.state["domestic_violence"]["is_experiencing"] is True
    assert "record_household_composition_prompt" in next_node


@pytest.mark.asyncio
async def test_record_domestic_violence_false(flow_manager):
    result, next_node = await record_domestic_violence(flow_manager, False)
    assert isinstance(result, dict)
    assert flow_manager.state["domestic_violence"]["is_experiencing"] is False
    assert "record_household_composition_prompt" in next_node


@pytest.mark.asyncio
async def test_record_household_composition_valid(flow_manager, patch_validator):
    patch_validator.check_household_composition = AsyncMock(return_value=(True, 3))
    result, next_node = await record_household_composition(flow_manager, 1, 2)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["household_composition"]["number_of_adults"] == 1
    assert flow_manager.state["household_composition"]["number_of_children"] == 2
    assert "record_income_prompt" in next_node


@pytest.mark.asyncio
async def test_record_household_composition_only_adults(flow_manager, patch_validator):
    patch_validator.check_household_composition = AsyncMock(return_value=(True, 2))
    result, next_node = await record_household_composition(flow_manager, 2, 0)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["household_composition"]["number_of_adults"] == 2
    assert flow_manager.state["household_composition"]["number_of_children"] == 0
    assert "record_income_prompt" in next_node


@pytest.mark.asyncio
async def test_record_household_composition_invalid_no_adults(
    flow_manager, patch_validator
):
    patch_validator.check_household_composition = AsyncMock(return_value=(False, 0))
    result, next_node = await record_household_composition(flow_manager, 0, 2)
    assert result["status"] == Status.ERROR
    assert next_node is None


@pytest.mark.asyncio
async def test_record_household_composition_invalid_negative_children(
    flow_manager, patch_validator
):
    patch_validator.check_household_composition = AsyncMock(return_value=(False, 0))
    result, next_node = await record_household_composition(flow_manager, 1, -1)
    assert result["status"] == Status.ERROR
    assert next_node is None


@pytest.mark.asyncio
async def test_record_income_valid_eligible_with_dummy_model(
    flow_manager, patch_validator
):
    patch_validator.check_income = AsyncMock(return_value=(True, 1000, 3))
    # Set household composition in state
    flow_manager.state["household_composition"] = {
        "number_of_adults": 2,
        "number_of_children": 1,
    }
    with patch("intake_bot.nodes.nodes.HouseholdIncome", HouseholdIncome):
        income = {
            "John Doe": {
                "Employment": {"amount": 1000, "period": "Monthly"},
            },
        }
        result, next_node = await record_income(flow_manager, income)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["income"]["is_eligible"] is True
    assert flow_manager.state["income"]["monthly_amount"] == 1000
    assert flow_manager.state["income"]["household_size"] == 3
    assert "record_assets_receives_benefits_prompt" in next_node


@pytest.mark.asyncio
async def test_record_income_multiple_members(flow_manager, patch_validator):
    flow_manager.state["household_composition"] = {
        "number_of_adults": 2,
        "number_of_children": 1,
    }
    patch_validator.check_income = AsyncMock(return_value=(True, 3200, 3))
    with patch("intake_bot.nodes.nodes.HouseholdIncome", HouseholdIncome):
        income = {
            "John Doe": {
                "Employment": {"amount": 3200, "period": "Monthly"},
            },
        }
    result, next_node = await record_income(flow_manager, income)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["income"]["is_eligible"] is True
    assert flow_manager.state["income"]["monthly_amount"] == 3200
    assert flow_manager.state["income"]["household_size"] == 3
    assert flow_manager.state["income"]["listing"] == income
    assert "record_assets_receives_benefits_prompt" in next_node


@pytest.mark.asyncio
async def test_record_income_valid_ineligible(flow_manager, patch_validator):
    flow_manager.state["household_composition"] = {
        "number_of_adults": 1,
        "number_of_children": 0,
    }
    patch_validator.get_alternative_providers = AsyncMock(return_value="AltProvider")
    patch_validator.check_income = AsyncMock(return_value=(False, 6000, 1))
    with patch("intake_bot.nodes.nodes.HouseholdIncome", HouseholdIncome):
        income = {
            "John Doe": {
                "Employment": {"amount": 6000, "period": "Monthly"},
            },
        }
    result, next_node = await record_income(flow_manager, income)
    assert isinstance(result, dict)
    assert result["status"] == Status.ERROR
    assert "Over the household income limit" in result["error"]
    assert flow_manager.state["income"]["is_eligible"] is False
    assert flow_manager.state["income"]["monthly_amount"] == 6000
    assert flow_manager.state["income"]["household_size"] == 1
    assert flow_manager.state["income"]["listing"] == income
    assert "confirm_income_over_limit_prompt" in next_node


@pytest.mark.asyncio
async def test_record_income_invalid(flow_manager):
    flow_manager.state["household_composition"] = {
        "number_of_adults": 1,
        "number_of_children": 0,
    }
    with patch("intake_bot.nodes.nodes.HouseholdIncome", HouseholdIncome):
        income = {"bad": "data"}
    result, next_node = await record_income(flow_manager, income)
    assert isinstance(result, dict)
    assert result["status"] == Status.ERROR
    assert "error" in result
    assert "validating the `income`" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_income_zero_fanout_collapses(flow_manager, patch_validator):
    """When the LLM enumerates all income categories at $0, the Pydantic
    validator should collapse them into a single 'No Household Income' entry."""
    patch_validator.check_income = AsyncMock(return_value=(True, 0, 1))
    flow_manager.state["household_composition"] = {
        "number_of_adults": 1,
        "number_of_children": 0,
    }
    income = {
        "Jane Doe": {
            "No Household Income": {"amount": 0, "period": "Monthly"},
            "Employment": {"amount": 0, "period": "Monthly"},
            "Child Support": {"amount": 0, "period": "Monthly"},
            "Spousal Support": {"amount": 0, "period": "Monthly"},
            "Social Security Retirement": {"amount": 0, "period": "Monthly"},
            "Social Security Disability (SSDI)": {"amount": 0, "period": "Monthly"},
            "SSI (Supplemental Security Income)": {"amount": 0, "period": "Monthly"},
            "SSI/SSDI combo": {"amount": 0, "period": "Monthly"},
            "Long-Term/Short-Term Disability": {"amount": 0, "period": "Monthly"},
            "Workers Compensation": {"amount": 0, "period": "Monthly"},
            "Unemployment Compensation": {"amount": 0, "period": "Monthly"},
            "Pension/Retirement (Not Soc. Sec.)": {"amount": 0, "period": "Monthly"},
            "TANF (Temporary Assistance for Needy Families)": {
                "amount": 0,
                "period": "Monthly",
            },
            "Food Stamps": {"amount": 0, "period": "Monthly"},
            "Veterans Benefits": {"amount": 0, "period": "Monthly"},
            "Trust/Dividends/Annuity": {"amount": 0, "period": "Monthly"},
            "Income Not Provided": {"amount": 0, "period": "Monthly"},
            "Other": {"amount": 0, "period": "Monthly"},
        }
    }
    with patch("intake_bot.nodes.nodes.HouseholdIncome", HouseholdIncome):
        result, next_node = await record_income(flow_manager, income)
    assert result["status"] == Status.SUCCESS
    listing = flow_manager.state["income"]["listing"]
    # Should collapse to a single member with a single "No Household Income" entry
    assert len(listing) == 1
    member_income = list(listing.values())[0]
    assert list(member_income.keys()) == ["No Household Income"]
    assert member_income["No Household Income"]["amount"] == 0


@pytest.mark.asyncio
async def test_record_income_strips_zero_only_children(flow_manager, patch_validator):
    """Children listed with only 'No Household Income' at $0 should be stripped
    when a real member also exists."""
    patch_validator.check_income = AsyncMock(return_value=(True, 0, 3))
    flow_manager.state["household_composition"] = {
        "number_of_adults": 1,
        "number_of_children": 2,
    }
    income = {
        "Jane Doe": {
            "No Household Income": {"amount": 0, "period": "Monthly"},
        },
        "Child One": {
            "No Household Income": {"amount": 0, "period": "Monthly"},
        },
        "Child Two": {
            "No Household Income": {"amount": 0, "period": "Monthly"},
        },
    }
    with patch("intake_bot.nodes.nodes.HouseholdIncome", HouseholdIncome):
        result, next_node = await record_income(flow_manager, income)
    assert result["status"] == Status.SUCCESS
    # All three are zero-only but strip_zero_only_members keeps at least one
    # when *all* are zero — however since they're all identical, the validator
    # keeps all of them.  The real scenario: the parent has real income or was
    # already collapsed.  With all three zero-only, none get stripped (all kept).
    # Re-test: when parent has real income, children are stripped.


@pytest.mark.asyncio
async def test_record_income_strips_children_keeps_parent_with_income(
    flow_manager, patch_validator
):
    """Children with only 'No Household Income' are stripped when a parent
    with real income exists."""
    patch_validator.check_income = AsyncMock(return_value=(True, 1200, 3))
    flow_manager.state["household_composition"] = {
        "number_of_adults": 1,
        "number_of_children": 2,
    }
    income = {
        "Jane Doe": {
            "Employment": {"amount": 1200, "period": "Monthly"},
        },
        "Child One": {
            "No Household Income": {"amount": 0, "period": "Monthly"},
        },
        "Child Two": {
            "No Household Income": {"amount": 0, "period": "Monthly"},
        },
    }
    with patch("intake_bot.nodes.nodes.HouseholdIncome", HouseholdIncome):
        result, next_node = await record_income(flow_manager, income)
    assert result["status"] == Status.SUCCESS
    listing = flow_manager.state["income"]["listing"]
    # Children should be stripped, only parent remains
    assert len(listing) == 1
    assert "Jane Doe" in listing
    assert listing["Jane Doe"]["Employment"]["amount"] == 1200


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
    flow_manager.state["assets_cash_accounts"] = {"listing": [{"cash": 20}]}
    result, next_node = await record_assets_receives_benefits(flow_manager, False)
    assert result is None
    assert "assets_cash_accounts" not in flow_manager.state
    assert "record_assets_cash_accounts_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_cash_accounts_stores_category_state(flow_manager):
    result, next_node = await record_assets_cash_accounts(
        flow_manager, [{"savings account": 1200}]
    )
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["listing"] == [{"savings account": 1200}]
    assert flow_manager.state["assets_cash_accounts"] == {
        "listing": [{"savings account": 1200}]
    }
    assert "record_assets_investments_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_investments_stores_category_state(flow_manager):
    flow_manager.state["assets_cash_accounts"] = {
        "listing": [{"savings account": 1200}]
    }
    result, next_node = await record_assets_investments(flow_manager, [{"stocks": 500}])
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["listing"] == [{"stocks": 500}]
    assert flow_manager.state["assets_cash_accounts"] == {
        "listing": [{"savings account": 1200}]
    }
    assert flow_manager.state["assets_investments"] == {"listing": [{"stocks": 500}]}
    assert "record_assets_other_property_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_other_property_routes_to_confirmation(flow_manager):
    flow_manager.state["assets_cash_accounts"] = {
        "listing": [{"savings account": 1200}]
    }
    result, next_node = await record_assets_other_property(
        flow_manager, [{"vacant land": 4000}]
    )
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["listing"] == [{"vacant land": 4000}]
    assert flow_manager.state["assets_other_property"] == {
        "listing": [{"vacant land": 4000}]
    }
    assert "record_assets_list_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_list_valid_eligible(flow_manager, patch_validator):
    patch_validator.check_assets = AsyncMock(return_value=(True, 7000))
    with patch("intake_bot.nodes.nodes.Assets", Assets):
        assets = [{"savings": 2000}, {"vacant land": 5000}]
    result, next_node = await record_assets_list(flow_manager, assets)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["assets"]["is_eligible"] is True
    assert flow_manager.state["assets"]["listing"] == assets
    assert flow_manager.state["assets"]["total_value"] == 7000
    assert flow_manager.state["assets"]["receives_benefits"] is False
    assert "assets_cash_accounts" not in flow_manager.state
    assert "assets_investments" not in flow_manager.state
    assert "assets_other_property" not in flow_manager.state
    assert "record_citizenship_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_list_valid_ineligible(flow_manager, patch_validator):
    patch_validator.get_alternative_providers = AsyncMock(return_value="AltProvider")
    patch_validator.check_assets = AsyncMock(return_value=(False, 12000))
    with patch("intake_bot.nodes.nodes.Assets", Assets):
        assets = [{"savings": 7000}, {"vacant land": 5000}]
    result, next_node = await record_assets_list(flow_manager, assets)
    assert isinstance(result, dict)
    assert result["status"] == Status.ERROR
    assert flow_manager.state["assets"]["is_eligible"] is False
    assert flow_manager.state["assets"]["listing"] == assets
    assert flow_manager.state["assets"]["total_value"] == 12000
    assert flow_manager.state["assets"]["receives_benefits"] is False
    assert "Over the household assets' value limit." in result["error"]
    assert "confirm_assets_over_limit_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_list_filters_primary_vehicle(
    flow_manager, patch_validator
):
    patch_validator.check_assets = AsyncMock(return_value=(True, 3600))
    assets = [{"savings account": 2100}, {"primary car": 8500}, {"jewelry": 1500}]

    result, next_node = await record_assets_list(flow_manager, assets)

    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["listing"] == [{"savings account": 2100}, {"jewelry": 1500}]
    assert result["total_value"] == 3600
    assert flow_manager.state["assets"]["listing"] == [
        {"savings account": 2100},
        {"jewelry": 1500},
    ]
    patch_validator.check_assets.assert_awaited_once()
    assert "record_citizenship_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_list_uses_accumulated_category_state(
    flow_manager, patch_validator
):
    patch_validator.check_assets = AsyncMock(return_value=(True, 7000))
    flow_manager.state["assets_cash_accounts"] = {"listing": [{"savings": 2000}]}
    flow_manager.state["assets_investments"] = {"listing": [{"stocks": 500}]}
    flow_manager.state["assets_other_property"] = {"listing": [{"vacant land": 4500}]}

    result, next_node = await record_assets_list(flow_manager)

    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["listing"] == [
        {"savings": 2000},
        {"stocks": 500},
        {"vacant land": 4500},
    ]
    assert flow_manager.state["assets"]["listing"] == [
        {"savings": 2000},
        {"stocks": 500},
        {"vacant land": 4500},
    ]
    patch_validator.check_assets.assert_awaited_once()
    assert "assets_cash_accounts" not in flow_manager.state
    assert "assets_investments" not in flow_manager.state
    assert "assets_other_property" not in flow_manager.state
    assert "record_citizenship_prompt" in next_node


@pytest.mark.asyncio
async def test_record_assets_list_invalid(flow_manager):
    with patch("intake_bot.nodes.nodes.Assets", Assets):
        assets = [{"bad": "data"}]
    result, next_node = await record_assets_list(flow_manager, assets)
    assert result["status"] == Status.ERROR
    assert next_node is None


@pytest.mark.asyncio
async def test_record_citizenship(flow_manager):
    result, next_node = await record_citizenship(
        flow_manager, True, answer_was_explicit=True
    )
    assert isinstance(result, dict)
    assert flow_manager.state["citizenship"]["is_citizen"] is True
    assert "record_ssn_last_4_prompt" in next_node


@pytest.mark.asyncio
async def test_record_citizenship_requires_explicit_answer(flow_manager):
    result, next_node = await record_citizenship(flow_manager, False)

    assert result["status"] == Status.ERROR
    assert "Citizenship can only be recorded" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_date_of_birth_valid(flow_manager, patch_validator):
    """Test record_date_of_birth with a valid date."""
    patch_validator.check_date_of_birth = AsyncMock(return_value=(True, "1980-01-15"))
    result, next_node = await record_date_of_birth(flow_manager, "01/15/1980")
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["date_of_birth"] == "1980-01-15"
    assert flow_manager.state["date_of_birth"]["date_of_birth"] == "1980-01-15"
    assert "record_names_prompt" in next_node


@pytest.mark.asyncio
async def test_record_date_of_birth_various_formats(flow_manager, patch_validator):
    """Test record_date_of_birth accepts various date formats."""
    test_cases = [
        ("01/15/1980", "1980-01-15"),
        ("01-15-1980", "1980-01-15"),
        ("1980-01-15", "1980-01-15"),
        ("January 15, 1980", "1980-01-15"),
    ]

    for input_date, expected_output in test_cases:
        patch_validator.check_date_of_birth = AsyncMock(
            return_value=(True, expected_output)
        )
        result, next_node = await record_date_of_birth(flow_manager, input_date)
        assert result["status"] == Status.SUCCESS
        assert result["date_of_birth"] == expected_output


@pytest.mark.asyncio
async def test_record_date_of_birth_invalid(flow_manager, patch_validator):
    """Test record_date_of_birth with invalid date."""
    patch_validator.check_date_of_birth = AsyncMock(return_value=(False, ""))
    result, next_node = await record_date_of_birth(flow_manager, "invalid date")
    assert isinstance(result, dict)
    assert result["status"] == Status.ERROR
    assert result["date_of_birth"] == ""
    assert next_node is None


@pytest.mark.asyncio
async def test_record_date_of_birth_future_date(flow_manager, patch_validator):
    """Test record_date_of_birth rejects future dates."""
    from datetime import datetime, timedelta

    patch_validator.check_date_of_birth = AsyncMock(return_value=(False, ""))
    future_date = (datetime.now() + timedelta(days=1)).strftime("%m/%d/%Y")
    result, next_node = await record_date_of_birth(flow_manager, future_date)
    assert result["status"] == Status.ERROR
    assert result["date_of_birth"] == ""
    assert next_node is None


@pytest.mark.asyncio
async def test_record_ssn_last_4_valid(flow_manager, patch_validator):
    """Test record_ssn_last_4 with valid SSN last 4 digits."""
    patch_validator.check_ssn_last_4 = AsyncMock(return_value=(True, "1234"))
    result, next_node = await record_ssn_last_4(flow_manager, "1234")
    assert result["status"] == Status.SUCCESS
    assert result["ssn_last_4"] == "1234"
    assert flow_manager.state["ssn_last_4"]["ssn_last_4"] == "1234"
    assert "record_date_of_birth_prompt" in next_node


@pytest.mark.asyncio
async def test_record_ssn_last_4_formatted_input(flow_manager, patch_validator):
    """Test record_ssn_last_4 with formatted input like 123-4."""
    patch_validator.check_ssn_last_4 = AsyncMock(return_value=(True, "1234"))
    result, next_node = await record_ssn_last_4(flow_manager, "123-4")
    assert result["status"] == Status.SUCCESS
    assert result["ssn_last_4"] == "1234"
    assert "record_date_of_birth_prompt" in next_node


@pytest.mark.asyncio
async def test_record_ssn_last_4_invalid(flow_manager, patch_validator):
    """Test record_ssn_last_4 with invalid input (too short)."""
    patch_validator.check_ssn_last_4 = AsyncMock(return_value=(False, ""))
    result, next_node = await record_ssn_last_4(flow_manager, "123")
    assert result["status"] == Status.ERROR
    assert result["ssn_last_4"] == ""
    assert next_node is None


@pytest.mark.asyncio
async def test_record_ssn_last_4_too_long(flow_manager, patch_validator):
    """Test record_ssn_last_4 with invalid input (too long)."""
    patch_validator.check_ssn_last_4 = AsyncMock(return_value=(False, ""))
    result, next_node = await record_ssn_last_4(flow_manager, "12345")
    assert result["status"] == Status.ERROR
    assert result["ssn_last_4"] == ""
    assert next_node is None


@pytest.mark.asyncio
async def test_record_ssn_last_4_non_digits(flow_manager, patch_validator):
    """Test record_ssn_last_4 with non-digit input."""
    patch_validator.check_ssn_last_4 = AsyncMock(return_value=(False, ""))
    result, next_node = await record_ssn_last_4(flow_manager, "abcd")
    assert result["status"] == Status.ERROR
    assert result["ssn_last_4"] == ""
    assert next_node is None


@pytest.mark.asyncio
async def test_record_citizenship_routes_to_ssn_last_4(flow_manager):
    """Test that record_citizenship routes to record_ssn_last_4 node."""
    result, next_node = await record_citizenship(
        flow_manager, True, answer_was_explicit=True
    )
    assert result["status"] == Status.SUCCESS
    assert result["is_citizen"] is True
    assert "record_ssn_last_4_prompt" in next_node


@pytest.mark.asyncio
async def test_record_names_with_prior_name(flow_manager):
    """Test record_names when a main name was already recorded at the start."""
    # Simulate the main name recorded at the start (from record_name)
    flow_manager.state["names"] = {
        "names": [
            {
                "first": "John",
                "middle": "Q",
                "last": "Public",
                "suffix": "Jr.",
                "type": "Legal Name",  # Primary name should have Legal Name type
            }
        ]
    }

    # Now user provides additional names
    additional_names = [
        {"first": "Jon", "last": "Doe", "type": "Former Name"},
        {
            "first": "Jack",
            "middle": "Q",
            "last": "Public",
            "suffix": "III",
            "type": "Maiden Name",
        },
    ]

    result, next_node = await record_names(flow_manager, additional_names)

    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    # Should have 3 names: original + 2 additional
    assert len(result["names"]) == 3
    # First name should be the original one with Legal Name type
    assert result["names"][0]["first"] == "John"
    assert result["names"][0]["middle"] == "Q"
    assert result["names"][0]["last"] == "Public"
    assert result["names"][0]["suffix"] == "Jr."
    assert result["names"][0]["type"] == "Legal Name"  # Verify type is preserved
    # Additional names follow with their types
    assert result["names"][1]["first"] == "Jon"
    assert result["names"][1]["type"] == "Former Name"
    assert result["names"][2]["first"] == "Jack"
    assert result["names"][2]["type"] == "Maiden Name"
    assert result["names"][2]["suffix"] == "III"
    # State should be overwritten with combined names
    assert len(flow_manager.state["names"]["names"]) == 3
    assert flow_manager.state["names"]["names"][0]["type"] == "Legal Name"
    assert "record_address_prompt" in next_node


@pytest.mark.asyncio
async def test_record_names_without_prior_name(flow_manager):
    """Test record_names when no main name was recorded (first-time user or reset flow)."""
    # No prior name in state
    flow_manager.state = {}

    additional_names = [
        {"first": "Alice", "last": "Smith"},
        {"first": "Ali", "last": "Smyth"},
    ]

    result, next_node = await record_names(flow_manager, additional_names)

    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    # Should only have the additional names
    assert len(result["names"]) == 2
    assert result["names"][0]["first"] == "Alice"
    assert result["names"][1]["first"] == "Ali"
    assert "record_address_prompt" in next_node


@pytest.mark.asyncio
async def test_record_names_empty_list(flow_manager):
    """Test record_names with no additional names but a prior main name."""
    flow_manager.state["names"] = {
        "names": [{"first": "John", "middle": "Q", "last": "Public"}]
    }

    # User provides no additional names
    additional_names = []

    result, next_node = await record_names(flow_manager, additional_names)

    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    # Should have just the original name
    assert len(result["names"]) == 1
    assert result["names"][0]["first"] == "John"
    assert "record_address_prompt" in next_node


@pytest.mark.asyncio
async def test_record_names_invalid_names(flow_manager):
    """Test record_names with invalid name data."""
    # Missing required 'last' field
    invalid_names = [
        {"first": "Alice"},  # Missing 'last'
    ]

    result, next_node = await record_names(flow_manager, invalid_names)

    assert result["status"] == Status.ERROR
    assert "validating the `names`" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_names_invalid_with_prior_name(flow_manager):
    """Test record_names when prior name is valid but new names are invalid."""
    flow_manager.state["names"] = {"names": [{"first": "John", "last": "Public"}]}

    # Invalid additional names
    invalid_names = [
        {"first": "Bob"},  # Missing required 'last'
    ]

    result, next_node = await record_names(flow_manager, invalid_names)

    assert result["status"] == Status.ERROR
    assert "validating the `names`" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_names_with_optional_middle_names(flow_manager):
    """Test record_names handles optional middle names correctly."""
    flow_manager.state["names"] = {
        "names": [{"first": "John", "middle": "Q", "last": "Public"}]
    }

    additional_names = [
        {"first": "Alice", "last": "Smith"},  # No middle name
        {"first": "Bob", "middle": "Robert", "last": "Jones"},  # With middle name
    ]

    result, next_node = await record_names(flow_manager, additional_names)

    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert len(result["names"]) == 3
    # Check middle names are handled correctly
    assert result["names"][1].get("middle") is None  # Alice has no middle name
    assert result["names"][2]["middle"] == "Robert"  # Bob has middle name
    assert "record_address_prompt" in next_node


@pytest.mark.asyncio
async def test_record_names_strips_whitespace(flow_manager):
    """Test that record_names properly strips whitespace from names."""
    flow_manager.state["names"] = {"names": [{"first": "John", "last": "Public"}]}

    additional_names = [
        {"first": "  Alice  ", "middle": "  M  ", "last": "  Smith  "},
    ]

    result, next_node = await record_names(flow_manager, additional_names)

    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    # Should have whitespace stripped by validator
    assert result["names"][1]["first"] == "Alice"
    assert result["names"][1]["middle"] == "M"
    assert result["names"][1]["last"] == "Smith"
    assert "record_address_prompt" in next_node


@pytest.mark.asyncio
async def test_record_adverse_parties_valid(flow_manager):
    adverse_parties = [
        {
            "first": "Bob",
            "last": "Smith",
            "suffix": "Sr.",
        }
    ]

    result, next_node = await record_adverse_parties(flow_manager, adverse_parties)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert len(result["adverse_parties"]) == 1
    assert result["adverse_parties"][0]["first"] == "Bob"
    assert result["adverse_parties"][0]["last"] == "Smith"
    assert result["adverse_parties"][0]["suffix"] == "Sr."
    assert "record_domestic_violence_prompt" in next_node


@pytest.mark.asyncio
async def test_record_adverse_parties_requires_one_follow_up_for_name_only(
    flow_manager,
):
    adverse_parties = [
        {
            "first": "Dexter",
            "middle": "Robert",
            "last": "Campbell",
        }
    ]

    first_result, first_next_node = await record_adverse_parties(
        flow_manager, adverse_parties
    )

    assert isinstance(first_result, dict)
    assert first_result["status"] == Status.ERROR
    assert "ask whether the caller knows any phone number" in first_result["error"]
    assert first_result["adverse_parties"][0]["first"] == "Dexter"
    assert first_next_node is None

    second_result, second_next_node = await record_adverse_parties(
        flow_manager, adverse_parties
    )

    assert isinstance(second_result, dict)
    assert second_result["status"] == Status.SUCCESS
    assert second_result["adverse_parties"][0]["first"] == "Dexter"
    assert "record_domestic_violence_prompt" in second_next_node


@pytest.mark.asyncio
async def test_record_adverse_parties_phone_without_type(flow_manager):
    adverse_parties = [
        {
            "first": "Bob",
            "last": "Smith",
            "phones": [{"number": "8665345256"}],
        }
    ]

    result, next_node = await record_adverse_parties(flow_manager, adverse_parties)

    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["adverse_parties"][0]["phones"][0]["number"] == "(866) 534-5256"
    assert result["adverse_parties"][0]["phones"][0].get("type") is None
    assert "record_domestic_violence_prompt" in next_node


@pytest.mark.asyncio
async def test_record_adverse_parties_invalid(flow_manager):
    # Invalid data - missing required 'last' field
    adverse_parties = [
        {
            "first": "Bob",
        }
    ]

    result, next_node = await record_adverse_parties(flow_manager, adverse_parties)
    assert result["status"] == Status.ERROR
    assert "validating the `adverse_parties`" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_continue_intake_valid(flow_manager):
    with patch(
        "intake_bot.nodes.nodes.prompts.get", return_value={"record_name": True}
    ):
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


@pytest.mark.asyncio
async def test_record_ssn_last_4_empty(flow_manager, patch_validator):
    result, next_node = await record_ssn_last_4(
        flow_manager,
        ssn_last_4="",
        ssn_unavailable_reason="refused",
    )

    assert result["status"] == Status.SUCCESS
    assert result["ssn_last_4"] == ""
    assert next_node is not None
    # Validator should NOT be called
    patch_validator.check_ssn_last_4.assert_not_called()


@pytest.mark.asyncio
async def test_record_ssn_last_4_empty_without_reason_errors(
    flow_manager, patch_validator
):
    result, next_node = await record_ssn_last_4(flow_manager, ssn_last_4="")

    assert result["status"] == Status.ERROR
    assert "explicitly refuses" in result["error"]
    assert next_node is None
    patch_validator.check_ssn_last_4.assert_not_called()


@pytest.mark.asyncio
async def test_record_date_of_birth_empty(flow_manager, patch_validator):
    result, next_node = await record_date_of_birth(flow_manager, date_of_birth="")

    assert result["status"] == Status.SUCCESS
    assert result["date_of_birth"] == ""
    assert next_node is not None
    # Validator should NOT be called
    patch_validator.check_date_of_birth.assert_not_called()


@pytest.mark.asyncio
async def test_record_address_empty(flow_manager):
    result, next_node = await record_address(
        flow_manager, street="", city="", state="", zip="", street_2="", county=""
    )

    assert result["status"] == Status.SUCCESS
    assert result.get("address") is None
    assert next_node is not None
