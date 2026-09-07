"""
Tool Registry Loader — the declarative half of core.pa_action_kernel's
TOOL_REGISTRY.

Investor/compliance-facing question this module answers: "how do we let a
compliance team manage which tools exist and what they're tagged with,
without them touching Python source?" The answer this codebase gives is a
deliberate split, not a blanket move to config (see CLAUDE.md Section 10):

  - WHAT CAN move to a file a non-engineer edits: which tools exist, and
    for each one, whether it's external_facing/reversible and which
    Permanent Tier C categories it touches. That's exactly what
    config/tool_registry.yaml holds, and this module's job is to load and
    validate it.

  - WHAT CANNOT move to a file, ever: the six-category set itself
    (models.schemas.PERMANENT_TIER_C_CATEGORIES) and what happens once a
    tool is tagged with one of them (core.pa_action_kernel.classify_tier
    forcing Tier C unconditionally). Those stay Python constants and
    Python control flow with no config-driven or API-reachable mutation
    path — see CLAUDE.md Section 2. This is the load-bearing distinction:
    a compliance admin editing this file can misclassify a tool (mark
    something external-facing that shouldn't be, say) and that is a real
    risk to manage with review/versioning discipline on the file, same as
    any config change — but they structurally cannot make a permanent-
    category tool stop being Tier C, and they cannot invent a new
    "permanent" category that bypasses the six the protocol actually
    defines. This module enforces that second guarantee at load time: any
    permanent_categories value in the file that isn't already one of the
    six is a hard load error, not a warning and not a silent drop.

Fails closed throughout: a missing file, a malformed entry, or an
unrecognized category tag all raise ToolRegistryError rather than falling
back to a permissive default or partially loading. A tool registry the
process can't fully validate is a tool registry the process shouldn't run
with — same fail-safe posture as an unknown tool_name at classification
time (core.pa_action_kernel.build_action_request).
"""
from __future__ import annotations

import os
from typing import Any

import yaml

from models.schemas import PERMANENT_TIER_C_CATEGORIES

DEFAULT_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "tool_registry.yaml"
)


class ToolRegistryError(ValueError):
    """Raised for anything wrong with a tool registry file: missing file,
    malformed YAML, a missing/mistyped field, or — the one this module
    exists to guarantee — a permanent_categories entry naming something
    outside the closed six-category set. Always fails closed."""


def _validate_entry(path: str, tool_name: str, entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ToolRegistryError(f"{path!r}: entry for tool {tool_name!r} must be a mapping")

    categories = entry.get("permanent_categories", [])
    if not isinstance(categories, list) or any(not isinstance(c, str) for c in categories):
        raise ToolRegistryError(
            f"{path!r}: {tool_name!r}.permanent_categories must be a list of strings"
        )
    unknown = set(categories) - PERMANENT_TIER_C_CATEGORIES
    if unknown:
        raise ToolRegistryError(
            f"{path!r}: tool {tool_name!r} tags unrecognized permanent categor"
            f"{'y' if len(unknown) == 1 else 'ies'} {sorted(unknown)} — not in the fixed "
            f"six-category set (models.schemas.PERMANENT_TIER_C_CATEGORIES: "
            f"{sorted(PERMANENT_TIER_C_CATEGORIES)}). This file can only reference an EXISTING "
            f"category; it can never define a new one (CLAUDE.md Section 2)."
        )

    external_facing = entry.get("external_facing")
    reversible = entry.get("reversible")
    if not isinstance(external_facing, bool):
        raise ToolRegistryError(
            f"{path!r}: {tool_name!r}.external_facing must be true or false, got {external_facing!r}"
        )
    if not isinstance(reversible, bool):
        raise ToolRegistryError(
            f"{path!r}: {tool_name!r}.reversible must be true or false, got {reversible!r}"
        )

    return {
        "permanent_categories": frozenset(categories),
        "external_facing": external_facing,
        "reversible": reversible,
    }


def load_tool_registry(path: str = DEFAULT_REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    """Parse and validate `path`, returning the exact shape
    core.pa_action_kernel.TOOL_REGISTRY has always had in code: a dict of
    tool_name -> {"permanent_categories": frozenset[str], "external_facing": bool, "reversible": bool}.

    Raises ToolRegistryError (never returns a partial result) for: a
    missing file, a top-level shape that isn't {"tools": {...}}, an empty
    tools mapping, a non-mapping entry, a permanent_categories value that
    isn't a list of strings drawn from the closed six-category set, or an
    external_facing/reversible value that isn't a real boolean."""
    if not os.path.exists(path):
        raise ToolRegistryError(f"Tool registry file not found: {path!r}")

    with open(path, "r", encoding="utf-8") as f:
        try:
            raw = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ToolRegistryError(f"{path!r}: invalid YAML — {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("tools"), dict) or not raw.get("tools"):
        raise ToolRegistryError(f"{path!r}: top-level 'tools' key must be a non-empty mapping")

    return {
        tool_name: _validate_entry(path, tool_name, entry)
        for tool_name, entry in raw["tools"].items()
    }
