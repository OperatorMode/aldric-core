"""
Shared Supabase client helper for the storage layer.

Whether ALDRIC's governance records (digest entries, adjudications, the
event log) live in the local SQLite file or in Supabase's Postgres is a
single environment-variable decision, not a code change: set SUPABASE_URL
and SUPABASE_KEY (the project's service role key) and every write in
storage/ routes there instead. Nothing in core/ needs to know which backend
is active — that split is confined entirely to storage/db.py and
storage/event_log.py, which is the point: swapping the durability layer
should never require touching governance logic.
"""
from __future__ import annotations

import os

try:
    from supabase import create_client
except ImportError:  # pragma: no cover - exercised only if dependency missing
    create_client = None  # type: ignore

_client = None


def is_configured() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


def get_client():
    global _client
    if _client is None:
        if create_client is None:
            raise RuntimeError(
                "The `supabase` package is not installed. `pip install supabase`."
            )
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _client = create_client(url, key)
    return _client
