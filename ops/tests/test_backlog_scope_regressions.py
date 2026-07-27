"""백로그 지역 ID(B1...)의 범위·재개·완료 판정 회귀.

B번호는 SubTask마다 다시 시작한다. 따라서 실행 소속과 도구 조작은 항상 실제
``(SubTask, Backlog)`` 쌍을 보존해야 하며, 후보가 애매하면 앞 단위를 추측하면 안 된다.
"""

import asyncio
import re

import pytest

from system.flow import Flow
from system.guide_tools import _resolve_scoped_backlog, make_guide_tools
from system.protocol import PIPELINE_CTX
from system.rule.backlog import (
    BacklogRelay, active_backlog_rows, blocked_ready_for_revisit, blocked_supplement_targets,
    normalize_active_backlogs, sync_completion, sync_delegation,
)
from system.rule.milestone import (
    Milestone, SubTask, _set_pipeline_ctx, claim_kick_target, meeting_stage, register_stage,
    rule_report_iter,
)
from system.sys_core import Sys


class _Guide:
    def __init__(self):
        self.picks = []

    async def post(self, *_args, **_kwargs):
        return "m1"

    async def pick(self, msg_id, **kwargs):
        self.picks.append((msg_id, kwargs))
        return True


def _flow_with_two_subtasks():
    flow = Flow(
        _Guide(),
        channel_id=500,
        guild_id=1,
        leader_id=11,
        bot_info={11: "리더", 12: "구현", 22: "QA", 99: "관찰자"},
    )
    milestone = Milestone("MS-X", "상태 전이 검사기", [])
    st1 = SubTask("MS-X/ST-1", "구현", [])
    st2 = SubTask("MS-X/ST-2", "검증", [])
    milestone.subtasks = [st1, st2]
    flow.milestones = [milestone]

    relay1 = BacklogRelay(st1.st_id)
    relay2 = BacklogRelay(st2.st_id)
    backlog1 = relay1.submit(12, "상태 전이 구현", force=True)
    backlog2 = relay2.submit(22, "상태 전이 검증", force=True)
    assert backlog1.backlog_id == backlog2.backlog_id == "B1"
    flow.backlog_relays = {st1.st_id: relay1, st2.st_id: relay2}
    return flow, (st1, relay1, backlog1), (st2, relay2, backlog2)


def _tools(flow, me=22):
    return {tool.name: tool for tool in make_guide_tools(flow, me, flow._info(me))}


def _tool_text(result):
    return result["content"][0]["text"]


def test_종결_flush는_throttle안_마지막생각을_원장과_요청기록에_남긴다():
    from system.rule.milestone import ms_status_snapshot

    flow, (_st1, _relay1, _backlog1), (_st2, relay2, backlog2) = (
        _flow_with_two_subtasks()
    )
    flow.root_id = "777"
    relay2.pick(22, backlog2.backlog_id, 22)
    saved = []
    flow.checkpoint_task = lambda: saved.append(ms_status_snapshot(flow))

    # 직전 미러 시각을 지금으로 고정해 두 note 모두 정상 1초 throttle 안에 놓는다.
    import time
    flow._last_backlog_activity_mirror = time.monotonic()
    flow.note_activity(22, "첫 생각")
    flow.note_activity(22, "중지 직전 마지막 생각")
    assert not saved
    assert len(backlog2.activity) == 2

    sys = Sys(
        flow.guide, guild_id=1, organt_builder=None,
        bot_info=flow.bot_info, workspace="/tmp/unused-terminal-flush",
    )
    asyncio.run(sys._flush_terminal_observability(flow))

    mirrored = next(
        b for ms in saved[-1]["list"] for st in ms["sts"] for b in st["bl"]
        if st["id"] == _st2.st_id and b["id"] == backlog2.backlog_id
    )
    assert mirrored["act"] == backlog2.activity
    assert flow.guide.picks[-1][0] == 777
    # [시각 동승(2026-07-27, 사용자: '생각등에 시간 안남는게 많은데')] 전송 줄은 생성 시각을
    # `[MM-DD HH:MM] ` 접두로 달고 나간다 — 본문은 원장 그대로여야 하고, 시각은 heartbeat마다
    # 같은 문자열이어야 한다(수신측 중복/신규 판정이 문자열 비교라서).
    _sent = flow.guide.picks[-1][1]["activity"]
    _stamp = re.compile(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] ")
    assert [_stamp.sub("", line) for line in _sent] == [row[0] for row in flow.activity_log]
    assert all(_stamp.match(line) for line in _sent), "생각 줄에 시각이 안 붙는다"


def test_pipeline_ctx는_첫_open단위가_아니라_실제_ST2_B1을_태깅한다():
    flow, (_st1, relay1, backlog1), (st2, relay2, backlog2) = _flow_with_two_subtasks()
    relay1.pick(12, backlog1.backlog_id, 12)
    relay1.done(12, backlog1.backlog_id)
    # 백로그가 끝나도 전체 소진 검증 전까지 ST-1 자체는 open일 수 있다.
    assert _st1.status == "open" and backlog1.status == "done"
    relay2.pick(22, backlog2.backlog_id, 22)

    PIPELINE_CTX.set(None)
    try:
        _set_pipeline_ctx(flow, 22)
        assert PIPELINE_CTX.get() == {
            "ms": "MS-X",
            "st": st2.st_id,
            "bl": "B1",
        }
    finally:
        PIPELINE_CTX.set(None)


def test_scoped_resolver는_명시한_ST의_B1을_선택한다():
    flow, (_st1, _relay1, backlog1), (st2, relay2, backlog2) = _flow_with_two_subtasks()

    hit, error = _resolve_scoped_backlog(
        flow, flow.milestones[0].subtasks, "B1", me_id=99, st_hint=st2.st_id
    )

    assert error is None
    assert hit == (st2, relay2, backlog2)
    assert hit[2] is not backlog1


def test_scoped_resolver와_block도구는_현재수행자의_뒤_ST_B1을_선택한다(
    monkeypatch,
):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (_st1, _relay1, backlog1), (st2, relay2, backlog2) = _flow_with_two_subtasks()
    relay2.pick(22, backlog2.backlog_id, 22)

    hit, error = _resolve_scoped_backlog(
        flow, flow.milestones[0].subtasks, "B1", me_id=22
    )
    assert error is None and hit == (st2, relay2, backlog2)

    result = asyncio.run(
        _tools(flow, 22)["block_backlog"].handler(
            {"id": "B1", "st": "", "reason": "구현 산출물이 먼저 필요함"}
        )
    )
    assert "차단(" in _tool_text(result)
    assert backlog1.status == "open"
    assert backlog2.status == "blocked"
    assert backlog2.block_reason == "구현 산출물이 먼저 필요함"


def test_scoped_resolver와_가이드도구는_애매한_B1을_첫_ST로_추측하지_않는다(
    monkeypatch,
):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (_st1, _relay1, backlog1), (_st2, _relay2, backlog2) = _flow_with_two_subtasks()

    hit, error = _resolve_scoped_backlog(
        flow, flow.milestones[0].subtasks, "B1", me_id=99
    )
    assert hit is None
    assert "여러 단위" in error and "st" in error

    result = asyncio.run(
        _tools(flow, 99)["pick_backlog"].handler({"id": "B1", "desc": "", "st": ""})
    )
    assert "여러 단위" in _tool_text(result)
    assert backlog1.status == backlog2.status == "open"


def test_claim_kick은_뒤_ST가_작업중이면_앞_ST_open을_동시착수하지_않는다():
    flow, (_st1, _relay1, _backlog1), (_st2, relay2, backlog2) = _flow_with_two_subtasks()
    relay2.pick(22, backlog2.backlog_id, 22)

    assert claim_kick_target(flow) is None


def test_위임도_뒤_ST가_작업중이면_앞_ST를_동시착수하지_않는다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (st1, _relay1, backlog1), (_st2, relay2, backlog2) = _flow_with_two_subtasks()
    relay2.pick(22, backlog2.backlog_id, 22)

    error = sync_delegation(
        flow, 12, 12, f"[백로그 {st1.st_id}::B1] 상태 전이 구현")

    assert error and "마일스톤 전체 순차 1활성" in error
    assert backlog1.status == "open"
    assert backlog2.status == "in_progress"
    assert len(active_backlog_rows(flow)) == 1


def test_지역_B1_위임은_현재수행자범위를_찾고_첫_ST를_추측하지_않는다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (_st1, _relay1, backlog1), (st2, relay2, backlog2) = _flow_with_two_subtasks()
    relay2.pick(22, backlog2.backlog_id, 22)

    assert sync_delegation(flow, 12, 22, "[백로그 B1] 상태 전이 검증 이어서") is None
    assert active_backlog_rows(flow)[0] == (st2, relay2, backlog2)
    assert backlog1.status == "open"


def test_지역_B1_위임이_끝까지_모호하면_범위를_요구한다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (_st1, _relay1, backlog1), (_st2, _relay2, backlog2) = _flow_with_two_subtasks()

    error = sync_delegation(flow, 99, 99, "[백로그 B1] 진행")

    assert error and "범위가 여러 단위" in error and "SubTask-ID::B1" in error
    assert backlog1.status == backlog2.status == "open"


def test_위임완료는_첫_ST가_아니라_실제_뒤_ST_active를_완료한다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (_st1, _relay1, backlog1), (_st2, relay2, backlog2) = _flow_with_two_subtasks()
    relay2.pick(22, backlog2.backlog_id, 22)

    sync_completion(flow, 22)

    assert backlog1.status == "open"
    assert backlog2.status == "done"


def test_report_iter의_소급백로그도_다른_ST_active를_우회하지_않는다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (st1, relay1, _backlog1), (_st2, relay2, backlog2) = _flow_with_two_subtasks()
    relay2.pick(22, backlog2.backlog_id, 22)

    result = rule_report_iter(
        flow, 12,
        {"target": st1.st_id, "results": "별도 접근성 점검 | pass | 수동 점검 통과"},
    )

    assert "잔여" in result
    fresh = relay1.get("B2")
    assert fresh.status == "open"
    assert active_backlog_rows(flow) == [(flow.milestones[0].subtasks[1], relay2, backlog2)]


def test_구버전_다중active복원은_먼저잡힌_하나만_보존한다():
    flow, (_st1, relay1, backlog1), (_st2, relay2, backlog2) = _flow_with_two_subtasks()
    relay1.pick(12, backlog1.backlog_id, 12)
    relay2.pick(22, backlog2.backlog_id, 22)
    backlog1.ts_pick = 10
    backlog2.ts_pick = 20

    kept, reopened = normalize_active_backlogs(flow)

    assert kept == (flow.milestones[0].subtasks[0], relay1, backlog1)
    assert [row[2] for row in reopened] == [backlog2]
    assert backlog1.status == "in_progress"
    assert backlog2.status == "open" and backlog2.assignee is None
    assert len(active_backlog_rows(flow)) == 1


def test_가이드도구도_마일스톤전체_순차1활성을_지킨다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (st1, _relay1, backlog1), (_st2, relay2, backlog2) = _flow_with_two_subtasks()
    relay2.pick(22, backlog2.backlog_id, 22)

    result = asyncio.run(
        _tools(flow, 12)["pick_backlog"].handler(
            {"id": "B1", "desc": "", "st": st1.st_id}
        )
    )

    assert "마일스톤 전체 순차 1활성" in _tool_text(result)
    assert backlog1.status == "open"
    assert backlog2.status == "in_progress"


def test_가이드도구는_blocked원본을_보충없이_즉시재선점하지_않는다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (st1, relay1, blocked), (_st2, relay2, other) = _flow_with_two_subtasks()
    relay1.pick(12, blocked.backlog_id, 12)
    relay1.block(12, blocked.backlog_id, 12, "검증 데이터 선행")
    relay2.pick(22, other.backlog_id, 22)
    relay2.done(22, other.backlog_id)

    result = asyncio.run(
        _tools(flow, 12)["pick_backlog"].handler(
            {"id": "B1", "desc": "", "st": st1.st_id}
        )
    )

    assert "선행 작업으로 차단" in _tool_text(result)
    assert blocked.status == "blocked"


def test_blocked원본은_차단뒤_새로생긴_보충세대_done뒤에만_재개된다():
    from types import SimpleNamespace

    flow, (st1, relay1, blocked), (st2, relay2, old_open) = _flow_with_two_subtasks()
    flow.current = SimpleNamespace(status=SimpleNamespace(goal="상태 전이 검사기"))
    relay1.pick(12, blocked.backlog_id, 12)
    relay1.block(12, blocked.backlog_id, 12, "검증 데이터 생성 선행")
    blocked_at = blocked.ts_done

    target = claim_kick_target(flow)
    assert target == (22, old_open, st2.st_id)         # 기존 open도 먼저 소진
    relay2.pick(22, old_open.backlog_id, 22)
    relay2.done(22, old_open.backlog_id)
    old_open.ts_done = blocked_at + 2
    assert old_open.ts_submit < blocked_at
    assert claim_kick_target(flow) is None             # 차단 전 생성분의 후속 완료는 보충 증거가 아님
    assert meeting_stage(flow) == "backlog"

    scope = f"{st1.st_id}::{blocked.backlog_id}"
    ok, note = register_stage(
        flow, "backlog",
        f"백로그: [검증] [해결: {scope}] 차단 원본용 검증 데이터 생성",
        "보충 회의")
    assert ok, note
    supplement = relay2.get("B2")
    assert supplement.supplement_for == [scope]         # 이름/시각 추측 아닌 원본 링크
    supplement.ts_submit = blocked_at + 3
    target2 = claim_kick_target(flow)
    worker = int(supplement.submitter)
    assert target2 == (worker, supplement, st2.st_id)
    relay2.pick(worker, supplement.backlog_id, worker)
    relay2.done(worker, supplement.backlog_id)
    supplement.ts_done = blocked_at + 4

    target3 = claim_kick_target(flow)
    assert target3 == (12, blocked, st1.st_id)         # 차단 뒤 생성+완료된 보충 증거 뒤에만 재개
    assert meeting_stage(flow) is None                  # 회의 재개설 대신 작업 단계가 원본을 집음


def test_원본에_연결된_보충여럿은_전부종결되고_하나는_done이어야_재개된다():
    flow, (st1, relay1, origin), (_st2, relay2, old) = _flow_with_two_subtasks()
    relay1.pick(12, origin.backlog_id, 12)
    relay1.block(12, origin.backlog_id, 12, "두 종류 선행 필요")
    blocked_at = origin.ts_done
    relay2.pick(22, old.backlog_id, 22)
    relay2.done(22, old.backlog_id)
    scope = f"{st1.st_id}::{origin.backlog_id}"

    first = relay2.submit(22, "첫 번째 보충 구현", force=True)
    second = relay2.submit(22, "두 번째 보충 검증", force=True)
    for row in (first, second):
        row.supplement_for = [scope]
        row.ts_submit = blocked_at + 1
    relay2.pick(22, first.backlog_id, 22)
    relay2.done(22, first.backlog_id)
    first.ts_done = blocked_at + 2
    relay2.pick(22, second.backlog_id, 22)
    relay2.block(22, second.backlog_id, 22, "추가 fixture 필요")
    second.ts_done = blocked_at + 3

    all_rows = [origin, old, first, second]
    assert blocked_ready_for_revisit(origin, all_rows, scope) is False
    targets = blocked_supplement_targets([(st1, origin), (flow.milestones[0].subtasks[1], old),
                                          (flow.milestones[0].subtasks[1], first),
                                          (flow.milestones[0].subtasks[1], second)])
    assert [row for _st, row in targets] == [second]  # O가 아니라 막힌 보충 leaf만 다음 회의 대상

    relay2.drop(22, second.backlog_id, "별도 경로로 불필요")
    second.ts_done = blocked_at + 4
    assert blocked_ready_for_revisit(origin, all_rows, scope) is True


def test_두_blocked원본의_보충은_각_scope에_따로_연결된다():
    from types import SimpleNamespace

    flow, (st1, relay1, first), (st2, relay2, second) = _flow_with_two_subtasks()
    flow.current = SimpleNamespace(status=SimpleNamespace(goal="상태 전이 검사기"))
    relay1.pick(12, first.backlog_id, 12)
    relay1.block(12, first.backlog_id, 12, "fixture 생성")
    relay2.pick(22, second.backlog_id, 22)
    relay2.block(22, second.backlog_id, 22, "브라우저 검증 환경")
    scope1 = f"{st1.st_id}::{first.backlog_id}"
    scope2 = f"{st2.st_id}::{second.backlog_id}"

    ok, note = register_stage(
        flow, "backlog",
        "\n".join([
            f"백로그: [구현] [해결: {scope1}] fixture 생성 자동화",
            f"백로그: [검증] [해결: {scope2}] 브라우저 검증 환경 구성",
        ]),
        "두 차단 원본 보충 회의",
    )

    assert ok, note
    assert relay1.get("B2").supplement_for == [scope1]
    assert relay2.get("B2").supplement_for == [scope2]


def test_blocked_백로그_재개는_새_시간창을_연다(monkeypatch):
    import system.rule.backlog as backlog_rule

    ticks = iter((50.0, 100.0, 110.0, 200.0))         # submit, pick, block, revisit
    monkeypatch.setattr(backlog_rule.time, "time", lambda: next(ticks))
    relay = BacklogRelay("MS-X/ST-1")
    backlog = relay.submit(12, "선행 뒤 재개할 구현", force=True)

    relay.pick(12, backlog.backlog_id, 12)
    assert backlog.ts_pick == 100.0
    relay.block(12, backlog.backlog_id, 12, "선행 필요")
    assert backlog.ts_done == 110.0

    relay.pick(12, backlog.backlog_id, 12)
    assert backlog.status == "in_progress"
    assert backlog.ts_pick == 200.0
    assert backlog.ts_done == 0


@pytest.mark.parametrize(
    "text",
    [
        "선행 작업이 필요해서 아직 완료하지 못했습니다.",
        "테스트가 실패해 다음 턴에서 계속해야 합니다.",
        "현재 미완 상태이며 추가 구현이 필요합니다.",
    ],
)
def test_명시적_미완_보고는_백로그_완료로_판정하지_않는다(text):
    assert Sys._backlog_turn_complete_text(text) is False


def test_백로그완료는_자연어추측아닌_마지막_구조표식으로만_판정한다():
    assert Sys._backlog_turn_complete_text("구현 완료") is False
    assert Sys._backlog_turn_complete_text(
        "초기에는 권한이 없었지만 대체 경로로 구현·검증을 모두 마쳤습니다.\n[백로그 완료]"
    ) is True
    assert Sys._backlog_turn_complete_text(
        "[백로그 완료]\n아직 테스트가 남았습니다."
    ) is False
