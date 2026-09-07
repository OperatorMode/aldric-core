"""
Tests for core.tool_registry_loader — the piece that answers "can a
compliance team manage the tool registry without touching source code?"
with a schema-validated YAML file, while proving the closed six-category
set (models.schemas.PERMANENT_TIER_C_CATEGORIES) stays unreachable from
that file. See CLAUDE.md Section 10.
"""
import pytest

from core import pa_action_kernel
from core.tool_registry_loader import DEFAULT_REGISTRY_PATH, ToolRegistryError, load_tool_registry
from models.schemas import Tier


def _write_registry(tmp_path, tools_yaml_body: str) -> str:
    path = tmp_path / "tool_registry.yaml"
    path.write_text(f"tools:\n{tools_yaml_body}\n")
    return str(path)


def test_loads_the_real_checked_in_registry_file():
    registry = load_tool_registry(DEFAULT_REGISTRY_PATH)
    assert registry["update_pricing"]["permanent_categories"] == frozenset({"pricing_or_cost_commitment"})
    assert registry["update_pricing"]["external_facing"] is True
    assert registry["update_pricing"]["reversible"] is False
    assert registry["log_internal_note"]["permanent_categories"] == frozenset()
    assert registry["sign_contract"]["permanent_categories"] == frozenset(
        {"contractual_terms_or_obligation", "binding_obligation"}
    )


def test_pa_action_kernel_module_level_registry_is_loaded_from_the_file_not_a_literal():
    # If TOOL_REGISTRY were still a Python dict literal, this would just be
    # trivially true; the point is that it is now produced by the loader.
    assert pa_action_kernel.TOOL_REGISTRY == load_tool_registry(DEFAULT_REGISTRY_PATH)


def test_missing_file_fails_closed(tmp_path):
    with pytest.raises(ToolRegistryError, match="not found"):
        load_tool_registry(str(tmp_path / "does_not_exist.yaml"))


def test_empty_tools_mapping_fails_closed(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("tools: {}\n")
    with pytest.raises(ToolRegistryError, match="non-empty mapping"):
        load_tool_registry(str(path))


def test_missing_top_level_tools_key_fails_closed(tmp_path):
    path = tmp_path / "no_tools_key.yaml"
    path.write_text("something_else: {}\n")
    with pytest.raises(ToolRegistryError, match="non-empty mapping"):
        load_tool_registry(str(path))


def test_invalid_yaml_fails_closed(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("tools:\n  broken_tool: [this is not: valid: yaml\n")
    with pytest.raises(ToolRegistryError, match="invalid YAML"):
        load_tool_registry(str(path))


def test_an_unrecognized_permanent_category_is_rejected_not_silently_dropped(tmp_path):
    """The core guarantee this module exists for: a compliance-editable
    file cannot invent a new Permanent Tier C category, or slip a made-up
    'safe' one past validation."""
    path = _write_registry(tmp_path, """\
  rogue_tool:
    permanent_categories: [made_up_category_that_does_not_exist]
    external_facing: true
    reversible: false
""")
    with pytest.raises(ToolRegistryError, match="unrecognized permanent categor"):
        load_tool_registry(path)


def test_a_real_category_name_is_accepted(tmp_path):
    path = _write_registry(tmp_path, """\
  new_tool:
    permanent_categories: [legal_matter]
    external_facing: true
    reversible: false
""")
    registry = load_tool_registry(path)
    assert registry["new_tool"]["permanent_categories"] == frozenset({"legal_matter"})


@pytest.mark.parametrize("bad_field,bad_value", [
    ("external_facing", '"yes"'),
    ("reversible", "1"),
])
def test_non_boolean_fields_are_rejected(tmp_path, bad_field, bad_value):
    other_field = "reversible" if bad_field == "external_facing" else "external_facing"
    path = _write_registry(tmp_path, f"""\
  bad_tool:
    permanent_categories: []
    {bad_field}: {bad_value}
    {other_field}: true
""")
    with pytest.raises(ToolRegistryError, match=f"{bad_field}.*must be true or false"):
        load_tool_registry(path)


def test_permanent_categories_must_be_a_list_of_strings(tmp_path):
    path = _write_registry(tmp_path, """\
  bad_tool:
    permanent_categories: "pricing_or_cost_commitment"
    external_facing: true
    reversible: true
""")
    with pytest.raises(ToolRegistryError, match="must be a list of strings"):
        load_tool_registry(path)


def test_editing_the_config_file_changes_classify_tier_output_with_no_python_change(tmp_path):
    """The actual investor-facing proof: reclassifying a tool's tier is a
    one-line change to the YAML file, picked up by the existing,
    unmodified classify_tier() — no source edit, no redeploy of core/."""
    from core.pa_action_kernel import build_action_request, classify_tier
    from models.schemas import ConfidenceState, DriftLevel, Surface

    surface = Surface(
        description="Routine surface, high confidence",
        state=ConfidenceState.EXECUTABLE,
        execution_rights_confirmed=True,
    )

    def _tier_for(tools_yaml_body: str) -> Tier:
        path = _write_registry(tmp_path, tools_yaml_body)
        pa_action_kernel.reload_tool_registry(path)
        action = build_action_request(tool_name="schedule_lunch", arguments={})
        decision = classify_tier(
            action=action, surface=surface, drift_level=DriftLevel.MINOR, mirror_drift_flagged=False,
        )
        return decision.tier

    try:
        # Internal, reversible, no permanent tag -> Tier A.
        assert _tier_for("""\
  schedule_lunch:
    permanent_categories: []
    external_facing: false
    reversible: true
""") == Tier.A

        # Same tool, now marked external-facing in the file alone -> Tier B.
        assert _tier_for("""\
  schedule_lunch:
    permanent_categories: []
    external_facing: true
    reversible: true
""") == Tier.B

        # Same tool, now tagged with an existing permanent category ->
        # unconditionally Tier C, regardless of external_facing/reversible.
        assert _tier_for("""\
  schedule_lunch:
    permanent_categories: [legal_matter]
    external_facing: false
    reversible: true
""") == Tier.C
    finally:
        # Restore the real registry so this test doesn't leak state into
        # whatever test runs after it in the same process.
        pa_action_kernel.reload_tool_registry()


def test_reload_restores_the_default_registry():
    pa_action_kernel.reload_tool_registry()
    assert pa_action_kernel.TOOL_REGISTRY == load_tool_registry(DEFAULT_REGISTRY_PATH)
