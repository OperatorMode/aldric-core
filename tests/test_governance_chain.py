from core.governance_chain import (
    OperatingMode,
    InitializationState,
    LOAD_ORDER_ALDRIC_FULL_STACK,
    initialization_state_for,
    verify_stack_load,
)


def test_aldric_mode_initializes_to_observation_not_dsd_discovery():
    """Governance Chain doc: 'Do not fire the DSD Discovery Loop on ALDRIC
    stack load completion.'"""
    assert initialization_state_for(OperatingMode.ALDRIC_MODE) == InitializationState.OBSERVATION_MODE


def test_governance_stack_mode_initializes_to_dsd_discovery():
    assert (
        initialization_state_for(OperatingMode.GOVERNANCE_STACK_MODE)
        == InitializationState.DSD_DISCOVERY_ACTIVE
    )


def test_correct_full_load_order_reports_complete():
    report = verify_stack_load(list(LOAD_ORDER_ALDRIC_FULL_STACK), OperatingMode.ALDRIC_MODE)
    assert report.stack_status == "Complete"
    assert all(result == "Accepted" for _name, result in report.component_results)


def test_out_of_order_load_fails_at_the_swapped_component_and_cascades():
    """Components must be registered in the order they are presented; a
    swap must fail at that position and mark everything after it unloaded,
    not silently continue."""
    scrambled = list(LOAD_ORDER_ALDRIC_FULL_STACK)
    scrambled[1], scrambled[2] = scrambled[2], scrambled[1]  # swap K1 and APEX
    report = verify_stack_load(scrambled, OperatingMode.ALDRIC_MODE)
    assert report.stack_status == "Incomplete — failed at Component 2"
    # Component 1 still accepted; components 2 onward are failed/unloaded.
    assert report.component_results[0][1] == "Accepted"
    assert "Load failed" in report.component_results[1][1]
    assert "unloaded" in report.component_results[-1][1] or "Load failed" in report.component_results[-1][1]


def test_missing_component_fails_cleanly():
    partial = list(LOAD_ORDER_ALDRIC_FULL_STACK[:4])  # first 4 match, then nothing supplied
    report = verify_stack_load(partial, OperatingMode.ALDRIC_MODE)
    assert report.stack_status == "Incomplete — failed at Component 5"
    assert report.component_results[0][1] == "Accepted"
    assert report.component_results[3][1] == "Accepted"
    assert "(missing)" in report.component_results[4][1]
    assert report.initialisation_state is None  # no init state granted on an incomplete load
