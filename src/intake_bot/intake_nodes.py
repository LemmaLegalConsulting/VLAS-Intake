import sys

from loguru import logger
from pipecat_flows import (
    ContextStrategy,
    ContextStrategyConfig,
    FlowManager,
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
    IntakeFlowResult,
    NameResult,
    PhoneNumberResult,
    ServiceAreaResult,
    Status,
    status_helper,
)
from intake_bot.intake_utils import convert_and_log_result
from intake_bot.intake_validator import IntakeValidator
from intake_bot.prompts import Prompts

# Initialize Prompts
prompts = Prompts()


# Initialize IntakeValidator
validator = IntakeValidator()


######################################################################
# Nodes
######################################################################


def node_initial() -> NodeConfig:
    """
    Create initial node for welcoming the caller. Allow the conversation to be ended.
    """
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
        "functions": [
            initial_function,
            end_conversation,
            caller_ended_conversation,
        ],
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


async def system_phone_number(
    flow_manager: FlowManager,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    This function checks if the phone system recieved the caller's phone number; if so, confirms the number with the caller; if not, collects the caller's phone number.
    """
    caller_id_phone_number = flow_manager.state.get("phone")
    logger.debug(f"""Caller ID phone number: {caller_id_phone_number}""")

    status = status_helper(caller_id_phone_number)
    result = dict(status=status.value, phone_number=caller_id_phone_number)
    next_node = NodeConfig(
        {
            **prompts.get("record_phone_number"),
            "functions": [record_phone_number],
        }
    )
    return result, next_node


@convert_and_log_result("phone")
async def record_phone_number(
    flow_manager: FlowManager, phone_number: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Collect the caller's US phone number.

    Args:
        phone_number (str): The caller's 10 digit US phone number.
    """
    is_valid, validated_phone_number = await validator.check_phone_number(phone_number=phone_number)

    status = status_helper(is_valid)
    result = PhoneNumberResult(
        status=status, is_valid=is_valid, phone_number=validated_phone_number
    )

    if status == Status.SUCCESS:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_name"),
                "functions": [record_name],
            }
        )
    else:
        result.error = "Not a valid US phone number"
        next_node = None
    return result, next_node


@convert_and_log_result("name")
async def record_name(
    flow_manager: FlowManager, first: str, middle: str, last: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
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

    status = status_helper(first and last)
    result = NameResult(status=status, first=first, middle=middle, last=last)

    if status == Status.SUCCESS:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_service_area"),
                "functions": [record_service_area],
            }
        )
    else:
        result.error = "Required: first name and last name"
        next_node = None
    return result, next_node


@convert_and_log_result("service_area")
async def record_service_area(
    flow_manager: FlowManager, location: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record the service area location.

    Args:
        location (str): The location of the caller's home or the legal incident. Must be a city or county.
    """
    match = await validator.check_service_area(location=location)
    is_eligible = match == location

    status = status_helper(is_eligible)
    result = ServiceAreaResult(status=status, is_eligible=is_eligible, location=location)

    if status == Status.SUCCESS:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_case_type"),
                "functions": [record_case_type],
            }
        )
    else:
        if match:
            result.error = f"No exact match found. Maybe you meant {match}?"
            next_node = None
        else:
            result.error = f"""Not in our service area. Alternate providers: {await validator.get_alternative_providers()}"""
            next_node = NodeConfig(
                node_partial_reset_with_summary()
                | {
                    **prompts.get("ineligible"),
                    "functions": [end_conversation],
                }
            )
    return result, next_node


@convert_and_log_result("case_type")
async def record_case_type(
    flow_manager: FlowManager, case_type: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Check eligibility of caller's type of case.

    Args:
        case_type (str): The type of legal case that the caller has.
    """
    is_eligible = await validator.check_case_type(case_type=case_type)

    status = status_helper(is_eligible)
    result = CaseTypeResult(status=status, is_eligible=is_eligible, case_type=case_type)
    if status == Status.SUCCESS:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("conflict_check"),
                "functions": [conflict_check],
            }
        )
    else:
        result.error = f"""Ineligible case type. Alternate providers: {await validator.get_alternative_providers()}"""
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("ineligible"),
                "functions": [end_conversation],
            }
        )
    return result, next_node


@convert_and_log_result("conflict_check")
async def conflict_check(
    flow_manager: FlowManager, opposing_party_members: list[str]
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
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

    there_is_a_conflict = await validator.check_conflict_of_interest(
        opposing_party_members=opposing_party_members
    )

    status = status_helper(not there_is_a_conflict)
    result = ConflictCheckResult(
        status=status,
        there_is_a_conflict=there_is_a_conflict,
        opposing_party_members=opposing_party_members,
    )
    if status == Status.SUCCESS:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_domestic_violence"),
                "functions": [record_domestic_violence],
            }
        )
    else:
        result.error = f"""There is a representation conflict. Alternate providers: {await validator.get_alternative_providers()}"""
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("ineligible"),
                "functions": [end_conversation],
            }
        )
    return result, next_node


@convert_and_log_result("domestic_violence")
async def record_domestic_violence(
    flow_manager: FlowManager, perpetrators: list[str]
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record the names of perpetrators of domestic violence if the caller is xperiencing domestic violence.

    Args:
        perpetrators_of_domestic_violence (list): The individuals perpetrating domestic violence on the caller.
    """
    is_experiencing = bool(perpetrators)
    result = DomesticViolenceResult(
        status=Status.SUCCESS,
        is_experiencing=is_experiencing,
        perpetrators=perpetrators,
    )
    next_node = NodeConfig(
        node_partial_reset_with_summary()
        | {
            **prompts.get("record_income"),
            "functions": [record_income],
        }
    )
    return result, next_node


@convert_and_log_result("income")
async def record_income(
    flow_manager: FlowManager, income: HouseholdIncome
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
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
        income_validated = HouseholdIncome.model_validate(income)
        is_eligible, income_monthly = await validator.check_income(income=income_validated)
    except ValidationError as e:
        result = IntakeFlowResult(
            status=status_helper(False),
            error=f"""There was an error validating the `income`: {e}. Expected `income` format is this pydantic model: {HouseholdIncome.model_json_schema()}""",
        )
        return result, None

    status = status_helper(is_eligible)
    result = IncomeResult(
        status=status,
        is_eligible=is_eligible,
        monthly_amount=income_monthly,
        listing=income_validated.model_dump(),
    )
    if status == Status.SUCCESS:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_assets_receives_benefits"),
                "functions": [record_assets_receives_benefits],
            }
        )
    else:
        result.error = f"""Over the household income limit. Alternate providers: {await validator.get_alternative_providers()}"""
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("confirm_income_over_limit"),
                "functions": [continue_intake, caller_ended_conversation, end_conversation],
            }
        )
    return result, next_node


@convert_and_log_result("assets")
async def record_assets_receives_benefits(
    flow_manager: FlowManager, receives_benefits: bool
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record if the caller is receiving Medicaid, SSI, or TANF benefits.

    Args:
        receives_benefits (bool): The caller has receives government benefits.
    """
    if receives_benefits:
        result = AssetsResult(
            status=status_helper(True),
            is_eligible=True,
            listing=[],
            total_value=0,
            receives_benefits=True,
        )
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_citizenship"),
                "functions": [record_citizenship],
            }
        )
    else:
        result = None
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_assets_list"),
                "functions": [record_assets_list],
            }
        )
    return result, next_node


@convert_and_log_result("assets")
async def record_assets_list(
    flow_manager: FlowManager, assets: list[dict[str, int]]
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
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
        assets_validated = Assets.model_validate(assets)
        is_eligible, assets_value = await validator.check_assets(assets=assets_validated)
    except ValidationError as e:
        result = IntakeFlowResult(
            status=status_helper(False),
            error=f"""There was an error validating the `income`: {e}. Expected `income` format is this pydantic model: {Assets.model_json_schema()}""",
        )
        return result, None

    status = status_helper(is_eligible)
    result = AssetsResult(
        status=status,
        is_eligible=is_eligible,
        listing=assets_validated.model_dump(),
        total_value=assets_value,
        receives_benefits=False,
    )
    if status == Status.SUCCESS:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_citizenship"),
                "functions": [record_citizenship],
            }
        )
    else:
        result.error = f"""Over the household assets' value limit. Alternate providers: {await validator.get_alternative_providers()}"""
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("confirm_assets_over_limit"),
                "functions": [continue_intake, caller_ended_conversation, end_conversation],
            }
        )
    return result, next_node


@convert_and_log_result("citizenship")
async def record_citizenship(
    flow_manager: FlowManager, is_a_us_citizen: bool
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record if the caller is a US citizen.

    Args:
        has_citizenship (bool): The caller's answer that they are or are not a US citizen.
    """
    result = CitizenshipResult(status=Status.SUCCESS, is_citizen=is_a_us_citizen)
    next_node = NodeConfig(
        node_partial_reset_with_summary()
        | {
            **prompts.get("record_emergency"),
            "functions": [record_emergency],
        }
    )
    return result, next_node


@convert_and_log_result("emergency")
async def record_emergency(
    flow_manager: FlowManager, is_emergency: bool
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record if the caller's case is an emergency.

    Args:
        is_emergency (bool): The caller's case is or is not an emergency.
    """
    result = EmergencyResult(status=Status.SUCCESS, is_emergency=is_emergency)
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
# Utility Nodes
######################################################################


async def continue_intake(
    flow_manager: FlowManager, next_step: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
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


async def end_conversation(
    flow_manager: FlowManager,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    End the conversation.
    """
    return None, node_end_conversation()


def node_end_conversation() -> NodeConfig:
    """
    Create the final node.
    """
    return {
        **prompts.get("end"),
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }


async def caller_ended_conversation(
    flow_manager: FlowManager,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    The caller ended the conversation.
    """
    return None, node_caller_ended_conversation()


def node_caller_ended_conversation() -> NodeConfig:
    """
    Create the final node.
    """
    return {
        **prompts.get("caller_ended_conversation"),
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }
