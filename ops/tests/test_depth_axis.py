"""넓이만으로는 두꺼워지지 않는다 (2026-08-06, 사용자: '아직 최소 단위인 느낌인데? 또 60초 60초
거리고 … 로그라이크에 성장형 증강등 여러 요소가 있던 그러한 화려한거나 그런게 없어').

[실측] 열린 요청 '게임 만들어줘'가 판마다 같은 자리로 수렴했다 — U-478 60초 · U-505 60초 ·
U-506 60초 · U-516 60초·3레인 · U-517 3분. 내용 폭은 '레인 3 · 장애물 4종'처럼 **가짓수만** 셌다.
작품을 두껍게 만드는 것은 가짓수가 아니라 **판이 진행되며 달라지는 축**(성장·해금·조합·강화·
메타 재화·난이도 곡선)이다. 디코 시절 p-012 GOAL이 그 축들로 14개 절을 채웠다.

[수리] ①축소어에 '한 판의 길이·단일 무대'를 추가 — 원문에 없던 60초/3분/단일 경기장은 목적지가
아니라 첫 주기의 크기다. ②내용 폭에 깊이 축 최소 1개 요구. 둘 다 형태 검사다(어휘가 있는지만
본다 — 시스템은 그 축이 좋은지 나쁜지 판단하지 않는다).
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import goal_narrowing_error, register_stage

_STD = "최대 표준: 실제 예 대조 · 핵심 루프 완결 · 주 사용 흐름 원탭"
_CRE = "창의 설계: 방패병 — 앞 열이 받는 피해 40% 감소"


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


def _reg(floor):
    f = _Flow()
    return register_stage(f, "goal", f"목표: 브라우저 액션 게임\n내용 폭: {floor}\n{_CRE}\n{_STD}")


def test_가짓수만_센_내용폭은_반려된다():
    ok, why = _reg("레인 3개 · 장애물 4종 · 수집물 3종")
    assert not ok and "깊이 축" in why


def test_깊이_축이_있으면_통과한다():
    # [지속 축 관문(2026-08-06)] 판 안의 축만으로는 통과하지 않는다 — 세션을 넘어 남는 축도 있어야
    # 한다(이 테스트가 보는 것은 깊이 축이므로 지속 항목을 함께 둔다). 전용 검사는 아래 별도 테스트.
    for floor in ("적 4종 · 강화 3택1 · 무기 6종 · 해금 8종",
                  "장애물 4종 · 웨이브 10단 · 최고 기록 갱신 보상 3단",
                  "카드 12종 · 해금 요소 5개",
                  "적 5종 · 난이도 곡선 3단 · 도전 과제 12개"):
        ok, why = _reg(floor)
        assert ok, f"{floor} → {why}"


def test_한_판_길이는_축소다():
    """원문이 말하지 않은 세션 길이는 목적지가 아니라 첫 주기의 크기다."""
    for g in ("60초 동안 장애물을 피하는 게임", "한 판 3분 아케이드", "3분 안에 끝나는 생존 게임"):
        assert goal_narrowing_error(g, "게임 만들어줘"), g


def test_단일_무대도_축소다():
    assert goal_narrowing_error("단일 경기장에서 벌어지는 액션 게임", "게임 만들어줘")


def test_사용자가_말한_길이는_존중한다():
    """원문이 '60초'라고 했으면 그대로 따른다 — 축소가 아니라 요청이다."""
    assert goal_narrowing_error("60초 동안 버티는 게임", "60초 게임 만들어줘") == ""


def test_한_판_안의_축만이면_반려된다():
    """[깊이도 최대로(2026-08-06, 사용자: '깊이도 최대 작업을 할 수 있도록 해서 재시작 해야지')]
    깊이 관문을 세운 뒤에도 결과는 '30초 웨이브 6개 · 3단계 난이도'였다(실측 U-525) — 전부 한 세션
    안에서만 달라지는 축이라 끝나면 남는 것이 없다. 앞서 사용자가 짚은 '보스에 도전하고 즉시
    재시작이라는게 실제 사용자를 고려하지도 않은 단발성'이 같은 뿌리다."""
    ok, why = _reg("웨이브 6개 · 난이도 3단계 · 장애물 4종")
    assert not ok and "다시 올 이유" in why


def test_지속_축이_있으면_통과한다():
    ok, why = _reg("웨이브 6개 · 난이도 3단계 · 해금 12종")
    assert ok, why


def test_정말_단발성이면_사유와_함께_면제된다():
    ok, why = _reg("변환 규칙 6종 · 난이도 곡선 3단 [지속 N/A: 1회용 변환 스크립트라 남길 상태 없음]")
    assert ok, why
