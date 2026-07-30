"""백로그 회의는 영역 하나로 열린다 — "내 영역 하나 반영됐으니 찬성"의 구조적 제거."""
import types

from system.rule.milestone import _STAGE_FRAME, meeting_stage, open_milestone, open_subtask


class _F:
    log = None
    backlog_relays = {}
    milestones = []


def _flow(tmp_path):
    f = _F()
    f.current = types.SimpleNamespace(task_id="T1", team=[11, 12],
                                      status=types.SimpleNamespace(goal="게임"),
                                      acceptance="- 동작 | 실증: pytest -q")
    f.workspace = str(tmp_path)
    f.milestones = []
    return f


def test_빈_영역이_여럿이면_첫_영역_하나로_연다(tmp_path):
    f = _flow(tmp_path)
    ms = open_milestone(f, "최소버전", [{"desc": "동작", "verify": "pytest"}])
    a = open_subtask(f, ms, "게임 규칙", [])
    open_subtask(f, ms, "판정 로직", [])
    assert meeting_stage(f) == "backlog"
    assert f._stage_target_st == a.st_id, "회의는 영역 하나를 대상으로 열려야 한다"


def test_프레임이_한_영역만_묻는다():
    fr = _STAGE_FRAME["backlog"]
    assert "그 영역 하나" in fr and "다른 영역은 각자의" in fr
