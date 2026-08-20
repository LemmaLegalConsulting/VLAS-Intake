import asyncio
import sys
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from loguru import logger
from pipecat.flows import (
    ContextStrategy,
    ContextStrategyConfig,
    FlowManager,
    NodeConfig,
)
from pipecat.frames.frames import (
    ManuallySwitchServiceFrame,
    STTUpdateSettingsFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.transcriptions.language import Language
from pydantic import ValidationError

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
    HouseholdMembersResult,
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
    HouseholdMembers,
    PhoneTypeCaller,
)
from intake_bot.nodes.utils import (
    clean_pydantic_error_message,
    convert_and_log_result,
    log_pydantic_validation_error,
    status_helper,
)
from intake_bot.nodes.validator import IntakeValidator
from intake_bot.services.dialpad import (
    REFERRAL,
    SMS,
    ReferralContent,
)
from intake_bot.services.phonenumber import phone_number_is_valid
from intake_bot.utils.ev import get_deepgram_tts_voices, get_ev
from intake_bot.utils.node_prompts import NodePrompts

prompts = NodePrompts()
validator = IntakeValidator()
sms_service = SMS()
_ADVERSE_PARTIES_FOLLOW_UP_KEY = "_adverse_parties_follow_up_requested"
_HOUSEHOLD_COMPOSITION_PENDING_KEY = "_pending_household_composition"
_SERVICE_AREA_PENDING_KEY = "_pending_service_area"
_SERVICE_AREA_RETRY_KEY = "_service_area_unresolved_count"

_ACKNOWLEDGMENT_CONFIRMATION = "confirmation"
_ACKNOWLEDGMENT_INFORMATION = "information"

_SPANISH_AFFIRMATIVE = {
    "si",
    "sí",
    "sip",
    "correcto",
    "cierto",
    "afirmativo",
    "confirmado",
    "de acuerdo",
    "vale",
    "ok",
    "okay",
}

_SPANISH_NEGATIVE = {
    "no",
    "nop",
    "nada",
    "incorrecto",
    "falso",
    "negativo",
}


def _is_affirmative(text: str) -> bool:
    t = text.strip().lower().rstrip(".,!?")
    return t in {
        "yes",
        "yeah",
        "yep",
        "correct",
        "right",
        "that's right",
        "that is right",
        "that's correct",
        "that is correct",
        "yes, that's right",
        "yes that's right",
        "yes, that is right",
        "yes that is right",
        "yes, that's correct",
        "yes that's correct",
        "yes sir",
        "yes ma'am",
        "yeah, correct",
        "yeah correct",
        "sure",
        "confirm",
        "confirmed",
        "affirmative",
        "true",
        "y",
        "sí, correcto",
        "si, correcto",
        "sí señor",
        "si señor",
        "sí, así es",
        "si, asi es",
        *_SPANISH_AFFIRMATIVE,
    }


def _is_negative(text: str) -> bool:
    t = text.strip().lower().rstrip(".,!?")
    return t in {
        "no",
        "nope",
        "nah",
        "negative",
        "incorrect",
        "false",
        "wrong",
        "n",
        "no, that's not right",
        "no that's not right",
        "no, that's wrong",
        "no that's wrong",
        "no, eso no es correcto",
        *_SPANISH_NEGATIVE,
    }


def _service_area_pending(flow_manager: FlowManager) -> dict | None:
    raw = flow_manager.state.get(_SERVICE_AREA_PENDING_KEY)
    if isinstance(raw, dict) and "candidates" in raw:
        return raw
    return None


def _store_service_area_pending(
    flow_manager: FlowManager,
    candidates: list[str],
) -> None:
    flow_manager.state[_SERVICE_AREA_PENDING_KEY] = {
        "candidates": candidates[:2],
    }


def _clear_service_area_pending(flow_manager: FlowManager) -> None:
    flow_manager.state.pop(_SERVICE_AREA_PENDING_KEY, None)


def _household_composition_pending(flow_manager: FlowManager) -> dict | None:
    pending = flow_manager.state.get(_HOUSEHOLD_COMPOSITION_PENDING_KEY)
    if not isinstance(pending, dict):
        return None
    if not {"number_of_adults", "number_of_children"} <= pending.keys():
        return None
    return pending


def _store_household_composition_pending(
    flow_manager: FlowManager,
    number_of_adults: int,
    number_of_children: int,
) -> None:
    flow_manager.state[_HOUSEHOLD_COMPOSITION_PENDING_KEY] = {
        "number_of_adults": number_of_adults,
        "number_of_children": number_of_children,
    }


def _clear_household_composition_pending(flow_manager: FlowManager) -> None:
    flow_manager.state.pop(_HOUSEHOLD_COMPOSITION_PENDING_KEY, None)


def _reset_retry_count(flow_manager: FlowManager) -> None:
    flow_manager.state.pop(_SERVICE_AREA_RETRY_KEY, None)


def _strip_negative_prefix(text: str) -> str:
    stripped = text.strip()
    normalized = stripped.lower().rstrip(".,!?")
    for prefix in (
        "no,",
        "no ",
        "nope,",
        "nope ",
        "not ",
        "wrong,",
        "wrong ",
        "incorrect,",
        "incorrect ",
        "actually,",
        "actually ",
        "sorry,",
        "sorry ",
        "incorrecto,",
        "incorrecto ",
        "falso,",
        "falso ",
        "negativo,",
        "negativo ",
        "correction,",
        "correction ",
    ):
        if normalized.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return stripped


######################################################################
# Nodes
######################################################################


def node_start() -> NodeConfig:
    initial_node = get_ev("TEST_INITIAL_NODE")
    if not initial_node:
        return node_record_language(include_initial_greeting=True)

    initial_builder = _intake_node_builders().get(initial_node)
    if initial_builder is None:
        raise ValueError(f"""Initial node '{initial_node}' does not exist.""")

    return initial_builder()


def node_partial_reset_with_state() -> NodeConfig:
    return cast(
        NodeConfig,
        {
            **prompts.get("primary_role_message"),
            "context_strategy": ContextStrategyConfig(
                strategy=ContextStrategy.RESET,
            ),
        },
    )


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
    return datetime.now(UTC).isoformat(timespec="milliseconds")


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

    return cast(NodeConfig, node)


def _build_static_tts_node(
    text: str, post_actions: list[dict] | None = None
) -> NodeConfig:
    node: dict[str, Any] = {
        "task_messages": [],
        "pre_actions": [{"type": "tts_say", "text": text}],
        "functions": [],
        "respond_immediately": False,
    }
    if post_actions is not None:
        node["post_actions"] = post_actions
    return cast(NodeConfig, node)


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


def _domestic_violence_prompt_text(flow_manager: FlowManager) -> str:
    return _spoken_prompt_text(
        flow_manager,
        "record_domestic_violence_question",
        _ACKNOWLEDGMENT_INFORMATION,
    )


def _household_composition_confirmation_prompt_text(
    flow_manager: FlowManager,
) -> str:
    pending = _household_composition_pending(flow_manager) or {}
    adults = pending.get("number_of_adults", 1)
    children = pending.get("number_of_children", 0)
    if _caller_language(flow_manager) == "spanish":
        adult_count_phrase = "un adulto" if adults == 1 else f"{adults} adultos"
        child_count_phrase = (
            "ningun nino"
            if children == 0
            else ("un nino" if children == 1 else f"{children} ninos")
        )
    else:
        adult_count_phrase = "one adult" if adults == 1 else f"{adults} adults"
        child_count_phrase = (
            "no children"
            if children == 0
            else ("one child" if children == 1 else f"{children} children")
        )
    return _spoken_prompt_text(
        flow_manager,
        "confirm_household_composition_question",
        adult_count_phrase=adult_count_phrase,
        child_count_phrase=child_count_phrase,
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
    has_tanf = bool(
        {
            "tanf",
            "tanf (temporary assistance for needy families)",
        }
        & categories
    )
    has_ssi = bool(
        {
            "ssi",
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


async def _select_tts_language(flow_manager: FlowManager, language: Language) -> str:
    tts_services = getattr(flow_manager, "_tts_services", None)
    if not isinstance(tts_services, dict) or language not in tts_services:
        raise RuntimeError(f"No TTS service configured for {language}")

    service = tts_services[language]
    voice = get_deepgram_tts_voices(language)
    settings = (
        DeepgramTTSService.Settings(voice=voice)
        if language == Language.ES
        else DeepgramFluxTTSService.Settings(voice=voice)
    )
    await flow_manager.worker.queue_frame(
        TTSUpdateSettingsFrame(delta=settings, service=service)
    )
    await flow_manager.worker.queue_frame(ManuallySwitchServiceFrame(service=service))
    return voice


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

    await _select_tts_language(flow_manager, Language.EN)
    await _log_spoken_text(flow_manager, english_prompt)
    await flow_manager.worker.queue_frame(TTSSpeakFrame(text=english_prompt))
    await _select_tts_language(flow_manager, Language.ES)
    await _log_spoken_text(flow_manager, spanish_prompt)
    await flow_manager.worker.queue_frame(TTSSpeakFrame(text=spanish_prompt))
    await _select_tts_language(flow_manager, Language.EN)


def node_record_language(include_initial_greeting: bool = False) -> NodeConfig:
    pre_action = {
        "type": "function",
        "handler": _speak_language_selection_prompt,
        "english_prompt_key": "record_language_prompt_english",
        "spanish_prompt_key": "record_language_prompt_spanish",
    }
    if include_initial_greeting:
        pre_action["welcome_prompt_key"] = "initial_greeting"

    return cast(
        NodeConfig,
        {
            **prompts.get("record_language"),
            "functions": [record_language],
            "pre_actions": [pre_action],
            "respond_immediately": False,
        },
    )


def node_record_phone_number(phone_number: str | None = None) -> NodeConfig:
    return cast(
        NodeConfig,
        {
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
        },
    )


def node_record_phone_type(phone_number: str | None = None) -> NodeConfig:
    return cast(
        NodeConfig,
        {
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
        },
    )


def node_record_name() -> NodeConfig:
    return cast(
        NodeConfig,
        {
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
        },
    )


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
    )


def node_confirm_household_composition(
    number_of_adults: int, number_of_children: int
) -> NodeConfig:
    return _build_step_node(
        "confirm_household_composition",
        [confirm_household_composition, record_household_composition],
        prompt_kwargs={
            "number_of_adults": number_of_adults,
            "number_of_children": number_of_children,
        },
        text_builder=_household_composition_confirmation_prompt_text,
    )


def node_record_household_members() -> NodeConfig:
    return _build_step_node(
        "record_household_members",
        [record_household_members],
    )


def node_record_income() -> NodeConfig:
    return _build_step_node(
        "record_income",
        [record_income],
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


def node_service_area_unserved() -> NodeConfig:
    return _build_step_node(
        "service_area_unserved",
        [send_general_referral_and_end],
        text_builder=_spoken_prompt_text_builder("service_area_unserved_question"),
    )


def node_service_area_unresolved() -> NodeConfig:
    return _build_step_node(
        "service_area_unresolved",
        [send_general_referral_and_end],
        text_builder=_spoken_prompt_text_builder("service_area_unresolved_question"),
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


def _adverse_party_has_optional_details(party: AdverseParty) -> bool:
    if party.organization_name:
        return bool(party.phones)
    return bool(party.suffix or party.dob or party.phones)


def _normalize_referral_delivery_method(delivery_method: str) -> str | None:
    normalized = delivery_method.strip().lower()
    if normalized in {"phone", "by phone", "over the phone", "voice", "call"}:
        return "phone"
    if normalized in {"text", "sms", "text message", "message", "by text"}:
        return "text"
    return None


def _format_adverse_party_name(party: AdverseParty) -> str:
    if party.organization_name:
        return party.organization_name
    parts = [party.first or ""]
    if party.middle:
        parts.append(party.middle)
    parts.append(party.last or "")
    if party.suffix:
        parts.append(party.suffix)
    return " ".join(parts)


def _normalize_person_name(name: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    return " ".join(normalized.split())


def _normalize_member_list_income(income: dict) -> dict | None:
    """Convert the member-list envelope occasionally produced by the LLM."""
    if set(income) != {"members"} or not isinstance(income.get("members"), list):
        return None

    normalized: dict[str, dict[str, dict[str, object]]] = {}
    for member in income["members"]:
        if not isinstance(member, dict):
            return None
        name = member.get("name")
        entries = member.get("income")
        if (
            not isinstance(name, str)
            or not name.strip()
            or name in normalized
            or not isinstance(entries, list)
        ):
            return None

        member_income: dict[str, dict[str, object]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                return None
            category = entry.get("category")
            if (
                not isinstance(category, str)
                or not category.strip()
                or category in member_income
                or "amount" not in entry
                or "period" not in entry
            ):
                return None
            member_income[category] = {
                "amount": entry["amount"],
                "period": entry["period"],
            }
        normalized[name] = member_income

    return normalized


def _caller_full_name(flow_manager: FlowManager) -> str | None:
    names_state = flow_manager.state.get("names", {})
    names = names_state.get("names", []) if isinstance(names_state, dict) else []
    if not names or not isinstance(names[0], dict):
        return None

    primary_name = names[0]
    parts = [
        primary_name.get("first"),
        primary_name.get("middle"),
        primary_name.get("last"),
        primary_name.get("suffix"),
    ]
    full_name = " ".join(
        part.strip() for part in parts if isinstance(part, str) and part.strip()
    )
    return full_name or None


def _known_adverse_party_names(flow_manager: FlowManager) -> set[str]:
    adverse_state = flow_manager.state.get("adverse_parties", {})
    parties = (
        adverse_state.get("adverse_parties", [])
        if isinstance(adverse_state, dict)
        else []
    )
    names = set()
    for party in parties:
        if not isinstance(party, dict):
            continue
        organization_name = party.get("organization_name")
        if isinstance(organization_name, str) and organization_name.strip():
            names.add(_normalize_person_name(organization_name))
            continue
        full_name = " ".join(
            str(party.get(field, "")).strip()
            for field in ("first", "middle", "last", "suffix")
            if party.get(field)
        )
        if full_name:
            names.add(_normalize_person_name(full_name))
    return names


def _adverse_party_follow_up_error(
    adverse_parties: AdverseParties,
) -> AdversePartiesResult:
    parties_missing_details = [
        party
        for party in adverse_parties.root
        if not _adverse_party_has_optional_details(party)
    ]
    organizations = [
        _format_adverse_party_name(party)
        for party in parties_missing_details
        if party.organization_name
    ]
    individuals = [
        _format_adverse_party_name(party)
        for party in parties_missing_details
        if not party.organization_name
    ]
    requests = []
    if organizations:
        requests.append(f"a business phone number for {', '.join(organizations)}")
    if individuals:
        requests.append(
            f"a phone number, date of birth, or suffix for {', '.join(individuals)}"
        )
    return AdversePartiesResult(
        status=Status.ERROR,
        error=(
            f"Before moving on, ask whether the caller knows {'; and '.join(requests)}. "
            "Do not ask an organization for a date of birth or suffix. If the caller does not "
            "know, call `record_adverse_parties` again with `optional_details_confirmed=true` "
            "and omit unknown fields."
        ),
        adverse_parties=adverse_parties,
    )


def _asset_validation_error_result(error: ValidationError) -> IntakeFlowResult:
    cleaned_error = clean_pydantic_error_message(error)
    log_pydantic_validation_error("assets", error)
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
        content.phone_delivery_text(language)
        if delivery_method == "phone"
        else content.text_delivery_text(language)
    )
    return _build_static_tts_node(
        spoken_text,
        post_actions=[{"type": "end_conversation"}],
    )


async def _send_referral_sms(
    flow_manager: FlowManager, content: ReferralContent
) -> dict:
    phone_number = _caller_phone_number(flow_manager)
    if not phone_number:
        logger.info(
            "Skipping referral SMS because no caller phone number is available."
        )
        return {"accepted": False, "reason": "no_phone_number"}

    valid_e164, normalized = phone_number_is_valid(phone_number)
    if not valid_e164:
        logger.info(
            "Skipping referral SMS because caller phone number is not valid E.164."
        )
        return {"accepted": False, "reason": "invalid_phone"}
    phone_number = normalized

    if not sms_service.is_configured:
        logger.warning("Skipping referral SMS because Dialpad SMS is not configured.")
        return {"accepted": False, "reason": "not_configured"}

    message_text = content.sms_text(_caller_language(flow_manager))
    try:
        response = await sms_service.send(phone_number, message_text)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception:  # noqa: BLE001 - SMS delivery failure becomes result data
        logger.warning("Failed to send referral SMS")
        return {"accepted": False, "reason": "send_failed"}

    sms_log = flow_manager.state.setdefault("sms_messages", [])
    sms_log.append(
        {
            "status": response.get("status"),
            "accepted": True,
        }
    )
    logger.info("Referral SMS accepted by Dialpad API")
    return {"accepted": True}


async def system_phone_number(
    flow_manager: FlowManager,
) -> tuple[dict[str, Any] | None, NodeConfig | None]:
    caller_id_phone_number = flow_manager.state.get("phone")
    is_valid, validated_caller_id_phone_number = await validator.check_phone_number(
        phone_number=str(caller_id_phone_number or "")
    )

    if is_valid:
        flow_manager.state["phone"] = {
            "phone_number": validated_caller_id_phone_number,
        }
    else:
        existing = flow_manager.state.get("phone")
        if isinstance(existing, dict) and existing.get("phone_number"):
            flow_manager.state["phone"] = existing

    status = status_helper(is_valid)
    result = {
        "status": status.value,
        "phone_number": validated_caller_id_phone_number,
    }
    next_node = NodeConfig(node_record_language())
    return result, next_node


@convert_and_log_result("language")
async def record_language(
    flow_manager: FlowManager, language: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    normalized_language = language.strip().lower()
    language_aliases = {
        "english": "English",
        "ingles": "English",
        "inglés": "English",
        "spanish": "Spanish",
        "espanol": "Spanish",
        "español": "Spanish",
    }
    canonical_language = language_aliases.get(normalized_language)
    if canonical_language is None:
        return (
            IntakeFlowResult(
                status=Status.ERROR,
                error="The caller must clearly choose English or Spanish.",
            ),
            None,
        )

    stt_language_hint = Language.ES if canonical_language == "Spanish" else Language.EN
    language_hints = [stt_language_hint]

    await flow_manager.worker.queue_frame(
        STTUpdateSettingsFrame(
            delta=DeepgramFluxSTTService.Settings(language_hints=language_hints)
        )
    )
    tts_voice = await _select_tts_language(flow_manager, stt_language_hint)
    flow_manager.state["tts_voice"] = tts_voice

    result = LanguageResult(status=Status.SUCCESS, language=canonical_language)
    next_node = NodeConfig(node_record_phone_number(_caller_phone_number(flow_manager)))
    return result, next_node


@convert_and_log_result("phone")
async def record_phone_number(
    flow_manager: FlowManager, phone_number: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
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
    try:
        name_validated = CallerName.model_validate(
            {
                "first": first,
                "middle": middle,
                "last": last,
                "suffix": suffix,
                "type": "Legal Name",
            }
        )
    except ValidationError as e:
        cleaned_error = clean_pydantic_error_message(e)
        log_pydantic_validation_error("name", e)
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
    pending = _service_area_pending(flow_manager)
    if pending:
        pending_candidates = pending.get("candidates", [])
        trimmed = location.strip().lower().rstrip(".,!?")

        if _is_affirmative(trimmed):
            if len(pending_candidates) == 1:
                candidate = pending_candidates[0]
                resolved = await validator.check_service_area(location=candidate)
                outcome = resolved.get("outcome", "unknown")
                if outcome in ("exact_match", "unserved"):
                    _clear_service_area_pending(flow_manager)
                    _reset_retry_count(flow_manager)
                    result, nn = _service_area_success(
                        resolved, outcome, candidate=candidate
                    )
                    return result, nn
            if len(pending_candidates) > 1:
                candidate_list = ", ".join(pending_candidates)
                return (
                    ServiceAreaResult(
                        status=Status.ERROR,
                        outcome="ambiguous",
                        candidates=pending_candidates,
                        error=(
                            f"I found multiple matching locations: {candidate_list}. "
                            "Please tell me which city or county by name."
                        ),
                    ),
                    None,
                )
            # Affirmation failed — charge retry
            count = _charge_retry_or_refer(flow_manager)
            if count is None:
                return _terminal_unresolved(flow_manager)
            return _retry_error(count), None

        if _is_negative(trimmed):
            _clear_service_area_pending(flow_manager)
            count = _charge_retry_or_refer(flow_manager)
            if count is None:
                return _terminal_unresolved(flow_manager)
            return _retry_error(count), None

        # A substantive answer replaces the pending suggestion. Resolve it before
        # changing state so a validation failure does not lose the old candidate.
        corrected = _strip_negative_prefix(location)
        resolved = await validator.check_service_area(location=corrected)

        _clear_service_area_pending(flow_manager)
        return _handle_service_area_resolution(flow_manager, resolved)

    _clear_service_area_pending(flow_manager)
    resolved = await validator.check_service_area(location=location)

    return _handle_service_area_resolution(flow_manager, resolved)


def _handle_service_area_resolution(
    flow_manager: FlowManager, resolved: dict
) -> tuple[ServiceAreaResult, NodeConfig | None]:
    """Apply one resolver outcome consistently for initial and corrected answers."""

    outcome = resolved.get("outcome", "unknown")
    candidates = resolved.get("candidates", [])

    if outcome == "exact_match":
        _reset_retry_count(flow_manager)
        result = ServiceAreaResult(
            status=Status.SUCCESS,
            is_eligible=resolved.get("is_eligible"),
            location=resolved.get("canonical_name"),
            fips_code=resolved.get("fips"),
            outcome=outcome,
            candidates=candidates,
            match_type=resolved.get("match_type"),
        )
        next_node = NodeConfig(node_record_case_type())
    elif outcome == "unserved":
        _reset_retry_count(flow_manager)
        result = ServiceAreaResult(
            status=Status.SUCCESS,
            is_eligible=False,
            location=resolved.get("canonical_name"),
            fips_code=resolved.get("fips"),
            outcome=outcome,
        )
        next_node = NodeConfig(node_service_area_unserved())
    elif outcome == "suggested":
        _reset_retry_count(flow_manager)
        _store_service_area_pending(flow_manager, candidates[:1])
        result = ServiceAreaResult(
            status=Status.ERROR,
            outcome=outcome,
            candidates=candidates,
            error=f"Did you mean {candidates[0]}? Please confirm."
            if candidates
            else "",
        )
        next_node = None
    elif outcome == "ambiguous":
        _reset_retry_count(flow_manager)
        candidate_list = ", ".join(candidates[:2]) if candidates else ""
        _store_service_area_pending(flow_manager, candidates[:2])
        result = ServiceAreaResult(
            status=Status.ERROR,
            outcome=outcome,
            candidates=candidates,
            error=f"I found multiple matching locations: {candidate_list}. Which one is correct?"
            if candidate_list
            else "I couldn't determine the location. Please specify the city or county name.",
        )
        next_node = None
    elif outcome in ("unresolved_service_area", "unknown"):
        count = _charge_retry_or_refer(flow_manager)
        if count is None:
            return _terminal_unresolved(flow_manager)
        result = ServiceAreaResult(
            status=Status.ERROR,
            outcome=outcome,
            candidates=candidates,
            error="I couldn't identify a Virginia city or county from that response. Ask the caller to repeat or spell the city or county.",
        )
        next_node = None
    else:
        result = ServiceAreaResult(
            status=Status.ERROR,
            outcome=outcome,
            error="I couldn't identify a Virginia city or county from that response. Ask the caller again for only the city or county where the legal incident occurred.",
        )
        next_node = None
    return result, next_node


def _charge_retry_or_refer(flow_manager: FlowManager) -> int | None:
    """Charge one retry.  Returns new count, or None if referral is needed."""
    count = flow_manager.state.get(_SERVICE_AREA_RETRY_KEY, 0) + 1
    if count >= 2:
        _clear_service_area_pending(flow_manager)
        _reset_retry_count(flow_manager)
        return None
    flow_manager.state[_SERVICE_AREA_RETRY_KEY] = count
    return count


def _terminal_unresolved(
    flow_manager: FlowManager,
) -> tuple[ServiceAreaResult, NodeConfig]:
    return (
        ServiceAreaResult(
            status=Status.SUCCESS,
            outcome="unresolved_service_area",
            error="We could not determine whether that location is in our service area.",
        ),
        NodeConfig(node_service_area_unresolved()),
    )


def _retry_error(count: int) -> ServiceAreaResult:
    return ServiceAreaResult(
        status=Status.ERROR,
        outcome="unresolved_service_area",
        error="Please tell me the Virginia city or county where the legal issue happened; we cannot determine coverage from the information provided.",
    )


def _service_area_success(
    resolved: dict,
    outcome: str,
    *,
    candidate: str | None = None,
) -> tuple[ServiceAreaResult, NodeConfig]:
    if outcome not in ("exact_match", "unserved"):
        raise ValueError(f"Cannot accept nonterminal service-area outcome: {outcome}")
    location = resolved.get("canonical_name") or candidate
    result = ServiceAreaResult(
        status=Status.SUCCESS,
        is_eligible=resolved.get("is_eligible"),
        location=location,
        fips_code=resolved.get("fips"),
        outcome=outcome,
        candidates=resolved.get("candidates", []),
        match_type=resolved.get("match_type"),
    )
    next_node = NodeConfig(
        node_service_area_unserved()
        if outcome == "unserved" or not resolved.get("is_eligible", True)
        else node_record_case_type()
    )
    return result, next_node


@convert_and_log_result("case_type")
async def record_case_type(
    flow_manager: FlowManager, case_description: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    language_state = flow_manager.state.get("language", {})
    language = (
        language_state.get("language", "English")
        if isinstance(language_state, dict)
        else "English"
    )
    case_response = await validator.check_case_type(
        case_description=case_description, language=language
    )

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
    if case_response.is_eligible is None:
        return (
            IntakeFlowResult(
                status=Status.ERROR,
                error=(
                    "The case-type classifier could not determine case-type eligibility. "
                    "Please provide more details about the legal problem."
                ),
            ),
            None,
        )

    is_eligible = case_response.is_eligible
    legal_problem_code = case_response.legal_problem_code or ""

    result = CaseTypeResult(
        status=Status.SUCCESS,
        is_eligible=is_eligible,
        legal_problem_code=legal_problem_code,
        case_description=case_description,
    )
    if is_eligible is False:
        result.error = "Ineligible case type."
        next_node = NodeConfig(node_case_type_ineligible())
    else:
        next_node = NodeConfig(node_record_adverse_parties())
    return result, next_node


@convert_and_log_result("adverse_parties")
async def record_adverse_parties(
    flow_manager: FlowManager,
    adverse_parties: list[dict],
    optional_details_confirmed: bool = False,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    try:
        adverse_parties_validated = AdverseParties.model_validate(adverse_parties)
    except ValidationError as e:
        cleaned_error = clean_pydantic_error_message(e)
        log_pydantic_validation_error("adverse_parties", e)
        result = IntakeFlowResult(
            status=Status.ERROR,
            error=f"""There was an error validating the `adverse_parties`: {cleaned_error}.""",
        )
        return result, None

    should_request_follow_up = bool(adverse_parties_validated.root) and any(
        not _adverse_party_has_optional_details(party)
        for party in adverse_parties_validated.root
    )
    follow_up_was_requested = flow_manager.state.get(
        _ADVERSE_PARTIES_FOLLOW_UP_KEY, False
    )
    if should_request_follow_up and (
        not follow_up_was_requested or not optional_details_confirmed
    ):
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
    result = DomesticViolenceResult(
        status=Status.SUCCESS,
        is_experiencing=is_experiencing,
    )

    next_node = NodeConfig(node_record_household_composition())
    return result, next_node


async def record_household_composition(
    flow_manager: FlowManager,
    number_of_other_adults: int,
    number_of_children: int,
) -> tuple[IntakeFlowResult | dict[str, Any] | None, NodeConfig | None]:
    """Propose household counts; the caller is always added to the adult total."""
    if (
        not isinstance(number_of_other_adults, int)
        or isinstance(number_of_other_adults, bool)
        or number_of_other_adults < 0
    ):
        return (
            IntakeFlowResult(
                status=Status.ERROR,
                error="Please provide the number of adults other than the caller as zero or more.",
            ).model_dump(exclude_none=True, mode="json"),
            None,
        )

    number_of_adults = number_of_other_adults + 1
    is_valid, _ = await validator.check_household_composition(
        adults=number_of_adults, children=number_of_children
    )

    if not is_valid:
        result = IntakeFlowResult(
            status=Status.ERROR,
            error="Please provide valid numbers for the number of adults in your household, including yourself (at least 1), and children (0 or more).",
        )
        return result.model_dump(exclude_none=True, mode="json"), None

    _store_household_composition_pending(
        flow_manager,
        number_of_adults=number_of_adults,
        number_of_children=number_of_children,
    )
    return None, NodeConfig(
        node_confirm_household_composition(number_of_adults, number_of_children)
    )


@convert_and_log_result("household_composition")
async def confirm_household_composition(
    flow_manager: FlowManager, confirmed: bool
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    pending = _household_composition_pending(flow_manager)
    if pending is None:
        return (
            IntakeFlowResult(
                status=Status.ERROR,
                error="There are no pending household counts to confirm.",
            ),
            None,
        )

    if not confirmed:
        _clear_household_composition_pending(flow_manager)
        return None, NodeConfig(node_record_household_composition())

    _clear_household_composition_pending(flow_manager)
    result = HouseholdCompositionResult(
        status=Status.SUCCESS,
        number_of_adults=pending["number_of_adults"],
        number_of_children=pending["number_of_children"],
    )
    if pending["number_of_adults"] == 1 and pending["number_of_children"] == 0:
        only_member = HouseholdMembers.model_validate(
            [
                {
                    "name": _caller_full_name(flow_manager) or "Caller",
                    "relationship": "self",
                    "is_caller": True,
                }
            ]
        )
        flow_manager.state["household_members"] = {
            "members": only_member.model_dump(mode="json", exclude_none=True)
        }
        return result, NodeConfig(node_record_income())

    return result, NodeConfig(node_record_household_members())


@convert_and_log_result("household_members")
async def record_household_members(
    flow_manager: FlowManager, members: list[dict]
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    try:
        validated_members = HouseholdMembers.model_validate(members)
    except ValidationError as e:
        cleaned_error = clean_pydantic_error_message(e)
        log_pydantic_validation_error("household_members", e)
        return (
            IntakeFlowResult(
                status=Status.ERROR,
                error=f"There was an error validating the household members: {cleaned_error}.",
            ),
            None,
        )

    composition = flow_manager.state.get("household_composition", {})
    expected_count = composition.get("number_of_adults", 0) + composition.get(
        "number_of_children", 0
    )
    if len(validated_members.root) != expected_count:
        return (
            IntakeFlowResult(
                status=Status.ERROR,
                error=(
                    f"The confirmed household contains {expected_count} people, but "
                    f"{len(validated_members.root)} household members were provided. "
                    "Ask only for the missing or extra household member and confirm the full list."
                ),
            ),
            None,
        )

    caller_members = [member for member in validated_members.root if member.is_caller]
    if len(caller_members) != 1:
        return (
            IntakeFlowResult(
                status=Status.ERROR,
                error="Exactly one household member must be identified as the caller.",
            ),
            None,
        )

    caller_name = _caller_full_name(flow_manager)
    if caller_name and _normalize_person_name(
        caller_members[0].name
    ) != _normalize_person_name(caller_name):
        return (
            IntakeFlowResult(
                status=Status.ERROR,
                error=f"Use the caller's already confirmed name, {caller_name}, for the self household member.",
            ),
            None,
        )

    known_adverse_names = _known_adverse_party_names(flow_manager)
    for member in validated_members.root:
        if (
            member.adverse_party_name
            and _normalize_person_name(member.adverse_party_name)
            not in known_adverse_names
        ):
            return (
                IntakeFlowResult(
                    status=Status.ERROR,
                    error=(
                        f"{member.adverse_party_name} does not match a previously recorded adverse party. "
                        "Confirm the identity or omit adverse_party_name."
                    ),
                ),
                None,
            )

    return (
        HouseholdMembersResult(status=Status.SUCCESS, members=validated_members),
        NodeConfig(node_record_income()),
    )


@convert_and_log_result("income")
async def record_income(
    flow_manager: FlowManager, income: dict[str, dict[str, object]]
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    try:
        normalized_member_list = _normalize_member_list_income(income)
        if normalized_member_list is not None:
            income = normalized_member_list
        elif "members" in income:
            return (
                IntakeFlowResult(
                    status=Status.ERROR,
                    error=(
                        "Household membership is already confirmed; do not ask about it again. "
                        "The income argument must map each household member's name directly to "
                        "income-category keys, and each category to an amount and period."
                    ),
                ),
                None,
            )

        household_members_state = flow_manager.state.get("household_members", {})
        members = (
            household_members_state.get("members", [])
            if isinstance(household_members_state, dict)
            else []
        )
        if members:
            expected_names = {
                _normalize_person_name(member["name"])
                for member in members
                if isinstance(member, dict) and isinstance(member.get("name"), str)
            }
            submitted_name_counts: dict[str, int] = {}
            for name in income:
                if isinstance(name, str):
                    normalized_name = _normalize_person_name(name)
                    submitted_name_counts[normalized_name] = (
                        submitted_name_counts.get(normalized_name, 0) + 1
                    )
            submitted_names = set(submitted_name_counts)
            duplicate_names = sorted(
                name for name, count in submitted_name_counts.items() if count > 1
            )
            if duplicate_names:
                return (
                    IntakeFlowResult(
                        status=Status.ERROR,
                        error=(
                            "Provide one income entry per household member; duplicate "
                            f"names were provided: {', '.join(duplicate_names)}."
                        ),
                    ),
                    None,
                )
            if submitted_names != expected_names:
                missing_names = sorted(expected_names - submitted_names)
                extra_names = sorted(submitted_names - expected_names)
                details = []
                if missing_names:
                    details.append(f"missing: {', '.join(missing_names)}")
                if extra_names:
                    details.append(f"not in the household: {', '.join(extra_names)}")
                return (
                    IntakeFlowResult(
                        status=Status.ERROR,
                        error=(
                            "Household membership is already confirmed; do not ask about it again. "
                            "Provide an income or No Household Income entry for every confirmed "
                            f"household member ({'; '.join(details)})."
                        ),
                    ),
                    None,
                )

        income_validated = HouseholdIncome.model_validate(income)
        household_composition = flow_manager.state.get("household_composition") or {}
        adults = household_composition.get("number_of_adults", 0)
        children = household_composition.get("number_of_children", 0)
        household_size = adults + children
        is_eligible, income_monthly, household_size = await validator.check_income(
            income=income_validated, household_size=household_size
        )
    except ValidationError as e:
        cleaned_error = clean_pydantic_error_message(e)
        log_pydantic_validation_error("income", e)
        result = IntakeFlowResult(
            status=Status.ERROR,
            error=f"""There was an error validating the `income`: {cleaned_error}.""",
        )
        return result, None

    result = IncomeResult(
        status=Status.SUCCESS,
        is_eligible=is_eligible,
        monthly_amount=income_monthly,
        listing=income_validated,
        household_size=household_size,
    )
    if is_eligible:
        next_node = NodeConfig(node_record_assets_receives_benefits())
    else:
        result.error = "Over the household income limit"
        next_node = NodeConfig(node_confirm_income_over_limit())
    return result, next_node


@convert_and_log_result("assets")
async def record_assets_receives_benefits(
    flow_manager: FlowManager, receives_benefits: bool
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    categories = _reported_income_categories(flow_manager)
    receives_benefits = receives_benefits or bool(
        {
            "ssi",
            "ssi (supplemental security income)",
            "ssi/ssdi combo",
            "tanf",
            "tanf (temporary assistance for needy families)",
        }
        & categories
    )
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
        cleaned_error = clean_pydantic_error_message(e)
        log_pydantic_validation_error("assets", e)
        result = IntakeFlowResult(
            status=Status.ERROR,
            error=f"Error validating assets: {cleaned_error}.",
        )
        return result, None
    except ValueError:
        logger.debug("Validation failed for assets: value_error")
        result = IntakeFlowResult(
            status=Status.ERROR,
            error="Error validating assets.",
        )
        return result, None

    result = AssetsResult(
        status=Status.SUCCESS,
        is_eligible=is_eligible,
        listing=assets_validated,
        total_value=assets_value,
        receives_benefits=False,
    )
    IntakeValidator.assets_clear_partial_state(flow_manager.state)
    if is_eligible:
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
    try:
        existing_names = []
        if "names" in flow_manager.state and "names" in flow_manager.state["names"]:
            existing_names = flow_manager.state["names"]["names"]
        all_names = existing_names + names
        names_validated = CallerNames.model_validate(all_names)
    except ValidationError as e:
        cleaned_error = clean_pydantic_error_message(e)
        log_pydantic_validation_error("names", e)
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
    street_2: str | None = None,
    city: str = "",
    state: str = "",
    zip: str = "",
    county: str = "",
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
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
        cleaned_error = clean_pydantic_error_message(e)
        log_pydantic_validation_error("address", e)
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


def _intake_node_builders() -> dict[str, Callable[[], NodeConfig]]:
    return {
        "record_name": node_record_name,
        "record_service_area": node_record_service_area,
        "record_case_type": node_record_case_type,
        "record_adverse_parties": node_record_adverse_parties,
        "record_domestic_violence": node_record_domestic_violence,
        "record_household_composition": node_record_household_composition,
        "record_household_members": node_record_household_members,
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


async def continue_intake(
    flow_manager: FlowManager, next_step: str
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    try:
        next_function = getattr(sys.modules[__name__], next_step)
    except AttributeError:
        raise ValueError(f"""Function '{next_step}' does not exist.""")

    deterministic_builder = _intake_node_builders().get(next_step)
    if deterministic_builder is not None:
        return None, NodeConfig(deterministic_builder())

    next_node = NodeConfig(
        cast(
            Any,
            node_partial_reset_with_state()
            | {
                **prompts.get(next_step),
                "functions": [next_function],
            },
        )
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
    sms_accepted = False
    if normalized_method == "text":
        sms_result = await _send_referral_sms(flow_manager, REFERRAL)
        sms_accepted = sms_result.get("accepted", False)
    if normalized_method == "text" and not sms_accepted:
        return None, _node_referral_and_end(flow_manager, REFERRAL, "phone")
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
    return None, node_end_conversation(_caller_language(flow_manager))


def node_end_conversation(language: str = "English") -> NodeConfig:
    return _build_static_tts_node(
        prompts.get_spoken_prompt("end_goodbye", language),
        post_actions=[{"type": "end_conversation"}],
    )


async def caller_ended_conversation(
    flow_manager: FlowManager,
) -> tuple[IntakeFlowResult | None, NodeConfig | None]:
    return None, node_caller_ended_conversation(_caller_language(flow_manager))


def node_caller_ended_conversation(language: str = "English") -> NodeConfig:
    return _build_static_tts_node(
        prompts.get_spoken_prompt("end_goodbye", language),
        post_actions=[{"type": "end_conversation"}],
    )
