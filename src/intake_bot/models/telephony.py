from pydantic import BaseModel


class DailyCallMetadata(BaseModel):
    call_id: str
    caller_phone_number: str
