"""
Tests for core.idempotency_ledger — the execution-integrity guard around
core.capability_broker.execute_action (see that module's and
idempotency_ledger's own docstrings). Nothing here touches a real external
API: `executor` is always a plain fake recording calls, exactly like
tests/test_capability_broker.py fakes the Google service objects.
"""
import pytest

from core import idempotency_ledger
from models.schemas import LedgerStatus
from storage import db


def _counting_executor(calls, result=None, error=None):
    def _executor(tool_name, arguments):
        calls.append((tool_name, arguments))
        if error is not None:
            raise error
        return result if result is not None else {"ok": True}
    return _executor


def test_first_call_executes_and_records_completed():
    calls = []
    result = idempotency_ledger.run_idempotent(
        "send_email", {"to": "a@example.com"}, _counting_executor(calls, {"message_id": "m1"}),
    )
    assert result == {"message_id": "m1"}
    assert len(calls) == 1


def test_a_second_call_with_the_same_arguments_does_not_re_execute():
    calls = []
    executor = _counting_executor(calls, {"message_id": "m1"})

    first = idempotency_ledger.run_idempotent("send_email", {"to": "a@example.com"}, executor)
    second = idempotency_ledger.run_idempotent("send_email", {"to": "a@example.com"}, executor)

    assert first == second == {"message_id": "m1"}
    assert len(calls) == 1  # the broker was only actually called once


def test_an_explicit_idempotency_key_dedupes_even_with_different_arguments():
    calls = []
    executor = _counting_executor(calls, {"message_id": "m1"})

    idempotency_ledger.run_idempotent(
        "send_email", {"to": "a@example.com", "attempt": 1}, executor, idempotency_key="turn-42",
    )
    result = idempotency_ledger.run_idempotent(
        "send_email", {"to": "a@example.com", "attempt": 2}, executor, idempotency_key="turn-42",
    )

    assert result == {"message_id": "m1"}
    assert len(calls) == 1


def test_different_arguments_without_a_shared_key_execute_independently():
    calls = []
    executor = _counting_executor(calls, {"message_id": "m1"})

    idempotency_ledger.run_idempotent("send_email", {"to": "a@example.com"}, executor)
    idempotency_ledger.run_idempotent("send_email", {"to": "b@example.com"}, executor)

    assert len(calls) == 2


def test_a_pending_row_raises_already_in_flight_instead_of_executing():
    from models.schemas import IdempotentActionRecord

    key = "manually-claimed"
    record = IdempotentActionRecord(idempotency_key=key, tool_name="send_email", arguments={})
    claimed = db.claim_idempotency_key(record)
    assert claimed is True  # sanity: we now own this key, simulating an in-flight/crashed attempt

    calls = []
    with pytest.raises(idempotency_ledger.AlreadyInFlightError):
        idempotency_ledger.run_idempotent(
            "send_email", {}, _counting_executor(calls), idempotency_key=key,
        )
    assert calls == []  # never executed


def test_a_failed_action_raises_and_is_not_silently_retried():
    calls = []
    executor = _counting_executor(calls, error=RuntimeError("network down"))

    with pytest.raises(RuntimeError):
        idempotency_ledger.run_idempotent("send_email", {"to": "a@example.com"}, executor)
    assert len(calls) == 1

    # A second attempt with the same key does NOT call the executor again —
    # it raises a distinct, explicit error instead of retrying silently.
    with pytest.raises(idempotency_ledger.ActionAlreadyFailedError):
        idempotency_ledger.run_idempotent("send_email", {"to": "a@example.com"}, executor)
    assert len(calls) == 1


def test_retry_failed_true_lets_a_failed_action_run_again():
    calls = []
    failing_executor = _counting_executor(calls, error=RuntimeError("network down"))

    with pytest.raises(RuntimeError):
        idempotency_ledger.run_idempotent(
            "send_email", {"to": "a@example.com"}, failing_executor, idempotency_key="retry-me",
        )

    succeeding_calls = []
    succeeding_executor = _counting_executor(succeeding_calls, {"message_id": "m2"})
    result = idempotency_ledger.run_idempotent(
        "send_email", {"to": "a@example.com"}, succeeding_executor,
        idempotency_key="retry-me", retry_failed=True,
    )
    assert result == {"message_id": "m2"}
    assert len(succeeding_calls) == 1


def test_retry_failed_false_never_overwrites_a_pending_or_completed_row():
    calls = []
    executor = _counting_executor(calls, {"message_id": "m1"})
    idempotency_ledger.run_idempotent(
        "send_email", {"to": "a@example.com"}, executor, idempotency_key="done-key",
    )
    # Even with retry_failed=True, a COMPLETED row must never re-execute —
    # only FAILED rows are reclaimable.
    result = idempotency_ledger.run_idempotent(
        "send_email", {"to": "a@example.com"}, executor, idempotency_key="done-key", retry_failed=True,
    )
    assert result == {"message_id": "m1"}
    assert len(calls) == 1


def test_ledger_row_is_completed_with_the_stored_result():
    calls = []
    executor = _counting_executor(calls, {"message_id": "m1"})
    idempotency_ledger.run_idempotent(
        "send_email", {"to": "a@example.com"}, executor, idempotency_key="check-row",
    )
    row = db.get_idempotent_action("check-row")
    assert row["status"] == LedgerStatus.COMPLETED.value
    assert row["result"] == {"message_id": "m1"}


def test_list_stuck_pending_actions_only_surfaces_old_pending_rows():
    import datetime as dt

    from models.schemas import IdempotentActionRecord

    # A fresh PENDING row (simulating an in-flight attempt) is not "stuck" yet.
    fresh = IdempotentActionRecord(idempotency_key="fresh", tool_name="send_email", arguments={})
    db.claim_idempotency_key(fresh)
    assert idempotency_ledger.list_stuck_pending_actions(older_than_seconds=300) == []

    # A COMPLETED row is never "stuck", regardless of age — checked with a
    # 0-second cutoff (which would otherwise catch the still-PENDING "fresh"
    # row above) to isolate that this row specifically is excluded.
    calls = []
    idempotency_ledger.run_idempotent(
        "send_email", {}, _counting_executor(calls), idempotency_key="finished",
    )
    assert "finished" not in [
        row["idempotency_key"] for row in idempotency_ledger.list_stuck_pending_actions(older_than_seconds=0)
    ]

    # A PENDING row IS surfaced once it's older than the cutoff. Backdated
    # explicitly rather than relying on wall-clock time passing during the
    # test, so this isn't flaky under a slow CI run.
    ten_minutes_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)).isoformat()
    stale = IdempotentActionRecord(
        idempotency_key="stale", tool_name="send_email", arguments={}, created_at=ten_minutes_ago,
    )
    db.claim_idempotency_key(stale)
    stuck = idempotency_ledger.list_stuck_pending_actions(older_than_seconds=300)
    assert [row["idempotency_key"] for row in stuck] == ["stale"]
