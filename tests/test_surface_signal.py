"""
Tests for core.surface_signal — the bridge between long-term memory's scope
tags and Learning Governance's Surface objects (see that module's docstring
for why this doesn't need the still-open real surface matcher). No LLM calls
here, same as tests/test_long_term_memory.py for the module it pairs with;
llm/aldric_reply.py and aldric_chat.py are what actually drive this during a
real session (see tests/test_aldric_reply.py and
tests/test_aldric_chat_flow.py for that side).
"""
from core.pa_action_kernel import generate_daily_digest
from core.surface_signal import record_confirmation, record_instruction_or_correction
from models.schemas import ConfidenceState
from storage import db


def test_first_instruction_for_a_new_scope_creates_a_surface_with_no_conflict_record():
    surface = record_instruction_or_correction("client:acme", "Formal tone, no jokes.", had_prior_preference=False)
    assert surface.surface_id == "client:acme"
    assert surface.description == "Formal tone, no jokes."
    assert surface.state == ConfidenceState.OBSERVING
    assert surface.correction_count == 0
    assert db.list_conflict_records("client:acme") == []


def test_first_instruction_logs_a_new_surface_candidate_to_the_digest():
    record_instruction_or_correction("client:acme", "Formal tone.", had_prior_preference=False)
    digest = generate_daily_digest()
    candidates = [e for e in digest if e["category"] == "new_surface_candidate"]
    assert len(candidates) == 1
    assert candidates[0]["payload"]["surface_id"] == "client:acme"


def test_overriding_an_established_scope_is_a_correction_not_a_fresh_instruction():
    record_instruction_or_correction("client:acme", "Formal tone.", had_prior_preference=False)
    surface = record_instruction_or_correction("client:acme", "Actually, warmer and casual.", had_prior_preference=True)

    assert surface.description == "Actually, warmer and casual."
    assert surface.correction_count == 1
    records = db.list_conflict_records("client:acme")
    assert len(records) == 1
    assert records[0]["model_held_before"] == "Formal tone."
    assert records[0]["operator_instruction"] == "Actually, warmer and casual."


def test_had_prior_preference_true_but_no_surface_description_yet_is_still_treated_as_instruction():
    """A scope can have a StandingPreference on record from before this
    Surface layer existed, with no matching Surface (or a Surface with an
    empty description) yet — there's genuinely nothing to have conflicted
    with, so this must not be misfiled as a correction with a blank
    'model_held_before'."""
    surface = record_instruction_or_correction("legacy:scope", "First real instruction.", had_prior_preference=True)
    assert surface.correction_count == 0
    assert db.list_conflict_records("legacy:scope") == []


def test_record_confirmation_returns_none_when_no_surface_exists_yet():
    assert record_confirmation("never:mentioned") is None


def test_record_confirmation_increments_an_existing_surfaces_confirmation_count():
    record_instruction_or_correction("client:acme", "Formal tone.", had_prior_preference=False)
    updated = record_confirmation("client:acme")
    assert updated is not None
    assert updated.confirmation_count == 1
