"""
KSP — Finality & Integrity Engine (04_Operator_Kernel_KSP1.md Section 3).

Section 3.1 in full: "No artifact reaches the Adjudication Buffer without
clearing the DSD Fuse and every subsequent phase." Before this module
existed, chat.py sent a Finality-scope or permanent-category artifact
straight from `run_governed_turn()` to the Adjudication Buffer — one model
call, self-classified, with no independent structural check in between.
That is precisely the "trust the model's own output for a governance-
critical fact" pattern this whole rebuild exists to close. This module is
what actually clears (or refuses to clear) an artifact through the phases
in between.

Honesty note, consistent with CLAUDE.md Section 5 and this codebase's other
modules: Phase 2 Thread 3 ("EGT Manifold... ρ/κ > 1.42/γ") and Phase 4
("Convergence Gate (D_KL)...") are the source document's own invented
vocabulary for describing reasoning posture, not literal computable
mathematics. Nothing here computes a fake divergence number or manifold
ratio. What IS implemented for real:

  * Phase 1 (Structural Projection) — gated by a real, deterministic
    Keystone check first: an unlocked/unconfirmed DSD is a Keystone Failure
    per Section 3.1's own "DSD Rule" and forces an immediate downgrade to
    Validation scope — no LLM call made at all in that case.
  * Phase 2 (Validation Threads) — three genuinely separate model calls
    (llm/ksp_finality.py), each a plain pass/fail judgment with rationale.
  * The Convergence Gate — implemented honestly as "did all three
    Validation Threads pass," an aggregate of real structured booleans, not
    a synthetic D_KL epsilon comparison. Any thread failing forces the same
    downgrade path as a Keystone Failure (Section 3.1: "Treat an undefined
    or partial Decision Surface as a Keystone Failure" — extended here, by
    the same logic, to any Phase-2 thread failure, since neither case
    leaves a stable enough basis for a Finality artifact).
  * Phase 3 (Integrity Gate) — deterministically ANDs the three structural
    booleans an independent model call produced; this module, not the LLM's
    own summary, decides whether the gate as a whole passed.
  * The Unknown Variable Audit — deterministic: ANY unresolved declared
    unknown forces the CONDITIONAL outcome and a deterministic banner is
    prepended to the artifact text, regardless of whether the Compaction
    call's own prose already framed itself that way. The LLM is not trusted
    alone for a structural labelling requirement.
  * Phase 5 (Compaction) — the LLM call producing the final artifact text;
    still only ever a candidate. Phase 6 (Adjudication Buffer) is entirely
    unchanged — this module hands its result to the existing, already-real
    core.ksp1_operator_kernel.AdjudicationBuffer, never bypasses it.

Unconditional gate, separate from all of the above: a Permanent Tier C
category is never excused from the Adjudication Buffer's two-stage
confirmation by anything this module decides (PA Action Kernel Section 3.3
/ CLAUDE.md Section 2). Callers must check `touches_permanent_tier_c`
independently — KSP downgrading a Finality *claim* to Validation is a
statement about the claim's reasoning integrity, not a statement that a
pricing/legal/contractual commitment stopped requiring adjudication.
"""
from __future__ import annotations

from llm.ksp_finality import audit_integrity, compact, project_structure, run_validation_thread
from models.schemas import (
    DecisionSurfaceDocument,
    KSPFinalityResult,
    KSPOutcome,
    ValidationThreadResult,
)

_THREADS = ("coherence", "security", "cooperation")


def run_ksp_finality(dsd: DecisionSurfaceDocument, candidate_claim: str) -> KSPFinalityResult:
    """The full Phase 1 -> Phase 5 sequence. Returns a KSPFinalityResult
    whose `outcome` tells the caller (chat.py) what to do next:

      * CLEARED / CONDITIONAL — proceed to the Adjudication Buffer using
        `compaction_text` as the artifact text (already carries the
        deterministic conditional banner if CONDITIONAL).
      * DOWNGRADED_TO_VALIDATION — this specific Finality claim did not
        survive Phase 1 or Phase 2; `compaction_text` is just the original
        candidate_claim unchanged. The caller decides separately whether a
        Permanent Tier C category still requires adjudication regardless.
    """
    if not (dsd.locked and dsd.confirmed):
        return KSPFinalityResult(
            dsd_ref=dsd.dsd_id,
            outcome=KSPOutcome.DOWNGRADED_TO_VALIDATION,
            downgrade_reason=(
                "Keystone Failure: the Decision Surface is not locked/confirmed "
                "(Section 3.1 DSD Rule — an undefined or partial Decision Surface "
                "is treated as a Keystone Failure)."
            ),
            compaction_text=candidate_claim,
        )

    projection = project_structure(dsd, candidate_claim)

    threads: list[ValidationThreadResult] = [
        run_validation_thread(name, dsd, projection, candidate_claim) for name in _THREADS
    ]
    failed_threads = [t.thread for t in threads if not t.passed]
    if failed_threads:
        return KSPFinalityResult(
            dsd_ref=dsd.dsd_id,
            outcome=KSPOutcome.DOWNGRADED_TO_VALIDATION,
            projection=projection,
            validation_threads=threads,
            downgrade_reason=(
                f"Convergence Gate failed: Validation Thread(s) {failed_threads} did not pass "
                "(honest pass/fail aggregate — see llm/ksp_finality.py for why this is not a "
                "synthetic D_KL number)."
            ),
            compaction_text=candidate_claim,
        )

    integrity = audit_integrity(dsd, projection, threads, candidate_claim)
    if not _integrity_passed(integrity):
        return KSPFinalityResult(
            dsd_ref=dsd.dsd_id,
            outcome=KSPOutcome.DOWNGRADED_TO_VALIDATION,
            projection=projection,
            validation_threads=threads,
            integrity_gate=integrity,
            downgrade_reason=f"Integrity Gate failed: {integrity.rationale}",
            compaction_text=candidate_claim,
        )

    unknown_audit, compaction_text = compact(dsd, projection, threads, integrity, candidate_claim)
    unresolved = [f.unknown for f in unknown_audit if not f.resolved]

    if unresolved:
        compaction_text = (
            f"[Conditional — depends on unresolved unknown(s): {', '.join(unresolved)}] {compaction_text}"
        )
        outcome = KSPOutcome.CONDITIONAL
    else:
        outcome = KSPOutcome.CLEARED

    return KSPFinalityResult(
        dsd_ref=dsd.dsd_id,
        outcome=outcome,
        projection=projection,
        validation_threads=threads,
        integrity_gate=integrity,
        unknown_audit=unknown_audit,
        compaction_text=compaction_text,
    )


def _integrity_passed(integrity) -> bool:
    """Deterministic AND of the three Phase 3 structural booleans — the
    gate's own pass/fail is decided here, never taken on the LLM's
    `rationale` prose alone."""
    return integrity.keystone_stable and integrity.forward_inverse_consistent and integrity.domain_closure_ok
