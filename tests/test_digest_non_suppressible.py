import inspect

from core.pa_action_kernel import generate_daily_digest, log_digest_entry


def test_digest_returns_every_entry_since_last_digest():
    log_digest_entry("executed_action", {"tool": "log_internal_note"})
    log_digest_entry("held_thread", {"reason": "no surface match"})
    log_digest_entry("mirror_drift_flag", {"surface_id": "surf_1"})

    entries = generate_daily_digest()
    categories = {e["category"] for e in entries}
    assert categories == {"executed_action", "held_thread", "mirror_drift_flag"}


def test_digest_has_no_filtering_parameter():
    """Section 7.3: the digest cannot be suppressed, delayed, or modified by
    any runtime condition. Enforced here as a signature check — there is no
    `hide_categories`, `omit`, or similar parameter to pass."""
    sig = inspect.signature(generate_daily_digest)
    assert list(sig.parameters) in (["db_path"], []), (
        "generate_daily_digest must not grow a filtering parameter"
    )


def test_second_digest_call_is_empty_once_first_call_consumed_the_backlog():
    log_digest_entry("executed_action", {"tool": "log_internal_note"})
    first = generate_daily_digest()
    assert len(first) == 1
    second = generate_daily_digest()
    assert second == []
