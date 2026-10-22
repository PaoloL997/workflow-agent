"""Thin HTTP helpers around the Workflow API. No domain rules live here."""

from datetime import date

import httpx


class NotFound(Exception):
    """Raised when a job, document, revision or status does not exist in Workflow."""
    pass

def find_document(client: httpx.Client, job: str, vendor_doc: str) -> dict | None:
    """Return the document with the given vendor_doc in the job, or None.

    Raises NotFound if the job does not exist.
    """
    response = client.get(f"api/commesse/{job}/documenti/")
    if response.status_code == 404:
        raise NotFound(f"Commessa {job} non trovata")
    response.raise_for_status()
    for doc in response.json()["documenti"]:
        if doc["vendor_doc"].strip().lower() == vendor_doc.strip().lower():
            return doc
    return None

def list_revisions(client: httpx.Client, document_id: int) -> list[dict]:
    """Return all the revisions of the document.

    Raises NotFound if the document does not exist.
    """
    response = client.get(f"api/documenti/{document_id}/revisioni/")
    if response.status_code == 404:
        raise NotFound(f"Documento {document_id} non trovato.")
    response.raise_for_status()
    return response.json()["revisioni"]

def find_revision(client: httpx.Client, document_id: int, rev_no: int) -> dict | None:
    """Return the revision with the given rev_no of the document, or None.

    rev_no is Workflow's internal index and may differ from what the user
    sees: to find the revision the user means, use find_revision_by_label.
    Raises NotFound if the document does not exist.
    """
    for rev in list_revisions(client, document_id):
        if int(rev["rev_no"]) == rev_no:
            return rev
    return None

def label_key(label: str) -> str:
    """`label` without whitespace and lowercased, so " d " and "D" compare equal."""
    return "".join(label.split()).lower()

def find_revision_by_label(client: httpx.Client, document_id: int, label: str) -> dict | None:
    """Return the revision of the document whose rev_label is `label`, or None.

    rev_label is the label the user sees in the app (e.g. "C", "3", "D"),
    computed by Workflow from the job's revision sequence. Whitespace and
    case are ignored. Raises NotFound if the document does not exist.
    """
    wanted = label_key(label)
    for rev in list_revisions(client, document_id):
        if label_key(rev["rev_label"]) == wanted:
            return rev
    return None

def list_statuses(client: httpx.Client) -> list[dict]:
    """Return all the external statuses (StatoEsterno) defined in Workflow."""
    response = client.get("/api/stati-esterni/")
    response.raise_for_status()
    return response.json()["stati"]

def status_by_letter(client: httpx.Client, letter: str) -> dict:
    """Return the external status (StatoEsterno) with the given letter.

    Raises NotFound, listing the valid letters, if it does not exist.
    """
    stati = list_statuses(client)
    for s in stati:
        if s["lettera"].upper() == letter.strip().upper():
            return s
    valide = ", ".join(s["lettera"] for s in stati)
    raise NotFound(f"Lettera {letter!r} non valida. Lettere ammesse: {valide}.")

def post_customer_response(
        client: httpx.Client,
        revision_id: int,
        ext_status_id: int
        ) -> httpx.Response:
    """Call POST /api/revisioni/<id>/risposta-cliente/ and return the raw response.

    Status handling (400/404/409) is left to the caller.
    """
    return client.post(
        f"/api/revisioni/{revision_id}/risposta-cliente/",
        json={"ext_status_id": ext_status_id},
    )

def post_modify_receipt(
        client: httpx.Client,
        revision_id: int,
        new_date: date | None
        ):
    """Call POST /api/revisioni/<id>/modifica-ricezione/ and return the raw response.

    new_date set → change the receipt date; None → undo the receipt.
    """
    return client.post(
        f"/api/revisioni/{revision_id}/modifica-ricezione/",
        json={"rec_act_date": new_date.isoformat() if new_date else None},
    )
    

def post_undo_submission(client: httpx.Client, revision_id: int):
    """Call POST /api/revisioni/<id>/annulla-invio/ and return the raw response."""
    return client.post(f"/api/revisioni/{revision_id}/annulla-invio/")