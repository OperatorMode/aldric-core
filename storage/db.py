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

import datetime as dt
import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from storage import _supabase

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "aldric.db")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()

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

-- Long-term memory (see models.schemas.StandingPreference/MemoryFact and
-- core/long_term_memory.py). Local SQLite only for now, deliberately: the
-- Supabase mirroring every other table above has is not yet extended to
-- these two, matching the agreed build order of proving the memory logic
-- itself locally before swapping the durability layer underneath it.
CREATE TABLE IF NOT EXISTS standing_preferences (
    preference_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL UNIQUE,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS long_term_facts (
    fact_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Idempotency ledger (see models.schemas.IdempotentActionRecord and
-- core/idempotency_ledger.py). idempotency_key is the primary key on
-- purpose: claim_idempotency_key()'s plain INSERT relies on the UNIQUE
-- constraint it implies to make "who gets to execute" a race-free decision
-- rather than a SELECT-then-INSERT that could double-claim under
-- concurrency. Not mirrored to Supabase's schema automatically the way the
-- other tables above are provisioned there — see claim_idempotency_key's
-- docstring for what the Supabase path assumes exists.
CREATE TABLE IF NOT EXISTS idempotent_actions (
    idempotency_key TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
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
    if _supabase.is_configured():
        # Tables are provisioned directly in Supabase (see the migration
        # applied when this was set up) — nothing to initialize locally.
        return
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
#
# Each function below checks _supabase.is_configured() first. When
# SUPABASE_URL/SUPABASE_KEY are set, the record goes to Supabase's Postgres
# instead of the local SQLite file — same data, same semantics (append-only,
# no filtering on read), different durability layer. See storage/_supabase.py.

def append_digest_entry(entry, db_path: str = DEFAULT_DB_PATH) -> None:
    if _supabase.is_configured():
        _supabase.get_client().table("digest_entries").insert(
            {
                "entry_id": entry.entry_id,
                "category": entry.category,
                "data": json.loads(entry.model_dump_json()),
                "created_at": entry.created_at,
                "digested": False,
            }
        ).execute()
        return
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO digest_entries (entry_id, category, data, created_at, digested) "
            "VALUES (?, ?, ?, ?, 0)",
            (entry.entry_id, entry.category, entry.model_dump_json(), entry.created_at),
        )


def list_undigested_entries(db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    """Everything since the last digest. No filtering — see module docstring
    and PA Action Kernel Component 7.3 (digest governance)."""
    if _supabase.is_configured():
        rows = (
            _supabase.get_client()
            .table("digest_entries")
            .select("data")
            .eq("digested", False)
            .order("created_at")
            .execute()
            .data
        )
        return [r["data"] for r in rows]
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT data FROM digest_entries WHERE digested = 0 ORDER BY created_at"
        ).fetchall()
        return [json.loads(r[0]) for r in rows]


def mark_all_digested(db_path: str = DEFAULT_DB_PATH) -> None:
    if _supabase.is_configured():
        _supabase.get_client().table("digest_entries").update(
            {"digested": True}
        ).eq("digested", False).execute()
        return
    with connect(db_path) as conn:
        conn.execute("UPDATE digest_entries SET digested = 1 WHERE digested = 0")


# --- Adjudications --------------------------------------------------------

def save_adjudication(record, db_path: str = DEFAULT_DB_PATH) -> None:
    if _supabase.is_configured():
        stage_value = record.stage.value if hasattr(record.stage, "value") else record.stage
        _supabase.get_client().table("adjudications").upsert(
            {
                "adjudication_id": record.adjudication_id,
                "dsd_ref": record.dsd_ref,
                "summary": record.summary,
                "touches_permanent_tier_c": record.touches_permanent_tier_c,
                "stage": stage_value,
                "data": json.loads(record.model_dump_json()),
                "created_at": record.created_at,
            }
        ).execute()
        return
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO adjudications (adjudication_id, data, created_at) "
            "VALUES (?, ?, ?)",
            (record.adjudication_id, record.model_dump_json(), record.created_at),
        )


def get_adjudication(adjudication_id: str, db_path: str = DEFAULT_DB_PATH) -> dict | None:
    if _supabase.is_configured():
        rows = (
            _supabase.get_client()
            .table("adjudications")
            .select("data")
            .eq("adjudication_id", adjudication_id)
            .execute()
            .data
        )
        return rows[0]["data"] if rows else None
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


# --- Long-term memory: standing preferences (upsert-by-scope) -------------
#
# One active preference per scope — see models.schemas.StandingPreference.
# INSERT OR REPLACE relies on the UNIQUE(scope) constraint above: setting a
# new preference for a scope that already has one replaces it outright
# (a new preference_id, the old row gone), matching Correction Absolute
# (CLAUDE.md Section 7) — the latest instruction applies immediately and
# completely, not as a resistible negotiation. Supabase mirroring not yet
# built for this table (see schema comment above) — local SQLite only.

def set_preference(preference, db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO standing_preferences (preference_id, scope, data, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (preference.preference_id, preference.scope, preference.model_dump_json(), preference.updated_at),
        )


def get_preference(scope: str, db_path: str = DEFAULT_DB_PATH) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT data FROM standing_preferences WHERE scope = ?", (scope,)
        ).fetchone()
        return json.loads(row[0]) if row else None


def list_preferences(db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT data FROM standing_preferences ORDER BY scope").fetchall()
        return [json.loads(r[0]) for r in rows]


# --- Long-term memory: facts (append-only) ---------------------------------

def append_fact(fact, db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO long_term_facts (fact_id, scope, data, created_at) VALUES (?, ?, ?, ?)",
            (fact.fact_id, fact.scope, fact.model_dump_json(), fact.created_at),
        )


def list_facts(scope: str | None = None, db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    with connect(db_path) as conn:
        if scope:
            rows = conn.execute(
                "SELECT data FROM long_term_facts WHERE scope = ? ORDER BY created_at", (scope,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT data FROM long_term_facts ORDER BY created_at").fetchall()
        return [json.loads(r[0]) for r in rows]


# --- Idempotency ledger (core/idempotency_ledger.py) -----------------------

def _looks_like_unique_violation(exc: Exception) -> bool:
    """Best-effort cross-backend check. sqlite3 raises its own
    IntegrityError type (checked directly, not via this function — see
    claim_idempotency_key below); this exists only for the Supabase/
    postgrest path, where the client library doesn't give a stable
    exception type to catch. Matching on message text is honestly a
    heuristic, not a guarantee — documented here rather than pretended
    otherwise (CLAUDE.md Section 5)."""
    message = str(exc).lower()
    return "duplicate" in message or "unique" in message or "23505" in message


def claim_idempotency_key(record, allow_reclaim_failed: bool = False, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Atomically claim `record.idempotency_key` as PENDING. Returns True
    if THIS call now owns execution of that key, False if it does not.

    The claim is a plain INSERT, not a SELECT-then-INSERT: the row's
    PRIMARY KEY constraint is what makes "who gets to execute" a race-free
    decision rather than a check-then-act window a concurrent caller could
    slip through. When `allow_reclaim_failed` is set, a second path is also
    allowed to succeed: overwriting an existing row, but ONLY when its
    current status is 'failed' — a caller opting into an explicit retry.
    It never overwrites a 'pending' or 'completed' row under any
    circumstance; that's the actual guarantee against a double execution."""
    if _supabase.is_configured():
        client = _supabase.get_client()
        payload = {
            "idempotency_key": record.idempotency_key,
            "tool_name": record.tool_name,
            "status": record.status.value,
            "data": json.loads(record.model_dump_json()),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        try:
            client.table("idempotent_actions").insert(payload).execute()
            return True
        except Exception as exc:  # noqa: BLE001 — re-checked below, re-raised if not a dupe
            if not _looks_like_unique_violation(exc):
                raise
            if not allow_reclaim_failed:
                return False
            updated = (
                client.table("idempotent_actions")
                .update(payload)
                .eq("idempotency_key", record.idempotency_key)
                .eq("status", "failed")
                .execute()
            )
            return bool(updated.data)
    with connect(db_path) as conn:
        try:
            conn.execute(
                "INSERT INTO idempotent_actions (idempotency_key, tool_name, status, data, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (record.idempotency_key, record.tool_name, record.status.value,
                 record.model_dump_json(), record.created_at, record.updated_at),
            )
            return True
        except sqlite3.IntegrityError:
            if not allow_reclaim_failed:
                return False
            cur = conn.execute(
                "UPDATE idempotent_actions SET status = ?, data = ?, created_at = ?, updated_at = ? "
                "WHERE idempotency_key = ? AND status = 'failed'",
                (record.status.value, record.model_dump_json(), record.created_at, record.updated_at,
                 record.idempotency_key),
            )
            return cur.rowcount > 0


def update_idempotent_action_status(
    idempotency_key: str, status, result: dict | None = None, error: str | None = None,
    updated_at: str | None = None, db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Flip an existing row to COMPLETED or FAILED. No-op if the key isn't
    in the ledger at all (defensive only — run_idempotent always claims a
    row before calling this, so that should not happen in practice)."""
    updated_at = updated_at or _now_iso()
    status_value = status.value if hasattr(status, "value") else status
    if _supabase.is_configured():
        current = get_idempotent_action(idempotency_key, db_path=db_path)
        if current is None:
            return
        current["status"] = status_value
        current["result"] = result
        current["error"] = error
        current["updated_at"] = updated_at
        _supabase.get_client().table("idempotent_actions").update(
            {"status": status_value, "data": current, "updated_at": updated_at}
        ).eq("idempotency_key", idempotency_key).execute()
        return
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT data FROM idempotent_actions WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if row is None:
            return
        data = json.loads(row[0])
        data["status"] = status_value
        data["result"] = result
        data["error"] = error
        data["updated_at"] = updated_at
        conn.execute(
            "UPDATE idempotent_actions SET status = ?, data = ?, updated_at = ? WHERE idempotency_key = ?",
            (status_value, json.dumps(data, default=str), updated_at, idempotency_key),
        )


def get_idempotent_action(idempotency_key: str, db_path: str = DEFAULT_DB_PATH) -> dict | None:
    if _supabase.is_configured():
        rows = (
            _supabase.get_client()
            .table("idempotent_actions")
            .select("data")
            .eq("idempotency_key", idempotency_key)
            .execute()
            .data
        )
        return rows[0]["data"] if rows else None
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT data FROM idempotent_actions WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        return json.loads(row[0]) if row else None


def list_pending_idempotent_actions(db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    """Startup/health-check recovery path — see
    core.idempotency_ledger.list_stuck_pending_actions, which is the
    intended caller. Deliberately returns every PENDING row rather than
    pre-filtering by age; age-based "is this actually stuck" judgment lives
    in that caller, not here."""
    if _supabase.is_configured():
        rows = (
            _supabase.get_client()
            .table("idempotent_actions")
            .select("data")
            .eq("status", "pending")
            .execute()
            .data
        )
        return [r["data"] for r in rows]
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT data FROM idempotent_actions WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
        return [json.loads(r[0]) for r in rows]
