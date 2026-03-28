import pytest
from server import SilenceMixer


@pytest.mark.asyncio
async def test_silence_mixer_passthrough():
    mixer = SilenceMixer()

    assert await mixer.mix(b"\x00\x01\x02") == b"\x00\x01\x02"
