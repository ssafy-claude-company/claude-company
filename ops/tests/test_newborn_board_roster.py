"""갓 생긴 판은 사람이 없어서 죽는다(2026-08-03, 실측 U-501·U-502).

U-502는 태어난 지 46초 만에 멈췄다. 흐름 원장:
  +0s  project_autoregistered
  +16s stage_meeting_opened(goal)   ← meet는 "회의할 멤버가 없습니다"를 즉시 반환
  +28s stage_meeting_opened(goal)
  +46s stage_meeting_opened(goal) -> stage_stall_break(n=3) -> stage_stuck_parked -> stalled_stopped
채용 0건 · 회의 응찰 0건. 열 수 없는 회의를 세 번 연 것이 '같은 자리 반복'으로 집계됐다.

재개설 계수가 잡으려는 것은 '같은 자리를 맴도는 팀'이지 '아직 없는 팀'이 아니다.
"""
from system.flow import Flow
from system.sys_core import Sys


class _G:
    async def post(self, *a, **k):
        return None


def _sys():
    return Sys.__new__(Sys)


def _flow(team):
    flow = Flow(_G(), channel_id=900, guild_id=1, leader_id=11, bot_info={11: "리더", 12: "구현"})
    flow.anchor = 11

    class _Cur:
        pass

    cur = _Cur()
    cur.team = list(team)
    flow.current = cur
    return flow


def test_사람이_없으면_단계_회의를_열지_않는다():
    sys = _sys()
    logs = []
    sys._log = lambda ev, **kw: logs.append((ev, kw))
    flow = _flow([11])                      # 앵커 혼자 — meet가 열릴 수 없는 상태

    assert sys._stage_roster_ready(flow) is False
    assert any(ev == "stage_meeting_skipped_empty_roster" for ev, _ in logs), (
        "열 수 없는 회의를 연 사실이 원장에 남지 않으면 왜 안 열렸는지 진단할 수 없다")


def test_사람이_있으면_종전대로_연다():
    sys = _sys()
    sys._log = lambda ev, **kw: None
    assert sys._stage_roster_ready(_flow([11, 12])) is True


def test_Task가_없으면_이_게이트의_판단_대상이_아니다():
    sys = _sys()
    sys._log = lambda ev, **kw: None
    flow = _flow([11])
    flow.current = None
    assert sys._stage_roster_ready(flow) is True
