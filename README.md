# VLAS Intake Bot

This project has two local entrypoints:

- `bot.py` for Daily and Pipecat Cloud runtime flows
- `server.py` for local websocket testing

The production telephony path uses Daily PSTN dial-in. Local websocket testing does not use Twilio.

## Intake-bot control flow diagram

```mermaid
flowchart TD
    start[Start Intake Screening]
    get_language[Select Language]
    get_phone[Get Phone Number]
    get_phone_type[Get Phone Type]
    get_name[Get Name]
    location{Service Area}
    case_type{Case Type}
    case_desc_clarify[Clarify Case Description]
    adverse_parties[Get Adverse Parties]
    dv[Check Domestic Violence]
    household[Get Household Composition]
    household_confirm{Confirm Household Counts}
    household_members[Confirm Household Members]
    income{Income Check}
    confirm_income[Confirm Over Income Limit]
    receives_benefits{Receives Benefits?}
    assets_cash[Cash Accounts]
    assets_invest[Investments]
    assets_other[Other Property]
    assets_list[Confirm Asset List]
    assets_over_limit[Confirm Over Asset Limit]
    citizenship[Get Citizenship]
    ssn[Get SSN Last 4]
    dob[Get Date of Birth]
    addl_names[Get Additional Names]
    address[Get Address]
    thankyou[Thank You / End]
    referral_ineligible[Ineligible Referral]
    referral_general[General Referral]
    decide_delivery{Choose Delivery}
    sms_attempt[SMS Attempt]
    fallback_phone[Fallback: Phone Read]
    end[(End Call)]
    persist[LegalServer Persist]
    persist_outcome{Persistence Result}

    start --> get_language
    get_language --> get_phone
    get_phone --> get_phone_type
    get_phone_type --> get_name
    get_name --> location

    location -- Exact match served --> case_type
    location -- Suggested --> location_confirm{Confirm?}
    location_confirm -- Yes --> case_type
    location_confirm -- No --> location
    location -- Ambiguous --> location_pick{Pick from ≤2}
    location_pick -- Selected --> case_type
    location_pick -- Neither --> location
    location -- Unserved/out-of-state --> referral_ineligible
    location -- Unknown --> location_retry{≤2 retries}
    location_retry -- Retry --> location
    location_retry -- Exhausted --> referral_general

    case_type -- Eligible --> adverse_parties
    case_type -- Needs clarification --> case_desc_clarify
    case_desc_clarify -.-> case_type
    case_type -- Ineligible --> referral_ineligible

    adverse_parties --> dv
    dv --> household
    household --> household_confirm
    household_confirm -- Yes --> household_members
    household_confirm -- Re-enter counts --> household
    household_members --> income

    income -- Eligible --> receives_benefits
    income -- Over limit --> confirm_income
    confirm_income -- Continue --> receives_benefits
    confirm_income -- Refer --> referral_general

    receives_benefits -- Yes --> citizenship
    receives_benefits -- No --> assets_cash
    assets_cash --> assets_invest
    assets_invest --> assets_other
    assets_other --> assets_list
    assets_list -- Eligible --> citizenship
    assets_list -- Over limit --> assets_over_limit
    assets_over_limit -- Continue --> citizenship
    assets_over_limit -- Refer --> referral_general

    citizenship --> ssn
    ssn --> dob
    dob --> addl_names
    addl_names --> address
    address --> thankyou

    referral_ineligible --> decide_delivery
    referral_general --> decide_delivery
    decide_delivery -- text --> sms_attempt
    sms_attempt -- Accepted --> end
    sms_attempt -- Failed --> fallback_phone
    fallback_phone --> end
    decide_delivery -- phone --> end

    thankyou --> end

    subgraph Pipeline_Finalization
        end --> persist
        persist --> persist_outcome
        persist_outcome -- Complete/Degraded/Skipped --> log[Log Result]
        persist_outcome -- Failed --> log
    end
```

## Federal Poverty Scale

The Federal Poverty Scale data is static and will need to be manually updated each year. The included json file `federal_poverty_scale.json` comes from the [docassemble-PovertyScale](https://github.com/SuffolkLITLab/docassemble-PovertyScale?tab=readme-ov-file) project and the [file itself is here](https://github.com/SuffolkLITLab/docassemble-PovertyScale/blob/main/docassemble/PovertyScale/data/sources/federal_poverty_scale.json).

## Development

1. Set up:

    ```bash
    uv sync --group dev
    ```

1. Activate the Python `.venv` (depends on your system)

1. Copy and rename the `.env.dist` file to `.env` and fill it out.

1. Run the local websocket server for browser or automated client testing:

    ```bash
    uv run uvicorn server:app --host 127.0.0.1 --port 8765 --reload
    ```

1. Run the restored browser websocket client:

    ```bash
    npm run --prefix ./client/typescript dev -- --port 5174
    ```

1. Run the Python websocket test client:

    ```bash
    python ./client/python/client.py
    ```

1. Production on Pipecat Cloud:

    - Deploy this bot to Pipecat Cloud using the `pcc-deploy.toml`.

    Pipecat Cloud handles inbound dial-in webhook lifecycle and room creation for PSTN calls.

Azure OpenAI notes:

- The bot LLM now uses Azure OpenAI via your deployment name in `AZURE_LLM_MODEL`.
- `AZURE_LLM_ENDPOINT` should be the Azure resource root such as `https://your-resource-name.openai.azure.com`, not an API path like `/openai/v1/`.
- The bot STT now uses Deepgram Flux via `DEEPGRAM_API_KEY` and defaults to `DEEPGRAM_STT_MODEL=flux-general-multi`.
- The bot TTS uses `DeepgramFluxTTSService` with token streaming and language-scoped Flux voice env vars such as `DEEPGRAM_TTS_VOICE_EN` and `DEEPGRAM_TTS_VOICE_ES`. Both default to `flux-alexis-en`; set the Spanish override only when your Deepgram account has access to a Spanish Flux TTS voice.
- Flux owns turn boundaries in both the bot and the scripted websocket client. The pipeline uses explicit external start/stop strategies instead of Pipecat-managed SmartTurn defaults.
- Silero VAD is still enabled as an optional local signal path for tuning and observability, but it does not decide turn completion for Flux.
- After the caller chooses Spanish, the bot narrows Flux STT language hints to Spanish and switches TTS to the configured Flux voice for the remainder of the call.
- The classifier's Azure providers default to using `AZURE_LLM_MODEL` unless you set model-specific deployment overrides.

The root `server.py` file is only for local websocket testing and is intentionally not copied into the Docker image.

1. Run Pipecat Tail (monitor):

   - ⚡ Option A: Pipeline runner

        Enable Pipecat Tail directly in your server console by setting `ENABLE_TAIL_RUNNER=TRUE` in your `.env`.

   - 🏠 Option B: Standalone app

        You can also start Tail as a standalone application. This lets you connect to a running session, whether local or remote.

        Enable Pipecat Tail connectivity by setting `ENABLE_TAIL_OBSERVER=TRUE` in your `.env`.

        Install the Pipecat-CLI:

        ```bash
        uv tool install pipecat-ai-cli
        ```

        Then start the app:

        ```bash
        pipecat tail [--url URL]
        ```

        By default, it will connect to `ws://localhost:9292`.

1. Run Pipecat Whisker (debugger, separate from Daily dial-in):

   - 🌐 Option A: Use the hosted UI (Recommended)

        1. Expose your local server with ngrok (required for Whisker's
           hosted UI to reach your machine):

            ```bash
            ngrok http 9090
            ```

        1. Copy the ngrok URL (e.g., `your-ngrok-url.ngrok.io`)

        1. Open the hosted Whisker UI: [https://whisker.pipecat.ai/](https://whisker.pipecat.ai/)

        1. Connect to your bot:
            - In the WebSocket URL field, enter: `wss://your-ngrok-url.ngrok.io`
            - Click connect

   - 🏠 Option B: Run the UI locally

        1. Clone the repository:

            ```bash
            git clone https://github.com/pipecat-ai/whisker.git
            ```

        1. Start the UI:

            ```bash
            cd whisker/ui
            npm install
            npm run dev
            ```

        1. Connect to [http://localhost:5173](http://localhost:5173)

            The UI will automatically connect to `ws://localhost:9090` by default.

   Note: Whisker is a Pipecat debugging tool, not related to Daily
   PSTN dial-in. The ngrok usage above is for Whisker connectivity
   only.
