from ops.live_e2e_probe import ProbeState, is_terminal


def _state(request_state, events):
    state = ProbeState(
        execute=True, pid="U-TEST", request_id=41, trace_id="t-41")
    state.latest_messages = {
        "messages": [{
            "kind": "user_request",
            "request_id": 41,
            "request_state": request_state,
        }]
    }
    state.trace_events = [{"event": event} for event in events]
    return state


def test_probe_accepts_success_only_after_flow_done():
    assert not is_terminal(_state("done", []))
    assert is_terminal(_state("done", ["flow_done"]))


def test_probe_seals_fail_closed_stop_without_waiting_for_flow_done():
    assert is_terminal(
        _state("stopped", ["flow_no_deliverable", "false_complete_blocked"]))
    assert not is_terminal(_state("stopped", ["flow_no_deliverable"]))
