from dataclasses import dataclass
from typing import Any
from psycopg2.extensions import connection


@dataclass
class AgentDeps:
    user: Any
    db: connection