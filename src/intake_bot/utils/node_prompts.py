import random
from copy import deepcopy
from pathlib import Path

import yaml
from intake_bot.utils.globals import DATA_DIR


class NodePrompts:
    ACKNOWLEDGMENT_PHRASES = {
        "confirmation": {
            "english": (
                "Thanks for confirming",
                "Okay",
                "All right",
            ),
            "spanish": (
                "Gracias por confirmarlo",
                "Bueno",
                "De acuerdo",
            ),
        },
        "information": {
            "english": (
                "Thanks for sharing that",
                "Thank you",
                "All right",
            ),
            "spanish": (
                "Gracias por compartir eso",
                "Gracias",
                "De acuerdo",
            ),
        },
        "neutral": {
            "english": (
                "Okay",
                "All right",
            ),
            "spanish": (
                "Bueno",
                "De acuerdo",
            ),
        },
    }

    ACKNOWLEDGMENT_PREFIX = (
        "[Acknowledgment]\n"
        "Before asking the next question or giving the next instruction, begin with one brief, natural acknowledgment that fits the caller's immediately preceding answer. "
        "Choose the acknowledgment based on what the caller just did in the current turn, not on earlier conversation history. "
        "Whenever possible, weave the acknowledgment directly into the next question or instruction instead of making it a separate sentence. "
        'Prefer connected phrasing with a comma, such as "Thanks for confirming, what type of phone number is this?" or "Okay, please tell me the city or county where the legal incident occurred." '
        'If the caller briefly confirmed something, prefer acknowledgments like "Thanks for confirming," "Okay," or "All right," '
        'If the caller provided new factual information, prefer acknowledgments like "Thanks for sharing that," "Thank you,", or "Okay," '
        'If the caller corrected, clarified, or spelled something, prefer acknowledgments like "Thanks for clarifying," "Thanks for spelling that," or "All right," '
        "Use exactly one short acknowledgment lead-in before continuing. "
        "Do not stack an acknowledgment sentence and then a separate next-question sentence when a single connected sentence will sound more natural. "
        "Do not add extra praise, filler, or multiple acknowledgments in a row.\n\n"
    )

    ACKNOWLEDGMENT_EXCLUDED_KEYS = {
        "primary_role_message",
        "initial",
        "record_language",
        "record_phone_number",
        "record_phone_type",
        "record_name",
        "complete_intake",
        "end",
        "caller_ended_conversation",
    }

    def __init__(self, default_role: str = "system"):
        path = Path(DATA_DIR) / "node_prompts.yml"
        self.prompts = self._load_prompts(path, default_role)

    def _load_prompts(self, path: Path, default_role: str) -> dict:
        with open(path) as f:
            prompts: dict = yaml.safe_load(f)
        for key, value in prompts.items():
            if isinstance(value, dict):
                value["name"] = key
                prompts[key] = self._add_default_role(value, default_role)
        return prompts

    def _add_default_role(self, data: dict, default_role: str) -> dict:
        for key, value in data.items():
            if isinstance(value, list):
                for item in value:
                    if (
                        isinstance(item, dict)
                        and "content" in item
                        and "role" not in item
                    ):
                        item["role"] = default_role
            elif isinstance(value, dict):
                data[key] = self._add_default_role(value, default_role)
        return data

    def get(self, key: str, **kwargs) -> dict:
        """
        Retrieve a prompt by key and optionally format its task_message["content"].

        Args:
            key (str): The key of the prompt to retrieve.
            **kwargs: Formatting arguments for task_message["content"].

        Returns:
            dict: The formatted prompt.
        """
        if key not in self.prompts:
            raise KeyError(f"""Prompt '{key}' not found.""")

        prompt = deepcopy(self.prompts[key])

        if kwargs:
            self._format_text_fields(prompt, **kwargs)

        if "task_messages" in prompt:
            if self._should_prepend_acknowledgment(key):
                for task_message in prompt["task_messages"]:
                    if "content" in task_message:
                        task_message["content"] = (
                            self.ACKNOWLEDGMENT_PREFIX + task_message["content"]
                        )
        return prompt

    def _format_text_fields(self, data, **kwargs):
        if isinstance(data, dict):
            for key, value in data.items():
                if key in {"content", "text"} and isinstance(value, str):
                    data[key] = value.format(**kwargs)
                elif isinstance(value, (dict, list)):
                    self._format_text_fields(value, **kwargs)
        elif isinstance(data, list):
            for item in data:
                self._format_text_fields(item, **kwargs)

    def get_spoken_prompt(
        self,
        key: str,
        language: str | None = None,
        **kwargs,
    ) -> str:
        spoken_prompts = self.prompts.get("spoken_prompts", {})
        if key not in spoken_prompts:
            raise KeyError(f"""Spoken prompt '{key}' not found.""")

        prompt = spoken_prompts[key]
        if isinstance(prompt, str):
            template = prompt
        elif isinstance(prompt, dict):
            normalized_language = (
                "spanish"
                if (language or "").strip().lower() == "spanish"
                else "english"
            )
            if normalized_language not in prompt:
                raise KeyError(
                    f"""Spoken prompt '{key}' does not define language '{normalized_language}'."""
                )
            template = prompt[normalized_language]
        else:
            raise TypeError(
                f"""Spoken prompt '{key}' must be a string or mapping, got {type(prompt).__name__}."""
            )

        return template.format(**kwargs) if kwargs else template

    def _should_prepend_acknowledgment(self, key: str) -> bool:
        return key not in self.ACKNOWLEDGMENT_EXCLUDED_KEYS

    def get_acknowledgment_phrase(
        self, category: str, language: str = "english"
    ) -> str:
        normalized_language = (
            "spanish" if language.strip().lower() == "spanish" else "english"
        )
        phrases = self.ACKNOWLEDGMENT_PHRASES.get(
            category,
            self.ACKNOWLEDGMENT_PHRASES["neutral"],
        )
        return random.choice(phrases[normalized_language])


if __name__ == "__main__":
    from pprint import pprint

    prompts = NodePrompts()
    # pprint(prompts)
    # pprint(prompts.get("collect_service_area"))
    # pprint(prompts.get("confirm_service_area", match="Amelia County"))
    # pprint(prompts.get("initial"))
    # pprint(prompts.get("primary_role_message"))
    pprint(prompts.get("primary_role_message") | prompts.get("initial"))
