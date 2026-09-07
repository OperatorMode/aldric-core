"""
Tests for core.learning_governance.apply_confirmation / promote_surface_state
— the confidence-growth half of Section 2.2 that test_correction_absolute.py
and test_mirror_drift_indicators.py don't cover. Proves confidence only ever
moves through this one deterministic path (never a self-elevating branch —
Section 6.3), that promotion thresholds are real state transitions rather
than narrated numbers, and that the digest actually receives a
confidence_change entry when (and only when) a transition really happens.
"""
from core.learning_governance import (
    DEFAULT_PROMOTION_THRESHOLDS,
    apply_confirmation,
    promote_surface_state,
)
from core.pa_action_kernel import generate_daily_digest
from models.schemas import ConfidenceState, Surface
from storage import db


def test_confirmation_increments_the_counter_and_persists():
    surface = Surface(description="draft client emails in a formal tone", state=ConfidenceState.OBSERVING)
    db.save_surface(surface)

    updated = apply_confirmation(surface)

    assert updated.confirmation_count == 1
    stored = db.get_surface(surface.surface_id)
    assert stored["confirmation_count"] == 1


def test_promotion_thresholds_move_state_observing_to_developing_to_executable():
    surface = Surface(description="test", state=ConfidenceState.OBSERVING)
    db.save_surface(surface)

    for _ in range(DEFAULT_PROMOTION_THRESHOLDS["developing_at"]):
        surface = apply_confirmation(surface)
    assert surface.state == ConfidenceState.DEVELOPING

    for _ in range(DEFAULT_PROMOTION_THRESHOLDS["executable_at"] - surface.confirmation_count):
        surface = apply_confirmation(surface)
    assert surface.state == ConfidenceState.EXECUTABLE


def test_custom_thresholds_are_respected():
    surface = Surface(description="test", state=ConfidenceState.OBSERVING)
    db.save_surface(surface)
    updated = apply_confirmation(surface, thresholds={"developing_at": 1, "executable_at": 2})
    assert updated.state == ConfidenceState.DEVELOPING
    updated = apply_confirmation(updated, thresholds={"developing_at": 1, "executable_at": 2})
    assert updated.state == ConfidenceState.EXECUTABLE


def test_promotion_never_moves_a_suspended_surface():
    """Section 8.2's lifecycle states are operator-set — confirmation volume
    must not silently override a Suspended or Retired surface back into an
    active state."""
    surface = Surface(description="test", state=ConfidenceState.SUSPENDED, confirmation_count=50)
    result = promote_surface_state(surface)
    assert result.state == ConfidenceState.SUSPENDED


def test_confidence_change_reaches_the_digest_only_on_a_real_transition():
    surface = Surface(description="test", state=ConfidenceState.OBSERVING)
    db.save_surface(surface)

    # First confirmation (count=1) does not cross the default developing_at=2
    # threshold yet — no transition, no digest entry.
    surface = apply_confirmation(surface)
    digest = generate_daily_digest()
    assert not any(e["category"] == "confidence_change" for e in digest)

    # Second confirmation crosses into DEVELOPING — a real transition.
    surface = Surface(**db.get_surface(surface.surface_id))
    surface = apply_confirmation(surface)
    digest = generate_daily_digest()
    changes = [e for e in digest if e["category"] == "confidence_change"]
    assert len(changes) == 1
    assert changes[0]["payload"]["previous_state"] == "observing"
    assert changes[0]["payload"]["new_state"] == "developing"
