import yaml

from intake_bot.globals import ROOT_DIR


class Prompts:
    def __init__(self, path: str = f"""{ROOT_DIR}/data/prompts.yaml""", default_role: str = "system"):
        self.prompts = self._load_prompts(path, default_role)

    def _load_prompts(self, path: str, default_role: str) -> dict:
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
                    if isinstance(item, dict) and "content" in item and "role" not in item:
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
            raise KeyError(f"Prompt '{key}' not found.")

        if kwargs:
            prompt = self.prompts[key].copy()
            if "task_messages" in prompt:
                for task_message in prompt["task_messages"]:
                    if "content" in task_message and kwargs:
                        task_message["content"] = task_message["content"].format(**kwargs)
        else:
            prompt = self.prompts[key]
        return prompt


if __name__ == "__main__":
    from pprint import pprint

    prompts = Prompts()
    # pprint(prompts)
    # pprint(prompts.get("collect_service_area"))
    # pprint(prompts.get("confirm_service_area", match="Amelia County"))
    # pprint(prompts.get("initial"))
    # pprint(prompts.get("primary_role_message"))
    pprint(prompts.get("primary_role_message") | prompts.get("initial"))
