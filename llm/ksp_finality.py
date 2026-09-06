"""
KSP Finality — the LLM reasoning calls behind Phases 1, 2, 3, and 5.

Maps to 04_Operator_Kernel_KSP1.md Section 3.1. Every function here makes a
real, separate model call and returns a structured, parsed result — none of
them decide anything governance-critical themselves. `core/ksp_finality.py`
is what turns these results into the CLEARED / CONDITIONAL /
DOWNGRADED_TO_VALIDATION outcome that actually controls whether an artifact
reaches the Adjudication Buffer; this module's only job is to ask the model
an honest, narrow question and parse what comes back.

Cost note: a single Finality-scope turn now makes six of these calls
(Projection, three Validation Threads, Integrity Gate, Compaction) on top of
the primary governed reply and the Sidecar IDS check — eight model calls
total for one Finality turn. Projection, the Validation Threads, and the
Integrity Gate are structural/audit judgments, not the user-facing content,
so they run on SIDECAR_MODEL (cheaper); Compaction produces the actual
artifact text an operator will read, so it runs on DEFAULT_MODEL. This is a
real spend/latency tradeoff, made deliberately and documented here rather
than hidden — same as the Sidecar Auditor promotion.

Section 3.1's own Phase 2 Thread 3 ("EGT Manifold... ρ/κ > 1.42/γ") and
Phase 4 ("Convergence Gate (D_KL)... D_KL(P∥Q) = Σ P(x) log(P(x)/Q(x))") are
the source document's invented vocabulary for describing reasoning posture,
consistent with this project's standing rule (CLAUDE.md Section 5) against
faking rigor. So: Thread 3 here is asked an honest cooperation/fixation
judgment in plain language, never a fake ratio; and there is no
`convergence_gate()` function computing a fake divergence number at all —
core.ksp_finality's Convergence Gate is implemented for real as "did all
three Validation Threads pass," an honest, checkable aggregate of the three
structured booleans this module already produced.
"""
from __future__ import annotations

import json

from llm.client import DEFAULT_MODEL, SIDECAR_MODEL, complete, strip_json_code_fence
from models.schemas import (
    DecisionSurfaceDocument,
    IntegrityGateResult,
    StructuralProjection,
    UnknownAuditFinding,
    ValidationThreadResult,
)


def _dsd_context(dsd: DecisionSurfaceDocument) -> str:
    return (
        f"Decision Locus: {dsd.decision_locus}\n"
        f"Operational Domain: {dsd.operational_domain}\n"
        f"Authority Boundary: {dsd.authority_boundary}\n"
        f"Time Horizon: {dsd.time_horizon}\n"
        f"Constraints & Invariants: {', '.join(dsd.constraints_and_invariants)}\n"
        f"Risk Posture: {dsd.risk_posture}"
    )


_PROJECTION_SYSTEM = """You are performing Phase 1 (Structural Projection) of the KSP Finality
sequence. Reframe the candidate claim below into a constraint surface bound
to the locked Decision Surface: who is affected (actors), what motivates
them (incentives), what must hold true regardless of outcome (invariants),
and what is genuinely NOT yet known or verified (unknowns) that the claim's
force depends on.

Respond with ONLY this JSON:
{"actors": [...], "incentives": [...], "invariants": [...], "unknowns": [...]}
Empty lists are fine and honest where nothing applies — do not invent
entries just to fill a field."""


def project_structure(dsd: DecisionSurfaceDocument, candidate_claim: str) -> StructuralProjection:
    user_message = f"Decision Surface:\n{_dsd_context(dsd)}\n\nCandidate claim:\n{candidate_claim}"
    raw = complete(system=_PROJECTION_SYSTEM, user_message=user_message, model=SIDECAR_MODEL, max_tokens=500)
    parsed = json.loads(strip_json_code_fence(raw))
    return StructuralProjection(
        actors=list(parsed.get("actors", [])),
        incentives=list(parsed.get("incentives", [])),
        invariants=list(parsed.get("invariants", [])),
        unknowns=list(parsed.get("unknowns", [])),
    )


_THREAD_PROMPTS = {
    "coherence": (
        "Thread 1 — Coherence. Test the candidate claim against Mode-7 Invariance: "
        "does it stay internally consistent with the constraint surface below, or does "
        "it contradict an actor, incentive, or invariant already identified?"
    ),
    "security": (
        "Thread 2 — Security. Test the candidate claim against the Security Constraint: "
        "is the cost of this claim being wrong or exploited (to the operator, a client, or "
        "a third party) proportionate to and bounded by the safeguards already in place, "
        "or does it expose more than it protects?"
    ),
    "cooperation": (
        "Thread 3 — Cooperation. In plain language (do not invent a numeric ratio or "
        "formula): does the candidate claim represent a stable, mutually sustainable "
        "position for everyone it commits, or does it look like a one-sided or "
        "unsustainable fixation that would unravel under continued pressure?"
    ),
}


def _thread_system(thread: str) -> str:
    return f"""You are performing Phase 2 of the KSP Finality sequence — one independent
Validation Thread, not a summary of all three. {_THREAD_PROMPTS[thread]}

Respond with ONLY this JSON:
{{"passed": true|false, "rationale": "<one or two sentences, specific to this thread only>"}}"""


def run_validation_thread(
    thread: str, dsd: DecisionSurfaceDocument, projection: StructuralProjection, candidate_claim: str
) -> ValidationThreadResult:
    user_message = (
        f"Decision Surface:\n{_dsd_context(dsd)}\n\n"
        f"Structural Projection — actors: {projection.actors}, incentives: {projection.incentives}, "
        f"invariants: {projection.invariants}, unknowns: {projection.unknowns}\n\n"
        f"Candidate claim:\n{candidate_claim}"
    )
    raw = complete(system=_thread_system(thread), user_message=user_message, model=SIDECAR_MODEL, max_tokens=300)
    parsed = json.loads(strip_json_code_fence(raw))
    return ValidationThreadResult(
        thread=thread,
        passed=bool(parsed.get("passed", False)),
        rationale=parsed.get("rationale", ""),
    )


_INTEGRITY_SYSTEM = """You are performing Phase 3 (Integrity Gate) of the KSP Finality sequence —
a mandatory audit, not a re-statement of the Validation Threads. Given the
Decision Surface, Structural Projection, and the three Validation Thread
results below, audit the candidate claim on exactly three structural
questions:

A. Keystone Identification: are the fundamental assumptions the claim rests
   on (the actors/incentives/invariants from Phase 1) actually stable, or
   does the claim quietly depend on one that could shift?
B. Forward/Inverse Symmetry: if you reversed the claim's logic (assume the
   conclusion, work backwards), does it stay internally consistent, or does
   it only work in one direction?
C. Domain Closure: does the claim stay within the Operational Domain and
   Authority Boundary declared in the Decision Surface, or does it leak into
   territory the Decision Surface doesn't cover?

Respond with ONLY this JSON:
{"keystone_stable": true|false, "forward_inverse_consistent": true|false,
"domain_closure_ok": true|false, "rationale": "<covering all three, briefly>"}"""


def audit_integrity(
    dsd: DecisionSurfaceDocument,
    projection: StructuralProjection,
    threads: list[ValidationThreadResult],
    candidate_claim: str,
) -> IntegrityGateResult:
    thread_summary = "; ".join(f"{t.thread}: {'pass' if t.passed else 'fail'} ({t.rationale})" for t in threads)
    user_message = (
        f"Decision Surface:\n{_dsd_context(dsd)}\n\n"
        f"Structural Projection — actors: {projection.actors}, incentives: {projection.incentives}, "
        f"invariants: {projection.invariants}, unknowns: {projection.unknowns}\n\n"
        f"Validation Threads: {thread_summary}\n\n"
        f"Candidate claim:\n{candidate_claim}"
    )
    raw = complete(system=_INTEGRITY_SYSTEM, user_message=user_message, model=SIDECAR_MODEL, max_tokens=400)
    parsed = json.loads(strip_json_code_fence(raw))
    return IntegrityGateResult(
        keystone_stable=bool(parsed.get("keystone_stable", False)),
        forward_inverse_consistent=bool(parsed.get("forward_inverse_consistent", False)),
        domain_closure_ok=bool(parsed.get("domain_closure_ok", False)),
        rationale=parsed.get("rationale", ""),
    )


_COMPACTION_SYSTEM = """You are performing Phase 5 (Compaction) of the KSP Finality sequence: emit
the final artifact — pure prose, zero fluff — informed by everything below,
and separately audit each declared unknown from Phase 1 against that
artifact.

Unknown Variable Audit rule: for each unknown, decide honestly whether the
final artifact's claim still depends on it being unresolved. If the
artifact's core recommendation depends on ANY unresolved unknown, the
artifact you emit must lead with what would need to be verified first (a
data audit requirement), not with an unconditional recommendation — a
recommendation built on an unresolved unknown is conditional analysis, not
Finality, and must read that way.

Respond with ONLY this JSON:
{"unknown_audit": [{"unknown": "<exact text from Phase 1>", "resolved": true|false}, ...],
"compaction_text": "<the final artifact prose>"}"""


def compact(
    dsd: DecisionSurfaceDocument,
    projection: StructuralProjection,
    threads: list[ValidationThreadResult],
    integrity: IntegrityGateResult,
    candidate_claim: str,
) -> tuple[list[UnknownAuditFinding], str]:
    thread_summary = "; ".join(f"{t.thread}: {'pass' if t.passed else 'fail'} ({t.rationale})" for t in threads)
    user_message = (
        f"Decision Surface:\n{_dsd_context(dsd)}\n\n"
        f"Structural Projection — actors: {projection.actors}, incentives: {projection.incentives}, "
        f"invariants: {projection.invariants}, unknowns: {projection.unknowns}\n\n"
        f"Validation Threads: {thread_summary}\n\n"
        f"Integrity Gate: keystone_stable={integrity.keystone_stable}, "
        f"forward_inverse_consistent={integrity.forward_inverse_consistent}, "
        f"domain_closure_ok={integrity.domain_closure_ok} ({integrity.rationale})\n\n"
        f"Candidate claim:\n{candidate_claim}"
    )
    raw = complete(system=_COMPACTION_SYSTEM, user_message=user_message, model=DEFAULT_MODEL, max_tokens=1200)
    parsed = json.loads(strip_json_code_fence(raw))
    audit = [
        UnknownAuditFinding(unknown=f.get("unknown", ""), resolved=bool(f.get("resolved", False)))
        for f in parsed.get("unknown_audit", [])
    ]
    return audit, parsed.get("compaction_text", candidate_claim)
