"""
Proves Learning Governance's self-model visibility (Section 6.2) actually
reaches core.pa_action_kernel's Daily Digest, not just the event log.
Before this, individual_correction_observation()/pattern_observation()
built the right dict shape but nothing ever called log_digest_entry with it
— a correction could happen and never show up in generate_daily_digest().
"""
import datetime as dt

from core.learning_governance import apply_confirmation, apply_correction
from core.pa_action_kernel import generate_daily_digest
from models.schemas import ConfidenceState, Surface
from storage import db


def test_every_correction_produces_a_correction_observation_in_the_digest():
    surface = Surface(description="v0", state=ConfidenceState.EXECUTABLE)
    db.save_surface(surface)

    apply_correction(surface, operator_instruction="v1", domain="pricing")

    digest = generate_daily_digest()
    observations = [e for e in digest if e["category"] == "correction_observation"]
    assert len(observations) == 1
    assert observations[0]["payload"]["model_held_before"] == "v0"
    assert observations[0]["payload"]["operator_instruction"] == "v1"
    assert observations[0]["payload"]["domain"] == "pricing"


def test_clustered_corrections_also_produce_a_pattern_observation():
    """DEFAULT_PATTERN_CLUSTER_SIZE is 3 — the third correction on the same
    surface should trigger a pattern observation alongside its own
    individual correction observation (Section 3.5: 'in addition to, not
    instead of')."""
    surface = Surface(description="v0", state=ConfidenceState.EXECUTABLE)
    db.save_surface(surface)

    for i in range(1, 4):
        surface, _entry = apply_correction(surface, operator_instruction=f"v{i}", domain="pricing")

    digest = generate_daily_digest()
    observations = [e for e in digest if e["category"] == "correction_observation"]
    patterns = [e for e in digest if e["category"] == "pattern_observation"]
    assert len(observations) == 3
    assert len(patterns) == 1
    assert patterns[0]["payload"]["domain"] == "pricing"
    assert patterns[0]["payload"]["correction_count"] == 3


def test_pattern_observation_does_not_fire_before_the_cluster_threshold():
    surface = Surface(description="v0", state=ConfidenceState.EXECUTABLE)
    db.save_surface(surface)

    surface, _entry = apply_correction(surface, operator_instruction="v1", domain="pricing")
    digest = generate_daily_digest()
    assert not any(e["category"] == "pattern_observation" for e in digest)


def _iso(days_ago: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()


def test_mirror_drift_flag_reaches_the_digest_once_on_the_transition_into_flagged():
    """Confirmation is the trigger here (Indicator 1: confidence climbing
    with zero corrections) — proves apply_confirmation's own drift re-check
    fires the digest entry, not just apply_correction's."""
    surface = Surface(
        description="test", state=ConfidenceState.EXECUTABLE, confirmation_count=4,
        created_at=_iso(10),
    )
    db.save_surface(surface)

    # Fifth confirmation crosses indicator 1's confirmation_count >= 5
    # threshold with zero corrections on record.
    updated = apply_confirmation(surface)
    assert updated.mirror_drift_flagged is True

    digest = generate_daily_digest()
    flags = [e for e in digest if e["category"] == "mirror_drift_flag"]
    assert len(flags) == 1
    assert flags[0]["payload"]["surface_id"] == surface.surface_id

    # A further confirmation on an already-flagged surface must not log a
    # second, repeat digest entry for the same standing flag.
    reloaded = Surface(**db.get_surface(surface.surface_id))
    apply_confirmation(reloaded)
    digest_again = generate_daily_digest()
    assert not any(e["category"] == "mirror_drift_flag" for e in digest_again)
