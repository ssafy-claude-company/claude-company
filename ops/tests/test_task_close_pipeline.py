"""[S2 마감 축 재편 — 계약 테스트] PIPELINE_REWORK §5의 마감 행들 고정.

고정하는 계약:
  - 플래그 OFF: pipeline_complete=None → complete_task는 기존 리더-마감 게이트 그대로(불변).
  - 플래그 ON: 마감의 판정자 = 등록된 완수조건의 실증(evidence 필수 — iter_verify 단일 판정자).
    리더 선언·재호출로는 안 닫힌다. 조건이 다 실증되면 주기가 스스로 닫힌다.
  - SubTask 닫힘에 잔여 백로그 정리(§2 on_subtask_wrapup)가 연동된다.
  - 마지막 마일스톤 닫힘 = Task 경계 → e2e(S3)로 넘김(여기서 판정 안 함). e2e pass면 기존
    마감 의식(_finalize_done)으로 Task 완료.
  - 완수조건 iter 반복 미충족(임계 3) → deadlock_signal(kind=criteria) — 결정권자 중재.
  - 수렴 경보(교차검증 임계): OFF=종전 그대로(사용자 판정), ON=deadlock_signal(kind=cross_check)
    로 재배치 + 결정권자 중재(§5 표).
"""
import asyncio
import types

import pytest

from system.protocol import TaskStatus
from system.rule.milestone import Criterion, Milestone, SubTask
from system.rule.task import TaskRef, complete_task
from system.rule.task_pipeline import (DEADLOCK_ITERS, check_criteria_deadlock,
                                       parse_result_lines, pipeline_complete)

A, B = 11, 12


def _ns_flow(ms_list, ev=None):
    """task_pipeline 단위 검증용 최소 flow(SimpleNamespace)."""
    ev = ev if ev is not None else []
    return types.SimpleNamespace(
        milestones=ms_list, backlog_relays={},
        log=lambda e, **k: ev.append((e, k)), _info=lambda x: f"봇{x}",
        current=types.SimpleNamespace(team=[A, B], owner=B, leader_writes=0),
        leader=A, guide=None), ev


def _ms(goal="목표", n_crit=1, subtasks=0):
    ms = Milestone(ms_id="MS-1", goal=goal,
                   criteria=[Criterion(f"조건{i}", f"run 검증{i}") for i in range(1, n_crit + 1)])
    for j in range(subtasks):
        ms.subtasks.append(SubTask(st_id=f"MS-1/ST-{j+1}", goal=f"부분{j+1}",
                                   criteria=[Criterion(f"부분조건{j+1}", f"run st{j+1}")]))
    return ms


# ══ 폴백 — 플래그 OFF 불변 ══════════════════════════════════════════════════

def test_OFF는_None_폴백(monkeypatch):
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)
    f, _ = _ns_flow([_ms()])
    assert asyncio.run(pipeline_complete(f, "leader", {})) is None


def test_ON이라도_마일스톤_없으면_폴백(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f, _ = _ns_flow([])
    assert asyncio.run(pipeline_complete(f, "leader", {})) is None


# ══ ON — 조건이 마감한다 ═══════════════════════════════════════════════════

def test_ON_선언만으론_거부_미충족과_처방_나열(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f, _ = _ns_flow([_ms(n_crit=2)])
    out = asyncio.run(pipeline_complete(f, "leader", {"result": "다 했습니다"}))
    assert "마감 거부" in out and "완수조건 실증" in out
    assert "조건1" in out and "조건2" in out and "run 검증1" in out   # 무엇을·어떻게가 처방


def test_ON_증거있는_결과만_접수(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    ms = _ms(n_crit=2)
    f, _ = _ns_flow([ms])
    out = asyncio.run(pipeline_complete(f, "leader",
                                        {"criteria_results": "조건1 | pytest 12 passed\n조건2 |"}))
    assert ms.criteria[0].passed and not ms.criteria[1].passed   # 증거 없는 줄은 버려짐
    assert "마감 거부" in out and "조건2" in out
    # 나머지 조건까지 실증하면 조건이 스스로 닫는다
    out2 = asyncio.run(pipeline_complete(f, "leader", {"criteria_results": "조건2 | curl 200 확인"}))
    assert "마감" in out2 and "거부" not in out2 and ms.status == "done"


def test_ON_SubTask_잔여백로그_정리_연동(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.backlog import relay_for
    ms = _ms(n_crit=1, subtasks=1)
    f, ev = _ns_flow([ms])
    st = ms.subtasks[0]
    r = relay_for(f, st)
    r.submit(A, "프론트 카드")                    # 미착수 잔여 1건
    out = asyncio.run(pipeline_complete(f, "leader", {
        "criteria_results": "조건1 | pytest ok\n부분조건1 | run st ok"}))
    assert st.status == "done" and ms.status == "done"
    assert "잔여 1건 정리" in out and r.closed     # §2 정리 연동
    assert r.get("B1").status == "open"           # 정리 ≠ 완료 참칭


def test_ON_마지막ms_닫힘은_e2e로_넘긴다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    ms = _ms()
    f, _ = _ns_flow([ms])
    out = asyncio.run(pipeline_complete(f, "leader", {"criteria_results": "조건1 | run ok"}))
    assert ms.status == "done"
    assert "e2e" in out and "판정하지 않습니다" in out   # 훅만 부르고 판정은 S3


def test_ON_다음주기_안내(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    ms1, ms2 = _ms(), _ms()
    ms2.ms_id, ms2.goal = "MS-2", "다음 목표"
    f, _ = _ns_flow([ms1, ms2])
    out = asyncio.run(pipeline_complete(f, "leader", {"criteria_results": "조건1 | run ok"}))
    assert ms1.status == "done" and ms2.status == "open"
    assert "MS-2" in out and "조건이 닫았습니다" in out


# ══ ON — 교착(§5 재배치: 완수조건 축) ═══════════════════════════════════════

def test_ON_iter_반복_미충족은_교착신호(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    ms = _ms(n_crit=1)
    f, ev = _ns_flow([ms])
    out = ""
    for _ in range(DEADLOCK_ITERS):               # 증거 없는 시도 반복 = iter만 쌓임
        out = asyncio.run(pipeline_complete(f, "leader", {"criteria_results": "조건1 |"}))
    assert ms.iter_n == DEADLOCK_ITERS
    assert "교착 신호" in out and "결정권자 중재" in out
    assert ("deadlock_signal", ) [0] in [e for e, _ in ev]
    dl = [k for e, k in ev if e == "deadlock_signal"]
    assert dl and dl[-1]["kind"] == "criteria"


def test_교착판정_임계미만은_침묵():
    ms = _ms()
    ms.iter_n = DEADLOCK_ITERS - 1
    f, ev = _ns_flow([ms])
    assert not check_criteria_deadlock(f, ms)
    ms.iter_n = DEADLOCK_ITERS
    assert check_criteria_deadlock(f, ms) and ev[-1][0] == "deadlock_signal"


# ══ Task 경계 — e2e pass면 기존 마감 의식으로 완료 ═══════════════════════════

def test_ON_e2e_pass면_기존_마감의식으로_Task완료(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from test_sys import FakeGuide, _flow
    f = _flow(FakeGuide())
    ms = _ms()
    ms.criteria[0].passed, ms.criteria[0].evidence = True, "run ok"
    ms.status = "done"
    f.milestones = [ms]
    f.e2e_verdict = "pass"                        # S3 판정 duck-필드(§12-1 접점 코멘트)
    ref = TaskRef(task_id="t1", thread_id="th", block_id="b",
                  status=TaskStatus(task_id="t1", purpose="p", status="진행",
                                    goal="g", owner="", group=""),
                  team=[11, 12], owner=12)
    f.current = ref
    out = asyncio.run(complete_task(f, "leader", {"result": "e2e까지 통과"}))
    assert ref.status.status == "완료"             # _finalize_done이 실제로 닫음(닫히면 current는 비워짐)
    assert "Task를 닫습니다" in str(out)


def test_OFF_complete_task는_기존_게이트가_먼저_말한다(monkeypatch):
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)
    from test_sys import FakeGuide, _flow
    f = _flow(FakeGuide())
    f.current = TaskRef(task_id="t1", thread_id="th", block_id="b",
                        status=TaskStatus(task_id="t1", purpose="p", status="진행",
                                          goal="g", owner="", group=""),
                        team=[11, 12], owner=0)
    out = asyncio.run(complete_task(f, "leader", {"result": "done"}))
    assert f.current.status.status != "완료"       # 기존 세계: 게이트(run 실증 등)가 거부
    assert "run" in str(out)


# ══ 수렴 경보 → 교착 신호 재배치 (§5) ═══════════════════════════════════════

def _alert_flow():
    posts = []
    class _G:
        async def post(self, ch, sender, text, reply_to=None):
            posts.append((ch, text))
    ev = []
    f = types.SimpleNamespace(
        guide=_G(), log=lambda e, **k: ev.append((e, k)),
        current=types.SimpleNamespace(task_id="t1", cross_checks=12),
        user_channel=1, project_channel=500, leader=A, _info=lambda x: f"봇{x}")
    return f, posts, ev


def test_수렴경보_OFF는_종전그대로_사용자판정(monkeypatch):
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)
    from system.rule.communication import _escalate_convergence
    f, posts, ev = _alert_flow()
    asyncio.run(_escalate_convergence(f))
    assert ev[0][0] == "loop_circuit_breaker"
    assert posts[0][0] == 1 and "[수렴 경보 — 사람 판정 필요]" in posts[0][1]


def test_수렴경보_ON은_교착신호_결정권자_중재(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.communication import _escalate_convergence
    f, posts, ev = _alert_flow()
    asyncio.run(_escalate_convergence(f))
    assert ev[0][0] == "deadlock_signal" and ev[0][1]["kind"] == "cross_check"
    assert posts[0][0] == 500 and "결정권자" in posts[0][1] and "중재" in posts[0][1]


# ══ 보조 ═══════════════════════════════════════════════════════════════════

def test_결과줄_파싱_증거없는_줄은_버림():
    rows = parse_result_lines("- 조건1 | pytest ok\n조건2 |\n\n* 조건3 | curl 200")
    assert [r["desc"] for r in rows] == ["조건1", "조건3"]
    assert all(r["passed"] and r["evidence"] for r in rows)
