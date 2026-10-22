
from datetime import date

import httpx
from pydantic_ai import RunContext, ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from workflow_agent.deps import AgentDeps
from workflow_agent.workflow_api import (
    list_statuses,
    status_by_letter,
    find_document,
    NotFound,
    find_revision,
    find_revision_by_label,
    list_revisions,
    post_customer_response,
    post_undo_submission,
    post_modify_receipt
    )

toolset = FunctionToolset()

# Status codes Workflow uses to refuse a write, with the reason in `error`.
# 403 means no write permission: the write tools are kept away from users
# without it (see agent.writes_allowed), so it can only show up if the
# permission is taken away while the run is waiting for the approval.
REFUSED = (400, 403, 404, 409)


def write_result(response: httpx.Response) -> dict:
    """The JSON body of a write call, or ModelRetry with Workflow's reason.

    So that a refused write is reported to the user instead of breaking the
    request.
    """
    if response.status_code in REFUSED:
        raise ModelRetry(response.json()["error"])
    response.raise_for_status()
    return response.json()


def resolve_revision(client: httpx.Client, document: dict, revision: str) -> dict:
    """Return the revision of `document` whose label is `revision`.

    Raises ModelRetry listing the labels of the document's revisions if there
    is none, so the model can pick an existing one or ask the user.
    """
    rev = find_revision_by_label(client, document_id=document["id"], label=revision)
    if rev is None:
        labels = ", ".join(r["rev_label"] for r in list_revisions(client, document["id"]))
        raise ModelRetry(f"Nessuna revisione {revision} per il documento {document['vendor_doc']}. "
                         f"Revisioni disponibili: {labels or 'nessuna'}.")
    return rev

def next_revision(client: httpx.Client, document_id: int, rev: dict) -> dict | None:
    """"The revision after `rev` (rev_no + 1) with its label and status, or None if `rev` is the last."""
    rev_no = (rev["rev_no"] or 0) + 1
    successive = [r for r in list_revisions(client, document_id) if r["rev_no"] == rev_no]
    if not successive:
        return None
    # Più righe con lo stesso rev_no (import Access): come Workflow, conta l'ultima.
    last = successive[-1]
    return {"revision": last["rev_label"], "int_status": last["int_status_eff_label"]}


@toolset.tool
def get_document(ctx: RunContext[AgentDeps], job: str, vendor_doc: str):
    """Find a document in a job and return its main data.

    Use it to check that a document exists and to learn its latest revision
    (latest_revision, its label as shown in the app) before working on
    revisions.

    Args:
        job: job code, e.g. "22155".
        vendor_doc: internal document number (vendor doc), e.g. "22155-01-QZ03".
    """
    try:
        doc = find_document(
            client=ctx.deps.client,
            job=job,
            vendor_doc=vendor_doc
        )
    except NotFound as e:
        raise ModelRetry(str(e))

    if doc is None:
        raise ModelRetry(f"Nessun documento {vendor_doc} trovato nella commessa {job}.")
    
    return {
        "id": doc["id"],
        "job": doc["job"],
        "vendor_doc": doc["vendor_doc"],
        "client_doc_no": doc["client_doc_no"],
        "title": doc["doc_title"],
        "latest_revision": doc["latest_rev_label"],
    }

@toolset.tool
def get_revision(
    ctx: RunContext[AgentDeps],
    job: str,
    vendor_doc: str,
    revision: str
    ) -> dict:
    """Return the current state of a document revision.

    Always call it before proposing a change to a revision, so you can show
    the user the current value and the new one. 
    Next_revision is the revision after this one (label and internal status),
    or null if this is the last revision of the document.

    Args:
        job: job code, e.g. "22155".
        vendor_doc: internal document number (vendor doc), e.g. "22155-01-QZ03".
        revision: the revision as the user sees it, e.g. "C", "3", "D".
    """
    try:
        document = find_document(
            client=ctx.deps.client,
            job=job,
            vendor_doc=vendor_doc
        )
        if document is None:
            raise ModelRetry(f"Nessun documento {vendor_doc} trovato nella commessa {job}")
        rev = resolve_revision(ctx.deps.client, document, revision)
        next_rev = next_revision(ctx.deps.client, document["id"], rev)
    except NotFound as e:
        raise ModelRetry(str(e))

    return {
        "id": rev["id"],
        "vendor_doc": vendor_doc,
        "revision": rev["rev_label"],
        "int_status": rev["int_status_eff_label"],
        "ext_status_letter": rev["ext_status_lettera"],
        "ext_status": rev["ext_status_label"],
        "dis_act_date": rev["dis_act_date"],
        "rec_act_date": rev["rec_act_date"],
        "next_revision": next_rev,
    }


@toolset.tool
def list_customer_responses(ctx: RunContext[AgentDeps]) -> list[dict]:
    """Return the customer responses (external statuses) defined in Workflow.

    Call it before change_external_status to map the user's description
    (e.g. "approvato", "commentato") to a valid letter. crea_nuova_rev says
    whether that response requires a new revision of the document.
    """
    return [
        {
            "letter": s["lettera"],
            "name": s["nome"],
            "crea_nuova_rev": s["crea_nuova_rev"],
        }
        for s in list_statuses(client=ctx.deps.client)
    ]


@toolset.tool(requires_approval=True)
def change_external_status(
    ctx: RunContext[AgentDeps],
    job: str,
    vendor_doc: str,
    revision: str,
    new_letter: str
    ) -> dict:
    """Correct the customer response (ext_status) of ONE revision already returned.

    Use it when the user says the recorded response is wrong or the customer
    changed it. Workflow also updates the crea_nuova_rev flag and, if needed,
    deletes or creates the next revision. In the result, next_revision_action
    ("creata", "eliminata", "nessuna") says what was done; when it is "nessuna"
    and new_status_creates_next_revision is true, the next revision already
    existed (see next_revision, its label).

    Before calling it: read the revision with get_revision and fetch the valid
    letters with list_customer_responses. If the user's description matches
    more than one status (e.g. two "Commented" statuses), ask which one.
    One revision per call.

    Args:
        job: job code, e.g. "22155".
        vendor_doc: internal document number (vendor doc), e.g. "22155-01-QZ03".
        revision: the revision to correct as the user sees it, e.g. "C", "3", "D".
        new_letter: letter of the new external status, as returned by
            list_customer_responses (e.g. "A" for Approved).
    """
    try:
        document = find_document(
            client=ctx.deps.client,
            job=job,
            vendor_doc=vendor_doc
        )
        if document is None:
            raise ModelRetry(f"Nessun documento {vendor_doc} trovato nella commessa {job}")
        rev = resolve_revision(ctx.deps.client, document, revision)

        new_status = status_by_letter(
            client=ctx.deps.client,
            letter=new_letter
        )
    except NotFound as e:
        raise ModelRetry(str(e))

    response = post_customer_response(
        client=ctx.deps.client,
        revision_id=rev["id"],
        ext_status_id=new_status["id"]
    )

    data = write_result(response)
    successiva = data["successiva"]
    return {
        "vendor_doc": vendor_doc,
        "revision": rev["rev_label"],
        "new_status": new_status["nome"],
        "new_status_creates_next_revision": new_status["crea_nuova_rev"],
        "next_revision_action": successiva["azione"],
        "next_revision": (successiva["revisione"] or {}).get("rev_label"),
    }


@toolset.tool(requires_approval=True)
def undo_submission(ctx: RunContext[AgentDeps], job: str, vendor_doc: str) -> dict:
    """Undo the submission to the customer of the LAST revision of a document.

    Use it when the user says a submission was recorded by mistake. The
    revision goes back to "to be issued" (submission date and planned return
    date cleared). The transmittal it was issued in, and its files, are left
    untouched. Only works if the revision was sent and not yet returned by
    the customer.

    Args:
        job: job code, e.g. "22155".
        vendor_doc: internal document number (vendor doc), e.g. "22155-01-QZ03".
    """
    try:
        document = find_document(client=ctx.deps.client, job=job, vendor_doc=vendor_doc)
        if document is None:
            raise ModelRetry(f"Nessun documento {vendor_doc} trovato nella commessa {job}")
        last_rev_label = document["latest_rev_label"]
        last_revision = find_revision(ctx.deps.client, document["id"], document["latest_rev_no"])
        if last_revision is None:
            raise ModelRetry(f"Nessuna revisione {last_rev_label} per il documento {vendor_doc}")
    except NotFound as e:
        raise ModelRetry(str(e))

    response = post_undo_submission(ctx.deps.client, revision_id=last_revision["id"])
    data = write_result(response)
    return {
        "vendor_doc": vendor_doc,
        "revision": last_rev_label,
        "new_int_status": data["revisione"]["int_status_eff_label"],
        "submission_date_cleared": last_revision["dis_act_date"],
    }

@toolset.tool
def search_from_manual(ctx: RunContext[AgentDeps], query: str) -> list[dict]:
    """Search the Workflow user manual for how to do something in the app.

    Use it for questions like "how do I register a customer response?" or
    "where do I set the revision days?". Answer ONLY from the sections
    returned, citing their titles. If no section is relevant, say the manual
    does not cover the topic.

    Args:
        query: the user's question, rephrased as a short search query in Italian.
    """
    return ctx.deps.chromadb.retrieve(query)

@toolset.tool(requires_approval=True)
def edit_receipt(
    ctx: RunContext[AgentDeps],
    job: str,
    vendor_doc: str,
    revision: str,
    new_date: date | None = None
    ):
    """Correct the receipt of ONE revision already returned by the customer.

    Two cases:
    - new_date given: change the receipt date (e.g. it was recorded with the
      wrong day).
    - new_date omitted: undo the receipt, because the document was not
      actually returned. The revision goes back to "sent to customer", its
      customer response is cleared, and the next revision created by the
      receipt is deleted if still empty.

    Before calling it, read the revision with get_revision.

    Args:
        job: job code, e.g. "22155".
        vendor_doc: internal document number (vendor doc), e.g. "22155-01-QZ03".
        revision: the revision as the user sees it, e.g. "C", "3".
        new_date: the correct receipt date (YYYY-MM-DD). Omit it to undo the receipt.
    """
    try:
        document = find_document(
            client=ctx.deps.client,
            job=job,
            vendor_doc=vendor_doc
        )
        if document is None:
            raise ModelRetry(f"Nessun documento {vendor_doc} trovato nella commessa {job}")
        rev = resolve_revision(ctx.deps.client, document, revision)
    except NotFound as e:
        raise ModelRetry(e)
    response = post_modify_receipt(ctx.deps.client, rev["id"], new_date)
    data = write_result(response)
    updated = data["revisione"]
    successiva = data["successiva"]
    return {
        "vendor_doc": vendor_doc,
        "revision": updated["rev_label"],
        "action": "date_changed" if new_date else "receipt_undone",
        "receipt_date": updated["rec_act_date"],
        "int_status": updated["int_status_eff_label"],
        "next_revision_action": successiva["azione"],
        "next_revision": (successiva["revisione"] or {}).get("rev_label"),
    }