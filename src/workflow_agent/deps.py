# Il contenitore di ciò che i tool ricevono a runtime e il modello non vede
from dataclasses import dataclass
import httpx

@dataclass
class AgentDeps:
    user: str
    client: httpx.Client