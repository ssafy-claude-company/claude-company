"""실증된 주기는 미착수 기록에 붙잡히지 않는다(2026-08-03, 실측 U-478).

U-478은 05:57에 마일스톤 완수조건이 3차 검증에서 전부 실증됐다(충족 1/1). 그런데 주기가 닫히지
않았다 — 단계는 '일감 소진 = 완수'로 닫히는데(2026-07-22), 러너가 보고의 조건 줄마다 일감을
소급 등재하므로 아무도 집지 않은 항목이 장부에 쌓여 소진이 오지 않았다.
실측 ST-7: 미완 58건 중 53건이 그렇게 태어나 한 번도 지명되지 않았다.

목표가 증명됐는데 기록이 판을 붙잡는 구조라 고쳤다. 착수 이력(ts_pick)이 없는 일감만 접고,
한 번이라도 손을 댄 일감은 그대로 남아 마감을 막는다.
"""
import pytest

from system.flow import Flow
from system.rule.backlog import BacklogRelay, drop_unstarted_on_proven_cycle
from system.rule.milestone import Milestone, SubTask, wrapup_done


class _G:
    async def post(self, *a, **k):
        return None


def _cycle():
    flow = Flow(_G(), channel_id=700, guild_id=1, leader_id=11,
                bot_info={11: "리더", 12: "구현", 22: "QA"})
    ms = Milestone("MS-P", "브라우저에서 한 판이 돌아간다", [])
    st = SubTask("MS-P/ST-1", "구현", [])
    ms.subtasks = [st]
    flow.milestones = [ms]
    relay = BacklogRelay(st.st_id)
    flow.backlog_relays = {st.st_id: relay}
    return flow, ms, st, relay


def test_아무도_착수하지_않은_일감은_실증된_주기와_함께_접힌다():
    flow, ms, st, relay = _cycle()
    ghost = relay.submit(12, "보고가 남긴 조건 줄", force=True)      # 소급 등재 — 아무도 안 집음

    dropped = drop_unstarted_on_proven_cycle(flow, ms)

    assert dropped == [ghost.backlog_id]
    assert ghost.status == "dropped"
    assert any("아무도" in str(x) for x in (ghost.activity or [])), "왜 접혔는지 장부에 남아야 한다"


def test_한_번이라도_손을_댄_일감은_남아_마감을_막는다():
    flow, ms, st, relay = _cycle()
    started = relay.submit(12, "실제로 집어서 하던 일", force=True)
    relay.pick(12, started.backlog_id, 12)

    assert drop_unstarted_on_proven_cycle(flow, ms) == []
    assert started.status == "in_progress"


def test_실증된_주기는_미착수_기록만_남았을_때_닫힌다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, ms, st, relay = _cycle()
    done_one = relay.submit(12, "실제로 끝낸 일", force=True)
    relay.pick(12, done_one.backlog_id, 12)
    relay.done(12, done_one.backlog_id)
    relay.submit(22, "보고가 남긴 조건 줄 1", force=True)
    relay.submit(22, "보고가 남긴 조건 줄 2", force=True)
    ms.status = "wrapup"                     # 완수조건 실증 통과 상태

    out = wrapup_done(flow, ms)

    assert ms.status == "done", f"실증된 주기가 미착수 기록에 막혔다: {out}"
    assert st.status in ("done", "superseded")


def test_완수조건_실증_시점에_미착수_기록은_게이트를_막지_않는다(monkeypatch):
    """[막힌 자리는 wrapup_done이 아니라 iter_verify였다(2026-08-03 실측)]

    U-478 MS-585233967-2는 완수조건 1/1을 3차 검증에서 실증하고도 주기 상태가 `open`에 머물렀다.
    iter_verify가 '완수조건 충족 + 백로그 전부 종결'을 함께 요구하는데(2026-07-14 사용자 규칙),
    ST-7에 미완 54건이 남아 wrapup에 이르지 못한 것이다 — 그중 대부분이 아무도 착수한 적 없는
    소급 등재분이었다.

    그 규칙의 근거는 '백로그를 모두 완수하면 끝난다 — 중단으로 처리된 것은 제외'다. 착수 이력이
    없는 항목은 접어서 게이트를 통과시키고, 손을 댄 항목은 그대로 막는다.
    """
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.milestone import Criterion, iter_verify

    flow, ms, st, relay = _cycle()
    ms.criteria = [Criterion(desc="브라우저에서 한 판이 돌아간다", verify="npm run verify")]
    ms.criteria[0].passed = True
    relay.submit(22, "보고가 남긴 조건 줄", force=True)      # 미착수 — 게이트를 막으면 안 된다

    ok, msg = iter_verify(flow, ms, [{"desc": "브라우저에서 한 판이 돌아간다", "passed": True, "evidence": "exit 0 · npm run verify PASS"}])

    assert ok is True, f"실증된 주기가 미착수 기록에 막혔다: {msg}"
    assert ms.status == "wrapup"


def test_손을_댄_일감은_실증_시점에도_게이트를_막는다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.milestone import Criterion, iter_verify

    flow, ms, st, relay = _cycle()
    ms.criteria = [Criterion(desc="브라우저에서 한 판이 돌아간다", verify="npm run verify")]
    ms.criteria[0].passed = True
    started = relay.submit(12, "실제로 집어서 하던 일", force=True)
    relay.pick(12, started.backlog_id, 12)

    ok, msg = iter_verify(flow, ms, [{"desc": "브라우저에서 한 판이 돌아간다", "passed": True, "evidence": "exit 0 · npm run verify PASS"}])

    assert ok is False and "백로그" in msg
    assert ms.status != "wrapup"


def test_재검증을_기다리지_않고_세그먼트_훑기에서도_풀린다(monkeypatch):
    """[iter_verify 안에만 두면 영영 안 불린다(2026-08-03 실측)]

    iter_verify는 팀이 report_iter를 다시 부를 때만 돈다. 그런데 백로그가 남아 있으면 재검증
    자체가 걸리지 않으므로(작업 소진 뒤에야 실증을 드라이브한다), 완수조건을 실증하고도 주기가
    영영 open에 머문다 — 실측 U-478: 05:57 실증, 이후 Task 마감 48회 거절.
    막힘이 관측되는 자리(세그먼트마다 도는 훑기)에서도 같은 정리가 돌아야 한다.
    """
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.backlog import sweep_drained_subtasks
    from system.rule.milestone import Criterion

    flow, ms, st, relay = _cycle()
    ms.criteria = [Criterion(desc="브라우저에서 한 판이 돌아간다", verify="npm run verify")]
    ms.criteria[0].passed = True
    ghost = relay.submit(22, "보고가 남긴 조건 줄", force=True)     # 미착수 기록만 남은 상태

    sweep_drained_subtasks(flow)

    assert ghost.status == "dropped", "관측 자리에서 미착수 기록이 풀리지 않는다"
    assert st.status in ("done", "superseded"), "소진된 단계가 닫히지 않았다"


def test_완수조건이_아직인_주기는_훑기가_건드리지_않는다(monkeypatch):
    """적용 범위는 그대로 — 아직 일감을 더 낼 주기를 조기에 닫으면 안 된다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.backlog import sweep_drained_subtasks
    from system.rule.milestone import Criterion

    flow, ms, st, relay = _cycle()
    ms.criteria = [Criterion(desc="아직 실증 안 됨", verify="npm run verify")]
    ghost = relay.submit(22, "미착수 일감", force=True)

    sweep_drained_subtasks(flow)

    assert ghost.status == "open"
    assert st.status not in ("done", "superseded")
