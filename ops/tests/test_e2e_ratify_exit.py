"""막힘의 출구는 파킹이 아니라 **정식 단계 회의**다(U-442 실측).

임시 회의를 열어 봤지만 결론이 어디에도 착지하지 않았다(ms_consensus_empty) — GOAL 실증 명령을
바꾸는 자리는 GOAL@ 마커를 붙이는 마일스톤 회의뿐이다. e2e 경계는 길만 비켜 준다.
"""
import inspect

from system import sys_core


def _src():
    return inspect.getsource(sys_core.Sys)


def test_비준이_없으면_경계가_길을_비킨다():
    src = _src()
    i = src.index("e2e_ratify_meeting_requested")
    seg = src[max(0, i - 900):i + 300]
    assert "_need_ratify" in seg
    assert "return False" in seg              # 처리하지 않음 — 단계 기계가 이어받는다
    assert "_stage_meet" not in seg           # 임시 회의는 폐기


def test_유예는_시간으로_쥔다():
    src = _src()
    assert "_e2e_ratify_until" in src and "time.monotonic() + 900" in src


def test_두_번까지만_비키고_그다음엔_사람을_부른다():
    src = _src()
    assert "_e2e_ratify_tries" in src and "_tries < 2" in src
    i = src.index("e2e_boundary_open_failed")
    seg = src[max(0, i - 400):i + 200]
    assert 'flow._stage_stuck = "e2e-open"' in seg
