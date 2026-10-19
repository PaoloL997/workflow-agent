
from pydantic_ai import RunContext, ModelRetry
from pydantic_ai.toolsets import FunctionToolset

from workflow_agent.deps import AgentDeps
from workflow_agent.helper import (
    status_by_letter,
    find_document,
    NotFound,
    find_revision,
    post_customer_response
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
    ):
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
        raise ModelRetry(f"Nessuna revisione {rev_no} per il documento {vendor_doc}"
                         f"(ultima revisione registrata: {document['latest_rev_no']})")
    return {
        "id": rev["id"],
        "vendor_doc": vendor_doc,
        "rev_no": rev["rev_no"],
        "rev_let": rev["rev_let"],
        "int_status": rev["int_status_eff_label"],
        "ext_status_lettera": rev["ext_status_lettera"],
        "ext_status": rev["ext_status_label"],
        "dis_act_date": rev["dis_act_date"],
        "rec_act_date": rev["rec_act_date"],
        "crea_nuova_rev": rev["crea_nuova_rev"],
    }


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
    deletes or creates the next revision: the "successiva" field in the result
    says what was done.

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
            raise ModelRetry(f"Nessuna revisione {rev_no} per il documento {vendor_doc}"
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
    return {
        "vendor_doc": vendor_doc,
        "rev_no": rev_no,
        "new_status": new_status["nome"],
        "successiva": data["successiva"]["azione"],
    }
    