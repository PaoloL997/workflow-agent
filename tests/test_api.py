"""Tests for the HTTP API.

Workflow is simulated with httpx.MockTransport (FakeWorkflow), the model with
a FunctionModel that returns a scripted list of responses, and the store is a
SQLite file under tmp_path.
"""

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from workflow_agent import api
from workflow_agent.store.history import ConversationStore
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
    "latest_rev_display": "B",
}
STATUSES = [
    {"id": 1, "lettera": "A", "nome": "Approvato", "crea_nuova_rev": False},
    {"id": 3, "lettera": "C", "nome": "Commentato", "crea_nuova_rev": True},
]


def revision(rev_no: int, letter: str, label: str) -> dict:
    """A revision of DOCUMENT as returned by GET api/documenti/<id>/revisioni/."""
    return {
        "id": 100 + rev_no,
        "rev_no": rev_no,
        "rev_let": "ABCD"[rev_no],
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
            return httpx.Response(200, json={"username": "paolo"})
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


def change_status_call(tool_call_id: str = "call-1", new_letter: str = "A") -> ToolCallPart:
    return ToolCallPart(
        "change_external_status",
        {"job": JOB, "vendor_doc": VENDOR_DOC, "rev_no": 1, "new_letter": new_letter},
        tool_call_id=tool_call_id,
    )


def reply(text: str = "Fatto.") -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


@pytest.fixture
def workflow(monkeypatch: pytest.MonkeyPatch) -> FakeWorkflow:
    fake = FakeWorkflow()
    monkeypatch.setattr(api, "make_client", lambda token: fake.client())
    return fake


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, tmp_path, workflow: FakeWorkflow
) -> Iterator[TestClient]:
    store = ConversationStore(str(tmp_path / "conversations.db"))
    monkeypatch.setattr(api, "store", store)
    yield TestClient(api.app)
    store.conn.close()


def chat(client: TestClient, message: str = "La rev 1 è approvata") -> dict:
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
    with api.agent.override(model=scripted(ModelResponse(parts=[change_status_call()]))):
        conversation_id = chat(client)["conversation_id"]
        response = confirm(client, conversation_id, approvals)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Pending actions do not match",
        "expected": ["call-1"],
        "received": list(approvals),
    }


def test_confirm_on_conversation_ended_with_reply_returns_409(client: TestClient) -> None:
    with api.agent.override(model=scripted(reply("Ciao!"))):
        conversation_id = chat(client, "ciao")["conversation_id"]
        response = confirm(client, conversation_id, {"call-1": True})

    assert response.status_code == 409
    assert response.json() == {"detail": "No pending actions for this conversation"}


def test_confirm_on_new_conversation_returns_409(client: TestClient) -> None:
    conversation_id = api.store.create("paolo")

    response = confirm(client, conversation_id, {"call-1": True})

    assert response.status_code == 409
    assert response.json() == {"detail": "No pending actions for this conversation"}


def test_confirm_with_matching_ids_resumes_run(client: TestClient, workflow: FakeWorkflow) -> None:
    # A read run in the same step as the write already has a result in the
    # history: only the write is pending.
    read = ToolCallPart(
        "get_revision", {"job": JOB, "vendor_doc": VENDOR_DOC, "rev_no": 1}, tool_call_id="read-1"
    )
    model = scripted(ModelResponse(parts=[read, change_status_call()]), reply())
    with api.agent.override(model=model):
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
    # Let a mismatch slip past the check to exercise the safety net.
    monkeypatch.setattr(api, "pending_call_ids", lambda history: ["bogus"])
    with api.agent.override(model=scripted(ModelResponse(parts=[change_status_call()]))):
        conversation_id = chat(client)["conversation_id"]
        response = confirm(client, conversation_id, {"bogus": True})

    assert response.status_code == 400
    assert "bogus" in response.json()["detail"]


FALLBACK = (
    f"change_external_status(job='{JOB}', vendor_doc='{VENDOR_DOC}', rev_no=1, new_letter='A')"
)


def test_chat_pending_has_summary(client: TestClient) -> None:
    with api.agent.override(model=scripted(ModelResponse(parts=[change_status_call()]))):
        body = chat(client)

    [pending] = body["pending"]
    assert pending["tool_call_id"] == "call-1"
    assert pending["summary"] == (
        f"Documento {VENDOR_DOC}, rev 1: risposta cliente C (Commentato) → A (Approvato)"
    )


@pytest.mark.parametrize(
    ("current", "new_letter", "next_exists", "suffix"),
    [
        (("A", "Approvato"), "C", False, "; verrà creata la rev 2"),
        (("A", "Approvato"), "C", True, ""),
        (("C", "Commentato"), "A", True, "; la rev 2 verrà eliminata se vuota"),
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
        f"Documento {VENDOR_DOC}, rev 1: risposta cliente {current[0]} ({current[1]})"
        f" → {new_letter} ({new_name}){suffix}"
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
    with api.agent.override(model=scripted(ModelResponse(parts=[change_status_call()]))):
        response = client.post("/chat", json={"message": "La rev 1 è approvata"}, headers=AUTH)

    assert response.status_code == 200
    [pending] = response.json()["pending"]
    assert pending["tool_call_id"] == "call-1"
    assert pending["summary"] == FALLBACK
