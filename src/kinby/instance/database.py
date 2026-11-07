"""The instance database: one SQLite file in the state directory that holds the instance's events,
thread records, waiting deliveries, config changes and coding-session recalls (ADR 0116)."""

import sqlite3
import threading
from pathlib import Path

DATABASE_NAME = "instance.sqlite"
_connecting = threading.Lock()
#: A delivery's provider id, which its arrival event carries.
DELIVERY_ID = "json_extract(payload, '$.delivery.delivery_id')"

# An event's key is its thread and sequence. Its rowid keeps the order events were appended in.
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS events (
    thread_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    turn_id TEXT NOT NULL,
    type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (thread_id, sequence)
);
CREATE INDEX IF NOT EXISTS events_by_turn ON events (turn_id);
CREATE INDEX IF NOT EXISTS events_by_type ON events (type, timestamp);
CREATE INDEX IF NOT EXISTS events_by_delivery ON events ({DELIVERY_ID})
    WHERE type = 'signal.received';
CREATE TABLE IF NOT EXISTS heads (
    thread_id TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL,
    last_activity_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS waiting_deliveries (
    turn_id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT NOT NULL,
    archived INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS config_changes (
    file TEXT NOT NULL,
    change TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recalls (
    run_id TEXT NOT NULL,
    recall TEXT NOT NULL
);
"""


def open_database(state_dir: Path) -> sqlite3.Connection:
    """Connect to the instance database in *state_dir*, making it when missing.

    Each statement commits on its own, and a write of several opens its transaction with
    ``BEGIN IMMEDIATE``. Any thread may use the connection, one thread at a time.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    return connect(state_dir / DATABASE_NAME)


def connect(path: Path) -> sqlite3.Connection:
    """Connect to the instance database at *path*, making its tables when missing."""
    db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    # Two connections that switch a new database to WAL at once may fail without waiting.
    with _connecting:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(_SCHEMA)
    # A commit waits for no disk sync: a power cut may lose the latest writes, never the database.
    db.execute("PRAGMA synchronous = NORMAL")
    return db
