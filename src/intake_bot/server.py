#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#


import sys

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pipecat.runner.types import WebSocketRunnerArguments
from starlette.responses import HTMLResponse

from intake_bot.bot import bot
from intake_bot.env_var import get_env_var, require_env_var
from intake_bot.security import generate_websocket_auth_code
from intake_bot.twilio import create_twiml, validate_webhook

logger.remove(0)
logger.add(sys.stderr, level=get_env_var("LOG_LEVEL", "INFO"))
# logger.add("server.log", level="TRACE")

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
    """
    Handle Twilio webhook and return TwiML with WebSocket streaming.
    """
    logger.debug("POST TwiML")

    valid_request = await validate_webhook(request=request)
    if not valid_request:
        raise HTTPException(status_code=403, detail="Webhook authentication failed")

    form_data = await request.form()
    domain = require_env_var("DOMAIN")
    url = f"""wss://{domain}/ws"""
    body_data = dict(
        caller_phone_number=form_data.get("From"),
        websocket_auth_code=generate_websocket_auth_code(call_id=form_data.get("CallSid")),
    )

    content = create_twiml(
        url=url,
        body_data=body_data,
    )
    return HTMLResponse(content=content, media_type="application/xml")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Handle WebSocket connections for inbound calls.
    """
    logger.info("Started call via websocket")
    await websocket.accept()

    runner_args = WebSocketRunnerArguments(websocket=websocket)
    runner_args.handle_sigint = False

    result = await bot(runner_args)

    if result and isinstance(result, dict) and "code" in result:
        logger.debug(f"""Closing websocket with code: {result["code"]}""")
        await websocket.close(code=result["code"])
