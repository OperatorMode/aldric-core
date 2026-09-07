"""
Unit tests for llm/client.py's extract_json_object() and LLMFormatError —
the shared fix behind the Attack 8/9 finding (a JSON-parse failure used to
leak the model's complete raw output to the operator's terminal). See
tests/test_aldric_reply.py for the end-to-end proof through run_casual_turn;
these test the two pieces in isolation.
"""
import json

from llm.client import LLMFormatError, extract_json_object, strip_json_code_fence
from storage.event_log import read_events


# --- extract_json_object --------------------------------------------------

def test_clean_json_only_reply_is_unchanged():
    raw = '{"a": 1, "b": [2, 3]}'
    assert extract_json_object(raw) == raw


def test_single_fence_wrapping_the_whole_reply_still_works_as_before():
    raw = '```json\n{"a": 1}\n```'
    assert json.loads(extract_json_object(raw)) == {"a": 1}


def test_prose_then_fenced_json_recovers_the_last_fence():
    raw = (
        "Here's some unrelated code:\n```python\nx = 1\n```\n"
        "And here's the actual answer:\n```json\n{\"a\": 2}\n```"
    )
    assert json.loads(extract_json_object(raw)) == {"a": 2}


def test_bare_json_buried_in_prose_with_no_fence_at_all():
    # Widest-brace-span extraction (first "{" to last "}") is best-effort
    # recovery, not a guarantee for every pathological case (e.g. braces
    # nested inside string values) — this case has none, so it recovers
    # cleanly.
    raw = 'Sure thing — {"a": 3, "b": "ok"} — hope that helps!'
    assert json.loads(extract_json_object(raw)) == {"a": 3, "b": "ok"}


def test_genuinely_unparseable_text_falls_back_to_fence_stripped_original():
    raw = "not json at all, no braces here"
    assert extract_json_object(raw) == strip_json_code_fence(raw)


# --- LLMFormatError --------------------------------------------------------

def test_message_never_contains_the_raw_text():
    raw = "SENSITIVE_PAYLOAD this must never reach str(exc)"
    exc = LLMFormatError("test context", raw)
    assert raw not in str(exc)
    assert raw not in repr(exc)
    assert exc.raw == raw
    assert exc.context == "test context"


def test_construction_writes_an_audit_event_unconditionally():
    raw = "some malformed output"
    LLMFormatError("a specific context", raw)
    events = [e for e in read_events() if e["event"] == "llm_format_error"]
    assert len(events) == 1
    assert events[0]["data"] == {"context": "a specific context", "raw_output": raw}
