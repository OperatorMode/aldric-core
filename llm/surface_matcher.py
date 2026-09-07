"""
The real Surface Matcher — PA Action Kernel Component 2 (Section 2.2).

`core.pa_action_kernel.NullSurfaceMatcher` was a deliberately honest stub:
its own docstring says real surface matching "requires an LLM call informed
by the actual self-model." This module is that LLM call — CLAUDE.md Section
5 names "semantic surface matching" explicitly as a genuine reasoning step
that belongs in `llm/`, gated by `core/` before it can become binding. This
is that gate's LLM half.

What "contextual fit" means here (Section 2.2): not action-type similarity —
a `send_client_email` call doesn't match a surface just because the surface
is also about emails. The question is whether THIS specific situation (this
client, this kind of ask, this conversation) sits within the boundary of
something ALDRIC has already built real, tested confidence about. Two costs
are asymmetric and the prompt below is written to reflect that: a false "no
match" costs one extra Tier C hold — annoying, never wrong. A false match
grants a tier the situation hasn't actually earned — the one mistake this
document exists to prevent. When genuinely unsure, this always says no
match.

Candidates are pre-filtered to Executable-state surfaces only, both as a
courtesy (Section 2.0: "Surface matching runs against Executable surfaces")
and as defense in depth — even if a caller passes a Developing or Suspended
surface by mistake, it is never offered to the model as something that could
be matched.

What this module does NOT decide: whether a match is close enough to count
as "partial" rather than "no match" (Section 2.4 lists Partial Match as its
own outcome). Both partial and no match produce the same Tier C result
downstream (`core.pa_action_kernel.classify_tier` only distinguishes "a
real Surface" from `None`), so this module collapses that distinction
deliberately rather than inventing a three-way return type nothing
downstream would act on differently — the model is still asked to explain
its reasoning (kept for the audit log, never gated on) so a genuine partial
fit is visible in the logs even though it resolves the same way as no match.

What matching a surface does NOT do: execute anything. There is no
Capability Broker in this codebase — a proposed tool call is still only
ever classification-and-audit metadata (see `llm/aldric_reply.py`'s module
docstring). A correctly matched Executable surface can now make
`classify_tier` return Tier A/B instead of an automatic Tier C, but nothing
downstream of that decision currently acts on it. This closes README
gap-list item 1 for classification purposes; it does not, by itself, make
ALDRIC autonomous.
"""
from __future__ import annotations

import json
from typing import Optional

from llm.client import DEFAULT_MODEL, complete, strip_json_code_fence
from models.schemas import ConfidenceState, Surface

_SYSTEM_PROMPT = """You are the Surface Matcher for ALDRIC's PA Action Kernel (Section 2.2).

ALDRIC is about to propose taking an action. Your only job is to decide
whether the CURRENT situation genuinely falls within the boundary of one of
the candidate surfaces listed below — each one is a class of situation
ALDRIC has already built real, tested confidence handling (multiple
confirmations, corrections applied and resolved). This is a judgment about
contextual fit, not keyword or action-type similarity: a proposed action
using the same tool name as a surface's history does NOT by itself mean it
matches. What matters is whether this specific situation — this counterpart,
this kind of ask, what's actually being asked for right now — sits inside
what that surface's history actually covers.

The two ways to get this wrong are not equally costly. Answering "no match"
when a real match existed costs one extra confirmation step — mildly
annoying, never dangerous. Answering with a match that isn't real hands the
situation more autonomy than it has actually earned — this is the mistake
the whole protocol exists to prevent. If you are not genuinely confident the
fit is real, answer null.

Respond with ONLY this JSON:
{"surface_id": "<the exact id of the one candidate that genuinely matches>" or null,
"reasoning": "<one sentence — kept for the audit log, not otherwise used>"}"""


def _format_candidates(candidates: list[Surface]) -> str:
    lines = []
    for s in candidates:
        lines.append(
            f'- id="{s.surface_id}": {s.description} '
            f'(confirmed {s.confirmation_count}x, corrected {s.correction_count}x)'
        )
    return "\n".join(lines)


def match_surface(context: dict, candidate_surfaces: list[Surface]) -> Optional[Surface]:
    """`context` carries whatever the caller has about the current
    situation — `llm/aldric_reply.py` passes the proposed tool call
    (name/arguments) plus the conversation excerpt that produced it.
    `candidate_surfaces` may be any surfaces at all; this function filters
    to Executable ones itself (see module docstring) rather than trusting
    the caller to have done so.

    Never calls the model when there is structurally nothing to match
    against — an empty or all-non-Executable candidate list returns None
    immediately, no network round trip, no cost. This also keeps every
    existing test that proposes a tool call with no surfaces on record
    (the common case today) exactly as deterministic and network-free as
    before this module existed.

    Fails closed on anything unexpected: non-JSON output, a missing
    `surface_id`, or a `surface_id` that isn't one of the real candidates
    (the model can point at a candidate by id — it can never invent one, and
    nothing about a returned Surface's fields ever comes from the model's
    own claims about it, only from what's actually on record in storage)
    all resolve to None, exactly like an honest "no match" would."""
    if not candidate_surfaces:
        return None

    executable = [s for s in candidate_surfaces if s.state == ConfidenceState.EXECUTABLE]
    if not executable:
        return None

    prompt = (
        f"Proposed action: tool_name={context.get('tool_name')!r}, "
        f"arguments={json.dumps(context.get('arguments', {}), default=str)}\n\n"
        f"Conversation so far:\n{context.get('conversation_excerpt', '')}\n\n"
        f"Candidate Executable surfaces:\n{_format_candidates(executable)}"
    )

    raw = complete(system=_SYSTEM_PROMPT, user_message=prompt, model=DEFAULT_MODEL, max_tokens=4000)

    try:
        parsed = json.loads(strip_json_code_fence(raw))
    except json.JSONDecodeError:
        return None  # fail closed, never fail open

    claimed_id = parsed.get("surface_id")
    if not claimed_id:
        return None

    by_id = {s.surface_id: s for s in executable}
    return by_id.get(claimed_id)


class LLMSurfaceMatcher:
    """Implements `core.pa_action_kernel.SurfaceMatcher`'s Protocol shape as
    an actual object, per that module's `NullSurfaceMatcher` docstring
    ("wire a real matcher behind this Protocol"). `llm/aldric_reply.py`
    calls the plain `match_surface` function directly instead (simpler to
    monkeypatch in tests, same pattern as `complete` elsewhere in this
    codebase) — this class exists for any caller that wants the Protocol
    itself, and holds no state of its own."""

    def match(self, context: dict, candidate_surfaces: list[Surface]) -> Optional[Surface]:
        return match_surface(context, candidate_surfaces)
