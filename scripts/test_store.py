# scripts/try_store.py
# Prova dello store: crea, salva una storia vera, ricarica.

from unittest.mock import MagicMock

from pydantic_ai.models.test import TestModel

from workflow_agent.agent import agent
from workflow_agent.deps import AgentDeps
from workflow_agent.store.history import ConversationStore

store = ConversationStore("test.db")

cid = store.create("plitta")
print("id:", cid)
print("vuota:", store.load(cid, "plitta"))        # []
print("altro utente:", store.load(cid, "altro"))  # None

result = agent.run_sync(
    "ciao",
    deps=AgentDeps(user="plitta", client=MagicMock()),
    model=TestModel(),
)
store.save(cid, result.all_messages())

messages = store.load(cid, "plitta")
print("messaggi salvati:", len(messages))
print("primo:", type(messages[0]).__name__)