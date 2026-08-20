import os
import re

from dotenv import load_dotenv

load_dotenv(override=True)


def require_ev(key: str) -> str:
    """
    Ensure that the specified environment variable is set to a non-empty value.

    Environment variables assigned as `VAR=` are treated as unset.

    Args:
        key (str): The name of the environment variable to check.

    Returns:
        str: The value of the environment variable.

    Raises:
        ValueError: If the environment variable is missing or empty.
    """
    if not (value := os.getenv(key)):
        raise ValueError(f"""The {key} environment variable must be set.""")
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


def get_ev(key: str, default: str = "") -> str:
    value = os.getenv(key=key, default=default)
    if value.strip() == "":
        return default
    return value


# --------------------------------------------------------------------
# Deepgram TTS Languages
# --------------------------------------------------------------------


DEEPGRAM_TTS_DEFAULT_LANGUAGE_VOICES = (("EN", "flux-alexis-en"),)
DEEPGRAM_TTS_DEFAULT_VOICE_BY_LANGUAGE = dict(DEEPGRAM_TTS_DEFAULT_LANGUAGE_VOICES)
DEEPGRAM_TTS_DEFAULT_VOICE = DEEPGRAM_TTS_DEFAULT_VOICE_BY_LANGUAGE["EN"]


def _normalize_language_env_token(value: object) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip().upper())
    return normalized.strip("_")


def get_deepgram_tts_voices(language: object | None = None) -> str:
    if language is None:
        return DEEPGRAM_TTS_DEFAULT_VOICE

    language_token = _normalize_language_env_token(language)
    if not language_token:
        return DEEPGRAM_TTS_DEFAULT_VOICE

    exact_env_key = f"DEEPGRAM_TTS_VOICE_{language_token}"
    exact_match = get_ev(exact_env_key)
    if exact_match:
        return exact_match

    prefix = "DEEPGRAM_TTS_VOICE_"
    for key, raw_value in os.environ.items():
        if not key.startswith(prefix):
            continue
        value = raw_value.strip()
        if not value:
            continue

        env_language_token = _normalize_language_env_token(key.removeprefix(prefix))
        if not env_language_token:
            continue
        if (
            env_language_token == language_token
            or env_language_token.startswith(f"{language_token}_")
            or language_token.startswith(f"{env_language_token}_")
        ):
            return value

    for (
        default_language_token,
        language_default_voice,
    ) in DEEPGRAM_TTS_DEFAULT_LANGUAGE_VOICES:
        if (
            default_language_token == language_token
            or default_language_token.startswith(f"{language_token}_")
            or language_token.startswith(f"{default_language_token}_")
        ):
            return language_default_voice

    return DEEPGRAM_TTS_DEFAULT_VOICE
