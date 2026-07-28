"""[U-077 실측 회귀(2026-07-28)] '최종 주기가 아니다' 반려는 기계 판정의 근거와 출구를 보여준다.

팀은 초안에 '이번 주기 = 최종 마일스톤'이라고 문장으로 선언해 두고 같은 초안을 10번 다시 냈다.
선언으로는 구조가 안 바뀌는데, 반려문이 규칙만 통보해 무엇이 어긋났는지 볼 수 없었기 때문이다.
"""
from system.rule.milestone import _goal_marker_not_final_note


class _Flow:
    roadmap = []
    milestones = []


def test_반려문이_기계가_읽은_주기_수와_현재_위치를_보여준다():
    note = _goal_marker_not_final_note(_Flow(), ["최소버전", "확장", "완성"])
    assert "3주기" in note and "1번째" in note
    assert "최소버전" in note and "완성" in note      # 그들이 쓴 항목을 그대로 인용


def test_출구_두_개를_구조로_제시한다():
    note = _goal_marker_not_final_note(_Flow(), ["A", "B"])
    assert "GOAL@ 없이 직접" in note                  # ① 이번 주기 몫만 쓰기
    assert "항목 하나로 줄이" in note                  # ② 한 주기로 만들기
    assert "`단계:` 줄이 정합니다" in note              # 선언이 아니라 그 줄이 정본


def test_완료한_주기가_있으면_현재_위치가_밀린다():
    class _F(_Flow):
        milestones = [type("M", (), {"status": "done"})(), type("M", (), {"status": "live"})()]

    assert "2번째" in _goal_marker_not_final_note(_F(), ["A", "B", "C"])


def test_로드맵이_비어도_문장이_깨지지_않는다():
    note = _goal_marker_not_final_note(_Flow(), [])
    assert "0주기" in note and "(비어 있음)" in note
