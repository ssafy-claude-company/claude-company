"""시스템이 가르친 범위 표기를 시스템이 되받는다(2026-08-03, 실측 U-478).

이 판은 봇에게 백로그 범위를 `<SubTask-ID>::<Bn>`으로 보여준다 — 모호할 때의 후보 목록도
(`ST::B1 · ST::B2`), 오류문(`[릴레이] 지정한 범위 ST::Bn을 …`)도, 보충 링크 문법(`[해결: ST::Bn]`)도
그 꼴이다. 그런데 봇이 그 문자열을 도구의 `st`로 되돌려주면 st_id와 같지 않아 '단위를 찾지
못했습니다'로 거절됐다.

실측 U-478 10:32~10:45: `drop_backlog({id:B73, st:MS-585233967-2/ST-7::B73})`가 거절되고, 같은
확인이 네 턴 반복됐다(부분 완료 ×4). 그 사이 주기 wrapup 재실증도 못 했다.
"""
import pytest

from system.guide_tools import _resolve_scoped_backlog
from system.rule.backlog import BacklogRelay
from system.rule.milestone import Milestone, SubTask
from system.flow import Flow


class _G:
    async def post(self, *a, **k):
        return None


def _flow():
    flow = Flow(_G(), channel_id=800, guild_id=1, leader_id=11, bot_info={11: "리더", 12: "구현"})
    ms = Milestone("MS-Z", "판", [])
    st1 = SubTask("MS-Z/ST-1", "구현", [])
    st2 = SubTask("MS-Z/ST-2", "검증", [])
    ms.subtasks = [st1, st2]
    flow.milestones = [ms]
    r1, r2 = BacklogRelay(st1.st_id), BacklogRelay(st2.st_id)
    r1.submit(12, "첫 단계의 일감", force=True)
    r2.submit(12, "둘째 단계의 같은 번호 일감", force=True)
    flow.backlog_relays = {st1.st_id: r1, st2.st_id: r2}
    return flow, ms, st2


def test_시스템_표기_ST콜론콜론Bn을_범위로_받아들인다():
    flow, ms, st2 = _flow()

    hit, err = _resolve_scoped_backlog(flow, ms.subtasks, "B1", me_id=12,
                                       st_hint="MS-Z/ST-2::B1")

    assert err is None, err
    assert hit is not None and hit[0].st_id == st2.st_id


def test_평범한_SubTask_ID도_종전대로_받는다():
    flow, ms, st2 = _flow()

    hit, err = _resolve_scoped_backlog(flow, ms.subtasks, "B1", me_id=12, st_hint="MS-Z/ST-2")

    assert err is None and hit[0].st_id == st2.st_id


def test_없는_범위는_여전히_거절한다():
    flow, ms, _st2 = _flow()

    hit, err = _resolve_scoped_backlog(flow, ms.subtasks, "B1", me_id=12,
                                       st_hint="MS-Z/ST-9::B1")

    assert hit is None and err and "찾지 못했습니다" in err
