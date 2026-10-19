from types import SimpleNamespace

import httpx

from workflow_agent.config import config
from workflow_agent.deps import AgentDeps
from workflow_agent.tools import get_document, get_revision

with httpx.Client(
    base_url=config.workflow_base_url,
    headers={"Authorization": f"Token {config.workflow_token}"},
    timeout=10,
) as client:
    ctx = SimpleNamespace(deps=AgentDeps(user="paolo", client=client))
    #print("CUSTOMER RESPONSES\n")
    #print(list_customer_responses(ctx))
    #print("\nGET DOCUMENT")
    #print(get_document(ctx, "22155", "22155-01-QZ03"))
    #print("\nGET REVISION")
    #print(get_revision(ctx, "22155", "22155-01-QZ03", 2))
    print(get_revision(ctx, "AGENT-TEST", "AGENT-TEST-01-DOC1", 0))
    