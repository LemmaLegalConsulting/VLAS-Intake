import sys

from intake_bot.models.intake_flow_result import (
    AddressResult,
    AdversePartiesResult,
    AssetsResult,
    CallerNamesResult,
    CaseTypeResult,
    CitizenshipResult,
    DateOfBirthResult,
    DomesticViolenceResult,
    HouseholdCompositionResult,
    IncomeResult,
    IntakeFlowResult,
    LanguageResult,
    PhoneNumberResult,
    ServiceAreaResult,
    SSNLast4Result,
    Status,
)
from intake_bot.models.validator import (
    Address,
    AdverseParties,
    Assets,
    CallerName,
    CallerNames,
    HouseholdIncome,
    PhoneTypeCaller,
)
from intake_bot.nodes.utils import (
    clean_pydantic_error_message,
    convert_and_log_result,
    status_helper,
)
from intake_bot.nodes.validator import IntakeValidator
from intake_bot.services.dialpad import (
    CASE_TYPE_REFERRAL,
    GENERAL_REFERRAL,
    OVER_LIMIT_REFERRAL,
    SMS,
    ReferralContent,
)
from intake_bot.utils.ev import get_ev
from intake_bot.utils.node_prompts import NodePrompts
from loguru import logger
from pipecat.frames.frames import STTUpdateSettingsFrame
from pipecat.services.azure.stt import AzureSTTService
from pipecat.transcriptions.language import Language
from pipecat_flows import (
    ContextStrategy,
    ContextStrategyConfig,
    FlowManager,
    NodeConfig,
)
from pydantic import ValidationError

# Initialize
prompts = NodePrompts()
validator = IntakeValidator()
sms_service = SMS()


######################################################################
# Nodes
######################################################################


def node_initial() -> NodeConfig:
    """
    Create initial node for welcoming the caller. Allow the conversation to be ended.
    """
    initial_prompt = get_ev("TEST_INITIAL_PROMPT", default="initial")
    initial_function_name = get_ev(
        "TEST_INITIAL_FUNCTION", default="system_phone_number"
    )
    try:
        initial_function = getattr(
            sys.modules[__name__],
            initial_function_name,
        )
    except AttributeError:
        raise ValueError(
            f"""Function '{initial_function_name}' does not exist."""
        ) from None

    return {
        **prompts.get("primary_role_message"),
        **prompts.get(initial_prompt),
        "functions": [initial_function],
    }


def node_partial_reset_with_summary() -> NodeConfig:
    return {
        **prompts.get("primary_role_message"),
        "context_strategy": ContextStrategyConfig(
            strategy=ContextStrategy.RESET_WITH_SUMMARY,
            summary_prompt=prompts.get("reset_with_summary"),
        ),
    }


def _caller_language(flow_manager: FlowManager) -> str:
    language = flow_manager.state.get("language", {}).get("language", "English")
    return language.strip().lower()


def _caller_phone_number(flow_manager: FlowManager) -> str | None:
    phone_state = flow_manager.state.get("phone")
    if isinstance(phone_state, dict):
        phone_number = phone_state.get("phone_number")
        return phone_number.strip() if isinstance(phone_number, str) else None
    if isinstance(phone_state, str):
        normalized = phone_state.strip()
        return normalized or None
    return None


def _normalize_referral_delivery_method(delivery_method: str) -> str | None:
    normalized = delivery_method.strip().lower()
    if normalized in {"phone", "by phone", "over the phone", "voice", "call"}:
        return "phone"
    if normalized in {"text", "sms", "text message", "message", "by text"}:
        return "text"
    return None


def _node_referral_and_end(
    flow_manager: FlowManager, content: ReferralContent, delivery_method: str
) -> NodeConfig:
    language = _caller_language(flow_manager)
    spoken_text = (
        content.spoken_text(language)
        if delivery_method == "phone"
        else content.text_delivery_text(language)
    )
    return {
        "pre_actions": [{"type": "tts_say", "text": spoken_text}],
        "functions": [],
        "post_actions": [{"type": "end_conversation"}],
    }


async def _send_referral_sms(
    flow_manager: FlowManager, content: ReferralContent
) -> None:
    phone_number = _caller_phone_number(flow_manager)
    if not phone_number:
        logger.info(
            "Skipping referral SMS because no caller phone number is available."
        )
        return

    if not sms_service.is_configured:
        logger.warning("Skipping referral SMS because Dialpad SMS is not configured.")
        return

    message_text = content.sms_text(_caller_language(flow_manager))
    try:
        response = await sms_service.send(phone_number, message_text)
    except Exception as exc:
        logger.warning(f"Failed to send referral SMS to {phone_number}: {exc}")
        return

    sms_log = flow_manager.state.setdefault("sms_messages", [])
    sms_log.append(
        {
            "to": phone_number,
            "text": message_text,
            "status": response.get("status"),
            "category": "referral",
        }
    )
    logger.info(f"Sent referral SMS to {phone_number}")


######################################################################
# Functions - Main Flow
######################################################################


async def system_phone_number(
    flow_manager: FlowManager,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    This function checks if the phone system recieved the caller's phone number;
    if so, confirms the number with the caller; if not, collects the caller's phone number.
    """
    caller_id_phone_number = flow_manager.state.get("phone")
    logger.debug(f"""Caller ID phone number: {caller_id_phone_number}""")
    is_valid, validated_caller_id_phone_number = await validator.check_phone_number(
        phone_number=caller_id_phone_number
    )
    logger.debug(
        f"""Caller ID phone number (validated): {validated_caller_id_phone_number}"""
    )

    status = status_helper(is_valid)
    result = dict(status=status.value, phone_number=validated_caller_id_phone_number)
    next_node = NodeConfig(
        {
            **prompts.get("record_language"),
            "functions": [record_language],
        }
    )
    return result, next_node


@convert_and_log_result("language")
async def record_language(
    flow_manager: FlowManager, language: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record the caller's preferred language.

    Args:
        language (str): The caller's preferred language (English or Spanish).
    """
    normalized_language = language.strip().lower()
    stt_language = (
        Language.ES_US if normalized_language == "spanish" else Language.EN_US
    )
    await flow_manager.task.queue_frame(
        STTUpdateSettingsFrame(delta=AzureSTTService.Settings(language=stt_language))
    )

    result = LanguageResult(status=Status.SUCCESS, language=language)
    next_node = NodeConfig(
        {
            **prompts.get(
                "record_phone_number",
                phone_number=flow_manager.state.get("phone"),
            ),
            "functions": [record_phone_number],
        }
    )
    return result, next_node


@convert_and_log_result("phone")
async def record_phone_number(
    flow_manager: FlowManager, phone_number: str, phone_type: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Collect the caller's US phone number and type.

    Args:
        phone_number (str): The caller's 10 digit US phone number.
        phone_type (str): The type of phone (mobile, home, work, or other).
    """
    is_valid, validated_phone_number = await validator.check_phone_number(
        phone_number=phone_number
    )

    status = status_helper(is_valid)

    try:
        validated_phone_type = PhoneTypeCaller(phone_type.lower())
    except ValueError:
        status = Status.ERROR
        validated_phone_type = None

    result = PhoneNumberResult(
        status=status,
        is_valid=is_valid,
        phone_number=validated_phone_number,
        phone_type=validated_phone_type,
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
        if not is_valid:
            result.error = "Not a valid US phone number"
        else:
            result.error = (
                "Invalid phone type. Please choose from: mobile, home, work, or other"
            )
        next_node = None
    return result, next_node


@convert_and_log_result("names")
async def record_name(
    flow_manager: FlowManager, first: str, middle: str, last: str, suffix: str = ""
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record the caller's primary name and set it as the main contact name.

    Args:
        first (str): The caller's first name.
        middle (str): The caller's middle name.
        last (str): The caller's last name.
        suffix (str): The caller's name suffix (e.g., Jr., Sr., III).
    """
    try:
        name_validated = CallerName.model_validate(
            {
                "first": first,
                "middle": middle,
                "last": last,
                "suffix": suffix,
                "type": "Legal Name",  # Primary/official name
            }
        )
    except ValidationError as e:
        logger.debug(e)
        cleaned_error = clean_pydantic_error_message(e)
        result = IntakeFlowResult(
            status=Status.ERROR,
            error=f"""There was an error validating the `name`: {cleaned_error}.""",
        )
        return result, None

    result = CallerNamesResult(status=Status.SUCCESS, names=[name_validated])
    next_node = NodeConfig(
        node_partial_reset_with_summary()
        | {
            **prompts.get("record_service_area"),
            "functions": [record_service_area],
        }
    )
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
    match, fips_code = await validator.check_service_area(location=location)
    is_eligible = match == location

    status = status_helper(is_eligible)
    result = ServiceAreaResult(
        status=status, is_eligible=is_eligible, location=location, fips_code=fips_code
    )

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
            result.error = f"""No exact match found. Maybe you meant {match}?"""
            next_node = None
        else:
            result.error = "Not in our service area."
            next_node = NodeConfig(
                node_partial_reset_with_summary()
                | {
                    **prompts.get("ineligible"),
                    "functions": [send_general_referral_and_end],
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

    # Check if we need to ask follow-up questions
    if case_response.follow_up_questions:
        follow_up_questions = [
            f"""Question: {item.question}"""
            + (f"""Options: {item.options}""" if item.options else "")
            for item in case_response.follow_up_questions
        ]
        error_text = f"""Use these questions to gather additional information and
        then resubmit the case description with the additional questions and answers. {follow_up_questions}"""
        result = IntakeFlowResult(
            status=Status.ERROR,
            error=error_text,
        )
        return result, None

    status = status_helper(case_response.is_eligible)
    result = CaseTypeResult(
        status=status,
        is_eligible=case_response.is_eligible,
        legal_problem_code=case_response.legal_problem_code,
        case_description=case_description,
    )
    if status == Status.SUCCESS:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_adverse_parties"),
                "functions": [record_adverse_parties],
            }
        )
    else:
        result.error = "Ineligible case type."
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("case_type_ineligible"),
                "functions": [send_case_type_referral_and_end],
            }
        )
    return result, next_node


@convert_and_log_result("adverse_parties")
async def record_adverse_parties(
    flow_manager: FlowManager, adverse_parties: list[dict]
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Collect information about the adverse (opposing) parties.

    Args:
        adverse_parties (list):
            A Pydantic model `AdverseParties` with a list of people
            who may be involved as adverse (opposing) parties in the
            legal case. Each person should include their first,
            middle, and last name, date of birth, and a list of phone
            numbers with types.

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
        adverse_parties_validated = AdverseParties.model_validate(adverse_parties)
    except ValidationError as e:
        logger.debug(e)
        cleaned_error = clean_pydantic_error_message(e)
        result = IntakeFlowResult(
            status=Status.ERROR,
            error=f"""There was an error validating the `adverse_parties`: {cleaned_error}.""",
        )
        return result, None

    result = AdversePartiesResult(
        status=Status.SUCCESS,
        adverse_parties=adverse_parties_validated,
    )
    next_node = NodeConfig(
        node_partial_reset_with_summary()
        | {
            **prompts.get("record_domestic_violence"),
            "functions": [record_domestic_violence],
        }
    )
    return result, next_node


@convert_and_log_result("domestic_violence")
async def record_domestic_violence(
    flow_manager: FlowManager, is_experiencing: bool
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record whether the caller is experiencing or has experienced domestic violence.

    Args:
        is_experiencing (bool): Whether the caller is experiencing or has experienced domestic violence.
    """
    result = DomesticViolenceResult(
        status=Status.SUCCESS,
        is_experiencing=is_experiencing,
    )

    next_node = NodeConfig(
        node_partial_reset_with_summary()
        | {
            **prompts.get("record_household_composition"),
            "functions": [record_household_composition],
        }
    )
    return result, next_node


@convert_and_log_result("household_composition")
async def record_household_composition(
    flow_manager: FlowManager, number_of_adults: int, number_of_children: int
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record the number of people in the household, excluding anyone who has perpetrated domestic violence against the caller.

    Args:
        number_of_adults (int): Number of adults in the household (18 and older), including yourself, excluding anyone who has perpetrated domestic violence against you.
        number_of_children (int): Number of children in the household (under 18).
    """
    is_valid, _ = await validator.check_household_composition(
        adults=number_of_adults, children=number_of_children
    )

    if not is_valid:
        result = IntakeFlowResult(
            status=Status.ERROR,
            error="Please provide valid numbers for the number of adults in your household, including yourself (at least 1), and children (0 or more).",
        )
        return result, None

    result = HouseholdCompositionResult(
        status=Status.SUCCESS,
        number_of_adults=number_of_adults,
        number_of_children=number_of_children,
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
            Note: Only include household members who have income. Children with no income do not need to be listed.
    """
    try:
        income_validated = HouseholdIncome.model_validate(income)
        household_composition = flow_manager.state.get("household_composition") or {}
        adults = household_composition.get("number_of_adults", 0)
        children = household_composition.get("number_of_children", 0)
        household_size = adults + children
        is_eligible, income_monthly, household_size = await validator.check_income(
            income=income_validated, household_size=household_size
        )
    except ValidationError as e:
        logger.debug(e)
        cleaned_error = clean_pydantic_error_message(e)
        result = IntakeFlowResult(
            status=Status.ERROR,
            error=f"""There was an error validating the `income`: {cleaned_error}.""",
        )
        return result, None

    status = status_helper(is_eligible)
    result = IncomeResult(
        status=status,
        is_eligible=is_eligible,
        monthly_amount=income_monthly,
        listing=income_validated,
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
        result.error = """Over the household income limit"""
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("confirm_income_over_limit"),
                "functions": [continue_intake, send_over_limit_referral_and_end],
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
            status=Status.SUCCESS,
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
        is_eligible, assets_value = await validator.check_assets(
            assets=assets_validated
        )
    except ValidationError as e:
        logger.debug(e)
        cleaned_error = clean_pydantic_error_message(e)
        result = IntakeFlowResult(
            status=Status.ERROR,
            error=f"""There was an error validating the `assets`: {cleaned_error}.""",
        )
        return result, None

    status = status_helper(is_eligible)
    result = AssetsResult(
        status=status,
        is_eligible=is_eligible,
        listing=assets_validated,
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
        result.error = "Over the household assets' value limit."
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("confirm_assets_over_limit"),
                "functions": [continue_intake, send_over_limit_referral_and_end],
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
            **prompts.get("record_ssn_last_4"),
            "functions": [record_ssn_last_4],
        }
    )
    return result, next_node


@convert_and_log_result("ssn_last_4")
async def record_ssn_last_4(
    flow_manager: FlowManager, ssn_last_4: str = ""
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Collect the last 4 digits of the caller's social security number.

    Args:
        ssn_last_4 (str): The last 4 digits of the caller's SSN (accepts various formats like XXXX, XXX-X, etc.)
                          Can be empty if the caller refuses or does not know.
    """
    if not ssn_last_4:
        status = Status.SUCCESS
        formatted_ssn = ""
    else:
        is_valid, formatted_ssn = await validator.check_ssn_last_4(
            ssn_last_4=ssn_last_4
        )
        status = status_helper(is_valid)

    result = SSNLast4Result(
        status=status,
        ssn_last_4=formatted_ssn,
    )

    if status == Status.SUCCESS:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_date_of_birth"),
                "functions": [record_date_of_birth],
            }
        )
    else:
        result.error = (
            "Invalid SSN. Please provide the last 4 digits in format: XXXX or XXX-X."
        )
        next_node = None
    return result, next_node


@convert_and_log_result("date_of_birth")
async def record_date_of_birth(
    flow_manager: FlowManager, date_of_birth: str = ""
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Collect the caller's date of birth.

    Args:
        date_of_birth (str): The caller's date of birth in ISO format (YYYY-MM-DD).
                             Can be empty if the caller refuses or does not know.
    """
    if not date_of_birth:
        status = Status.SUCCESS
        formatted_dob = ""
    else:
        is_valid, formatted_dob = await validator.check_date_of_birth(
            dob_string=date_of_birth
        )
        status = status_helper(is_valid)

    result = DateOfBirthResult(
        status=status,
        date_of_birth=formatted_dob,
    )

    if status == Status.SUCCESS:
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("record_names"),
                "functions": [record_names],
            }
        )
    else:
        result.error = "Invalid date of birth. Please provide a date in the format MM/DD/YYYY or similar."
        next_node = None
    return result, next_node


@convert_and_log_result("names")
async def record_names(
    flow_manager: FlowManager, names: list[dict]
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record the caller's additional names (maiden name, previous marriage names, legally changed names, etc.).

    This function combines the previously recorded primary name with any additional names
    the caller provides, creating a complete list of all names associated with the caller.

    Args:
        names (list[dict]):
            REQUIRED: A list of additional name objects. Each object contains:
            - "first" (str, required): The first name
            - "middle" (str, optional): The middle name
            - "last" (str, required): The last name
            - "type_id" (int, optional): The alias type ID (333=Former Name, 334=Maiden Name, 817=Nickname, 3315536=Legal Name)
              Defaults to 333 (Former Name) if not specified.

            IMPORTANT:
            1. The "names" argument is REQUIRED - always pass it, never omit it
            2. Use EXACT field names: "first", "middle", "last", "type_id"
            3. Pass an empty list [] if the caller has no additional names
            4. If type_id is not specified, it defaults to 333 (Former Name)

            Example 1 - One additional name with type:
                names=[{"first": "Sarah", "middle": "Jane", "last": "Smith", "type_id": 334}]

            Example 2 - No additional names (empty list):
                names=[]

            Example 3 - Two additional names with different types:
                names=[
                    {"first": "Mary", "last": "Johnson", "type_id": 334},
                    {"first": "Robert", "middle": "Lee", "last": "Davis", "type_id": 333}
                ]
    """
    try:
        existing_names = []
        if "names" in flow_manager.state and "names" in flow_manager.state["names"]:
            existing_names = flow_manager.state["names"]["names"]
        all_names = existing_names + names
        names_validated = CallerNames.model_validate(all_names)
    except ValidationError as e:
        logger.debug(e)
        cleaned_error = clean_pydantic_error_message(e)
        result = IntakeFlowResult(
            status=Status.ERROR,
            error=f"""There was an error validating the `names`: {cleaned_error}.""",
        )
        return result, None

    result = CallerNamesResult(status=Status.SUCCESS, names=names_validated)
    next_node = NodeConfig(
        node_partial_reset_with_summary()
        | {
            **prompts.get("record_address"),
            "functions": [record_address],
        }
    )
    return result, next_node


@convert_and_log_result("address")
async def record_address(
    flow_manager: FlowManager,
    street: str = "",
    street_2: str = None,
    city: str = "",
    state: str = "",
    zip: str = "",
    county: str = "",
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record the caller's residential address.

    Args:
        street (str): The primary street address (required).
        street_2 (str): The apartment, suite, or unit number (optional).
        city (str): The city (required).
        state (str): The state abbreviation, e.g., "VA" (required).
        zip (str): The 5-digit ZIP code (required).
        county (str): The county of residence (required).

        Note: All fields can be empty if the caller refuses or does not have an address.
    """
    # Check if all required fields are empty
    if not any([street, city, state, zip, county]):
        result = AddressResult(status=Status.SUCCESS, address=None)
        next_node = NodeConfig(
            node_partial_reset_with_summary()
            | {
                **prompts.get("complete_intake"),
                "post_actions": [{"type": "end_conversation"}],
            }
        )
        return result, next_node

    try:
        address_validated = Address.model_validate(
            {
                "street": street,
                "street_2": street_2,
                "city": city,
                "state": state,
                "zip": zip,
                "county": county,
            }
        )
    except ValidationError as e:
        logger.debug(e)
        cleaned_error = clean_pydantic_error_message(e)
        result = IntakeFlowResult(
            status=Status.ERROR,
            error=f"""There was an error validating the `address`: {cleaned_error}.""",
        )
        return result, None

    result = AddressResult(status=Status.SUCCESS, address=address_validated)
    next_node = NodeConfig(
        node_partial_reset_with_summary()
        | {
            **prompts.get("complete_intake"),
            "post_actions": [{"type": "end_conversation"}],
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


async def send_general_referral_and_end(
    flow_manager: FlowManager,
    delivery_method: str,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    normalized_method = _normalize_referral_delivery_method(delivery_method)
    if normalized_method is None:
        return (
            IntakeFlowResult(
                status=Status.ERROR,
                error="delivery_method must be either 'phone' or 'text'.",
            ),
            None,
        )
    if normalized_method == "text":
        await _send_referral_sms(flow_manager, GENERAL_REFERRAL)
    return None, _node_referral_and_end(
        flow_manager, GENERAL_REFERRAL, normalized_method
    )


async def send_case_type_referral_and_end(
    flow_manager: FlowManager,
    delivery_method: str,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    normalized_method = _normalize_referral_delivery_method(delivery_method)
    if normalized_method is None:
        return (
            IntakeFlowResult(
                status=Status.ERROR,
                error="delivery_method must be either 'phone' or 'text'.",
            ),
            None,
        )
    if normalized_method == "text":
        await _send_referral_sms(flow_manager, CASE_TYPE_REFERRAL)
    return None, _node_referral_and_end(
        flow_manager, CASE_TYPE_REFERRAL, normalized_method
    )


async def send_over_limit_referral_and_end(
    flow_manager: FlowManager,
    delivery_method: str,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    normalized_method = _normalize_referral_delivery_method(delivery_method)
    if normalized_method is None:
        return (
            IntakeFlowResult(
                status=Status.ERROR,
                error="delivery_method must be either 'phone' or 'text'.",
            ),
            None,
        )
    if normalized_method == "text":
        await _send_referral_sms(flow_manager, OVER_LIMIT_REFERRAL)
    return None, _node_referral_and_end(
        flow_manager, OVER_LIMIT_REFERRAL, normalized_method
    )


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
