"""각자 자기 것 → 막히면 중지 → 기다리던 게 끝나면 깨어남(2026-07-31 사용자 설계)."""
import inspect
import types

from system import sys_core
from system.rule import backlog as bl


def _relay():
    r = bl.BacklogRelay(subtask_id="MS-1/ST-1")
    r.submit(11, "저장 API 만들기", force=True)
    r.submit(12, "화면에서 저장 호출", force=True)
    return r


def test_중지는_무엇을_기다리는지_적는다():
    r = _relay()
    r.pick(12, "B2", 12)
    b, _dl = r.block(12, "B2", 12, "B1의 저장 API가 먼저 필요", waits_for="B1")
    assert b.status == "blocked" and b.waits_for == "B1"


def test_기다리던_일감이_끝나면_깨어난다():
    r = _relay()
    r.pick(12, "B2", 12)
    r.block(12, "B2", 12, "B1 먼저", waits_for="B1")
    r.pick(11, "B1", 11)
    r.done(11, "B1")
    flow = types.SimpleNamespace(
        milestones=[types.SimpleNamespace(
            status="open", subtasks=[types.SimpleNamespace(st_id="MS-1/ST-1", status="open")])],
        backlog_relays={"MS-1/ST-1": r})
    woke = bl.blocked_wakeups(flow, "B1", 11)
    assert [b.backlog_id for _k, b in woke] == ["B2"]
    assert r.get("B2").status == "open"
    assert any("대기 해제" in a for a in (r.get("B2").activity or []))


def test_다른_것을_기다리는_중지는_깨우지_않는다():
    r = _relay()
    r.submit(13, "배포 스크립트", force=True)
    r.pick(12, "B2", 12)
    r.block(12, "B2", 12, "B3 먼저", waits_for="B3")
    flow = types.SimpleNamespace(
        milestones=[types.SimpleNamespace(
            status="open", subtasks=[types.SimpleNamespace(st_id="MS-1/ST-1", status="open")])],
        backlog_relays={"MS-1/ST-1": r})
    assert bl.blocked_wakeups(flow, "B1", 11) == []
    assert r.get("B2").status == "blocked"


def test_인계가_사라지고_끝낸_사람이_직접_집는다():
    src = inspect.getsource(sys_core.Sys._finish_backlog_turn)
    assert "_backlog_handoff" not in src
    assert "_start_next_in_order" in src and "blocked_wakeups" in src


def test_다음_집기는_선점킥과_같은_규칙을_쓴다():
    src = inspect.getsource(sys_core.Sys._start_next_in_order)
    assert "claim_kick_target" in src and "backlog_next_in_order" in src
