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
