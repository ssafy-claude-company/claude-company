"""응찰이 '패스'로만 돌아오던 진짜 이유 — 무엇에 대해 묻는지 말하지 않았다(U-442 실측).

한 QA는 82번 질문에 73번 연속 [패스]했다. 문구는 "진행 중인 작업 상황에 지금 보탤 게 있나"였는데
그 상황이 무엇인지는 주지 않았다 — 봇은 수십 분 전 자기 세션의 기억으로 판단할 수밖에 없었다.
"""
import inspect

from system import sys_core


def _src():
    return inspect.getsource(sys_core.Sys._floor_segment_open)


def test_질문에_지금_벌어지는_일이_담긴다():
    src = _src()
    assert "_now_lines" in src
    assert "지금 작업 중" in src and "최근 활동" in src
    assert "지금 판에서 벌어지는 일입니다" in src


def test_직군_관점을_묻는다():
    src = _src()
    assert "당신 직군에서 지금 말하지 않으면" in src


def test_백오프는_보조에_그친다():
    """원인을 고쳤으므로 간격은 완만하게 — 지수로 벌리면 할 말이 생긴 사람을 오래 못 부른다."""
    src = _src()
    assert "gap = 2 if st >= 5 else 1" in src
    assert "2 ** (st // 3)" not in src
