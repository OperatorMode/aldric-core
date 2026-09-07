"""
Confidence Cascade — try to resolve from memory before asking, and turn the
operator's answer to a *reflective* question into real Confirmation/
Correction Signal instead of a one-off answer that teaches the self-model
nothing.

Not one of the eight governance documents' own components — an operator
extension, same footing as `core/long_term_memory.py` and
`core/surface_signal.py` (see those modules' docstrings). Origin: the
operator's own earlier build ("work ALDRIC", a simpler stack wired to a
single Google Drive folder) was observed doing exactly this — when
confidence was low, it checked its own stored data and adjustment history
before ever interrupting the operator, and only asked once that came up
empty. This module is the deterministic core of that behaviour, built to
the same discipline as the rest of `core/`: the *decision* of whether to
resolve, ask now, or park and ask later is a plain function of structural
facts (a Surface's confidence state and mirror-drift flag, whether relevant
memory exists, whether the caller says this is urgent) — never the model's
own unchecked say-so about how sure it feels.

Three design calls the operator made explicitly, each worth keeping visible
here rather than buried in the code:

  1. A successful memory-resolve does NOT, by itself, raise a Surface's
     confidence — Learning Governance Section 6.3, "ALDRIC cannot elevate
     its own confidence unilaterally," is not weakened for this feature.
     Instead, when confidence is real but not yet high enough to act
     silently, the cascade produces a *reflective* question ("we've done
     X before for Y, want me to do the same going forward?") and it is the
     operator's own answer to THAT question — run through
     `classify_reflective_response` below, then dispatched to the exact
     same `core.learning_governance.apply_confirmation` /
     `apply_correction` this codebase already uses for real Confirmation/
     Correction Signal — that actually moves confidence. The memory-check
     only ever decides whether to ask a smarter question, never grows
     trust by itself.
  2. A scoped answer ("yes, for this client") must not generalize the
     broader Surface — it strengthens a narrower one instead
     (`narrow_scope` below), leaving the general Surface's confidence
     exactly where it was. This reuses `core.surface_signal`'s own
     discipline that a scope string IS the Surface's identity; a narrower
     scope is just a more specific identity, not a new mechanism.
  3. A flagged Surface (Section 4.2 mirror drift) never gets to justify
     skipping the operator, full stop, regardless of confirmation_count —
     see `decide_cascade`.

What this module deliberately does NOT do yet: generate the reflective
question's actual wording (an LLM step, belongs in `llm/`, not written
here), decide what counts as "relevant memory" for a given topic (also an
LLM/matching step — the same shape of problem `llm/surface_matcher.py`
already solves for tool calls), or wire any of this into `aldric_chat.py`'s
live turn loop. This is the decision core those pieces will call into, the
same way `core.pa_action_kernel.classify_tier` is the core that
`llm/aldric_reply.py` calls into rather than each caller re-deriving tier
logic itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from core import learning_governance
from core.pa_action_kernel import log_digest_entry
from models.schemas import ConfidenceState, Surface, normalize_utterance
from storage import db
from storage.event_log import write_event

# Section 2.2/3.1 already has a canonical confirmation vocabulary
# (models.schemas.CONFIRMATION_VOCABULARY) for Tier C content ratification.
# This is deliberately a SEPARATE, smaller vocabulary: the reflective
# question this module asks is a different shape of question ("want me to
# do the same going forward?", not "confirmed?"), and conflating the two
# would mean a plain "yes" to a Tier C ratification prompt could
# accidentally also generalize a Surface's confidence, which is not
# something either flow should do to the other.
REFLECTIVE_GENERALIZE_VOCABULARY = frozenset({
    "yes", "yep", "confirmed", "correct", "always", "every time",
    "do that every time", "do that from now on", "yes, every time", "yes always",
})

# Explicit correction language — routed through apply_correction(), which
# has no confidence-gated bypass (CLAUDE.md Section 7). Saying this is
# itself Correction Signal on the Surface: the operator is telling the
# self-model its confidence was too high, not just declining once.
REFLECTIVE_ALWAYS_ASK_VOCABULARY = frozenset({
    "no", "no, ask every time", "ask every time", "always ask",
    "please ask every time", "check with me every time", "no, always ask",
})

# A scoped yes: "yes, for this client" / "just for acme corp" / "only for
# this one". Deliberately a structural pattern match, not an LLM judgment
# call — CLAUDE.md Section 1's discipline applies here exactly as it does
# to classify_confirmation: whether a Surface's confidence moves is a
# governance-critical-ish fact and must come from a function whose output
# is predictable from its input, not from asking the model to interpret
# the reply.
_SCOPE_NARROW_PATTERN = re.compile(
    r"^yes,?\s*(?:but\s+)?(?:just|only)\s+for\s+(.+?)\.?$", re.IGNORECASE
)


class ReflectiveResponse(str, Enum):
    GENERALIZE = "generalize"      # grow confidence on the general Surface
    SCOPE_NARROW = "scope_narrow"  # grow confidence on a narrower Surface only
    ALWAYS_ASK = "always_ask"      # explicit correction: stop trusting this pattern
    AMBIGUOUS = "ambiguous"        # matches nothing canonical — re-prompt, never guess


def classify_reflective_response(text: str) -> tuple[ReflectiveResponse, Optional[str]]:
    """Deterministic classification of the operator's answer to a
    reflective confirmation question. Returns (bucket, scope_qualifier) —
    `scope_qualifier` is only ever set for SCOPE_NARROW (the raw text
    matched after "for", e.g. "this client" or "acme corp"), and is the raw
    extracted string, not yet turned into a scope id — see `narrow_scope`.

    Exact-match discipline throughout, same as
    `models.schemas.classify_confirmation`: an utterance that doesn't
    cleanly match one of the three canonical shapes comes back AMBIGUOUS
    rather than being guessed at from tone or partial overlap. A caller
    getting AMBIGUOUS should re-ask once, neutrally — never assume either
    direction."""
    normalized = normalize_utterance(text)

    if normalized in REFLECTIVE_ALWAYS_ASK_VOCABULARY:
        return ReflectiveResponse.ALWAYS_ASK, None
    if normalized in REFLECTIVE_GENERALIZE_VOCABULARY:
        return ReflectiveResponse.GENERALIZE, None

    match = _SCOPE_NARROW_PATTERN.match(normalized)
    if match:
        return ReflectiveResponse.SCOPE_NARROW, match.group(1).strip()

    return ReflectiveResponse.AMBIGUOUS, None


def narrow_scope(general_scope: str, qualifier: str) -> str:
    """Turns a scope-narrowing qualifier ("this client", "Acme Corp") into
    a distinct, stable scope id, reusing core.surface_signal's own rule
    that a scope string IS the Surface's identity (no semantic matching
    needed — the operator supplied the label). "pricing_discount" plus
    "acme corp" becomes "pricing_discount:acme corp" — a different Surface
    from the general one, with its own independent confirmation/correction
    history. The general Surface's confidence is never touched by anything
    that happens on a narrowed one, or vice versa."""
    return f"{general_scope}:{normalize_utterance(qualifier)}"


@dataclass
class CascadeDecision:
    """What `decide_cascade` recommends, and why. `resolve=True` means act
    or answer now using memory alone, no question. `resolve=False` means a
    question is needed; `ask_now` then says whether it must interrupt the
    operator immediately (True) or can be parked and asked at a natural
    later point (False) — the operator's own "Eisenhower Matrix" framing:
    urgent-and-blocking gets asked now regardless of confidence, everything
    else waits on confidence and memory. `cite_memory` says whether the
    resulting question (now or later) has real prior history to reference
    ("we've done X before for Y...") as opposed to a plain "I don't have
    anything on this yet" ask."""

    resolve: bool
    ask_now: bool
    cite_memory: bool
    reasons: list[str]


# A Surface needs to have actually earned some trust before its confidence
# number is allowed to justify skipping the operator outright — reusing
# Learning Governance's own EXECUTABLE bar (core.learning_governance.
# DEFAULT_PROMOTION_THRESHOLDS) rather than inventing a second number here.
# This is deliberately the SAME bar PA Action Kernel uses before a tool call
# can reach Tier A/B — "confident enough to skip the operator" and
# "confident enough to act without confirmation" are the same underlying
# claim about a Surface, so they use the same threshold rather than two
# thresholds quietly drifting apart.
_TRUSTED_STATE = ConfidenceState.EXECUTABLE


def decide_cascade(surface: Optional[Surface], has_relevant_memory: bool, urgent: bool) -> CascadeDecision:
    """The deterministic gate. `surface` is whatever Learning Governance
    Surface (if any) core.surface_signal / llm.surface_matcher already
    identified as covering this topic — this function does no matching of
    its own, same division of labour as classify_tier() consuming a
    surface rather than finding one. `has_relevant_memory` is whatever the
    caller already established exists in long-term memory or the Surface's
    own conflict-record/confirmation history for this topic. `urgent` is
    the caller's own structural signal for "can this wait" (e.g. nothing
    else can proceed until this is answered) — see module docstring for
    why that stays a caller-supplied signal rather than something guessed
    at here.

    A mirror-drift-flagged Surface can never produce `resolve=True`,
    regardless of confirmation_count — the operator's explicit call (see
    module docstring, point 3): a flagged Surface's confidence number is
    exactly the number under suspicion, so it cannot be the thing that
    justifies not asking."""
    reasons: list[str] = []

    if surface is not None and surface.mirror_drift_flagged:
        reasons.append("Surface is mirror-drift-flagged (Section 4.2) — confidence number is not trusted")
        return CascadeDecision(resolve=False, ask_now=urgent, cite_memory=has_relevant_memory, reasons=reasons)

    trusted = surface is not None and surface.state == _TRUSTED_STATE
    if trusted and has_relevant_memory:
        reasons.append(f"Surface confidence is {_TRUSTED_STATE.value} and relevant memory exists — resolve without asking")
        return CascadeDecision(resolve=True, ask_now=False, cite_memory=True, reasons=reasons)

    if has_relevant_memory:
        reasons.append("Some relevant memory found, but confidence isn't high enough to act on it silently — reflect it back")
    else:
        reasons.append("No relevant memory found")

    if urgent:
        reasons.append("Caller marked this urgent/blocking — ask now rather than park")
    else:
        reasons.append("Not urgent — park the loop and ask at a later, natural point")

    return CascadeDecision(resolve=False, ask_now=urgent, cite_memory=has_relevant_memory, reasons=reasons)


def apply_reflective_response(
    general_scope: str,
    response_text: str,
    operator_instruction_if_correction: str = "",
    domain: str = "",
    db_path: str = db.DEFAULT_DB_PATH,
) -> tuple[ReflectiveResponse, Optional[Surface]]:
    """Classifies the operator's answer to a reflective question and
    dispatches it to the exact existing Learning Governance machinery —
    never a parallel confidence-mutation path of its own:

      * GENERALIZE  -> core.learning_governance.apply_confirmation on the
        general Surface (real Confirmation Signal, Section 2.2).
      * SCOPE_NARROW -> apply_confirmation on a narrower Surface
        (`narrow_scope`) instead; the general Surface is not touched at all.
      * ALWAYS_ASK  -> core.learning_governance.apply_correction on the
        general Surface — a real Correction, subject to the same
        Correction Absolute (CLAUDE.md Section 7) as any other. Requires
        `operator_instruction_if_correction` (what to record as the new
        instruction — e.g. "Always ask before doing X for Y") and `domain`.
      * AMBIGUOUS   -> nothing is mutated; returns (AMBIGUOUS, None) so the
        caller re-prompts once, neutrally, same discipline as an ambiguous
        Tier C ratification utterance.

    Returns (bucket, the Surface that was actually written to, or None for
    AMBIGUOUS)."""
    bucket, qualifier = classify_reflective_response(response_text)

    if bucket == ReflectiveResponse.AMBIGUOUS:
        return bucket, None

    if bucket == ReflectiveResponse.GENERALIZE:
        surface = _get_or_new_surface(general_scope, db_path)
        learning_governance.apply_confirmation(surface, db_path=db_path)
        write_event("CASCADE_REFLECTIVE_GENERALIZE", {"scope": general_scope})
        return bucket, surface

    if bucket == ReflectiveResponse.SCOPE_NARROW:
        assert qualifier is not None  # guaranteed by classify_reflective_response
        narrowed = narrow_scope(general_scope, qualifier)
        surface = _get_or_new_surface(narrowed, db_path)
        learning_governance.apply_confirmation(surface, db_path=db_path)
        write_event("CASCADE_REFLECTIVE_SCOPE_NARROW", {
            "general_scope": general_scope, "narrowed_scope": narrowed,
        })
        return bucket, surface

    # ALWAYS_ASK
    surface = _get_or_new_surface(general_scope, db_path)
    surface, _entry = learning_governance.apply_correction(
        surface, operator_instruction=operator_instruction_if_correction or response_text,
        domain=domain or general_scope, db_path=db_path,
    )
    write_event("CASCADE_REFLECTIVE_ALWAYS_ASK", {"scope": general_scope})
    return bucket, surface


def park_for_later(loop_manager, loop_name: str, question: str, db_path: str = db.DEFAULT_DB_PATH) -> None:
    """The non-urgent half of `CascadeDecision.ask_now=False`: park the
    thread (core.ksp1_operator_kernel.LoopManager, already-built park/
    resume machinery — no new state machine needed here) and log the
    pending question to the Daily Digest as a `held_thread` entry
    (models.schemas.DigestEntry's own documented category) so it surfaces
    at the operator's next natural check-in rather than interrupting them
    now. `loop_manager` is caller-supplied (a LoopManager instance) rather
    than constructed here, matching how the rest of this codebase treats
    LoopManager as session-scoped state the caller owns."""
    loop_manager.park_loop(loop_name)
    log_digest_entry("held_thread", {
        "loop_name": loop_name,
        "question": question,
        "note": "Parked by the confidence cascade — not urgent, ask at the next natural check-in.",
    }, db_path=db_path)


def _get_or_new_surface(scope: str, db_path: str = db.DEFAULT_DB_PATH) -> Surface:
    """Same shape as core.surface_signal._get_or_new_surface: a scope
    string IS the Surface's identity, so look it up by that id and build a
    fresh, unsaved OBSERVING one if nothing exists yet. Kept local rather
    than imported since core.surface_signal's version is a private helper,
    not a shared one — duplicating four lines here is cheaper than making
    that a cross-module dependency for something this small."""
    existing = db.get_surface(scope, db_path)
    if existing:
        return Surface(**existing)
    return Surface(surface_id=scope, description="")
