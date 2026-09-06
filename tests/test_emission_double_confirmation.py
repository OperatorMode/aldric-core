"""
Proves the Tier C two-stage confirmation gate (KSP-1 Section 3.2, KSP-0
Section 9.5, PA Action Kernel Section 3.4) cannot be collapsed into one
utterance, and that the emission-vocabulary check is exact, not fuzzy.
"""
import pytest

from core.ksp1_operator_kernel import AdjudicationBuffer, AdjudicationError
from models.schemas import AdjudicationStage


def test_permanent_tier_c_requires_two_distinct_confirmations():
    buf = AdjudicationBuffer()
    record = buf.open(dsd_ref="dsd_1", summary="Update pricing for Item A to $5,000", touches_permanent_tier_c=True)

    record = buf.confirm_content(record.adjudication_id, "confirmed")
    assert record.stage == AdjudicationStage.PENDING_EMISSION_AUTHORIZATION

    record = buf.authorize_emission(record.adjudication_id, "send it")
    assert record.stage == AdjudicationStage.EMITTED
    assert buf.is_binding(record.adjudication_id)


def test_combined_utterance_does_not_satisfy_emission():
    """The exact worked example from KSP-1 Section 3.2: 'confirmed — transmit'
    is content ratification only, and must not be accepted as emission
    authorization."""
    buf = AdjudicationBuffer()
    record = buf.open(dsd_ref="dsd_1", summary="Sign the contract", touches_permanent_tier_c=True)
    record = buf.confirm_content(record.adjudication_id, "confirmed")

    with pytest.raises(AdjudicationError):
        buf.authorize_emission(record.adjudication_id, "confirmed — transmit")

    # still not emitted
    assert not buf.is_binding(record.adjudication_id)


def test_cannot_authorize_emission_before_content_confirmed():
    buf = AdjudicationBuffer()
    record = buf.open(dsd_ref="dsd_1", summary="Change project scope", touches_permanent_tier_c=True)
    with pytest.raises(AdjudicationError):
        buf.authorize_emission(record.adjudication_id, "send it")


def test_repeating_confirmed_does_not_satisfy_emission():
    buf = AdjudicationBuffer()
    record = buf.open(dsd_ref="dsd_1", summary="Commit to a deadline", touches_permanent_tier_c=True)
    record = buf.confirm_content(record.adjudication_id, "confirmed")
    with pytest.raises(AdjudicationError):
        buf.authorize_emission(record.adjudication_id, "confirmed")


def test_non_permanent_artifact_emits_on_single_content_confirmation():
    buf = AdjudicationBuffer()
    record = buf.open(dsd_ref="dsd_1", summary="Draft a routine internal note", touches_permanent_tier_c=False)
    record = buf.confirm_content(record.adjudication_id, "confirmed")
    assert record.stage == AdjudicationStage.EMITTED
    # and there is no separate emission stage to invoke
    with pytest.raises(AdjudicationError):
        buf.authorize_emission(record.adjudication_id, "send it")


def test_ambiguous_content_confirmation_rejected():
    buf = AdjudicationBuffer()
    record = buf.open(dsd_ref="dsd_1", summary="Something", touches_permanent_tier_c=False)
    with pytest.raises(AdjudicationError):
        buf.confirm_content(record.adjudication_id, "yeah that seems fine")
