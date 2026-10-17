import asyncio

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

from demo import app as demo

ORIGIN = {"origin": "http://localhost:8080"}


async def fake_runner(ws, session):
    session.state.update({"names": {"names": {"first": "Sample"}},
                          "ssn_last_4": {"ssn_last_4": "1234"},
                          "_private": "hidden"})
    await ws.send_text("ready")
    await ws.receive_text()


def create(client):
    response = client.post('/api/sessions', headers=ORIGIN)
    assert response.status_code == 200
    return response.json()['token']


def test_session_results_and_replay():
    with TestClient(demo.create_app(fake_runner)) as client:
        token = create(client)
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get('/api/results').status_code == 404
        with client.websocket_connect(f'/ws/{token}', headers=ORIGIN) as ws:
            assert ws.receive_text() == 'ready'
            response = client.get('/api/results', headers=headers)
            assert response.headers['cache-control'] == 'no-store'
            assert response.json()['sections'] == [{"title": "Name", "data": {"names": {"first": "Sample"}}}]
            ws.send_text('finish')
            assert ws.receive()['type'] == 'websocket.close'
        assert client.get('/api/results', headers=headers).json()['finished']
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(f'/ws/{token}', headers=ORIGIN):
                pass
        assert error.value.code == 1008


def test_origin_capacity_and_reserved_session_expiry(monkeypatch):
    with TestClient(demo.create_app(fake_runner)) as client:
        assert client.post('/api/sessions').status_code == 403
        assert client.post('/api/sessions', headers={"origin": "https://evil.example"}).status_code == 403
        create(client)
        create(client)
        assert client.post('/api/sessions', headers=ORIGIN).status_code == 429
        now = demo.time.monotonic()
        monkeypatch.setattr(demo.time, 'monotonic', lambda: now + 61)
        create(client)


def test_timeout_and_result_expiry(monkeypatch):
    async def stalled(ws, session):
        await asyncio.sleep(5)
    monkeypatch.setattr(demo, 'SESSION_SECONDS', .02)
    with TestClient(demo.create_app(stalled)) as client:
        token = create(client)
        with client.websocket_connect(f'/ws/{token}', headers=ORIGIN) as ws:
            assert ws.receive()['type'] == 'websocket.close'
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get('/api/results', headers=headers).json()['error']
        now = demo.time.monotonic()
        monkeypatch.setattr(demo.time, 'monotonic', lambda: now + 301)
        assert client.get('/api/results', headers=headers).status_code == 404


def test_hourly_limit(monkeypatch):
    monkeypatch.setattr(demo, 'STARTS_PER_HOUR', 1)
    with TestClient(demo.create_app(fake_runner)) as client:
        create(client)
        assert client.post('/api/sessions', headers=ORIGIN).status_code == 429


def test_missing_voice_configuration(monkeypatch):
    monkeypatch.delenv('AZURE_API_KEY', raising=False)
    with TestClient(demo.create_app()) as client:
        assert client.get('/healthz').status_code == 200
        assert client.post('/api/sessions', headers=ORIGIN).status_code == 503


def test_other_session_cannot_read_results():
    with TestClient(demo.create_app(fake_runner)) as client:
        first, second = create(client), create(client)
        with client.websocket_connect(f'/ws/{first}', headers=ORIGIN) as ws:
            ws.receive_text()
            assert client.get('/api/results', headers={'Authorization': f'Bearer {second}'}).json()['sections'] == []
            ws.send_text('finish')
            ws.receive()


def test_foreign_websocket_origin_rejected():
    with TestClient(demo.create_app(fake_runner)) as client:
        token = create(client)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f'/ws/{token}', headers={'origin': 'https://evil.example'}):
                pass


def test_timeout_when_runner_swallows_cancellation(monkeypatch):
    async def swallowing_runner(ws, session):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass  # Pipecat's PipelineRunner performs cleanup and swallows this.
    monkeypatch.setattr(demo, 'SESSION_SECONDS', .02)
    with TestClient(demo.create_app(swallowing_runner)) as client:
        token = create(client)
        with client.websocket_connect(f'/ws/{token}', headers=ORIGIN) as ws:
            ws.receive()
        result = client.get('/api/results', headers={'Authorization': f'Bearer {token}'}).json()
        assert result['finished'] and 'ten-minute' in result['error']
