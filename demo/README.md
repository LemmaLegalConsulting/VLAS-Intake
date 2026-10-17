# Lemma Legal voice demo

A browser front end and a small FastAPI host for the repository's actual intake flow. Click **Start voice demo**, allow the microphone, and speak with the assistant. **Finish & see summary** ends audio and displays the structured answers collected so far, with an optional JSON download. The intake can also end naturally.

This uses the full Virginia sample screening, capped at ten minutes; it is not a shortened marketing script. It can ask for personal identifiers, so visitors are explicitly asked to use invented details. SSN and date of birth are excluded from the summary. No LegalServer submission, SMS, saved transcript, or state file is produced in demo mode. Azure still processes audio and conversation data under the account's provider settings.

## Folder layout and shared code

All demo-specific application, presentation, deployment, and test files live here:

```text
demo/
  app.py             # Session API, WebSocket host, and demo isolation
  explain.py         # Live explanations and fictional example records
  web/               # Case-study page, voice client, and cost calculator
  tests/             # Demo API and explanation tests
  Dockerfile         # Builds the browser and shared Python runtime
  fly.toml           # Dedicated Fly app with automatic sleep/wake
```

The demo imports the existing `src/intake_bot/` engine rather than copying it.
Small shared changes expose audio sample rates, disable external writes in demo
mode, preserve classifier evidence, and share income math with the explanation
screen. Telephone defaults remain unchanged. Python dependencies stay in the
root `pyproject.toml` / `uv.lock`, with a dedicated `demo` dependency group.
Generated browser builds, dependencies, and credentials are not committed.

## Run locally

From the repository root:

```sh
uv sync --no-dev --group demo --group test
npm ci --prefix demo/web
npm run --prefix demo/web build
# Export the five Azure settings below into the process environment, then:
uv run --no-dev --group demo uvicorn demo.app:app --host 127.0.0.1 --port 8080 --no-access-log --ws-max-size 65536
```

If the local environment lacks tokenizer data, run `uv run python -m nltk.downloader punkt_tab` once (the container includes it).

Open http://localhost:8080. The browser uses a local AudioWorklet for PCM capture and Web Audio for playback, with no remote Daily browser bundle. If autoplay is blocked, use **Enable audio**. Static pages and health checks work without credentials; starting voice returns a clear configuration error until these are set:

- `AZURE_API_KEY`
- `AZURE_LLM_ENDPOINT` (Azure resource root)
- `AZURE_LLM_MODEL` (deployment name)
- `AZURE_SPEECH_REGION`
- `AZURE_SPEECH_VOICE`

The same Azure setup requirements as the main bot apply. Optional classifier deployment overrides are described in the root `.env.dist`. Export values rather than relying on the root `.env`: the demo checks configuration before importing the bot. Do not configure Dialpad or LegalServer credentials for this deployment.

## Fly.io

Run commands from the repository root; the Docker build needs both `src/` and `demo/`. The app name must be unique. If changed, also update `DEMO_ALLOWED_ORIGINS` in `demo/fly.toml`.

```sh
fly apps create lemma-voice-demo
# Import a protected dotenv-format file through stdin, never commit it:
fly secrets import --app lemma-voice-demo < /path/to/demo-azure.env
fly deploy --config demo/fly.toml --ha=false
fly scale count 1 --app lemma-voice-demo
```

Keep **one Machine and one Uvicorn worker**: session ownership, capacity limits, and results live in process memory. A restart loses sessions and summaries. Active sessions are limited to two, ten minutes each, and thirty new sessions per hour per process. These are cost bounds, not a distributed abuse prevention system. Unused reservations expire after a minute; results expire five minutes after the session ends and are swept every thirty seconds. Browser-displayed and downloaded results remain with the visitor.

The config uses `auto_stop_machines = "stop"`, `auto_start_machines = true`, and `min_machines_running = 0`, with connection-based concurrency. A live WebSocket keeps the Machine in use. Idle pages do not poll. Fly wakes the Machine on the next request; the first page load and first voice session can be slower. The initial 2 GB allocation accommodates the existing speech/turn-detection dependencies; measure memory before reducing it. The interface is small, but the reused Python voice runtime is not a tiny container. See [Fly autostop documentation](https://fly.io/docs/launch/autostop-autostart/).

## Put it on lemmalegal.com

Use `voice.lemmalegal.com` for the demo, preserving the existing main website:

```sh
fly certs add voice.lemmalegal.com --app lemma-voice-demo
fly certs setup voice.lemmalegal.com --app lemma-voice-demo
```

Apply the DNS records returned by Fly, then link to `https://voice.lemmalegal.com` from the main website. The allowed origins are exact and must include the public demo origin. The apex domain's DNS should continue pointing to the existing website.

For an embedded demo that wakes only on a click, add a button to the main website and insert this iframe in its click handler (do not preload the iframe):

```html
<iframe src="https://voice.lemmalegal.com/"
  title="Lemma Legal voice intake demo"
  allow="microphone; autoplay"
  style="width:100%;height:900px;border:0"></iframe>
```

The main website must also permit the iframe origin in its CSP and microphone Permissions Policy. Direct linking is the simplest option, especially on mobile browsers. The demo allows framing by `lemmalegal.com` and `www.lemmalegal.com`.

## Branch review

Reviewed remote branches after fetching origin:

- `main` (`e4e453c`): current Azure STT/LLM/TTS flow and browser WebSocket transport, reused here.
- `deepgram` (`a688276`): switches speech services and adds turn handling. A provider change is optional for this demo, and needs different credentials.
- `deepgram-flux` (`04e6cf3`) and `cartesia-flux` (`b3bc3b3`): alternate speech stacks on older Pipecat dependency lines. Not suitable for wholesale merge into current main.
- `reduce-llm-deepgram` (`77ed9d7`): larger Pipecat 1.5 migration, prompt/latency improvements, handshake metadata, rate limiting, and lazy LegalServer credentials. This demo incorporates the latter two ideas, using opaque session tokens rather than caller details in URLs.
- `refactor-cleanup` (`e7598bc`): most advanced continuation, with Pipecat 1.7, Flux, conversation continuity, and in-process text scenario coverage. Worth adopting as a separate core upgrade with its regression tests; cherry-picking only its bot file would mix incompatible APIs.
- `dev` and `twilio-websockets` (`ea571d4`): older shared telephony implementation, not needed for a browser demo.

Core changes are limited to an explicit `demo_mode` flag that suppresses persistence/observability/SMS and a lazy LegalServer import. Normal bot callers retain the default behavior. The new demo does not expose the root development server publicly.

## Verification

```sh
.venv/bin/python -m pytest demo/tests -q
npm run --prefix demo/web build
```

Server tests cover session ownership, replay rejection, origin checks, capacity, expiration, hourly limits, missing credentials, timeout handling, and summary filtering. The Azure-backed WebSocket smoke test was exercised with synthetic speech and returned captured language data; browser layout/start/finish paths were exercised separately. A human microphone/speaker test remains useful: this headless WSL environment has no functioning audio output clock in Chromium. The native browser audio adapter initializes its context on the Start gesture and offers an explicit Enable audio fallback. DNS and TLS must be configured before browser microphone access on the public custom domain works.

## Current deployment

### Transparency and sample data

The page offers a worked fictional example alongside a separate **Your conversation** view. County/city suggestions come from `reference_data.yml`. During an active conversation only, the page refreshes the explanation every two seconds; polling stops when the conversation ends, preserving idle sleep behavior.

The legal-issue panel shows the actual description, suggested LSC problem code, individual classifier votes and weights, and follow-up questions. The example mapping is explicitly illustrative. Provider scores are model-voting scores, not probabilities. High-confidence classifications no longer make an unnecessary call to merge unused follow-up questions.

Income rows use the same `monthly_income_rows()` function as the validator. The panel displays each conversion, the truncated monthly total, household size, bundled poverty-guideline year, and the repository's 300% threshold. These are demonstration rules, not a promise of eligibility.

Conflict checks compare collected opposing-party full names against two **fictional** records in `demo/explain.py`. Try **Jordan Vale** to match **DEMO-1042**. Matching ignores case, punctuation and whitespace; it does not connect to LegalServer, establish a real conflict, or stop the intake. The fictional example is never inserted into session state.

The Fly demo uses 2-second Smart Turn silence fallback, a 3-second user-turn timeout, and a 0.8-second Azure transcription-latency allowance (previously 4.5, 7 and 1.5 seconds). Smart Turn and finalized-transcript interruption remain enabled. The interface distinguishes listening, preparing a response, and speaking, and reports response preparation time after the end-of-turn event. Longer pauses and cold starts can still occur.

Validation includes desktop/mobile rendering, live-panel fixture updates in a real browser session, example/session separation, HTML escaping, JSON download, real Azure classification, and unit tests of income conversions and fictional matching. A broader validator run found the pre-existing `South Boston` fuzzy-location test fails (matches `Southampton County`); that location is not used in the demo's county suggestions.

### Hosting

- Public URL: https://voice.lemmalegal.com
- Fly app: `lemma-voice-demo`, Lemma organization (`lemma`), `iad` region.
- Azure: existing `quint-mln02sj6-eastus2` AI Services resource, `gpt-4.1-mini` intake deployment and `en-US-AvaMultilingualNeural` speech voice. Credentials are stored only in Fly secrets.
- SiteGround CNAME: `voice` → `kjx0meg.lemma-voice-demo.fly.dev`.
- SiteGround TXT: `_fly-ownership.voice` → `app-kjx0meg`.
- TLS certificate verified with Fly.
- Deployment verified on October 17, 2026: HTTPS, browser start/finish and summary, and Azure-backed speech recognition and audio output over the public WebSocket. A stopped Machine woke and served the page in approximately 2.9 seconds; its first voice session also connected successfully. Voice-library imports run in a background thread to keep cold-start WebSocket handshakes responsive.

The browser captures mono 16-bit PCM at 16 kHz, matching the demo pipeline and Azure speech recognition input. Playback remains 24 kHz. Unlike telephony transports, this WebSocket path does not resample incoming PCM automatically. Keep capture, WebSocket metadata, and pipeline input rates aligned. The microphone level meter reflects local captured audio, not server transcription.

The demo keeps Pipecat Silero VAD and Smart Turn v3 enabled, and uses finalized transcriptions for turn-start detection. It requests browser `echoCancellation: "all"` where supported (with standard cancellation as fallback), retaining natural spoken interruption. The last finalized transcript is shown on screen. Fly enables `DEMO_REQUIRE_EXPLICIT_ANSWERS=true` to reinforce answer provenance at every intake step; recording functions also reject calls before the first nonempty visitor turn. Previously supplied facts can still be reused.

Timing spot checks for the same synthetic “English please” answer measured 3.28 seconds from end of speech to first response text before tuning, and 3.89 / 3.15 seconds afterward. These small samples do not demonstrate a general latency improvement. The settings reduce fallback waits, while ordinary model/tool turnaround remains roughly 3–4 seconds. A separate real classification of the example returned code 63 in 2.61 seconds. First response text is not the same as first audible playback.

### Case-study presentation

The public page now covers the configured screening flow, a responsive component
and data-flow diagram, an illustrative LegalServer API handoff, and an interactive
operating-cost comparison. The handoff is documentation only: it does not import
or enable the LegalServer client. The fictional conflict search is explicitly
separate from the repository's adverse-party collection and export.

`web/src/costs.ts` compares one sleeping machine, one warm machine, and two warm
machines. Fly's published Ashburn 2 shared CPU / 2 GB price was checked October
17, 2026 ($13.39 per 30-day month); stopped storage assumes 2 GB at $0.15/GB/month.
Azure speech rates and the model-per-intake allowance are explicitly editable
illustrative assumptions, not verified account rates or measured consumption.
The page lists billing exclusions, formulas, source links, and capacity caveats.
At the initial 100 ten-minute calls, estimates are $27.91 / $40.46 / $53.85
per month for the three hosting scenarios. These are planning figures only.

Validation: TypeScript/Vite build, desktop and 390px/320px browser checks,
existing county/income/classification fixtures, calculator totals, zero-call
hosting, capacity warning, and browser error monitoring. No voice-runtime code
or credentials changed for this presentation update.
