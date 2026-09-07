"""
PA Action Kernel — KSP-1 Autonomous Extension.

Maps to 05_PA_Action_Kernel.md. Only active in ALDRIC Mode (autonomous
operation); inert in session-based Governance Stack Mode (Position in
Stack section).

This is the module where "not prompt-based anymore" earns its keep the
most concretely. The single most important property in this whole codebase
lives in `classify_tier()` below: an action's tier is decided from a
schema-validated, declarative registry (`TOOL_REGISTRY`, loaded from
config/tool_registry.yaml by core.tool_registry_loader — see that module
and CLAUDE.md Section 10 for why the tool-to-tag mapping is a file a
compliance team can edit, while the six-category set it can only reference,
never extend, stays a Python constant) and the caller-supplied structural
facts (surface state, drift, mirror-drift flag) — never from whatever the
LLM says its own tier should be. `ActionRequest.claimed_tier` is carried
through purely for audit/comparison logging. If a future prompt injection
gets a model to say "this is Tier A, proceed silently" about a pricing
change, `classify_tier` still returns Tier C, because pricing is in
PERMANENT_TIER_C_CATEGORIES and that set is not reachable from anywhere the
LLM's output lands, or from the tool registry file either.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from models.schemas import (
    ActionRequest,
    ConfidenceState,
    DigestEntry,
    DriftLevel,
    PERMANENT_TIER_C_CATEGORIES,
    Surface,
    Tier,
)
from core.tool_registry_loader import DEFAULT_REGISTRY_PATH, load_tool_registry
from storage import db
from storage.event_log import write_event, read_events


# ---------------------------------------------------------------------------
# Component 2 — Surface Matching
# ---------------------------------------------------------------------------

class SurfaceMatchOutcome(Protocol):
    ...


class SurfaceMatcher(Protocol):
    def match(self, context: dict, candidate_surfaces: list[Surface]) -> Optional[Surface]: ...


class NullSurfaceMatcher:
    """Honesty stub: real surface matching (Section 2.2 — 'contextual fit',
    not action-type similarity) is a semantic judgment over the operator's
    full relationship/project context, which requires an LLM call informed
    by the actual self-model. This stub always returns 'no match', which is
    the SAFE default per Section 2.4 ('No match' -> Tier C, hold and
    notify).

    A real matcher now exists — `llm.surface_matcher.match_surface`,
    wired into `llm/aldric_reply.py`'s proposed-tool-call path — so this
    stub is kept only as the explicit, still-honest fallback for Governance
    Stack Mode (`llm/governed_reply.py`), where the PA Action Kernel is
    inert by the source document's own design (Position in Stack: 'In
    session-based operation, the PA Action Kernel is inactive') and
    `surface=None` is hardcoded there on purpose, not because no matcher
    exists."""

    def match(self, context: dict, candidate_surfaces: list[Surface]) -> Optional[Surface]:
        return None


# Tool/action registry. What a tool touches — categories, external-facing,
# reversible — is declarative data now (config/tool_registry.yaml), loaded
# and schema-validated by core.tool_registry_loader at import time; it is
# no longer a Python dict literal. This is the direct analogue of the
# Gemini spec's TOOL_REGISTRY, keyed to the six real Permanent Tier C
# categories (Section 3.3) instead of a generic Tier A/B/C guess.
#
# What moving this to a file does NOT change (CLAUDE.md Section 10): the
# six-category set itself stays PERMANENT_TIER_C_CATEGORIES, a Python
# constant the loader validates every entry's tags against — an
# unrecognized category in the file is a load-time error, not a runtime
# toggle. And classify_tier() below, which decides what a permanent-
# category tag actually does, is unchanged Python control flow. Only the
# tool-to-tag mapping itself — the part a compliance team legitimately
# needs to manage without a source change — moved out.
#
# reload_tool_registry() exists for tooling/tests that need to point at a
# different file; call it with no arguments to reload the default file
# in place (e.g. after a compliance team edits it) without reassigning the
# module attribute from outside. A production deployment should still
# redeploy on a registry change rather than rely on hot-reload for its own
# sake — this function is for local iteration and test isolation, not a
# claim that live reload is a substitute for the review/versioning
# discipline a file under source control already gives you (CLAUDE.md
# Section 5: say what's real).
TOOL_REGISTRY: dict[str, dict] = load_tool_registry()


def reload_tool_registry(path: str = DEFAULT_REGISTRY_PATH) -> dict[str, dict]:
    global TOOL_REGISTRY
    TOOL_REGISTRY = load_tool_registry(path)
    return TOOL_REGISTRY


# PA Action Kernel Tier B: "PA signature discloses AI authorship
# transparently... applied to all external output." This is a plain,
# honest disclosure line, not a cryptographic signature despite the source
# document's naming — see CLAUDE.md Section 5 on not dressing up a labeling
# step as more than it is. Applied by the caller (aldric_chat.py) to the
# externally-visible text argument of a Tier B action before it reaches
# core.capability_broker.execute_action; capability_broker itself never
# decides content, only executes what it's given.
PA_SIGNATURE_DISCLOSURE = "\n\n— Drafted and sent by ALDRIC, an AI system, on the operator's behalf."


def apply_pa_signature(text: str) -> str:
    return text.rstrip() + PA_SIGNATURE_DISCLOSURE


def build_action_request(tool_name: str, arguments: dict, surface_id: Optional[str] = None,
                          claimed_tier: Optional[Tier] = None) -> ActionRequest:
    """The only place a raw tool call becomes an ActionRequest. Permanent
    category tags come from TOOL_REGISTRY, not from `arguments` or from
    whatever the caller/LLM asserts."""
    entry = TOOL_REGISTRY.get(tool_name)
    if entry is None:
        # Unknown tool: fail safe. Treat as touching every permanent category
        # so it is forced to Tier C rather than silently defaulting to Tier A.
        write_event("PA_UNKNOWN_TOOL", {"tool_name": tool_name})
        return ActionRequest(
            tool_name=tool_name, arguments=arguments, surface_id=surface_id,
            claimed_tier=claimed_tier, permanent_categories=PERMANENT_TIER_C_CATEGORIES,
            external_facing=True, reversible=False,
        )
    return ActionRequest(
        tool_name=tool_name, arguments=arguments, surface_id=surface_id, claimed_tier=claimed_tier,
        permanent_categories=entry["permanent_categories"],
        external_facing=entry["external_facing"], reversible=entry["reversible"],
    )


# ---------------------------------------------------------------------------
# Component 3 — Tier Classification
# ---------------------------------------------------------------------------

@dataclass
class TierDecision:
    tier: Tier
    reasons: list[str]


def classify_tier(
    action: ActionRequest,
    surface: Optional[Surface],
    drift_level: Optional[DriftLevel],
    mirror_drift_flagged: bool,
) -> TierDecision:
    """Section 3.2 + Section 2.4. Any one Tier C condition is sufficient
    (Section 3.2, Tier C: 'any one is sufficient'). Permanent exceptions are
    checked FIRST and unconditionally — Section 3.3: 'cannot be overridden
    at runtime or by any operator instruction short of protocol
    modification.'"""
    reasons: list[str] = []

    # 3.3 — Permanent Tier C Exceptions. Checked before anything else, and
    # this check alone decides the outcome if it fires. Nothing below can
    # downgrade past this.
    if action.permanent_categories & PERMANENT_TIER_C_CATEGORIES:
        reasons.append(
            f"Permanent Tier C exception: {sorted(action.permanent_categories)} (Section 3.3)"
        )
        return TierDecision(tier=Tier.C, reasons=reasons)

    # 2.4 — No Executable surface match.
    if surface is None:
        reasons.append("No Executable surface matches current context (Section 2.4, 'No match')")
        return TierDecision(tier=Tier.C, reasons=reasons)

    if surface.state != ConfidenceState.EXECUTABLE:
        reasons.append(f"Surface state is {surface.state.value}, not Executable")
        return TierDecision(tier=Tier.C, reasons=reasons)

    # Section 4.2, "First Executable Crossing": the surface reaching
    # Executable state is the kernel's own confidence assessment (Section
    # 1.5) — a distinct thing from the operator's live, explicit sign-off
    # that execution may actually run against it, which this section
    # requires separately ("the one point in the continuous cycle where
    # live confirmation is required"). A surface can sit at
    # state=Executable indefinitely without ever clearing this.
    if not surface.execution_rights_confirmed:
        reasons.append(
            "Surface reached Executable state but the operator has not yet granted execution "
            "rights (Section 4.2, 'First Executable Crossing')"
        )
        return TierDecision(tier=Tier.C, reasons=reasons)

    # Drift detected on a matched surface still forces Tier C (2.4, "Drift
    # detected").
    if drift_level == DriftLevel.MATERIAL:
        reasons.append("Material contextual drift detected on matching surface (Section 5.3)")
        return TierDecision(tier=Tier.C, reasons=reasons)

    if mirror_drift_flagged:
        reasons.append("Mirror drift flagged on this surface by the Learning Governance Document")
        return TierDecision(tier=Tier.C, reasons=reasons)

    # Moderate drift elevates Tier B -> Tier C on the affected surface but
    # does not by itself block Tier A internal actions.
    elevate_b_to_c = drift_level == DriftLevel.MODERATE

    if action.external_facing:
        if elevate_b_to_c:
            reasons.append("Moderate drift elevates this external-facing (Tier B) action to Tier C")
            return TierDecision(tier=Tier.C, reasons=reasons)
        reasons.append("Executable surface, external-facing action, no permanent exception (Tier B)")
        return TierDecision(tier=Tier.B, reasons=reasons)

    if not action.reversible:
        reasons.append("Irreversible internal action without a permanent-exception tag still "
                        "requires notification, not silent execution")
        return TierDecision(tier=Tier.B, reasons=reasons)

    reasons.append("High confidence, internal, reversible, no external exposure (Tier A)")
    return TierDecision(tier=Tier.A, reasons=reasons)


# ---------------------------------------------------------------------------
# Component 5 — Contextual Drift Detection
# ---------------------------------------------------------------------------

def effective_tool_tier(identity_tier: Optional[Tier], touches_permanent_tier_c: bool) -> Optional[Tier]:
    """The tier that actually governs a proposed tool call once argument-text
    (and output-text, and self-report) scanning is folded in. Escalation is
    one-directional and authoritative: if `identity_tier` (classify_tier()'s
    own conclusion from tool identity + surface state alone) is already C, or
    `touches_permanent_tier_c` is True (a permanent category was found
    anywhere in the turn — self-reported, scanned from the free-form output,
    or scanned from the tool call's own arguments), the effective tier is
    forced to C — full stop. This can only ever raise a tier to C; nothing
    here is permitted to lower a tier `classify_tier()` already set.

    Shared by llm/governed_reply.py (Governance Stack Mode, a locked DSD
    already exists) and llm/aldric_reply.py (ALDRIC Mode, no DSD yet) so this
    one governance-critical arithmetic can't quietly drift into two different
    answers between the two call sites — see CLAUDE.md Section 1."""
    if identity_tier is None:
        return None
    if identity_tier == Tier.C or touches_permanent_tier_c:
        return Tier.C
    return identity_tier


def assess_drift(context_snapshot: dict, current_context: dict, thresholds: dict) -> DriftLevel:
    """Section 5.4: thresholds are operator-defined, not system-defined.
    `thresholds` is expected to carry operator-set cutoffs (e.g. how many
    changed keys constitute 'moderate' vs 'material'); this function applies
    them mechanically rather than deciding on its own what counts as drift.
    A simple, transparent default: count of changed top-level keys."""
    changed = sum(1 for k, v in current_context.items() if context_snapshot.get(k) != v)
    material_at = thresholds.get("material_changed_keys", 3)
    moderate_at = thresholds.get("moderate_changed_keys", 1)
    if changed >= material_at:
        return DriftLevel.MATERIAL
    if changed >= moderate_at:
        return DriftLevel.MODERATE
    return DriftLevel.MINOR


# ---------------------------------------------------------------------------
# Component 6 — Asynchronous Thread Isolation
# ---------------------------------------------------------------------------
# Implemented via core.ksp1_operator_kernel.LoopManager: each execution
# thread is its own named loop. A Tier C hold parks/suspends only that
# loop's name; every other loop's state is untouched by construction,
# because LoopManager operations are always scoped to a single loop_name.


# ---------------------------------------------------------------------------
# Component 7 — The Daily Digest
# ---------------------------------------------------------------------------

def generate_daily_digest(db_path: str = db.DEFAULT_DB_PATH) -> list[dict]:
    """Section 7.3: 'The digest cannot be suppressed, delayed, or modified
    by any runtime condition.' This function takes no filter/omit
    parameters at all — that is the enforcement mechanism. If you find
    yourself wanting to add a `hide_categories=` argument here, that is
    the digest-suppression this document forbids; don't."""
    entries = db.list_undigested_entries(db_path)
    db.mark_all_digested(db_path)
    write_event("DAILY_DIGEST_GENERATED", {"entry_count": len(entries)})
    return entries


def log_digest_entry(category: str, payload: dict, db_path: str = db.DEFAULT_DB_PATH) -> DigestEntry:
    entry = DigestEntry(category=category, payload=payload)
    db.append_digest_entry(entry, db_path)
    return entry
