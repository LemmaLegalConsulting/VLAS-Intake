import importlib.util
import json
from pathlib import Path

import pytest


def _load_test_manager_module():
    module_path = (
        Path(__file__).resolve().parents[1] / "client" / "python" / "test_manager.py"
    )
    spec = importlib.util.spec_from_file_location(
        "client_python_test_manager", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_test_runner_defaults_use_canonical_intake_bot_logs():
    module = _load_test_manager_module()

    runner = module.TestRunner()

    assert Path(runner.results_file) == module.DEFAULT_RESULTS_FILE
    assert Path(runner.flow_manager_state_file) == module.DEFAULT_STATE_FILE


def test_test_runner_relative_paths_resolve_from_repo_roots():
    module = _load_test_manager_module()

    runner = module.TestRunner(
        results_file="logs/client_test_results.json",
        flow_manager_state_file="logs/flow_manager_state.json",
        scripts_file="scripts.yml",
    )

    assert Path(runner.results_file) == module.DEFAULT_RESULTS_FILE
    assert Path(runner.flow_manager_state_file) == module.DEFAULT_STATE_FILE
    assert "victoria" in runner.scripts


def test_state_validator_ignores_storage_only_tts_voice_extra_key():
    module = _load_test_manager_module()

    validator = module.StateValidator()

    passed, mismatches = validator.compare_states(
        actual_state={"language": "en", "tts_voice": "aura-2-mars-en"},
        expected_state={"language": "en"},
    )

    assert passed is True
    assert mismatches == []


def test_state_validator_normalizes_phone_number_formatting():
    module = _load_test_manager_module()

    validator = module.StateValidator()

    passed, mismatches = validator.compare_states(
        actual_state={
            "phone": {"phone_number": "+18665345243"},
            "adverse_parties": {
                "adverse_parties": [{"phones": [{"number": "+18665345256"}]}]
            },
        },
        expected_state={
            "phone": {"phone_number": "(866) 534-5243"},
            "adverse_parties": {
                "adverse_parties": [{"phones": [{"number": "(866) 534-5256"}]}]
            },
        },
    )

    assert passed is True
    assert mismatches == []


def test_state_validator_ignores_service_area_metadata():
    module = _load_test_manager_module()

    validator = module.StateValidator()

    passed, mismatches = validator.compare_states(
        actual_state={
            "service_area": {
                "location": "Amelia County",
                "outcome": "exact_match",
                "candidates": [],
                "match_type": "canonical",
            },
            "household_members": {"members": []},
        },
        expected_state={"service_area": {"location": "Amelia County"}},
    )

    assert passed is True
    assert mismatches == []


@pytest.mark.asyncio
async def test_test_runner_validates_in_memory_state(tmp_path):
    module = _load_test_manager_module()
    runner = module.TestRunner(
        results_file=tmp_path / "results.json",
        flow_manager_state_file=tmp_path / "state.json",
    )

    passed, mismatches = await runner.validate_state(
        call_id="text-1",
        script_name="celeste",
        actual_state={"language": {"language": "English"}},
        expected_state={"language": {"language": "English"}},
    )

    assert passed is True
    result = json.loads((tmp_path / "results.json").read_text())
    assert result["text-1"]["state_file"] == str(tmp_path / "state.json")
    assert mismatches == []
