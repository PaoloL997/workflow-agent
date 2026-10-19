"""Thin HTTP helpers around the Workflow API. No domain rules live here."""

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

def find_revision(client: httpx.Client, document_id: int, rev_no: int) -> dict | None:
    """Return the revision with the given rev_no of the document, or None.

    Raises NotFound if the document does not exist.
    """
    response = client.get(f"api/documenti/{document_id}/revisioni/")
    if response.status_code == 404:
        raise NotFound(f"Documento {document_id} non trovato.")
    response.raise_for_status()
    for rev in response.json()["revisioni"]:
        if int(rev["rev_no"]) == rev_no:
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