import sqlite3
import uuid
from datetime import datetime, timezone

from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

class ConversationStore:
    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, user: str) -> str:
        conversation_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO conversations (id, user, messages, updated_at) VALUES (?, ?, ?, ?)",
            (conversation_id, user, "[]", self._now())
        )
        self.conn.commit()
        return conversation_id

    def load(self, conversation_id: str, user: str) -> list[ModelMessage] | None:
        row = self.conn.execute(
            "SELECT messages FROM conversations WHERE id = ? AND user = ?",
            (conversation_id, user),
        ).fetchone()
        if row is None:
            return None
        return ModelMessagesTypeAdapter.validate_json(row[0])

    def save(self, conversation_id: str, messages: list[ModelMessage]) -> None:
        self.conn.execute(
            "UPDATE conversations SET messages = ?, updated_at = ? WHERE id = ?",
            (ModelMessagesTypeAdapter.dump_json(messages).decode(), self._now(), conversation_id),
        )
        self.conn.commit()