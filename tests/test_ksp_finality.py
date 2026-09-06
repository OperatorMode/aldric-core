"""
Proves core.ksp_finality.run_ksp_finality actually implements
04_Operator_Kernel_KSP1.md Section 3.1's claim that "no artifact reaches
the Adjudication Buffer without clearing the DSD Fuse and every subsequent
phase" — not just that the module exists and is importable.

Two groups, deliberately kept separate:

  1. Unit tests against core.ksp_finality directly, with every LLM call
     (project_structure, run_validation_thread, audit_integrity, compact)
     mocked at the point of use — proving each gate's own logic: the
     Keystone check short-circuits before any LLM call, a failing
     Validation Thread or Integrity Gate result forces
     DOWNGRADED_TO_VALIDATION and skips every later phase, and an
     unresolved declared unknown forces CONDITIONAL with a deterministic
     banner regardless of what the compaction call's own prose said.

  2. End-to-end tests through chat.main() proving the wiring: a Finality-
     scope reply that clears KSP proceeds to adjudication with the
     KSP-produced text; one that gets downgraded skips adjudication
     entirely UNLESS it also touches a Permanent Tier C category, in which
     case KSP's downgrade is logged but adjudication still happens
     (CLAUDE.md Section 2 / PA Action Kernel Section 3.3: that gate is
     unconditional and is never excused by KSP's own outcome).
"""
import core.ksp_finality as ksp_finality_module
import chat
from core.ksp_finality import run_ksp_finality
from llm.governed_reply import GovernedTurnResult
from models.schemas import (
    DecisionSurfaceDocument,
    IntegrityGateResult,
    KSPFinalityResult,
    KSPOutcome,
    StructuralProjection,
    UnknownAuditFinding,
    ValidationThreadResult,
)
from storage import db


def _locked_dsd() -> DecisionSurfaceDocument:
    dsd = DecisionSurfaceDocument(
        decision_locus="Whether to take an action on the operator's behalf",
        operational_domain="Client account management",
        authority_boundary="Operator decides",
        time_horizon="Today",
        constraints_and_invariants=["No unilateral commitments"],
        risk_posture="Low tolerance for unauthorized commitments",
    )
    dsd.confirmed = True
    dsd.locked = True
    return dsd


def _all_pass_threads():
    return [
        ValidationThreadResult(thread="coherence", passed=True, rationale="consistent"),
        ValidationThreadResult(thread="security", passed=True, rationale="bounded"),
        ValidationThreadResult(thread="cooperation", passed=True, rationale="stable"),
    ]


def _passing_integrity():
    return IntegrityGateResult(
        keystone_stable=True, forward_inverse_consistent=True, domain_closure_ok=True, rationale="solid",
    )


# --- Group 1: core.ksp_finality gate logic --------------------------------

def test_unlocked_dsd_is_a_keystone_failure_and_makes_no_llm_call(monkeypatch):
    def _explode(*a, **kw):
        raise AssertionError("no LLM call should be made for an unlocked/unconfirmed DSD")

    monkeypatch.setattr(ksp_finality_module, "project_structure", _explode)
    monkeypatch.setattr(ksp_finality_module, "run_validation_thread", _explode)
    monkeypatch.setattr(ksp_finality_module, "audit_integrity", _explode)
    monkeypatch.setattr(ksp_finality_module, "compact", _explode)

    dsd = DecisionSurfaceDocument(
        decision_locus="x", operational_domain="x", authority_boundary="x",
        time_horizon="x", constraints_and_invariants=["x"], risk_posture="x",
    )  # confirmed=False, locked=False by default
    result = run_ksp_finality(dsd, "Some candidate claim.")
    assert result.outcome == KSPOutcome.DOWNGRADED_TO_VALIDATION
    assert "Keystone Failure" in result.downgrade_reason
    assert result.compaction_text == "Some candidate claim."


def test_a_failing_validation_thread_downgrades_and_skips_integrity_and_compaction(monkeypatch):
    dsd = _locked_dsd()
    monkeypatch.setattr(ksp_finality_module, "project_structure", lambda d, c: StructuralProjection())

    threads = [
        ValidationThreadResult(thread="coherence", passed=True, rationale="ok"),
        ValidationThreadResult(thread="security", passed=False, rationale="exposes too much"),
        ValidationThreadResult(thread="cooperation", passed=True, rationale="ok"),
    ]
    monkeypatch.setattr(
        ksp_finality_module, "run_validation_thread",
        lambda name, d, p, c: next(t for t in threads if t.thread == name),
    )

    def _explode(*a, **kw):
        raise AssertionError("Integrity Gate / Compaction must not run after a thread fails")

    monkeypatch.setattr(ksp_finality_module, "audit_integrity", _explode)
    monkeypatch.setattr(ksp_finality_module, "compact", _explode)

    result = run_ksp_finality(dsd, "We guarantee delivery Friday no matter what.")
    assert result.outcome == KSPOutcome.DOWNGRADED_TO_VALIDATION
    assert "security" in result.downgrade_reason
    assert result.validation_threads == threads
    assert result.compaction_text == "We guarantee delivery Friday no matter what."


def test_integrity_gate_failure_downgrades_and_skips_compaction(monkeypatch):
    dsd = _locked_dsd()
    monkeypatch.setattr(ksp_finality_module, "project_structure", lambda d, c: StructuralProjection())
    monkeypatch.setattr(
        ksp_finality_module, "run_validation_thread",
        lambda name, d, p, c: ValidationThreadResult(thread=name, passed=True, rationale="ok"),
    )
    failing_integrity = IntegrityGateResult(
        keystone_stable=True, forward_inverse_consistent=False, domain_closure_ok=True,
        rationale="the claim only holds in one direction",
    )
    monkeypatch.setattr(ksp_finality_module, "audit_integrity", lambda *a, **kw: failing_integrity)

    def _explode(*a, **kw):
        raise AssertionError("Compaction must not run after the Integrity Gate fails")

    monkeypatch.setattr(ksp_finality_module, "compact", _explode)

    result = run_ksp_finality(dsd, "A claim that only works one way.")
    assert result.outcome == KSPOutcome.DOWNGRADED_TO_VALIDATION
    assert "Integrity Gate failed" in result.downgrade_reason
    assert result.integrity_gate == failing_integrity


def test_unresolved_unknown_forces_conditional_with_a_deterministic_banner(monkeypatch):
    """The compaction call's own prose does NOT say this is conditional —
    proving the banner is prepended by code, not trusted from the LLM."""
    dsd = _locked_dsd()
    monkeypatch.setattr(ksp_finality_module, "project_structure", lambda d, c: StructuralProjection(unknowns=["client budget ceiling"]))
    monkeypatch.setattr(
        ksp_finality_module, "run_validation_thread",
        lambda name, d, p, c: ValidationThreadResult(thread=name, passed=True, rationale="ok"),
    )
    monkeypatch.setattr(ksp_finality_module, "audit_integrity", lambda *a, **kw: _passing_integrity())
    monkeypatch.setattr(
        ksp_finality_module, "compact",
        lambda *a, **kw: (
            [UnknownAuditFinding(unknown="client budget ceiling", resolved=False)],
            "We recommend proceeding at the proposed price.",  # no conditional framing of its own
        ),
    )

    result = run_ksp_finality(dsd, "We recommend proceeding at the proposed price.")
    assert result.outcome == KSPOutcome.CONDITIONAL
    assert result.compaction_text.startswith("[Conditional — depends on unresolved unknown(s): client budget ceiling]")


def test_all_unknowns_resolved_clears_without_a_banner(monkeypatch):
    dsd = _locked_dsd()
    monkeypatch.setattr(ksp_finality_module, "project_structure", lambda d, c: StructuralProjection(unknowns=["x"]))
    monkeypatch.setattr(
        ksp_finality_module, "run_validation_thread",
        lambda name, d, p, c: ValidationThreadResult(thread=name, passed=True, rationale="ok"),
    )
    monkeypatch.setattr(ksp_finality_module, "audit_integrity", lambda *a, **kw: _passing_integrity())
    monkeypatch.setattr(
        ksp_finality_module, "compact",
        lambda *a, **kw: ([UnknownAuditFinding(unknown="x", resolved=True)], "Final artifact text."),
    )

    result = run_ksp_finality(dsd, "candidate")
    assert result.outcome == KSPOutcome.CLEARED
    assert result.compaction_text == "Final artifact text."


# --- Group 2: end-to-end wiring through chat.main() -----------------------

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


def _no_op_ids_detector():
    from core.apex_supervisor import HeuristicIDSDetector
    return HeuristicIDSDetector()


def test_finality_scope_reply_that_clears_ksp_uses_the_compacted_text(monkeypatch, capsys):
    db.init_db()
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", _no_op_ids_detector)

    finality_result = GovernedTurnResult(
        reasoning="r", output_text="Here is the final recommendation.",
        self_reported_scope="finality", self_reported_categories=frozenset(),
        scanned_categories=frozenset(),
    )
    monkeypatch.setattr(chat, "run_governed_turn", lambda dsd, conv, msg, profile_fragment="": finality_result)
    monkeypatch.setattr(
        chat, "run_ksp_finality",
        lambda dsd, candidate: KSPFinalityResult(
            dsd_ref=dsd.dsd_id, outcome=KSPOutcome.CLEARED,
            compaction_text="Compacted: here is the final recommendation.",
        ),
    )
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs("confirmed", "give me the final answer", "confirmed", "exit"),
    )

    chat.main()
    out = capsys.readouterr().out
    assert "ADJUDICATION REQUIRED" in out
    assert "Compacted: here is the final recommendation." in out


def test_downgraded_finality_without_permanent_category_skips_adjudication(monkeypatch, capsys):
    db.init_db()
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", _no_op_ids_detector)

    finality_result = GovernedTurnResult(
        reasoning="r", output_text="This is a sweeping global claim.",
        self_reported_scope="finality", self_reported_categories=frozenset(),
        scanned_categories=frozenset(),
    )
    monkeypatch.setattr(chat, "run_governed_turn", lambda dsd, conv, msg, profile_fragment="": finality_result)
    monkeypatch.setattr(
        chat, "run_ksp_finality",
        lambda dsd, candidate: KSPFinalityResult(
            dsd_ref=dsd.dsd_id, outcome=KSPOutcome.DOWNGRADED_TO_VALIDATION,
            downgrade_reason="Convergence Gate failed: Validation Thread(s) ['security'] did not pass.",
            compaction_text=candidate,
        ),
    )
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs("confirmed", "make a sweeping claim", "exit"),
    )

    chat.main()
    out = capsys.readouterr().out
    assert "[KSP] Convergence Gate failed" in out
    assert "ADJUDICATION REQUIRED" not in out
    assert "ALDRIC: This is a sweeping global claim." in out


def test_downgraded_finality_that_also_touches_permanent_category_still_adjudicates(monkeypatch, capsys):
    """The unconditional gate (CLAUDE.md Section 2): KSP downgrading the
    Finality claim must never excuse the separate Permanent Tier C
    double-confirmation requirement."""
    db.init_db()
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", _no_op_ids_detector)

    pricing_result = GovernedTurnResult(
        reasoning="r", output_text="Locking in $10,000 regardless.",
        self_reported_scope="finality", self_reported_categories=frozenset(),
        scanned_categories=frozenset({"pricing_or_cost_commitment"}),
    )
    monkeypatch.setattr(chat, "run_governed_turn", lambda dsd, conv, msg, profile_fragment="": pricing_result)
    monkeypatch.setattr(
        chat, "run_ksp_finality",
        lambda dsd, candidate: KSPFinalityResult(
            dsd_ref=dsd.dsd_id, outcome=KSPOutcome.DOWNGRADED_TO_VALIDATION,
            downgrade_reason="Integrity Gate failed: leaks outside the Operational Domain.",
            compaction_text=candidate,
        ),
    )
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs("confirmed", "lock in the price", "confirmed", "send it", "exit"),
    )

    chat.main()
    out = capsys.readouterr().out
    assert "[KSP] Integrity Gate failed" in out
    assert "Still held for adjudication" in out
    assert "ADJUDICATION REQUIRED" in out
    assert "Locking in $10,000 regardless." in out


def test_conditional_outcome_shows_the_conditional_note_and_still_adjudicates(monkeypatch, capsys):
    db.init_db()
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", _no_op_ids_detector)

    finality_result = GovernedTurnResult(
        reasoning="r", output_text="We recommend proceeding.",
        self_reported_scope="finality", self_reported_categories=frozenset(),
        scanned_categories=frozenset(),
    )
    monkeypatch.setattr(chat, "run_governed_turn", lambda dsd, conv, msg, profile_fragment="": finality_result)
    monkeypatch.setattr(
        chat, "run_ksp_finality",
        lambda dsd, candidate: KSPFinalityResult(
            dsd_ref=dsd.dsd_id, outcome=KSPOutcome.CONDITIONAL,
            unknown_audit=[UnknownAuditFinding(unknown="client budget ceiling", resolved=False)],
            compaction_text="[Conditional — depends on unresolved unknown(s): client budget ceiling] "
                             "We recommend proceeding.",
        ),
    )
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs("confirmed", "should we proceed", "confirmed", "exit"),
    )

    chat.main()
    out = capsys.readouterr().out
    assert "[KSP] Conditional" in out
    assert "client budget ceiling" in out
    assert "ADJUDICATION REQUIRED" in out
    assert "[Conditional — depends on unresolved unknown(s): client budget ceiling]" in out
