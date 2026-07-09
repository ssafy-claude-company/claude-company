"""[Rule — 마감 축 재편 (파이프라인 §5, S2)] 플래그 ON에서 마감의 판정자를 바꾼다.

§5 표의 마감 행들:
  - complete_task 리더 마감  → **완수조건 iter 검증이 마감**: 도구 표면(complete_task)은 유지하되
    (봇들이 이미 아는 문), 문 뒤의 판정을 교체한다 — 리더의 열린 판단이 아니라 등록된 완수조건의
    실증(evidence 있는 passed)만이 주기를 닫는다. 조건이 다 실증되면 주기가 스스로 닫힌다.
  - SYS 집행 마감 백스톱     → **자동 판정으로 흡수**: 기존 백스톱(sys_core)은 "complete_task를
    불러라"로 코칭하는데, ON 세계에선 그 complete_task가 곧 조건 판정이므로 백스톱은 코드 수정
    없이 자연 흡수된다(백스톱이 리더를 조건 판정으로 밀어넣는 셈).
  - 수렴 경보(교차검증 임계) → **교착 신호로 재배치**: communication._escalate_convergence 참조.
    완수조건 쪽 교착(iter 반복 미충족)은 여기(check_criteria_deadlock)가 같은 계열로 신호한다.

Task 경계: 마지막 마일스톤이 닫히면 e2e(S3)로 넘긴다 — 여기는 훅만 부르고 판정하지 않는다(§6).
플래그 OFF면 pipeline_complete가 즉시 None → 기존 리더-마감 파이프라인 그대로(라이브 불변).
"""
import os
from typing import Optional

from .backlog import on_subtask_wrapup
from .milestone import (Milestone, iter_verify, next_milestone, pipeline_on,
                        wrapup_done)

# [교착 임계 — §5 재배치·계약 §3 계열] 완수조건 검증(iter)이 이 횟수 이상 돌고도 미충족이면
# 교착 신호(deadlock_signal) → 결정권자 중재(권한 ③). 릴레이의 DEADLOCK_BLOCKS(2)와 같은 정신 —
# iter는 회의·작업을 낀 큰 주기라 한 번 더 여유를 둔다(제안값 3, env로 조정).
DEADLOCK_ITERS = int(os.environ.get("ORGANT_ITER_DEADLOCK", "3"))


def parse_result_lines(text: str) -> list:
    """봇이 제출한 실증 결과(한 줄 = '조건 | 증거') → iter_verify results 형식.
    증거가 비면 그 줄은 버려진다 — 증거 없는 passed는 어차피 iter_verify가 안 받는다(허위 차단)."""
    out = []
    for ln in str(text or "").splitlines():
        ln = ln.strip().lstrip("-•* ").strip()
        if not ln:
            continue
        d, _, ev = ln.partition("|")
        if d.strip() and ev.strip():
            out.append({"desc": d.strip(), "passed": True, "evidence": ev.strip()})
    return out


def check_criteria_deadlock(flow, obj) -> bool:
    """완수조건 교착 판정 — iter가 임계 이상 돌았는데 아직 미충족이면 deadlock_signal(§5 재배치).
    중재 라우팅(결정권자 권한 ③)은 호출부 몫 — 여기는 신호만."""
    remain = [c.desc for c in obj.criteria if not c.passed]
    if not remain or obj.iter_n < DEADLOCK_ITERS:
        return False
    if flow.log:
        oid = getattr(obj, "ms_id", None) or getattr(obj, "st_id", "")
        flow.log("deadlock_signal", kind="criteria", id=oid, iter=obj.iter_n,
                 remain=[d[:40] for d in remain])
    return True


async def _close_task_boundary(flow, args, closed_ms: Milestone) -> str:
    """마지막 마일스톤 닫힘 → Task 경계. e2e(S3)가 판정자다 — 여기는 넘기기만 한다.

    e2e 상태는 flow의 S3 필드를 duck-read(미착지·미개시=None). 통과 확정일 때만 기존 마감
    의식(_finalize_done — 상태 '완료'·장부 적립·게시)을 실행한다. 게이트는 안 탄다 — ON 세계의
    게이트는 완수조건(등록 시점 실증)과 e2e(경계 전수)로 재배치됐다(§5 표 그대로).
    """
    verdict = str(getattr(flow, "e2e_verdict", "") or "").strip().lower()
    if verdict == "pass":
        from .task_gates import _finalize_done
        third = [m for m in flow.current.team
                 if m not in (flow.leader, flow.current.owner)]
        has_product = (bool(flow.current.owner)
                       or getattr(flow.current, "leader_writes", 0) > 0)
        note = await _finalize_done(flow, flow.guide, args, third, has_product)
        return (f"마일스톤 {closed_ms.ms_id} 마감(조건 전부 실증) — 마지막 주기였고 e2e 전수도 "
                f"통과(S3 판정)라 Task를 닫습니다.\n{note}")
    return (f"마일스톤 {closed_ms.ms_id} 마감(조건 전부 실증) — **마지막 주기입니다. Task 마감은 "
            f"e2e 전수 검증(S3) 통과 후**입니다. e2e를 개시하세요(개시·판정은 e2e 표면의 몫 — "
            f"여기서 대신 판정하지 않습니다). e2e가 결함을 내면 ms_replan으로 복기 주기가 열립니다.")


async def pipeline_complete(flow, role, args) -> Optional[str]:
    """[§5 마감 축] 플래그 ON + 마일스톤 세계일 때 complete_task의 대체 판정.

    반환 None = 파이프라인 세계 아님(OFF거나 마일스톤 미사용) → 호출부가 기존 경로로 폴백.
    문자열 = ON 세계의 판정 결과(닫힘 보고 또는 거부+처방).

    판정 순서(마감은 조건이, 진행은 주기가):
      1) 제출된 실증(criteria_results)을 iter_verify로 접수 — 판정자는 S1의 그 함수 하나
         (evidence 없는 passed는 안 받는다. 과도기 표면 — S1 iter 구동부가 서면 그쪽이 정식).
      2) 조건 전부 실증된 SubTask → 잔여 백로그 정리(on_subtask_wrapup, §2) → done.
      3) 마일스톤 조건 전부 실증 + SubTask 전부 done → 마일스톤 done → 다음 주기 안내.
         마지막 마일스톤이면 Task 경계(e2e, S3)로.
      4) 미충족 → 거부 + 무엇이 남았는지·어떻게 실증하는지(verify 절차) 처방. 임계 초과면
         교착 신호(결정권자 중재).
    """
    if not pipeline_on() or not getattr(flow, "milestones", None):
        return None
    ms = next_milestone(flow)
    if ms is None:
        # 마일스톤이 전부 done인데 complete가 또 불림 — Task 경계 안내만(중복 닫기 방지).
        return await _close_task_boundary(flow, args, flow.milestones[-1])

    # 마감 시도 = iter 1회다(결과가 비어도) — 증거 없는 반복 시도가 iter_n으로 쌓여 교착 검출의
    # 분모가 된다. 접수는 iter_verify(S1) 하나가 판정: evidence 없는 passed는 어차피 안 받는다.
    results = parse_result_lines(args.get("criteria_results") or args.get("results") or "")
    iter_verify(flow, ms, results)
    for st in ms.subtasks:
        if st.status == "open":
            iter_verify(flow, st, results)

    notes = []
    for st in ms.subtasks:
        if st.status == "open" and all(c.passed for c in st.criteria):
            st.status = "wrapup"
        if st.status == "wrapup":
            sweep = on_subtask_wrapup(flow, st)          # §2 잔여 백로그 정리 + 장부(§9 미러)
            wrapup_done(flow, st)
            notes.append(f"SubTask {st.st_id} 닫힘 — {sweep}")

    ms_remain = [c.desc for c in ms.criteria if not c.passed]
    st_open = [st.st_id for st in ms.subtasks if st.status != "done"]
    if not ms_remain and not st_open:
        ms.status = "wrapup"
        wrapup_done(flow, ms)
        head = ("\n".join(notes) + "\n") if notes else ""
        nxt = next_milestone(flow)
        if nxt is None:
            return head + await _close_task_boundary(flow, args, ms)
        return (head + f"마일스톤 {ms.ms_id} 마감 — 완수조건 전부 실증됐습니다(리더 판단이 아니라 "
                       f"조건이 닫았습니다). 다음 주기: {nxt.ms_id} — {nxt.goal[:60]}")

    # 미충족 — 거부(무엇이·어떻게가 처방의 전부) + 교착 검사
    deadlock = check_criteria_deadlock(flow, ms)
    head = ("\n".join(notes) + "\n") if notes else ""
    lines = [f"- {c.desc[:60]} → 실증: {c.verify[:80]}" for c in ms.criteria if not c.passed]
    tail = ""
    if st_open and not ms_remain:
        tail = f"\n(마일스톤 조건은 실증됐으나 SubTask 미완: {', '.join(st_open)})"
    dl = ""
    if deadlock:
        dl = (f"\n\n[교착 신호 — iter {ms.iter_n}회에도 미충족(§5 재배치)] 반복 실증 시도가 수렴하지 "
              f"않습니다. 결정권자 중재(권한 ③) 사안입니다 — ① 조건 재수립 회의(달성 불가 조건의 "
              f"교체·완화) 또는 ② 방향 전환을 결정권자가 확정하세요.")
    return (head + f"마감 거부 — 마감은 선언이 아니라 **완수조건 실증**입니다(§5). "
                   f"미충족 {len(ms_remain)}건:\n" + "\n".join(lines)
                 + f"\n각 조건의 verify 절차를 run으로 실증하고, 그 증거를 "
                   f"criteria_results('조건 | 증거' 한 줄씩)로 제출하세요. 증거 없는 통과 주장은 "
                   f"접수되지 않습니다.{tail}{dl}")
