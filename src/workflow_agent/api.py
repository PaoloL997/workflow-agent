"""HTTP entry point for the Workflow chat widget.

Two endpoints mirror the two halves of an agent run:
- POST /chat    sends a user message; the run either ends with a reply or
                pauses with a list of actions awaiting approval.
- POST /confirm sends the user's decisions on those actions and resumes the run.

Conversation state lives in the store, keyed by conversation_id and owned by
the user resolved from the Workflow token. The token itself is never stored:
it travels in the Authorization header of every request and is forwarded to
Workflow for the duration of that request only.
"""

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic_ai import DeferredToolRequests, DeferredToolResults, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse, RetryPromptPart, ToolReturnPart

from workflow_agent.agent import agent
from workflow_agent.config import config
from workflow_agent.deps import AgentDeps
from workflow_agent.schemas import ChatRequest, ChatResponse, ConfirmRequest, PendingAction
from workflow_agent.store.history import ConversationStore

app = FastAPI(title="workflow-agent")
store = ConversationStore(config.store_path)


def make_client(token: str) -> httpx.Client:
    """Build a Workflow API client authenticated as the user who owns `token`.

    Created per request and closed by the caller (use it in a `with` block).
    """
    return httpx.Client(
        base_url=config.workflow_base_url,
        headers={"Authorization": f"Token {token}"},
        timeout=30,
    )


def token_from(authorization: str) -> str:
    """Extract the token from an `Authorization: Token <key>` header.

    Raises HTTP 401 if the scheme is wrong or the key is missing.
    """
    scheme, _, token = authorization.partition(" ")
    if scheme != "Token" or not token:
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return token


def to_response(conversation_id: str, output: str | DeferredToolRequests) -> ChatResponse:
    """Translate the outcome of a run into the HTTP response.

    A `str` output means the run finished: it goes in `reply`. A
    `DeferredToolRequests` output means the run is paused on write tools: each
    pending call goes in `pending`, with the tool_call_id the widget must send
    back to /confirm.
    """
    if isinstance(output, DeferredToolRequests):
        return ChatResponse(
            conversation_id=conversation_id,
            pending=[
                PendingAction(
                    tool_call_id=call.tool_call_id,
                    tool_name=call.tool_name,
                    args=call.args_as_dict(),
                )
                for call in output.approvals
            ],
        )
    return ChatResponse(conversation_id=conversation_id, reply=output)


def pending_call_ids(history: list[ModelMessage]) -> list[str]:
    """Return the tool_call_ids a paused run is waiting on, in call order.

    They are the tool calls of the last model response that have no result
    (ToolReturnPart or RetryPromptPart) in the requests after it: calls run in
    the same step as the paused ones, such as reads, already have one. This is
    the set Pydantic AI requires in DeferredToolResults to resume the run. An
    empty list means the run is not paused.
    """
    answered: set[str] = set()
    for message in reversed(history):
        if isinstance(message, ModelResponse):
            return [
                call.tool_call_id
                for call in message.tool_calls
                if call.tool_call_id not in answered
            ]
        answered.update(
            part.tool_call_id
            for part in message.parts
            if isinstance(part, ToolReturnPart | RetryPromptPart)
        )
    return []


def resolve_user(client: httpx.Client) -> str:
    """Ask Workflow who owns the token in `client` (GET /api/agent/me/).

    Raises HTTP 401 if Workflow rejects the token. This is the only source of
    the user identity: nothing sent by the widget is trusted for it.
    """
    response = client.get("/api/agent/me/")
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid token")
    response.raise_for_status()
    return response.json()["username"]


@app.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, authorization: str = Header()) -> ChatResponse:
    """Send a user message to the agent.

    Without `conversation_id` a new conversation is created; with it, the
    stored history is loaded (404 if missing or owned by another user) and
    the message is appended. The run may end with a reply or pause with
    pending actions.
    """
    token = token_from(authorization)

    with make_client(token) as client:
        user = resolve_user(client)

        conversation_id = body.conversation_id or store.create(user)
        history = store.load(conversation_id, user)
        if history is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        deps = AgentDeps(user=user, client=client)
        result = agent.run_sync(body.message, deps=deps, message_history=history)

    store.save(conversation_id, result.all_messages())
    return to_response(conversation_id, result.output)


@app.post("/confirm", response_model=ChatResponse)
def confirm(body: ConfirmRequest, authorization: str = Header()) -> ChatResponse | JSONResponse:
    """Resume a paused run with the user's decisions on the pending actions.

    `approvals` maps each tool_call_id to True (execute) or False (reject).
    Which tools run, and with which arguments, comes from the stored history,
    never from the request. The run may pause again with new pending actions.

    The ids in `approvals` must be exactly those of the pending actions: 409
    if the conversation has none, 400 (with `expected` and `received`) if
    some are missing or unknown.
    """
    token = token_from(authorization)

    with make_client(token) as client:
        user = resolve_user(client)

        history = store.load(body.conversation_id, user)
        if history is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        expected = pending_call_ids(history)
        if not expected:
            raise HTTPException(status_code=409, detail="No pending actions for this conversation")
        received = list(body.approvals)
        if set(received) != set(expected):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Pending actions do not match",
                    "expected": expected,
                    "received": received,
                },
            )

        deps = AgentDeps(user=user, client=client)
        try:
            result = agent.run_sync(
                message_history=history,
                deferred_tool_results=DeferredToolResults(approvals=body.approvals),
                deps=deps,
            )
        except UserError as exc:
            # Safety net: Pydantic AI rejects results that do not match the
            # history with UserError. The check above should catch that first.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    store.save(body.conversation_id, result.all_messages())
    return to_response(body.conversation_id, result.output)
