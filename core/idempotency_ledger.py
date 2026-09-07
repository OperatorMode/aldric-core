"""
Idempotency Ledger.

Not one of the eight governance documents' own concepts — a genuinely
infrastructural one that only became necessary once core.capability_broker
started calling real external APIs (CLAUDE.md Section 8; README gap-list
item 9). Between "the send_email call was made" and "the digest recorded
it succeeded" there is a window where a crash, a retry, or a re-run of the
same turn could cause the same email to send twice or the same calendar
event to be created twice. This is deliberately NOT a tier/permission
decision — core.pa_action_kernel.classify_tier() still, and only, decides
WHETHER an action may run at all; this module decides, independently of
that and after that gate has already cleared, whether *this specific
attempt* has already been made — and if so, refuses to make it again
rather than trusting the caller not to retry.

Design, deliberately simple (three states, one-directional, append-mostly):

  1. Before executing, claim the action's idempotency key with a PENDING
     row. The claim is a race-free INSERT (storage.db.claim_idempotency_key
     relies on the table's PRIMARY KEY, not a SELECT-then-INSERT that a
     concurrent caller could slip through), not a decision this module
     makes by inspecting prior state first.
  2. If the claim fails, a row already exists — do not execute. Return the
     stored result if it's COMPLETED; raise if it's already FAILED (the
     caller must opt in to a retry, `retry_failed=True`, rather than one
     happening silently); raise AlreadyInFlightError if it's still PENDING
     (a concurrent or crashed-mid-flight attempt — see
     `list_stuck_pending_actions` for how that gets noticed, never
     auto-resolved).
  3. If the claim succeeds, run the executor exactly once, then flip the
     row to COMPLETED (with the result) or FAILED (with the error) —
     never leave it PENDING once the executor has returned or raised.

This module intentionally does not decide what "stuck" means or retry
anything on its own initiative — no background job, no automatic
re-execution. `list_stuck_pending_actions` only surfaces candidates; a
human, or a higher-level caller that can check the real external system
("did this email actually send?"), decides what to do with them. Deciding
that automatically from a heuristic would be exactly the kind of
"resolve an execution-integrity fact by guessing instead of checking
ground truth" this codebase avoids everywhere else (CLAUDE.md Section 1).
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

from models.schemas import IdempotentActionRecord, LedgerStatus, compute_idempotency_key
from storage import db
from storage.event_log import write_event


class AlreadyInFlightError(RuntimeError):
    """Raised when `run_idempotent` finds an existing PENDING row for this
    idempotency key: some other attempt — concurrent, or crashed before it
    could flip the row to COMPLETED/FAILED — already claimed it. The
    caller must not treat this as "safe to execute anyway"; see the module
    docstring and `list_stuck_pending_actions` for the actual recovery
    path."""


class ActionAlreadyFailedError(RuntimeError):
    """Raised when `run_idempotent` finds an existing FAILED row for this
    idempotency key and `retry_failed=False` (the default). This module
    never silently retries a failed action on the caller's behalf — the
    caller decides, explicitly, whether a fresh attempt is warranted."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run_idempotent(
    tool_name: str,
    arguments: dict,
    executor: Callable[[str, dict], dict],
    idempotency_key: Optional[str] = None,
    retry_failed: bool = False,
    db_path: str = db.DEFAULT_DB_PATH,
) -> dict:
    """Run `executor(tool_name, arguments)` at most once per idempotency
    key.

    `idempotency_key` should be supplied by the caller whenever a stable
    per-attempt identifier already exists (an AdjudicationRecord id, an
    operator-turn id) — that is the strongest guarantee, because it
    survives even a caller that reconstructs slightly-different-looking
    but logically-equivalent `arguments` on retry. When omitted, this
    falls back to `models.schemas.compute_idempotency_key` — a
    deterministic hash of (tool_name, arguments) — which still dedupes the
    common case: the exact same call proposed twice (e.g. a crashed
    process re-processing the same turn on restart).

    Intended call site: aldric_chat._execute_cleared_tool_call, wrapped
    directly around its existing `capability_broker.execute_action(...)`
    call. This function is a thin wrapper around that call, never a
    replacement for the governance gates (classify_tier and everything
    upstream of it) that already ran before either is ever reached — see
    CLAUDE.md Section 8."""
    key = idempotency_key or compute_idempotency_key(tool_name, arguments)

    record = IdempotentActionRecord(
        idempotency_key=key, tool_name=tool_name, arguments=arguments, status=LedgerStatus.PENDING,
    )
    claimed = db.claim_idempotency_key(record, allow_reclaim_failed=retry_failed, db_path=db_path)

    if not claimed:
        existing = db.get_idempotent_action(key, db_path=db_path)
        existing_status = LedgerStatus(existing["status"]) if existing else None
        write_event("IDEMPOTENCY_DUPLICATE_SUPPRESSED", {
            "idempotency_key": key, "tool_name": tool_name,
            "status": existing_status.value if existing_status else None,
        })
        if existing_status == LedgerStatus.COMPLETED:
            return (existing or {}).get("result") or {}
        if existing_status == LedgerStatus.FAILED:
            raise ActionAlreadyFailedError(
                f"Action with idempotency_key={key!r} ({tool_name}) already failed once "
                f"({(existing or {}).get('error')!r}); pass retry_failed=True to try again."
            )
        # PENDING, or (defensively) no row found even though the claim
        # failed — either way, refuse to execute rather than guess.
        raise AlreadyInFlightError(
            f"Action with idempotency_key={key!r} ({tool_name}) is already claimed "
            f"(status={existing_status.value if existing_status else 'unknown'}) — not executing again."
        )

    write_event("IDEMPOTENCY_ACTION_STARTED", {"idempotency_key": key, "tool_name": tool_name})
    try:
        result = executor(tool_name, arguments)
    except Exception as exc:
        db.update_idempotent_action_status(
            key, LedgerStatus.FAILED, error=str(exc), updated_at=_now(), db_path=db_path,
        )
        write_event("IDEMPOTENCY_ACTION_FAILED", {
            "idempotency_key": key, "tool_name": tool_name, "error": str(exc),
        })
        raise

    db.update_idempotent_action_status(
        key, LedgerStatus.COMPLETED, result=result, updated_at=_now(), db_path=db_path,
    )
    write_event("IDEMPOTENCY_ACTION_COMPLETED", {"idempotency_key": key, "tool_name": tool_name})
    return result


def list_stuck_pending_actions(
    older_than_seconds: int = 300, db_path: str = db.DEFAULT_DB_PATH,
) -> list[dict]:
    """Startup/health-check recovery path (module docstring, step 2): rows
    still PENDING after `older_than_seconds` almost certainly mean the
    process that claimed them died before it could flip the row to
    COMPLETED or FAILED — the executor itself may or may not have actually
    run against the real external system. This function only surfaces
    candidates; it never re-executes or auto-resolves them. Only something
    that can check ground truth (did this email really send?) — a human,
    or a caller with that visibility — can safely decide what happened."""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=older_than_seconds)).isoformat()
    stuck = [
        row for row in db.list_pending_idempotent_actions(db_path=db_path)
        if row["created_at"] < cutoff
    ]
    if stuck:
        write_event("IDEMPOTENCY_STUCK_PENDING_FOUND", {
            "count": len(stuck),
            "idempotency_keys": [row["idempotency_key"] for row in stuck],
        })
    return stuck
