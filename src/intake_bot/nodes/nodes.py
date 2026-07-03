import sys
import unicodedata
from datetime import datetime, timezone

from intake_bot.models.intake_flow_result import (
    AddressResult,
    AdversePartiesResult,
    AssetCategoryResult,
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
    AdverseParty,
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
    REFERRAL,
    SMS,
    ReferralContent,
)
from intake_bot.utils.ev import get_deepgram_tts_voices, get_ev
from intake_bot.utils.node_prompts import NodePrompts
from loguru import logger
from pipecat.frames.frames import (
    STTUpdateSettingsFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
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
_ADVERSE_PARTIES_FOLLOW_UP_KEY = "_adverse_parties_follow_up_requested"

_ACKNOWLEDGMENT_CONFIRMATION = "confirmation"
_ACKNOWLEDGMENT_INFORMATION = "information"


######################################################################
# Nodes
######################################################################


def node_initial() -> NodeConfig:
    """
    Create initial node for welcoming the caller. Allow the conversation to be ended.
    """
    initial_prompt = get_ev("TEST_INITIAL_PROMPT", default="initial")
    initial_prompt_kwargs = {}
    if initial_prompt == "initial":
        initial_prompt_kwargs["initial_greeting"] = prompts.get_spoken_prompt(
            "initial_greeting"
        )

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
        **prompts.get(initial_prompt, **initial_prompt_kwargs),
        "functions": [initial_function],
    }


def node_start() -> NodeConfig:
    initial_prompt = get_ev("TEST_INITIAL_PROMPT", default="initial")
    initial_function = get_ev("TEST_INITIAL_FUNCTION", default="system_phone_number")

    if initial_prompt == "initial" and initial_function == "system_phone_number":
        return node_record_language(include_initial_greeting=True)

    return node_initial()


def node_partial_reset_with_state() -> NodeConfig:
    return {
        **prompts.get("primary_role_message"),
        "context_strategy": ContextStrategyConfig(
            strategy=ContextStrategy.RESET,
        ),
    }


def _normalize_prompt_lead(text: str) -> str:
    if text.startswith("I "):
        return text

    for index, char in enumerate(text):
        if char.isalpha():
            return text[:index] + char.lower() + text[index + 1 :]
    return text


def _compose_spoken_prompt(
    flow_manager: FlowManager,
    question: str,
    acknowledgment_category: str | None = None,
) -> str:
    if not acknowledgment_category:
        return question

    acknowledgment = prompts.get_acknowledgment_phrase(
        acknowledgment_category,
        _caller_language(flow_manager),
    )
    if not acknowledgment:
        return question

    return f"{acknowledgment}, {_normalize_prompt_lead(question)}"


def _spoken_prompt_text(
    flow_manager: FlowManager,
    prompt_key: str,
    acknowledgment_category: str | None = None,
    **kwargs,
) -> str:
    question = prompts.get_spoken_prompt(
        prompt_key,
        _caller_language(flow_manager),
        **kwargs,
    )
    return _compose_spoken_prompt(flow_manager, question, acknowledgment_category)


def _spoken_prompt_text_builder(
    prompt_key: str,
    acknowledgment_category: str | None = None,
    prompt_kwargs: dict | None = None,
):
    resolved_prompt_kwargs = prompt_kwargs or {}

    def builder(flow_manager: FlowManager) -> str:
        return _spoken_prompt_text(
            flow_manager,
            prompt_key,
            acknowledgment_category,
            **resolved_prompt_kwargs,
        )

    return builder


def _transcript_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


async def _log_spoken_text(flow_manager: FlowManager, text: str) -> None:
    if not text or not text.strip():
        return

    transcript_handler = flow_manager.__dict__.get("_transcript_handler")
    if transcript_handler is None:
        transcript_handler = flow_manager.state.get("_transcript_handler")
    if transcript_handler is None:
        return

    if hasattr(transcript_handler, "save_assistant_tts"):
        await transcript_handler.save_assistant_tts(text)
        return

    if hasattr(transcript_handler, "save_transcript_message"):
        await transcript_handler.save_transcript_message(
            "assistant",
            text,
            _transcript_timestamp(),
        )


def _build_step_node(
    prompt_key: str,
    functions: list,
    *,
    prompt_kwargs: dict | None = None,
    text_builder=None,
) -> NodeConfig:
    node = node_partial_reset_with_state() | {
        **prompts.get(prompt_key, **(prompt_kwargs or {})),
        "functions": functions,
    }

    if text_builder is not None:
        node["pre_actions"] = [
            {
                "type": "function",
                "handler": _speak_dynamic_prompt,
                "text_builder": text_builder,
            }
        ]
        node["respond_immediately"] = False

    return node


def _build_static_tts_node(
    text: str, post_actions: list[dict] | None = None
) -> NodeConfig:
    node = {
        "task_messages": [],
        "pre_actions": [{"type": "tts_say", "text": text}],
        "functions": [],
    }
    if post_actions is not None:
        node["post_actions"] = post_actions
    return node


def _phone_digits(value: str | None) -> str:
    if not value:
        return ""

    digits = "".join(char for char in value if char.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def _spoken_phone_number(value: str | None, language: str) -> str | None:
    digits = _phone_digits(value)
    if not digits:
        return None

    number_words = {
        "english": {
            "0": "zero",
            "1": "one",
            "2": "two",
            "3": "three",
            "4": "four",
            "5": "five",
            "6": "six",
            "7": "seven",
            "8": "eight",
            "9": "nine",
        },
        "spanish": {
            "0": "cero",
            "1": "uno",
            "2": "dos",
            "3": "tres",
            "4": "cuatro",
            "5": "cinco",
            "6": "seis",
            "7": "siete",
            "8": "ocho",
            "9": "nueve",
        },
    }

    language_key = "spanish" if language == "spanish" else "english"
    groups = (digits[:3], digits[3:6], digits[6:])
    spoken_groups = []
    for group in groups:
        spoken_groups.append(
            " ".join(number_words[language_key][digit] for digit in group)
        )
    return ", ".join(spoken_groups)


def _phone_number_prompt_text(flow_manager: FlowManager) -> str:
    language = _caller_language(flow_manager)
    spoken_phone_number = _spoken_phone_number(
        _caller_phone_number(flow_manager), language
    )

    if spoken_phone_number:
        question = prompts.get_spoken_prompt(
            "record_phone_number_confirmation",
            language,
            spoken_phone_number=spoken_phone_number,
        )
        return _compose_spoken_prompt(
            flow_manager,
            question,
            _ACKNOWLEDGMENT_CONFIRMATION,
        )

    return prompts.get_spoken_prompt("record_phone_number_request", language)


def _phone_type_prompt_text(flow_manager: FlowManager) -> str:
    question = prompts.get_spoken_prompt(
        "record_phone_type_question",
        _caller_language(flow_manager),
    )

    return _compose_spoken_prompt(
        flow_manager,
        question,
        _ACKNOWLEDGMENT_CONFIRMATION,
    )


def _name_prompt_text(flow_manager: FlowManager) -> str:
    question = prompts.get_spoken_prompt(
        "record_name_question",
        _caller_language(flow_manager),
    )

    return _compose_spoken_prompt(
        flow_manager,
        question,
        _ACKNOWLEDGMENT_INFORMATION,
    )


def _service_area_prompt_text(flow_manager: FlowManager) -> str:
    return _spoken_prompt_text(
        flow_manager,
        "record_service_area_question",
        _ACKNOWLEDGMENT_CONFIRMATION,
    )


def _case_type_prompt_text(flow_manager: FlowManager) -> str:
    return _spoken_prompt_text(
        flow_manager,
        "record_case_type_question",
        _ACKNOWLEDGMENT_INFORMATION,
    )


def _adverse_parties_prompt_text(flow_manager: FlowManager) -> str:
    return _spoken_prompt_text(
        flow_manager,
        "record_adverse_parties_question",
        _ACKNOWLEDGMENT_INFORMATION,
    )


def _domestic_violence_prompt_text(flow_manager: FlowManager) -> str:
    return _spoken_prompt_text(
        flow_manager,
        "record_domestic_violence_question",
        _ACKNOWLEDGMENT_INFORMATION,
    )


def _household_composition_prompt_text(flow_manager: FlowManager) -> str:
    prompt_key = "record_household_composition_question"
    if flow_manager.state.get("domestic_violence", {}).get("is_experiencing"):
        prompt_key = "record_household_composition_question_domestic_violence"

    return _spoken_prompt_text(
        flow_manager,
        prompt_key,
        _ACKNOWLEDGMENT_CONFIRMATION,
    )


def _income_prompt_text(flow_manager: FlowManager) -> str:
    return _spoken_prompt_text(
        flow_manager,
        "record_income_question",
        _ACKNOWLEDGMENT_INFORMATION,
    )


def _reported_income_categories(flow_manager: FlowManager) -> set[str]:
    income_state = flow_manager.state.get("income", {})
    listing = income_state.get("listing") if isinstance(income_state, dict) else None
    categories: set[str] = set()

    if not isinstance(listing, dict):
        return categories

    for member_income in listing.values():
        if isinstance(member_income, dict):
            categories.update(category.strip().lower() for category in member_income)

    return categories


def _assets_receives_benefits_prompt_text(flow_manager: FlowManager) -> str:
    categories = _reported_income_categories(flow_manager)
    has_tanf = "tanf (temporary assistance for needy families)" in categories
    has_ssi = bool(
        {
            "ssi (supplemental security income)",
            "ssi/ssdi combo",
        }
        & categories
    )

    if has_tanf and has_ssi:
        prompt_key = "record_assets_receives_benefits_question_tanf_ssi"
    elif has_tanf:
        prompt_key = "record_assets_receives_benefits_question_tanf"
    elif has_ssi:
        prompt_key = "record_assets_receives_benefits_question_ssi"
    else:
        prompt_key = "record_assets_receives_benefits_question"

    return _spoken_prompt_text(
        flow_manager,
        prompt_key,
        _ACKNOWLEDGMENT_INFORMATION,
    )


async def _speak_dynamic_prompt(action: dict, flow_manager: FlowManager) -> None:
    text = action["text_builder"](flow_manager)
    await _log_spoken_text(flow_manager, text)
    await flow_manager.worker.queue_frame(TTSSpeakFrame(text=text))


async def _speak_language_selection_prompt(
    action: dict, flow_manager: FlowManager
) -> None:
    welcome_prompt_key = action.get("welcome_prompt_key")
    if welcome_prompt_key:
        welcome_prompt = prompts.get_spoken_prompt(welcome_prompt_key)
        await _log_spoken_text(flow_manager, welcome_prompt)
        await flow_manager.worker.queue_frame(TTSSpeakFrame(text=welcome_prompt))

    english_prompt = prompts.get_spoken_prompt(action["english_prompt_key"])
    spanish_prompt = prompts.get_spoken_prompt(action["spanish_prompt_key"])

    english_voice = get_deepgram_tts_voices(Language.EN)
    spanish_voice = get_deepgram_tts_voices(Language.ES)

    await flow_manager.worker.queue_frame(
        TTSUpdateSettingsFrame(delta=DeepgramTTSService.Settings(voice=english_voice))
    )
    await _log_spoken_text(flow_manager, english_prompt)
    await flow_manager.worker.queue_frame(TTSSpeakFrame(text=english_prompt))
    await flow_manager.worker.queue_frame(
        TTSUpdateSettingsFrame(delta=DeepgramTTSService.Settings(voice=spanish_voice))
    )
    await _log_spoken_text(flow_manager, spanish_prompt)
    await flow_manager.worker.queue_frame(TTSSpeakFrame(text=spanish_prompt))
    await flow_manager.worker.queue_frame(
        TTSUpdateSettingsFrame(delta=DeepgramTTSService.Settings(voice=english_voice))
    )


def node_record_language(include_initial_greeting: bool = False) -> NodeConfig:
    pre_action = {
        "type": "function",
        "handler": _speak_language_selection_prompt,
        "english_prompt_key": "record_language_prompt_english",
        "spanish_prompt_key": "record_language_prompt_spanish",
    }
    if include_initial_greeting:
        pre_action["welcome_prompt_key"] = "initial_greeting"

    return {
        **prompts.get("record_language"),
        "functions": [record_language],
        "pre_actions": [pre_action],
        "respond_immediately": False,
    }


def node_record_phone_number(phone_number: str | None = None) -> NodeConfig:
    return {
        **node_partial_reset_with_state(),
        **prompts.get("record_phone_number", phone_number=phone_number or ""),
        "functions": [record_phone_number],
        "pre_actions": [
            {
                "type": "function",
                "handler": _speak_dynamic_prompt,
                "text_builder": _phone_number_prompt_text,
            }
        ],
        "respond_immediately": False,
    }


def node_record_phone_type(phone_number: str | None = None) -> NodeConfig:
    return {
        **node_partial_reset_with_state(),
        **prompts.get("record_phone_type", phone_number=phone_number or ""),
        "functions": [record_phone_type],
        "pre_actions": [
            {
                "type": "function",
                "handler": _speak_dynamic_prompt,
                "text_builder": _phone_type_prompt_text,
            }
        ],
        "respond_immediately": False,
    }


def node_record_name() -> NodeConfig:
    return {
        **node_partial_reset_with_state(),
        **prompts.get("record_name"),
        "functions": [record_name],
        "pre_actions": [
            {
                "type": "function",
                "handler": _speak_dynamic_prompt,
                "text_builder": _name_prompt_text,
            }
        ],
        "respond_immediately": False,
    }


def node_record_service_area() -> NodeConfig:
    return _build_step_node(
        "record_service_area",
        [record_service_area],
        text_builder=_service_area_prompt_text,
    )


def node_record_case_type() -> NodeConfig:
    return _build_step_node(
        "record_case_type",
        [record_case_type],
        text_builder=_case_type_prompt_text,
    )


def node_record_adverse_parties() -> NodeConfig:
    return _build_step_node(
        "record_adverse_parties",
        [record_adverse_parties],
        text_builder=_adverse_parties_prompt_text,
    )


def node_record_domestic_violence() -> NodeConfig:
    return _build_step_node(
        "record_domestic_violence",
        [record_domestic_violence],
        text_builder=_domestic_violence_prompt_text,
    )


def node_record_household_composition() -> NodeConfig:
    return _build_step_node(
        "record_household_composition",
        [record_household_composition],
        text_builder=_household_composition_prompt_text,
    )


def node_record_income() -> NodeConfig:
    return _build_step_node(
        "record_income",
        [record_income],
        text_builder=_income_prompt_text,
    )


def node_confirm_income_over_limit() -> NodeConfig:
    return _build_step_node(
        "confirm_income_over_limit",
        [continue_intake, send_over_limit_referral_and_end],
        text_builder=_spoken_prompt_text_builder("confirm_income_over_limit_question"),
    )


def node_record_assets_receives_benefits() -> NodeConfig:
    return _build_step_node(
        "record_assets_receives_benefits",
        [record_assets_receives_benefits],
        text_builder=_assets_receives_benefits_prompt_text,
    )


def node_record_assets_cash_accounts() -> NodeConfig:
    return _build_step_node(
        "record_assets_cash_accounts",
        [record_assets_cash_accounts],
        text_builder=_spoken_prompt_text_builder(
            "record_assets_cash_accounts_question",
            _ACKNOWLEDGMENT_CONFIRMATION,
        ),
    )


def node_record_assets_investments() -> NodeConfig:
    return _build_step_node(
        "record_assets_investments",
        [record_assets_investments],
        text_builder=_spoken_prompt_text_builder(
            "record_assets_investments_question",
            _ACKNOWLEDGMENT_INFORMATION,
        ),
    )


def node_record_assets_other_property() -> NodeConfig:
    return _build_step_node(
        "record_assets_other_property",
        [record_assets_other_property],
        text_builder=_spoken_prompt_text_builder(
            "record_assets_other_property_question",
            _ACKNOWLEDGMENT_INFORMATION,
        ),
    )


def node_record_assets_list(current_assets_summary: str) -> NodeConfig:
    return _build_step_node(
        "record_assets_list",
        [record_assets_list],
        prompt_kwargs={"current_assets_summary": current_assets_summary},
        text_builder=_spoken_prompt_text_builder(
            "record_assets_list_confirmation",
            _ACKNOWLEDGMENT_INFORMATION,
            {"current_assets_summary": current_assets_summary},
        ),
    )


def node_confirm_assets_over_limit() -> NodeConfig:
    return _build_step_node(
        "confirm_assets_over_limit",
        [continue_intake, send_over_limit_referral_and_end],
        text_builder=_spoken_prompt_text_builder("confirm_assets_over_limit_question"),
    )


def node_record_citizenship() -> NodeConfig:
    return _build_step_node(
        "record_citizenship",
        [record_citizenship],
        text_builder=_spoken_prompt_text_builder(
            "record_citizenship_question",
            _ACKNOWLEDGMENT_CONFIRMATION,
        ),
    )


def node_record_ssn_last_4() -> NodeConfig:
    return _build_step_node(
        "record_ssn_last_4",
        [record_ssn_last_4],
        text_builder=_spoken_prompt_text_builder(
            "record_ssn_last_4_question",
            _ACKNOWLEDGMENT_CONFIRMATION,
        ),
    )


def node_record_date_of_birth() -> NodeConfig:
    return _build_step_node(
        "record_date_of_birth",
        [record_date_of_birth],
        text_builder=_spoken_prompt_text_builder(
            "record_date_of_birth_question",
            _ACKNOWLEDGMENT_CONFIRMATION,
        ),
    )


def node_record_names() -> NodeConfig:
    return _build_step_node(
        "record_names",
        [record_names],
        text_builder=_spoken_prompt_text_builder(
            "record_names_question",
            _ACKNOWLEDGMENT_INFORMATION,
        ),
    )


def node_record_address() -> NodeConfig:
    return _build_step_node(
        "record_address",
        [record_address],
        text_builder=_spoken_prompt_text_builder(
            "record_address_question",
            _ACKNOWLEDGMENT_INFORMATION,
        ),
    )


def node_case_type_ineligible() -> NodeConfig:
    return _build_step_node(
        "case_type_ineligible",
        [send_case_type_referral_and_end],
        text_builder=_spoken_prompt_text_builder("case_type_ineligible_question"),
    )


def node_complete_intake(language: str = "English") -> NodeConfig:
    return _build_static_tts_node(
        prompts.get_spoken_prompt("complete_intake_thanks", language),
        post_actions=[{"type": "end_conversation"}],
    )


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


def _normalize_phone_type_value(phone_type: str) -> PhoneTypeCaller | None:
    normalized = (
        unicodedata.normalize("NFKD", phone_type)
        .encode("ascii", "ignore")
        .decode("ascii")
        .strip()
        .lower()
    )

    aliases = {
        "mobile": PhoneTypeCaller.MOBILE,
        "cell": PhoneTypeCaller.MOBILE,
        "cell phone": PhoneTypeCaller.MOBILE,
        "cellphone": PhoneTypeCaller.MOBILE,
        "cellular": PhoneTypeCaller.MOBILE,
        "celular": PhoneTypeCaller.MOBILE,
        "movil": PhoneTypeCaller.MOBILE,
        "home": PhoneTypeCaller.HOME,
        "home phone": PhoneTypeCaller.HOME,
        "house": PhoneTypeCaller.HOME,
        "casa": PhoneTypeCaller.HOME,
        "work": PhoneTypeCaller.WORK,
        "work phone": PhoneTypeCaller.WORK,
        "business": PhoneTypeCaller.WORK,
        "business phone": PhoneTypeCaller.WORK,
        "office": PhoneTypeCaller.WORK,
        "trabajo": PhoneTypeCaller.WORK,
        "other": PhoneTypeCaller.OTHER,
        "otro": PhoneTypeCaller.OTHER,
        "fax": PhoneTypeCaller.FAX,
    }
    return aliases.get(normalized)


def _normalize_referral_delivery_method(delivery_method: str) -> str | None:
    normalized = delivery_method.strip().lower()
    if normalized in {"phone", "by phone", "over the phone", "voice", "call"}:
        return "phone"
    if normalized in {"text", "sms", "text message", "message", "by text"}:
        return "text"
    return None


def _adverse_party_has_optional_details(party: AdverseParty) -> bool:
    return bool(party.suffix or party.dob or party.phones)


def _format_adverse_party_name(party: AdverseParty) -> str:
    parts = [party.first]
    if party.middle:
        parts.append(party.middle)
    parts.append(party.last)
    if party.suffix:
        parts.append(party.suffix)
    return " ".join(parts)


def _adverse_party_follow_up_error(
    adverse_parties: AdverseParties,
) -> AdversePartiesResult:
    parties_missing_details = [
        party
        for party in adverse_parties.root
        if not _adverse_party_has_optional_details(party)
    ]
    party_names = ", ".join(
        _format_adverse_party_name(party) for party in parties_missing_details
    )
    return AdversePartiesResult(
        status=Status.ERROR,
        error=(
            "Before moving on, ask whether the caller knows any phone number, date of birth, "
            f"or suffix for {party_names}. If they do not know, confirm that and then call "
            "`record_adverse_parties` again with the best available information, omitting unknown fields."
        ),
        adverse_parties=adverse_parties,
    )


def _asset_validation_error_result(error: ValidationError) -> IntakeFlowResult:
    logger.debug(error)
    cleaned_error = clean_pydantic_error_message(error)
    return IntakeFlowResult(
        status=Status.ERROR,
        error=f"""There was an error validating the `assets`: {cleaned_error}.""",
    )


def _validated_countable_assets(
    assets: list[dict] | None,
    category: IntakeValidator.AssetCategory,
) -> Assets:
    assets_validated = IntakeValidator.assets_validate(assets, category)
    countable_assets = IntakeValidator.assets_filter_countable_entries(
        [entry.root for entry in assets_validated.root]
    )
    return IntakeValidator.assets_validate(countable_assets, category)


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
        "task_messages": [],
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
    next_node = NodeConfig(node_record_language())
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
    stt_language_hint = Language.ES if normalized_language == "spanish" else Language.EN
    language_hints = [stt_language_hint]
    tts_voice = get_deepgram_tts_voices(stt_language_hint)

    await flow_manager.worker.queue_frame(
        STTUpdateSettingsFrame(
            delta=DeepgramFluxSTTService.Settings(language_hints=language_hints)
        )
    )
    await flow_manager.worker.queue_frame(
        TTSUpdateSettingsFrame(delta=DeepgramTTSService.Settings(voice=tts_voice))
    )
    flow_manager.state["tts_voice"] = tts_voice

    result = LanguageResult(status=Status.SUCCESS, language=language)
    next_node = NodeConfig(node_record_phone_number(_caller_phone_number(flow_manager)))
    return result, next_node


@convert_and_log_result("phone")
async def record_phone_number(
    flow_manager: FlowManager, phone_number: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Collect the caller's US phone number and type.

    Args:
        phone_number (str): The caller's 10 digit US phone number.
    """
    is_valid, validated_phone_number = await validator.check_phone_number(
        phone_number=phone_number
    )

    status = status_helper(is_valid)

    result = PhoneNumberResult(
        status=status,
        is_valid=is_valid,
        phone_number=validated_phone_number,
    )

    if status == Status.SUCCESS:
        next_node = NodeConfig(node_record_phone_type(validated_phone_number))
    else:
        if not is_valid:
            result.error = "Not a valid US phone number"
        next_node = None
    return result, next_node


@convert_and_log_result("phone")
async def record_phone_type(
    flow_manager: FlowManager, phone_type: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record the caller's phone type after the phone number has been confirmed.

    Args:
        phone_type (str): The type of phone (mobile, home, work, other, or fax).
    """
    phone_number = _caller_phone_number(flow_manager) or ""
    if not phone_number:
        result = IntakeFlowResult(
            status=Status.ERROR,
            error="Phone number must be recorded before phone type.",
        )
        return result, None

    validated_phone_type = _normalize_phone_type_value(phone_type)
    if validated_phone_type is None:
        result = PhoneNumberResult(
            status=Status.ERROR,
            is_valid=True,
            phone_number=phone_number,
            error=(
                "Invalid phone type. Please choose from: mobile, home, work, or other"
            ),
        )
        return result, None

    result = PhoneNumberResult(
        status=Status.SUCCESS,
        is_valid=True,
        phone_number=phone_number,
        phone_type=validated_phone_type,
    )
    next_node = NodeConfig(node_record_name())
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
    next_node = NodeConfig(node_record_service_area())
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
    canonical_location = match or ""
    is_eligible = fips_code != 0

    status = status_helper(is_eligible)
    result = ServiceAreaResult(
        status=status,
        is_eligible=is_eligible,
        location=canonical_location,
        fips_code=fips_code,
    )

    if status == Status.SUCCESS:
        next_node = NodeConfig(node_record_case_type())
    else:
        if match:
            result.error = f"""No exact match found. Maybe you meant {match}?"""
            next_node = None
        else:
            result.error = (
                "I couldn't identify a Virginia city or county from that response. "
                "Ask the caller again for only the city or county where the legal incident occurred."
            )
            next_node = None
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
        next_node = NodeConfig(node_record_adverse_parties())
    else:
        result.error = "Ineligible case type."
        next_node = NodeConfig(node_case_type_ineligible())
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

    should_request_follow_up = (
        bool(adverse_parties_validated.root)
        and any(
            not _adverse_party_has_optional_details(party)
            for party in adverse_parties_validated.root
        )
        and not flow_manager.state.get(_ADVERSE_PARTIES_FOLLOW_UP_KEY, False)
    )
    if should_request_follow_up:
        flow_manager.state[_ADVERSE_PARTIES_FOLLOW_UP_KEY] = True
        return _adverse_party_follow_up_error(adverse_parties_validated), None

    flow_manager.state.pop(_ADVERSE_PARTIES_FOLLOW_UP_KEY, None)

    result = AdversePartiesResult(
        status=Status.SUCCESS,
        adverse_parties=adverse_parties_validated,
    )
    next_node = NodeConfig(node_record_domestic_violence())
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

    next_node = NodeConfig(node_record_household_composition())
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
    next_node = NodeConfig(node_record_income())
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
        next_node = NodeConfig(node_record_assets_receives_benefits())
    else:
        result.error = """Over the household income limit"""
        next_node = NodeConfig(node_confirm_income_over_limit())
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
        IntakeValidator.assets_clear_partial_state(flow_manager.state)
        result = AssetsResult(
            status=Status.SUCCESS,
            is_eligible=True,
            listing=[],
            total_value=0,
            receives_benefits=True,
        )
        next_node = NodeConfig(node_record_citizenship())
    else:
        IntakeValidator.assets_clear_partial_state(flow_manager.state)
        result = None
        next_node = NodeConfig(node_record_assets_cash_accounts())
    return result, next_node


@convert_and_log_result("assets_cash_accounts")
async def record_assets_cash_accounts(
    flow_manager: FlowManager, assets: list[dict] | None = None
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    try:
        assets_validated = _validated_countable_assets(
            assets, IntakeValidator.AssetCategory.CASH
        )
    except ValidationError as e:
        return _asset_validation_error_result(e), None

    result = AssetCategoryResult(status=Status.SUCCESS, listing=assets_validated)
    next_node = NodeConfig(node_record_assets_investments())
    return result, next_node


@convert_and_log_result("assets_investments")
async def record_assets_investments(
    flow_manager: FlowManager, assets: list[dict] | None = None
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    try:
        assets_validated = _validated_countable_assets(
            assets, IntakeValidator.AssetCategory.INVESTMENTS
        )
    except ValidationError as e:
        return _asset_validation_error_result(e), None

    result = AssetCategoryResult(status=Status.SUCCESS, listing=assets_validated)
    next_node = NodeConfig(node_record_assets_other_property())
    return result, next_node


@convert_and_log_result("assets_other_property")
async def record_assets_other_property(
    flow_manager: FlowManager, assets: list[dict] | None = None
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    try:
        assets_validated = _validated_countable_assets(
            assets, IntakeValidator.AssetCategory.OTHER_PROPERTY
        )
    except ValidationError as e:
        return _asset_validation_error_result(e), None

    current_assets = [entry.root for entry in assets_validated.root]
    merged_assets = IntakeValidator.assets_combine_partial(
        flow_manager.state,
        overrides={"assets_other_property": current_assets},
    )
    result = AssetCategoryResult(status=Status.SUCCESS, listing=assets_validated)
    next_node = NodeConfig(
        node_record_assets_list(IntakeValidator.assets_prompt_text(merged_assets))
    )
    return result, next_node


@convert_and_log_result("assets")
async def record_assets_list(
    flow_manager: FlowManager, assets: list[dict] | None = None
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Collect assets' value and determine eligibility of caller.

    Args:
        assets (list[dict] | None):
            Optional list of asset entries to validate and total.
            If omitted or None, the function combines the previously collected
            partial asset categories from flow_manager.state before validating
            eligibility.

            Each entry maps a single asset name (str) to an integer net present
            value.

            Example:
                [
                    {"car": 5000},
                    {"savings": 2000}
                ]
    """
    try:
        assets_input = assets
        if assets_input is None:
            assets_input = IntakeValidator.assets_combine_partial(flow_manager.state)

        assets_input = IntakeValidator.assets_filter_countable_entries(assets_input)

        assets_validated = Assets.model_validate(assets_input)
        is_eligible, assets_value = await validator.check_assets(
            assets=assets_validated
        )
    except ValidationError as e:
        return _asset_validation_error_result(e), None

    status = status_helper(is_eligible)
    result = AssetsResult(
        status=status,
        is_eligible=is_eligible,
        listing=assets_validated,
        total_value=assets_value,
        receives_benefits=False,
    )
    IntakeValidator.assets_clear_partial_state(flow_manager.state)
    if status == Status.SUCCESS:
        next_node = NodeConfig(node_record_citizenship())
    else:
        result.error = "Over the household assets' value limit."
        next_node = NodeConfig(node_confirm_assets_over_limit())
    return result, next_node


@convert_and_log_result("citizenship")
async def record_citizenship(
    flow_manager: FlowManager,
    is_a_us_citizen: bool,
    answer_was_explicit: bool = False,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Record if the caller is a US citizen.

    Args:
        has_citizenship (bool): The caller's answer that they are or are not a US citizen.
    """
    if not answer_was_explicit:
        result = IntakeFlowResult(
            status=Status.ERROR,
            error=(
                "Citizenship can only be recorded after the caller explicitly says yes, "
                "no, or refuses to answer."
            ),
        )
        return result, None

    result = CitizenshipResult(status=Status.SUCCESS, is_citizen=is_a_us_citizen)
    next_node = NodeConfig(node_record_ssn_last_4())
    return result, next_node


@convert_and_log_result("ssn_last_4")
async def record_ssn_last_4(
    flow_manager: FlowManager,
    ssn_last_4: str = "",
    ssn_unavailable_reason: str = "",
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    Collect the last 4 digits of the caller's social security number.

    Args:
        ssn_last_4 (str): The last 4 digits of the caller's SSN (accepts various formats like XXXX, XXX-X, etc.)
                          Can be empty if the caller refuses or does not know.
        ssn_unavailable_reason (str): Required only when ssn_last_4 is empty. Must reflect
                                      an explicit caller response such as refusing to provide
                                      the SSN or not knowing it.
    """
    if not ssn_last_4:
        normalized_reason = ssn_unavailable_reason.strip().lower()
        allowed_skip_reasons = {
            "refused",
            "does_not_know",
            "does not know",
            "unknown",
            "prefer_not_to_say",
            "prefer not to say",
        }
        if normalized_reason not in allowed_skip_reasons:
            result = IntakeFlowResult(
                status=Status.ERROR,
                error=(
                    "SSN last 4 can only be skipped if the caller explicitly refuses "
                    "to provide it or says they do not know it."
                ),
            )
            return result, None
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
        next_node = NodeConfig(node_record_date_of_birth())
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
        next_node = NodeConfig(node_record_names())
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
    next_node = NodeConfig(node_record_address())
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
        next_node = NodeConfig(node_complete_intake(_caller_language(flow_manager)))
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
    next_node = NodeConfig(node_complete_intake(_caller_language(flow_manager)))
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

    deterministic_builders = {
        "record_name": node_record_name,
        "record_service_area": node_record_service_area,
        "record_case_type": node_record_case_type,
        "record_adverse_parties": node_record_adverse_parties,
        "record_domestic_violence": node_record_domestic_violence,
        "record_household_composition": node_record_household_composition,
        "record_income": node_record_income,
        "record_assets_receives_benefits": node_record_assets_receives_benefits,
        "record_assets_cash_accounts": node_record_assets_cash_accounts,
        "record_assets_investments": node_record_assets_investments,
        "record_assets_other_property": node_record_assets_other_property,
        "record_citizenship": node_record_citizenship,
        "record_ssn_last_4": node_record_ssn_last_4,
        "record_date_of_birth": node_record_date_of_birth,
        "record_names": node_record_names,
        "record_address": node_record_address,
    }
    deterministic_builder = deterministic_builders.get(next_step)
    if deterministic_builder is not None:
        return None, NodeConfig(deterministic_builder())

    next_node = NodeConfig(
        node_partial_reset_with_state()
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
    return await _send_referral_and_end(flow_manager, delivery_method)


async def _send_referral_and_end(
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
        await _send_referral_sms(flow_manager, REFERRAL)
    return None, _node_referral_and_end(flow_manager, REFERRAL, normalized_method)


async def send_case_type_referral_and_end(
    flow_manager: FlowManager,
    delivery_method: str,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    return await _send_referral_and_end(flow_manager, delivery_method)


async def send_over_limit_referral_and_end(
    flow_manager: FlowManager,
    delivery_method: str,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    return await _send_referral_and_end(flow_manager, delivery_method)


async def end_conversation(
    flow_manager: FlowManager,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    End the conversation.
    """
    return None, node_end_conversation(_caller_language(flow_manager))


def node_end_conversation(language: str = "English") -> NodeConfig:
    """
    Create the final node.
    """
    return _build_static_tts_node(
        prompts.get_spoken_prompt("end_goodbye", language),
        post_actions=[{"type": "end_conversation"}],
    )


async def caller_ended_conversation(
    flow_manager: FlowManager,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    """
    The caller ended the conversation.
    """
    return None, node_caller_ended_conversation(_caller_language(flow_manager))


def node_caller_ended_conversation(language: str = "English") -> NodeConfig:
    """
    Create the final node.
    """
    return _build_static_tts_node(
        prompts.get_spoken_prompt("end_goodbye", language),
        post_actions=[{"type": "end_conversation"}],
    )
