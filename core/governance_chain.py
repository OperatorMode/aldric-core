"""
Governance Chain — ALDRIC Operational Stack (reference/router document).

Maps to 08_Governance_Chain.md. This is the top-level orchestrator: it does
not introduce new governance rules of its own, it enforces the load order
and mode-initialization rules the other seven documents already declared,
and it is the module main.py talks to.

Two load-order-sensitive facts from the source document, both enforced by
`verify_stack_load` below:

  * "Components must be registered in the order they are presented" (KSP-0
    Section, Load Rules) — order matters, and this function checks it.
  * Load order and precedence order are DIFFERENT axes. K1 has absolute
    precedence (see core.k1_safety.PRECEDENCE_ORDER) but loads second, after
    KSP-0. Do not conflate the two lists.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OperatingMode(str, Enum):
    ALDRIC_MODE = "aldric_mode"
    GOVERNANCE_STACK_MODE = "governance_stack_mode"


class InitializationState(str, Enum):
    OBSERVATION_MODE = "observation_mode"       # ALDRIC Mode default
    DSD_DISCOVERY_ACTIVE = "dsd_discovery_active"  # Governance Stack Mode default


# Load order (registration order), NOT precedence order. See module docstring.
LOAD_ORDER_ALDRIC_FULL_STACK: tuple[str, ...] = (
    "01_KSP-0_DSD",
    "02_K1_Safety_Kernel",
    "03_APEX_Supervisor",
    "04_Operator_Kernel_KSP-1",
    "05_PA_Action_Kernel",
    "06_Learning_Governance",
    "07_Operator_Profiles",
    "08_Governance_Chain",
)

LOAD_ORDER_GOVERNANCE_STACK_ONLY: tuple[str, ...] = (
    "01_KSP-0_DSD",
    "02_K1_Safety_Kernel",
    "03_APEX_Supervisor",
    "04_Operator_Kernel_KSP-1",
    "07_Operator_Profiles",
)

# Three invocation paths for Governance Stack Mode (Governance Chain doc).
GOVERNANCE_STACK_INVOCATION_PATHS: tuple[str, ...] = (
    "aldric_escalates_tier_c_hold",
    "operator_invokes_manually",
    "operator_feeds_ratified_output_back_as_governed_instruction_signal",
)


def load_order_for(mode: OperatingMode) -> tuple[str, ...]:
    return LOAD_ORDER_ALDRIC_FULL_STACK if mode == OperatingMode.ALDRIC_MODE else LOAD_ORDER_GOVERNANCE_STACK_ONLY


def initialization_state_for(mode: OperatingMode) -> InitializationState:
    """Governance Chain doc, 'Critical Initialisation Statement': ALDRIC
    Mode's correct initialization state is observation mode — the DSD does
    NOT fire on ALDRIC stack load. Governance Stack Mode fires the DSD
    Discovery Loop immediately and unconditionally per KSP-0."""
    if mode == OperatingMode.ALDRIC_MODE:
        return InitializationState.OBSERVATION_MODE
    return InitializationState.DSD_DISCOVERY_ACTIVE


@dataclass
class StackLoadReport:
    mode: OperatingMode
    component_results: list[tuple[str, str]]  # (component_name, "Accepted"|"Load failed — reason")
    stack_status: str  # "Complete" | "Incomplete — failed at Component N"
    initialisation_state: InitializationState | None

    def render(self) -> str:
        lines = ["STACK LOAD REPORT", ""]
        for idx, (name, result) in enumerate(self.component_results, start=1):
            lines.append(f"Component {idx} — {name}: {result}")
        lines.append("")
        lines.append(f"Stack status: {self.stack_status}")
        lines.append(f"Initialisation state: {self.initialisation_state.value if self.initialisation_state else 'N/A'}")
        return "\n".join(lines)


def verify_stack_load(loaded_components: list[str], mode: OperatingMode) -> StackLoadReport:
    """`loaded_components` must match the required load order for `mode`
    exactly, in sequence — position i of `loaded_components` is checked
    against position i of the required order. The first mismatch fails that
    component AND every component after it (KSP-0: "If any component fails,
    state which downstream components are also unloaded and why"), because
    each component depends on all components before it."""
    required = load_order_for(mode)
    results: list[tuple[str, str]] = []
    failed_at: int | None = None

    for idx, expected in enumerate(required):
        if failed_at is not None:
            results.append((expected, f"Load failed — unloaded: depends on Component {failed_at} which failed"))
            continue
        actual = loaded_components[idx] if idx < len(loaded_components) else None
        if actual == expected:
            results.append((expected, "Accepted"))
        else:
            failed_at = idx + 1
            got = actual if actual is not None else "(missing)"
            results.append((expected, f"Load failed — expected '{expected}' at position {idx + 1}, got '{got}'"))

    status = "Complete" if failed_at is None else f"Incomplete — failed at Component {failed_at}"
    init_state = initialization_state_for(mode) if failed_at is None else None
    return StackLoadReport(mode=mode, component_results=results, stack_status=status, initialisation_state=init_state)
