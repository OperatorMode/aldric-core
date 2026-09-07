"""
KSP-0 / DSD Discovery Loop — the conversational half.

01_KSP-0_DSD.md Sections 5-7 describe a specific interview posture: literal
extraction only, no binary/multiple-choice questions, no field names
exposed, a "Reason Anchor" (why more info is needed) always precedes an
"Open, Content-Eliciting Question." This module's only job is to run ONE
step of that interview and return structured field guesses — it does not
decide when the surface is complete (that's `DecisionSurfaceDocument
.completeness_test()`), it does not reflect the surface back (that's done
deterministically in chat.py, from the actual locked field values, not by
asking the model to re-describe what it just extracted — asking it to
restate its own extraction is exactly the kind of self-report this
project's own governance rules distrust), and it never marks anything
confirmed (that's `core.ksp0_dsd.DSDGate.confirm`, checked against the
literal canonical vocabulary, never inferred from this module's output).
"""
from __future__ import annotations

import json

from models.schemas import DSDField
from llm.client import DEFAULT_MODEL, LLMFormatError, complete, extract_json_object

_SYSTEM_PROMPT = """You are running the KSP-0 / DSD Discovery Loop for ALDRIC, a governance
system. Your ONLY job this turn is to extract, from the conversation so far,
as many of six fields as the operator's own words already make unambiguous,
and — for whatever remains missing — produce ONE next utterance that
elicits it.

The six fields (do not reveal these names to the operator):
- decision_locus: what exactly is being decided
- operational_domain: the field/environment it operates within
- authority_boundary: who holds decision power
- time_horizon: the relevant timeframe
- constraints_and_invariants: what cannot move (a list)
- risk_posture: tolerance for uncertainty and downside

Hard rules, non-negotiable:
- Extraction is LITERAL only. No inference, no projection, no assumption.
- Never ask a yes/no or multiple-choice question.
- Never expose the field names or any governance/protocol vocabulary.
- Never adopt a persona, mirror the operator's tone, or offer suggestions.
- If a field is missing, the next utterance MUST be: a short neutral Reason
  Anchor (why you're asking) followed by ONE open question that embeds the
  missing meaning inside it, forcing free-text content back.
- If an earlier answer already covers a field, do not ask about it again.
- Do not end with an offer, a suggestion, or an implied next step beyond the
  single question you are asking right now.

Respond with ONLY this JSON (no other text):
{"extracted_fields": {"<field_name>": "<value>", ...only fields you can
confidently fill from what's literally been said...}, "missing_fields":
["<field_name>", ...], "next_utterance": "<Reason Anchor + Open Question, or
empty string if missing_fields is empty>"}"""


def run_interview_step(conversation: list[dict[str, str]]) -> dict:
    """`conversation` is a list of {"role": "user"|"assistant", "content": str}
    turns so far (the discovery-loop turns only). Returns the parsed dict
    described in the system prompt above. Raises ValueError on unparseable
    output rather than guessing — an interview step whose output can't be
    trusted structurally should be retried, not silently patched over."""
    transcript = "\n".join(f"{t['role']}: {t['content']}" for t in conversation)
    if not transcript.strip():
        # First turn of the Discovery Loop: no operator input exists yet.
        # The API rejects an empty user message outright, so we send an
        # explicit start-of-loop marker instead of blank content — this
        # carries no operator-provided information and extracts nothing,
        # it only prompts the model to produce its opening Reason Anchor +
        # Open Question per the system prompt above.
        transcript = "(Discovery Loop start — no operator input yet. Ask your opening question.)"
    # max_tokens is a hard cap on TOTAL output — thinking tokens plus the
    # actual text — and DEFAULT_MODEL (claude-sonnet-5) thinks by default
    # before writing a word. 500 was sized for a "no thinking" reply and
    # left too little room once thinking ate its share, producing
    # truncated/invalid JSON. 2000 leaves real headroom for both.
    raw = complete(system=_SYSTEM_PROMPT, user_message=transcript, model=DEFAULT_MODEL, max_tokens=2000)
    try:
        parsed = json.loads(extract_json_object(raw))
    except json.JSONDecodeError as exc:
        raise LLMFormatError("DSD interview step", raw) from exc

    known_fields = {f.value for f in DSDField}
    extracted = {k: v for k, v in parsed.get("extracted_fields", {}).items() if k in known_fields}
    missing = [m for m in parsed.get("missing_fields", []) if m in known_fields]
    return {
        "extracted_fields": extracted,
        "missing_fields": missing,
        "next_utterance": parsed.get("next_utterance", ""),
    }
