"""Readable summaries of the actions awaiting the user's approval.

The widget shows `summary` next to each pending action, so it must say what
will change in the user's terms (current and new value), not as a tool call.

Each tool can register a summarizer with `@summarizer("<tool_name>")`. Tools
without one, and summarizers that cannot read what they need from Workflow,
fall back to `<tool_name>(<args>)`: the summary never blocks the response.
"""

from collections.abc import Callable
from datetime import date

import httpx

from workflow_agent.workflow_api import (
    NotFound,
    find_document,
    find_revision,
    find_revision_by_label,
    status_by_letter,
)

Summarizer = Callable[[dict, httpx.Client], str]

SUMMARIZERS: dict[str, Summarizer] = {}

# What a failed read looks like: missing job/document/revision/status, HTTP or
# network errors, and payloads (or args) without the expected fields.
READ_ERRORS = (NotFound, httpx.HTTPError, KeyError, ValueError)


def summarizer(tool_name: str) -> Callable[[Summarizer], Summarizer]:
    """Register the decorated function as the summarizer of `tool_name`."""

    def register(func: Summarizer) -> Summarizer:
        SUMMARIZERS[tool_name] = func
        return func

    return register


def fallback(tool_name: str, args: dict) -> str:
    """Render a call as `tool_name(key=value, ...)`."""
    rendered = ", ".join(f"{key}={value!r}" for key, value in args.items())
    return f"{tool_name}({rendered})"


def summarize(tool_name: str, args: dict, client: httpx.Client) -> str:
    """Describe a pending call of `tool_name` with `args` for the user.

    `client` is the Workflow client of the current request, used to read the
    current state. Falls back to `tool_name(args)` if the tool has no
    summarizer or the read fails.
    """
    func = SUMMARIZERS.get(tool_name)
    if func is None:
        return fallback(tool_name, args)
    try:
        return func(args, client)
    except READ_ERRORS:
        return fallback(tool_name, args)


@summarizer("change_external_status")
def summarize_change_external_status(args: dict, client: httpx.Client) -> str:
    """Current and new customer response of the revision, and what happens to the next one.

    Mirrors what Workflow does on POST risposta-cliente: a status that
    requires a new revision creates the next one (rev_no + 1) if missing; a
    status that does not, deletes the next one if it exists and is empty.
    Revisions are named by their label (rev_label); a revision still to be
    created has no label yet, so it is just "the next one".
    """
    label = str(args["revision"])
    document = find_document(client, job=args["job"], vendor_doc=args["vendor_doc"])
    if document is None:
        raise NotFound(f"Nessun documento {args['vendor_doc']} nella commessa {args['job']}")
    rev = find_revision_by_label(client, document_id=document["id"], label=label)
    if rev is None:
        raise NotFound(f"Nessuna revisione {label} per il documento {document['vendor_doc']}")
    new_status = status_by_letter(client, letter=args["new_letter"])
    next_rev = find_revision(client, document_id=document["id"], rev_no=(rev["rev_no"] or 0) + 1)

    if rev["ext_status_lettera"]:
        current = f"{rev['ext_status_lettera']} ({rev['ext_status_label']})"
    else:
        current = "nessuna"
    summary = (
        f"Documento {document['vendor_doc']}, rev {rev['rev_label']}: risposta cliente {current}"
        f" → {new_status['lettera']} ({new_status['nome']})"
    )
    if new_status["crea_nuova_rev"] and next_rev is None:
        summary += "; verrà creata la revisione successiva"
    elif not new_status["crea_nuova_rev"] and next_rev is not None:
        summary += f"; la rev {next_rev['rev_label']} verrà eliminata se vuota"
    return summary


UNDO_SUBMISSION_EFFECT = ': annulla l\'invio al cliente; torna "da emettere"'


@summarizer("undo_submission")
def summarize_undo_submission(args: dict, client: httpx.Client) -> str:
    """Which revision loses its submission to the customer, and what happens to it.

    The tool always works on the last revision of the document, so the
    summary names it (latest_rev_label) and, when known, the submission date
    that will be cleared.
    """
    document = find_document(client, job=args["job"], vendor_doc=args["vendor_doc"])
    if document is None:
        raise NotFound(f"Nessun documento {args['vendor_doc']} nella commessa {args['job']}")
    label = document["latest_rev_label"]
    rev = find_revision(client, document_id=document["id"], rev_no=int(document["latest_rev_no"]))
    if rev is None:
        raise NotFound(f"Nessuna revisione {label} per il documento {document['vendor_doc']}")

    summary = f"Documento {document['vendor_doc']}, rev {label}"
    if rev["dis_act_date"]:
        sent_on = date.fromisoformat(rev["dis_act_date"]).strftime("%d/%m/%Y")
        summary += f" (inviata il {sent_on})"
    return summary + UNDO_SUBMISSION_EFFECT


UNDO_RECEIPT_EFFECT = 'torna "inviato al cliente"'


@summarizer("edit_receipt")
def summarize_edit_receipt(args: dict, client: httpx.Client) -> str:
    """Current and new receipt date of the revision, or what undoing the receipt clears.

    With new_date the tool only moves the receipt date. Without it, Workflow
    undoes the receipt: the revision goes back to "inviato al cliente", its
    customer response is cleared and the next revision (rev_no + 1) is
    deleted if empty.
    """
    label = str(args["revision"])
    new_date = args.get("new_date")
    document = find_document(client, job=args["job"], vendor_doc=args["vendor_doc"])
    if document is None:
        raise NotFound(f"Nessun documento {args['vendor_doc']} nella commessa {args['job']}")
    rev = find_revision_by_label(client, document_id=document["id"], label=label)
    if rev is None:
        raise NotFound(f"Nessuna revisione {label} per il documento {document['vendor_doc']}")
    received_on = (
        date.fromisoformat(rev["rec_act_date"]).strftime("%d/%m/%Y")
        if rev["rec_act_date"]
        else None
    )
    summary = f"Documento {document['vendor_doc']}, rev {rev['rev_label']}"

    if new_date:
        new_received_on = date.fromisoformat(str(new_date)).strftime("%d/%m/%Y")
        return summary + f": data ricezione {received_on or 'nessuna'} → {new_received_on}"

    next_rev = find_revision(client, document_id=document["id"], rev_no=(rev["rev_no"] or 0) + 1)
    if received_on:
        summary += f" (ricevuta il {received_on})"
    summary += ": annulla la ricezione"
    if rev["ext_status_lettera"]:
        summary += f" e la risposta cliente {rev['ext_status_lettera']} ({rev['ext_status_label']})"
    summary += f"; {UNDO_RECEIPT_EFFECT}"
    if next_rev is not None:
        summary += f"; la rev {next_rev['rev_label']} verrà eliminata se vuota"
    return summary
