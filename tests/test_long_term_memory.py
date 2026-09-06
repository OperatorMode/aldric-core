"""
Tests for core.long_term_memory — the deterministic storage-facing layer
for standing preferences and facts (see that module's docstring for the
full reasoning). No LLM calls here; llm/aldric_reply.py is what actually
uses this during a real turn (see tests/test_aldric_reply.py for that side).
"""
import core.long_term_memory as ltm


def test_setting_a_preference_for_a_new_scope_is_retrievable():
    ltm.set_preference("client", "Formal tone, no jokes.")
    pref = ltm.get_preference("client")
    assert pref is not None
    assert pref.content == "Formal tone, no jokes."


def test_setting_a_second_preference_for_the_same_scope_replaces_the_first():
    """Correction Absolute applied to preferences (CLAUDE.md Section 7): the
    latest instruction for a scope applies immediately and completely, no
    trace of the old one left as the active preference."""
    ltm.set_preference("client", "Formal tone.")
    ltm.set_preference("client", "Actually, warmer and more casual.")
    client_prefs = [p for p in ltm.list_preferences() if p.scope == "client"]
    assert len(client_prefs) == 1
    assert client_prefs[0].content == "Actually, warmer and more casual."


def test_preferences_for_different_scopes_do_not_collide():
    ltm.set_preference("client", "Formal.")
    ltm.set_preference("team", "Casual.")
    assert ltm.get_preference("client").content == "Formal."
    assert ltm.get_preference("team").content == "Casual."


def test_unknown_scope_returns_none():
    assert ltm.get_preference("nonexistent_scope") is None


def test_facts_are_append_only_not_replaced():
    ltm.record_fact("general", "Invoice numbers start with INV-", source="operator_clarification")
    ltm.record_fact("general", "Client portal launched in July.", source="casual_turn")
    facts = ltm.list_facts(scope="general")
    assert len(facts) == 2
    contents = {f.content for f in facts}
    assert "Invoice numbers start with INV-" in contents
    assert "Client portal launched in July." in contents


def test_list_facts_can_filter_by_scope():
    ltm.record_fact("general", "A general fact.", source="test")
    ltm.record_fact("client:acme", "Acme's PO number format is PO-####.", source="test")
    acme_facts = ltm.list_facts(scope="client:acme")
    assert len(acme_facts) == 1
    assert acme_facts[0].content == "Acme's PO number format is PO-####."
