#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import json
import os
import sys

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from starlette.responses import HTMLResponse

from .bot import run_bot
from .security import generate_websocket_auth_code, verify_websocket_auth_code
from .twilio import create_twiml, validate_webhook

load_dotenv(override=True)
logger.remove(0)
logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/")
async def start_call(request: Request):
    logger.debug("POST TwiML")

    valid_request = await validate_webhook(request=request)
    if not valid_request:
        raise HTTPException(status_code=403, detail="Webhook authentication failed")

    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    url = f"""wss://{os.getenv("DOMAIN")}/ws"""
    caller_phone_number = form_data.get("From")
    websocket_auth_code = generate_websocket_auth_code(call_sid)

    content = create_twiml(
        url=url,
        caller_phone_number=caller_phone_number,
        websocket_auth_code=websocket_auth_code,
    )
    return HTMLResponse(content=content, media_type="application/xml")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    logger.info("Started call via websocket")
    await websocket.accept()
    start_data = websocket.iter_text()
    await start_data.__anext__()
    call_data = json.loads(await start_data.__anext__())
    logger.debug(str(call_data))

    call_sid = call_data["start"]["callSid"]
    stream_sid = call_data["start"]["streamSid"]

    if os.getenv("ALLOW_TEST_CLIENT") == "TRUE" and call_sid == "ws_mock_call_sid":
        caller_phone_number = "8665345243"
        call_is_valid = True
    else:
        websocket_auth_code = call_data["start"]["customParameters"]["websocket_auth_code"]
        caller_phone_number = call_data["start"]["customParameters"]["caller_phone_number"]
        call_is_valid = verify_websocket_auth_code(
            call_sid=call_sid,
            received_code=websocket_auth_code,
        )

    if call_is_valid:
        logger.debug(f"""WebSocket connection accepted for CallSid: {call_sid}""")
        await run_bot(websocket, stream_sid, call_sid, caller_phone_number)
    else:
        logger.debug(f"""WebSocket connection denied for CallSid: {call_sid}""")
        await websocket.close(code=1008)
        return
