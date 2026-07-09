"""진짜 채용(공고→지원→선발) — 사회적 채용 절차의 계약 테스트.

사용자 설계(2026-07-09): "리더나 팀이 독단으로 데려오는 것이 아니라, 팀이 '필요'를
올리면 Organt가 지원하는 형식으로." 발언권 1층(응찰=자기선택)의 멤버십판.
검증: 공고·지원서의 채널 가시성 / 자기선택(지원·패스) / 지원자 중에서만 선발 /
유찰→genesis / 타 흐름 점유 후보 제외 / flow 관측 이벤트.
"""
import asyncio

from system.flow import Flow
from system.guide_tools import make_guide_tools
from system.protocol import Kind
from system.rule.comm_engine import Engagement

from test_sys import FakeGuide   # 동일 하네스 재사용


class SocialGuide(FakeGuide):
    """thread_id를 정수로 — 공고·지원서의 채널 게시(_say → g.post(int(thread)))를 관측 가능하게."""

    async def open_task(self, ch, status):
        self.calls.append(("open_task", ch, status.purpose))
        return "blk", 777


def _mk(bot_info, leader=11):
    g = SocialGuide()
    f = Flow(g, channel_id=500, guild_id=1, leader_id=leader, bot_info=dict(bot_info))
    f.start_root("root")
    logs = []
    f.log = lambda ev, **kw: logs.append((ev, kw))
    t = {x.name: x for x in make_guide_tools(f, leader, "leader")}
    return g, f, t, logs


def _txt(r):
    return r["content"][0]["text"]


def test_공고_지원_선발_해피패스_과정이_채널에_보인다():
    g, f, t, logs = _mk({11: "백엔드", 12: "프론트엔드", 13: "QA", 14: "예비"})
    asyncio.run(t["create_task"].handler({"members": "12"}))     # 팀 = 11, 12 (13·14는 팀 밖)
    woken = []

    async def wake(to, body, kind):
        woken.append(to)
        assert "[채용 공고]" in body                             # 후보는 공고문을 받는다
        if to == 13:
            return "[지원] QA 자동화 회귀 스위트 경험으로 이 검증을 맡고 싶습니다."
        return "[패스]"
    f.wake = wake
    r = asyncio.run(t["recruit"].handler({"role": "QA", "reason": "회귀 검증 인력"}))
    # 후보 = QA(13) + 예비(14) — 팀·공고자 제외. 지원 1(13)·패스 1(14).
    assert set(woken) == {13, 14}
    assert "지원 1건" in _txt(r) and "QA" in _txt(r)
    posts = [c for c in g.calls if c[0] == "post"]
    assert any("[채용 공고]" in c[3] for c in posts)             # 공고가 채널에 게시됨
    assert any("[지원]" in c[3] and c[2] == 13 for c in posts)   # 지원서가 본인(13) 명의로 게시됨
    assert not any("[지원] [지원]" in c[3] for c in posts)        # 마커 중복 없음(본문 선두 마커는 벗김)
    assert f.recruit_open and 13 in f.recruit_open["applicants"]
    # 지원 안 한 12·14 지명 → 거부 / 지원자 13 선발 → 합류
    rno = asyncio.run(t["recruit"].handler({"member": "14", "reason": "그냥"}))
    assert "지원하지 않았습니다" in _txt(rno) and 14 not in f.current.team
    rs = asyncio.run(t["recruit"].handler({"member": "13", "reason": "지원서의 회귀 경험"}))
    assert 13 in f.current.team and f.recruit_open is None
    assert any("[채용 확정]" in c[3] for c in g.calls if c[0] == "post")
    evs = [e for e, _ in logs]
    assert "recruit_posted" in evs and "recruit_apply" in evs and "recruit_awarded" in evs


def test_지명_직행은_공고가_없으면_거부():
    g, f, t, _ = _mk({11: "백엔드", 12: "프론트엔드", 13: "QA"})
    asyncio.run(t["create_task"].handler({"members": "12"}))
    r = asyncio.run(t["recruit"].handler({"member": "13", "reason": "데려오기"}))
    assert "폐지" in _txt(r) and "공고" in _txt(r) and 13 not in f.current.team


def test_전원_패스면_유찰_genesis로_신규채용():
    g, f, t, logs = _mk({11: "백엔드", 12: "프론트엔드", 13: "예비"})
    asyncio.run(t["create_task"].handler({"members": "12"}))

    async def wake(to, body, kind):
        return "[패스]"
    f.wake = wake
    r = asyncio.run(t["recruit"].handler({"role": "사운드", "reason": "효과음"}))
    assert "유찰" in _txt(r) and "합류" in _txt(r)
    assert ("create_agent", 500, "사운드", 11) in g.calls        # 채용 상속(recruiter=공고자)
    gen = next(i for i in f.current.team if f.bot_info.get(i) == "사운드")
    assert gen >= 9501                                           # 신입이 그 직군으로 합류
    assert any("[채용] " in c[3] and "유찰" in c[3] for c in g.calls if c[0] == "post")
    assert any(e == "recruit_genesis" for e, _ in logs)


def test_타흐름_점유_후보는_공고를_받지_않는다():
    g, f, t, _ = _mk({11: "백엔드", 12: "프론트엔드", 13: "QA", 14: "QA"})
    asyncio.run(t["create_task"].handler({"members": "12"}))
    eng = Engagement()
    eng.engage(13, "other-flow")                                 # 13은 다른 흐름에서 일하는 중
    f.comm.attach_engagement(eng, "this-flow")
    woken = []

    async def wake(to, body, kind):
        woken.append(to)
        return "[지원] 시간 있습니다."
    f.wake = wake
    asyncio.run(t["recruit"].handler({"role": "QA", "reason": "검증"}))
    assert 13 not in woken and 14 in woken                       # 점유 후보 제외, 한가한 동료만


def test_마커없는_응답은_지원으로_치지_않는다():
    g, f, t, logs = _mk({11: "백엔드", 12: "프론트엔드", 13: "QA"})
    asyncio.run(t["create_task"].handler({"members": "12"}))

    async def wake(to, body, kind):
        return "관심은 있는데 지금은 좀 바빠서요. 다음에 기회가 되면…"   # [지원] 마커 없음
    f.wake = wake
    r = asyncio.run(t["recruit"].handler({"role": "QA", "reason": "검증"}))
    assert "유찰" in _txt(r)                                     # 명시 [지원]만 지원으로(멤버십 결정이므로)
    assert any(e == "recruit_pass" for e, _ in logs)
