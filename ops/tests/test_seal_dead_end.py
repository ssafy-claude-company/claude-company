"""주기 안에서 e2e 항목을 봉인하려 하면, 왜 안 되고 무엇을 해야 하는지가 함께 나온다."""
from system.guide_tools import _target_dead_end_hint


class _MS:
    status = "in_progress"
    ms_id = "MS-1"
    goal = "e2e 결함 해소"


class _Flow:
    milestones = [_MS()]
    workspace = "/tmp"


def test_주기중_e2e항목_봉인시도는_경계와_두_출구를_알려준다():
    hint = _target_dead_end_hint(_Flow(), "condition:13")
    assert "Task 경계" in hint
    assert "원인" in hint and "drop_backlog" in hint


def test_봉인할_대상이_없으면_그_사실과_대안을_말한다():
    """[U-442 실측] challenge가 하나도 없을 때 '정확한 target이 아니다'로만 끝나, 팀이
    '주기를 닫아야 target이 생긴다 → 닫으려면 영수증이 필요하다'는 순환에 갇혀 판이 멈췄다."""
    hint = _target_dead_end_hint(_Flow(), "릴리스 검증")
    assert "봉인할 대상이 없습니다" in hint and "report_iter" in hint


def test_challenge가_열려_있으면_군더더기를_안_붙인다():
    class _F(_Flow):
        _release_verify_challenge = {"desc": "다른 조건", "verify": "pytest -q"}
    assert _target_dead_end_hint(_F(), "릴리스 검증") == ""
