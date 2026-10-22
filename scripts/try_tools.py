from types import SimpleNamespace

import httpx

from workflow_agent.config import config
from workflow_agent.deps import AgentDeps
from workflow_agent.rag.retrieval import ChromaDB
from workflow_agent.tools import get_document, get_revision, undo_submission, edit_receipt

chromadb = ChromaDB()

with httpx.Client(
    base_url=config.workflow_base_url,
    headers={"Authorization": f"Token {config.workflow_token}"},
    timeout=10,
) as client:
    ctx = SimpleNamespace(deps=AgentDeps(user="paolo", client=client, chromadb=chromadb, can_write=True))
    #print("CUSTOMER RESPONSES\n")
    #print(list_customer_responses(ctx))
    #print("\nGET DOCUMENT")
    #print(get_document(ctx, "22155", "22155-01-QZ03"))
    #print("\nGET REVISION")
    #print(get_revision(ctx, "22155", "22155-01-QZ03", 2))
    #print(get_revision(ctx, "AGENT-TEST", "AGENT-TEST-01-DOC1", 0))
    #print(get_revision(ctx, "AGENT-TEST", "AGENT-TEST-01-DOC5", 0))
    #print(get_revision(ctx, "AGENT-TEST", "AGENT-TEST-01-DOC5", 1))
    #print(undo_submission(ctx, "25089", "25089-01-2010"))
    print(edit_receipt(ctx, "25089", "25089-01-30", "3"))
