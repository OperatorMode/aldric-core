from core.ksp1_operator_kernel import LoopManager
from models.schemas import LoopState


def test_single_active_loop_rule_auto_parks_previous():
    lm = LoopManager()
    lm.open_loop("client_a_renewal")
    assert lm.state_of("client_a_renewal") == LoopState.ACTIVE

    lm.open_loop("client_b_onboarding")
    assert lm.state_of("client_b_onboarding") == LoopState.ACTIVE
    assert lm.state_of("client_a_renewal") == LoopState.PARKED


def test_tier_c_hold_isolates_only_its_own_thread():
    lm = LoopManager()
    lm.open_loop("thread_1")
    lm.reopen_loop("thread_2")  # thread_1 auto-parked, thread_2 active
    # Simulate a Tier C hold on thread_2 only.
    lm.suspend_loop("thread_2", reason="Tier C hold: permanent exception")
    assert lm.state_of("thread_2") == LoopState.SUSPENDED
    # thread_1 is untouched by the hold on thread_2 (Component 6, Async
    # Thread Isolation) — its state is whatever it already was, not altered
    # by the unrelated suspension.
    assert lm.state_of("thread_1") == LoopState.PARKED


def test_close_loop():
    lm = LoopManager()
    lm.open_loop("t1")
    lm.close_loop("t1")
    assert lm.state_of("t1") == LoopState.CLOSED
