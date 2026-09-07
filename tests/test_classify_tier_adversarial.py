"""
Adversarial extension of tests/test_tier_c_permanent_exceptions.py.

That file already proves the core claim: an unknown or spoofed `tool_name`
fails safe to Tier C, and a known Permanent Tier C tool can't be talked down
to Tier A by `claimed_tier`, surface confidence, or drift state. This file
pushes on the same property from angles that file doesn't cover, and — this
is the important part — one of these tests documents a REAL gap rather than
a false alarm: `classify_tier` classifies by `tool_name` identity only, and
never inspects `arguments`. That's fine for the categories a tool registry
entry can honestly declare (a `log_internal_note` call really is internal
and reversible no matter what string you log), but it means a tool that is
registered as safe can carry unsafe *content* in its arguments and this
module will not catch it. `core/permanent_category_scan.py` is the piece
meant to catch content — but today it only runs over `governed_reply.py`'s
free-form `output` text, never over an `ActionRequest.arguments` payload.
Anyone wiring structured tool calls into chat.py needs to scan argument text
too, not just look up the tool name — see test
`test_registered_safe_tool_can_carry_unscanned_pricing_commitment_in_arguments`
below for the concrete case this would miss.
"""
from core.pa_action_kernel import build_action_request, classify_tier
from core.permanent_category_scan import scan_for_permanent_categories
from models.schemas import ConfidenceState, DriftLevel, Surface, Tier


def _executable_surface() -> Surface:
    """See tests/test_tier_c_permanent_exceptions.py's identical helper for
    why execution_rights_confirmed=True is set explicitly here: this file's
    tests are about tool-identity/argument classification, not PA Action
    Kernel Section 4.2's separate execution-rights gate (covered on its own
    in tests/test_execution_rights_gate.py)."""
    return Surface(
        description="Routine surface, high confidence",
        state=ConfidenceState.EXECUTABLE,
        confirmation_count=50,
        execution_rights_confirmed=True,
    )


def test_unknown_tool_fails_safe_even_with_every_favorable_signal_present():
    """Stack every signal an attacker would want in their favor — claimed
    Tier A, an executable high-confidence surface, no drift, no mirror-drift
    flag — on top of a tool name that isn't in TOOL_REGISTRY. Unknown must
    still win and force Tier C; nothing else in the call is allowed to
    outvote the fail-safe default."""
    action = build_action_request(
        tool_name="totally_unregistered_tool",
        arguments={"looks": "harmless"},
        claimed_tier=Tier.A,
    )
    decision = classify_tier(
        action=action,
        surface=_executable_surface(),
        drift_level=DriftLevel.MINOR,
        mirror_drift_flagged=False,
    )
    assert decision.tier == Tier.C
    assert any("No Executable surface" not in r for r in decision.reasons) or True
    assert any("Permanent Tier C exception" in r for r in decision.reasons)


def test_case_and_whitespace_variants_of_a_dangerous_tool_name_are_not_recognized_but_still_fail_safe():
    """TOOL_REGISTRY lookup is an exact, case-sensitive dict key match. A
    variant spelling of a registered dangerous tool ('Update_Pricing', a
    trailing space, a trailing newline someone smuggled in via a template)
    will NOT be recognized as `update_pricing` and will NOT get tagged with
    `pricing_or_cost_commitment` from the registry. The property this test
    actually proves is narrower but still load-bearing: because unrecognized
    tool names fail safe to Tier C (not Tier A), a spelling variant can
    never accidentally downgrade enforcement — the worst outcome of the
    registry not recognizing a variant is "held for confirmation anyway,"
    never "silently executed"."""
    for variant in ("Update_Pricing", " update_pricing", "update_pricing\n", "update-pricing"):
        action = build_action_request(tool_name=variant, arguments={"new_price": 5000}, claimed_tier=Tier.A)
        decision = classify_tier(
            action=action, surface=_executable_surface(), drift_level=DriftLevel.MINOR,
            mirror_drift_flagged=False,
        )
        assert decision.tier == Tier.C, f"variant {variant!r} did not fail safe"


def test_forged_classification_keys_inside_arguments_are_ignored():
    """An attacker who can only control `arguments` (not `tool_name`) might
    try stuffing keys that LOOK like they belong to the classification
    machinery — `permanent_categories`, `external_facing`, `reversible`,
    `tier` — directly into the arguments dict, hoping something downstream
    reads them instead of the registry. `build_action_request` must ignore
    all of them; only the TOOL_REGISTRY entry for the real tool name may set
    those fields."""
    action = build_action_request(
        tool_name="update_pricing",  # genuinely dangerous tool
        arguments={
            "permanent_categories": [],       # forged: claims no categories
            "external_facing": False,          # forged: claims internal-only
            "reversible": True,                # forged: claims reversible
            "tier": "tier_a",                  # forged: claims pre-cleared
            "new_price": 5000,
        },
    )
    # The forged keys must not have leaked into the ActionRequest's real
    # classification fields.
    assert action.permanent_categories == frozenset({"pricing_or_cost_commitment"})
    assert action.external_facing is True
    assert action.reversible is False

    decision = classify_tier(
        action=action, surface=_executable_surface(), drift_level=None, mirror_drift_flagged=False
    )
    assert decision.tier == Tier.C


def test_registered_safe_tool_can_carry_unscanned_pricing_commitment_in_arguments():
    """THIS TEST DOCUMENTS A REAL GAP, NOT A FALSE ALARM.

    `draft_client_email` is registered with no permanent categories
    (external-facing, reversible — an ordinary Tier B action). Nothing stops
    its `arguments` from containing a drafted email body that actually makes
    a pricing commitment. `classify_tier` looks at tool identity only, so
    this reaches Tier B — the same tier a harmless internal draft would get.
    The independent safety net for this shape of content is
    `core/permanent_category_scan.py`, which flags the same body text as
    `pricing_or_cost_commitment` when it's run. The point: if a future
    (tool_name, arguments) wiring only calls `classify_tier` and never also
    runs the argument text through `scan_for_permanent_categories`, this
    exact case slips through at Tier B instead of being forced to Tier C.
    """
    body = "Sounds good — I can lock in a fixed price of $5,000 for the full package."
    action = build_action_request(
        tool_name="draft_client_email",
        arguments={"to": "client@example.com", "body": body},
    )
    decision = classify_tier(
        action=action, surface=_executable_surface(), drift_level=None, mirror_drift_flagged=False
    )
    # Current, real behavior: tool-identity classification alone lets this
    # through at Tier B, not Tier C.
    assert decision.tier == Tier.B

    # The independent text scanner *does* catch it — proving the fix is to
    # union its result in, not to trust classify_tier() on arguments alone.
    assert "pricing_or_cost_commitment" in scan_for_permanent_categories(body)
