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


def test_등재자가_바쁘면_손_빈_적임자가_집는다(monkeypatch):
    """[2026-08-03, 실측 U-478] 1차 선정은 일감을 등재자에게만 준다(자기 등재 원칙). 그런데
    러너가 검증 보고의 조건 줄마다 백로그를 소급 등재하므로, 많이 보고한 사람일수록 자기만 집을 수
    있는 일감이 쌓인다. ST-7의 미배정 24건이 전부 제출자 5명 것이었고 그 5명이 모두 다른 일을 들고
    있어, 판에 42명이 있는데도 18시간 동안 아무도 집지 못했다. 단위는 모든 백로그가 종결돼야 닫히니
    판이 완주할 수 없다. 등재자가 손이 차 있으면 손 빈 적임자에게 넘어가야 한다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.flow import Flow
    from system.rule.backlog import BacklogRelay
    from system.rule.milestone import Milestone, SubTask, claim_kick_target

    class _G:
        async def post(self, *a, **k):
            return None

    flow = Flow(_G(), channel_id=501, guild_id=1, leader_id=11,
                bot_info={11: "리더", 12: "구현", 22: "QA", 33: "배포"})
    milestone = Milestone("MS-Y", "판정 계약", [])
    st = SubTask("MS-Y/ST-1", "구현", [])
    milestone.subtasks = [st]
    flow.milestones = [milestone]

    relay = BacklogRelay(st.st_id)
    held = relay.submit(12, "먼저 든 일", force=True)
    waiting = relay.submit(12, "같은 사람이 등재한 다음 일", force=True)
    flow.backlog_relays = {st.st_id: relay}
    relay.pick(12, held.backlog_id, 12)          # 등재자 12는 손이 찼다

    t = claim_kick_target(flow)
    assert t is not None, "등재자가 바쁘다고 남은 일감이 아무에게도 안 간다"
    who, b, st_id = t
    assert b.backlog_id == waiting.backlog_id and st_id == st.st_id
    assert who != 12, "손이 찬 등재자에게 두 번째 일감을 주면 '한 사람은 한 번에 하나'가 깨진다"
    assert who in (11, 22, 33)
