"""막힘의 출구는 파킹이 아니라 회의다(U-442 실측: 재개 → 즉시 같은 실패 → 파킹 반복)."""
import inspect

from system import sys_core


def _src():
    return inspect.getsource(sys_core.Sys)


def test_비준이_없어_못_열면_회의를_연다():
    src = _src()
    assert "e2e_ratify_meeting_requested" in src
    i = src.index("e2e_ratify_meeting_requested")
    head = src[max(0, i - 1200):i]
    assert "_need_ratify" in head and "flow._stage_stuck = None" in head


def test_두_번까지만_열고_그다음엔_사람을_부른다():
    src = _src()
    assert "_e2e_ratify_tries" in src and "_tries < 2" in src
    i = src.index("e2e_boundary_open_failed")
    seg = src[max(0, i - 400):i + 200]
    assert 'flow._stage_stuck = "e2e-open"' in seg      # 재비준으로도 안 되면 종전대로 파킹


def test_요청_뒤엔_경계를_잠시_쉰다():
    """요청만 하고 곧바로 다시 열려 하면 회의가 열리기도 전에 횟수를 태운다(실측: 같은 초에 2회)."""
    src = _src()
    assert "_e2e_ratify_skip" in src
    i = src.index("e2e_ratify_meeting_requested")
    assert "flow._e2e_ratify_skip = 4" in src[max(0, i - 300):i]
