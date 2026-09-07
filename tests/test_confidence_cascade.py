"""
Tests for core.confidence_cascade — the deterministic core of "try to
resolve from memory before asking, and let a real answer to a reflective
question grow or shrink confidence instead of a plain confirmation prompt
silently teaching the self-model nothing." See that module's docstring for
the three explicit design calls this codebase's operator made, each
checked here.
"""
from core import learning_governance
from core.confidence_cascade import (
    CascadeDecision,
    ReflectiveResponse,
    apply_reflective_response,
    classify_reflective_response,
    decide_cascade,
    narrow_scope,
    park_for_later,
)
from core.ksp1_operator_kernel import LoopManager, LoopState
from models.schemas import ConfidenceState, Surface
from storage import db


def _surface(**overrides) -> Surface:
    defaults = dict(surface_id="pricing_discount", description="", state=ConfidenceState.OBSERVING)
    defaults.update(overrides)
    return Surface(**defaults)


# --- decide_cascade ---------------------------------------------------------

def test_no_surface_and_no_memory_urgent_asks_now():
    decision = decide_cascade(surface=None, has_relevant_memory=False, urgent=True)
    assert decision.resolve is False
    assert decision.ask_now is True
    assert decision.cite_memory is False


def test_no_surface_and_no_memory_not_urgent_parks():
    decision = decide_cascade(surface=None, has_relevant_memory=False, urgent=False)
    assert decision.resolve is False
    assert decision.ask_now is False


def test_high_confidence_with_memory_resolves_without_asking():
    surface = _surface(state=ConfidenceState.EXECUTABLE)
    decision = decide_cascade(surface=surface, has_relevant_memory=True, urgent=False)
    assert decision.resolve is True
    assert decision.ask_now is False
    assert decision.cite_memory is True


def test_high_confidence_without_memory_does_not_resolve():
    """Confidence alone isn't enough — there has to be something to act on."""
    surface = _surface(state=ConfidenceState.EXECUTABLE)
    decision = decide_cascade(surface=surface, has_relevant_memory=False, urgent=False)
    assert decision.resolve is False


def test_low_confidence_with_some_memory_reflects_instead_of_resolving():
    surface = _surface(state=ConfidenceState.DEVELOPING)
    decision = decide_cascade(surface=surface, has_relevant_memory=True, urgent=False)
    assert decision.resolve is False
    assert decision.cite_memory is True
    assert decision.ask_now is False  # not urgent -> park, not interrupt


def test_urgent_low_confidence_asks_now_instead_of_parking():
    surface = _surface(state=ConfidenceState.DEVELOPING)
    decision = decide_cascade(surface=surface, has_relevant_memory=True, urgent=True)
    assert decision.resolve is False
    assert decision.ask_now is True


def test_mirror_drift_flagged_surface_never_resolves_even_at_executable_confidence():
    """The operator's explicit call: a flagged Surface's confidence number
    is exactly the number under suspicion, so it can never justify skipping
    the operator, no matter how high confirmation_count climbed."""
    surface = _surface(state=ConfidenceState.EXECUTABLE, mirror_drift_flagged=True)
    decision = decide_cascade(surface=surface, has_relevant_memory=True, urgent=False)
    assert decision.resolve is False
    assert any("mirror-drift" in r for r in decision.reasons)


def test_mirror_drift_flagged_surface_still_respects_urgency_for_ask_timing():
    surface = _surface(state=ConfidenceState.EXECUTABLE, mirror_drift_flagged=True)
    decision = decide_cascade(surface=surface, has_relevant_memory=True, urgent=True)
    assert decision.resolve is False
    assert decision.ask_now is True


# --- classify_reflective_response -------------------------------------------

def test_generalize_vocabulary_matches_exactly():
    bucket, qualifier = classify_reflective_response("Yes")
    assert bucket == ReflectiveResponse.GENERALIZE
    assert qualifier is None


def test_always_ask_vocabulary_matches_exactly():
    bucket, qualifier = classify_reflective_response("No, ask every time")
    assert bucket == ReflectiveResponse.ALWAYS_ASK
    assert qualifier is None


def test_scope_narrow_extracts_the_qualifier():
    bucket, qualifier = classify_reflective_response("yes, just for Acme Corp")
    assert bucket == ReflectiveResponse.SCOPE_NARROW
    assert qualifier == "acme corp"


def test_scope_narrow_variant_phrasing():
    bucket, qualifier = classify_reflective_response("Yes, only for this client")
    assert bucket == ReflectiveResponse.SCOPE_NARROW
    assert qualifier == "this client"


def test_unrecognized_phrasing_is_ambiguous_not_guessed():
    bucket, qualifier = classify_reflective_response("hmm, maybe, let me think about it")
    assert bucket == ReflectiveResponse.AMBIGUOUS
    assert qualifier is None


def test_a_confirmation_vocabulary_word_used_off_label_does_not_smuggle_a_qualifier():
    # "yes for now" isn't "yes, just/only for <X>" — must stay ambiguous,
    # not be mis-parsed into a scope qualifier of "now".
    bucket, qualifier = classify_reflective_response("yes for now")
    assert bucket == ReflectiveResponse.AMBIGUOUS


# --- narrow_scope ------------------------------------------------------------

def test_narrow_scope_is_stable_and_distinct_from_the_general_scope():
    narrowed = narrow_scope("pricing_discount", "Acme Corp")
    assert narrowed != "pricing_discount"
    assert narrowed == narrow_scope("pricing_discount", "acme corp")  # normalized, case-insensitive


# --- apply_reflective_response -----------------------------------------------

def test_generalize_grows_confidence_on_the_general_surface():
    bucket, surface = apply_reflective_response("pricing_discount", "yes")
    assert bucket == ReflectiveResponse.GENERALIZE
    assert surface.confirmation_count == 1
    stored = db.get_surface("pricing_discount")
    assert stored is not None


def test_scope_narrow_grows_confidence_only_on_the_narrower_surface():
    bucket, surface = apply_reflective_response("pricing_discount", "yes, just for Acme Corp")
    assert bucket == ReflectiveResponse.SCOPE_NARROW
    assert surface.surface_id == "pricing_discount:acme corp"
    assert surface.confirmation_count == 1
    # The general surface was never created/touched by this at all.
    assert db.get_surface("pricing_discount") is None


def test_always_ask_applies_a_real_correction_not_just_a_decline():
    bucket, surface = apply_reflective_response(
        "pricing_discount", "no, ask every time",
        operator_instruction_if_correction="Always ask before applying a discount", domain="pricing",
    )
    assert bucket == ReflectiveResponse.ALWAYS_ASK
    assert surface.correction_count == 1
    assert surface.description == "Always ask before applying a discount"
    records = db.list_conflict_records("pricing_discount")
    assert len(records) == 1


def test_ambiguous_response_mutates_nothing():
    bucket, surface = apply_reflective_response("pricing_discount", "not sure honestly")
    assert bucket == ReflectiveResponse.AMBIGUOUS
    assert surface is None
    assert db.get_surface("pricing_discount") is None


def test_generalize_and_scope_narrow_are_independent_histories():
    """A scoped 'yes, for Acme' must never leak into the general surface's
    confidence, and a later general 'yes' must not retroactively touch the
    narrowed one either — this is the operator's explicit '=' case."""
    apply_reflective_response("pricing_discount", "yes, just for Acme Corp")
    apply_reflective_response("pricing_discount", "yes")

    general = db.get_surface("pricing_discount")
    narrowed = db.get_surface("pricing_discount:acme corp")
    assert general["confirmation_count"] == 1
    assert narrowed["confirmation_count"] == 1


# --- park_for_later ------------------------------------------------------

def test_park_for_later_parks_the_loop_and_logs_a_held_thread_digest_entry():
    manager = LoopManager()
    manager.open_loop("turn-42")

    park_for_later(manager, "turn-42", "We've done a 10% discount for repeat clients before, same here?")

    assert manager.state_of("turn-42") == LoopState.PARKED
    entries = db.list_undigested_entries()
    held = [e for e in entries if e["category"] == "held_thread"]
    assert len(held) == 1
    assert held[0]["payload"]["loop_name"] == "turn-42"
