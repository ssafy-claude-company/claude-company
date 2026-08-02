"""[한 사람이 여러 판의 병목이 된다(2026-08-02 실측·사용자 확인)] 봇 단위 전역 점유는 '같은 사람이 두
채널에서 동시에 말하는 이중 존재'를 막는 의도된 설계다. 리크루터가 하나뿐이면 그 배타성이 곧 판 사이
병목이 된다(오늘 신판 3.0h·새판 3.9h 굶음). 사람이 여럿이면 한가한 쪽을 먼저 고른다."""
from system.sys_core import Sys


class _Eng:
    def __init__(self, busy=()):
        self.busy = set(busy)

    def holder(self, b):
        return "다른 판" if int(b) in self.busy else None


class _S(Sys):
    def __init__(self, bots, busy=()):
        self.bot_info = bots
        self.engaged = _Eng(busy)
        self.logged = []

    def _log(self, ev, **kw):
        self.logged.append((ev, kw))


def test_한가한_리크루터를_먼저_고른다():
    s = _S({1: "채용", 2: "채용", 3: "프론트엔드"}, busy=(1,))
    assert s._pick_recruiter() == 2


def test_전원_바쁘면_종전대로_첫번째():
    s = _S({1: "채용", 2: "채용"}, busy=(1, 2))
    assert s._pick_recruiter() == 1


def test_한명뿐이고_바쁘면_관측을_남긴다():
    s = _S({1: "채용"}, busy=(1,))
    assert s._pick_recruiter() == 1
    assert any(ev == "recruiter_single_busy" for ev, _ in s.logged)


def test_없으면_None():
    assert _S({1: "프론트엔드"})._pick_recruiter() is None
