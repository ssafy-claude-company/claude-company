"""[자르되 사실은 잃지 않는다(2026-08-02, U-478 실측)] 미러가 백로그를 앞에서 12개만 실어, ST-7의
앞 12개가 전부 done이라 화면·프롬프트가 '전부 완료'로 읽혔다 — 실제로는 33개 중 21건이 남아 있었다."""
from system.rule.backlog import BacklogRelay
from system.rule.milestone import Milestone, SubTask, ms_status_snapshot


class _F:
    log = None

    def _info(self, x):
        return ""


def _flow(n_done, n_open):
    st = SubTask("MS-X/ST-7", "배포·인프라", [])
    ms = Milestone("MS-X", "주기", [])
    ms.subtasks = [st]
    f = _F()
    f.milestones = [ms]
    r = BacklogRelay(st.st_id)
    for i in range(n_done):
        r.submit(11, f"끝난 일 {i}", force=True).status = "done"
    for i in range(n_open):
        r.submit(11, f"남은 일 {i}", force=True)
    f.backlog_relays = {st.st_id: r}
    return f


def test_앞_열두개가_전부_done이어도_미완이_보인다():
    snap = ms_status_snapshot(_flow(12, 21))
    st = snap["sts"][0]
    assert st["bl_total"] == 33 and st["bl_left"] == 21
    assert any(b["s"] not in ("done", "dropped") for b in st["bl"]), "목록에 미완이 하나도 없다"


def test_총계는_잘림과_무관하게_참이다():
    st = ms_status_snapshot(_flow(30, 5))["sts"][0]
    assert st["bl_total"] == 35 and st["bl_left"] == 5 and len(st["bl"]) == 12


def test_전부_끝났으면_미완_0():
    st = ms_status_snapshot(_flow(8, 0))["sts"][0]
    assert st["bl_total"] == 8 and st["bl_left"] == 0
