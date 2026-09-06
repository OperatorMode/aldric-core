from core.learning_governance import apply_correction
from models.schemas import ConfidenceState, Surface
from storage import db


def test_correction_always_applies_even_at_high_confidence_executable_state():
    surface = Surface(description="Old model: always discount 10% for renewals", state=ConfidenceState.EXECUTABLE,
                       confirmation_count=100)
    db.save_surface(surface)

    updated, entry = apply_correction(
        surface, operator_instruction="No — never discount without my sign-off", domain="pricing"
    )

    assert updated.description == "No — never discount without my sign-off"
    assert updated.correction_count == 1
    assert entry.model_held_before == "Old model: always discount 10% for renewals"
    assert entry.operator_instruction == "No — never discount without my sign-off"
    assert len(db.list_conflict_records(surface.surface_id)) == 1


def test_correction_applies_regardless_of_surface_state():
    """The Correction Absolute (Section 3.1) does not carve out an exception
    for a Suspended or Degrading surface — there is no state value that
    causes apply_correction to refuse."""
    for state in ConfidenceState:
        surface = Surface(description=f"model in state {state.value}", state=state)
        db.save_surface(surface)
        updated, _entry = apply_correction(surface, operator_instruction="corrected", domain="test")
        assert updated.description == "corrected"
        assert updated.correction_count == 1


def test_repeated_corrections_each_produce_their_own_conflict_record():
    """No accumulation/weighting mechanism reverses or suppresses a
    correction because several have already landed (Section 3.3: 'does not
    create a condition where enough conflicts restore the original
    model')."""
    surface = Surface(description="v0", state=ConfidenceState.EXECUTABLE)
    db.save_surface(surface)
    for i in range(1, 4):
        surface, _entry = apply_correction(surface, operator_instruction=f"v{i}", domain="test")
    assert surface.description == "v3"
    assert surface.correction_count == 3
    assert len(db.list_conflict_records(surface.surface_id)) == 3
