import os

from dotenv import load_dotenv

load_dotenv(override=True)


def get_ev(key: str, default: str = None) -> str:
    return os.getenv(key=key, default=default)


def require_ev(key: str) -> str:
    """
    Ensure that the specified environment variable is set and return its value.

    Args:
        key (str): The name of the environment variable to check.

    Returns:
        str: The value of the environment variable.

    Raises:
        ValueError: If the environment variable is not set.
    """
    if not (value := os.getenv(key)):
        raise ValueError(f"The {key} environment variable must be set.")
    return value


def ev_is_true(key: str) -> bool:
    """
    Check if the specified environment variable is set to 'true' (case-insensitive) or '1'.

    Args:
        key (str): The name of the environment variable.

    Returns:
        bool: True if the environment variable is set to 'true' or '1', False otherwise.
    """
    value = os.getenv(key, "").strip().lower()
    return value == "true" or value == "1"
