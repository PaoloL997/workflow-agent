
from pydantic_ai import RunContext, ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from workflow_agent.deps import AgentDeps
from workflow_agent.workflow_api import (
    list_statuses,
    status_by_letter,
    find_document,
    NotFound,
    find_revision,
    post_customer_response,
    post_undo_submission
    )

toolset = FunctionToolset()

@toolset.tool
def get_document(ctx: RunContext[AgentDeps], job: str, vendor_doc: str):
    """Find a document in a job and return its main data.

    Use it to check that a document exists and to learn its latest revision
    before working on revisions.

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
        "latest_rev_no": doc["latest_rev_no"],
    }

@toolset.tool
def get_revision(
    ctx: RunContext[AgentDeps],
    job: str,
    vendor_doc: str,
    rev_no: int
    ) -> dict:
    """Return the current state of a document revision.

    Always call it before proposing a change to a revision, so you can show
    the user the current value and the new one.

    Args:
        job: job code, e.g. "22155".
        vendor_doc: internal document number (vendor doc), e.g. "22155-01-QZ03".
        rev_no: progressive revision number (0 = first issue). If the user
            gives a letter (e.g. "rev C"), use get_document and get_revision
            to find the matching number (rev_let).
    """
    try:
        document = find_document(
            client=ctx.deps.client,
            job=job,
            vendor_doc=vendor_doc
        )
        if document is None:
            raise ModelRetry(f"Nessun documento {vendor_doc} trovato nella commessa {job}")
        rev = find_revision(
            client=ctx.deps.client,
            document_id=document["id"],
            rev_no=rev_no
            )
    except NotFound as e:
        raise ModelRetry(str(e))

    if rev is None:
        raise ModelRetry(f"Nessuna revisione {rev_no} per il documento {vendor_doc} "
                         f"(ultima revisione registrata: {document['latest_rev_no']})")
    return {
        "id": rev["id"],
        "vendor_doc": vendor_doc,
        "rev_no": rev["rev_no"],
        "rev_let": rev["rev_let"],
        "int_status": rev["int_status_eff_label"],
        "ext_status_letter": rev["ext_status_lettera"],
        "ext_status": rev["ext_status_label"],
        "dis_act_date": rev["dis_act_date"],
        "rec_act_date": rev["rec_act_date"],
        "creates_next_revision": rev["crea_nuova_rev"],
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
    rev_no: int,
    new_letter: str
    ) -> dict:
    """Correct the customer response (ext_status) of ONE revision already returned.

    Use it when the user says the recorded response is wrong or the customer
    changed it. Workflow also updates the crea_nuova_rev flag and, if needed,
    deletes or creates the next revision. In the result, next_revision_action
    ("creata", "eliminata", "nessuna") says what was done; when it is "nessuna"
    and new_status_creates_next_revision is true, the next revision already
    existed (see next_revision_no).

    Before calling it: read the revision with get_revision and fetch the valid
    letters with list_customer_responses. If the user's description matches
    more than one status (e.g. two "Commented" statuses), ask which one.
    One revision per call.

    Args:
        job: job code, e.g. "22155".
        vendor_doc: internal document number (vendor doc), e.g. "22155-01-QZ03".
        rev_no: progressive number of the revision to correct.
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
        rev = find_revision(
            client=ctx.deps.client,
            document_id=document["id"],
            rev_no=rev_no
        )
        if rev is None:
            raise ModelRetry(f"Nessuna revisione {rev_no} per il documento {vendor_doc} "
                             f"(ultima revisione registrata: {document['latest_rev_no']})")

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

    if response.status_code in [400, 404, 409]:
        raise ModelRetry(response.json()["error"])
    response.raise_for_status()
    
    data = response.json()
    successiva = data["successiva"]
    return {
        "vendor_doc": vendor_doc,
        "rev_no": rev_no,
        "new_status": new_status["nome"],
        "new_status_creates_next_revision": new_status["crea_nuova_rev"],
        "next_revision_action": successiva["azione"],
        "next_revision_no": (successiva["revisione"] or {}).get("rev_no"),
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
        last_rev_num = document["latest_rev_no"]
        last_revision = find_revision(ctx.deps.client, document["id"], last_rev_num)
        if last_revision is None:
            raise ModelRetry(f"Nessuna revisione {last_rev_num} per il documento {vendor_doc}")
    except NotFound as e:
        raise ModelRetry(str(e))

    response = post_undo_submission(ctx.deps.client, revision_id=last_revision["id"])
    if response.status_code in [400, 404, 409]:
        raise ModelRetry(response.json()["error"])
    response.raise_for_status()

    data = response.json()
    return {
        "vendor_doc": vendor_doc,
        "rev_no": last_rev_num,
        "rev_display": document["latest_rev_display"],
        "new_int_status": data["revisione"]["int_status_eff_label"],
        "submission_date_cleared": last_revision["dis_act_date"],
    }
