import sys

from intake_bot.models.results import (
    AssetsResult,
    CaseTypeResult,
    CitizenshipResult,
    ConflictResult,
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
from intake_bot.models.validators import Assets, HouseholdIncome, PotentialConflicts
from intake_bot.nodes.utils import clean_pydantic_error_message, convert_and_log_result
from intake_bot.utils.ev import get_ev
from intake_bot.utils.prompts import Prompts
from intake_bot.validator.validator import IntakeValidator
from loguru import logger
from pipecat_flows import (
    ContextStrategy,
    ContextStrategyConfig,
    FlowManager,
    NodeConfig,
)
from pydantic import ValidationError

# Initialize
prompts = Prompts()
validator = IntakeValidator()


######################################################################
# Nodes
######################################################################


def node_initial() -> NodeConfig:
    """
    Create initial node for welcoming the caller. Allow the conversation to be ended.
    """
    initial_prompt = get_ev("TEST_INITIAL_PROMPT", default="initial")
    try:
        initial_function = getattr(
            sys.modules[__name__],
            get_ev("TEST_INITIAL_FUNCTION", default="system_phone_number"),
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
    flow_manager: FlowManager, case_description: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Check eligibility of caller's legal case.

    Args:
        case_description (str): The description of the legal case that the caller has.
    """
    case_response = await validator.check_case_type(case_description=case_description)
    logger.debug(f"""case_response: {case_response}""")
    best_match = case_response["labels"][0]
    logger.debug(f"""best_match: {best_match}""")
    if float(best_match["confidence"]) < 2.5 and "follow_up_questions" in case_response:
        follow_up_questions = [
            f"""Question: {item["question"]}"""
            + (f"""Options: {item["options"]}""" if "options" in item else "")
            for item in case_response["follow_up_questions"]
        ]
        error_text = f"""Use these questions to gather additional information and then resubmit the case description with the additional questions and answers. {follow_up_questions}"""
        result = CaseTypeResult(status=status_helper(False), **best_match, error=error_text)
        return result, None

    is_eligible = bool(best_match["legal_problem_code"])
    status = status_helper(is_eligible)
    result = CaseTypeResult(status=status, is_eligible=is_eligible, **best_match)
    if status == Status.SUCCESS:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_conflict"),
                "functions": [record_conflict],
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


@convert_and_log_result("record_conflict")
async def record_conflict(
    flow_manager: FlowManager, opposing_parties: list[dict]
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Collect information about the opposing parties.

    Args:
        opposing_parties (list):
            A Pydantic model `PotentialConflicts` with a list of people who may be involved as opposing parties in the legal case. Each person should include their first, middle, and last name, date of birth, visa number (if applicable), and a list of phone numbers with types.

            Example:
                [
                    {
                        "first": "Deanna",
                        "middle": "Julie",
                        "last": "Troi",
                        "dob": "1974-12-25",
                        "phones": [
                            {
                                "number": "5555551212",
                                "type": "mobile"
                            },
                        ],
                    },
                ]
    """
    try:
        opposing_parties_validated = PotentialConflicts.model_validate(opposing_parties)
        responses = await validator.check_conflict(potential_conflicts=opposing_parties_validated)
    except ValidationError as e:
        logger.debug(e)
        cleaned_error = clean_pydantic_error_message(e)
        result = IntakeFlowResult(
            status=status_helper(False),
            error=f"""There was an error validating the `opposing_parties`: {cleaned_error}.""",
        )
        return result, None

    has_highest_conflict = responses.counts["highest"] > 0
    status = status_helper(not has_highest_conflict)
    result = ConflictResult(
        status=status,
        has_highest_conflict=has_highest_conflict,
        responses=responses,
        opposing_parties=opposing_parties_validated,
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
    flow_manager: FlowManager, income: dict[dict]
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
        household_size = len(income_validated.root.keys())
        is_eligible, income_monthly = await validator.check_income(income=income_validated)
    except ValidationError as e:
        logger.debug(e)
        cleaned_error = clean_pydantic_error_message(e)
        result = IntakeFlowResult(
            status=status_helper(False),
            error=f"""There was an error validating the `income`: {cleaned_error}.""",
        )
        return result, None

    status = status_helper(is_eligible)
    result = IncomeResult(
        status=status,
        is_eligible=is_eligible,
        monthly_amount=income_monthly,
        listing=income_validated.model_dump(),
        household_size=household_size,
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
    flow_manager: FlowManager, assets: list[dict]
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
        logger.debug(e)
        cleaned_error = clean_pydantic_error_message(e)
        result = IntakeFlowResult(
            status=status_helper(False),
            error=f"""There was an error validating the `assets`: {cleaned_error}.""",
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
