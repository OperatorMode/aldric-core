"""
Self-model persistence.

Learning Governance Document Section 6.2: "The self-model persists in the
memory layer — not in the session context." This module is that memory
layer. Vector-store style semantic recall (sqlite-vec) is a Phase 6+ concern
per the project's own technology stack reference and is deliberately not
built here — this is plain relational SQLite, enough to hold surfaces,
the conflict record, adjudications, and digest entries durably.

Every write function here is intentionally narrow: there is no
`update_surface_confidence_upward()` that ALDRIC can call on itself, and no
delete on conflict_records or digest_entries. Widening what is writable here
is an architectural decision, not a convenience — see PA Action Kernel
"What the Kernel Does Not Do" and Learning Governance Section 6.3 before
adding a new write path.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "aldric.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS dsds (
    dsd_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS surfaces (
    surface_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflict_records (
    entry_id TEXT PRIMARY KEY,
    surface_id TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digest_entries (
    entry_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    digested INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS adjudications (
    adjudication_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS loops (
    loop_name TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@contextmanager
def connect(db_path: str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


# --- DSDs -------------------------------------------------------------

def save_dsd(dsd, db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dsds (dsd_id, data, created_at) VALUES (?, ?, ?)",
            (dsd.dsd_id, dsd.model_dump_json(), dsd.created_at),
        )


def get_dsd(dsd_id: str, db_path: str = DEFAULT_DB_PATH) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT data FROM dsds WHERE dsd_id = ?", (dsd_id,)).fetchone()
        return json.loads(row[0]) if row else None


# --- Surfaces -----------------------------------------------------------

def save_surface(surface, db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO surfaces (surface_id, data, updated_at) VALUES (?, ?, ?)",
            (surface.surface_id, surface.model_dump_json(), surface.updated_at),
        )


def get_surface(surface_id: str, db_path: str = DEFAULT_DB_PATH) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT data FROM surfaces WHERE surface_id = ?", (surface_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None


def list_surfaces(db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT data FROM surfaces").fetchall()
        return [json.loads(r[0]) for r in rows]


# --- Conflict record (append-only; no delete, no update) ---------------

def append_conflict_record(entry, db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO conflict_records (entry_id, surface_id, data, created_at) "
            "VALUES (?, ?, ?, ?)",
            (entry.entry_id, entry.surface_id, entry.model_dump_json(), entry.created_at),
        )


def list_conflict_records(surface_id: str | None = None, db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    with connect(db_path) as conn:
        if surface_id:
            rows = conn.execute(
                "SELECT data FROM conflict_records WHERE surface_id = ? ORDER BY created_at",
                (surface_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT data FROM conflict_records ORDER BY created_at"
            ).fetchall()
        return [json.loads(r[0]) for r in rows]


# --- Digest entries (append-only; digest generation marks as digested) --

def append_digest_entry(entry, db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO digest_entries (entry_id, category, data, created_at, digested) "
            "VALUES (?, ?, ?, ?, 0)",
            (entry.entry_id, entry.category, entry.model_dump_json(), entry.created_at),
        )


def list_undigested_entries(db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    """Everything since the last digest. No filtering — see module docstring
    and PA Action Kernel Component 7.3 (digest governance)."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT data FROM digest_entries WHERE digested = 0 ORDER BY created_at"
        ).fetchall()
        return [json.loads(r[0]) for r in rows]


def mark_all_digested(db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute("UPDATE digest_entries SET digested = 1 WHERE digested = 0")


# --- Adjudications --------------------------------------------------------

def save_adjudication(record, db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO adjudications (adjudication_id, data, created_at) "
            "VALUES (?, ?, ?)",
            (record.adjudication_id, record.model_dump_json(), record.created_at),
        )


def get_adjudication(adjudication_id: str, db_path: str = DEFAULT_DB_PATH) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT data FROM adjudications WHERE adjudication_id = ?",
            (adjudication_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None


# --- Loops ----------------------------------------------------------------

def set_loop_state(loop_name: str, state: str, updated_at: str, db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO loops (loop_name, state, updated_at) VALUES (?, ?, ?)",
            (loop_name, state, updated_at),
        )


def get_active_loop(db_path: str = DEFAULT_DB_PATH) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT loop_name FROM loops WHERE state = 'active'").fetchone()
        return row[0] if row else None


def list_loops(db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT loop_name, state, updated_at FROM loops").fetchall()
        return [{"loop_name": r[0], "state": r[1], "updated_at": r[2]} for r in rows]
