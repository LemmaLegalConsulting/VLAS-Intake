#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from dotenv import load_dotenv
from openai import AsyncAzureOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pipecat.services.azure.llm import AzureLLMService

CLIENT_PYTHON_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CLIENT_PYTHON_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(CLIENT_PYTHON_DIR))

from scenarios import SCRIPTS_FILE, build_caller_system_prompt, load_scripts
from test_manager import TestRunner

from intake_bot.nodes.nodes import NodeDependencies
from intake_bot.nodes.validator import IntakeValidator
from intake_bot.services.dialpad import SMS
from intake_bot.testing.text_pipeline import TextSession, TextTurnResult

DEFAULT_CALLER_MODEL = "gpt-4.1-mini"
DEFAULT_MAX_TURNS = 80
DEFAULT_TEXT_FLUSH_TIMEOUT_SECS = 30.0
DEFAULT_CALLER_RETRY_ATTEMPTS = 4
DEFAULT_SCENARIO_ATTEMPTS = 3
DEFAULT_RESULTS_FILE = PROJECT_ROOT / "logs" / "text_client_results.json"
DEFAULT_STATE_FILE = PROJECT_ROOT / "logs" / "text_flow_manager_state.json"


class Caller(Protocol):
    async def reply(self, assistant_text: Sequence[str]) -> str: ...


class AzureCaller:
    """Generate the automated caller's next text reply with Azure OpenAI."""

    def __init__(
        self,
        *,
        client: AsyncAzureOpenAI,
        model: str,
        system_prompt: str,
    ):
        self.client = client
        self.model = model
        self.messages: list[ChatCompletionMessageParam] = [
            cast(
                ChatCompletionMessageParam, {"role": "system", "content": system_prompt}
            )
        ]

    @staticmethod
    def _is_assistant_echo(reply: str, assistant_text: str) -> bool:
        def normalize(value: str) -> str:
            return " ".join(value.lower().strip(" .!?").split())

        return normalize(reply) == normalize(assistant_text)

    @staticmethod
    def _is_bot_like_reply(reply: str) -> bool:
        normalized = " ".join(reply.lower().split())
        if "?" in reply:
            return True
        return any(
            phrase in normalized
            for phrase in (
                "please provide",
                "welcome to virginia legal aid society",
                "you have already chosen",
                "i can only help with the intake process",
                "let me continue",
                "i'll continue",
                "i will continue",
                "next question",
                "next step",
                "move on to the next",
                "move to the next",
                "i have recorded that",
            )
        )

    async def reply(self, assistant_text: Sequence[str]) -> str:
        content = "\n".join(text.strip() for text in assistant_text if text.strip())
        if not content:
            raise RuntimeError("The intake bot produced no text for the caller")

        self.messages.append(
            cast(ChatCompletionMessageParam, {"role": "assistant", "content": content})
        )
        for attempt in range(DEFAULT_CALLER_RETRY_ATTEMPTS):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=list(self.messages),
                temperature=0.0,
            )
            reply = (response.choices[0].message.content or "").strip()
            if not reply:
                raise RuntimeError("The Azure caller model returned an empty reply")
            if not self._is_assistant_echo(
                reply, content
            ) and not self._is_bot_like_reply(reply):
                self.messages.append(
                    cast(ChatCompletionMessageParam, {"role": "user", "content": reply})
                )
                return reply
            if attempt + 1 < DEFAULT_CALLER_RETRY_ATTEMPTS:
                self.messages.append(
                    cast(
                        ChatCompletionMessageParam,
                        {
                            "role": "developer",
                            "content": (
                                "The previous output was not a caller reply. Do not "
                                "repeat or ask the intake bot anything. Answer the "
                                "assistant's latest message directly using the "
                                "scenario facts, in one short caller response. "
                                "Follow any exact response instruction in the "
                                "scenario."
                            ),
                        },
                    )
                )

        raise RuntimeError("The Azure caller model did not produce a caller reply")


@dataclass
class RecordingSMS:
    """Record referral SMS requests without making an external request."""

    accepted: bool = False
    is_configured: bool = True
    messages: list[dict[str, str]] = field(default_factory=list)

    async def send(self, phone_number: str, message: str) -> dict[str, Any]:
        self.messages.append({"phone_number": phone_number, "message": message})
        return {"status": "simulated", "accepted": self.accepted}


@dataclass(frozen=True)
class TextScenarioResult:
    script_name: str
    call_id: str
    turn_count: int
    state: dict[str, Any]
    assistant_transcript: tuple[str, ...]


async def run_text_scenario(
    script_name: str,
    *,
    bot_llm: Any,
    caller: Caller,
    call_id: str,
    caller_phone_number: str,
    node_dependencies: NodeDependencies | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    session_factory: Callable[..., TextSession] = TextSession,
) -> TextScenarioResult:
    """Run one scripts.yml scenario through the real text intake pipeline."""
    if max_turns < 1:
        raise ValueError("max_turns must be at least 1")

    transcript: list[str] = []
    async with session_factory(
        llm=bot_llm,
        node_dependencies=node_dependencies,
        call_id=call_id,
        caller_phone_number=caller_phone_number,
        flush_timeout_secs=DEFAULT_TEXT_FLUSH_TIMEOUT_SECS,
    ) as session:
        turn: TextTurnResult = await session.start()
        turn_count = 0
        while True:
            transcript.extend(turn.assistant_text)
            if session.finished:
                break
            caller_text = await caller.reply(turn.assistant_text)
            if not caller_text.strip():
                raise RuntimeError("The caller model returned an empty reply")
            turn = await session.send_user_turn(caller_text)
            turn_count += 1

            if session.finished:
                transcript.extend(turn.assistant_text)
                break
            if turn_count >= max_turns:
                raise RuntimeError(
                    f"Scenario '{script_name}' exceeded max_turns={max_turns}"
                )

    return TextScenarioResult(
        script_name=script_name,
        call_id=call_id,
        turn_count=turn_count,
        state=turn.state,
        assistant_transcript=tuple(transcript),
    )


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"The {name} environment variable must be set")
    return value


def _new_call_id(script_name: str) -> str:
    return f"text-test-{script_name}-{uuid.uuid4().hex[:10]}"


def _scenario_phone_number(script_config: dict[str, Any], fallback: str) -> str:
    expected_state = script_config.get("expected_state", {})
    phone = expected_state.get("phone", {}) if isinstance(expected_state, dict) else {}
    configured = phone.get("phone_number") if isinstance(phone, dict) else None
    return str(configured).strip() if configured else fallback


def _azure_endpoint() -> str:
    endpoint = os.getenv("AZURE_LLM_ENDPOINT") or os.getenv("AZURE_CHATGPT_ENDPOINT")
    if not endpoint:
        raise ValueError("AZURE_LLM_ENDPOINT or AZURE_CHATGPT_ENDPOINT must be set")
    return endpoint


def _build_azure_client() -> AsyncAzureOpenAI:
    api_version = (
        os.getenv("AZURE_LLM_API_VERSION")
        or os.getenv("AZURE_OPENAI_API_VERSION")
        or "2024-09-01-preview"
    )
    return AsyncAzureOpenAI(
        api_key=_required_env("AZURE_API_KEY"),
        azure_endpoint=_azure_endpoint(),
        api_version=api_version,
    )


def _write_state_snapshot(
    state_file: Path, call_id: str, state: dict[str, Any]
) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    snapshots: dict[str, Any] = {}
    if state_file.exists():
        try:
            snapshots = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            snapshots = {}
    snapshots[call_id] = state
    state_file.write_text(
        json.dumps(snapshots, indent=2, ensure_ascii=True), encoding="utf-8"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run scripts.yml scenarios against the intake bot text pipeline"
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--script", help="Run one named scripts.yml scenario")
    selection.add_argument("--all", action="store_true", help="Run all scenarios")
    parser.add_argument(
        "--caller-model",
        default=None,
        help=f"Azure caller deployment (default: {DEFAULT_CALLER_MODEL})",
    )
    parser.add_argument(
        "--bot-model",
        default=None,
        help="Override the intake bot Azure deployment",
    )
    parser.add_argument(
        "--phone",
        default="8665345243",
        help="Caller phone number (default: 8665345243)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_TURNS,
        help=f"Maximum caller turns per scenario (default: {DEFAULT_MAX_TURNS})",
    )
    parser.add_argument(
        "--scripts-file",
        type=Path,
        default=SCRIPTS_FILE,
        help="Path to scripts.yml",
    )
    parser.add_argument(
        "--results-file",
        type=Path,
        default=DEFAULT_RESULTS_FILE,
        help="TestManager result output path",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="Text FlowManager state output path",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip expected-state comparison",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Continue running scenarios after one fails",
    )
    parser.add_argument(
        "--scenario-attempts",
        type=int,
        default=DEFAULT_SCENARIO_ATTEMPTS,
        help=(
            "Attempts per scenario to tolerate transient caller-model or "
            "network failures (default: 3)"
        ),
    )
    return parser


async def _run_one_from_cli(
    args: argparse.Namespace,
    scripts: dict[str, Any],
    script_name: str,
) -> bool:
    if script_name not in scripts:
        raise ValueError(f"Script '{script_name}' not found in scripts.yml")
    script_config = scripts[script_name]
    if not isinstance(script_config, dict):
        raise TypeError(f"Script '{script_name}' has invalid format")

    caller_model = args.caller_model or os.getenv(
        "AZURE_CALLER_MODEL", DEFAULT_CALLER_MODEL
    )
    bot_model = args.bot_model or _required_env("AZURE_LLM_MODEL")
    azure_client = _build_azure_client()
    caller = AzureCaller(
        client=azure_client,
        model=caller_model,
        system_prompt=build_caller_system_prompt(script_name, scripts, text_mode=True),
    )
    bot_llm = AzureLLMService(
        api_key=_required_env("AZURE_API_KEY"),
        endpoint=_azure_endpoint(),
        settings=AzureLLMService.Settings(model=bot_model),
    )
    node_dependencies = NodeDependencies(
        validator=IntakeValidator(),
        sms_service=cast(SMS, RecordingSMS()),
    )
    call_id = _new_call_id(script_name)
    result = await run_text_scenario(
        script_name,
        bot_llm=bot_llm,
        caller=caller,
        call_id=call_id,
        caller_phone_number=_scenario_phone_number(script_config, args.phone),
        node_dependencies=node_dependencies,
        max_turns=args.max_turns,
    )
    _write_state_snapshot(args.state_file, call_id, result.state)

    if args.no_validate:
        print(f"PASS {script_name} ({result.turn_count} turns; validation skipped)")
        return True

    test_runner = TestRunner(
        results_file=args.results_file,
        flow_manager_state_file=args.state_file,
        scripts_file=args.scripts_file,
    )
    passed, mismatches = await test_runner.validate_state(
        call_id=call_id,
        script_name=script_name,
        actual_state=result.state,
        expected_state=script_config.get("expected_state"),
    )
    status = "PASS" if passed else "FAIL"
    print(f"{status} {script_name} ({result.turn_count} turns)")
    for mismatch in mismatches[:5]:
        print(f"  {mismatch.get('path', 'root')}: {mismatch.get('issue', 'unknown')}")
    return passed


async def _run(args: argparse.Namespace) -> int:
    load_dotenv(override=True)
    if args.scenario_attempts < 1:
        raise ValueError("--scenario-attempts must be at least 1")
    scripts = load_scripts(args.scripts_file)
    script_names = [args.script] if args.script else list(scripts)
    failures = 0

    for script_name in script_names:
        scenario_passed = False
        for attempt in range(args.scenario_attempts):
            try:
                scenario_passed = await _run_one_from_cli(args, scripts, script_name)
            except Exception as exc:  # noqa: BLE001 - isolate scenario failures
                print(
                    f"ERROR {script_name} attempt {attempt + 1}/"
                    f"{args.scenario_attempts}: {type(exc).__name__}: {exc}"
                )
            if scenario_passed:
                break
            if attempt + 1 < args.scenario_attempts:
                print(f"RETRY {script_name} ({attempt + 1}/{args.scenario_attempts})")

        if not scenario_passed:
            failures += 1
            if not args.continue_on_failure:
                break

    return 1 if failures else 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(build_parser().parse_args())))


if __name__ == "__main__":
    main()
