"""
Thin Anthropic client wrapper.

This is the ONLY module that should import `anthropic` directly. Every
call site elsewhere in the codebase goes through one of the functions here,
so that (a) the model name/version is set in exactly one place, and (b) it
stays obvious, when reading core/*, which steps are genuine LLM reasoning
and which are deterministic gates — anything not routed through here is
deterministic by construction.

Per the project's own standing infrastructure rule, the API key is read
from the environment variable `Aldric-API` (matching the Windows machine
setup already confirmed for this project) with a fallback to the more
conventional `ANTHROPIC_API_KEY` for portability.
"""
from __future__ import annotations

import json
import os
import re

try:
    import anthropic
except ImportError:  # pragma: no cover - exercised only if dependency missing
    anthropic = None  # type: ignore

from storage.event_log import write_event

DEFAULT_MODEL = os.environ.get("ALDRIC_PRIMARY_MODEL", "claude-sonnet-5")
SIDECAR_MODEL = os.environ.get("ALDRIC_SIDECAR_MODEL", "claude-haiku-4-5-20251001")


class LLMFormatError(Exception):
    """Raised by every call site in this codebase that asks a model for
    JSON-only output (llm/dsd_interview.py, llm/governed_reply.py,
    llm/aldric_reply.py) when the response still can't be parsed as JSON
    after extract_json_object()'s best effort.

    Found live: all three call sites used to build a plain ValueError whose
    message embedded the complete raw response (`f"...: {raw!r}"`). Every
    caller of those functions (chat.py, aldric_chat.py) catches failures
    with a generic `except Exception as exc: print(f"...: {exc}")` so it
    can keep the session alive after one bad turn — which meant the raw,
    ungated, unscanned model output was printed straight to the operator's
    terminal, bypassing requires_escalation/permanent-category scanning
    entirely, since the crash happens before a CasualTurnResult/
    GovernedTurnResult can even be constructed to run those checks against.
    A live adversarial test found this: two separate parse failures put a
    fully generated smtplib script — real recipient address, real pricing
    text — directly on the operator's screen (README gap-list; Attacks 8/9).

    The fix is structural, not just "write a better message": raw is kept
    as a plain attribute, never folded into str(exc)/repr(exc), so a caller
    has to reach for `.raw` deliberately (this class does exactly that,
    once, below, to preserve it for audit) rather than getting it for free
    from a bare print(exc). str(exc) is always safe to show an operator.

    Every instance writes an `llm_format_error` audit event as a side
    effect of construction — not left to each of chat.py's/aldric_chat.py's
    several call sites to remember individually — so the raw text is never
    silently discarded: storage/event_log.py is append-only telemetry, so
    nothing here can leak back into what's shown or how a turn is decided
    (module docstring: "logs are telemetry, they do not influence
    inference"), and the Daily Digest only ever surfaces what
    core.pa_action_kernel.log_digest_entry() explicitly adds, not the raw
    event_log — so this stays a private audit trail, not a second display
    path with the same leak deferred to `digest`."""

    def __init__(self, context: str, raw: str):
        super().__init__(
            f"{context} returned output that could not be parsed as the "
            "expected JSON. The raw response was logged for review, not "
            "displayed here."
        )
        self.context = context
        self.raw = raw
        write_event("llm_format_error", {"context": context, "raw_output": raw})


def _api_key() -> str:
    key = os.environ.get("Aldric-API") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "No Anthropic API key found in environment (checked Aldric-API, ANTHROPIC_API_KEY). "
            "Set one before calling into llm.client — nothing in core/ requires this at import time."
        )
    return key


def get_client() -> "anthropic.Anthropic":
    if anthropic is None:
        raise RuntimeError("The `anthropic` package is not installed. `pip install anthropic`.")
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    default_headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
    return anthropic.Anthropic(api_key=_api_key(), default_headers=default_headers)


def strip_json_code_fence(raw: str) -> str:
    """Models sometimes wrap a requested JSON-only reply in a ```json ... ```
    fence despite being told to respond with only the JSON. This is purely a
    formatting-layer concession shared by every call site that asks for
    structured output — it does not relax parsing itself, malformed JSON
    inside (or outside) a fence still fails exactly as before."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence (``` or ```json)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        elif lines and lines[-1].strip().endswith("```"):
            lines[-1] = lines[-1].rsplit("```", 1)[0]
        text = "\n".join(lines).strip()
    return text


def extract_json_object(raw: str) -> str:
    """Best-effort recovery of a JSON object from a reply that wasn't
    JSON-only, despite every call site's system prompt asking for exactly
    that. strip_json_code_fence() already handles a single fence wrapping
    the *entire* reply; this handles the messier shape a live adversarial
    test actually produced — conversational prose, sometimes a code block
    of something else entirely (a generated script), THEN the real
    structured answer, fenced or bare.

    This only widens what text a caller tries json.loads() on. It never
    relaxes what counts as valid JSON once a candidate is chosen — every
    caller still does its own json.loads() and still fails closed
    (LLMFormatError) if nothing here parses, exactly as before this
    existed. For an already-clean JSON-only reply, the first candidate
    (strip_json_code_fence's own output) parses immediately and every
    caller's behaviour is unchanged.

    Candidates are tried in order and the first one that parses wins:
      1. strip_json_code_fence(raw) — the existing, single-fence path.
      2. Every ```json ... ``` / ```...``` fenced {...} block in the raw
         text, LAST one first — a model that talks first and answers last
         (the pattern actually seen live) puts its real answer in the
         final fence.
      3. The widest brace span in the raw text (first "{" to last "}"), as
         a last resort for a bare, unfenced object buried in prose.
    If nothing parses, the original fence-stripped text is returned
    unchanged so the caller's own json.loads() raises exactly the error it
    always would have."""
    fence_stripped = strip_json_code_fence(raw)
    candidates = [fence_stripped]

    fence_matches = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    candidates.extend(reversed(fence_matches))

    first_brace = raw.find("{")
    last_brace = raw.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(raw[first_brace:last_brace + 1])

    for candidate in candidates:
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return candidate
    return fence_stripped


def complete(system: str, user_message: str, model: str = DEFAULT_MODEL, max_tokens: int = 1024) -> str:
    """A single, non-streaming completion. Callers are responsible for
    parsing structured output out of the returned text and for passing it
    through the appropriate core/ gate before treating it as binding.

    `response.content[0]` is NOT reliably the text block: a model with
    extended thinking on (claude-sonnet-5 among them) puts a `ThinkingBlock`
    (or `RedactedThinkingBlock`) first in the content list, ahead of the
    actual `TextBlock`. Indexing blindly broke every call site the moment
    such a model was used. Find the first block whose `type` is "text"
    instead of assuming position — correct regardless of how many
    thinking/other blocks precede it."""
    client = get_client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""
