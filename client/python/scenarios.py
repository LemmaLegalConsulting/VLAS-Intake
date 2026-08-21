from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SCRIPTS_FILE = Path(__file__).parent / "scripts.yml"

AUTOMATED_CALLER_SYSTEM_PROMPT = """This is an automated intake test caller.
Your job is to behave like a cooperative human caller while preserving the scenario facts exactly.

Core rules:
- Never change the spelling of names, streets, cities, counties, or other proper nouns from the scenario.
- Never invent phonetic spellings, STT-style errors, or alternate spellings unless the scenario explicitly says the value is different.
- If you are unsure, repeat the exact scenario wording verbatim instead of paraphrasing or guessing.
- If asked for a phone type, answer with the exact scenario phone type using one short phrase.
- If the assistant reads back or confirms a name, address, or other value that sounds close to the correct scenario value, confirm it briefly even if the spelling differs slightly. Do not attempt to correct minor differences caused by speech recognition.
- When asked to spell something, spell it using the exact canonical letters from the scenario.
- Answer only the question that was asked. Do not volunteer extra facts unless the question requires them.
- Never combine the answer to the current question with facts from a different intake step.
- Do not repeat previously answered facts unless the assistant is explicitly confirming or re-asking them.
- Do not turn answers into questions.
- The intake bot speaks first in every turn. Treat each assistant message as the bot's question or statement, then answer as the caller.
- Your reply must never ask the intake bot a question or supply the bot's next prompt.
- Keep responses short, direct, and natural for voice.
- For numbers, dates, SSN digits, phone numbers, addresses, and money amounts, preserve the exact scenario values.
- Never drop or substitute parts of a person's legal name. Keep first, middle, and last names exactly as given in the scenario.
- If asked about household income, include every person in the scenario who has income.
- Attribute each income source to the person who actually receives it. Child support paid for a child still belongs to the adult who receives it unless the scenario says otherwise.
- A minor child can still have income. If asked whether a minor is an adult, say no while preserving that child's income.
- Only provide alternate names that the scenario explicitly says should be included in the legal file.
- For asset questions, follow the assistant's scope exactly. Do not volunteer exempt assets when the assistant is asking only about countable assets.
- In Spanish, answer naturally in Spanish, but keep proper nouns and factual values exactly aligned with the scenario.
"""


def load_scripts(path: str | Path = SCRIPTS_FILE) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as file_handle:
        scripts = yaml.safe_load(file_handle) or {}
    if not isinstance(scripts, dict):
        raise TypeError(f"Scripts file must contain a mapping: {path}")
    return scripts


def build_caller_system_prompt(
    script: str,
    scripts: dict[str, Any] | None = None,
    *,
    text_mode: bool = False,
) -> str:
    loaded_scripts = scripts if scripts is not None else load_scripts()
    script_config = loaded_scripts.get(script)
    if not isinstance(script_config, dict):
        raise KeyError(f"Script '{script}' not found or has invalid format")
    scenario_prompt = script_config.get("system_prompt")
    if not isinstance(scenario_prompt, str) or not scenario_prompt.strip():
        raise ValueError(f"Script '{script}' does not define a system_prompt")
    prompt = f"{AUTOMATED_CALLER_SYSTEM_PROMPT}\n\nScenario:\n{scenario_prompt}"
    if text_mode:
        prompt += (
            "\n\nText-mode rule:\n"
            "- Respond with only the caller's next spoken reply.\n"
            "- Never repeat the assistant's question as your reply; answer it "
            "using the scenario facts.\n"
            "- Do not include reasoning, stage directions, labels, or metadata."
        )
    return prompt


def build_client_system_prompt(
    script: str,
    scripts: dict[str, Any] | None = None,
) -> str:
    return build_caller_system_prompt(script, scripts)
