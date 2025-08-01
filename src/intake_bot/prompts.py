import yaml

from intake_bot.globals import ROOT_DIR


def add_default_role(data: dict, default_role: str = "system") -> dict:
    """
    Recursively add a default role to all role_messages and task_messages in the YAML data.

    Args:
        data (dict): The parsed YAML data.
        default_role (str): The default role to add.

    Returns:
        dict: The updated data with default roles added.
    """
    for key, value in data.items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and "content" in item and "role" not in item:
                    item["role"] = default_role
        elif isinstance(value, dict):
            data[key] = add_default_role(value, default_role)
    return data


def get_prompts(path: str = f"""{ROOT_DIR}/data/prompts.yaml""", default_role: str = "system") -> dict:
    with open(path) as f:
        prompts = yaml.safe_load(f)

    for key, value in prompts.items():
        if isinstance(value, dict):
            value["name"] = key
            prompts[key] = add_default_role(value, default_role)

    return prompts


if __name__ == "__main__":
    from pprint import pprint

    prompts = get_prompts()

    pprint(prompts)
