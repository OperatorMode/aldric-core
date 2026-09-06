from core.permanent_category_scan import scan_for_permanent_categories


def test_pricing_language_detected():
    hits = scan_for_permanent_categories("Let's update the price to $5,000 for this package.")
    assert "pricing_or_cost_commitment" in hits


def test_contract_language_detected():
    hits = scan_for_permanent_categories("I'll draft the contract terms for your signature.")
    assert "contractual_terms_or_obligation" in hits


def test_ordinary_text_has_no_hits():
    hits = scan_for_permanent_categories("Here's a summary of what we discussed in the meeting today.")
    assert hits == frozenset()


def test_multiple_categories_can_fire_together():
    hits = scan_for_permanent_categories(
        "I'll commit to a deadline of Friday and include that in the contract at $2,000."
    )
    assert {"deadline_commitment", "contractual_terms_or_obligation", "pricing_or_cost_commitment"} <= hits
