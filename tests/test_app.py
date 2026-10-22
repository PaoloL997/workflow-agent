"""Tests for the HTTP API.

Workflow is simulated with httpx.MockTransport (FakeWorkflow), the model with
a FunctionModel that returns a scripted list of responses, and the store is a
SQLite file under tmp_path.
"""

import threading
import time
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from workflow_agent import app as app_module
from workflow_agent.app import with_context
from workflow_agent.conversations import ConversationStore
from workflow_agent.summaries import summarize

AUTH = {"Authorization": "Token test-token"}

JOB = "22155"
VENDOR_DOC = "22155-01-QZ03"
DOCUMENT = {
    "id": 10,
    "job": JOB,
    "vendor_doc": VENDOR_DOC,
    "client_doc_no": "CL-0001",
    "doc_title": "Piping layout",
    "latest_rev_no": 1,
    "latest_rev_label": "B",
}
STATUSES = [
    {"id": 1, "lettera": "A", "nome": "Approvato", "crea_nuova_rev": False},
    {"id": 3, "lettera": "C", "nome": "Commentato", "crea_nuova_rev": True},
]

# Labels of a job with the custom sequence "1,2,3,D", which goes on from its
# last label (D → E): the revision with rev_no n is labelled SEQUENCE[n], so
# "3" is rev_no 2 and "D" is rev_no 3.
SEQUENCE = "123DE"


def revision(rev_no: int, letter: str, label: str, rev_label: str = "") -> dict:
    """A revision of DOCUMENT as returned by GET api/documenti/<id>/revisioni/.

    `letter` and `label` are its customer response. `rev_label` is the label
    the user sees: by default the letter of rev_no (0 → A), as in a job
    without a custom sequence.
    """
    return {
        "id": 100 + rev_no,
        "rev_no": rev_no,
        "rev_let": "ABCDE"[rev_no],
        "rev_label": rev_label or "ABCDE"[rev_no],
        "int_status_eff_label": "Emesso",
        "ext_status_lettera": letter,
        "ext_status_label": label,
        "dis_act_date": "2026-09-01",
        "rec_act_date": "2026-09-15",
        "crea_nuova_rev": letter == "C",
    }


class FakeWorkflow:
    """In-memory stand-in for the Workflow REST API.

    `fail` maps a path to the status code (or exception) to answer with
    instead of the normal response; `requests` records every call.
    """

    def __init__(self) -> None:
        self.revisions = [revision(0, "C", "Commentato"), revision(1, "C", "Commentato")]
        self.fail: dict[str, int | Exception] = {}
        self.can_write = True
        self.requests: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        failure = self.fail.get(path)
        if isinstance(failure, Exception):
            raise failure
        if failure is not None:
            return httpx.Response(failure, json={"error": "simulated failure"})

        if path == "/api/agent/me/":
            return httpx.Response(200, json={"username": "paolo", "can_write": self.can_write})
        if path == f"/api/commesse/{JOB}/documenti/":
            return httpx.Response(200, json={"documenti": [DOCUMENT]})
        if path == f"/api/documenti/{DOCUMENT['id']}/revisioni/":
            return httpx.Response(200, json={"revisioni": self.revisions})
        if path == "/api/stati-esterni/":
            return httpx.Response(200, json={"stati": STATUSES})
        if request.method == "POST" and path.endswith("/risposta-cliente/"):
            return httpx.Response(
                200, json={"successiva": {"azione": "nessuna", "revisione": None}}
            )
        return httpx.Response(404, json={"error": f"Not found: {path}"})

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url="http://workflow.test", transport=httpx.MockTransport(self.handler)
        )


def scripted(*responses: ModelResponse) -> FunctionModel:
    """A model that answers each request with the next of `responses`."""
    queue = list(responses)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return queue.pop(0)

    return FunctionModel(respond)


def change_status_call(
    tool_call_id: str = "call-1", new_letter: str = "A", revision: str = "B"
) -> ToolCallPart:
    return ToolCallPart(
        "change_external_status",
        {"job": JOB, "vendor_doc": VENDOR_DOC, "revision": revision, "new_letter": new_letter},
        tool_call_id=tool_call_id,
    )


def reply(text: str = "Fatto.") -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


@pytest.fixture
def workflow(monkeypatch: pytest.MonkeyPatch) -> FakeWorkflow:
    fake = FakeWorkflow()
    monkeypatch.setattr(app_module, "make_client", lambda token: fake.client())
    return fake


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, tmp_path, workflow: FakeWorkflow
) -> Iterator[TestClient]:
    store = ConversationStore(str(tmp_path / "conversations.db"))
    monkeypatch.setattr(app_module, "store", store)
    yield TestClient(app_module.app)
    store.conn.close()


def chat(client: TestClient, message: str = "La rev B è approvata") -> dict:
    response = client.post("/chat", json={"message": message}, headers=AUTH)
    assert response.status_code == 200
    return response.json()


def confirm(client: TestClient, conversation_id: str, approvals: dict[str, bool]) -> httpx.Response:
    return client.post(
        "/confirm",
        json={"conversation_id": conversation_id, "approvals": approvals},
        headers=AUTH,
    )


@pytest.mark.parametrize(
    "approvals",
    [
        {"bogus": True},
        {"call-1": True, "bogus": False},
        {},
    ],
    ids=["unknown", "extra", "missing"],
)
def test_confirm_with_mismatched_ids_returns_400(client: TestClient, approvals: dict) -> None:
    with app_module.agent.override(model=scripted(ModelResponse(parts=[change_status_call()]))):
        conversation_id = chat(client)["conversation_id"]
        response = confirm(client, conversation_id, approvals)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Pending actions do not match",
        "expected": ["call-1"],
        "received": list(approvals),
    }


def test_confirm_on_conversation_ended_with_reply_returns_409(client: TestClient) -> None:
    with app_module.agent.override(model=scripted(reply("Ciao!"))):
        conversation_id = chat(client, "ciao")["conversation_id"]
        response = confirm(client, conversation_id, {"call-1": True})

    assert response.status_code == 409
    assert response.json() == {"detail": "No pending actions for this conversation"}


def test_confirm_on_new_conversation_returns_409(client: TestClient) -> None:
    conversation_id = app_module.store.create("paolo")

    response = confirm(client, conversation_id, {"call-1": True})

    assert response.status_code == 409
    assert response.json() == {"detail": "No pending actions for this conversation"}


def test_confirm_with_matching_ids_resumes_run(client: TestClient, workflow: FakeWorkflow) -> None:
    # A read run in the same step as the write already has a result in the
    # history: only the write is pending.
    read = ToolCallPart(
        "get_revision",
        {"job": JOB, "vendor_doc": VENDOR_DOC, "revision": "B"},
        tool_call_id="read-1",
    )
    model = scripted(ModelResponse(parts=[read, change_status_call()]), reply())
    with app_module.agent.override(model=model):
        body = chat(client)
        assert [p["tool_call_id"] for p in body["pending"]] == ["call-1"]

        response = confirm(client, body["conversation_id"], {"call-1": True})
        assert response.status_code == 200
        assert response.json()["reply"] == "Fatto."
        assert ("POST", "/api/revisioni/101/risposta-cliente/") in workflow.requests

        # The call has run: confirming it again finds nothing pending.
        response = confirm(client, body["conversation_id"], {"call-1": True})
        assert response.status_code == 409


def test_confirm_maps_user_error_to_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_module.agent.override(model=scripted(ModelResponse(parts=[change_status_call()]))):
        conversation_id = chat(client)["conversation_id"]
        # Let a mismatch slip past the check to exercise the safety net.
        monkeypatch.setattr(app_module, "pending_call_ids", lambda history: ["bogus"])
        response = confirm(client, conversation_id, {"bogus": True})

    assert response.status_code == 400
    assert "bogus" in response.json()["detail"]


def test_chat_on_paused_conversation_rejects_pending(
    client: TestClient, workflow: FakeWorkflow
) -> None:
    model = scripted(ModelResponse(parts=[change_status_call()]), reply("Ok, non la cambio."))
    with app_module.agent.override(model=model):
        conversation_id = chat(client)["conversation_id"]
        response = client.post(
            "/chat",
            json={"message": "No, aspetta", "conversation_id": conversation_id},
            headers=AUTH,
        )

    assert response.status_code == 200
    assert response.json()["reply"] == "Ok, non la cambio."
    assert ("POST", "/api/revisioni/101/risposta-cliente/") not in workflow.requests

    # The rejection and the new message reach the model in the same request.
    history = app_module.store.load(conversation_id, "paolo")
    [denial, prompt] = history[-2].parts
    assert isinstance(denial, ToolReturnPart)
    assert denial.tool_call_id == "call-1"
    assert denial.content == app_module.SUPERSEDED.message
    assert isinstance(prompt, UserPromptPart)
    assert prompt.content == "No, aspetta"


FALLBACK = (
    f"change_external_status(job='{JOB}', vendor_doc='{VENDOR_DOC}', revision='B', new_letter='A')"
)


def test_chat_pending_has_summary(client: TestClient) -> None:
    with app_module.agent.override(model=scripted(ModelResponse(parts=[change_status_call()]))):
        body = chat(client)

    [pending] = body["pending"]
    assert pending["tool_call_id"] == "call-1"
    assert pending["summary"] == (
        f"Documento {VENDOR_DOC}, rev B: risposta cliente C (Commentato) → A (Approvato)"
    )


@pytest.mark.parametrize(
    ("current", "new_letter", "next_exists", "suffix"),
    [
        (("A", "Approvato"), "C", False, "; verrà creata la revisione successiva"),
        (("A", "Approvato"), "C", True, ""),
        (("C", "Commentato"), "A", True, "; la rev C verrà eliminata se vuota"),
        (("C", "Commentato"), "A", False, ""),
    ],
    ids=["creates-next", "next-already-exists", "deletes-next", "no-next"],
)
def test_change_external_status_summary_describes_next_revision(
    workflow: FakeWorkflow,
    current: tuple[str, str],
    new_letter: str,
    next_exists: bool,
    suffix: str,
) -> None:
    workflow.revisions = [revision(0, "C", "Commentato"), revision(1, *current)]
    if next_exists:
        workflow.revisions.append(revision(2, "", ""))
    new_name = {"A": "Approvato", "C": "Commentato"}[new_letter]
    args = change_status_call(new_letter=new_letter).args_as_dict()

    with workflow.client() as http:
        summary = summarize("change_external_status", args, http)

    assert summary == (
        f"Documento {VENDOR_DOC}, rev B: risposta cliente {current[0]} ({current[1]})"
        f" → {new_letter} ({new_name}){suffix}"
    )


def test_change_external_status_summary_names_revisions_by_label(workflow: FakeWorkflow) -> None:
    # rev "D" is rev_no 3: the summary must name it, and the next one (rev_no
    # 4, "E"), by label, never by rev_no.
    workflow.revisions = [revision(n, "C", "Commentato", SEQUENCE[n]) for n in range(5)]
    args = change_status_call(revision="D").args_as_dict()

    with workflow.client() as http:
        summary = summarize("change_external_status", args, http)

    assert summary == (
        f"Documento {VENDOR_DOC}, rev D: risposta cliente C (Commentato) → A (Approvato)"
        "; la rev E verrà eliminata se vuota"
    )


def test_summary_of_unknown_tool_falls_back(workflow: FakeWorkflow) -> None:
    with workflow.client() as http:
        summary = summarize("archive_document", {"job": JOB, "rev_no": 1}, http)

    assert summary == f"archive_document(job='{JOB}', rev_no=1)"
    assert workflow.requests == []


@pytest.mark.parametrize(
    ("path", "failure"),
    [
        (f"/api/commesse/{JOB}/documenti/", 404),
        (f"/api/documenti/{DOCUMENT['id']}/revisioni/", 500),
        ("/api/stati-esterni/", 503),
        (f"/api/commesse/{JOB}/documenti/", httpx.ReadTimeout("timed out")),
    ],
    ids=["job-not-found", "revisions-500", "statuses-503", "timeout"],
)
def test_chat_summary_falls_back_when_read_fails(
    client: TestClient, workflow: FakeWorkflow, path: str, failure: int | Exception
) -> None:
    workflow.fail[path] = failure
    with app_module.agent.override(model=scripted(ModelResponse(parts=[change_status_call()]))):
        response = client.post("/chat", json={"message": "La rev B è approvata"}, headers=AUTH)

    assert response.status_code == 200
    [pending] = response.json()["pending"]
    assert pending["tool_call_id"] == "call-1"
    assert pending["summary"] == FALLBACK


CONTEXT_LINE = f"[Contesto: l'utente sta guardando la commessa {JOB}]"


def first_user_prompt(conversation_id: str) -> str:
    """The text of the first user message stored for `conversation_id`."""
    history = app_module.store.load(conversation_id, "paolo")
    assert history is not None
    [request] = [m for m in history[:1] if isinstance(m, ModelRequest)]
    [prompt] = [p for p in request.parts if isinstance(p, UserPromptPart)]
    assert isinstance(prompt.content, str)
    return prompt.content


@pytest.mark.parametrize(
    "context", [None, {}, {"altro": "y"}], ids=["none", "empty", "unknown-key"]
)
def test_with_context_without_job_leaves_message_unchanged(context: dict | None) -> None:
    assert with_context("ciao", context) == "ciao"


def test_with_context_with_job_prepends_context_line() -> None:
    assert with_context("ciao", {"job": JOB}) == f"{CONTEXT_LINE}\n\nciao"


def test_chat_with_job_context_stores_context_line(client: TestClient) -> None:
    with app_module.agent.override(model=scripted(reply("Ok."))):
        response = client.post(
            "/chat", json={"message": "ciao", "context": {"job": JOB}}, headers=AUTH
        )

    assert response.status_code == 200
    assert first_user_prompt(response.json()["conversation_id"]) == f"{CONTEXT_LINE}\n\nciao"


def test_chat_with_unknown_context_keys_stores_message_unchanged(client: TestClient) -> None:
    with app_module.agent.override(model=scripted(reply("Ok."))):
        response = client.post(
            "/chat", json={"message": "ciao", "context": {"altro": "y"}}, headers=AUTH
        )

    assert response.status_code == 200
    assert first_user_prompt(response.json()["conversation_id"]) == "ciao"


WRITE_TOOLS = {"change_external_status", "edit_receipt", "undo_submission"}


def offered(answer: ModelResponse) -> tuple[FunctionModel, dict]:
    """A model that answers `answer` and records what the agent offered it.

    The recorded dict has the names of the tools in `tools` and the
    instructions of the run in `instructions`.
    """
    seen: dict = {}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["tools"] = {tool.name for tool in info.function_tools}
        seen["instructions"] = messages[0].instructions or ""
        return answer

    return FunctionModel(respond), seen


def test_chat_offers_the_write_tools_to_a_user_who_can_write(client: TestClient) -> None:
    model, seen = offered(reply("Ok."))
    with app_module.agent.override(model=model):
        chat(client, "ciao")

    assert WRITE_TOOLS <= seen["tools"]
    assert "Non hai i permessi" not in seen["instructions"]


def test_chat_without_write_permission_hides_the_write_tools(
    client: TestClient, workflow: FakeWorkflow
) -> None:
    # Nothing to approve: the model is told to say that the user cannot write.
    workflow.can_write = False
    model, seen = offered(reply("Non hai i permessi per modificare i dati in Workflow."))
    with app_module.agent.override(model=model):
        body = chat(client)

    assert seen["tools"] & WRITE_TOOLS == set()
    assert "get_revision" in seen["tools"]
    assert "Non hai i permessi per modificare i dati in Workflow" in seen["instructions"]
    assert body["pending"] == []
    assert body["reply"] == "Non hai i permessi per modificare i dati in Workflow."


HOLD = 0.2


def hold(
    store: ConversationStore,
    conversation_id: str,
    tag: str,
    events: list[str],
    entered: threading.Event | None = None,
) -> None:
    """Take the conversation's lock and keep it for HOLD, recording the stay."""
    with store.locked(conversation_id):
        events.append(f"in-{tag}")
        if entered is not None:
            entered.set()
        time.sleep(HOLD)
        events.append(f"out-{tag}")


def test_locked_serializes_two_requests_on_the_same_conversation(tmp_path) -> None:
    store = ConversationStore(str(tmp_path / "conversations.db"))
    conversation_id = store.create("paolo")
    events: list[str] = []

    threads = [
        threading.Thread(target=hold, args=(store, conversation_id, tag, events))
        for tag in ("a", "b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    store.conn.close()

    # Whoever goes first, the two stays do not overlap.
    assert [event.split("-")[0] for event in events] == ["in", "out", "in", "out"]


def test_locked_does_not_hold_up_another_conversation(tmp_path) -> None:
    store = ConversationStore(str(tmp_path / "conversations.db"))
    first = store.create("paolo")
    second = store.create("paolo")
    events: list[str] = []

    entered = threading.Event()
    waiting = threading.Thread(target=hold, args=(store, first, "a", events, entered))
    waiting.start()
    assert entered.wait(timeout=5)
    with store.locked(second):
        events.append("other")
    waiting.join()
    store.conn.close()

    # The other conversation got in while the first one was still held.
    assert events == ["in-a", "other", "out-a"]


def locks_taken(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the conversations whose lock the endpoints take."""
    taken: list[str] = []
    locked = app_module.store.locked

    def recording(conversation_id: str):
        taken.append(conversation_id)
        return locked(conversation_id)

    monkeypatch.setattr(app_module.store, "locked", recording)
    return taken


def test_chat_and_confirm_hold_the_conversation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = locks_taken(monkeypatch)
    model = scripted(ModelResponse(parts=[change_status_call()]), reply())
    with app_module.agent.override(model=model):
        conversation_id = chat(client)["conversation_id"]
        confirm(client, conversation_id, {"call-1": True})

    assert taken == [conversation_id, conversation_id]
