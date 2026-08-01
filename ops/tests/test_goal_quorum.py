"""[목표 정족수·내부 인용(2026-08-01, 사용자 실측 지시)] 한 사람의 첫 제안이 1표로 확정되던 자리."""
from system.rule.milestone import (goal_quorum_hold, strip_internal_citation,
                                   GOAL_QUORUM_TRIES)


class _F:
    log = None


def test_두명_한표로는_목표가_확정되지_않는다():
    f = _F()
    hold = goal_quorum_hold(f, [1, 2], 1)      # ch303 실측: 심의 2명·찬성 1
    assert hold and "모자랍니다" in hold and "recruit" in hold


def test_셋이_모여_두표면_확정된다():
    assert goal_quorum_hold(_F(), [1, 2, 3], 2) == ""


def test_찬성이_하나뿐이면_인원이_차도_보류():
    assert goal_quorum_hold(_F(), [1, 2, 3, 4], 1)


def test_끝내_못모으면_판을_죽이지_않는다():
    f = _F()
    for _ in range(GOAL_QUORUM_TRIES):
        assert goal_quorum_hold(f, [1, 2], 1)
    assert goal_quorum_hold(f, [1, 2], 1) == ""


def test_목표에서_내부_파일_인용을_뗀다():
    g = "브라우저용 1인 끝없는 피하기 게임을 만든다(근거: MINUTES.md:10-16)."
    assert strip_internal_citation(g) == "브라우저용 1인 끝없는 피하기 게임을 만든다"


def test_산문_근거는_보존한다():
    g = "게임을 만든다 근거: 사용자 요청"
    assert strip_internal_citation(g) == g
