from copy import deepcopy
from pathlib import Path

import yaml
from intake_bot.utils.globals import DATA_DIR


class NodePrompts:
    ACKNOWLEDGMENT_PREFIX = (
        "[Acknowledgment]\n"
        "Before asking the next question or giving the next instruction, begin with one brief, natural acknowledgment that fits the caller's immediately preceding answer. "
        "Choose the acknowledgment based on what the caller just did in the current turn, not on earlier conversation history. "
        "Whenever possible, weave the acknowledgment directly into the next question or instruction instead of making it a separate sentence. "
        "Prefer connected phrasing with a comma, such as \"Thanks for confirming, what type of phone number is this?\" or \"Okay, please tell me the city or county where the legal incident occurred.\" "
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

        if "task_messages" in prompt:
            for task_message in prompt["task_messages"]:
                if "content" in task_message and kwargs:
                    task_message["content"] = task_message["content"].format(**kwargs)

            if self._should_prepend_acknowledgment(key):
                for task_message in prompt["task_messages"]:
                    if "content" in task_message:
                        task_message["content"] = (
                            self.ACKNOWLEDGMENT_PREFIX + task_message["content"]
                        )
        return prompt

    def _should_prepend_acknowledgment(self, key: str) -> bool:
        return key not in self.ACKNOWLEDGMENT_EXCLUDED_KEYS


if __name__ == "__main__":
    from pprint import pprint

    prompts = NodePrompts()
    # pprint(prompts)
    # pprint(prompts.get("collect_service_area"))
    # pprint(prompts.get("confirm_service_area", match="Amelia County"))
    # pprint(prompts.get("initial"))
    # pprint(prompts.get("primary_role_message"))
    pprint(prompts.get("primary_role_message") | prompts.get("initial"))
