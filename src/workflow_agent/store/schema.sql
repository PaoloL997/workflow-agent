CREATE TABLE IF NOT EXISTS conversations (
    id  TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    messages TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
