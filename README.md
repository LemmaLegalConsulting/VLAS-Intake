# VLAS Intake Bot

This project is a Pipecat-AI/FastAPI-based intake-bot that integrates with Twilio to provide real-time phone-based communication.

## Intake-bot control flow diagram:

```mermaid
flowchart TD
    start[Start Intake Screening]
    get_phone_name[Get Phone, Name]
    location{Location for caller, problem}
    exit[(Redirect or referral)]
    case_type{Case type}
    check_conflict{Conflict check}
    check_income{Income check}
    check_assets{Assets check}
    check_citizenship{Citizenship eligible}
    check_emergency{Qualifying Emergency}
    conduct_interview{Conduct Interview}


    start --> get_phone_name
    get_phone_name --> location
    location -- Neither --> exit
    location -- Either --> case_type
    case_type -- Handled --> check_conflict
    case_type -- Not handled --> exit
    check_conflict -- Conflict --> exit
    check_conflict -- No conflict --> check_income
    check_income -- Under limit --> check_assets
    check_income -- Over limit --> exit
    check_assets -- Eligible --> check_citizenship
    check_assets -- Ineligible --> exit
    check_citizenship -- Eligible --> check_emergency
    check_citizenship -- Ineligible --> exit
    check_emergency -- Emergency [expedited] --> conduct_interview
    check_emergency -- Non-emergency --> conduct_interview

```

## Secret Key
You will need to provide a 64 character hexidecimal secret key. You can generate one with the following methods:

Using openssl:
```
openssl rand −hex 32
```

Using python:
```python
import secrets
print(secrets.token_hex(32))
```

## Development

1. Set up:
```
uv sync --group dev
```

2. Activate the Python `.venv` (depends on your system)

3. Run the server with reload:
```
granian intake_bot.server:app --interface asgi --host 0.0.0.0 --port 8765 --reload --reload-paths ./src/intake_bot
```

4. Install the local websocket test client:
```
cd ./client/typescript
npm install
```

5. Run the local websocket test client (from project root):
```
npm run --prefix ./client/typescript dev -- --port 5173
```

6. Install the Whisker (Pipecat) debugger:
```
cd ./debug/ui
npm install
```

7. Run the Whisker (Pipecat) debugger (from project root):
```
npm run --prefix ./debug/ui dev -- --port 5174
```
