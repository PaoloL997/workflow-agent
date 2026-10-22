"""Tests for the summaries of `undo_submission` and `edit_receipt`.

Workflow is simulated with the same FakeWorkflow used by the API tests.
"""

import httpx
import pytest

from tests.test_app import DOCUMENT, JOB, VENDOR_DOC, FakeWorkflow, revision
from workflow_agent.summaries import summarize

ARGS = {"job": JOB, "vendor_doc": VENDOR_DOC}
FALLBACK = f"undo_submission(job='{JOB}', vendor_doc='{VENDOR_DOC}')"
EFFECT = ': annulla l\'invio al cliente; torna "da emettere"'


@pytest.fixture
def workflow() -> FakeWorkflow:
    return FakeWorkflow()


def test_undo_submission_summary_names_last_revision_and_date(workflow: FakeWorkflow) -> None:
    with workflow.client() as http:
        summary = summarize("undo_submission", ARGS, http)

    assert summary == f"Documento {VENDOR_DOC}, rev B (inviata il 01/09/2026){EFFECT}"


def test_undo_submission_summary_omits_date_when_not_sent(workflow: FakeWorkflow) -> None:
    last = revision(1, "", "")
    last["dis_act_date"] = None
    workflow.revisions = [revision(0, "C", "Commentato"), last]

    with workflow.client() as http:
        summary = summarize("undo_submission", ARGS, http)

    assert summary == f"Documento {VENDOR_DOC}, rev B{EFFECT}"


@pytest.mark.parametrize(
    ("path", "failure"),
    [
        (f"/api/commesse/{JOB}/documenti/", 404),
        (f"/api/documenti/{DOCUMENT['id']}/revisioni/", 500),
        (f"/api/commesse/{JOB}/documenti/", httpx.ReadTimeout("timed out")),
    ],
    ids=["job-not-found", "revisions-500", "timeout"],
)
def test_undo_submission_summary_falls_back_when_read_fails(
    workflow: FakeWorkflow, path: str, failure: int | Exception
) -> None:
    workflow.fail[path] = failure

    with workflow.client() as http:
        summary = summarize("undo_submission", ARGS, http)

    assert summary == FALLBACK


def test_undo_submission_summary_falls_back_when_last_revision_is_missing(
    workflow: FakeWorkflow,
) -> None:
    workflow.revisions = [revision(0, "C", "Commentato")]

    with workflow.client() as http:
        summary = summarize("undo_submission", ARGS, http)

    assert summary == FALLBACK


RECEIPT_ARGS = {"job": JOB, "vendor_doc": VENDOR_DOC, "revision": "A"}
RECEIPT_FALLBACK = f"edit_receipt(job='{JOB}', vendor_doc='{VENDOR_DOC}', revision='Z')"


def test_edit_receipt_summary_shows_current_and_new_date(workflow: FakeWorkflow) -> None:
    with workflow.client() as http:
        summary = summarize("edit_receipt", {**RECEIPT_ARGS, "new_date": "2026-09-20"}, http)

    assert summary == f"Documento {VENDOR_DOC}, rev A: data ricezione 15/09/2026 → 20/09/2026"


def test_edit_receipt_summary_without_date_undoes_receipt_and_names_next_revision(
    workflow: FakeWorkflow,
) -> None:
    with workflow.client() as http:
        summary = summarize("edit_receipt", RECEIPT_ARGS, http)

    assert summary == (
        f"Documento {VENDOR_DOC}, rev A (ricevuta il 15/09/2026): annulla la ricezione"
        ' e la risposta cliente C (Commentato); torna "inviato al cliente";'
        " la rev B verrà eliminata se vuota"
    )


def test_edit_receipt_summary_of_last_revision_has_no_next(workflow: FakeWorkflow) -> None:
    with workflow.client() as http:
        summary = summarize("edit_receipt", {**RECEIPT_ARGS, "revision": "B"}, http)

    assert summary == (
        f"Documento {VENDOR_DOC}, rev B (ricevuta il 15/09/2026): annulla la ricezione"
        ' e la risposta cliente C (Commentato); torna "inviato al cliente"'
    )


def test_edit_receipt_summary_falls_back_when_revision_is_missing(workflow: FakeWorkflow) -> None:
    with workflow.client() as http:
        summary = summarize("edit_receipt", {**RECEIPT_ARGS, "revision": "Z"}, http)

    assert summary == RECEIPT_FALLBACK
