"""무엇을 만들지는 만들 사람들이 모인 뒤 정한다 (2026-08-06, 사용자: '기획 회의가 지금 그냥 1명이서
매우 간단한 주제를 뱉어버렸어 … 이게 최대 접근 가능 범위의 최대 산출물이야?').

[실측 U-516] goal 회의 참석 **1명**(게임 기획 혼자), 표결 찬성 1·반대 0으로 Task 전체가 확정됐다.
QA·배포/인프라·사운드 디자이너·게임 비주얼 디자이너·게임 클라이언트 엔지니어 **다섯 직군은 그
뒤에** 채용됐다. 한 사람이 30초에 떠올릴 수 있는 범위로 목표가 굳는 구조다 — 나온 결과가
'3레인 좌우 이동·60초'다. 로그라이크·성장·증강 같은 두께는 여러 도메인이 같은 방에 있을 때 나온다.

goal_quorum_hold(2026-08-01)는 심의 3인을 요구하지만 **로스터가 3 미만이면 건너뛴다** — 그 예외가
이 자리를 만들었다. 목표 단계에서는 로스터가 정족수에 닿을 때까지 회의를 열지 않는다(채용이 먼저).
다른 단계는 종전대로 — 사람이 하나라도 있으면 연다.
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import GOAL_QUORUM_MIN


class _Cur:
    def __init__(self, team):
        self.team = list(team)
        self.task_id = "T-1"
        self.acceptance = ""
        self.status = types.SimpleNamespace(goal="")


class _Flow:
    def __init__(self, team, goal=""):
        self.current = _Cur(team)
        self.current.status.goal = goal
        self.anchor = 11
        self.user_channel = 1
        self.milestones = []
        self.roadmap = []
        self.backlog_relays = {}
        self.log = None
        self.workspace = ""
        self.bot_info = {m: "게임 기획" for m in team}

    def _info(self, oid):
        return self.bot_info.get(int(oid), "")


def _sys():
    from system.sys_core import Sys
    o = object.__new__(Sys)
    o._logged = []
    o._log = lambda e, **f: o._logged.append((e, f))
    return o


def test_목표_단계는_정족수_전에_회의를_열지_않는다():
    o = _sys()
    f = _Flow([11, 12])                       # 앵커 + 1명 = 2 < 3
    assert o._stage_roster_ready(f) is False
    assert any(e == "goal_meeting_deferred_thin_roster" for e, _ in o._logged), o._logged


def test_정족수를_채우면_목표_회의가_열린다():
    o = _sys()
    f = _Flow([11, 12, 13])                   # 3명
    assert o._stage_roster_ready(f) is True


def test_목표가_이미_정해진_뒤_단계는_종전대로():
    """goal이 끝난 판(다음 단계)은 한 명만 있어도 회의를 연다 — 이 관문은 목표 단계 전용."""
    o = _sys()
    f = _Flow([11, 12], goal="이미 정한 목표")
    assert o._stage_roster_ready(f) is True


def test_정족수_상수는_3이다():
    assert GOAL_QUORUM_MIN == 3
