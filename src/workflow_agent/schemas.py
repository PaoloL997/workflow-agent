from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class ConfirmRequest(BaseModel):
    conversation_id: str
    approvals: dict[str, bool]


class PendingAction(BaseModel):
    tool_call_id: str
    tool_name: str
    args: dict
    summary: str


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str | None = None
    pending: list[PendingAction] = []
