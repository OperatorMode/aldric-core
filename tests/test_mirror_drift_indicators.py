"""
Proves 06_Learning_Governance.md Section 4.2's first two Mirror Drift
Indicators are real, structural checks over stored data — not narrated
placeholders — and that indicators 3 and 4 (which need an outcome-tracking
data model this codebase doesn't define yet) are honestly left alone rather
than faked. No test in this file was previously exercising
`detect_mirror_drift` or `apply_mirror_drift_response` at all.

Indicator 1 ("confidence climbing with zero corrections") was already
implemented; these tests are its first real coverage. Indicator 2
("corrections tapering off over time while confirmations keep climbing")
is the new structural addition here — a genuine time-series comparison of
the conflict record's own timestamps, split at the surface's own lifetime
midpoint, with no interpretation of *why* the rate dropped (Section 3.5's
Non-Interpretation Rule: that determination is reserved for the operator,
Section 4.3).
"""
import datetime as dt

from core.learning_governance import apply_mirror_drift_response, detect_mirror_drift
from models.schemas import ConfidenceState, ConflictRecordEntry, Surface
from storage import db


def _iso(days_ago: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()


def _surface(confirmation_count=5, state=ConfidenceState.EXECUTABLE, created_days_ago=10) -> Surface:
    surface = Surface(
        description="test surface", state=state, confirmation_count=confirmation_count,
        created_at=_iso(created_days_ago),
    )
    db.save_surface(surface)
    return surface


def _append_correction(surface: Surface, days_ago: float) -> None:
    entry = ConflictRecordEntry(
        surface_id=surface.surface_id, model_held_before="old", operator_instruction="new",
        domain="test", created_at=_iso(days_ago),
    )
    db.append_conflict_record(entry)


# --- Indicator 1: climbing confidence, zero corrections -------------------

def test_indicator_1_fires_when_confidence_climbs_with_zero_corrections():
    surface = _surface(confirmation_count=5, state=ConfidenceState.EXECUTABLE)
    assessment = detect_mirror_drift(surface)
    assert assessment.flagged
    assert any("indicator 1" in i for i in assessment.indicators)


def test_indicator_1_does_not_fire_below_confirmation_threshold():
    surface = _surface(confirmation_count=1, state=ConfidenceState.EXECUTABLE)
    assessment = detect_mirror_drift(surface)
    assert not assessment.flagged


def test_indicator_1_does_not_fire_outside_executable_state():
    surface = _surface(confirmation_count=10, state=ConfidenceState.DEVELOPING)
    assessment = detect_mirror_drift(surface)
    assert not assessment.flagged


# --- Indicator 2: corrections tapering over the surface's lifetime -------

def test_indicator_2_fires_when_corrections_taper_over_the_surface_lifetime():
    surface = _surface(confirmation_count=5, created_days_ago=10)
    # Three corrections in the surface's first half of life, none since:
    _append_correction(surface, days_ago=9)
    _append_correction(surface, days_ago=8)
    _append_correction(surface, days_ago=7)
    assessment = detect_mirror_drift(surface)
    assert assessment.flagged
    assert any("indicator 2" in i for i in assessment.indicators)
    assert any("3 earlier vs 0 more recent" in i for i in assessment.indicators)


def test_indicator_2_does_not_fire_when_corrections_keep_pace_or_increase():
    surface = _surface(confirmation_count=5, created_days_ago=10)
    _append_correction(surface, days_ago=9)   # earlier half
    _append_correction(surface, days_ago=2)   # later half
    _append_correction(surface, days_ago=1)   # later half
    assessment = detect_mirror_drift(surface)
    assert not any("indicator 2" in i for i in assessment.indicators)


def test_indicator_2_does_not_fire_with_fewer_than_two_records():
    surface = _surface(confirmation_count=5, created_days_ago=10)
    _append_correction(surface, days_ago=9)
    assessment = detect_mirror_drift(surface)
    assert not any("indicator 2" in i for i in assessment.indicators)


def test_indicator_2_does_not_fire_below_confirmation_threshold():
    surface = _surface(confirmation_count=2, created_days_ago=10)
    _append_correction(surface, days_ago=9)
    _append_correction(surface, days_ago=8)
    assessment = detect_mirror_drift(surface)
    assert not any("indicator 2" in i for i in assessment.indicators)


def test_no_indicators_on_a_young_clean_surface():
    surface = _surface(confirmation_count=1, state=ConfidenceState.OBSERVING, created_days_ago=0.1)
    assessment = detect_mirror_drift(surface)
    assert not assessment.flagged


# --- Response step (Section 4.3) ------------------------------------------

def test_apply_mirror_drift_response_flags_surface_when_assessment_is_flagged():
    surface = _surface(confirmation_count=5, state=ConfidenceState.EXECUTABLE)
    assessment = detect_mirror_drift(surface)
    assert assessment.flagged
    updated = apply_mirror_drift_response(surface, assessment)
    assert updated.mirror_drift_flagged is True


def test_apply_mirror_drift_response_leaves_surface_untouched_when_clean():
    surface = _surface(confirmation_count=1, state=ConfidenceState.OBSERVING, created_days_ago=0.1)
    assessment = detect_mirror_drift(surface)
    assert not assessment.flagged
    updated = apply_mirror_drift_response(surface, assessment)
    assert updated.mirror_drift_flagged is False
