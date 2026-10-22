import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

SCHEMA = (Path(__file__).parent / "conversations.sql").read_text(encoding="utf-8")

class ConversationStore:
    """The conversations, in SQLite, shared by the threads serving the requests.

    One connection for every thread (sqlite3.threadsafety is 3, serialized):
    WAL keeps a writer from blocking the readers.
    """

    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(SCHEMA)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @contextmanager
    def locked(self, conversation_id: str) -> Iterator[None]:
        """Hold `conversation_id` for the whole of a load, run and save.

        Two requests on the same conversation (the widget sending twice, or a
        /chat overlapping a /confirm) would otherwise read the same history
        and both write it back, losing one of the two runs. They are
        serialized instead: the second one waits and reads what the first
        saved. Requests on different conversations never wait for each other.
        """
        with self._locks_guard:
            lock = self._locks.setdefault(conversation_id, threading.Lock())
        with lock:
            yield

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
