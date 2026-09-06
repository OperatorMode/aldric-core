"""
Append-only JSONL audit ledger.

Governance Chain / KSP-1 Section 1.5 (Instrumentation): logs are telemetry,
they do not influence inference, and — per the Infrastructure Specification
Items standing rule from the project instructions — "stateless pipeline is
not acceptable" and the audit trail foundation must be maintained.

This module only ever appends. There is no update or delete function. If you
need to change how logging behaves, add a new event type — do not add a way
to rewrite history.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading

from storage import _supabase

_LOCK = threading.Lock()
DEFAULT_LOG_PATH = os.path.join(os.path.dirname(__file__), "event_log.jsonl")


def write_event(event_type: str, payload: dict, log_path: str = DEFAULT_LOG_PATH) -> dict:
    """Append one event. Returns the event actually written (useful for tests).

    Backend is chosen by whether SUPABASE_URL/SUPABASE_KEY are set (see
    storage/_supabase.py) — the local JSONL file otherwise. Either way this
    stays append-only: no update or delete path exists for either backend."""
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    entry = {"event": event_type, "timestamp": timestamp, "data": payload}

    if _supabase.is_configured():
        _supabase.get_client().table("event_log").insert(
            {"event_type": event_type, "payload": payload, "created_at": timestamp}
        ).execute()
        return entry

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with _LOCK:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    return entry


def read_events(log_path: str = DEFAULT_LOG_PATH, since_iso: str | None = None) -> list[dict]:
    """Read events, optionally only those at/after a given ISO timestamp.

    Used by the Daily Digest generator (PA Action Kernel Component 7) — the
    digest reads everything since the last digest with no filtering step.
    """
    if _supabase.is_configured():
        query = (
            _supabase.get_client()
            .table("event_log")
            .select("event_type,payload,created_at")
            .order("created_at")
        )
        if since_iso is not None:
            query = query.gte("created_at", since_iso)
        rows = query.execute().data
        return [
            {"event": r["event_type"], "timestamp": r["created_at"], "data": r["payload"]}
            for r in rows
        ]

    if not os.path.exists(log_path):
        return []
    events = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if since_iso is None or entry["timestamp"] >= since_iso:
                events.append(entry)
    return events
