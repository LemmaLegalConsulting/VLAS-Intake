# Python Client for LLM-Powered Automated Caller Testing

This Python client enables LLM-powered automated voice testing against
the local websocket server without making a real PSTN call.

## Setup Instructions

### 1. **Start the Server:**

Follow the instructions in the main README to start the server.

### 2. **Run the local websocket server:**

```sh
uv run uvicorn server:app --host 0.0.0.0 --port 8765 --reload
```

### 3. **Run the client:**

```sh
python client.py
```

- `-u`: Intake-bot server URL (default is `http://localhost:8765`)
- `-c`: Number of concurrent client connections (default is `1`)
- `-s`: The script from `scripts.yml` that you want to use
- `--validate`: Validate the saved flow-manager state after the call
  completes
- `--call-id`: Use a specific call id for a single client run
- `--call-id-prefix`: Prefix used for generated timestamp call ids
  (default is `ws-test`)
- `--server-idle-timeout`: Override the server-side websocket idle
  timeout in seconds for this test run

The client connects directly to the local `/ws` endpoint and passes
the caller phone number plus generated call id in the websocket URL
query string. Generated ids are timestamp-based and include the client
name so they remain sortable in logs and unique across concurrent test
runs. When `--server-idle-timeout` is provided, the client also passes
`idle_timeout_secs=...` so websocket test calls can tolerate slower
STT turnaround without changing the default idle timeout for other
call paths. If the generated or supplied call id starts with
`ws-test`, the server also defaults websocket idle timeout to `25`
seconds unless overridden with `--server-idle-timeout`,
`WEBSOCKET_USER_IDLE_TIMEOUT_SECS`, or
`WEBSOCKET_TEST_USER_IDLE_TIMEOUT_SECS`.

This exercises the current websocket transport directly:

- client connects to `/ws?call_id=...&caller_phone_number=...`
- transport uses the protobuf websocket serializer
- caller simulation uses Azure STT, Azure OpenAI, and Azure TTS

The client transport is configured with a `SilenceMixer` so that the
output transport sends continuous audio frames — TTS audio when
speaking, silence when idle. Without this, the server's Azure STT
never receives silence between utterances and cannot finalize
recognitions (real phone calls send continuous audio naturally).

An `InterimTranscriptionFinalizer` sits between the client's STT and
LLM aggregator. The server transport also lacks a mixer, so it only
sends audio during TTS playback. The client's Azure STT therefore only
produces interim transcriptions of the server's speech. The finalizer
promotes an interim to a final transcript after a short quiet period
so the client LLM can respond.

The Python test client now uses the same Azure environment contract as
the bot runtime, with fallback support for legacy Pipecat Azure env
names where useful. Local `.env` must include:

- `AZURE_API_KEY`
- `AZURE_SPEECH_REGION`
- `AZURE_SPEECH_VOICE`
- `AZURE_LLM_ENDPOINT`
- `AZURE_LLM_MODEL`

Optional:

- `AZURE_LLM_SUMMARY_MODEL` for periodic client-side conversation
  summaries
- `AZURE_LLM_API_VERSION` or `AZURE_OPENAI_API_VERSION` to override
  the Azure OpenAI API version used by the summary client
