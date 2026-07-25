from ops.live_e2e_probe import (
    EXPECTED_MILESTONE_COUNT,
    ProbeState,
    is_terminal,
)


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


def test_fixed_probe_requires_the_requested_single_milestone():
    state = _state("done", ["flow_done"])
    state.latest_status = {
        "list": [
            {"ms": "MS-1", "status": "done"},
            {"ms": "MS-2", "status": "done"},
        ]
    }
    report = state.report("terminal")
    check = next(
        row for row in report["assertions"]
        if row["name"] == "requested_milestone_count_preserved"
    )
    assert EXPECTED_MILESTONE_COUNT == 1
    assert check["ok"] is False
    assert check["detail"]["actual"] == 2
