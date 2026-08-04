"""[경제 감각(2026-08-04)] economy 도구 — 읽기 전용 조회, 매체중립 강등.

봇이 요율·주인 예산·시장을 셈에 넣을 수 있고, guide가 economy를 모르면(디스코드 등)
협업을 막지 않고 '지원하지 않는다'로 답한다. 돈이 움직이는 걸음은 도구에 없다.
"""
import asyncio

from test_sys import FakeGuide, _flow, _tools


class EconomyGuide(FakeGuide):
    async def economy(self, channel_id=None):
        self.economy_asked = channel_id
        return {"ok": True,
                "rates": {"currency": "뮤르", "buy_krw": 18, "cashout_krw": 6,
                          "convert_rate": 0.75, "fee_pct": 15, "fee_normal_pct": 30,
                          "credit_krw": 24.0},
                "owner_budget": {"remaining_credits": 2100, "quota_credits": 3000,
                                 "plan": "starter", "murr_balance": 500},
                "market": [{"listing_id": 1, "name": "장인", "role": "QA",
                            "price": 700, "sold_count": 3, "distills": 9}],
                "work_market": [{"pid": "wm-1", "name": "게임판", "price": 300,
                                 "sold_count": 2}]}


def test_경제를_한눈에_요약한다():
    f = _flow(EconomyGuide())
    t = _tools(f, 12, "member")
    out = asyncio.run(t["economy"].handler({}))
    text = out["content"][0]["text"]
    assert "18원" in text and "수수료 15%" in text          # 요율
    assert "2100" in text and "500" in text                 # 주인 예산
    assert "장인" in text and "700" in text                 # 직원 시장
    assert "게임판" in text and "300" in text               # 완성작 시장
    assert f.guide.economy_asked == f.user_channel          # 이 판 기준으로 묻는다


def test_모르는_매체에서는_조용히_강등된다():
    f = _flow(FakeGuide())                                  # economy 없음(디스코드 등)
    t = _tools(f, 12, "member")
    out = asyncio.run(t["economy"].handler({}))
    assert "지원하지 않습니다" in out["content"][0]["text"]


def test_웹이_죽어도_협업은_계속된다():
    class DownGuide(FakeGuide):
        async def economy(self, channel_id=None):
            raise RuntimeError("down")
    f = _flow(DownGuide())
    t = _tools(f, 12, "member")
    out = asyncio.run(t["economy"].handler({}))
    assert "가져오지 못했습니다" in out["content"][0]["text"]
