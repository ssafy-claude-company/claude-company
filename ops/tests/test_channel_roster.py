"""[내 직원만 판에 부른다(2026-07-28, 사용자 지시)] 참여 공고 후보 = 채널 주인이 쓸 수 있는 직원.

종전엔 공개 직원 전원이 공고를 받아, 남의 쇼케이스 직원이 내 판에서 일하고 비용은 내 계정에 붙었다.
'공개 직원을 내 직원으로 추가'(AgentAccess)가 생겼으므로 판의 후보도 그 문을 지나야 한다.
"""
from pathlib import Path


from system.sys_core import Sys


class _Guide:
    async def post(self, *a, **k):
        return None


def _sys(bot_info, tmp_path=None):
    def _builder(*a, **k):
        raise AssertionError("이 테스트는 봇을 깨우지 않는다")

    return Sys(_Guide(), 1, _builder, bot_info=dict(bot_info),
               workspace=str(tmp_path or Path("/tmp/ws")))


BOTS = {1: "게임 기획자", 2: "프론트엔드", 3: "백엔드", 9: "채용"}


def test_좁힌_로스터가_있으면_그것만_본다():
    s = _sys(BOTS)
    s.channel_rosters = {77: {3: "백엔드", 9: "채용"}}
    assert s._channel_roster(77) == {3: "백엔드", 9: "채용"}


def test_좁힌_로스터가_없으면_전체(무회귀도_포함=None):
    """디스코드처럼 소유 개념이 없는 매체·부팅 직후는 종전 그대로 전체를 본다."""
    s = _sys(BOTS)
    assert s._channel_roster(77) == BOTS
    s.channel_rosters = {}
    assert s._channel_roster(77) == BOTS
    assert s._channel_roster(None) == BOTS


def test_라벨이_비면_전체_로스터로_보강한다():
    """판 도중 채용된 신입이 좁힌 목록에 라벨 없이 들어와도 이름이 비지 않는다."""
    s = _sys(BOTS)
    s.channel_rosters = {77: {1: "", 5: None}}
    r = s._channel_roster(77)
    assert r[1] == "게임 기획자" and r[5] == "예비"
