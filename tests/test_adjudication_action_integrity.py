"""
Proves the fix for the informed-authorization gap: an operator confirming or
authorizing an adjudication must be acting on the exact proposed action —
response text AND any proposed tool call, with its risk classification —
not just a truncated summary of the prose.

Three things are proven here, deliberately kept in three groups:

  1. `AdjudicationRecord` carries the complete action (proposed_output,
     proposed_tool_call, tool_effective_tier, permanent_categories,
     self_reported_scope) and `action_hash` stays identical across both
     `confirm_content()` and `authorize_emission()` — the action genuinely
     cannot drift between the two stages of a real, unmodified flow.

  2. The hash check is a real, load-bearing guard, not decoration: directly
     tampering with a stored record's action-defining fields (bypassing
     AdjudicationBuffer, the way a bug or a second write path might) is
     caught by `_assert_action_unchanged` and refuses to proceed.

  3. `chat._render_adjudication()` — the pure, testable rendering function —
     actually surfaces the tool name, complete arguments, tier, categories
     and fingerprint to whatever prints it to the operator. No I/O, so this
     is tested directly without mocking `input()`.

Per the accompanying instructions: no real execution layer is added here.
This only makes the authorization boundary's own bookkeeping honest about
what it's binding.
"""
import pytest

import chat
from core.ksp1_operator_kernel import AdjudicationBuffer, AdjudicationError
from llm.governed_reply import GovernedTurnResult
from models.schemas import AdjudicationRecord, AdjudicationStage, Tier
from storage import db


def _complete_interview_step(conversation):
    return {
        "extracted_fields": {
            "decision_locus": "Whether to take an action on the operator's behalf",
            "operational_domain": "Client account management",
            "authority_boundary": "Operator decides",
            "time_horizon": "Today",
            "constraints_and_invariants": ["No unilateral commitments"],
            "risk_posture": "Low tolerance for unauthorized commitments",
        },
        "missing_fields": [],
        "next_utterance": "",
    }


def _scripted_inputs(*answers):
    it = iter(answers)

    def _fake_input(prompt=""):
        return next(it)

    return _fake_input


# --- Group 1: the action survives an honest two-stage flow intact --------

def test_action_fields_and_hash_identical_across_both_stages_of_a_tool_call():
    buf = AdjudicationBuffer()
    tool_call = {"tool_name": "update_pricing", "arguments": {"customer": "Acme", "new_price": 5000}}

    opened = buf.open(
        dsd_ref="dsd_1",
        summary="Updating the price for Acme",
        touches_permanent_tier_c=True,
        proposed_output="Updating the price now.",
        proposed_tool_call=tool_call,
        tool_effective_tier=Tier.C,
        permanent_categories=frozenset({"pricing_or_cost_commitment"}),
        self_reported_scope="finality",
    )
    original_hash = opened.action_hash
    assert original_hash  # non-empty — a real fingerprint was computed

    after_confirm = buf.confirm_content(opened.adjudication_id, "confirmed")
    assert after_confirm.action_hash == original_hash
    assert after_confirm.proposed_tool_call == tool_call
    assert after_confirm.proposed_output == "Updating the price now."
    assert after_confirm.tool_effective_tier == Tier.C
    assert after_confirm.permanent_categories == frozenset({"pricing_or_cost_commitment"})
    assert after_confirm.stage == AdjudicationStage.PENDING_EMISSION_AUTHORIZATION

    after_emit = buf.authorize_emission(opened.adjudication_id, "send it")
    assert after_emit.action_hash == original_hash
    assert after_emit.proposed_tool_call == tool_call
    assert after_emit.stage == AdjudicationStage.EMITTED


def test_action_fields_and_hash_identical_for_a_non_permanent_single_stage_action():
    buf = AdjudicationBuffer()
    tool_call = {"tool_name": "log_internal_note", "arguments": {"text": "met with client today"}}

    opened = buf.open(
        dsd_ref="dsd_1",
        summary="Logging a note",
        touches_permanent_tier_c=False,
        proposed_output="Logged.",
        proposed_tool_call=tool_call,
        tool_effective_tier=Tier.C,  # e.g. fail-closed no-surface-match, not a permanent category
        permanent_categories=frozenset(),
        self_reported_scope="exploration",
    )
    original_hash = opened.action_hash

    after_confirm = buf.confirm_content(opened.adjudication_id, "confirmed")
    assert after_confirm.action_hash == original_hash
    assert after_confirm.stage == AdjudicationStage.EMITTED
    assert after_confirm.proposed_tool_call == tool_call


# --- Group 2: the hash check is load-bearing, not decorative --------------

def test_tampering_with_proposed_output_between_stages_is_caught():
    buf = AdjudicationBuffer()
    opened = buf.open(
        dsd_ref="dsd_1", summary="s", touches_permanent_tier_c=True,
        proposed_output="Original, approved text.",
        proposed_tool_call={"tool_name": "sign_contract", "arguments": {"party": "Acme"}},
        tool_effective_tier=Tier.C,
        permanent_categories=frozenset({"contractual_terms_or_obligation"}),
        self_reported_scope="finality",
    )

    # Simulate a bug or a second write path mutating the stored record's
    # action-defining field WITHOUT recomputing action_hash — exactly what
    # AdjudicationBuffer itself never does, but nothing on disk enforces
    # that some other code path won't. Bypass the buffer entirely.
    raw = db.get_adjudication(opened.adjudication_id)
    raw["proposed_output"] = "SILENTLY SWAPPED TEXT — not what the operator saw."
    tampered = AdjudicationRecord(**raw)  # action_hash field is carried over unchanged
    db.save_adjudication(tampered)

    with pytest.raises(AdjudicationError):
        buf.confirm_content(opened.adjudication_id, "confirmed")


def test_tampering_with_proposed_tool_call_after_content_confirmation_blocks_emission():
    buf = AdjudicationBuffer()
    opened = buf.open(
        dsd_ref="dsd_1", summary="s", touches_permanent_tier_c=True,
        proposed_output="Approved text.",
        proposed_tool_call={"tool_name": "update_pricing", "arguments": {"new_price": 100}},
        tool_effective_tier=Tier.C,
        permanent_categories=frozenset({"pricing_or_cost_commitment"}),
        self_reported_scope="finality",
    )
    buf.confirm_content(opened.adjudication_id, "confirmed")

    # Swap in a much larger price AFTER content confirmation but before
    # emission authorization — the exact scenario the fingerprint exists to
    # prevent from slipping through unnoticed.
    raw = db.get_adjudication(opened.adjudication_id)
    raw["proposed_tool_call"] = {"tool_name": "update_pricing", "arguments": {"new_price": 999999}}
    tampered = AdjudicationRecord(**raw)
    db.save_adjudication(tampered)

    with pytest.raises(AdjudicationError):
        buf.authorize_emission(opened.adjudication_id, "send it")


# --- Group 3: the operator-facing render actually shows the action -------

def test_render_adjudication_shows_tool_name_arguments_tier_and_fingerprint():
    record = AdjudicationRecord(
        dsd_ref="dsd_1",
        summary="s",
        touches_permanent_tier_c=True,
        proposed_output="Sounds good — updating now.",
        proposed_tool_call={"tool_name": "update_pricing", "arguments": {"customer": "Acme", "new_price": 5000}},
        tool_effective_tier=Tier.C,
        permanent_categories=frozenset({"pricing_or_cost_commitment"}),
        self_reported_scope="finality",
        action_hash="abcdef0123456789" + "0" * 48,
    )
    rendered = chat._render_adjudication(record)

    assert "Sounds good — updating now." in rendered
    assert "Tool: update_pricing" in rendered
    assert "customer: Acme" in rendered
    assert "new_price: 5000" in rendered
    assert "Tool effective tier: tier_c" in rendered
    assert "Categories: ['pricing_or_cost_commitment']" in rendered
    assert "abcdef0123456789" in rendered  # fingerprint prefix


def test_render_adjudication_without_a_tool_call_omits_the_proposed_action_section():
    record = AdjudicationRecord(
        dsd_ref="dsd_1",
        summary="s",
        touches_permanent_tier_c=False,
        proposed_output="Just a recap, nothing more.",
        proposed_tool_call=None,
        tool_effective_tier=None,
        permanent_categories=frozenset(),
        self_reported_scope="exploration",
        action_hash="0" * 64,
    )
    rendered = chat._render_adjudication(record)
    assert "Just a recap, nothing more." in rendered
    assert "Proposed action:" not in rendered
    assert "Tool effective tier" not in rendered


# --- End-to-end: the real chat.main() loop, not just the pure helpers ----

def test_end_to_end_operator_actually_sees_the_proposed_tool_call_before_confirming(monkeypatch, capsys):
    """The concrete scenario the whole fix is for: a governed turn proposes
    a Permanent Tier C tool call. Before this patch, the operator would see
    only `output_text` and 'touches permanent categories: [...]' — never the
    tool name or arguments. Now the printed screen must show both, and the
    operator's two confirmations ('confirmed', 'send it') must appear AFTER
    that screen was printed, not before."""
    db.init_db()
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)

    pricing_result = GovernedTurnResult(
        reasoning="r",
        output_text="Updating the price now.",
        self_reported_scope="finality",
        self_reported_categories=frozenset({"pricing_or_cost_commitment"}),
        scanned_categories=frozenset(),
        proposed_tool_call={"tool_name": "update_pricing", "arguments": {"customer": "Acme", "new_price": 5000}},
        tool_identity_tier=Tier.C,
        tool_argument_categories=frozenset({"pricing_or_cost_commitment"}),
    )

    def _fake_governed_turn(dsd, conversation, user_message, profile_fragment=""):
        return pricing_result

    monkeypatch.setattr(chat, "run_governed_turn", _fake_governed_turn)
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs(
            "confirmed",              # DSD confirmation
            "update the price",       # user turn
            "confirmed",              # content confirmation
            "send it",                # emission authorization
            "exit",
        ),
    )

    chat.main()
    out = capsys.readouterr().out

    assert "Proposed action:" in out
    assert "Tool: update_pricing" in out
    assert "customer: Acme" in out
    assert "new_price: 5000" in out
    assert "Categories: ['pricing_or_cost_commitment']" in out
    assert "Action fingerprint:" in out
    assert "Authorizing emission releases exactly the action shown above" in out

    # The screen showing the tool call must print before ADJUDICATION is
    # resolved into a final "ALDRIC:" line — proven by ordering in the
    # captured output (the prompt text itself isn't in `out` because the
    # scripted fake `input()` swallows its prompt argument rather than
    # printing it, same as every other test in this suite).
    proposed_action_index = out.index("Proposed action:")
    final_line_index = out.index("ALDRIC: Updating the price now.")
    assert proposed_action_index < final_line_index
