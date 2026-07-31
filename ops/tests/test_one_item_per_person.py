"""한 사람은 한 번에 하나(U-442 실측: 같은 봇이 ST-1/B2와 ST-2/B1을 동시에 들고 있었다)."""
import inspect

from system import guide_tools, sys_core
from system.rule import backlog as bl
from system.rule import milestone as ms


def test_사람별_점유를_묻는_함수가_있다():
    src = inspect.getsource(bl.worker_busy_with)
    assert "한 사람은 한 번에 하나" in src


def test_선점킥이_이미_일하는_사람을_건너뛴다():
    src = inspect.getsource(ms.claim_kick_target)
    assert "worker_busy_with" in src and "not _busy(flow, owner)" in src


def test_다음_집기가_바쁜_사람을_건너뛴다():
    """(2026-07-31: 인계가 폐지돼 이 계약은 claim_kick_target 한 곳에서 지켜진다.)"""
    src = inspect.getsource(ms.claim_kick_target)
    assert "worker_busy_with" in src and "not _busy(flow, owner)" in src


def test_도구가_두_개째_선점을_막는다():
    src = inspect.getsource(guide_tools)
    assert "당신은 이미 백로그" in src and "동시에 두 개를 들면" in src


def test_사람이_다르면_동시에_간다():
    """막는 것은 '한 사람의 두 일감'이지 '두 사람의 두 일감'이 아니다."""
    src = inspect.getsource(bl.BacklogRelay.pick)
    assert "같은 영역을 작업 중" not in src        # 영역 기반 직렬화는 폐기된 채로
    assert "동시 진행 상한" in src
