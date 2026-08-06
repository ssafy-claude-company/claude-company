"""영역이 관문 모양으로 굳는다 (2026-08-06, 현준-1 — 사용자: '왜이렇게 작품이 엉성하게 나오는가를
계속 실 데이터와 구조적으로 분석해서 해결하라는거').

[실측] U-496 전 주기 누계 백로그 255건을 작업 영역별로 세면 상위 14개 영역 중 **9개가 검증·배포
계열**이다(브라우저 실행·배포 24 · 브라우저 수용 QA 증거 20 · QA 수용 게이트 12 · 검증 실행 환경
12 · E2E 증거 인계 12 · QA 증적 회귀 게이트 12 · 정적 API 계약 회귀 10 · …, 합 ~112건). 제품 부품
계열은 4개(~55건). 어휘로 분류해도 과정 43% 대 제품 15%다. 작품이 엉성한 것은 솜씨가 아니라
**노동이 놓인 자리** 탓이다.

[구조] 영역 분해 회의는 '이번에 만들 것을 어떤 작업 영역으로 나눌지'를 자유롭게 묻는다. 팀 입장에서
가장 또렷한 좌표는 자기가 통과해야 할 관문이라, 영역이 관문 모양(증거·수용·회귀·배포)으로 굳는다.
goal 회의가 이미 정한 부품(내용 폭·최대 표준)은 그 자리에 실려 오지 않았다.

[수리] ①영역 분해 골격이 goal의 부품을 '단위:' 후보로 먼저 싣는다 ②부품이 하나도 영역에 닿지
않으면 반려. 검증·배포 영역은 그대로 열 수 있다 — 제품에서 출발하게만 한다(중앙 지시가 아니라
이미 그 팀이 내린 결정을 딛게 하는 것).
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import Milestone, register_stage, stage_draft_template


class _Cur:
    def __init__(self):
        self.task_id = "T-1"
        self.status = types.SimpleNamespace(goal="피하기·수집 게임")
        self.acceptance = ""
        self.content_floor = ["적 유형 4종", "성장 축 2개"]
        self.creative = []
        self.standard = "실제 예 대조 · 사운드 이펙트 · 스테이지 변화"


class _Flow:
    def __init__(self):
        self.current = _Cur()
        ms = Milestone(ms_id="MS-1-1", goal="이번 주기", criteria=[])
        ms.status = "open"
        self.milestones = [ms]
        self.backlog_relays = {}
        self.log = None
        self.workspace = ""
        self.roadmap = []


def _reg(prop, flow=None):
    f = flow or _Flow()
    return f, register_stage(f, "subtask", prop)


def test_골격이_제품_부품을_영역_후보로_싣는다():
    t = stage_draft_template("subtask", "안건", flow=_Flow())
    assert "단위: 적 유형 4종" in t and "단위: 사운드 이펙트" in t
    assert "검증·배포 영역은 그 위에 더하세요" in t


def test_전부_관문_모양이면_반려된다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    prop = ("단위: QA 수용 증거\n단위: 브라우저 실행·배포 증적\n단위: 회귀 게이트")
    _f, (ok, why) = _reg(prop)
    assert not ok and "제품 부품" in why and "적 유형 4종" in why


def test_부품_하나라도_영역이면_통과한다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    prop = ("단위: 적 유형 4종 구현\n단위: QA 수용 증거\n단위: 브라우저 배포")
    _f, (ok, why) = _reg(prop)
    assert ok, why


def test_부품_결정이_없던_판은_종전_그대로다(monkeypatch):
    """구 판(내용 폭·최대 표준 이전)은 이 관문이 끼어들지 않는다 — 이중 수용."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _Flow()
    f.current.content_floor = []
    f.current.standard = ""
    _f, (ok, why) = _reg("단위: QA 수용 증거\n단위: 배포 증적", flow=f)
    assert ok, why
