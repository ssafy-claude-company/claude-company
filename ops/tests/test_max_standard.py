"""최대 표준이 빠지면 판은 '문자 그대로 최소'로 간다 (2026-08-06, 현준-1 — 사용자: '너무 Task가
최소로 잡히는걸로 바뀐 느낌 … 디코 시절 p002나 p031처럼 완벽한 게임 … 방대한 크기의 큰 게임을
구조로 잡던 그 시절이 필요해').

[규명 — 역사 대조] 디스코드 시절 Task GOAL은 set_goal(rule/task.py)의 **최대화 관문**을 통과해야
섰다: "이 종류의 실제 훌륭한 예를 WebSearch로 찾아(상상 금지) 그 최대판이 당연히 갖춘 구성요소를
분해해 standard에 체크 가능한 항목 목록으로". 그 결과가 p-012 GOAL이다 — 핵심루프·난이도 상하한·
메타재화 존재이유(log10 곡선)·어뷰징 방어·server-authoritative 리더보드·XSS·가용성·반응형·시너지축
승률까지 **14개 절**, Acceptance 15항목, Standard '상용 캐주얼 웹게임 완성도, placeholder 금지'.

지금 파이프라인의 Task GOAL은 goal 회의(register_stage)가 낳는다 — set_goal을 거치지 않으므로
**최대화 관문을 한 번도 지나지 않는다**. 실측: 현행 전 판의 GOAL.md `## Standard`가 빈칸, U-496
GOAL은 두 문장에 Acceptance 0줄. 08-04의 '내용 폭'(수치 하한)은 넓이의 일부만 복원했고, 최대판
구성요소 분해라는 잣대 자체는 사라져 있었다.

[수리] 관문을 goal 회의로 옮긴다 — 수렴안에 '최대 표준:'(부품 목록 3항목 이상) 필수, 미룸 문구
불가, 정말 없으면 '[최대화 N/A: 사유]'. 기록은 _cur.standard → GOAL.md `## Standard`로 흘러
마감 대조의 잣대가 된다. 형태 검사다 — 내용의 좋고 나쁨은 판단하지 않는다.
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import register_stage, stage_draft_template

_BASE = ("목표: 2인 턴제 카드 대전 게임\n내용 폭: 카드 12종 · 보스 3종\n"
         "창의 설계: 방패병 — 앞 열이 받는 피해 40% 감소\n")
_STD = "최대 표준: 실제 예 3종 대조 · 핵심 루프 완결 · 성장 축 2개 · 주 사용 흐름 원탭 시작"


class _Cur:
    def __init__(self):
        self.task_id = "T-1"
        self.status = types.SimpleNamespace(goal="")
        self.content_floor = []
        self.creative = []
        self.standard = ""


class _Flow:
    def __init__(self):
        self.current = _Cur()
        self.log = None
        self.milestones = []
        self.workspace = ""


def _reg(prop, flow=None):
    f = flow or _Flow()
    return f, register_stage(f, "goal", prop)


def test_최대_표준_없는_목표는_반려된다():
    _f, (ok, why) = _reg(_BASE)
    assert not ok and "최대 표준" in why and "WebSearch" in why


def test_한_문장_표준은_부품_목록이_아니다():
    _f, (ok, why) = _reg(_BASE + "최대 표준: 완성도 높게 만든다")
    assert not ok and "항목 목록" in why


def test_미룸_문구는_결정이_아니다():
    _f, (ok, why) = _reg(_BASE + "최대 표준: 후속 협의에서 확정")
    assert not ok


def test_부품_목록이_있으면_등록되고_GOAL_Standard로_흐른다():
    f, (ok, why) = _reg(_BASE + _STD)
    assert ok, why
    assert "핵심 루프 완결" in f.current.standard


def test_최대화_NA는_사유와_함께_면제된다():
    """최대화할 차원이 정말 없는 산출물(단순 스크립트 등)은 사유를 적어 통과한다."""
    _f, (ok, why) = _reg(_BASE + "최대 표준: [최대화 N/A: 단일 변환 스크립트라 확장 차원 없음]")
    assert ok, why


def test_goal_골격에_최대_표준_칸이_있다():
    t = stage_draft_template("goal", "안건")
    assert "최대 표준:" in t and "창의 설계:" in t and "내용 폭:" in t
