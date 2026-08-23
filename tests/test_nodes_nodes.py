from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from pipecat.flows import ContextStrategy
from pipecat.frames.frames import (
    ManuallySwitchServiceFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSSettings
from pipecat.services.deepgram.tts import DeepgramTTSSettings
from pipecat.transcriptions.language import Language

from intake_bot.models.classifier import ClassificationResponse
from intake_bot.models.intake_flow_result import Status
from intake_bot.models.validator import Assets, HouseholdIncome
from intake_bot.nodes.nodes import (
    _charge_retry_or_refer,
    _clear_service_area_pending,
    _household_composition_pending,
    _is_affirmative,
    _is_negative,
    _reset_retry_count,
    _service_area_pending,
    _store_service_area_pending,
    caller_ended_conversation,
    confirm_household_composition,
    continue_intake,
    end_conversation,
    node_caller_ended_conversation,
    node_end_conversation,
    node_record_language,
    node_start,
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
    record_household_members,
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
from intake_bot.nodes.validator import IntakeValidator
from intake_bot.services.dialpad import REFERRAL
from intake_bot.utils.node_prompts import NodePrompts

ACKNOWLEDGMENT_BY_LANGUAGE = {
    "english": "Okay",
    "spanish": "De acuerdo",
}


def _normalized_language(language: str | None) -> str:
    return "spanish" if (language or "").strip().lower() == "spanish" else "english"


def _with_acknowledgment(text: str, acknowledgment: str | None = None) -> str:
    if not acknowledgment:
        return text

    if text.startswith("I "):
        normalized = text
    else:
        normalized = text
        for index, char in enumerate(text):
            if char.isalpha():
                normalized = text[:index] + char.lower() + text[index + 1 :]
                break

    return f"{acknowledgment}, {normalized}"


async def _assert_spoken_next_node(
    next_node,
    flow_manager,
    prompt_loader,
    prompt_key: str,
    *,
    language: str = "English",
    acknowledgment: str | None = None,
    **kwargs,
):
    assert next_node["respond_immediately"] is False
    prompt_action = next_node["pre_actions"][0]

    await prompt_action["handler"](prompt_action, flow_manager)

    queued_frames = [
        call.args[0] for call in flow_manager.worker.queue_frame.await_args_list
    ]
    assert isinstance(queued_frames[-1], TTSSpeakFrame)
    assert queued_frames[-1].text == _with_acknowledgment(
        prompt_loader.get_spoken_prompt(prompt_key, language, **kwargs),
        acknowledgment,
    )


@pytest.fixture
def flow_manager():
    fm = MagicMock()
    fm.state = {}
    fm.worker.queue_frame = AsyncMock()
    fm.worker.flush_pipeline = AsyncMock(return_value=True)
    fm._tts_services = {
        Language.EN: MagicMock(name="english_tts"),
        Language.ES: MagicMock(name="spanish_tts"),
    }
    return fm


@pytest.fixture(scope="module")
def prompt_loader():
    return NodePrompts()


@pytest.fixture(autouse=True)
def patch_validator(monkeypatch):
    validator_mock = MagicMock()
    monkeypatch.setattr("intake_bot.nodes.nodes.validator", validator_mock)
    return validator_mock


@pytest.fixture(autouse=True)
def patch_prompts(monkeypatch, prompt_loader):
    prompts_mock = MagicMock()
    prompts_mock.get.side_effect = lambda k, **kwargs: {f"""{k}_prompt""": True}
    prompts_mock.get_acknowledgment_phrase.side_effect = (
        lambda category, language="english": ACKNOWLEDGMENT_BY_LANGUAGE[
            _normalized_language(language)
        ]
    )
    prompts_mock.get_spoken_prompt.side_effect = prompt_loader.get_spoken_prompt
    monkeypatch.setattr("intake_bot.nodes.nodes.prompts", prompts_mock)
    return prompts_mock


@pytest.mark.asyncio
async def test_system_phone_number_with_phone(flow_manager, patch_validator):
    flow_manager.state["phone"] = "+18665345243"
    patch_validator.check_phone_number = AsyncMock(return_value=(True, "+18665345243"))
    result, next_node = await system_phone_number(flow_manager)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["phone_number"] == "+18665345243"
    assert "record_language_prompt" in next_node
    assert next_node["respond_immediately"] is False
    assert next_node["pre_actions"][0]["type"] == "function"


@pytest.mark.asyncio
async def test_system_phone_number_without_phone(flow_manager, patch_validator):
    patch_validator.check_phone_number = AsyncMock(return_value=(False, ""))
    result, next_node = await system_phone_number(flow_manager)
    assert isinstance(result, dict)
    assert result["status"] == Status.ERROR
    assert "record_language_prompt" in next_node
    assert next_node["respond_immediately"] is False


@pytest.mark.asyncio
async def test_system_phone_number_persists_e164(flow_manager, patch_validator):
    """Valid national caller ID is persisted as canonical E.164 in state."""
    flow_manager.state["phone"] = "(866) 534-5243"
    patch_validator.check_phone_number = AsyncMock(return_value=(True, "+18665345243"))
    result, _next_node = await system_phone_number(flow_manager)
    assert result["phone_number"] == "+18665345243"
    assert flow_manager.state["phone"]["phone_number"] == "+18665345243"


@pytest.mark.asyncio
async def test_system_phone_number_keeps_existing_e164(flow_manager, patch_validator):
    """Already-E.164 caller ID remains canonical in state."""
    flow_manager.state["phone"] = "+18665345243"
    patch_validator.check_phone_number = AsyncMock(return_value=(True, "+18665345243"))
    await system_phone_number(flow_manager)
    assert flow_manager.state["phone"]["phone_number"] == "+18665345243"


@pytest.mark.asyncio
async def test_system_phone_number_invalid_preserves_prior_valid(
    flow_manager, patch_validator
):
    """Invalid caller ID does not overwrite an existing valid stored E.164."""
    patch_validator.check_phone_number = AsyncMock(return_value=(True, "+18665345243"))
    flow_manager.state["phone"] = "not-a-number"
    await system_phone_number(flow_manager)
    assert flow_manager.state["phone"]["phone_number"] == "+18665345243"


@pytest.mark.asyncio
async def test_system_phone_number_invalid_no_prior_does_not_write(
    flow_manager, patch_validator
):
    """Invalid caller ID with no prior valid state leaves state unchanged."""
    patch_validator.check_phone_number = AsyncMock(return_value=(False, ""))
    flow_manager.state["phone"] = "garbage"
    await system_phone_number(flow_manager)
    assert flow_manager.state["phone"] == "garbage"


@pytest.mark.asyncio
async def test_system_phone_number_queues_bilingual_language_prompt(
    flow_manager, patch_validator, prompt_loader
):
    patch_validator.check_phone_number = AsyncMock(return_value=(False, ""))

    with patch(
        "intake_bot.nodes.nodes.get_deepgram_tts_voices",
        side_effect=["voice-en", "voice-es", "voice-en"],
    ):
        _, next_node = await system_phone_number(flow_manager)
        prompt_action = next_node["pre_actions"][0]

        await prompt_action["handler"](prompt_action, flow_manager)

    queued_frames = [
        call.args[0] for call in flow_manager.worker.queue_frame.await_args_list
    ]

    assert len(queued_frames) == 8
    assert isinstance(queued_frames[0], TTSUpdateSettingsFrame)
    assert isinstance(queued_frames[0].delta, DeepgramFluxTTSSettings)
    assert queued_frames[0].delta.voice == "voice-en"
    assert queued_frames[0].service is flow_manager._tts_services[Language.EN]
    assert isinstance(queued_frames[1], ManuallySwitchServiceFrame)
    assert queued_frames[1].service is flow_manager._tts_services[Language.EN]
    assert isinstance(queued_frames[2], TTSSpeakFrame)
    assert queued_frames[2].text == prompt_loader.get_spoken_prompt(
        "record_language_prompt_english"
    )
    assert isinstance(queued_frames[3], TTSUpdateSettingsFrame)
    assert isinstance(queued_frames[3].delta, DeepgramTTSSettings)
    assert queued_frames[3].delta.voice == "voice-es"
    assert queued_frames[3].service is flow_manager._tts_services[Language.ES]
    assert isinstance(queued_frames[4], ManuallySwitchServiceFrame)
    assert queued_frames[4].service is flow_manager._tts_services[Language.ES]
    assert isinstance(queued_frames[5], TTSSpeakFrame)
    assert queued_frames[5].text == prompt_loader.get_spoken_prompt(
        "record_language_prompt_spanish"
    )
    assert isinstance(queued_frames[6], TTSUpdateSettingsFrame)
    assert isinstance(queued_frames[6].delta, DeepgramFluxTTSSettings)
    assert queued_frames[6].delta.voice == "voice-en"
    assert queued_frames[6].service is flow_manager._tts_services[Language.EN]
    assert isinstance(queued_frames[7], ManuallySwitchServiceFrame)
    assert queued_frames[7].service is flow_manager._tts_services[Language.EN]
    assert (
        prompt_loader.get_spoken_prompt("record_language_prompt_english")
        == "Please say English or Spanish to choose your preferred language."
    )
    assert (
        prompt_loader.get_spoken_prompt("record_language_prompt_spanish")
        == "Por favor, diga inglés o español para elegir su idioma preferido."
    )


@pytest.mark.asyncio
async def test_initial_language_prompt_drains_each_utterance_before_switching(
    flow_manager,
):
    events = []

    async def record_queue(frame):
        events.append(("queue", frame))

    async def record_flush():
        events.append(("flush",))
        return True

    flow_manager.worker.queue_frame = AsyncMock(side_effect=record_queue)
    flow_manager.worker.flush_pipeline = AsyncMock(side_effect=record_flush)
    action = node_record_language(include_initial_greeting=True)["pre_actions"][0]

    with patch(
        "intake_bot.nodes.nodes.get_deepgram_tts_voices",
        side_effect=["voice-en", "voice-es", "voice-en"],
    ):
        await action["handler"](action, flow_manager)

    tts_positions = [
        index
        for index, event in enumerate(events)
        if event[0] == "queue" and isinstance(event[1], TTSSpeakFrame)
    ]
    assert len(tts_positions) == 3
    assert all(events[index + 1][0] == "flush" for index in tts_positions)


@pytest.mark.asyncio
async def test_initial_language_prompt_stops_when_caller_starts_speaking(
    flow_manager, prompt_loader
):
    flow_manager.state["_user_turn_started_count"] = 0
    flush_count = 0

    async def flush_and_interrupt():
        nonlocal flush_count
        flush_count += 1
        if flush_count == 1:
            flow_manager.state["_user_turn_started_count"] += 1
        return True

    flow_manager.worker.flush_pipeline = AsyncMock(side_effect=flush_and_interrupt)
    action = node_record_language(include_initial_greeting=True)["pre_actions"][0]

    with patch(
        "intake_bot.nodes.nodes.get_deepgram_tts_voices",
        side_effect=["voice-en", "voice-es", "voice-en"],
    ):
        await action["handler"](action, flow_manager)

    spoken_texts = [
        call.args[0].text
        for call in flow_manager.worker.queue_frame.await_args_list
        if isinstance(call.args[0], TTSSpeakFrame)
    ]
    assert spoken_texts == [prompt_loader.get_spoken_prompt("initial_greeting")]
    assert (
        flow_manager.worker.queue_frame.await_args_list[-1].args[0].service
        is (flow_manager._tts_services[Language.EN])
    )


@pytest.mark.asyncio
async def test_node_record_language_can_include_initial_greeting(
    flow_manager, prompt_loader
):
    transcript_handler = MagicMock()
    transcript_handler.save_assistant_tts = AsyncMock()
    flow_manager.state["_transcript_handler"] = transcript_handler

    with patch(
        "intake_bot.nodes.nodes.get_deepgram_tts_voices",
        side_effect=["voice-en", "voice-es", "voice-en"],
    ):
        node = node_record_language(include_initial_greeting=True)
        prompt_action = node["pre_actions"][0]

        await prompt_action["handler"](prompt_action, flow_manager)

    queued_frames = [
        call.args[0] for call in flow_manager.worker.queue_frame.await_args_list
    ]

    assert isinstance(queued_frames[0], TTSSpeakFrame)
    assert queued_frames[0].text == prompt_loader.get_spoken_prompt("initial_greeting")
    assert isinstance(queued_frames[1], TTSUpdateSettingsFrame)
    assert isinstance(queued_frames[2], ManuallySwitchServiceFrame)
    assert isinstance(queued_frames[3], TTSSpeakFrame)
    assert queued_frames[3].text == prompt_loader.get_spoken_prompt(
        "record_language_prompt_english"
    )
    assert isinstance(queued_frames[4], TTSUpdateSettingsFrame)
    assert isinstance(queued_frames[5], ManuallySwitchServiceFrame)
    assert isinstance(queued_frames[6], TTSSpeakFrame)
    assert queued_frames[6].text == prompt_loader.get_spoken_prompt(
        "record_language_prompt_spanish"
    )
    assert isinstance(queued_frames[7], TTSUpdateSettingsFrame)
    assert isinstance(queued_frames[8], ManuallySwitchServiceFrame)
    transcript_handler.save_assistant_tts.assert_has_awaits(
        [
            call(prompt_loader.get_spoken_prompt("initial_greeting")),
            call(prompt_loader.get_spoken_prompt("record_language_prompt_english")),
            call(prompt_loader.get_spoken_prompt("record_language_prompt_spanish")),
        ]
    )


@pytest.mark.asyncio
async def test_record_language(flow_manager, prompt_loader):
    flow_manager.state["phone"] = "+18665345243"
    transcript_handler = MagicMock()
    transcript_handler.save_assistant_tts = AsyncMock()
    flow_manager.state["_transcript_handler"] = transcript_handler
    result, next_node = await record_language(flow_manager, "English")
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["language"]["language"] == "English"
    assert (
        flow_manager.worker.queue_frame.await_count == 3
    )  # STT update + TTS configuration + provider switch
    queued_frames = [
        call.args[0] for call in flow_manager.worker.queue_frame.await_args_list
    ]
    assert isinstance(queued_frames[1], TTSUpdateSettingsFrame)
    assert isinstance(queued_frames[1].delta, DeepgramFluxTTSSettings)
    assert queued_frames[1].service is flow_manager._tts_services[Language.EN]
    assert isinstance(queued_frames[2], ManuallySwitchServiceFrame)
    assert queued_frames[2].service is flow_manager._tts_services[Language.EN]
    assert "record_phone_number_prompt" in next_node
    assert next_node["respond_immediately"] is False

    prompt_action = next_node["pre_actions"][0]
    await prompt_action["handler"](prompt_action, flow_manager)

    queued_frames = [
        call.args[0] for call in flow_manager.worker.queue_frame.await_args_list
    ]
    assert isinstance(queued_frames[-1], TTSSpeakFrame)
    assert queued_frames[-1].text == _with_acknowledgment(
        prompt_loader.get_spoken_prompt(
            "record_phone_number_confirmation",
            "English",
            spoken_phone_number="eight six six, five three four, five two four three",
        ),
        ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )
    transcript_handler.save_assistant_tts.assert_awaited_once_with(
        queued_frames[-1].text
    )


@pytest.mark.asyncio
async def test_record_spanish_language_selects_aura_tts(flow_manager):
    flow_manager.state["phone"] = "+18665345243"

    result, _ = await record_language(flow_manager, "Spanish")

    assert result["status"] == Status.SUCCESS
    queued_frames = [
        call.args[0] for call in flow_manager.worker.queue_frame.await_args_list
    ]
    assert isinstance(queued_frames[1], TTSUpdateSettingsFrame)
    assert isinstance(queued_frames[1].delta, DeepgramTTSSettings)
    assert queued_frames[1].delta.voice == "aura-2-olivia-es"
    assert queued_frames[1].service is flow_manager._tts_services[Language.ES]
    assert isinstance(queued_frames[2], ManuallySwitchServiceFrame)
    assert queued_frames[2].service is flow_manager._tts_services[Language.ES]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer,expected_language",
    [
        ("English", "English"),
        ("inglés", "English"),
        ("ingles", "English"),
        ("Spanish", "Spanish"),
        ("español", "Spanish"),
        ("espanol", "Spanish"),
    ],
)
async def test_record_language_normalizes_explicit_language_names(
    flow_manager, answer, expected_language
):
    with patch("intake_bot.nodes.nodes.get_deepgram_tts_voices", return_value="voice"):
        result, next_node = await record_language(flow_manager, answer)

    assert result["status"] == Status.SUCCESS
    assert result["language"] == expected_language
    assert flow_manager.state["language"]["language"] == expected_language
    assert "record_phone_number_prompt" in next_node


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["yes", "yeah", "no", "si", "sí", "los"])
async def test_record_language_rejects_answers_without_language_name(
    flow_manager, answer
):
    result, next_node = await record_language(flow_manager, answer)

    assert result["status"] == Status.ERROR
    assert next_node is None
    assert "language" not in flow_manager.state
    flow_manager.worker.queue_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_phone_number_valid(flow_manager, patch_validator, prompt_loader):
    patch_validator.check_phone_number = AsyncMock(return_value=(True, "+18665345243"))
    result, next_node = await record_phone_number(flow_manager, "+18665345243")
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["phone"]["is_valid"] is True
    assert flow_manager.state["phone"]["phone_number"] == "+18665345243"
    assert "phone_type" not in flow_manager.state["phone"]
    assert "record_phone_type_prompt" in next_node
    assert next_node["context_strategy"].strategy == ContextStrategy.RESET
    assert next_node["respond_immediately"] is False

    prompt_action = next_node["pre_actions"][0]
    await prompt_action["handler"](prompt_action, flow_manager)

    queued_frames = [
        call.args[0] for call in flow_manager.worker.queue_frame.await_args_list
    ]
    assert isinstance(queued_frames[-1], TTSSpeakFrame)
    assert queued_frames[-1].text == _with_acknowledgment(
        prompt_loader.get_spoken_prompt("record_phone_type_question", "English"),
        ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


@pytest.mark.asyncio
async def test_record_phone_type_valid(flow_manager, prompt_loader):
    flow_manager.state["phone"] = {
        "is_valid": True,
        "phone_number": "+18665345243",
    }

    result, next_node = await record_phone_type(flow_manager, "mobile")

    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["phone"]["phone_type"] == "mobile"
    assert "record_name_prompt" in next_node
    assert next_node["respond_immediately"] is False

    prompt_action = next_node["pre_actions"][0]
    await prompt_action["handler"](prompt_action, flow_manager)

    queued_frames = [
        call.args[0] for call in flow_manager.worker.queue_frame.await_args_list
    ]
    assert isinstance(queued_frames[-1], TTSSpeakFrame)
    assert queued_frames[-1].text == _with_acknowledgment(
        prompt_loader.get_spoken_prompt("record_name_question", "English"),
        ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


@pytest.mark.asyncio
async def test_record_phone_type_normalizes_spanish_synonym(flow_manager):
    flow_manager.state["phone"] = {
        "is_valid": True,
        "phone_number": "+18665345243",
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
    # State should NOT be written on error (Phase 3 requirement)
    assert "phone" not in flow_manager.state
    assert next_node is None


@pytest.mark.asyncio
async def test_record_name_valid(flow_manager, prompt_loader):
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
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_service_area_question",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


@pytest.mark.asyncio
async def test_record_name_invalid(flow_manager):
    result, next_node = await record_name(flow_manager, "", "", "")
    assert result["status"] == Status.ERROR
    assert "validating the `name`" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_address_valid(flow_manager, prompt_loader):
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
    assert next_node["pre_actions"][0]["text"] == prompt_loader.get_spoken_prompt(
        "complete_intake_thanks", "English"
    )
    assert next_node["post_actions"] == [{"type": "end_conversation"}]
    assert next_node["respond_immediately"] is False
    assert "intake is complete" in next_node["pre_actions"][0]["text"].lower()
    assert next_node["pre_actions"][0]["text"].endswith("Goodbye.")


@pytest.mark.asyncio
async def test_record_address_valid_no_street_2(flow_manager, prompt_loader):
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
    assert next_node["pre_actions"][0]["text"] == prompt_loader.get_spoken_prompt(
        "complete_intake_thanks", "English"
    )
    assert next_node["post_actions"] == [{"type": "end_conversation"}]


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


def test_standard_prompts_load_nonempty_system_task_messages(prompt_loader):
    for prompt_name in (
        "record_address",
        "record_service_area",
        "record_adverse_parties",
    ):
        prompt = prompt_loader.get(prompt_name)
        assert prompt["task_messages"]
        assert all(
            task_message.get("role") == "system"
            for task_message in prompt["task_messages"]
            if "content" in task_message
        )
        assert all(
            task_message["content"].strip()
            for task_message in prompt["task_messages"]
            if "content" in task_message
        )


def test_context_aware_household_prompts_cover_transcript_regressions(prompt_loader):
    composition_content = prompt_loader.get("record_household_composition")[
        "task_messages"
    ][0]["content"]
    members_content = prompt_loader.get("record_household_members")["task_messages"][0][
        "content"
    ]
    income_content = prompt_loader.get("record_income")["task_messages"][0]["content"]
    case_content = prompt_loader.get("record_case_type")["task_messages"][0]["content"]

    assert "Besides you" in composition_content
    assert '"none," "no one," or "nobody"' in composition_content
    assert "known_members" in composition_content
    assert "zero other adults and zero children" in composition_content
    assert "Is your [relationship] [known name]" in members_content
    assert "Never silently assume" in members_content
    assert "Do not repeat any confirmed household names or counts" in members_content
    assert "ask for that person's name and relationship together" in members_content
    assert "Never use placeholders" in members_content
    assert "do not ask a generic category question" in case_content
    assert "Do not explain internal household exclusion rules" in income_content
    assert "Do not ask for them again" in income_content
    assert "whose `is_caller` field is true" in income_content
    assert "Do NOT speak the caller's name" in income_content
    assert 'use "you" for the caller' in income_content
    assert 'Do NOT use a top-level "members" list' in income_content
    assert "ask whether it is Social Security Retirement" in income_content
    assert (
        '{"No Household Income": {"amount": 0, "period": "Monthly"}}' in income_content
    )


def test_name_and_adverse_party_prompts_cover_transcript_regressions(prompt_loader):
    name_content = prompt_loader.get("record_name")["task_messages"][0]["content"]
    adverse_content = prompt_loader.get("record_adverse_parties")["task_messages"][0][
        "content"
    ]

    assert "Spell every supplied name part in that single confirmation" in name_content
    assert (
        "A letter-by-letter spelling supplied by the caller is authoritative"
        in name_content
    )
    assert 'never "I heard John, spelled J O N."' in name_content
    assert '"organization_name": "First National Bank"' in adverse_content
    assert (
        "NEVER ask for a date of birth or suffix for an organization" in adverse_content
    )


def test_initial_prompt_formats_tts_pre_action_text(prompt_loader):
    initial_greeting = prompt_loader.get_spoken_prompt("initial_greeting")
    prompt = prompt_loader.get(
        "initial",
        initial_greeting=initial_greeting,
    )

    assert prompt["pre_actions"][0]["text"] == initial_greeting


def test_node_start_defaults_to_language_selection(monkeypatch, patch_prompts):
    monkeypatch.delenv("TEST_INITIAL_NODE", raising=False)

    node = node_start()

    assert node["respond_immediately"] is False
    assert node["pre_actions"][0]["handler"]
    assert node["pre_actions"][0]["welcome_prompt_key"] == "initial_greeting"
    patch_prompts.get_spoken_prompt.assert_not_called()


def test_node_start_uses_configured_step_builder(monkeypatch):
    monkeypatch.setenv("TEST_INITIAL_NODE", "record_service_area")

    node = node_start()

    assert node["functions"] == [record_service_area]
    assert node["respond_immediately"] is False
    assert node["pre_actions"][0]["handler"]
    assert node["pre_actions"][0]["text_builder"]
    assert node["context_strategy"].strategy == ContextStrategy.RESET


def test_node_start_rejects_unknown_initial_node(monkeypatch):
    monkeypatch.setenv("TEST_INITIAL_NODE", "missing_node")

    with pytest.raises(ValueError, match="Initial node 'missing_node' does not exist."):
        node_start()


def test_standard_node_prompts_prepend_acknowledgment_instruction(prompt_loader):
    prompt = prompt_loader.get("record_service_area")
    content = prompt["task_messages"][0]["content"]

    assert content.startswith(NodePrompts.ACKNOWLEDGMENT_PREFIX)
    assert content.count(NodePrompts.ACKNOWLEDGMENT_PREFIX) == 1


def test_excluded_node_prompts_do_not_prepend_acknowledgment_instruction(prompt_loader):
    prompt = prompt_loader.get("record_language")
    content = prompt["task_messages"][0]["content"]

    assert not content.startswith(NodePrompts.ACKNOWLEDGMENT_PREFIX)

    converted_prompt = prompt_loader.get("record_name")
    converted_content = converted_prompt["task_messages"][0]["content"]

    assert not converted_content.startswith(NodePrompts.ACKNOWLEDGMENT_PREFIX)


@pytest.mark.asyncio
async def test_record_service_area_eligible(
    flow_manager, patch_validator, prompt_loader
):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "exact_match",
            "canonical_name": "Amelia County",
            "fips": 51007,
            "is_eligible": True,
            "candidates": [],
            "match_type": "canonical",
        }
    )
    result, next_node = await record_service_area(flow_manager, "Amelia County")
    assert isinstance(result, dict)
    assert result["is_eligible"] is True
    assert flow_manager.state["service_area"]["location"] == "Amelia County"
    assert result["fips_code"] == 51007
    assert "record_case_type_prompt" in next_node
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_case_type_question",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


@pytest.mark.asyncio
async def test_record_service_area_eligible_with_canonical_match(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "exact_match",
            "canonical_name": "Suffolk City",
            "fips": 51800,
            "is_eligible": True,
            "candidates": [],
            "match_type": "canonical",
        }
    )
    result, next_node = await record_service_area(flow_manager, "Suffolk City")
    assert isinstance(result, dict)
    assert result["is_eligible"] is True
    assert result["location"] == "Suffolk City"
    assert flow_manager.state["service_area"]["location"] == "Suffolk City"
    assert result["fips_code"] == 51800
    assert "record_case_type_prompt" in next_node


@pytest.mark.asyncio
async def test_record_service_area_unserved(flow_manager, patch_validator):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "unserved",
            "canonical_name": "Accomack County",
            "fips": 51001,
            "is_eligible": False,
            "candidates": [],
            "match_type": "canonical",
        }
    )
    result, next_node = await record_service_area(flow_manager, "Accomack")
    assert result["status"] == Status.SUCCESS
    assert result["is_eligible"] is False
    assert result["location"] == "Accomack County"
    assert "service_area_unserved_prompt" in next_node


@pytest.mark.asyncio
async def test_record_service_area_unknown(flow_manager, patch_validator):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "unknown",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": [],
            "match_type": None,
        }
    )
    result, next_node = await record_service_area(flow_manager, "Nowhere")
    assert "couldn't identify a Virginia city or county" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_service_area_suggested_then_yes_confirm(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        side_effect=[
            {
                "outcome": "suggested",
                "canonical_name": "Amelia County",
                "fips": 51007,
                "is_eligible": True,
                "candidates": ["Amelia County"],
                "match_type": "fuzzy",
            },
            {
                "outcome": "exact_match",
                "canonical_name": "Amelia County",
                "fips": 51007,
                "is_eligible": True,
                "candidates": [],
                "match_type": "canonical",
            },
        ]
    )
    fm = flow_manager
    result, next_node = await record_service_area(fm, "Amelia County")
    assert result["status"] == Status.ERROR
    assert result["outcome"] == "suggested"
    pending = _service_area_pending(fm)
    assert pending and "Amelia County" in pending.get("candidates", [])

    result, next_node = await record_service_area(fm, "Yes, that's right")
    assert result["status"] == Status.SUCCESS
    assert result["location"] == "Amelia County"
    assert "record_case_type_prompt" in next_node


@pytest.mark.asyncio
async def test_record_service_area_suggested_then_no_correction(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        side_effect=[
            {
                "outcome": "suggested",
                "canonical_name": "Franklin City",
                "fips": 51630,
                "is_eligible": True,
                "candidates": ["Franklin City"],
                "match_type": "fuzzy",
            },
            {
                "outcome": "exact_match",
                "canonical_name": "Amherst County",
                "fips": 51009,
                "is_eligible": True,
                "candidates": [],
                "match_type": "canonical",
            },
        ]
    )
    fm = flow_manager
    result, _next_node = await record_service_area(fm, "Franklin")
    assert result["status"] == Status.ERROR
    assert result["outcome"] == "suggested"

    result, _next_node = await record_service_area(fm, "Amherst County")
    assert result["status"] == Status.SUCCESS
    assert result["location"] == "Amherst County"
    assert _service_area_pending(fm) is None


@pytest.mark.asyncio
async def test_record_service_area_replaces_pending_suggestion_without_retry(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        side_effect=[
            {
                "outcome": "suggested",
                "canonical_name": "Amelia County",
                "fips": None,
                "is_eligible": None,
                "candidates": ["Amelia County"],
                "match_type": "bare_name",
            },
            {
                "outcome": "suggested",
                "canonical_name": "Danville City",
                "fips": None,
                "is_eligible": None,
                "candidates": ["Danville City"],
                "match_type": "bare_name",
            },
            {
                "outcome": "exact_match",
                "canonical_name": "Danville City",
                "fips": 51595,
                "is_eligible": True,
                "candidates": [],
                "match_type": "canonical",
            },
        ]
    )

    result, next_node = await record_service_area(flow_manager, "Amelia")
    assert result["outcome"] == "suggested"
    assert next_node is None

    result, next_node = await record_service_area(flow_manager, "No, Danville")
    assert result["outcome"] == "suggested"
    assert result["candidates"] == ["Danville City"]
    assert next_node is None
    assert _service_area_pending(flow_manager)["candidates"] == ["Danville City"]
    assert flow_manager.state.get("_service_area_unresolved_count") is None

    result, next_node = await record_service_area(flow_manager, "yes")
    assert result["status"] == Status.SUCCESS
    assert result["location"] == "Danville City"
    assert "record_case_type_prompt" in next_node


@pytest.mark.asyncio
async def test_new_suggestion_after_rejection_resets_unresolved_retry(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        side_effect=[
            {
                "outcome": "suggested",
                "canonical_name": "Danville City",
                "fips": None,
                "is_eligible": None,
                "candidates": ["Danville City"],
                "match_type": "bare_name",
            },
            {
                "outcome": "suggested",
                "canonical_name": "Amelia County",
                "fips": None,
                "is_eligible": None,
                "candidates": ["Amelia County"],
                "match_type": "bare_name",
            },
            {
                "outcome": "exact_match",
                "canonical_name": "Amelia County",
                "fips": 51007,
                "is_eligible": True,
                "candidates": [],
                "match_type": "canonical",
            },
        ]
    )

    result, _ = await record_service_area(flow_manager, "Danville")
    assert result["outcome"] == "suggested"

    result, next_node = await record_service_area(flow_manager, "no")
    assert result["status"] == Status.ERROR
    assert next_node is None
    assert flow_manager.state["_service_area_unresolved_count"] == 1

    result, next_node = await record_service_area(flow_manager, "Amelia")
    assert result["outcome"] == "suggested"
    assert next_node is None
    assert _service_area_pending(flow_manager)["candidates"] == ["Amelia County"]
    assert flow_manager.state.get("_service_area_unresolved_count") is None

    result, next_node = await record_service_area(flow_manager, "yes")
    assert result["status"] == Status.SUCCESS
    assert result["location"] == "Amelia County"
    assert "record_case_type_prompt" in next_node


@pytest.mark.asyncio
async def test_record_service_area_ambiguous_confirmation(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "ambiguous",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": ["Franklin City", "Franklin County"],
            "match_type": "fuzzy",
        }
    )
    fm = flow_manager
    result, _next_node = await record_service_area(fm, "Franklin")
    assert result["status"] == Status.ERROR
    assert result["outcome"] == "ambiguous"


@pytest.mark.asyncio
async def test_record_service_area_yes_with_multiple_candidates_reprompts_without_retry(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "ambiguous",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": ["Franklin City", "Franklin County"],
            "match_type": "fuzzy",
        }
    )

    result, _ = await record_service_area(flow_manager, "Franklin")
    assert result["status"] == Status.ERROR
    assert _service_area_pending(flow_manager)["candidates"] == [
        "Franklin City",
        "Franklin County",
    ]

    result, next_node = await record_service_area(flow_manager, "yes")

    assert result["status"] == Status.ERROR
    assert result["outcome"] == "ambiguous"
    assert "Franklin City" in result["error"]
    assert "Franklin County" in result["error"]
    assert next_node is None
    assert flow_manager.state.get("_service_area_unresolved_count", 0) == 0
    patch_validator.check_service_area.assert_awaited_once_with(location="Franklin")


@pytest.mark.asyncio
async def test_record_service_area_three_ambiguous_candidates_limits_pending(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "ambiguous",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": ["First County", "Second County", "Third County"],
            "match_type": "fuzzy",
        }
    )

    result, next_node = await record_service_area(flow_manager, "unclear place")

    assert result["status"] == Status.ERROR
    assert result["outcome"] == "ambiguous"
    assert result["candidates"] == [
        "First County",
        "Second County",
        "Third County",
    ]
    assert "First County" in result["error"]
    assert "Second County" in result["error"]
    assert "Third County" not in result["error"]
    assert next_node is None
    assert _service_area_pending(flow_manager)["candidates"] == [
        "First County",
        "Second County",
    ]


@pytest.mark.asyncio
async def test_record_service_area_unserved_out_of_state_routes_to_referral(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "unserved",
            "canonical_name": None,
            "fips": None,
            "is_eligible": False,
            "candidates": [],
            "match_type": None,
        }
    )
    fm = flow_manager
    result, next_node = await record_service_area(fm, "North Carolina")
    assert result["status"] == Status.SUCCESS
    assert result["is_eligible"] is False
    assert "service_area_unserved_prompt" in next_node


@pytest.mark.asyncio
async def test_record_service_area_invented_yes_not_confirmed(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "unknown",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": [],
            "match_type": None,
        }
    )
    fm = flow_manager
    _store_service_area_pending(fm, ["Invented County"])
    result, _next_node = await record_service_area(fm, "yes")
    assert result["status"] == Status.ERROR


@pytest.mark.asyncio
async def test_record_service_area_spanish_yes_confirms(flow_manager, patch_validator):
    patch_validator.check_service_area = AsyncMock(
        side_effect=[
            {
                "outcome": "suggested",
                "canonical_name": "Amelia County",
                "fips": 51007,
                "is_eligible": True,
                "candidates": ["Amelia County"],
                "match_type": "fuzzy",
            },
            {
                "outcome": "exact_match",
                "canonical_name": "Amelia County",
                "fips": 51007,
                "is_eligible": True,
                "candidates": [],
                "match_type": "canonical",
            },
        ]
    )
    fm = flow_manager
    result, _next_node = await record_service_area(fm, "amelia")
    assert result["outcome"] == "suggested"
    result, _next_node = await record_service_area(fm, "sí")
    assert result["status"] == Status.SUCCESS
    assert result["location"] == "Amelia County"


@pytest.mark.asyncio
async def test_record_service_area_spanish_no_then_retry(flow_manager, patch_validator):
    patch_validator.check_service_area = AsyncMock(
        side_effect=[
            {
                "outcome": "suggested",
                "canonical_name": "Amelia County",
                "fips": 51007,
                "is_eligible": True,
                "candidates": ["Amelia County"],
                "match_type": "fuzzy",
            },
            {
                "outcome": "unknown",
                "canonical_name": None,
                "fips": None,
                "is_eligible": None,
                "candidates": [],
                "match_type": None,
            },
        ]
    )
    fm = flow_manager
    result, next_node = await record_service_area(fm, "ameila")
    assert result["outcome"] == "suggested"
    result, next_node = await record_service_area(fm, "no")
    assert result["status"] == Status.ERROR
    assert next_node is None


@pytest.mark.asyncio
async def test_record_service_area_two_unresolved_then_referral(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "unresolved_service_area",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": [],
            "match_type": None,
        }
    )
    fm = flow_manager
    result, next_node = await record_service_area(fm, "asdf")
    assert result["status"] == Status.ERROR
    assert result["outcome"] == "unresolved_service_area"
    assert next_node is None

    result, next_node = await record_service_area(fm, "xyz")
    assert result["status"] == Status.SUCCESS
    assert result["outcome"] == "unresolved_service_area"
    assert result.get("is_eligible") is None
    assert "service_area_unresolved_prompt" in next_node


@pytest.mark.asyncio
async def test_record_service_area_preserves_valid_state_after_invalid_retry(
    flow_manager, patch_validator
):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "unknown",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": [],
            "match_type": None,
        }
    )
    fm = flow_manager
    flow_manager.state["service_area"] = {
        "location": "Amelia County",
        "fips_code": 51007,
        "is_eligible": True,
        "outcome": "exact_match",
    }
    result, next_node = await record_service_area(fm, "bzzt")
    assert next_node is None
    assert result["status"] == Status.ERROR
    assert fm.state["service_area"]["location"] == "Amelia County"


@pytest.mark.asyncio
async def test_is_affirmative_english_and_spanish():
    assert _is_affirmative("yes")
    assert _is_affirmative("sí")
    assert _is_affirmative("si")
    assert _is_affirmative("correcto")
    assert _is_affirmative("that's right")
    assert _is_affirmative("confirm")
    assert _is_affirmative("Yes, that's right")
    assert _is_affirmative("Yes sir")
    assert not _is_affirmative("Yes, but I live in Richmond")


@pytest.mark.asyncio
async def test_is_negative():
    assert _is_negative("no")
    assert _is_negative("nope")
    assert _is_negative("nah")
    assert not _is_negative("yes")
    assert not _is_negative("Amelia")
    assert not _is_negative("not sure")
    assert _is_negative("No, that's wrong")
    assert not _is_negative("No, I live in Richmond")


@pytest.mark.parametrize("text", ["incorrecto", "falso", "negativo"])
def test_is_negative_spanish(text):
    assert _is_negative(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("No, Amelia", "Amelia"),
        ("Incorrecto, Amelia", "Amelia"),
        ("Falso Amelia", "Amelia"),
        ("Negativo, Amelia", "Amelia"),
    ],
)
def test_strip_negative_prefix_supports_spanish(text, expected):
    from intake_bot.nodes.nodes import _strip_negative_prefix

    assert _strip_negative_prefix(text) == expected


@pytest.mark.asyncio
async def test_record_case_type_rejects_unknown_eligibility(
    flow_manager, patch_validator
):
    patch_validator.check_case_type = AsyncMock(
        return_value=ClassificationResponse(
            legal_problem_code=None,
            confidence=None,
            is_eligible=None,
            follow_up_questions=None,
        )
    )
    result, next_node = await record_case_type(flow_manager, "something")
    assert result["status"] == Status.ERROR
    assert "could not determine case-type eligibility" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_case_type_requires_valid_legal_problem_code(
    flow_manager, patch_validator
):
    patch_validator.check_case_type = AsyncMock(
        return_value=ClassificationResponse(
            legal_problem_code=None,
            confidence=None,
            is_eligible=None,
            follow_up_questions=[],
        )
    )
    result, next_node = await record_case_type(flow_manager, "legal problem")
    assert result["status"] == Status.ERROR
    assert "could not determine case-type eligibility" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_case_type_eligible(flow_manager, patch_validator, prompt_loader):
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
    assert "pre_actions" not in next_node
    assert "respond_immediately" not in next_node


@pytest.mark.asyncio
async def test_record_case_type_ineligible(
    flow_manager, patch_validator, prompt_loader
):
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

    assert result["status"] == Status.SUCCESS
    assert "Ineligible case type." in result["error"]
    assert result["is_eligible"] is False
    assert "case_type_ineligible_prompt" in next_node
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "case_type_ineligible_question",
    )


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
        REFERRAL.sms_text("English"),
    )
    assert flow_manager.state["sms_messages"][0].get("accepted") is True
    assert next_node["pre_actions"][0]["text"] == REFERRAL.text_delivery_text("English")
    assert next_node["task_messages"] == []
    assert next_node["post_actions"] == [{"type": "end_conversation"}]
    assert next_node["respond_immediately"] is False


@pytest.mark.asyncio
async def test_referral_sms_uses_stored_e164(flow_manager, monkeypatch):
    """The production referral path uses the stored E.164 value
    in the exact Dialpad send payload."""
    flow_manager.state["phone"] = {"phone_number": "+18665345243"}
    flow_manager.state["language"] = {"language": "English"}
    sms_mock = MagicMock()
    sms_mock.is_configured = True
    sms_mock.send = AsyncMock(return_value={"status": 200, "body": {"id": "1"}})
    monkeypatch.setattr("intake_bot.nodes.nodes.sms_service", sms_mock)

    result, _next_node = await send_general_referral_and_end(flow_manager, "text")

    assert result is None
    sms_mock.send.assert_awaited_once_with(
        "+18665345243",
        REFERRAL.sms_text("English"),
    )


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
    assert next_node["pre_actions"][0]["text"] == REFERRAL.phone_delivery_text(
        "Spanish"
    )
    assert next_node["task_messages"] == []


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


def test_case_type_ineligible_prompt_routes_to_referral_end_function_without_url(
    prompt_loader,
):
    prompt = prompt_loader.get("case_type_ineligible")
    content = prompt["task_messages"][0]["content"]

    assert "send_case_type_referral_and_end" in content
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
async def test_record_domestic_violence_true(flow_manager, prompt_loader):
    result, next_node = await record_domestic_violence(flow_manager, True)
    assert isinstance(result, dict)
    assert flow_manager.state["domestic_violence"]["is_experiencing"] is True
    assert "record_household_composition_prompt" in next_node
    assert "pre_actions" not in next_node
    assert "respond_immediately" not in next_node


@pytest.mark.asyncio
async def test_record_domestic_violence_false(flow_manager, prompt_loader):
    result, next_node = await record_domestic_violence(flow_manager, False)
    assert isinstance(result, dict)
    assert flow_manager.state["domestic_violence"]["is_experiencing"] is False
    assert "record_household_composition_prompt" in next_node
    assert "pre_actions" not in next_node
    assert "respond_immediately" not in next_node


@pytest.mark.asyncio
async def test_record_household_composition_valid(
    flow_manager, patch_validator, prompt_loader
):
    patch_validator.check_household_composition = AsyncMock(return_value=(True, 3))
    result, next_node = await record_household_composition(flow_manager, 0, 2)

    assert result is None
    assert "household_composition" not in flow_manager.state
    assert _household_composition_pending(flow_manager) == {
        "number_of_adults": 1,
        "number_of_children": 2,
    }
    patch_validator.check_household_composition.assert_awaited_once_with(
        adults=1, children=2
    )
    assert "confirm_household_composition_prompt" in next_node
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "confirm_household_composition_question",
        adult_count_phrase="one adult",
        child_count_phrase="2 children",
    )

    result, next_node = await confirm_household_composition(flow_manager, True)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["household_composition"] == {
        "number_of_adults": 1,
        "number_of_children": 2,
    }
    assert _household_composition_pending(flow_manager) is None
    assert "record_household_members_prompt" in next_node
    assert "pre_actions" not in next_node


@pytest.mark.asyncio
async def test_household_composition_preserves_known_members_and_skips_reask(
    flow_manager, patch_validator
):
    flow_manager.state["names"] = {
        "names": [{"first": "Celeste", "middle": "Caroline", "last": "Campbell"}]
    }
    patch_validator.check_household_composition = AsyncMock(return_value=(True, 3))

    await record_household_composition(
        flow_manager,
        number_of_other_adults=0,
        number_of_children=2,
        known_members=[
            {"name": "Celeste Caroline Campbell", "is_caller": True},
            {"name": "James Campbell", "relationship": "child"},
            {"name": "Thomas Campbell", "relationship": "child"},
        ],
    )

    _, next_node = await confirm_household_composition(flow_manager, True)

    assert "record_income_prompt" in next_node
    assert flow_manager.state["household_members"]["members"] == [
        {
            "name": "Celeste Caroline Campbell",
            "relationship": "self",
            "is_caller": True,
        },
        {"name": "James Campbell", "relationship": "child", "is_caller": False},
        {"name": "Thomas Campbell", "relationship": "child", "is_caller": False},
    ]


@pytest.mark.asyncio
async def test_household_composition_retains_known_name_when_relationship_missing(
    flow_manager, patch_validator
):
    flow_manager.state["names"] = {
        "names": [{"first": "Jon", "middle": "Patrick", "last": "Adamson"}]
    }
    patch_validator.check_household_composition = AsyncMock(return_value=(True, 2))

    await record_household_composition(
        flow_manager,
        number_of_other_adults=1,
        number_of_children=0,
        known_members=[{"name": "Sarah Marshall"}],
    )

    _, _next_node = await confirm_household_composition(flow_manager, True)

    assert flow_manager.state["household_members_known"] == {
        "members": [
            {"name": "Jon Patrick Adamson", "relationship": "self", "is_caller": True},
            {"name": "Sarah Marshall", "is_caller": False},
        ]
    }


@pytest.mark.asyncio
async def test_household_members_rejects_placeholder_names(flow_manager):
    flow_manager.state["household_composition"] = {
        "number_of_adults": 1,
        "number_of_children": 1,
    }

    result, next_node = await record_household_members(
        flow_manager,
        [
            {"name": "Jon Adamson", "relationship": "self", "is_caller": True},
            {"name": "Unknown Child 1", "relationship": "child"},
        ],
    )
    assert result["status"] == Status.ERROR
    assert "actual name" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_household_composition_only_adults(flow_manager, patch_validator):
    patch_validator.check_household_composition = AsyncMock(return_value=(True, 2))
    result, next_node = await record_household_composition(flow_manager, 1, 0)
    assert result is None
    assert _household_composition_pending(flow_manager) == {
        "number_of_adults": 2,
        "number_of_children": 0,
    }
    assert "confirm_household_composition_prompt" in next_node


@pytest.mark.asyncio
async def test_record_household_composition_invalid_negative_other_adults(
    flow_manager, patch_validator
):
    result, next_node = await record_household_composition(flow_manager, -1, 2)
    assert result["status"] == Status.ERROR
    assert next_node is None
    patch_validator.check_household_composition.assert_not_called()


@pytest.mark.asyncio
async def test_record_household_composition_invalid_negative_children(
    flow_manager, patch_validator
):
    patch_validator.check_household_composition = AsyncMock(return_value=(False, 0))
    result, next_node = await record_household_composition(flow_manager, 1, -1)
    assert result["status"] == Status.ERROR
    assert next_node is None


@pytest.mark.asyncio
async def test_household_confirmation_rejection_clears_pending(
    flow_manager, patch_validator
):
    patch_validator.check_household_composition = AsyncMock(return_value=(True, 1))
    await record_household_composition(flow_manager, 0, 0)

    result, next_node = await confirm_household_composition(flow_manager, False)

    assert result is None
    assert _household_composition_pending(flow_manager) is None
    assert "household_composition" not in flow_manager.state
    assert "record_household_composition_prompt" in next_node


@pytest.mark.asyncio
async def test_none_household_answer_becomes_caller_only(flow_manager, patch_validator):
    flow_manager.state["names"] = {
        "names": [{"first": "Jack", "last": "Adamson", "type": "Legal Name"}]
    }
    patch_validator.check_household_composition = AsyncMock(return_value=(True, 1))
    await record_household_composition(
        flow_manager,
        number_of_other_adults=0,
        number_of_children=0,
    )

    result, next_node = await confirm_household_composition(flow_manager, True)

    assert result["number_of_adults"] == 1
    assert result["number_of_children"] == 0
    assert flow_manager.state["household_members"] == {
        "members": [
            {
                "name": "Jack Adamson",
                "relationship": "self",
                "is_caller": True,
            }
        ]
    }
    assert "record_income_prompt" in next_node


@pytest.mark.asyncio
async def test_household_correction_replaces_pending_counts(
    flow_manager, patch_validator
):
    patch_validator.check_household_composition = AsyncMock(
        side_effect=[(True, 1), (True, 2)]
    )
    await record_household_composition(flow_manager, 0, 0)

    result, next_node = await record_household_composition(flow_manager, 1, 0)

    assert result is None
    assert _household_composition_pending(flow_manager) == {
        "number_of_adults": 2,
        "number_of_children": 0,
    }
    assert "confirm_household_composition_prompt" in next_node


@pytest.mark.asyncio
async def test_record_household_members_preserves_relationship_and_adverse_link(
    flow_manager,
):
    flow_manager.state.update(
        {
            "names": {
                "names": [{"first": "Jack", "last": "Adamson", "type": "Legal Name"}]
            },
            "adverse_parties": {
                "adverse_parties": [{"first": "Betty", "last": "Smith"}]
            },
            "household_composition": {
                "number_of_adults": 2,
                "number_of_children": 0,
            },
        }
    )

    result, next_node = await record_household_members(
        flow_manager,
        [
            {"name": "Jack Adamson", "relationship": "self", "is_caller": True},
            {
                "name": "Betty Smith",
                "relationship": "wife",
                "is_caller": False,
                "adverse_party_name": "Betty Smith",
            },
        ],
    )

    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["household_members"]["members"][1] == {
        "name": "Betty Smith",
        "relationship": "wife",
        "is_caller": False,
        "adverse_party_name": "Betty Smith",
    }
    assert "record_income_prompt" in next_node
    assert "pre_actions" not in next_node
    assert "respond_immediately" not in next_node


@pytest.mark.asyncio
async def test_record_household_members_rejects_unconfirmed_adverse_name(flow_manager):
    flow_manager.state.update(
        {
            "household_composition": {
                "number_of_adults": 1,
                "number_of_children": 0,
            },
            "adverse_parties": {
                "adverse_parties": [{"first": "Betty", "last": "Smith"}]
            },
        }
    )

    result, next_node = await record_household_members(
        flow_manager,
        [
            {
                "name": "Jack Adamson",
                "relationship": "self",
                "is_caller": True,
                "adverse_party_name": "Someone Else",
            }
        ],
    )

    assert result["status"] == Status.ERROR
    assert "does not match" in result["error"]
    assert next_node is None
    assert "household_members" not in flow_manager.state


@pytest.mark.asyncio
async def test_record_income_valid_eligible_with_dummy_model(
    flow_manager, patch_validator, prompt_loader
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
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_assets_receives_benefits_question",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


@pytest.mark.asyncio
async def test_record_income_requires_every_confirmed_household_member(
    flow_manager, patch_validator
):
    flow_manager.state.update(
        {
            "household_composition": {
                "number_of_adults": 2,
                "number_of_children": 0,
            },
            "household_members": {
                "members": [
                    {
                        "name": "Jack Adamson",
                        "relationship": "self",
                        "is_caller": True,
                    },
                    {
                        "name": "Betty Smith",
                        "relationship": "wife",
                        "is_caller": False,
                    },
                ]
            },
        }
    )

    result, next_node = await record_income(
        flow_manager,
        {"Jack Adamson": {"No Household Income": {"amount": 0, "period": "Monthly"}}},
    )

    assert result["status"] == Status.ERROR
    assert "betty smith" in result["error"]
    assert next_node is None
    assert "income" not in flow_manager.state
    patch_validator.check_income.assert_not_called()


@pytest.mark.asyncio
async def test_record_income_rejects_duplicate_normalized_member_names(
    flow_manager, patch_validator
):
    flow_manager.state.update(
        {
            "household_members": {
                "members": [
                    {
                        "name": "John Doe",
                        "relationship": "self",
                        "is_caller": True,
                    }
                ]
            },
            "household_composition": {
                "number_of_adults": 1,
                "number_of_children": 0,
            },
        }
    )

    result, next_node = await record_income(
        flow_manager,
        {
            "John Doe": {"Employment": {"amount": 500, "period": "Monthly"}},
            "JOHN DOE": {"Employment": {"amount": 500, "period": "Monthly"}},
        },
    )

    assert result["status"] == Status.ERROR
    assert "one income entry per household member" in result["error"]
    assert next_node is None
    patch_validator.check_income.assert_not_called()


@pytest.mark.asyncio
async def test_record_income_normalizes_member_list_envelope(
    flow_manager, patch_validator
):
    flow_manager.state.update(
        {
            "household_composition": {
                "number_of_adults": 1,
                "number_of_children": 0,
            },
            "household_members": {
                "members": [
                    {
                        "name": "Jon Patrick Adamson",
                        "relationship": "self",
                        "is_caller": True,
                    }
                ]
            },
        }
    )
    patch_validator.check_income = AsyncMock(return_value=(True, 700, 1))

    result, next_node = await record_income(
        flow_manager,
        {
            "members": [
                {
                    "name": "Jon Patrick Adamson",
                    "relationship": "self",
                    "is_caller": True,
                    "income": [
                        {
                            "category": "Social Security Retirement",
                            "amount": 500,
                            "period": "Monthly",
                        },
                        {
                            "category": "Food Stamps",
                            "amount": 200,
                            "period": "Monthly",
                        },
                    ],
                }
            ]
        },
    )

    assert result["status"] == Status.SUCCESS
    assert result["listing"] == {
        "Jon Patrick Adamson": {
            "Social Security Retirement": {"amount": 500.0, "period": "Monthly"},
            "Food Stamps": {"amount": 200.0, "period": "Monthly"},
        }
    }
    assert "record_assets_receives_benefits_prompt" in next_node


@pytest.mark.asyncio
async def test_record_income_structural_error_does_not_reopen_household(
    flow_manager, patch_validator
):
    result, next_node = await record_income(
        flow_manager,
        {"members": [{"name": "Jon Patrick Adamson", "income": "invalid"}]},
    )

    assert result["status"] == Status.ERROR
    assert "Household membership is already confirmed" in result["error"]
    assert "do not ask about it again" in result["error"]
    assert next_node is None
    patch_validator.check_income.assert_not_called()


@pytest.mark.asyncio
async def test_record_income_with_ssi_uses_medicaid_follow_up(
    flow_manager, patch_validator, prompt_loader
):
    patch_validator.check_income = AsyncMock(return_value=(True, 900, 1))
    flow_manager.state["household_composition"] = {
        "number_of_adults": 1,
        "number_of_children": 0,
    }
    with patch("intake_bot.nodes.nodes.HouseholdIncome", HouseholdIncome):
        income = {
            "Jane Doe": {
                "SSI": {
                    "amount": 900,
                    "period": "Monthly",
                }
            }
        }
        result, next_node = await record_income(flow_manager, income)

    assert result["status"] == Status.SUCCESS
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_assets_receives_benefits_question_ssi",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category,prompt_key",
    [
        (
            "SSI (Supplemental Security Income)",
            "record_assets_receives_benefits_question_ssi",
        ),
        ("SSI/SSDI combo", "record_assets_receives_benefits_question_ssi"),
        ("TANF", "record_assets_receives_benefits_question_tanf"),
        (
            "TANF (Temporary Assistance for Needy Families)",
            "record_assets_receives_benefits_question_tanf",
        ),
    ],
)
async def test_record_income_benefit_aliases_use_medicaid_follow_up(
    flow_manager, patch_validator, prompt_loader, category, prompt_key
):
    patch_validator.check_income = AsyncMock(return_value=(True, 500, 1))
    flow_manager.state["household_composition"] = {
        "number_of_adults": 1,
        "number_of_children": 0,
    }

    result, next_node = await record_income(
        flow_manager,
        {"Jane Doe": {category: {"amount": 500, "period": "Monthly"}}},
    )

    assert result["status"] == Status.SUCCESS
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        prompt_key,
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


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
async def test_record_income_valid_ineligible(
    flow_manager, patch_validator, prompt_loader
):
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
    assert result["status"] == Status.SUCCESS
    assert "Over the household income limit" in result["error"]
    assert "income" in flow_manager.state
    assert flow_manager.state["income"]["is_eligible"] is False
    assert "confirm_income_over_limit_prompt" in next_node
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "confirm_income_over_limit_question",
    )


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
        result, _next_node = await record_income(flow_manager, income)
    assert result["status"] == Status.SUCCESS
    listing = flow_manager.state["income"]["listing"]
    # Should collapse to a single member with a single "No Household Income" entry
    assert len(listing) == 1
    member_income = next(iter(listing.values()))
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
        result, _next_node = await record_income(flow_manager, income)
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
        result, _next_node = await record_income(flow_manager, income)
    assert result["status"] == Status.SUCCESS
    listing = flow_manager.state["income"]["listing"]
    # Children should be stripped, only parent remains
    assert len(listing) == 1
    assert "Jane Doe" in listing
    assert listing["Jane Doe"]["Employment"]["amount"] == 1200


@pytest.mark.asyncio
async def test_record_assets_receives_benefits_true(flow_manager, prompt_loader):
    result, next_node = await record_assets_receives_benefits(flow_manager, True)
    assert isinstance(result, dict)
    assert result["is_eligible"] is True
    assert flow_manager.state["assets"]["is_eligible"] is True
    assert flow_manager.state["assets"]["listing"] == []
    assert flow_manager.state["assets"]["total_value"] == 0
    assert flow_manager.state["assets"]["receives_benefits"] is True
    assert "record_citizenship_prompt" in next_node
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_citizenship_question",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


@pytest.mark.asyncio
async def test_record_assets_receives_benefits_false(flow_manager, prompt_loader):
    flow_manager.state["assets_cash_accounts"] = {"listing": [{"cash": 20}]}
    result, next_node = await record_assets_receives_benefits(flow_manager, False)
    assert result is None
    assert "assets_cash_accounts" not in flow_manager.state
    assert "record_assets_cash_accounts_prompt" in next_node
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_assets_cash_accounts_question",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["SSI", "TANF"])
async def test_known_income_benefit_survives_negative_medicaid_answer(
    flow_manager, category
):
    flow_manager.state["income"] = {
        "listing": {"Jane Doe": {category: {"amount": 500, "period": "Monthly"}}}
    }

    result, next_node = await record_assets_receives_benefits(flow_manager, False)

    assert result["status"] == Status.SUCCESS
    assert result["receives_benefits"] is True
    assert flow_manager.state["assets"]["receives_benefits"] is True
    assert "record_citizenship_prompt" in next_node


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
async def test_record_assets_other_property_routes_to_confirmation(
    flow_manager, prompt_loader
):
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
    current_assets_summary = IntakeValidator.assets_prompt_text(
        [{"savings account": 1200}, {"vacant land": 4000}]
    )
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_assets_list_confirmation",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
        current_assets_summary=current_assets_summary,
    )


@pytest.mark.asyncio
async def test_record_assets_list_valid_eligible(
    flow_manager, patch_validator, prompt_loader
):
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
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_citizenship_question",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


@pytest.mark.asyncio
async def test_record_assets_list_valid_ineligible(
    flow_manager, patch_validator, prompt_loader
):
    patch_validator.get_alternative_providers = AsyncMock(return_value="AltProvider")
    patch_validator.check_assets = AsyncMock(return_value=(False, 12000))
    with patch("intake_bot.nodes.nodes.Assets", Assets):
        assets = [{"savings": 7000}, {"vacant land": 5000}]
    result, next_node = await record_assets_list(flow_manager, assets)
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert "Over the household assets' value limit." in result["error"]
    assert "assets" in flow_manager.state
    assert flow_manager.state["assets"]["is_eligible"] is False
    assert "confirm_assets_over_limit_prompt" in next_node
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "confirm_assets_over_limit_question",
    )


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
async def test_record_citizenship(flow_manager, prompt_loader):
    result, next_node = await record_citizenship(
        flow_manager, True, answer_was_explicit=True
    )
    assert isinstance(result, dict)
    assert flow_manager.state["citizenship"]["is_citizen"] is True
    assert "record_ssn_last_4_prompt" in next_node
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_ssn_last_4_question",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


@pytest.mark.asyncio
async def test_record_citizenship_requires_explicit_answer(flow_manager):
    result, next_node = await record_citizenship(flow_manager, False)

    assert result["status"] == Status.ERROR
    assert "Citizenship can only be recorded" in result["error"]
    assert next_node is None


@pytest.mark.asyncio
async def test_record_date_of_birth_valid(flow_manager, patch_validator, prompt_loader):
    """Test record_date_of_birth with a valid date."""
    patch_validator.check_date_of_birth = AsyncMock(return_value=(True, "1980-01-15"))
    result, next_node = await record_date_of_birth(flow_manager, "01/15/1980")
    assert isinstance(result, dict)
    assert result["status"] == Status.SUCCESS
    assert result["date_of_birth"] == "1980-01-15"
    assert flow_manager.state["date_of_birth"]["date_of_birth"] == "1980-01-15"
    assert "record_names_prompt" in next_node
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_names_question",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


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
        result, _next_node = await record_date_of_birth(flow_manager, input_date)
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
    from datetime import UTC, datetime, timedelta

    patch_validator.check_date_of_birth = AsyncMock(return_value=(False, ""))
    future_date = (datetime.now(tz=UTC).astimezone() + timedelta(days=1)).strftime(
        "%m/%d/%Y"
    )
    result, next_node = await record_date_of_birth(flow_manager, future_date)
    assert result["status"] == Status.ERROR
    assert result["date_of_birth"] == ""
    assert next_node is None


@pytest.mark.asyncio
async def test_record_ssn_last_4_valid(flow_manager, patch_validator, prompt_loader):
    """Test record_ssn_last_4 with valid SSN last 4 digits."""
    patch_validator.check_ssn_last_4 = AsyncMock(return_value=(True, "1234"))
    result, next_node = await record_ssn_last_4(flow_manager, "1234")
    assert result["status"] == Status.SUCCESS
    assert result["ssn_last_4"] == "1234"
    assert flow_manager.state["ssn_last_4"]["ssn_last_4"] == "1234"
    assert "record_date_of_birth_prompt" in next_node
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_date_of_birth_question",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


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
async def test_record_names_with_prior_name(flow_manager, prompt_loader):
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
    await _assert_spoken_next_node(
        next_node,
        flow_manager,
        prompt_loader,
        "record_address_question",
        acknowledgment=ACKNOWLEDGMENT_BY_LANGUAGE["english"],
    )


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
    assert "a phone number, date of birth, or suffix" in first_result["error"]
    assert first_result["adverse_parties"][0]["first"] == "Dexter"
    assert first_next_node is None

    second_result, second_next_node = await record_adverse_parties(
        flow_manager, adverse_parties, optional_details_confirmed=True
    )

    assert isinstance(second_result, dict)
    assert second_result["status"] == Status.SUCCESS
    assert second_result["adverse_parties"][0]["first"] == "Dexter"
    assert "record_domestic_violence_prompt" in second_next_node


@pytest.mark.asyncio
async def test_record_adverse_party_organization_never_requests_person_details(
    flow_manager,
):
    adverse_parties = [{"organization_name": "First National Bank"}]

    first_result, first_next_node = await record_adverse_parties(
        flow_manager, adverse_parties
    )

    assert first_result["status"] == Status.ERROR
    assert "business phone number for First National Bank" in first_result["error"]
    assert (
        "Do not ask an organization for a date of birth or suffix"
        in first_result["error"]
    )
    assert first_next_node is None

    second_result, second_next_node = await record_adverse_parties(
        flow_manager, adverse_parties, optional_details_confirmed=True
    )

    assert second_result["status"] == Status.SUCCESS
    assert second_result["adverse_parties"] == [
        {"organization_name": "First National Bank"}
    ]
    assert "record_domestic_violence_prompt" in second_next_node


@pytest.mark.asyncio
async def test_adverse_party_first_call_cannot_skip_optional_detail_follow_up(
    flow_manager,
):
    result, next_node = await record_adverse_parties(
        flow_manager,
        [{"organization_name": "First National Bank"}],
        optional_details_confirmed=True,
    )

    assert result["status"] == Status.ERROR
    assert "business phone number for First National Bank" in result["error"]
    assert next_node is None


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
    assert result["adverse_parties"][0]["phones"][0]["number"] == "+18665345256"
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
async def test_end_conversation(flow_manager, prompt_loader):
    result, node = await end_conversation(flow_manager)
    assert result is None
    assert node["pre_actions"][0]["text"] == prompt_loader.get_spoken_prompt(
        "end_goodbye", "English"
    )
    assert node["post_actions"] == [{"type": "end_conversation"}]
    assert node["respond_immediately"] is False


@pytest.mark.asyncio
async def test_caller_ended_conversation(flow_manager, prompt_loader):
    flow_manager.state["language"] = {"language": "Spanish"}
    result, node = await caller_ended_conversation(flow_manager)
    assert result is None
    assert node["pre_actions"][0]["text"] == prompt_loader.get_spoken_prompt(
        "end_goodbye", "Spanish"
    )
    assert node["post_actions"] == [{"type": "end_conversation"}]
    assert node["respond_immediately"] is False


def test_node_end_conversation(prompt_loader):
    node = node_end_conversation()
    assert node["pre_actions"][0]["text"] == prompt_loader.get_spoken_prompt(
        "end_goodbye", "English"
    )
    assert "post_actions" in node


def test_node_caller_ended_conversation(prompt_loader):
    node = node_caller_ended_conversation("Spanish")
    assert node["pre_actions"][0]["text"] == prompt_loader.get_spoken_prompt(
        "end_goodbye", "Spanish"
    )
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
async def test_record_address_empty(flow_manager, prompt_loader):
    result, next_node = await record_address(
        flow_manager, street="", city="", state="", zip="", street_2="", county=""
    )

    assert result["status"] == Status.SUCCESS
    assert result.get("address") is None
    assert next_node is not None
    assert next_node["pre_actions"][0]["text"] == prompt_loader.get_spoken_prompt(
        "complete_intake_thanks", "English"
    )


@pytest.mark.asyncio
async def test_two_negative_confirmations_then_referral(flow_manager, patch_validator):
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "suggested",
            "canonical_name": "Amelia County",
            "fips": 51007,
            "is_eligible": True,
            "candidates": ["Amelia County"],
            "match_type": "fuzzy",
        }
    )
    fm = flow_manager
    # Initial suggestion — free
    result, nn = await record_service_area(fm, "amelia")
    assert result["status"] == Status.ERROR
    assert result["outcome"] == "suggested"

    # First "no" — charges retry 1, asks again
    result, nn = await record_service_area(fm, "no")
    assert result["status"] == Status.ERROR
    assert "repeat or spell" not in result.get("error", "")
    assert nn is None

    # Second "no" — retry exhausted → referral
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "unresolved_service_area",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": [],
            "match_type": None,
        }
    )
    result, nn = await record_service_area(fm, "no")
    assert result["status"] == Status.SUCCESS
    assert result["outcome"] == "unresolved_service_area"
    assert result.get("is_eligible") is None
    assert "service_area_unresolved_prompt" in nn
    # Retry counter should be reset after terminal referral
    assert fm.state.get("_service_area_unresolved_count") is None


@pytest.mark.asyncio
async def test_unclear_then_unknown_converges_to_referral(
    flow_manager, patch_validator
):
    """Unclear reply after suggestion + unknown outcome exhausts shared budget."""
    unknown = {
        "outcome": "unknown",
        "canonical_name": None,
        "fips": None,
        "is_eligible": None,
        "candidates": [],
        "match_type": None,
    }
    patch_validator.check_service_area = AsyncMock(
        side_effect=[
            # Call 1: suggested (free)
            {
                "outcome": "suggested",
                "canonical_name": "Amelia County",
                "fips": 51007,
                "is_eligible": True,
                "candidates": ["Amelia County"],
                "match_type": "fuzzy",
            },
            unknown,  # Call 2: "something unclear" → unknown → retry 1
            unknown,  # Call 3: "still unclear" → unknown → referral
        ]
    )
    fm = flow_manager
    result, nn = await record_service_area(fm, "amelia")
    assert result["outcome"] == "suggested"

    result, nn = await record_service_area(fm, "something unclear")
    assert result["status"] == Status.ERROR
    assert nn is None

    # Call 3: another unresolved → retry exhausted → referral
    result, nn = await record_service_area(fm, "still unclear")
    assert result["status"] == Status.SUCCESS
    assert result["outcome"] == "unresolved_service_area"
    assert "service_area_unresolved_prompt" in nn


@pytest.mark.asyncio
async def test_stale_pending_replaced_then_affirmation_fails(
    flow_manager, patch_validator
):
    """A stale pending suggestion must be cleared when user gives a new
    location; a subsequent 'yes' must NOT confirm the stale candidate."""
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "unresolved_service_area",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": [],
            "match_type": None,
        }
    )
    fm = flow_manager
    _store_service_area_pending(fm, ["Stale County"])
    # User says a completely new location
    result, nn = await record_service_area(fm, "Amelia County")
    # Stale cleared, fresh resolve → unresolved → charges retry 1
    assert result["status"] == Status.ERROR
    assert "service_area_unresolved_prompt" not in (nn or {})
    # Pending should be cleared (stale gone)
    assert _service_area_pending(fm) is None


@pytest.mark.asyncio
async def test_direct_franklin_county_converges_through_retry(
    flow_manager, patch_validator
):
    """Direct 'Franklin County' must not fuzzy-suggest Franklin City
    and must follow retry/referral behavior."""
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "unresolved_service_area",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": [],
            "match_type": None,
        }
    )
    fm = flow_manager
    result, nn = await record_service_area(fm, "Franklin County")
    assert result["outcome"] == "unresolved_service_area"
    assert result["status"] == Status.ERROR
    assert nn is None

    result, nn = await record_service_area(fm, "something else")
    assert result["status"] == Status.SUCCESS
    assert result["outcome"] == "unresolved_service_area"
    assert "service_area_unresolved_prompt" in nn


@pytest.mark.asyncio
async def test_service_area_result_explicit_nulls(flow_manager, patch_validator):
    """ServiceAreaResult payload must include explicit null keys for
    unresolved fields rather than omitting them."""
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "unresolved_service_area",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": [],
            "match_type": None,
        }
    )
    fm = flow_manager
    result, _nn = await record_service_area(fm, "Nowhere")
    assert result["status"] == Status.ERROR
    # Keys must exist with None values (not missing)
    assert "location" in result
    assert result["location"] is None
    assert "is_eligible" in result
    assert result["is_eligible"] is None
    assert "fips_code" in result
    assert result["fips_code"] is None
    # Candidates should be an empty list
    assert result["candidates"] == []


@pytest.mark.asyncio
async def test_charge_retry_or_refer_resets_on_second_failure(flow_manager):
    fm = flow_manager
    r1 = _charge_retry_or_refer(fm)
    assert r1 == 1
    assert fm.state.get("_service_area_unresolved_count") == 1
    r2 = _charge_retry_or_refer(fm)
    assert r2 is None
    # Counter should be reset after referral trigger
    assert fm.state.get("_service_area_unresolved_count") is None
    assert _service_area_pending(fm) is None


@pytest.mark.asyncio
async def test_clear_pending_does_not_reset_retry(flow_manager):
    fm = flow_manager
    fm.state["_service_area_unresolved_count"] = 1
    _store_service_area_pending(fm, ["Test"])
    _clear_service_area_pending(fm)
    # Pending cleared but retry count preserved
    assert _service_area_pending(fm) is None
    assert fm.state.get("_service_area_unresolved_count") == 1


@pytest.mark.asyncio
async def test_reset_retry_clears_count_only(flow_manager):
    fm = flow_manager
    fm.state["_service_area_unresolved_count"] = 2
    _store_service_area_pending(fm, ["Test"])
    _reset_retry_count(fm)
    assert fm.state.get("_service_area_unresolved_count") is None
    # Pending preserved
    assert _service_area_pending(fm) is not None


######################################################################
# State rollback regression tests (Phase 3)
######################################################################


@pytest.mark.asyncio
async def test_name_valid_then_invalid_preserves_state(flow_manager, prompt_loader):
    """A valid name followed by an invalid attempt preserves the valid state."""
    result, _next_node = await record_name(flow_manager, "John", "Q", "Public", "Jr.")
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["names"]["names"][0]["first"] == "John"

    result, _next_node = await record_name(flow_manager, "", "", "")
    assert result["status"] == Status.ERROR
    assert flow_manager.state["names"]["names"][0]["first"] == "John"


@pytest.mark.asyncio
async def test_phone_valid_then_invalid_preserves_state(flow_manager, patch_validator):
    """A valid phone number followed by an invalid attempt preserves the valid state."""
    patch_validator.check_phone_number = AsyncMock(return_value=(True, "+18665345243"))
    result, _next_node = await record_phone_number(flow_manager, "866-534-5243")
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["phone"]["phone_number"] == "+18665345243"

    patch_validator.check_phone_number = AsyncMock(return_value=(False, "bad"))
    result, _next_node = await record_phone_number(flow_manager, "bad")
    assert result["status"] == Status.ERROR
    # State should still have the valid phone
    assert flow_manager.state["phone"]["phone_number"] == "+18665345243"


@pytest.mark.asyncio
async def test_service_area_valid_then_invalid_preserves_state(
    flow_manager, patch_validator
):
    """A valid service area followed by an invalid attempt preserves the valid state."""
    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "exact_match",
            "canonical_name": "Amelia County",
            "fips": 51007,
            "is_eligible": True,
            "candidates": [],
            "match_type": "canonical",
        }
    )
    result, _next_node = await record_service_area(flow_manager, "Amelia County")
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["service_area"]["location"] == "Amelia County"

    patch_validator.check_service_area = AsyncMock(
        return_value={
            "outcome": "unknown",
            "canonical_name": None,
            "fips": None,
            "is_eligible": None,
            "candidates": [],
            "match_type": None,
        }
    )
    result, _next_node = await record_service_area(flow_manager, "bzzt")
    assert result["status"] == Status.ERROR
    assert flow_manager.state["service_area"]["location"] == "Amelia County"


@pytest.mark.asyncio
async def test_income_valid_then_invalid_preserves_state(flow_manager, patch_validator):
    """A valid income entry followed by an invalid attempt preserves the valid state."""
    from unittest.mock import patch

    patch_validator.check_income = AsyncMock(return_value=(True, 1000, 1))
    flow_manager.state["household_composition"] = {
        "number_of_adults": 1,
        "number_of_children": 0,
    }
    with patch("intake_bot.nodes.nodes.HouseholdIncome", HouseholdIncome):
        income = {
            "John Doe": {
                "Employment": {"amount": 1000, "period": "Monthly"},
            },
        }
        result, _next_node = await record_income(flow_manager, income)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["income"]["is_eligible"] is True

    # Now an invalid income attempt
    with patch("intake_bot.nodes.nodes.HouseholdIncome", HouseholdIncome):
        income = {"bad": "data"}
        result, _next_node = await record_income(flow_manager, income)
    assert result["status"] == Status.ERROR
    # Previous valid state should be preserved
    assert flow_manager.state["income"]["is_eligible"] is True


@pytest.mark.asyncio
async def test_assets_valid_then_invalid_preserves_state(flow_manager, patch_validator):
    """A valid assets entry followed by an invalid attempt preserves the valid state."""
    from unittest.mock import patch

    from intake_bot.models.validator import Assets

    patch_validator.check_assets = AsyncMock(return_value=(True, 7000))
    with patch("intake_bot.nodes.nodes.Assets", Assets):
        assets = [{"savings": 2000}, {"vacant land": 5000}]
        result, _next_node = await record_assets_list(flow_manager, assets)
    assert result["status"] == Status.SUCCESS
    assert flow_manager.state["assets"]["is_eligible"] is True

    # Now an invalid assets attempt
    with patch("intake_bot.nodes.nodes.Assets", Assets):
        assets = [{"bad": "data"}]
        result, _next_node = await record_assets_list(flow_manager, assets)
    assert result["status"] == Status.ERROR
    # Previous valid state should be preserved
    assert flow_manager.state["assets"]["is_eligible"] is True


@pytest.mark.asyncio
async def test_address_valid_then_invalid_preserves_state(flow_manager, prompt_loader):
    """A valid address followed by an invalid non-empty address preserves
    the exact prior valid state through the production decorated handler."""
    result, _next_node = await record_address(
        flow_manager,
        street="123 Main St",
        city="Richmond",
        state="VA",
        zip="23219",
        county="Richmond",
    )
    assert result["status"] == Status.SUCCESS
    prior = flow_manager.state["address"]["address"]
    assert prior["city"] == "Richmond"

    # Invalid non-empty address: street provided but city blank → validation fails
    result, _next_node = await record_address(
        flow_manager,
        street="456 Oak Ave",
        city="",
        state="VA",
        zip="",
        county="",
    )
    assert result["status"] == Status.ERROR
    # Exact prior valid state must survive
    assert flow_manager.state["address"]["address"]["city"] == "Richmond"
    assert flow_manager.state["address"]["address"]["street"] == "123 Main St"


@pytest.mark.asyncio
async def test_state_rollback_after_truthy_partial_mutation(flow_manager):
    """A handler that partially mutates state before failing must have its exact
    pre-call value restored (deep-copied, no aliasing)."""
    from intake_bot.models.intake_flow_result import IntakeFlowResult, Status
    from intake_bot.nodes.utils import convert_and_log_result

    flow_manager.state["test_key"] = {
        "original": "value",
        "nested": {"unchanged": True},
    }

    @convert_and_log_result("test_key")
    async def mutating_handler(flow_manager, **kwargs):
        # Partially mutate the state dict in-place
        flow_manager.state["test_key"]["mutated"] = True
        flow_manager.state["test_key"]["nested"]["unchanged"] = False
        return IntakeFlowResult(status=Status.ERROR, error="fail"), None

    _, next_node = await mutating_handler(flow_manager)
    assert next_node is None
    # State must be restored to the exact pre-call value
    assert flow_manager.state["test_key"] == {
        "original": "value",
        "nested": {"unchanged": True},
    }
    assert "mutated" not in flow_manager.state["test_key"]


@pytest.mark.asyncio
async def test_missing_key_stays_missing_on_error(flow_manager):
    """When state key does not exist and handler fails, key remains absent."""
    from intake_bot.models.intake_flow_result import IntakeFlowResult, Status
    from intake_bot.nodes.utils import convert_and_log_result

    assert "unknown_key" not in flow_manager.state

    @convert_and_log_result("unknown_key")
    async def failing_handler(flow_manager, **kwargs):
        return IntakeFlowResult(status=Status.ERROR, error="fail"), None

    _, next_node = await failing_handler(flow_manager)
    assert next_node is None
    assert "unknown_key" not in flow_manager.state


@pytest.mark.asyncio
async def test_none_value_preserved_on_error(flow_manager):
    """A state key with a None value is preserved on failure (not confused with missing)."""
    from intake_bot.models.intake_flow_result import IntakeFlowResult, Status
    from intake_bot.nodes.utils import convert_and_log_result

    flow_manager.state["null_key"] = None

    @convert_and_log_result("null_key")
    async def failing_handler(flow_manager, **kwargs):
        return IntakeFlowResult(status=Status.ERROR, error="fail"), None

    _, next_node = await failing_handler(flow_manager)
    assert next_node is None
    assert "null_key" in flow_manager.state
    assert flow_manager.state["null_key"] is None


@pytest.mark.asyncio
async def test_successful_result_writes_state(flow_manager):
    """A successful result must always write to state (generic state write preserved)."""
    from intake_bot.models.intake_flow_result import IntakeFlowResult, Status
    from intake_bot.nodes.utils import convert_and_log_result

    @convert_and_log_result("my_key")
    async def success_handler(flow_manager, **kwargs):
        return IntakeFlowResult(status=Status.SUCCESS, error=""), None

    _, _ = await success_handler(flow_manager)
    assert flow_manager.state["my_key"] == {}


@pytest.mark.asyncio
async def test_unrelated_key_mutation_not_reverted(flow_manager):
    """Partial mutation of an unrelated state key (not tracked by decorator)
    must NOT be reverted by the decorator."""
    from intake_bot.models.intake_flow_result import IntakeFlowResult, Status
    from intake_bot.nodes.utils import convert_and_log_result

    flow_manager.state["unrelated"] = {"safe": True}
    flow_manager.state["target"] = {"original": True}

    @convert_and_log_result("target")
    async def mutating_handler(flow_manager, **kwargs):
        flow_manager.state["target"]["mutated"] = True
        flow_manager.state["unrelated"]["safe"] = False
        return IntakeFlowResult(status=Status.ERROR, error="fail"), None

    _, _ = await mutating_handler(flow_manager)
    # target must be restored (tracked by decorator)
    assert flow_manager.state["target"] == {"original": True}
    # unrelated is NOT restored (not tracked by decorator)
    assert flow_manager.state["unrelated"] == {"safe": False}


@pytest.mark.asyncio
async def test_missing_key_created_then_error_gets_removed(flow_manager):
    """When state key does not exist, handler writes it, then returns ERROR,
    the key must be removed."""
    from intake_bot.models.intake_flow_result import IntakeFlowResult, Status
    from intake_bot.nodes.utils import convert_and_log_result

    @convert_and_log_result("new_key")
    async def create_then_error(flow_manager, **kwargs):
        flow_manager.state["new_key"] = {"truthy": True}
        return IntakeFlowResult(status=Status.ERROR, error="fail"), None

    await create_then_error(flow_manager)
    assert "new_key" not in flow_manager.state


@pytest.mark.asyncio
async def test_exception_restores_exact_nested_value(flow_manager):
    """An exception raised inside a decorated handler must restore the exact
    nested pre-call value AND propagate the exception."""
    from intake_bot.nodes.utils import convert_and_log_result

    flow_manager.state["nested_key"] = {"a": 1, "b": [2, 3]}

    @convert_and_log_result("nested_key")
    async def exception_handler(flow_manager, **kwargs):
        flow_manager.state["nested_key"]["a"] = 999
        flow_manager.state["nested_key"]["b"].append(4)
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await exception_handler(flow_manager)
    assert flow_manager.state["nested_key"] == {"a": 1, "b": [2, 3]}


@pytest.mark.asyncio
async def test_cancellation_restores_state_and_propagates(flow_manager):
    """A CancelledError inside a decorated handler must restore the exact
    pre-call value AND propagate the CancelledError."""
    import asyncio

    from intake_bot.nodes.utils import convert_and_log_result

    flow_manager.state["cancel_key"] = {"original": True}

    @convert_and_log_result("cancel_key")
    async def cancel_handler(flow_manager, **kwargs):
        flow_manager.state["cancel_key"]["mutated"] = True
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await cancel_handler(flow_manager)
    assert flow_manager.state["cancel_key"] == {"original": True}
