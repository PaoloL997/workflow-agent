# Il contenitore di ciò che i tool ricevono a runtime e il modello non vede
from dataclasses import dataclass
import httpx

from workflow_agent.rag.retrieval import ChromaDB

@dataclass
class AgentDeps:
    user: str
    client: httpx.Client
    chromadb: ChromaDB
    # Se l'utente ha il permesso di scrittura in Workflow: senza, i tool di
    # scrittura non vengono nemmeno proposti (vedi agent.writes_allowed).
    can_write: bool
