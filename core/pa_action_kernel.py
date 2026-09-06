"""
PA Action Kernel — KSP-1 Autonomous Extension.

Maps to 05_PA_Action_Kernel.md. Only active in ALDRIC Mode (autonomous
operation); inert in session-based Governance Stack Mode (Position in
Stack section).

This is the module where "not prompt-based anymore" earns its keep the
most concretely. The single most important property in this whole codebase
lives in `classify_tier()` below: an action's tier is decided from a static,
code-owned registry (`TOOL_REGISTRY`) and the caller-supplied structural
facts (surface state, drift, mirror-drift flag) — never from whatever the
LLM says its own tier should be. `ActionRequest.claimed_tier` is carried
through purely for audit/comparison logging. If a future prompt injection
gets a model to say "this is Tier A, proceed silently" about a pricing
change, `classify_tier` still returns Tier C, because pricing is in
PERMANENT_TIER_C_CATEGORIES and that set is not reachable from anywhere the
LLM's output lands.
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
    notify). Wire a real matcher behind this Protocol before relying on
    Tier A/B autonomous execution for anything that matters."""

    def match(self, context: dict, candidate_surfaces: list[Surface]) -> Optional[Surface]:
        return None


# Static tool/action registry. Each entry is the code-owned declaration of
# what a tool touches. This is the direct analogue of the Gemini spec's
# TOOL_REGISTRY, but keyed to the six real Permanent Tier C categories
# (Section 3.3) instead of a generic Tier A/B/C guess.
TOOL_REGISTRY: dict[str, dict] = {
    "log_internal_note": {"permanent_categories": frozenset(), "external_facing": False, "reversible": True},
    "schedule_internal_reminder": {"permanent_categories": frozenset(), "external_facing": False, "reversible": True},
    "draft_client_email": {"permanent_categories": frozenset(), "external_facing": True, "reversible": True},
    "send_client_email": {"permanent_categories": frozenset(), "external_facing": True, "reversible": False},
    "update_pricing": {"permanent_categories": frozenset({"pricing_or_cost_commitment"}), "external_facing": True, "reversible": False},
    "change_project_scope": {"permanent_categories": frozenset({"scope_commitment_or_change"}), "external_facing": True, "reversible": False},
    "commit_deadline": {"permanent_categories": frozenset({"deadline_commitment"}), "external_facing": True, "reversible": False},
    "sign_contract": {"permanent_categories": frozenset({"contractual_terms_or_obligation", "binding_obligation"}), "external_facing": True, "reversible": False},
    "send_legal_correspondence": {"permanent_categories": frozenset({"legal_matter"}), "external_facing": True, "reversible": False},
}


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
