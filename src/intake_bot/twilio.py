import os

from dotenv import load_dotenv
from fastapi import Request
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from .server import logger

load_dotenv(override=True)


def create_twiml(url: str, **kwargs) -> str:
    response = VoiceResponse()
    connect = Connect()
    stream = Stream(url=url)
    for name, value in kwargs.items():
        stream.parameter(name=name, value=value)
    connect.append(stream)
    response.append(connect)
    xml = str(response)
    logger.debug(xml)
    return xml


async def validate_webhook(request: Request) -> bool:
    # https://community.fly.io/t/redirect-uri-is-http-instead-of-https/6671/6
    url = f"""{os.getenv("PROTOCOL", "https")}://{os.getenv("DOMAIN")}/"""
    form_data = await request.form()
    twilio_signature = request.headers.get("X-TWILIO-SIGNATURE", "")
    validator = RequestValidator(os.getenv("TWILIO_AUTH_TOKEN"))
    return validator.validate(uri=url, params=form_data, signature=twilio_signature)
