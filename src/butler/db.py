"""Database initialization and connection management for Downloads Butler."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  sha256 TEXT UNIQUE NOT NULL,
  original_name TEXT NOT NULL,
  current_path TEXT NOT NULL,
  size_bytes INTEGER,
  mime TEXT,
  first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  trigger TEXT,
  dry_run INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS moves (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL REFERENCES batches(id),
  file_id INTEGER NOT NULL REFERENCES files(id),
  timestamp TEXT NOT NULL,
  src_path TEXT NOT NULL,
  dst_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT,
  confidence REAL,
  model TEXT
);

CREATE TABLE IF NOT EXISTS cards (
  file_id INTEGER PRIMARY KEY REFERENCES files(id),
  category TEXT,
  document_type TEXT,
  suggested_name TEXT,
  summary TEXT,
  key_facts TEXT,
  tags TEXT,
  document_date TEXT,
  confidence REAL,
  extracted_excerpt TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_moves_batch_id ON moves(batch_id);
CREATE INDEX IF NOT EXISTS idx_moves_status ON moves(status);
"""


def init_db(db_path: Path | str) -> sqlite3.Connection:
    """Initialize database tables and indexes if not already present."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    with conn:
        conn.executescript(SCHEMA_SQL)
    return conn


@contextmanager
def get_db(db_path: Path | str) -> Generator[sqlite3.Connection, None, None]:
    """Provide a transactional database connection context."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
