"""다른 사람의 일감은 동시에, 같은 사람은 순서대로 — 그리고 인계 없이 자기가 집는다.

(2026-07-31: 배분권 인계(_backlog_handoff)가 폐지돼 계약의 주소가 _start_next_in_order로 옮겨졌다.)
"""
import inspect

from system import sys_core
from system.rule import backlog as bl
from system.rule import milestone as ms


def test_인계는_사라졌다():
    assert not hasattr(sys_core.Sys, "_backlog_handoff")
    assert "_backlog_handoff" not in inspect.getsource(sys_core.Sys._finish_backlog_turn)


def test_끝낸_사람이_등록_순서대로_다음을_집는다():
    src = inspect.getsource(sys_core.Sys._start_next_in_order)
    assert "claim_kick_target" in src            # 고르는 규칙은 선점킥과 하나
    assert "backlog_next_in_order" in src        # 순서 표식은 그대로 남는다


def test_영역을_이유로_기다리게_하지_않는다():
    src = inspect.getsource(bl.BacklogRelay.pick)
    assert "같은 영역을 작업 중" not in src
    assert "동시 진행 상한" in src                # 남는 제한은 상한뿐


def test_선점킥이_상한을_존중한다():
    src = inspect.getsource(ms.claim_kick_target)
    assert "backlog_parallel_width" in src
