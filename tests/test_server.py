import pytest
from intake_bot.nodes.nodes import node_start
from server import SilenceMixer


@pytest.mark.asyncio
async def test_silence_mixer_passthrough():
    mixer = SilenceMixer()

    assert await mixer.mix(b"\x00\x01\x02") == b"\x00\x01\x02"


def test_websocket_startup_uses_shared_start_node():
    node = node_start()

    assert node["respond_immediately"] is False
