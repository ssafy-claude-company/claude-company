"""증류가 굶고 있었다 (2026-08-07, 사용자: '지식 증류적 활용이 오히려 이득 아니야?').

[실측] 원석 풀(bot_experience) 보유 봇 54명의 줄 수 분포:

    1줄 17명 · 2줄 15명 · 3줄 9명 · 4줄 13명 · **5줄 이상 0명**

증류 발동 임계는 5다(_BOT_DISTILL_MIN). 아무도 닿지 못한다. 원료가 4에서 멎는다.

[원인] 원료를 요청하는 자리가 craft_note 하나인데, 그 함수는 **first_wake에만** 불린다
(resume이면 통째로 생략). 그래서 세션이 길수록 요청이 안 나가고, **일을 오래 할수록 배운 것이
덜 남는다** — 정확히 거꾸로다. 과거 431회 증류된 프로필이 있는 것은 첫 wake가 잦던 시절의 유산이다.

[조치] 자기 일감을 쥔 턴은 그 턴이 '끝내는 턴'일 수 있다 — 교훈이 가장 신선한 자리다. resume이어도
그 자리에서만 경험 블록을 다시 요청한다. 정체성·기준 재주입은 하지 않는다(07-06 격리 계약 유지).

[세션 재시작과의 관계] 일감 완료 경계에서 세션을 새로 시작하면 다음 턴이 first_wake가 된다 —
증류된 기준이 다시 주입되고, 경험 요청도 다시 나간다. 둘은 대안이 아니라 한 쌍이다:
끊는 것이 증류를 돌리고, 증류가 끊어도 잃지 않게 만든다.
"""
import inspect
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system import sys_prompt as SP


class _Sys:
    bot_info = {12: "게임 QA"}
    bot_profiles = {12: "기준 줄"}
    bot_experience = {12: ["교훈1"]}
    capability_ledger = {}


class _B:
    def __init__(self, who, status):
        self.assignee = who
        self.status = status
        self.backlog_id = "B1"


def _flow(status="in_progress", who=12):
    return types.SimpleNamespace(
        backlog_relays={"ST-1": types.SimpleNamespace(backlogs=[_B(who, status)])})


def test_일감을_쥔_턴은_resume이어도_경험을_묻는다():
    t = SP.craft_note(_Sys(), 12, first_wake=False, working=True)
    assert "[경험]" in t and "게임 QA" in t


def test_일감이_없으면_종전대로_침묵한다():
    assert SP.craft_note(_Sys(), 12, first_wake=False, working=False) == ""


def test_정체성은_재주입하지_않는다():
    """07-06 격리 계약 — resume에 기준·경험 회고를 다시 싣지 않는다. 요청 한 블록뿐."""
    t = SP.craft_note(_Sys(), 12, first_wake=False, working=True)
    assert "당신의 최근 경험" not in t and "직무 기준 작성" not in t


def test_진행중_일감_판정():
    assert SP._holds_open_work(_flow("in_progress"), 12) is True
    assert SP._holds_open_work(_flow("done"), 12) is False
    assert SP._holds_open_work(_flow("in_progress", who=99), 12) is False


def test_깨져도_턴을_막지_않는다():
    class _Bad:
        @property
        def backlog_relays(self):
            raise RuntimeError("깨짐")
    assert SP._holds_open_work(_Bad(), 12) is False
    assert SP._holds_open_work(None, 12) is False


def test_프롬프트_양쪽에_연결됐다():
    src = inspect.getsource(SP.prompt)
    assert src.count("working=_holds_open_work(flow, me)") >= 2
