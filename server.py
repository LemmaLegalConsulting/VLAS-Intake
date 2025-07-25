#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import argparse
import json
import os

import uvicorn
from bot import run_bot
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import HTMLResponse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

load_dotenv(override=True)

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


@app.post("/")
async def start_call():
    print("POST TwiML")
    with open("templates/streams.xml", "r") as file:
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
    stream_sid = call_data["start"]["streamSid"]
    call_sid = call_data["start"]["callSid"]
    print("WebSocket connection accepted")
    await run_bot(websocket, stream_sid, call_sid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VLAS Intake-Bot Server")
    parser.add_argument("--reload", action="store_true", help="Reload code on change")
    config = parser.parse_args()

    uvicorn.run("server:app", host="0.0.0.0", port=8765, reload=config.reload)
