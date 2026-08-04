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


def test_등재자가_바쁘면_그_일감은_기다린다(monkeypatch):
    """[등재자=담당, 불변(2026-08-04, 사용자: '백로그는 백로그 등록자가 담당하고 바뀔 수 없거든')]
    08-03의 '손 빈 적임자에게 넘긴다' 2차 패스는 정본 위반이라 회수됐다. 등재자가 손이 차 있으면
    그 일감은 그 사람 차례까지 기다리고, 구조 선정은 손 빈 **다른 등재자의** 일감을 세운다 —
    병렬은 담당 이관이 아니라 등재자들이 각자 자기 일감을 동시에 굴려서 온다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.flow import Flow
    from system.rule.backlog import BacklogRelay
    from system.rule.milestone import claim_kick_target

    class _G:  # 최소 guide
        pass
    flow = Flow(_G(), channel_id=1, guild_id=1, leader_id=11, bot_info={11: "프론트", 12: "백엔드"})
    import types
    ms = types.SimpleNamespace(status="open", subtasks=[types.SimpleNamespace(st_id="ST-1", status="open")])
    flow.milestones = [ms]
    r = BacklogRelay("ST-1")
    r.submit(11, "프론트 화면 붙이기", force=True)     # 등재자 11
    r.submit(12, "백엔드 API 붙이기", force=True)      # 등재자 12
    flow.backlog_relays = {"ST-1": r}
    # 11이 이미 손에 든 상태(다른 일감 in_progress)
    r.pick(11, r.backlogs[0].backlog_id, 11)
    t = claim_kick_target(flow)
    assert t is not None
    who, b, st = t
    assert who == 12 and b.submitter == 12   # 손 빈 다른 등재자의 자기 일감 — 이관 없음
