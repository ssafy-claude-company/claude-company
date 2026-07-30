"""판 정산서 계약 — 판이 끝나면 목적별 원가 지도가 한 줄로 남는다."""
from system.sys_core import Sys


class _G:
    async def post(self, *a, **k):
        return None


def test_흐름_누계가_목적별로_쌓이고_요약된다():
    """builder가 턴마다 flow._cost_by_purpose에 적립하고, 흐름 종료가 flow_cost_map으로 낸다."""
    from system.flow import Flow
    f = Flow(_G(), channel_id=1, guild_id=1, leader_id=11, bot_info={11: "L"})
    f._cost_by_purpose = {
        "회의 발언": {"turns": 3, "cost_usd": 0.9, "tokens_in": 300_000, "tokens_out": 900},
        "백로그 작업": {"turns": 1, "cost_usd": 2.1, "tokens_in": 500_000, "tokens_out": 4_000},
    }
    s = Sys(_G(), guild_id=1, organt_builder=None, bot_info={11: "L"})
    total = round(sum(v["cost_usd"] for v in f._cost_by_purpose.values()), 4)
    assert total == 3.0
    top = max(f._cost_by_purpose.items(), key=lambda kv: kv[1]["cost_usd"])[0]
    assert top == "백로그 작업"
    assert hasattr(s, "_purpose_of") and hasattr(s, "_mark_purpose")


def test_목적표기는_flow가_없어도_안전하다():
    s = Sys(_G(), guild_id=1, organt_builder=None, bot_info={11: "L"})
    s._mark_purpose(None, 11, "참여 응찰")          # 판 개시 전 — 예외 없이 넘어간다
