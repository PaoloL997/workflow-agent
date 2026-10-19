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
from pydantic_ai import DeferredToolRequests, DeferredToolResults

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
def confirm(body: ConfirmRequest, authorization: str = Header()) -> ChatResponse:
    """Resume a paused run with the user's decisions on the pending actions.

    `approvals` maps each tool_call_id to True (execute) or False (reject).
    Which tools run, and with which arguments, comes from the stored history,
    never from the request. The run may pause again with new pending actions.
    """
    token = token_from(authorization)

    with make_client(token) as client:
        user = resolve_user(client)

        history = store.load(body.conversation_id, user)
        if history is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        deps = AgentDeps(user=user, client=client)
        result = agent.run_sync(
            message_history=history,
            deferred_tool_results=DeferredToolResults(approvals=body.approvals),
            deps=deps,
        )

    store.save(body.conversation_id, result.all_messages())
    return to_response(body.conversation_id, result.output)