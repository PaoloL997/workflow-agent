# Il contenitore di ciò che i tool ricevono a runtime e il modello non vede
from dataclasses import dataclass
import httpx

from workflow_agent.rag.retrieval import ChromaDB

@dataclass
class AgentDeps:
    user: str
    client: httpx.Client
    chromadb: ChromaDB