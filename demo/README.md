# Lemma Legal voice demo

A browser front end and a small FastAPI host for the repository's actual intake flow. Click **Start voice demo**, allow the microphone, and speak with the assistant. **Finish & see summary** ends audio and displays the structured answers collected so far, with an optional JSON download. The intake can also end naturally.

This uses the full Virginia sample screening, capped at ten minutes; it is not a shortened marketing script. It can ask for personal identifiers, so visitors are explicitly asked to use invented details. SSN and date of birth are excluded from the summary. No LegalServer submission, SMS, saved transcript, or state file is produced in demo mode. Deepgram processes speech and Azure processes conversation text under the accounts' provider settings.

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
# Export the four provider settings below into the process environment, then:
uv run --no-dev --group demo uvicorn demo.app:app --host 127.0.0.1 --port 8080 --no-access-log --ws-max-size 65536
```

If the local environment lacks tokenizer data, run `uv run python -m nltk.downloader punkt_tab` once (the container includes it).

Open http://localhost:8080. The browser uses a local AudioWorklet for PCM capture and Web Audio for playback, with no remote Daily browser bundle. If autoplay is blocked, use **Enable audio**. Static pages and health checks work without credentials; starting voice returns a clear configuration error until these are set:

- `AZURE_API_KEY`
- `AZURE_LLM_ENDPOINT` (Azure resource root)
- `AZURE_LLM_MODEL` (deployment name)
- `DEEPGRAM_API_KEY`

The same Azure setup requirements as the main bot apply. Optional classifier deployment overrides are described in the root `.env.dist`. Export values rather than relying on the root `.env`: the demo checks configuration before importing the bot. Do not configure Dialpad or LegalServer credentials for this deployment.

## Fly.io

Run commands from the repository root; the Docker build needs both `src/` and `demo/`. The app name must be unique. If changed, also update `DEMO_ALLOWED_ORIGINS` in `demo/fly.toml`.

```sh
fly apps create lemma-voice-demo
# Import a protected dotenv-format file through stdin, never commit it:
fly secrets import --app lemma-voice-demo < /path/to/demo-voice.env
git switch demo/azure-deepgram
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

The `demo/azure-deepgram` branch merges `refactor-cleanup` (`e7598bc`) with the
browser demo and its latest branding/calculator changes. Fly builds the checked-out
source; it is not automatically connected to a Git branch.

The shared runtime uses Pipecat 1.7, Azure LLM, Deepgram Flux multilingual STT,
streaming Flux English TTS (`flux-alexis-en`), and Aura 2 Spanish TTS
(`aura-2-olivia-es`). It includes deterministic spoken prompts, structured state
context, and the branch's conversation regression tests. Demo isolation and browser
audio sample rates are explicitly preserved; telephone defaults stay at 8 kHz.

`deepgram` and `reduce-llm-deepgram` are earlier versions of this runtime. The old
Cartesia branch is not the deployment baseline. No Cartesia or direct OpenAI key is
required. Pipecat Cloud is a separate deployment; its deployed revision and secret
set cannot be verified until organization access is available.

## Verification

```sh
.venv/bin/python -m pytest demo/tests -q
npm run --prefix demo/web build
```

Server tests cover session ownership, replay rejection, origin checks, capacity, expiration, hourly limits, missing credentials, timeout handling, and summary filtering. Voice smoke tests use synthetic English and Spanish speech through the same WebSocket path as the browser; browser layout/start/finish paths are checked separately. A human microphone/speaker test remains useful: this headless WSL environment has no functioning audio output clock in Chromium. The native browser audio adapter initializes its context on the Start gesture and offers an explicit Enable audio fallback. DNS and TLS must be configured before browser microphone access on the public custom domain works.

## Transparency and sample data

The page offers a worked fictional example alongside a separate **Your conversation** view. County/city suggestions come from `reference_data.yml`. During an active conversation only, the page refreshes the explanation every two seconds; polling stops when the conversation ends, preserving idle sleep behavior.

The legal-issue panel shows the actual description, suggested LSC problem code, individual classifier votes and weights, and follow-up questions. The example mapping is explicitly illustrative. Provider scores are model-voting scores, not probabilities. Follow-up questions are deduplicated locally without another model call.

Income rows use the same `monthly_income_rows()` function as the validator. The panel displays each conversion, the truncated monthly total, household size, bundled poverty-guideline year, and the repository's 300% threshold. These are demonstration rules, not a promise of eligibility.

Conflict checks compare collected opposing-party full names against two **fictional** records in `demo/explain.py`. Try **Jordan Vale** to match **DEMO-1042**. Matching ignores case, punctuation and whitespace; it does not connect to LegalServer, establish a real conflict, or stop the intake. The fictional example is never inserted into session state.

The Fly demo uses Flux's integrated turn detection with a 0.6 end-of-turn
confidence threshold, an 800 ms silence fallback, and a 0.2-second external-turn
stop timeout. The branch's eager threshold does not start speculative LLM calls;
no eager-response speedup is claimed. The interface reports time from detected
turn completion to the bot-speaking event, not physical speaker playback.

## Deployment

- Public URL: https://voice.lemmalegal.com
- Fly app: `lemma-voice-demo`, Lemma organization (`lemma`), `iad` region.
- Runtime: Azure LLM plus Deepgram speech; provider credentials live in Fly secrets.
- Branch: `demo/azure-deepgram`; deploy from the repository root with the command above.
- SiteGround CNAME: `voice` → `kjx0meg.lemma-voice-demo.fly.dev`.
- SiteGround TXT: `_fly-ownership.voice` → `app-kjx0meg`.

The browser captures mono 16-bit PCM at 16 kHz and plays output at 24 kHz.
Keep capture, WebSocket metadata, and pipeline input rates aligned. The browser
requests echo cancellation and supports spoken interruption. The demo reinforces
answer provenance at every intake step and rejects recording calls before the
first nonempty visitor turn.

### Case-study presentation

The public page now covers the configured screening flow, a responsive component
and data-flow diagram, an illustrative LegalServer API handoff, and an interactive
operating-cost comparison. The handoff is documentation only: the demo never invokes
the LegalServer client. The fictional conflict search is explicitly
separate from the repository's adverse-party collection and export.

The calculator starts from the client workbook's 22,000 monthly minutes and 1,500
calls. It offers selectable STT, LLM, TTS, transport, and hosting, plus Bland Start,
Bland Build (with modeled BYOT carrier costs), and Telnyx Voice AI comparisons.
Monthly, annual, per-call, and per-minute totals include explicit assumptions and
can be exported as CSV. The workbook itself is not shipped in the public site.

`web/src/pricing-data.ts` holds the October 19, 2026 rate snapshot and primary
source URLs. `cost-model.ts` contains the pure billing calculations; `costs.ts`
binds controls and results. The default uses GPT-4.1 mini direct-API pricing in
place of the unverified PhoneLLM allowance. PhoneLLM remains labeled as a workbook
estimate, and direct OpenAI rates must not be mistaken for Azure deployment rates.

Key updates from the workbook:

- Deepgram opt-out no longer changes pricing (March 5, 2026 change). Current STT
  promotions can be switched to regular rates. Growth applies one shared $4,000
  annual prepaid minimum, allocated monthly; sufficient prepaid credits assumed.
- Azure East US 2 retail API confirms S1 STT at $1/audio hour (meter
  `3bd267f0-01d5-4348-ba67-70e2473f0c27`) and neural TTS at $15/M characters
  (`0f98e708-a16c-407b-8089-a0ed9e14ab49`). No S1 free tier is assumed.
- Google TTS deducts optional unused characters, not a flat dollar credit, with
  costs bounded at zero. Google STT V2 uses marginal volume tiers.
- Twilio Media Streams is $0.0044/min. Number rentals, REFER event fees, and
  Bland's transfer-minute fees are separate. SIP-only routes exclude upstream
  carrier costs unless entered. Daily's number rental is not assumed to be free.
- Telnyx Voice AI has a $0.05/min engine plus model/carrier costs. The calculator
  rounds the average call duration up to a minute for the engine; individual call
  rounding may be higher. Its $0.0064/min model figure is a provider example,
  not a token-based quote for the selected component model.
- LMNT has shut down. Rime Mist/Coda use current character rates; Arcana is labeled
  legacy. Deprecated OpenAI options are labeled. Flux TTS is a distinct product.
- Fly replaces the workbook's flat $150 allowance with machine uptime and stopped
  rootfs pricing. Multiple machines require changes to the demo's session routing.

Validation:

```sh
npm test --prefix demo/web
npm run build --prefix demo/web
```

The billing tests cover independent expected totals, tier boundaries, bounded free
allowances, Growth commitments, transfers, number rental, reserved hosting, zero
usage, and finite totals across provider choices. Browser checks exercise presets,
custom model rates, invalid volume, CSV download, and 320–1440px layouts.

### Brand assets and opening

The page follows lemmalegal.com's measured layout and typography: Livvic 300 body,
600 charcoal headings, snow background, rounded lavender panels, coral actions,
and the same mountain cover photo. The opening focuses on rural access to legal
aid; the technical workflow is available later in an expandable section.

Local assets in `web/public/brand/` come from:

- Official SVG logo and favicon: https://github.com/LemmaLegalConsulting/Logos,
  commit `0f02f4b0a700351bc075780cdc2ae1db72389370`.
- Livvic fonts: the main site's theme; OFL license included alongside the fonts.
- Mountain cover: the main site's `egor-myznik-p7Zch98meq8-unsplash` image,
  https://lemmalegal.com/wp-content/uploads/2025/06/egor-myznik-p7Zch98meq8-unsplash-1536x1028.jpg.

### Voice migration verification

The merge preserves no-SMS/no-persistence demo behavior and 16 kHz input / 24 kHz
output. Regression tests cover inactive voice playback signals, returning flush
probes, context installation before the interruptible opening, and cancellation
on browser disconnect. These address stalls found during actual English and
Spanish audio tests, beyond the branch's text-only coverage.
