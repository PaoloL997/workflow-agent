"""Tests for how the tools find the revision the user means.

The user names a revision by its label as shown in the app, which in a job
with a custom sequence differs from Workflow's rev_no. Workflow is simulated
with the same FakeWorkflow used by the API tests; the tools are called
directly, with a context that only carries the Workflow client.
"""

from types import SimpleNamespace

import httpx
import pytest
from pydantic_ai import ModelRetry

from tests.test_app import JOB, SEQUENCE, VENDOR_DOC, FakeWorkflow, revision
from workflow_agent.tools import change_external_status, get_revision

UNKNOWN_LABEL = (
    f"Nessuna revisione F per il documento {VENDOR_DOC}. Revisioni disponibili: 1, 2, 3, D."
)


def context(http: httpx.Client) -> SimpleNamespace:
    return SimpleNamespace(deps=SimpleNamespace(client=http))


@pytest.fixture
def workflow() -> FakeWorkflow:
    fake = FakeWorkflow()
    fake.revisions = [revision(n, "C", "Commentato", SEQUENCE[n]) for n in range(4)]
    return fake


@pytest.mark.parametrize(
    ("label", "rev_no"),
    [("D", 3), ("3", 2), ("d", 3), (" D ", 3)],
    ids=["letter", "number-of-another-rev_no", "lowercase", "spaces"],
)
def test_get_revision_resolves_label(workflow: FakeWorkflow, label: str, rev_no: int) -> None:
    with workflow.client() as http:
        result = get_revision(context(http), JOB, VENDOR_DOC, revision=label)

    assert result["id"] == 100 + rev_no
    assert result["revision"] == SEQUENCE[rev_no]
    assert "rev_no" not in result


def test_get_revision_with_unknown_label_lists_available_labels(workflow: FakeWorkflow) -> None:
    with workflow.client() as http, pytest.raises(ModelRetry) as retry:
        get_revision(context(http), JOB, VENDOR_DOC, revision="F")

    assert retry.value.message == UNKNOWN_LABEL


def test_change_external_status_posts_on_revision_with_label(workflow: FakeWorkflow) -> None:
    with workflow.client() as http:
        result = change_external_status(
            context(http), JOB, VENDOR_DOC, revision="D", new_letter="A"
        )

    assert ("POST", "/api/revisioni/103/risposta-cliente/") in workflow.requests
    assert result["revision"] == "D"
    assert "rev_no" not in result


def test_change_external_status_with_unknown_label_posts_nothing(workflow: FakeWorkflow) -> None:
    with workflow.client() as http, pytest.raises(ModelRetry) as retry:
        change_external_status(context(http), JOB, VENDOR_DOC, revision="F", new_letter="A")

    assert retry.value.message == UNKNOWN_LABEL
    assert all(method == "GET" for method, _ in workflow.requests)

def test_get_revision_returns_next_revision(workflow: FakeWorkflow) -> None:
    with workflow.client() as http:
        result = get_revision(context(http), JOB, VENDOR_DOC, revision="3")

    assert result["next_revision"] == {"revision": "D", "int_status": "Emesso"}


def test_get_revision_of_last_revision_has_no_next(workflow: FakeWorkflow) -> None:
    with workflow.client() as http:
        result = get_revision(context(http), JOB, VENDOR_DOC, revision="D")

    assert result["next_revision"] is None