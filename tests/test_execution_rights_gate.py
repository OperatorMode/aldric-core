"""
PA Action Kernel Section 4.2, "First Executable Crossing" — a surface's
`state` reaching Executable (a confidence assessment, Section 1.5) is
deliberately NOT the same thing as the operator's live sign-off that
execution may actually run against it. This file proves that second gate is
real: classify_tier() holds at Tier C for an Executable surface until
execution_rights_confirmed is set, and only core.learning_governance's
confirm_execution_rights (never the surface itself, never ALDRIC) can set it.
"""
from core.learning_governance import confirm_execution_rights
from core.pa_action_kernel import build_action_request, classify_tier, generate_daily_digest
from models.schemas import ConfidenceState, Surface
from storage import db


def _executable_surface(execution_rights_confirmed: bool = False) -> Surface:
    return Surface(
        description="test", state=ConfidenceState.EXECUTABLE, confirmation_count=10,
        execution_rights_confirmed=execution_rights_confirmed,
    )


def test_executable_surface_without_execution_rights_still_holds_at_tier_c():
    action = build_action_request(tool_name="log_internal_note", arguments={})
    decision = classify_tier(
        action=action, surface=_executable_surface(execution_rights_confirmed=False),
        drift_level=None, mirror_drift_flagged=False,
    )
    assert decision.tier.value == "tier_c"
    assert any("execution rights" in r for r in decision.reasons)


def test_executable_surface_with_execution_rights_can_reach_tier_a():
    action = build_action_request(tool_name="log_internal_note", arguments={})
    decision = classify_tier(
        action=action, surface=_executable_surface(execution_rights_confirmed=True),
        drift_level=None, mirror_drift_flagged=False,
    )
    assert decision.tier.value == "tier_a"


def test_confirm_execution_rights_sets_the_flag_and_persists_it():
    surface = Surface(description="test", state=ConfidenceState.EXECUTABLE, confirmation_count=10)
    db.save_surface(surface)

    updated = confirm_execution_rights(surface)

    assert updated.execution_rights_confirmed is True
    stored = db.get_surface(surface.surface_id)
    assert stored["execution_rights_confirmed"] is True


def test_confirm_execution_rights_logs_to_the_digest():
    surface = Surface(description="test", state=ConfidenceState.EXECUTABLE, confirmation_count=10)
    db.save_surface(surface)

    confirm_execution_rights(surface)

    digest = generate_daily_digest()
    entries = [e for e in digest if e["payload"].get("execution_rights_confirmed")]
    assert len(entries) == 1
    assert entries[0]["payload"]["surface_id"] == surface.surface_id


def test_a_surface_still_developing_is_untouched_by_the_execution_rights_gate():
    """Developing surfaces already hold at Tier C via the earlier
    'state != Executable' check — proves the new gate doesn't change or
    duplicate that reason."""
    surface = Surface(description="test", state=ConfidenceState.DEVELOPING)
    action = build_action_request(tool_name="log_internal_note", arguments={})
    decision = classify_tier(action=action, surface=surface, drift_level=None, mirror_drift_flagged=False)
    assert decision.tier.value == "tier_c"
    assert any("not Executable" in r for r in decision.reasons)
    assert not any("execution rights" in r for r in decision.reasons)
