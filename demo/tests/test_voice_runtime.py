"""Regression coverage for the browser adapter across the Pipecat migration."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from intake_bot import bot


@pytest.mark.asyncio
async def test_demo_runtime_keeps_audio_rates_and_blocks_external_side_effects(monkeypatch):
    # Deliberately enable production observability flags: demo_mode must still win.
    monkeypatch.setattr(bot, "ev_is_true", lambda name: True)
    monkeypatch.setattr(bot, "require_ev", lambda name: "test")
    monkeypatch.setattr(bot, "get_ev", lambda name, default="": default)
    forbidden = MagicMock(side_effect=AssertionError("Demo attempted persistence or logging"))
    monkeypatch.setattr(bot, "call_logging_context", forbidden)
    monkeypatch.setattr(bot, "save_state_to_json", forbidden)
    monkeypatch.setattr(bot, "save_intake_legalserver", forbidden)
    for name in ("DeepgramFluxSTTService", "DeepgramFluxTTSService", "DeepgramTTSService", "AzureLLMService", "ServiceSwitcher", "Pipeline"):
        monkeypatch.setattr(bot, name, MagicMock())
    monkeypatch.setattr(bot, "build_context_aggregator", MagicMock())
    handlers = {}
    worker = MagicMock()
    worker.event_handler.side_effect = lambda name: lambda handler: handlers.setdefault(name, handler)
    factory = MagicMock(return_value=worker)
    monkeypatch.setattr(bot, "PipelineWorker", factory)
    flow = SimpleNamespace(state={})
    monkeypatch.setattr(bot, "StateContextFlowManager", MagicMock(return_value=flow))

    async def finish():
        await handlers["on_pipeline_finished"](worker, None)

    runner = SimpleNamespace(add_workers=AsyncMock(), run=finish)
    monkeypatch.setattr(bot, "WorkerRunner", MagicMock(return_value=runner))
    transport = MagicMock()
    transport_handlers = {}
    transport.event_handler.side_effect = lambda name: lambda handler: transport_handlers.setdefault(name, handler)
    worker.cancel = AsyncMock()
    configure = AsyncMock()
    await bot.run_bot(transport, "test-demo", "", False, demo_mode=True,
                      audio_in_sample_rate=16000, audio_out_sample_rate=24000,
                      configure_transport=configure)
    params = factory.call_args.kwargs["params"]
    assert params.audio_in_sample_rate == 16000
    assert params.audio_out_sample_rate == 24000
    assert factory.call_args.kwargs["observers"] == []
    assert flow.state["_demo_mode"] is True
    assert flow.state["_demo_has_user_answer"] is False
    configure.assert_awaited_once_with(transport, worker, flow, "test-demo")
    forbidden.assert_not_called()

    await transport_handlers["on_client_disconnected"](transport, None)
    worker.cancel.assert_awaited_once()
    worker.stop_when_done.assert_not_called()
