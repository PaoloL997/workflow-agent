"""Readable summaries of the actions awaiting the user's approval.

The widget shows `summary` next to each pending action, so it must say what
will change in the user's terms (current and new value), not as a tool call.

Each tool can register a summarizer with `@summarizer("<tool_name>")`. Tools
without one, and summarizers that cannot read what they need from Workflow,
fall back to `<tool_name>(<args>)`: the summary never blocks the response.
"""

from collections.abc import Callable

import httpx

from workflow_agent.helper import NotFound, find_document, find_revision, status_by_letter

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
    requires a new revision creates the next one if missing; a status that
    does not, deletes the next one if it exists and is empty.
    """
    rev_no = int(args["rev_no"])
    document = find_document(client, job=args["job"], vendor_doc=args["vendor_doc"])
    if document is None:
        raise NotFound(f"Nessun documento {args['vendor_doc']} nella commessa {args['job']}")
    rev = find_revision(client, document_id=document["id"], rev_no=rev_no)
    if rev is None:
        raise NotFound(f"Nessuna revisione {rev_no} per il documento {document['vendor_doc']}")
    new_status = status_by_letter(client, letter=args["new_letter"])
    next_rev = find_revision(client, document_id=document["id"], rev_no=rev_no + 1)

    if rev["ext_status_lettera"]:
        current = f"{rev['ext_status_lettera']} ({rev['ext_status_label']})"
    else:
        current = "nessuna"
    summary = (
        f"Documento {document['vendor_doc']}, rev {rev_no}: risposta cliente {current}"
        f" → {new_status['lettera']} ({new_status['nome']})"
    )
    if new_status["crea_nuova_rev"] and next_rev is None:
        summary += f"; verrà creata la rev {rev_no + 1}"
    elif not new_status["crea_nuova_rev"] and next_rev is not None:
        summary += f"; la rev {rev_no + 1} verrà eliminata se vuota"
    return summary
