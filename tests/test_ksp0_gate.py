import pytest

from core.ksp0_dsd import DSDGate, DSDGateError, build_dsd


def _valid_fields():
    return dict(
        decision_locus="Whether to accept the Q3 retainer renewal",
        operational_domain="Client account management",
        authority_boundary="Operator decides; ALDRIC may draft, not send",
        time_horizon="Decision needed by end of week",
        constraints_and_invariants=["No discount below list price"],
        risk_posture="Low tolerance for pricing errors",
    )


def test_completeness_test_fails_on_missing_field():
    dsd = build_dsd(**_valid_fields())
    dsd.risk_posture = ""  # bypasses pydantic validator by direct mutation, simulating a
                            # partially-populated DSD arriving from an upstream extraction step
    gate = DSDGate(dsd)
    with pytest.raises(DSDGateError):
        gate.completeness_check()


def test_ambiguous_confirmation_is_rejected():
    dsd = build_dsd(**_valid_fields())
    gate = DSDGate(dsd)
    with pytest.raises(DSDGateError):
        gate.confirm("sure, sounds good I guess")


def test_canonical_confirmation_locks_the_dsd():
    dsd = build_dsd(**_valid_fields())
    gate = DSDGate(dsd)
    gate.confirm("confirmed")
    locked = gate.lock()
    assert locked.confirmed is True
    assert locked.locked is True


def test_locked_dsd_is_immutable():
    dsd = build_dsd(**_valid_fields())
    gate = DSDGate(dsd)
    gate.confirm("confirmed")
    gate.lock()
    with pytest.raises(DSDGateError):
        gate.confirm("confirmed")  # attempting to re-confirm/edit after lock


def test_lock_refused_without_confirmation():
    dsd = build_dsd(**_valid_fields())
    gate = DSDGate(dsd)
    with pytest.raises(DSDGateError):
        gate.lock()
