"""백지 단계는 전원이 먼저 독립으로 말한다 (2026-08-06, 사용자: '1명이 너무 이상한 설계를 냈어 …
다른 얘들은 회의 참여도 안했고 문제 근본적으로 찾고 해결해서 다시 돌려봐').

[규명] 2026-07-09에 강제 R1(전원 의무 발화)을 폐지하며 코드에 이렇게 적어 뒀다: "R1은 발산
(앵커링 방지) 장치였다 — 제거가 의견 다양성에 주는 영향은 floor_bid 분포로 관측해 데이터로
판단한다". 그 데이터가 나왔다. 단계별 확정 표결 참여자 중앙값 실측:

    마일스톤 6 · 작업 영역 7 · 백로그 6   ↔   goal 2 · criteria 2

뒤 단계는 '앞 결론'이라는 딛을 자리가 있어 응찰이 붙는다. 백지에서 시작하는 앞 두 단계는 먼저 쓴
사람의 초안이 곧 결론이 된다 — 실측 U-516의 goal 회의는 참석 1명·발언 2건·찬성 1·반대 0으로
Task 전체를 확정했고, 그 결과가 '3레인 좌우 이동 60초'였다.

[수리] goal·criteria에서만 DRAFT 수렴 **전에** 독립 의견을 동시 수집한다(서로의 발언이 안 보임).
질문은 '남의 초안을 다듬어라'가 아니라 '당신이라면 무엇을 만들 것인가'다. 뒤 단계는 종전
turn-taking 그대로 — 중앙 지시가 아니라 각자 자기 도메인 관점으로 한 번씩 말하는 구조다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import communication as C


def _src():
    return inspect.getsource(C)


def test_백지_단계만_독립_의견을_먼저_모은다():
    src = _src()
    assert '_R1_STAGES = ("goal", "criteria")' in src, "백지 단계 목록이 사라졌다"
    i = src.find("if _stage in _R1_STAGES and members:")
    assert i > 0, "독립 의견 선행 수집이 사라졌다"


def test_독립_수집은_서로를_못_보게_한다():
    src = _src()
    i = src.find("if _stage in _R1_STAGES and members:")
    block = src[i:i + 1200]
    assert "앵커링 방지" in block, "독립성(서로 안 보임) 안내가 빠졌다"
    assert "_fork_collect" in block, "동시 수집(fork)이 아니면 앞사람 발언이 보인다"


def test_질문이_초안_다듬기가_아니라_자기_설계다():
    src = _src()
    i = src.find("if _stage in _R1_STAGES and members:")
    block = src[i:i + 1200]
    assert "당신이라면 무엇을 만들 것인가" in block, "질문이 자기 설계를 묻지 않는다"
    assert "자기 도메인 관점" in block


def test_DRAFT_비준_기계는_그대로다():
    """R1 분기(구 경로)로 갈아타면 DRAFT·비준 표결이 통째로 빠진다 — 그 회귀를 막는다."""
    src = _src()
    assert "_no_r1 = _ms_on()" in src, "_no_r1 계약이 바뀌면 DRAFT 경로가 죽는다"
    i = src.find("if _stage in _R1_STAGES and members:")
    assert "_no_r1 = False" not in src[max(0, i - 800):i], "백지 단계에서 구 R1 분기로 새면 안 된다"
