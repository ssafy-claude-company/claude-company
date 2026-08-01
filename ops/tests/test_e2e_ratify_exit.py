"""막힘의 출구는 파킹이 아니라 회의다(U-442 실측: 재개 → 즉시 같은 실패 → 파킹 반복)."""
import inspect

from system import sys_core


def _src():
    return inspect.getsource(sys_core.Sys)


def test_비준이_없어_못_열면_회의를_직접_연다():
    src = _src()
    assert "e2e_ratify_meeting_opened" in src        # 글만 남기지 않고 실제로 연다
    i = src.index("e2e_ratify_meeting_opened")
    head = src[max(0, i - 1800):i]
    assert "_need_ratify" in head and "_stage_meet" in head


def test_유예는_시간으로_쥔다():
    """세그먼트 수로 세면 같은 순회 연속 호출로 순식간에 소진된다(실측: 같은 초에 2회)."""
    src = _src()
    assert "_e2e_ratify_until" in src and "time.monotonic() + 900" in src


def test_두_번까지만_열고_그다음엔_사람을_부른다():
    src = _src()
    assert "_e2e_ratify_tries" in src and "_tries < 2" in src
    i = src.index("e2e_boundary_open_failed")
    seg = src[max(0, i - 400):i + 200]
    assert 'flow._stage_stuck = "e2e-open"' in seg


def test_회의에_실제로_있는_검증기를_준다():
    body = inspect.getsource(sys_core._existing_verifier_files)
    assert "verify" in body and "os.listdir" in body.replace("_os.", "os.")
