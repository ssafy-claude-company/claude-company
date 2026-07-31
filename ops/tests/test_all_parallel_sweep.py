"""전원 병렬 계약 — 남아 있던 1활성 관문이 전부 사라졌는지 소스로 확인한다(전수)."""
import inspect

from system import guide_tools, sys_core
from system.rule import backlog as bl
from system.rule import milestone as ms
from system import sys_prompt


def test_인계에_1활성_관문이_없다():
    src = inspect.getsource(sys_core.Sys._backlog_handoff)
    assert "backlog_parallel_width()" in src
    assert "같은 작업 영역" not in src and "write_scopes_conflict" not in src


def test_릴레이_pick에_1활성_거부가_없다():
    src = inspect.getsource(bl.BacklogRelay.pick)
    assert "동시 진행 상한" in src
    assert "같은 영역을 작업 중" not in src


def test_도구층에_마일스톤_전체_1활성_거부가_없다():
    src = inspect.getsource(guide_tools)
    assert "마일스톤 전체 순차 1활성" not in src


def test_선점킥이_활성이_있다고_침묵하지_않는다():
    src = inspect.getsource(ms.claim_kick_target)
    assert 'if any(b.status == "in_progress"' not in src
    assert "backlog_parallel_width" in src


def test_봇에게_주는_규칙이_전원_병렬을_말한다():
    src = inspect.getsource(sys_prompt)
    assert "백로그 작업은 각자 자기 것을 동시에" in src
    assert "회의 발언권도 백로그 작업도 **한 번에 한 명만**" not in src


def test_회의_발언권은_여전히_한_명씩이다():
    """말은 베턴, 일은 각자 — 회의까지 동시에 열면 대화가 아니라 소음이 된다."""
    src = inspect.getsource(sys_prompt)
    assert "회의 발언권은 한 번에 한 명" in src
