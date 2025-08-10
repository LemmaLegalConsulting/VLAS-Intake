#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import json
import os
from functools import wraps

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import HTMLResponse
from twilio.request_validator import RequestValidator

from .bot import run_bot
from .globals import ROOT_DIR

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

load_dotenv(override=True)

# In-memory storage for Twilio signatures
TWILIO_SIGNATURES = dict()
INBOUND_PHONE_NUMBER = dict(ws_mock_call_sid="8665345243")


def validate_twilio_request(f):
    """Validates that incoming requests genuinely originated from Twilio"""

    @wraps(f)
    async def decorated_function(request: Request, *args, **kwargs):
        validator = RequestValidator(os.getenv("TWILIO_AUTH_TOKEN"))

        # https://community.fly.io/t/redirect-uri-is-http-instead-of-https/6671/6
        url = f"""{os.getenv("PROTOCOL", "https")}://{os.getenv("DOMAIN")}/"""
        print("url:", url)
        form_data = await request.form()
        print(form_data)
        twilio_signature = request.headers.get("X-TWILIO-SIGNATURE", "")
        valid_request = validator.validate(uri=url, params=form_data, signature=twilio_signature)
        call_sid = form_data.get("CallSid")

        if valid_request and twilio_signature and call_sid:
            TWILIO_SIGNATURES[call_sid] = twilio_signature
            INBOUND_PHONE_NUMBER[call_sid] = form_data["From"]
            print(f"Stored Twilio signature for CallSid: {call_sid}")
            return await f(request, *args, **kwargs)
        else:
            raise HTTPException(status_code=403, detail="Webhook authentication failed")

    return decorated_function


@app.post("/")
@validate_twilio_request
async def start_call(request: Request):
    print("POST TwiML")

    with open(f"""{ROOT_DIR}/__assets__/streams.xml""", "r") as file:
        xml_content = file.read()

    xml_content = xml_content.replace("{{ DOMAIN }}", os.getenv("DOMAIN"))

    return HTMLResponse(content=xml_content, media_type="application/xml")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    start_data = websocket.iter_text()
    await start_data.__anext__()
    call_data = json.loads(await start_data.__anext__())
    print(call_data, flush=True)

    call_sid = call_data["start"]["callSid"]
    stream_sid = call_data["start"]["streamSid"]
    caller_phone_number = INBOUND_PHONE_NUMBER[call_sid]

    if (TWILIO_SIGNATURES.get(call_sid)) or (
        os.getenv("ALLOW_TEST_CLIENT") == "TRUE" and call_sid == "ws_mock_call_sid"
    ):
        print(f"""WebSocket connection accepted for CallSid: {call_sid}""")
        await run_bot(websocket, stream_sid, call_sid, caller_phone_number)
    else:
        print(f"""WebSocket connection denied for CallSid: {call_sid}""")
        await websocket.close(code=1008)  # Close WebSocket with error code
        return
