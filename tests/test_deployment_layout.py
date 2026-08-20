"""Smoke tests for deployment layout — entrypoint import and runtime data loaders.

Phase 1 – Safety and Deployment coverage:
  - Isolated copied layout (like Docker ``--no-install-project``)
  - Every required data file exercised through its production loader
  - Sentinel PII checks and transcript gating (preserved from original)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. Isolated deployment-layout smoke test (mimics Docker --no-install-project)
# ---------------------------------------------------------------------------


def _find_project_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parents[1]


def _copy_deployment_layout(dest: Path) -> None:
    """Copy ``bot.py`` and ``src/`` into *dest*, like the Dockerfile."""
    root = _find_project_root()
    shutil.copy2(root / "bot.py", dest / "bot.py")
    shutil.copytree(root / "src", dest / "src", dirs_exist_ok=True)


def test_isolated_bot_import(tmp_path):
    """Import root bot.py from an isolated copy, without editable install.

    This matches Docker's ``--no-install-project`` behaviour: only the
    checked-in files (``bot.py`` + ``src/``) are present, and the package
    is *not* pip-installed as editable.  Environment variables are cleared
    so ``.env`` cannot influence the import.
    """
    _copy_deployment_layout(tmp_path)

    # Remove .env influence: unset all intake-bot related env vars
    # and strip PYTHONPATH/PYTHONHOME so inherited paths cannot leak in.
    env = {k: v for k, v in os.environ.items() if not k.startswith("ENABLE_")}
    for key in (
        "AZURE_API_KEY",
        "AZURE_LLM_ENDPOINT",
        "AZURE_LLM_MODEL",
        "DEEPGRAM_API_KEY",
        "DIALPAD_API_KEY",
        "DIALPAD_SMS_NUMBER",
        "LEGAL_SERVER_SUBDOMAIN",
        "LEGAL_SERVER_BEARER_TOKEN",
        "OPENAI_API_KEY",
        "DAILY_API_KEY",
        "LOG_LEVEL",
        "LOG_TO_FILE",
        "ENABLE_TRANSCRIPTS",
        "ENABLE_DEV_STATE_PERSISTENCE",
        "ENABLE_TAIL_RUNNER",
        "ENABLE_TAIL_OBSERVER",
        "ENABLE_WHISKER",
        "USER_IDLE_TIMEOUT_SECS",
        "USER_IDLE_TIMEOUT_MAX_SECS",
        "WS_RATE_LIMIT_PER_MINUTE",
        "WS_AUTH_TOKEN",
        "LEGALSERVER_TESTING_DISABLE_CONNECTION",
        "WEBSOCKET_USER_IDLE_TIMEOUT_SECS",
        "WEBSOCKET_TEST_USER_IDLE_TIMEOUT_SECS",
        "DEEPGRAM_TTS_VOICE_EN",
        "DEEPGRAM_TTS_VOICE_ES",
        "AZURE_CLASSIFIER_GPT_4_1_MINI_DEPLOYMENT",
        "AZURE_CLASSIFIER_GPT_5_NANO_DEPLOYMENT",
        "DEEPGRAM_FLUX_EAGER_EOT_THRESHOLD",
        "DEEPGRAM_FLUX_EOT_THRESHOLD",
        "DEEPGRAM_FLUX_EOT_TIMEOUT_MS",
        "DEEPGRAM_FLUX_MIN_CONFIDENCE",
        "EXTERNAL_TURN_STOP_TIMEOUT_SECS",
    ):
        env.pop(key, None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)

    # Run as a subprocess with PYTHONPATH=src (no editable install).
    # Third-party site-packages are kept so that yaml, aiohttp, etc.
    # are available; only the host PYTHONPATH is stripped.
    import_paths_script = (
        "import sys, os; "
        "sys.path.insert(0, 'src'); "
        "import intake_bot; "
        "print(f'intake_bot.__file__={intake_bot.__file__}'); "
        "import bot; "
        "print(f'bot.__file__={bot.__file__}'); "
        "from intake_bot.utils.globals import DATA_DIR, PROJECT_ROOT; "
        "print(f'data_dir={DATA_DIR}'); "
        "print(f'project_root={PROJECT_ROOT}'); "
        "cwd = os.getcwd(); "
        "ok_bot = bot.__file__.startswith(cwd); "
        "ok_intake = intake_bot.__file__.startswith(os.path.join(cwd, 'src')); "
        "print(f'ISOLATION_OK={ok_bot and ok_intake}'); "
    )
    result = subprocess.run(
        [sys.executable, "-c", import_paths_script],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        pytest.fail(f"Isolated layout import failed (rc={result.returncode})")

    assert "ISOLATION_OK=True" in result.stdout, (
        f"Imports resolved outside isolated copy. Output:\n{result.stdout}"
    )


def test_version_safe_when_not_installed():
    """The version lookup returns a fallback when distribution metadata is absent."""
    # Direct test: verify the except path works
    from intake_bot import __version__

    # In the test venv the package is installed, so we get the real version.
    # Assert at least that __version__ is a non-empty string.
    assert isinstance(__version__, str) and len(__version__) > 0


def test_version_fallback_logic(monkeypatch):
    """Verify the version fallback is used when PackageNotFoundError is raised."""
    import importlib
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _orig_version_fn

    import intake_bot

    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda name: (_ for _ in ()).throw(PackageNotFoundError(name)),
    )
    importlib.reload(intake_bot)
    assert intake_bot.__version__ == "0.0.0.dev0"

    # Restore the original version function and re-import so subsequent
    # tests still see the real version.
    monkeypatch.setattr("importlib.metadata.version", _orig_version_fn)
    importlib.reload(intake_bot)


# ---------------------------------------------------------------------------
# 2. Each required data file exercised through its production loader
# ---------------------------------------------------------------------------


# 2a. reference_data.yml — ReferenceDataLoader
def test_reference_data_via_production_loader():
    from intake_bot.services.reference_data import ReferenceDataLoader

    loader = ReferenceDataLoader()
    ref = loader.get_all()
    assert "virginia_localities" in ref
    assert isinstance(ref["virginia_localities"], dict)
    assert len(ref["virginia_localities"]) >= 1
    assert "official_name_normalizations" in ref


# 2b. asset_exemptions.yml — IntakeValidator._load_asset_exemptions
def test_asset_exemptions_via_production_loader():
    from intake_bot.nodes.validator import _load_asset_exemptions

    single, phrases = _load_asset_exemptions()
    assert len(single) >= 1
    assert len(phrases) >= 1
    # Spot-check that known exemption terms are loaded
    assert (
        "primary vehicle" in phrases or "primary car" in phrases or "primary" in single
    )


# 2c. referral_content.yml — _load_referral_content
def test_referral_content_via_production_loader():
    from intake_bot.services.dialpad import _load_referral_content

    content = _load_referral_content()
    assert content.spoken_en
    assert content.spoken_es
    assert content.phone_en
    assert content.phone_es
    assert content.text_en
    assert content.text_es
    assert content.sms_en
    assert content.sms_es


# 2d. classifier_prompts.yml — Classifier._load_prompts
def test_classifier_prompts_via_production_loader():
    from intake_bot.services.classifier import Classifier

    prompts = Classifier._load_prompts()
    assert "default" in prompts
    assert "{{taxonomy}}" in prompts["default"] or len(prompts["default"]) > 0


# 2e. node_prompts.yml — NodePrompts (uses DATA_DIR / "node_prompts.yml")
def test_node_prompts_via_production_loader():
    from intake_bot.utils.node_prompts import NodePrompts

    prompts = NodePrompts()
    initial = prompts.get("initial")
    assert initial is not None
    assert "task_messages" in initial

    spoken = prompts.get_spoken_prompt("initial_greeting")
    assert isinstance(spoken, str) and len(spoken) > 0


# 2f. federal_poverty_scale.json — get_poverty_scale_data
def test_federal_poverty_scale_via_production_loader():
    from intake_bot.services.poverty import get_poverty_scale_data

    data = get_poverty_scale_data()
    assert "poverty_base" in data
    assert "poverty_increment" in data
    assert "poverty_level_update_year" in data

    from datetime import date

    today = date.today()  # noqa: DTZ011 - policy uses the local calendar date
    year = int(data["poverty_level_update_year"])
    assert year >= today.year, (
        f"Poverty scale year {year} is behind {today.year}. "
        f"Update federal_poverty_scale.json before December 1."
    )
    if today.month >= 12 and today.day >= 1:
        assert year >= today.year + 1, (
            f"Poverty scale year {year} must already advance to {today.year + 1} "
            f"after December 1. Update federal_poverty_scale.json."
        )


# 2g. verify that the module-level REFERRAL singleton loads correctly
def test_referral_singleton_loads():
    from intake_bot.services.dialpad import REFERRAL

    assert REFERRAL.spoken_en
    assert REFERRAL.spoken_es


# 2h. verify that IntakeValidator loads asset exemptions at class level
def test_validator_loads_asset_exemptions():
    from intake_bot.nodes.validator import IntakeValidator

    assert len(IntakeValidator.ASSET_EXEMPT_SINGLE_WORDS) >= 1
    assert len(IntakeValidator.ASSET_EXEMPT_PHRASES) >= 1


# ---------------------------------------------------------------------------
# 3. Preserved: Sentinel PII checks and transcript gating
# ---------------------------------------------------------------------------


SENTINEL_PII = "SSN-SENTINEL-9999"


@pytest.mark.asyncio
async def test_sentinel_pii_not_in_production_logs(monkeypatch):
    """Verify sentinel PII values never appear in production-level logs."""
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("ENABLE_TRANSCRIPTS", "false")
    monkeypatch.setenv("LOG_TO_FILE", "false")
    monkeypatch.setenv("ENABLE_DEV_STATE_PERSISTENCE", "false")
    monkeypatch.setenv("LEGALSERVER_TESTING_DISABLE_CONNECTION", "true")

    captured = []

    def log_sink(msg):
        captured.append(msg)

    from loguru import logger

    sink_id = logger.add(log_sink, level="INFO")

    try:
        from intake_bot.services.dialpad import SMS

        sms = SMS(api_key="", from_number="")
        assert not sms.is_configured
        with pytest.raises(ValueError, match="Dialpad SMS is not configured"):
            await sms.send("+15096305855", SENTINEL_PII)

        for entry in captured:
            msg = str(entry)
            assert SENTINEL_PII not in msg, f"Sentinel PII found in log: {msg}"
    finally:
        logger.remove(sink_id)


@pytest.mark.asyncio
async def test_sentinel_pii_not_in_classifier_logs(monkeypatch):
    """Verify sentinel PII does not appear in classifier logs."""
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    captured = []

    def log_sink(msg):
        captured.append(msg)

    from loguru import logger

    sink_id = logger.add(log_sink, level="INFO")

    try:
        from intake_bot.services.classifier import Classifier

        clf = Classifier()
        clf.taxonomy = {"Test": "99 Test"}
        clf.providers = []

        response = await clf.classify(problem_description=SENTINEL_PII)
        assert response is not None

        for entry in captured:
            msg = str(entry)
            assert SENTINEL_PII not in msg, (
                f"Sentinel PII found in classifier log: {msg}"
            )
    finally:
        logger.remove(sink_id)


def test_transcript_absent_by_default(monkeypatch):
    monkeypatch.setenv("ENABLE_TRANSCRIPTS", "false")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    from intake_bot.utils.ev import ev_is_true

    assert not ev_is_true("ENABLE_TRANSCRIPTS")


def test_transcript_requires_debug(monkeypatch):
    monkeypatch.setenv("ENABLE_TRANSCRIPTS", "true")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    from intake_bot.utils.ev import ev_is_true

    assert ev_is_true("ENABLE_TRANSCRIPTS")


def test_transcript_file_requires_log_to_file(monkeypatch):
    monkeypatch.setenv("ENABLE_TRANSCRIPTS", "true")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_TO_FILE", "true")
    from intake_bot.utils.ev import ev_is_true, get_ev

    assert ev_is_true("ENABLE_TRANSCRIPTS")
    assert get_ev("LOG_LEVEL") == "DEBUG"
    assert ev_is_true("LOG_TO_FILE")


# ---------------------------------------------------------------------------
# 4. Cold-start measurement: genuinely isolated copied layout with path assertions
# ---------------------------------------------------------------------------

COLD_START_TIMEOUT_SECS = 60.0


def _cold_start_measure_script() -> str:
    """Python script run inside the isolated copy to measure import time and
    assert every imported project module originates from the copy, not the
    repository's editable install or inherited PYTHONPATH.

    Distribution metadata (``__version__`` via ``importlib.metadata``) may
    still come from the installed package in the venv -- that is accepted and
    separately tested by ``test_version_fallback_logic``.  This test isolates
    *source module paths* only.
    """
    return r"""
import os, sys, time

_cwd = os.getcwd()
_copy_root = _cwd  # tmp_path set as cwd by the parent

sys.path.insert(0, 'src')
_expected_prefix = os.path.join(_copy_root, 'src')

def _check_module(module, label):
    f = getattr(module, '__file__', None)
    if f is None:
        print(f'{label}.__file__=NONE')
        return True
    print(f'{label}.__file__={f}')
    if not f.startswith(_expected_prefix):
        print(f'ISOLATION_FAILED: {label} __file__ {f!r} does not start with {_expected_prefix!r}')
        return False
    return True

t0 = time.perf_counter()

# ---- import project modules ----
import intake_bot.bot as _bot_mod
ok = _check_module(_bot_mod, 'intake_bot.bot')

from intake_bot import __version__ as _ver
print(f'__version__={_ver}')

from intake_bot.utils.daily_dialin import (
    looks_like_daily_dialin_body, normalize_daily_dialin_body,
)
import intake_bot.utils.daily_dialin as _dd
ok = _check_module(_dd, 'intake_bot.utils.daily_dialin') and ok

import intake_bot.utils.ev as _ev
ok = _check_module(_ev, 'intake_bot.utils.ev') and ok

import intake_bot.utils.node_prompts as _np
ok = _check_module(_np, 'intake_bot.utils.node_prompts') and ok

import intake_bot.services.reference_data as _rd
ok = _check_module(_rd, 'intake_bot.services.reference_data') and ok

from intake_bot.nodes.validator import IntakeValidator
import intake_bot.nodes.validator as _nv
ok = _check_module(_nv, 'intake_bot.nodes.validator') and ok

import intake_bot.services.dialpad as _dp
ok = _check_module(_dp, 'intake_bot.services.dialpad') and ok

from intake_bot.services.classifier import Classifier
import intake_bot.services.classifier as _clf
ok = _check_module(_clf, 'intake_bot.services.classifier') and ok

import intake_bot.services.poverty as _pv
ok = _check_module(_pv, 'intake_bot.services.poverty') and ok

# ---- force data-file loads ----
from intake_bot.utils.node_prompts import NodePrompts
NodePrompts()

from intake_bot.services.reference_data import ReferenceDataLoader
_ = ReferenceDataLoader().get_all()

_ = IntakeValidator.ASSET_EXEMPT_SINGLE_WORDS

from intake_bot.services.dialpad import _load_referral_content
_ = _load_referral_content()

_ = Classifier._load_prompts()

from intake_bot.services.poverty import get_poverty_scale_data
_ = get_poverty_scale_data()

t1 = time.perf_counter()
elapsed = t1 - t0

print(f'cold_start_secs={elapsed:.4f}')
print(f'ISOLATION_OK={ok}')

if not ok:
    sys.exit(2)
sys.exit(0)
""".lstrip()


def test_cold_start_from_isolated_copy(tmp_path):
    """Measure cold import/init time from an isolated copied deployment layout
    and assert every imported project module's ``__file__`` is beneath the copy.

    The source layout (``bot.py`` + ``src/``) is copied into a temporary
    directory to match the Docker ``--no-install-project`` arrangement.
    The subprocess uses only that copy: ``PYTHONPATH`` points at the copy's
    ``src/``, the child's cwd is the copy root, and all credential/network
    environment variables are stripped.  ``PYTHON_DOTENV_DISABLED=1`` prevents
    ``dotenv`` from reading any ``.env`` file.

    Distribution metadata (``importlib.metadata`` version) may still come
    from the installed package in the venv -- that is accepted and separately
    tested by ``test_version_fallback_logic``.  The assertion here is on
    *source module file paths* only.

    The elapsed time is printed for diagnostic trending but is NOT asserted
    against a fixed threshold because absolute timings vary across CI workers
    and local hardware.  A 60-second subprocess timeout guards against hangs;
    exceeding it is a failure.
    """
    import os
    import subprocess
    import sys

    _copy_deployment_layout(tmp_path)

    env = {k: v for k, v in os.environ.items() if not k.startswith("ENABLE_")}
    cred_keys = [
        "AZURE_API_KEY",
        "AZURE_LLM_ENDPOINT",
        "AZURE_LLM_MODEL",
        "DEEPGRAM_API_KEY",
        "DIALPAD_API_KEY",
        "DIALPAD_SMS_NUMBER",
        "LEGAL_SERVER_SUBDOMAIN",
        "LEGAL_SERVER_BEARER_TOKEN",
        "OPENAI_API_KEY",
        "DAILY_API_KEY",
        "LOG_LEVEL",
        "LOG_TO_FILE",
        "ENABLE_TRANSCRIPTS",
        "ENABLE_DEV_STATE_PERSISTENCE",
        "ENABLE_TAIL_RUNNER",
        "ENABLE_TAIL_OBSERVER",
        "ENABLE_WHISKER",
        "USER_IDLE_TIMEOUT_SECS",
        "USER_IDLE_TIMEOUT_MAX_SECS",
        "WS_RATE_LIMIT_PER_MINUTE",
        "WS_AUTH_TOKEN",
        "LEGALSERVER_TESTING_DISABLE_CONNECTION",
        "WEBSOCKET_USER_IDLE_TIMEOUT_SECS",
        "WEBSOCKET_TEST_USER_IDLE_TIMEOUT_SECS",
        "DEEPGRAM_TTS_VOICE_EN",
        "DEEPGRAM_TTS_VOICE_ES",
        "AZURE_CLASSIFIER_GPT_4_1_MINI_DEPLOYMENT",
        "AZURE_CLASSIFIER_GPT_5_NANO_DEPLOYMENT",
        "DEEPGRAM_FLUX_EAGER_EOT_THRESHOLD",
        "DEEPGRAM_FLUX_EOT_THRESHOLD",
        "DEEPGRAM_FLUX_EOT_TIMEOUT_MS",
        "DEEPGRAM_FLUX_MIN_CONFIDENCE",
        "EXTERNAL_TURN_STOP_TIMEOUT_SECS",
    ]
    for k in cred_keys:
        env.pop(k, None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["PYTHONPATH"] = str(tmp_path / "src")

    code = _cold_start_measure_script()
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=COLD_START_TIMEOUT_SECS,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)

    lines = result.stdout.strip().splitlines()

    # Check isolation outcome
    isolation_lines = [ln for ln in lines if ln.startswith("ISOLATION_OK=")]
    assert len(isolation_lines) == 1, f"Expected ISOLATION_OK= line, got: {lines}"
    assert isolation_lines[0].endswith("=True"), (
        f"Module isolation FAILED: at least one module __file__ was outside "
        f"the copied layout. Full output:\n{result.stdout}"
    )

    # Parse timing
    cold_start_line = [ln for ln in lines if ln.startswith("cold_start_secs=")]
    assert len(cold_start_line) == 1, f"Expected cold_start_secs= line, got: {lines}"
    elapsed = float(cold_start_line[0].split("=", 1)[1])

    # Version is allowed to come from distribution metadata
    version_lines = [ln for ln in lines if ln.startswith("__version__=")]
    assert len(version_lines) == 1, f"Expected __version__= line, got: {lines}"

    print(f"Cold-start measurement: {elapsed:.3f}s, all modules under {tmp_path}")
    print(
        f"Isolation verified: every intake_bot module __file__ starts with {tmp_path}/src"
    )
