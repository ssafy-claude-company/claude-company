"""[요구를 채우면 요구만큼만 나온다(2026-08-05, 사용자: '몹을 추가하라는 명령에 방패병이라는 얘를
두고 앞으로 오는 공격은 데미지가 감소하는등의 고도화 된 봇의 창의적 설계')]

실측 U-496(968턴 $166.70): 제품 3,285줄(game-state 1,956 · game-ui 948 · styles 381)에 검증
13,900줄(verify_ui.py 9,655 · 브라우저 검증 1,031 · 테스트 3,214) — 4.2:1. 요구 밖 기여는 0건.
'내용 폭'은 얼마나 많이를 정할 뿐 요구 밖을 정하지 않아, 판은 명세 충족과 그 실증에만 크레딧을
썼다. 얹을 자리를 회의가 만들지 않으면 아무도 얹지 않는다.

구조: ①goal 수렴안에 '창의 설계'(요구 밖 기여 1건 이상) 필수 ②그 항목이 완수 기준 회의의 조건
초안으로 승계 ③채워진 조건은 기존 GOAL 잠금이 그대로 지킨다. 내용 폭과 같은 통로 — 선언에서
끝나면 실증 분모에 닿지 못하고, 닿지 못한 것은 만들어지지 않는다. 시스템은 기여의 좋고 나쁨을
판단하지 않는다(결정이 있었는지만)."""
import sys, types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import register_stage, stage_draft_template

_FLOOR = "내용 폭: 카드 12종 · 보스 3종"


class _Cur:
    def __init__(self):
        self.task_id = "T-1"
        self.status = types.SimpleNamespace(goal="")
        self.content_floor = []
        self.creative = []


class _Flow:
    def __init__(self):
        self.current = _Cur()
        self.log = None
        self.milestones = []
        self.workspace = ""


def _reg(flow, prop):
    return register_stage(flow, "goal", prop)


def test_창의설계_없는_목표는_반려된다():
    ok, why = _reg(_Flow(), f"목표: 2인 턴제 카드 대전 게임\n{_FLOOR}")
    assert not ok and "창의 설계" in why


def test_미룸_문구는_결정이_아니다():
    ok, why = _reg(_Flow(), f"목표: 카드 대전 게임\n{_FLOOR}\n창의 설계: 후속 협의에서 확정")
    assert not ok and "창의 설계" in why


def test_요구밖_기여가_있으면_등록되고_Task에_남는다():
    f = _Flow()
    ok, why = _reg(f, f"목표: 2인 턴제 카드 대전 게임\n{_FLOOR}\n"
                      "창의 설계: 방패병 — 앞 열이 받는 피해 40% 감소 · 결과 화면에 판정 근거 표시")
    assert ok, why
    assert f.current.creative == ["방패병 — 앞 열이 받는 피해 40% 감소", "결과 화면에 판정 근거 표시"]


def test_창의설계가_완수기준_조건_초안으로_승계된다():
    f = _Flow()
    f.current.content_floor = ["카드 12종"]
    f.current.creative = ["방패병 — 앞 열이 받는 피해 40% 감소"]
    t = stage_draft_template("criteria", "안건", flow=f)
    assert "창의 설계 — 방패병 — 앞 열이 받는 피해 40% 감소 | 실증:" in t
    assert "내용 폭 — 카드 12종 | 실증:" in t


def test_흐름_없이도_골격은_선다():
    """flow 미관통 호출(구 경로·테스트)도 종전 골격 그대로 — 이중 수용."""
    t = stage_draft_template("criteria", "안건")
    assert "완수조건:" in t and "창의 설계 —" not in t


def test_goal_골격에_창의설계_칸이_있다():
    t = stage_draft_template("goal", "안건")
    assert "창의 설계:" in t and "내용 폭:" in t


def test_criteria_코칭이_검증_진입점_통합을_안내한다():
    """검증 4.2:1은 강제로 막지 않는다 — 회의 골격이 통합 진입점을 먼저 권한다."""
    from system.rule.milestone import stage_agenda
    _desc, tmpl = stage_agenda("criteria")
    assert "검증 진입점" in tmpl


def test_goal_코칭이_방패병_선례를_든다():
    _desc, tmpl = __import__("system.rule.milestone", fromlist=["x"]).stage_agenda("goal")
    assert "창의 설계:" in tmpl and "방패병" in tmpl
