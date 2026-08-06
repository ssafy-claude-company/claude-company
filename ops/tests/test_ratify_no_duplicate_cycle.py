"""비준 회의는 비준 없이 주기를 낳을 수 없다 (2026-08-06, 현준-1 — 사용자: '어떻게 주기 3에서 했던게
거의 똑같이 주기 4의 목표로 잡을 수 있는거지 … 전의 구조적 이슈로 인한 마일스톤 생성이였나').

[규명] e2e가 'GOAL 조건에 비준된 exact verifier 없음'으로 막히면 시스템이 마일스톤 회의를 연다.
그런데 그 회의 골격의 질문은 '이번에 완성해 보여줄 딱 하나'라, 팀은 비준 대신 **거의 같은 목표의
새 주기**를 등록했다 — 실측 U-506 MS-4(직전과 유사도 0.79), U-496 MS-4~7 네 개 전부 같은 공장
산물(주기당 하루·수십$). 새 주기를 다 돌아도 비준은 여전히 없으니 e2e가 또 막히고 회의가 또
열린다. 등록 관문 어디에도 '비준 회의면 비준이 있어야 한다'는 검사가 없었다.

[수리] 비준 모드(_e2e_ratify_tries/_goal_ratify_tries > 0)에서는 미비준 GOAL 조건을 전부 덮는
수렴안만 주기가 될 수 있다. 내용 판단이 아니라 '이 회의가 열린 이유를 답했는가'라는 형태 검사다.
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import milestone as M


class _Cur:
    def __init__(self):
        self.task_id = "T-1"
        self.status = types.SimpleNamespace(goal="피하기·수집 게임")
        self.acceptance = "게임 전체 흐름 동작 | 실증: 자연어 절차로 확인"
        self.team = [11, 12]


class _Flow:
    def __init__(self, ratify=0):
        self.current = _Cur()
        self.milestones = [M.Milestone(ms_id="MS-1-1", goal="완성판", criteria=[])]
        self.milestones[0].status = "done"
        self.roadmap = ["완성판"]
        self.backlog_relays = {}
        self.log = None
        self.workspace = ""
        self.user_channel = 1
        self._e2e_ratify_tries = ratify


_PROP = ("이번 주기: 브라우저에서 플레이 가능한 피하기·수집 게임 완성판\n"
         "조건 하나 | 실증: node verify.mjs")


def _reg(flow, prop=_PROP):
    return M.register_stage(flow, "milestone", prop)


def test_비준_모드에서_비준_없는_새_주기는_반려된다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _Flow(ratify=1)
    # 비준 대기 중인 자연어 GOAL 조건이 있다고 세운다
    ref = types.SimpleNamespace(desc="게임 전체 흐름 동작", verify="자연어 절차로 확인")
    monkeypatch.setattr(M, "_natural_goal_refs", lambda fl: [ref])
    ok, why = _reg(f)
    assert not ok and "비준" in why and "GOAL@" in why


def test_비준을_덮은_수렴안은_통과한다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _Flow(ratify=1)
    ref = types.SimpleNamespace(desc="게임 전체 흐름 동작", verify="자연어 절차로 확인")
    monkeypatch.setattr(M, "_natural_goal_refs", lambda fl: [ref])
    prop = ("이번 주기: 최종 실증판\n"
            "게임 전체 흐름 동작 | 실증: node verify.mjs")
    ok, why = _reg(f, prop)
    assert ok, why


def test_비준_모드가_아니면_종전_그대로다(monkeypatch):
    """비준 대기 조건이 없는 평시 등록은 이 관문을 지나지 않는다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _Flow(ratify=0)
    f.current.acceptance = ""                 # 자연어 GOAL 조건 없음 — 비준 대상 자체가 없다
    ok, why = _reg(f)
    assert ok, why
