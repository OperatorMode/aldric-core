"""
ALDRIC Mode — the lightweight, adaptive entrypoint README gap-list item 5
was still missing. Everything else in core/ so far is either mode-agnostic
(K1 Safety, APEX Supervisor, Learning Governance) or built specifically for
Governance Stack Mode's "a Decision Surface is locked before anything else
happens" assumption (KSP-0, KSP-1, KSP Finality). ALDRIC Mode inverts that
starting condition: ordinary conversation runs with no DSD at all, per
08_Governance_Chain.md's Two-Mode Architecture — "ALDRIC Mode: autonomous,
default, observation-mode initialisation, NO DSD required upfront."

That inversion is only safe if something still decides, on every turn,
whether this has stopped being ordinary conversation. This module is that
something — and per CLAUDE.md Section 1, that decision is never made by
asking the model "was this risky" and trusting the answer. It is made here,
from data the caller supplies structurally: the model's own self-reported
scope/categories (soft signal only, exactly as in Governance Stack Mode's
GovernedTurnResult), the deterministic permanent-category scan of what it
actually wrote (core.permanent_category_scan — authoritative), and, if a
tool call was proposed, PA Action Kernel's classify_tier()/effective_tool_tier
conclusion on it (also authoritative). Escalation only ever goes in one
direction, casual -> governed, the same one-directional principle PA Action
Kernel already applies to tiers (nothing here is allowed to downgrade a
signal that already fired) — see llm/aldric_reply.py for how one turn's
result becomes an EscalationSignal, and aldric_chat.py for what happens once
requires_escalation() returns True.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from models.schemas import PERMANENT_TIER_C_CATEGORIES, Tier


@dataclass
class EscalationSignal:
    """Everything the escalation decision below is allowed to look at. Built
    by llm/aldric_reply.py from one casual turn's output — never constructed
    from the model's self-report alone; see that module for how
    `scanned_categories` and `tool_argument_categories` are produced by the
    same deterministic scanner Governance Stack Mode already uses."""

    self_reported_scope: str  # "exploration" | "validation" | "finality"
    self_reported_categories: frozenset[str] = field(default_factory=frozenset)
    scanned_categories: frozenset[str] = field(default_factory=frozenset)
    tool_identity_tier: Optional[Tier] = None
    tool_argument_categories: frozenset[str] = field(default_factory=frozenset)
    has_proposed_tool_call: bool = False

    @property
    def all_categories(self) -> frozenset[str]:
        return self.self_reported_categories | self.scanned_categories | self.tool_argument_categories

    @property
    def touches_permanent_tier_c(self) -> bool:
        return bool(self.all_categories & PERMANENT_TIER_C_CATEGORIES)

    @property
    def tool_effective_tier(self) -> Optional[Tier]:
        if not self.has_proposed_tool_call:
            return None
        # Local import: keeps this module's only dependency on
        # core.pa_action_kernel confined to the one function that needs it,
        # rather than importing PA Action Kernel's whole surface-matching
        # machinery (which ALDRIC Mode's casual path does not use) at
        # module load time.
        from core.pa_action_kernel import effective_tool_tier
        return effective_tool_tier(self.tool_identity_tier, self.touches_permanent_tier_c)


def requires_escalation(signal: EscalationSignal) -> bool:
    """The one deterministic gate ALDRIC Mode exists to enforce: does this
    turn have to stop being casual conversation and open a real Decision
    Surface? True if the model itself called its own claim "finality"-scope,
    or if the deterministic scan (of the reply text or a proposed tool
    call's arguments) found a Permanent Tier C category the model didn't
    disclose, or if a proposed tool call's effective tier is C. Any one of
    these is sufficient (identical structure to
    GovernedTurnResult.requires_adjudication in llm/governed_reply.py — the
    same governance-critical decision, just evaluated before a DSD exists
    instead of after one is locked), and none of them can be talked back
    down by more text later in the same turn."""
    return (
        signal.self_reported_scope == "finality"
        or signal.touches_permanent_tier_c
        or signal.tool_effective_tier == Tier.C
    )
