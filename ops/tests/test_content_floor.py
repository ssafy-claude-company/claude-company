"""[넓이를 아무도 결정하지 않으면 판은 가장 좁게 흐른다(2026-08-04, 사용자: '구현은 최소 단위로
되며 안좋은 결과물')]

실측 U-478(완주작): 게임 코드 1,889줄에 콘텐츠 어휘(wave/upgrade/skill/boss/item) 0회 — 낙하물
회피 단일 루프. 백로그 219건 중 제품 노동 14%(구현 6%·비주얼/사운드 5%·기획 3%), 과정 노동 86%.
원인: 완수조건은 '실증 가능한 것'으로 수렴하는데 넓이는 누구의 결정 사항도 아니었다 — 관문이
'닫힘'을 보상하고 '풍부함'을 보상하지 않으니 회의는 언제나 가장 좁은 실증 가능 범위로 갔다.

구조: ①goal 수렴안에 '내용 폭'(수치 하한) 필수 ②그 항목들이 완수 기준 회의의 조건 초안으로
승계 ③채워진 조건은 GOAL 잠금 — 기존 하드 게이트가 그대로 지킨다. 시스템은 숫자의 크기를
판단하지 않는다(결정이 있었는지만)."""
import sys, types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import register_stage, stage_draft_template


class _Cur:
    def __init__(self):
        self.task_id = "T-1"
        self.status = types.SimpleNamespace(goal="")
        self.content_floor = []


class _Flow:
    def __init__(self):
        self.current = _Cur()
        self.log = None
        self.milestones = []
        self.workspace = ""


def _reg(flow, prop):
    return register_stage(flow, "goal", prop)


def test_내용폭_없는_목표는_반려된다():
    ok, why = _reg(_Flow(), "[수렴안]\n목표: 2인 턴제 카드 대전 게임\n[/수렴안]")
    assert not ok and "내용 폭" in why


def test_수치_없는_내용폭은_반려된다():
    ok, why = _reg(_Flow(), "목표: 카드 대전 게임\n내용 폭: 카드 다양하게 · 보스 여러 종")
    assert not ok and "숫자" in why


def test_미룸_문구는_결정이_아니다():
    ok, why = _reg(_Flow(), "목표: 카드 대전 게임\n내용 폭: 후속 협의에서 확정")
    assert not ok


def test_수치_하한이_있으면_등록되고_Task에_남는다():
    f = _Flow()
    ok, why = _reg(f, "목표: 2인 턴제 카드 대전 게임\n내용 폭: 카드 12종 · 보스 3종 · 성장 축 2개\n"
                      "창의 설계: 방패병 — 앞 열이 받는 피해 40% 감소\n최대 표준: 실제 예 대조 · 핵심 기능 3종 · 주 사용 흐름 원탭")   # 2026-08-05 관문 추가분
    assert ok, why
    assert f.current.content_floor == ["카드 12종", "보스 3종", "성장 축 2개"]


def test_내용폭이_완수기준_조건_초안으로_승계된다():
    f = _Flow()
    f.current.content_floor = ["카드 12종", "보스 3종"]
    t = stage_draft_template("criteria", "안건", flow=f)
    assert "내용 폭 — 카드 12종 | 실증:" in t
    assert "내용 폭 — 보스 3종 | 실증:" in t


def test_흐름_없이도_골격은_선다():
    """flow 미관통 호출(구 경로·테스트)도 종전 골격 그대로 — 이중 수용."""
    t = stage_draft_template("criteria", "안건")
    assert "완수조건:" in t and "내용 폭 —" not in t
