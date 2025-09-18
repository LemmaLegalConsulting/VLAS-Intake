import sys
from typing import Literal

from loguru import logger
from pipecat_flows import (
    ContextStrategy,
    ContextStrategyConfig,
    FlowManager,
    FlowResult,
    NodeConfig,
)
from pydantic import ValidationError

from intake_bot.env_var import get_env_var
from intake_bot.intake_arg_models import Assets, HouseholdIncome
from intake_bot.intake_results import (
    AssetsResult,
    CaseTypeResult,
    CitizenshipResult,
    ConflictCheckResult,
    DomesticViolenceResult,
    EmergencyResult,
    IncomeResult,
    NameResult,
    PhoneNumberResult,
    ServiceAreaResult,
)
from intake_bot.prompts import Prompts
from intake_bot.remote import MockRemoteSystem


def status_helper(status: bool) -> Literal["success", "failure"]:
    """Helper for FlowResult's _status_ value."""
    return "success" if status else "failure"


# Initialize Prompts
prompts = Prompts()


# Initialize mock system
remote_system = MockRemoteSystem()


######################################################################
# Nodes
######################################################################


def node_initial() -> NodeConfig:
    """Create initial node for welcoming the caller. Allow the conversation to be ended."""
    initial_prompt = get_env_var("TEST_INITIAL_PROMPT", default="initial")
    try:
        initial_function = getattr(
            sys.modules[__name__],
            get_env_var("TEST_INITIAL_FUNCTION", default="system_phone_number"),
        )
    except AttributeError:
        raise ValueError(f"""Function '{initial_function}' does not exist.""")

    return {
        **prompts.get("primary_role_message"),
        **prompts.get(initial_prompt),
        "functions": [initial_function, caller_ended_conversation, end_conversation],
    }


def node_partial_reset_with_summary() -> NodeConfig:
    return {
        **prompts.get("primary_role_message"),
        "context_strategy": ContextStrategyConfig(
            strategy=ContextStrategy.RESET_WITH_SUMMARY,
            summary_prompt=prompts.get("reset_with_summary"),
        ),
    }


######################################################################
# Functions - Main Flow
######################################################################


async def system_phone_number(flow_manager: FlowManager) -> tuple[PhoneNumberResult, NodeConfig]:
    """
    This function checks if the phone system recieved the caller's phone number; if so, confirms the number with the caller; if not, collects the caller's phone number.
    """
    phone = flow_manager.state.get("phone")
    logger.debug(f"""System phone number: {phone}""")
    status = status_helper(phone)
    if status == "success":
        result = PhoneNumberResult(status=status, phone=phone)
    else:
        result = None

    next_node = NodeConfig(
        {
            **prompts.get("record_phone_number"),
            "functions": [record_phone_number, caller_ended_conversation, end_conversation],
        }
    )
    return result, next_node


async def record_phone_number(
    flow_manager: FlowManager, phone: str
) -> tuple[PhoneNumberResult, NodeConfig]:
    """
    Collect the caller's phone number.

    Args:
        phone (str): The caller's 10 digit phone number.
    """

    logger.debug(f"""Twilio phone number: {flow_manager.state.get("phone")}""")

    valid_phone, phone = await remote_system.valid_phone_number(phone=phone)

    logger.debug(f"""Phone: {phone}""")
    logger.debug(f"""Valid: {valid_phone}""")

    status = status_helper(valid_phone)

    if status == "success":
        result = PhoneNumberResult(status=status, phone=phone)
        flow_manager.state["phone"] = phone
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_name"),
                "functions": [record_name, caller_ended_conversation, end_conversation],
            }
        )
    else:
        result = FlowResult(status=status, error="Invalid phone number")
        next_node = None
    return result, next_node


async def record_name(
    flow_manager: FlowManager, first: str, middle: str, last: str
) -> tuple[NameResult, NodeConfig]:
    """
    Record the caller's name.

    Args:
        first (str): The caller's first name.
        middle (str): The caller's middle name.
        last (str): The caller's last name.
    """

    first = first.strip()
    middle = middle.strip()
    last = last.strip()

    logger.debug(f"""First: {first}""")
    logger.debug(f"""Middle: {middle}""")
    logger.debug(f"""Last: {last}""")

    status = status_helper(first and last)

    if status == "success":
        result = NameResult(status=status, first=first, middle=middle, last=last)
        flow_manager.state["name first"] = first
        flow_manager.state["name middle"] = middle
        flow_manager.state["name last"] = last
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_service_area"),
                "functions": [record_service_area, caller_ended_conversation, end_conversation],
            }
        )
    else:
        result = FlowResult(status=status, error="Required: first name and last name")
        next_node = None
    return result, next_node


async def record_service_area(
    flow_manager: FlowManager, service_area: str
) -> tuple[ServiceAreaResult, NodeConfig]:
    """
    Record the service area.

    Args:
        service_area (str): The location of the caller or the legal incident. Must be a city or county.
    """

    match = await remote_system.check_service_area(service_area)
    if match == service_area:
        is_eligible = True
    else:
        is_eligible = False
    status = status_helper(is_eligible)

    if status == "success":
        result = ServiceAreaResult(
            status=status, service_area=service_area, is_eligible=is_eligible, match=match
        )
        flow_manager.state["service area"] = service_area
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_case_type"),
                "functions": [record_case_type, caller_ended_conversation, end_conversation],
            }
        )
    else:
        if match:
            result = FlowResult(
                status=status, error=f"No exact match found. Maybe you meant {match}?"
            )
            next_node = None
        else:
            result["error"] = (
                f"""Not in our service area. Alternate providers: {await remote_system.get_alternative_providers()}"""
            )
            next_node = NodeConfig(
                node_partial_reset_with_summary()
                | {
                    **prompts.get("ineligible"),
                    "functions": [end_conversation],
                }
            )
    return result, next_node


async def record_case_type(
    flow_manager: FlowManager, case_type: str
) -> tuple[CaseTypeResult, NodeConfig]:
    """
    Check eligibility of caller's type of case.

    Args:
        case_type (str): The type of legal case that the caller has.
    """

    is_eligible = await remote_system.check_case_type(case_type=case_type)
    flow_manager.state["case type"] = case_type
    flow_manager.state["case type eligible"] = is_eligible
    status = status_helper(is_eligible)
    result = CaseTypeResult(status=status, case_type=case_type)

    if status == "success":
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("conflict_check"),
                "functions": [conflict_check, caller_ended_conversation, end_conversation],
            }
        )
    else:
        result["error"] = (
            f"""Ineligible case type. Alternate providers: {await remote_system.get_alternative_providers()}"""
        )
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("ineligible"),
                "functions": [end_conversation],
            }
        )
    return result, next_node


async def conflict_check(
    flow_manager: FlowManager, opposing_party_members: list[str]
) -> tuple[CaseTypeResult, NodeConfig]:
    """
    Check for conflicts of interest with the caller's case.

    Args:
        opposing_party_members (list[str]): The members of the opposing party.
    """

    # TODO: Need to see what LegalServer's conflict-check API looks like;
    # may need to ask for other related names, not just adverse;
    # may need to perform additional searches/checks for the caller
    # to see if they had previous cases that might disqualify.
    # Probably flag as "potential conflict" and pass them on in many cases.

    there_is_a_conflict = await remote_system.check_conflict_of_interest(
        opposing_party_members=opposing_party_members
    )

    flow_manager.state["conflict"] = there_is_a_conflict
    flow_manager.state["conflict list"] = opposing_party_members

    status = status_helper(not there_is_a_conflict)
    result = ConflictCheckResult(status=status, there_is_a_conflict=there_is_a_conflict)

    if status == "success":
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_domestic_violence"),
                "functions": [
                    record_domestic_violence,
                    caller_ended_conversation,
                    end_conversation,
                ],
            }
        )
    else:
        result["error"] = (
            f"""There is a representation conflict. Alternate providers: {await remote_system.get_alternative_providers()}"""
        )
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("ineligible"),
                "functions": [end_conversation],
            }
        )
    return result, next_node


async def record_domestic_violence(
    flow_manager: FlowManager, experiencing_domestic_violence: bool
) -> tuple[None, NodeConfig]:
    """
    Record if the caller experiencing domestic violence or not.

    Args:
        experiencing_domestic_violence (bool): The caller's answer that they are or are not experiencing domestic violence.
    """

    result = DomesticViolenceResult(
        status="success", experiencing_domestic_violence=experiencing_domestic_violence
    )

    flow_manager.state["domestic violence"] = experiencing_domestic_violence

    next_node = NodeConfig(
        node_partial_reset_with_summary()
        | {
            **prompts.get("record_income"),
            "functions": [record_income, caller_ended_conversation, end_conversation],
        }
    )
    return result, next_node


async def record_income(
    flow_manager: FlowManager, income: HouseholdIncome
) -> tuple[IncomeResult, NodeConfig]:
    """
    Collect income information for all household members and determine eligibility.

    Args:
        income (HouseholdIncome):
            A Pydantic model where each key is a household member's name (str),
            and each value is a MemberIncome model mapping income type (str) to an IncomeDetail.
            Example:
                {
                    "John Doe": {
                        "wages": {"amount": 2000, "period": "month"},
                        "child support": {"amount": 300, "period": "month"},
                    },
                    "Jane Doe": {
                        "social security": {"amount": 1200, "period": "year"},
                    }
                }
    """
    try:
        income_model = HouseholdIncome.model_validate(income)
    except ValidationError as e:
        result = IncomeResult(
            status=status_helper(False),
            error=f"""There was an error validating the `income`: {e}. Expected `income` format is this pydantic model: {HouseholdIncome.model_dump_json()}""",
        )
        return result, None

    is_eligible, monthly_income = await remote_system.check_income(income=income_model)

    logger.debug(f"""Income results: eligible: {is_eligible}, monthly income: {monthly_income}""")

    flow_manager.state["income eligible"] = is_eligible
    flow_manager.state["income monthly"] = monthly_income
    flow_manager.state["income data"] = income

    status = status_helper(is_eligible)
    result = IncomeResult(
        status=status,
        is_eligible=is_eligible,
        monthly_income=monthly_income,
    )

    if status == "success":
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_assets_receives_benefits"),
                "functions": [
                    record_assets_receives_benefits,
                    caller_ended_conversation,
                    end_conversation,
                ],
            }
        )
    else:
        result["error"] = (
            f"""Over the household income limit. Alternate providers: {await remote_system.get_alternative_providers()}"""
        )
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("confirm_income_over_limit"),
                "functions": [continue_intake, caller_ended_conversation, end_conversation],
            }
        )
    return result, next_node


async def record_assets_receives_benefits(
    flow_manager: FlowManager, receives_benefits: bool
) -> tuple[AssetsResult, NodeConfig]:
    """
    Record if the caller is receiving Medicaid, SSI, or TANF benefits.

    Args:
        receives_benefits (bool): The caller has receives government benefits.
    """

    logger.debug(f"""Government means tested: {receives_benefits}""")

    if receives_benefits:
        result = AssetsResult(
            status=status_helper(True),
            is_eligible=True,
        )

        flow_manager.state["assets eligible"] = True
        flow_manager.state["assets list"] = []
        flow_manager.state["assets value"] = 0
        flow_manager.state["assets receives benefits"] = True

        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_citizenship"),
                "functions": [record_citizenship, caller_ended_conversation, end_conversation],
            }
        )
    else:
        result = None
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_assets_list"),
                "functions": [record_assets_list, caller_ended_conversation, end_conversation],
            }
        )
    return result, next_node


async def record_assets_list(
    flow_manager: FlowManager, assets: list[dict[str, int]]
) -> tuple[AssetsResult, NodeConfig]:
    """
    Collect assets' value and determine eligibility of caller.

    Args:
        assets (Assets):
            A Pydantic RootModel where the value is a list of AssetEntry objects.
            Each AssetEntry maps a single asset name (str) to an integer net present value.
            Example:
                [
                    {"car": 5000},
                    {"savings": 2000}
                ]
    """

    try:
        assets_model = Assets.model_validate(assets)
    except ValidationError as e:
        result = IncomeResult(
            status=status_helper(False),
            error=f"""There was an error validating the `income`: {e}. Expected `income` format is this pydantic model: {Assets.model_dump_json()}""",
        )
        return result, None

    is_eligible, assets_value = await remote_system.check_assets(assets=assets_model)

    logger.debug(f"""Assets: {[asset.items() for asset in assets_model]}""")
    logger.debug(f"""Assets total value: {assets_value}""")
    logger.debug(f"""Assets value results: eligible: {is_eligible}""")

    flow_manager.state["assets eligible"] = is_eligible
    flow_manager.state["assets list"] = assets_model
    flow_manager.state["assets value"] = assets_value
    flow_manager.state["assets receives benefits"] = False

    status = status_helper(is_eligible)
    result = AssetsResult(
        status=status,
        is_eligible=is_eligible,
    )

    if status == "success":
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_citizenship"),
                "functions": [record_citizenship, caller_ended_conversation, end_conversation],
            }
        )
    else:
        result["error"] = (
            f"""Over the household assets' value limit. Alternate providers: {await remote_system.get_alternative_providers()}"""
        )
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("confirm_assets_over_limit"),
                "functions": [continue_intake, caller_ended_conversation, end_conversation],
            }
        )
    return result, next_node


async def record_citizenship(
    flow_manager: FlowManager, has_citizenship: bool
) -> tuple[None, NodeConfig]:
    """
    Record if the caller is a US citizen.

    Args:
        has_citizenship (bool): The caller's answer that they are or are not a US citizen.
    """
    logger.debug(f"""Citizenship: {has_citizenship}""")
    flow_manager.state["us citizenship"] = has_citizenship

    result = CitizenshipResult(status="success", has_citizenship=has_citizenship)
    next_node = NodeConfig(
        node_partial_reset_with_summary()
        | {
            **prompts.get("record_emergency"),
            "functions": [record_emergency, caller_ended_conversation, end_conversation],
        }
    )
    return result, next_node


async def record_emergency(
    flow_manager: FlowManager, is_emergency: bool
) -> tuple[None, NodeConfig]:
    """
    Record if the caller's case is an emergency.

    Args:
        is_emergency (bool): The caller's case is or is not an emergency.
    """
    logger.debug(f"""Emergency: {is_emergency}""")
    flow_manager.state["emergency"] = is_emergency
    result = EmergencyResult(status="success", is_emergency=is_emergency)

    if is_emergency:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("complete_intake"),
                "functions": [end_conversation],
            }
        )
    else:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("complete_intake"),
                "functions": [end_conversation],
            }
        )
    return result, next_node


######################################################################
# Functions - Utility
######################################################################


async def continue_intake(flow_manager: FlowManager, next_step: str) -> tuple[None, NodeConfig]:
    """
    Continue the intake even though the caller may be ineligible.

    Args:
        next_step (str): The next step of the intake.
    """
    # Dynamically reference the function using the next_step string
    try:
        next_function = getattr(sys.modules[__name__], next_step)
    except AttributeError:
        raise ValueError(f"""Function '{next_step}' does not exist.""")

    next_node = NodeConfig(
        node_partial_reset_with_summary()
        | {
            **prompts.get(next_step),
            "functions": [next_function],
        }
    )
    return None, next_node


async def end_conversation(flow_manager: FlowManager) -> tuple[None, NodeConfig]:
    """End the conversation."""
    return None, node_end_conversation()


def node_end_conversation() -> NodeConfig:
    """Create the final node."""
    return {
        **prompts.get("end"),
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }


async def caller_ended_conversation(flow_manager: FlowManager) -> tuple[None, NodeConfig]:
    """The caller ended the conversation."""
    return None, node_caller_ended_conversation()


def node_caller_ended_conversation() -> NodeConfig:
    """Create the final node."""
    return {
        **prompts.get("caller_ended_conversation"),
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }
