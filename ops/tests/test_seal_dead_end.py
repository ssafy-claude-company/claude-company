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


def test_e2e항목이_아닌_target에는_군더더기를_안_붙인다():
    assert _target_dead_end_hint(_Flow(), "릴리스 검증") == ""
