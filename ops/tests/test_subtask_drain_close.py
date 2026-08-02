"""[마지막 백로그가 닫히면 그 단계도 닫힌다(2026-08-02, U-478 실측)] 서브태스크는 '백로그 소진 = 완수'로
닫기로 되어 있었는데(2026-07-22 결정) 그 판정이 report_iter를 누군가 target=ST로 다시 불러 줄 때만
돌았다. ST-7이 백로그 12개를 전부 닫고도 open으로 남아 주기가 안 닫히고 마감이 14회 거절됐다."""
from system.rule.backlog import BacklogRelay, close_subtask_if_drained
from system.rule.milestone import Milestone, SubTask


class _F:
    log = None
    milestones = []
    backlog_relays = {}


def _mk(statuses):
    st = SubTask("MS-X/ST-7", "배포·인프라", [])
    ms = Milestone("MS-X", "주기", [])
    ms.subtasks = [st]
    f = _F()
    f.milestones = [ms]
    r = BacklogRelay(st.st_id)
    for i, s in enumerate(statuses, 1):
        b = r.submit(10 + i, f"일감 {i}", force=True)
        b.status = s
    f.backlog_relays = {st.st_id: r}
    return f, st, r


def test_전부_종결이면_단계가_닫힌다():
    f, st, r = _mk(["done", "done", "dropped"])
    assert close_subtask_if_drained(f, st, r) is True
    assert st.status == "done"


def test_하나라도_남으면_열어_둔다():
    f, st, r = _mk(["done", "in_progress"])
    assert close_subtask_if_drained(f, st, r) is False
    assert st.status == "open"


def test_백로그가_없으면_닫지_않는다():
    f, st, r = _mk([])
    assert close_subtask_if_drained(f, st, r) is False


def test_이미_닫힌_단계는_건드리지_않는다():
    f, st, r = _mk(["done"])
    st.status = "done"
    assert close_subtask_if_drained(f, st, r) is False
