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


def test_보고할_때마다_소진_판정_자리에서_풀린다(monkeypatch):
    """[막힘이 관측되는 자리에서 푼다(2026-08-03, 실측)]

    같은 정리를 iter_verify와 세그먼트 훑기에 두었지만 둘 다 이 상황에 도달하지 않는다 —
    전자는 백로그가 남으면 재검증이 안 걸려서, 후자는 세그먼트 경계가 몇 시간에 한 번이라서.
    실측 U-478: 완수조건 1/1을 05:57에 실증하고 3시간 뒤에도 상태 open, 그 사이 경계 1회.

    소진 여부를 실제로 묻는 자리는 report_iter의 단계 마감 판정이다 — 보고마다 돈다.
    """
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.milestone import Criterion, rule_report_iter

    flow, ms, st, relay = _cycle()
    ms.criteria = [Criterion(desc="브라우저에서 한 판이 돌아간다", verify="npm run verify")]
    ms.criteria[0].passed = True
    ghost = relay.submit(22, "보고가 남긴 조건 줄", force=True)     # 미착수 기록

    rule_report_iter(flow, 12, {"target": st.st_id, "results": "다른 조건 | pass | exit 0"})

    assert ghost.status == "dropped", "보고 자리에서 미착수 기록이 풀리지 않는다"
    assert st.status in ("done", "superseded"), "소진된 단계가 닫히지 않았다"


def test_일감이_하나도_없는_단계도_실증된_주기에서는_닫힌다(monkeypatch):
    """[빈 단계는 닫을 방아쇠가 없다(2026-08-03, 실측 U-496)]

    소진 판정은 '일감이 하나 이상 있었고 전부 종결'을 요구한다 — 아직 분해 중인 단계를 조기에
    닫지 않으려는 조건이다. 그런데 회의가 단계 둘을 열고 일감을 한쪽에만 등재하면 빈 단계가
    남는다(실측 U-496 MS-749610899-2: 'QA·브라우저 수용 게이트' bl_total 0). 그 단계는 완료될
    일감이 없으니 방아쇠가 영영 오지 않고, 주기가 그것 하나 때문에 못 닫힌다.

    완수조건이 이미 실증된 주기에서는 '아직 분해 중'일 수 없다 — 빈 단계도 소진으로 본다.
    """
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.backlog import BacklogRelay, sweep_drained_subtasks
    from system.rule.milestone import Criterion, SubTask

    flow, ms, st, relay = _cycle()
    ms.criteria = [Criterion(desc="브라우저에서 한 판이 돌아간다", verify="npm run verify")]
    ms.criteria[0].passed = True
    done_one = relay.submit(12, "실제로 끝낸 일", force=True)
    relay.pick(12, done_one.backlog_id, 12)
    relay.done(12, done_one.backlog_id)

    empty = SubTask("MS-P/ST-2", "QA 게이트", [])      # 일감이 한 건도 등재되지 않은 단계
    ms.subtasks.append(empty)
    flow.backlog_relays[empty.st_id] = BacklogRelay(empty.st_id)

    sweep_drained_subtasks(flow)

    assert empty.status in ("done", "superseded"), "빈 단계가 주기를 영영 붙잡는다"
    assert st.status in ("done", "superseded")


def test_완수조건이_아직인_주기의_빈_단계는_건드리지_않는다(monkeypatch):
    """분해 중인 단계를 조기에 닫으면 안 된다 — 원래 조건의 취지는 그대로 지킨다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.backlog import BacklogRelay, close_subtask_if_drained
    from system.rule.milestone import SubTask

    flow, ms, st, relay = _cycle()
    empty = SubTask("MS-P/ST-9", "아직 분해 중", [])
    ms.subtasks.append(empty)
    r = BacklogRelay(empty.st_id)
    flow.backlog_relays[empty.st_id] = r

    assert close_subtask_if_drained(flow, empty, r) is False
    assert empty.status not in ("done", "superseded")


def test_쓰이지_않은_빈_단계는_마감_게이트를_막지_않는다(monkeypatch):
    """[빈 단계는 완수가 아니라 쓰이지 않은 것(2026-08-03, 실측 U-478 마감 게이트)]

    Task 마감 게이트(work_ledger_release_error)는 단위마다 백로그 1개 이상을 요구한다. 그래서
    일감이 한 건도 없는 단계를 done으로 닫으면 마감에서 "백로그 0개"로 거절돼 그 주기가 다시
    열리고 같은 바퀴를 돈다(실측: MS-755549625-3이 12:35에 그렇게 재개방됐다).

    쓰이지 않은 단위의 정직한 처분은 superseded다 — 마감 게이트도 그것만은 건너뛴다.
    """
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.backlog import BacklogRelay, sweep_drained_subtasks
    from system.rule.milestone import Criterion, SubTask, work_ledger_release_error

    flow, ms, st, relay = _cycle()
    ms.criteria = [Criterion(desc="브라우저에서 한 판이 돌아간다", verify="npm run verify")]
    ms.criteria[0].passed = True
    b = relay.submit(12, "실제로 끝낸 일", force=True)
    relay.pick(12, b.backlog_id, 12)
    relay.done(12, b.backlog_id)

    unused = SubTask("MS-P/ST-2", "쓰이지 않은 단위", [])
    ms.subtasks.append(unused)
    flow.backlog_relays[unused.st_id] = BacklogRelay(unused.st_id)

    sweep_drained_subtasks(flow)

    assert unused.status == "superseded", "빈 단계를 done으로 닫으면 마감 게이트가 다시 연다"
    ms.status = "done"
    assert work_ledger_release_error(flow) is None, (
        "쓰이지 않은 단위 때문에 Task 마감이 막힌다")


def test_증거로_실증된_단계없는_주기는_손상으로_보지_않는다(monkeypatch):
    """[실증된 주기는 손상 기록이 아니다(2026-08-03, 실측 U-478)]

    work_ledger_release_error의 취지는 그 docstring 그대로 '구·손상 체크포인트 탐지'다 — 아무 일도
    안 했는데 done으로 굳은 기록을 잡는 것. 그런데 단계 없이 닫힌 주기는 정상 경로로도 생긴다
    (주기 완수 = 조건 실증, 단계는 선택). 그것까지 손상으로 보면 이미 끝난 일에 분해 회의를 다시
    열게 된다 — 실측 MS-755549625-3('e2e 결함 1건 해소')은 12:35에 재개방된 뒤 12명 회의가 두 번
    연속 meet_gate_exhausted로 소진됐다.

    조건이 실행 증거(sys_run)로 실증된 주기는 그 검증 자체가 작업 기록이다.
    """
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.milestone import Criterion, Milestone, work_ledger_release_error

    flow, ms, st, relay = _cycle()
    b = relay.submit(12, "첫 주기의 일감", force=True)
    relay.pick(12, b.backlog_id, 12)
    relay.done(12, b.backlog_id)
    st.status = "done"
    ms.status = "done"

    repair = Milestone("MS-R", "e2e 결함 1건 해소", [])
    c = Criterion(desc="결함이 사라졌다", verify="npm run verify")
    c.passed = True
    c.evidence_source = "sys_run"
    repair.criteria = [c]
    repair.subtasks = []
    repair.status = "done"
    flow.milestones.append(repair)

    assert work_ledger_release_error(flow) is None, "실증된 주기를 손상으로 보고 다시 연다"


def test_증거_없이_done인_단계없는_주기는_여전히_잡는다(monkeypatch):
    """이 검사가 원래 잡으려던 것 — 아무 증거 없이 done으로 굳은 기록은 그대로 막는다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.milestone import Criterion, Milestone, work_ledger_release_error

    flow, ms, st, relay = _cycle()
    b = relay.submit(12, "첫 주기의 일감", force=True)
    relay.pick(12, b.backlog_id, 12)
    relay.done(12, b.backlog_id)
    st.status = "done"
    ms.status = "done"

    hollow = Milestone("MS-H", "빈 기록", [])
    c = Criterion(desc="조건", verify="npm run verify")
    c.passed = True                       # 통과 표기는 있으나 실행 증거가 없다
    hollow.criteria = [c]
    hollow.subtasks = []
    hollow.status = "done"
    flow.milestones.append(hollow)

    err = work_ledger_release_error(flow)
    assert err and "MS-H" in err
