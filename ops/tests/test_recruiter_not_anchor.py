"""채용은 사회를 보지 않는다 (2026-08-06, 사용자: '이상하게 회의가 열리고 자기가 회의 표결을 열고
반대를 누른건가?').

[실측 U-520] 세 명이 말한 것처럼 보이지만 판의 사람은 **한 명**이었다.

    0                  = SYS
    정하준 role=채용    = 앵커 — goal·criteria·milestone 회의를 전부 열고 닫음
    임현우 role=게임 클라이언트 엔지니어 = 유일한 실무자

08-04 계약('채용 봇은 Organt가 아니고 시스템적인 존재야')은 채용을 팀·회의·표결·기여 관문에서
뺐지만 **앵커 자리에서는 빼지 않았다**. 새 요청을 받은 봇이 그대로 앵커가 되는데 그 자리에 채용이
있었다. 결과는 두 겹이었다:

  ① 정족수 산술이 부풀었다 — `len(roster) + 1`의 +1은 '앵커 자신'을 세는 자리인데 그 앵커가
     사람이 아니었다. 실무자 1명 + 채용 앵커 = 2로 계산돼 3인 관문을 통과했고, goal 회의가
     한 명으로 열렸다(로그: goal_quorum_skipped roster=1 voters=1).
  ② 그 한 명이 초안·발언·표결을 혼자 했다 — 자기 초안에 반대(찬성 0·반대 1)를 찍고, 고친 뒤
     자기가 찬성(찬성 1·반대 0)했다. 화면에는 '확정 표결' 블록 두 개가 연달아 섰다.

앵커가 채용이고 판에 실무자가 있으면 사회를 그 사람에게 넘긴다. 넘길 사람이 없으면 그대로 둔다 —
이 자리는 채용을 벌하는 곳이 아니라 사람이 사회를 보게 하는 곳이고, 사람이 없으면 정족수 관문이
이미 회의를 막는다.
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])


class _Comm:
    def __init__(self, ok=True):
        self.ok = ok
        self.rotated = []

    def rotate_origin_holder(self, who):
        self.rotated.append(int(who))
        return self.ok


class _Cur:
    def __init__(self, team):
        self.team = list(team)
        self.task_id = "T-1"
        self.acceptance = ""
        self.status = types.SimpleNamespace(goal="")


class _Flow:
    def __init__(self, info, team, anchor, rotate_ok=True):
        self.bot_info = dict(info)
        self.current = _Cur(team)
        self.anchor = anchor
        self.comm = _Comm(rotate_ok)
        self.user_channel = 1
        self.milestones = []
        self.roadmap = []
        self.backlog_relays = {}
        self.log = None
        self.workspace = ""

    def _info(self, oid):
        return self.bot_info.get(int(oid), "")


def _sys():
    from system.sys_core import Sys
    o = object.__new__(Sys)
    o._logged = []
    o._log = lambda e, **f: o._logged.append((e, f))
    return o


_RECRUIT = {10: "채용", 11: "게임 클라이언트 엔지니어", 12: "게임 기획"}


def test_채용_앵커는_실무자에게_사회를_넘긴다():
    o = _sys()
    f = _Flow(_RECRUIT, [10, 11], anchor=10)
    assert o._ensure_working_anchor(f) is True
    assert f.anchor == 11
    assert f.comm.rotated == [11]
    assert any(e == "anchor_rotated" for e, _ in o._logged), o._logged


def test_사람이_앵커면_건드리지_않는다():
    o = _sys()
    f = _Flow(_RECRUIT, [11, 12], anchor=11)
    assert o._ensure_working_anchor(f) is False
    assert f.anchor == 11 and f.comm.rotated == []


def test_넘길_사람이_없으면_그대로_둔다():
    """실무자가 아직 없는 갓 생긴 판 — 여기서 앵커를 비우면 판이 멈춘다. 정족수 관문이 막는다."""
    o = _sys()
    f = _Flow(_RECRUIT, [10], anchor=10)
    assert o._ensure_working_anchor(f) is False
    assert f.anchor == 10


def test_회전이_거부되면_앵커가_흔들리지_않는다():
    """단일활성 원자 회전이 실패하면(다른 봇이 프레임을 쥔 중) 종전 앵커를 유지한다."""
    o = _sys()
    f = _Flow(_RECRUIT, [10, 11], anchor=10, rotate_ok=False)
    assert o._ensure_working_anchor(f) is False
    assert f.anchor == 10


def test_채용_앵커는_정족수에_세지_않는다():
    """실무자 1명 + 채용 앵커 = 2가 아니라 1이다 — U-520이 통과했던 산술을 막는다."""
    o = _sys()
    f = _Flow(_RECRUIT, [10, 11], anchor=10)
    assert o._stage_roster_ready(f) is False
    ev = dict((e, d) for e, d in o._logged).get("goal_meeting_deferred_thin_roster")
    assert ev and ev["roster"] == 1, o._logged


def test_사람_앵커는_종전대로_한_명으로_센다():
    o = _sys()
    f = _Flow(_RECRUIT, [11, 12], anchor=11)      # 앵커 1 + 팀 1 = 2 < 3
    assert o._stage_roster_ready(f) is False
    ev = dict((e, d) for e, d in o._logged).get("goal_meeting_deferred_thin_roster")
    assert ev and ev["roster"] == 2, o._logged
