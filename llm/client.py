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

import os

try:
    import anthropic
except ImportError:  # pragma: no cover - exercised only if dependency missing
    anthropic = None  # type: ignore

DEFAULT_MODEL = os.environ.get("ALDRIC_PRIMARY_MODEL", "claude-sonnet-5")
SIDECAR_MODEL = os.environ.get("ALDRIC_SIDECAR_MODEL", "claude-haiku-4-5-20251001")


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


def complete(system: str, user_message: str, model: str = DEFAULT_MODEL, max_tokens: int = 1024) -> str:
    """A single, non-streaming completion. Callers are responsible for
    parsing structured output out of the returned text and for passing it
    through the appropriate core/ gate before treating it as binding."""
    client = get_client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text if response.content else ""
