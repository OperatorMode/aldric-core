"""
Shared data models for the ALDRIC deterministic governance stack.

Every model here corresponds directly to a concept defined in the eight
governance documents (01 KSP-0 through 08 Governance Chain). Where a document
says a rule "cannot be overridden at runtime", the corresponding model or
constant here is implemented so that there is no code path that mutates it
through the API. Governance-critical fields are enforced in code, not
requested of the LLM as an instruction to follow.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# KSP-0 / DSD  (01_KSP-0_DSD.md)
# ---------------------------------------------------------------------------

class DSDField(str, Enum):
    DECISION_LOCUS = "decision_locus"
    OPERATIONAL_DOMAIN = "operational_domain"
    AUTHORITY_BOUNDARY = "authority_boundary"
    TIME_HORIZON = "time_horizon"
    CONSTRAINTS_AND_INVARIANTS = "constraints_and_invariants"
    RISK_POSTURE = "risk_posture"


# KSP-0 Section 10 / KSP-1 Section 4 — Confirmation Vocabulary.
# Canonical, case-insensitive. Any operator utterance not on this list
# (after normalization) is treated as ambiguous and re-prompted; it is never
# inferred to be an affirmative from context, tone, or momentum.
CONFIRMATION_VOCABULARY = frozenset({"confirmed", "correct", "yes", "proceed", "locked"})

# KSP-1 Section 3.2 — Emission Authorization (Tier C only).
# Deliberately disjoint from CONFIRMATION_VOCABULARY. Matching is exact
# (post-normalisation), not "contains", so a phrase like "confirmed - transmit"
# does not match: it is content ratification language with a word borrowed
# from emission vocabulary, not a canonical emission authorization.
EMISSION_VOCABULARY = frozenset({"send it", "transmit now", "dispatch this", "authorize emission"})


def normalize_utterance(text: str) -> str:
    return " ".join(text.strip().lower().split())


class ConfirmationResult(str, Enum):
    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"


def classify_confirmation(text: str) -> ConfirmationResult:
    """KSP-0 Section 10 / KSP-1 Section 4.

    'Ambiguous responses trigger a single neutral clarification request.
    Silence and non-response do not constitute confirmation. Protocol
    language cannot substitute for operator confirmation under any framing.'
    """
    normalized = normalize_utterance(text)
    if normalized in CONFIRMATION_VOCABULARY:
        return ConfirmationResult.CONFIRMED
    return ConfirmationResult.AMBIGUOUS


def classify_emission_authorization(text: str) -> bool:
    """KSP-1 Section 3.2 — strict, exact match only. See module docstring."""
    return normalize_utterance(text) in EMISSION_VOCABULARY


class DecisionSurfaceDocument(BaseModel):
    """The six mandatory DSD fields (KSP-0 Section 2).

    All six fields must be specific, non-empty, and non-contradictory
    (Section 8.1, the DSD Completeness Test) before this object may be
    locked. Once locked (KSP Phase 1 begins), it is immutable — Section 8.3.
    """

    dsd_id: str = Field(default_factory=lambda: _new_id("dsd"))
    decision_locus: str = Field(..., min_length=1)
    operational_domain: str = Field(..., min_length=1)
    authority_boundary: str = Field(..., min_length=1)
    time_horizon: str = Field(..., min_length=1)
    constraints_and_invariants: list[str] = Field(..., min_length=1)
    risk_posture: str = Field(..., min_length=1)

    confirmed: bool = False
    confirmed_at: Optional[str] = None
    locked: bool = False
    created_at: str = Field(default_factory=_now)

    @field_validator(
        "decision_locus", "operational_domain", "authority_boundary",
        "time_horizon", "risk_posture",
    )
    @classmethod
    def _non_empty_non_placeholder(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("DSD field cannot be empty (Section 8.1 Completeness Test)")
        return v.strip()

    def completeness_test(self) -> tuple[bool, list[str]]:
        """Section 8.1. Returns (passes, list_of_failing_field_names)."""
        failures = []
        for field_name in DSDField:
            value = getattr(self, field_name.value)
            if isinstance(value, list):
                if not value or any(not v.strip() for v in value):
                    failures.append(field_name.value)
            elif not value or not value.strip():
                failures.append(field_name.value)
        return (len(failures) == 0, failures)


# ---------------------------------------------------------------------------
# PA Action Kernel  (05_PA_Action_Kernel.md)
# ---------------------------------------------------------------------------

class ConfidenceState(str, Enum):
    """Section 1.2 + Section 8.2 (Surface Lifecycle superset)."""
    OBSERVING = "observing"
    DEVELOPING = "developing"
    EXECUTABLE = "executable"
    DEGRADING = "degrading"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class Tier(str, Enum):
    A = "tier_a"  # Silent execution
    B = "tier_b"  # Execute then notify
    C = "tier_c"  # Hold for confirmation


# PA Action Kernel Section 3.3 / Learning Governance Section 5.2.
# Identical lists in both source documents. Hardcoded as a Python constant,
# not a database row or config value — there is deliberately no API endpoint
# that can add, remove, or edit this set. This is what "cannot be overridden
# at runtime" means in code: the set is not reachable from any mutation path.
PERMANENT_TIER_C_CATEGORIES = frozenset({
    "pricing_or_cost_commitment",
    "scope_commitment_or_change",
    "deadline_commitment",
    "contractual_terms_or_obligation",
    "legal_matter",
    "binding_obligation",
})


class DriftLevel(str, Enum):
    MINOR = "minor"
    MODERATE = "moderate"
    MATERIAL = "material"


class ActionRequest(BaseModel):
    """A candidate action ALDRIC (or the Governance Stack) wants to take.

    `permanent_categories` is the tool/action registry's static declaration
    of which Permanent Tier C categories this action touches (see
    core/pa_action_kernel.py TOOL_REGISTRY). It is never taken from the
    LLM's own claim about the action — the LLM's `claimed_tier` field below
    is recorded for audit purposes only and is never trusted for the actual
    gating decision.
    """

    action_id: str = Field(default_factory=lambda: _new_id("act"))
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    surface_id: Optional[str] = None
    claimed_tier: Optional[Tier] = None  # what the LLM/prompt asserts — audit only
    permanent_categories: frozenset[str] = frozenset()
    external_facing: bool = False
    reversible: bool = True
    created_at: str = Field(default_factory=_now)


class Surface(BaseModel):
    """PA Action Kernel Component 1/8. ALDRIC's accumulated model of a class
    of situation. Not a task type — a contextual model built from
    observation."""

    surface_id: str = Field(default_factory=lambda: _new_id("surf"))
    description: str
    state: ConfidenceState = ConfidenceState.OBSERVING
    context_snapshot: dict = Field(default_factory=dict)
    correction_count: int = 0
    confirmation_count: int = 0
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    mirror_drift_flagged: bool = False


class ConflictRecordEntry(BaseModel):
    """Learning Governance Section 3.3. A transparency record, not a
    weighting mechanism — corrections are never reversed by accumulating
    conflict-record entries."""

    model_config = {"protected_namespaces": ()}

    entry_id: str = Field(default_factory=lambda: _new_id("conflict"))
    surface_id: str
    model_held_before: str
    operator_instruction: str
    domain: str
    created_at: str = Field(default_factory=_now)


class DigestEntry(BaseModel):
    """One line item in the non-suppressible Daily Digest (PA Action Kernel
    Component 7). The digest generator (core/pa_action_kernel.py) queries
    ALL entries since the last digest with no filtering step — there is no
    "shape toward approval" code path."""

    entry_id: str = Field(default_factory=lambda: _new_id("digest"))
    category: str  # executed_action | held_thread | drift_flag | confidence_change |
                    # mirror_drift_flag | correction_observation | pattern_observation |
                    # new_surface_candidate
    payload: dict
    created_at: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Long-term memory — standing preferences and facts (not a source document
# section on its own; this is the operator's own extension, built from what
# an earlier prompt-based ALDRIC was observed doing in real use — see
# core/long_term_memory.py's module docstring for the full reasoning and the
# distinction from Learning Governance's per-Surface confidence, which is a
# separate, still-unbuilt third memory category blocked on a real surface
# matcher).
# ---------------------------------------------------------------------------

class StandingPreference(BaseModel):
    """A standing rule about how to behave that should just be looked up and
    applied, not re-decided or asked about each time — e.g. "clients get a
    formal tone, the team gets a casual one." One active preference per
    `scope`: setting a new preference for a scope replaces the old one
    outright (see core.long_term_memory.set_preference), matching this
    project's existing Correction Absolute principle (CLAUDE.md Section 7)
    — the latest thing the operator said applies immediately and completely,
    not gradually or resistibly."""

    preference_id: str = Field(default_factory=lambda: _new_id("pref"))
    scope: str  # a free-form tag the operator/model chooses, e.g. "client",
                # "team", "boss", or something more specific like "client:acme"
    content: str
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class MemoryFact(BaseModel):
    """A durable fact or piece of context worth remembering across sessions
    that isn't itself a standing behavioral rule — e.g. "invoice numbers
    start with INV-". Append-only, like ConflictRecordEntry/DigestEntry
    above: a fact is never silently overwritten, only superseded by a newer
    one with the same scope (both remain visible in the record)."""

    fact_id: str = Field(default_factory=lambda: _new_id("fact"))
    scope: str
    content: str
    source: str  # where this came from: "casual_turn", "operator_clarification", "dsd_ref:<id>", etc.
    created_at: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# KSP-1 Operator Kernel  (04_Operator_Kernel_KSP1.md)
# ---------------------------------------------------------------------------

class LoopState(str, Enum):
    ACTIVE = "active"
    PARKED = "parked"
    CLOSED = "closed"
    SUSPENDED = "suspended"


class Scope(str, Enum):
    EXPLORATION = "exploration"
    VALIDATION = "validation"
    FINALITY = "finality"


class AdjudicationStage(str, Enum):
    """The two-stage Tier C gate (KSP-1 Section 3.2, PA Action Kernel 3.4,
    KSP-0 Section 9.5). Content ratification and emission authorization are
    distinct operator actions; neither can be inferred from or satisfied by
    the other."""

    PENDING_CONTENT_CONFIRMATION = "pending_content_confirmation"
    CONTENT_CONFIRMED = "content_confirmed"          # non-binding until emission cleared, if Tier C
    PENDING_EMISSION_AUTHORIZATION = "pending_emission_authorization"
    EMITTED = "emitted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


def compute_action_hash(
    dsd_ref: str,
    proposed_output: str,
    proposed_tool_call: Optional[dict],
    permanent_categories: frozenset[str],
    tool_effective_tier: Optional["Tier"],
) -> str:
    """A canonical fingerprint of the exact action an AdjudicationRecord
    adjudicates: the DSD it cites, the exact response text, the exact
    proposed tool call (if any), which permanent categories were found, and
    the tool's effective tier. Two calls building the identical tuple always
    produce the same hash (json.dumps with sort_keys=True makes key order in
    nested dicts irrelevant).

    This exists for two reasons. First, `AdjudicationBuffer` recomputes it on
    every `confirm_content()` / `authorize_emission()` call and compares
    against the hash stored at `open()` time — proving, rather than just
    assuming, that nothing altered the action between the two authorization
    stages. Second, per the project's own engineering constraints: once a
    real capability-execution layer exists, it should be told 'execute the
    action with this exact hash', not 'the model said something that looks
    like the authorized action, so figure out what it meant' — binding
    execution to this fingerprint is what makes that possible later, even
    though nothing in this codebase executes anything yet."""
    canonical = json.dumps(
        {
            "dsd_ref": dsd_ref,
            "proposed_output": proposed_output,
            "proposed_tool_call": proposed_tool_call,
            "permanent_categories": sorted(permanent_categories),
            "tool_effective_tier": tool_effective_tier.value if tool_effective_tier else None,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AdjudicationRecord(BaseModel):
    """KSP-1 Section 3.2 — the Adjudication Buffer. No artifact is binding
    or integrated into the session record until explicitly confirmed by the
    operator. If it touches a Permanent Tier C exception, content
    confirmation alone does not authorize emission (KSP-0 Section 9.5).

    Beyond the confirmation state machine, this record carries the complete
    proposed action itself — response text, any proposed tool call, and its
    risk classification — not merely a truncated `summary` string. That is
    what lets the operator-facing confirmation screen show the operator the
    exact thing their 'confirmed' / 'send it' applies to, instead of prose
    alone while a structured tool call rides along unseen. `action_hash` is
    the fingerprint of that exact tuple (see `compute_action_hash`), checked
    by `AdjudicationBuffer` at both confirmation stages so the action cannot
    silently change between them."""

    adjudication_id: str = Field(default_factory=lambda: _new_id("adj"))
    dsd_ref: str  # the DSD this artifact was machined against — mandatory citation
    summary: str  # one-line plain-language summary for the "!" indicator
    touches_permanent_tier_c: bool
    stage: AdjudicationStage = AdjudicationStage.PENDING_CONTENT_CONFIRMATION
    content_confirmed_at: Optional[str] = None
    emission_authorized_at: Optional[str] = None
    created_at: str = Field(default_factory=_now)

    # The complete proposed action this record adjudicates. Set once at
    # AdjudicationBuffer.open() and never touched again by confirm_content()
    # or authorize_emission(), which only ever set stage/*_at fields above —
    # action_hash lets that invariant be checked, not just assumed.
    proposed_output: str = ""
    proposed_tool_call: Optional[dict] = None
    self_reported_scope: str = ""
    tool_effective_tier: Optional[Tier] = None
    permanent_categories: frozenset[str] = frozenset()
    action_hash: str = ""


# ---------------------------------------------------------------------------
# KSP — Finality & Integrity Engine  (04_Operator_Kernel_KSP1.md Section 3)
# ---------------------------------------------------------------------------
#
# Section 3.1: "No artifact reaches the Adjudication Buffer without clearing
# the DSD Fuse and every subsequent phase." These models carry the real
# output of Phases 1-3 and 5 (Structural Projection, Validation Threads,
# Integrity Gate, Compaction) — see core/ksp_finality.py and
# llm/ksp_finality.py for what's genuinely computed vs. genuinely an LLM
# reasoning step, and why Phase 2 Thread 3 (EGT Manifold) and Phase 4
# (D_KL Convergence Gate) are deliberately NOT implemented as literal math.

class KSPOutcome(str, Enum):
    CLEARED = "cleared"                             # a real Finality artifact
    CONDITIONAL = "conditional"                      # cleared, but depends on an unresolved unknown
    DOWNGRADED_TO_VALIDATION = "downgraded_to_validation"  # Keystone Failure or Convergence Gate failure


class StructuralProjection(BaseModel):
    """Phase 1. Reframes the candidate claim into a constraint surface bound
    to the locked Decision Surface — actors, incentives, invariants, and
    unknowns. A real LLM reasoning step (Section 3.1); this module's only
    job is to refuse to run it at all against an unlocked/unconfirmed DSD."""

    actors: list[str] = Field(default_factory=list)
    incentives: list[str] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class ValidationThreadResult(BaseModel):
    """One of Phase 2's three named trajectories (Section 3.1: Coherence,
    Security, Cooperation). Deliberately a plain pass/fail reasoning
    judgment with rationale, never a synthetic divergence number or
    manifold ratio — see core/ksp_finality.py's module docstring."""

    thread: str  # "coherence" | "security" | "cooperation"
    passed: bool
    rationale: str


class IntegrityGateResult(BaseModel):
    """Phase 3 (Section 3.1): Keystone Identification, Forward/Inverse
    Symmetry, Domain Closure. Three independent structural booleans,
    deterministically ANDed by core.ksp_finality.audit_integrity_passed —
    never taken on the LLM's own summary judgment alone."""

    keystone_stable: bool
    forward_inverse_consistent: bool
    domain_closure_ok: bool
    rationale: str


class UnknownAuditFinding(BaseModel):
    """One unknown declared in Phase 1, checked against the emerging
    artifact before Compaction (Section 3.1's 'Unknown Variable Audit').
    core.ksp_finality treats ANY unresolved declared unknown as sufficient
    to force the Conditional outcome — a conservative simplification of
    'the artifact's core recommendation depends on it', made explicit here
    rather than silently assumed."""

    unknown: str
    resolved: bool


class KSPFinalityResult(BaseModel):
    """The complete record of one Finality pass — everything Phases 1-3 and
    5 produced, plus the outcome that decides what happens next in
    chat.py: CLEARED/CONDITIONAL artifacts proceed to the (unchanged, already
    real) Adjudication Buffer; DOWNGRADED_TO_VALIDATION artifacts do not,
    unless a Permanent Tier C category independently requires adjudication
    regardless of KSP's own outcome (that gate is unconditional per
    PA Action Kernel Section 3.3 / CLAUDE.md Section 2 — KSP downgrading the
    Finality *claim* never excuses the separate, unconditional permanent-
    category gate)."""

    dsd_ref: str
    outcome: KSPOutcome
    projection: Optional[StructuralProjection] = None
    validation_threads: list[ValidationThreadResult] = Field(default_factory=list)
    integrity_gate: Optional[IntegrityGateResult] = None
    unknown_audit: list[UnknownAuditFinding] = Field(default_factory=list)
    compaction_text: str = ""
    downgrade_reason: Optional[str] = None
