"""[Rule — 마일스톤 파이프라인, S1 구조층] PIPELINE_REWORK_2026-07-09 §1·§2·§6의 구현.

리더(중앙) 해체의 구조 절반: **진행은 주기(iter)가, 마감은 완수조건이** 맡는다.
- Milestone: 목표+완수조건으로 Task를 큰 주기로 나눈다. SubTask들이 그 안에서 단계적으로 처리된다.
- iter 검증: 조건 충족 → 잔여 정리(wrapup) → 다음 주기. 조건이 주기를 닫는다(사람이 아니라).
- 등록 게이트: run으로 실증 가능한 조건만 등록된다(소망형 금지 — 기존 _gate_verified·acceptance의
  '실증' 요건을 완료 시점→등록 시점으로 재배치, 계약 §2 확정).
- ms_replan: e2e 전수 실패(S3) → 결함으로 새 마일스톤을 여는 복기 진입점(계약 §6).

플래그 이중수용(계약 §12): `ORGANT_PIPELINE=milestone`일 때만 라이브 경로가 이 모듈을 탄다 —
미설정이면 기존 파이프라인 동작 불변. 저장은 최대 저장(계약 §9): 모든 상태가 to_dict/from_dict로
체크포인트에 동승하고, 전이마다 flow.log 이벤트로 재구축 가능해야 한다.
"""
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

__all__ = [
    "pipeline_on", "Criterion", "SubTask", "Milestone",
    "gate_criteria", "open_milestone", "open_subtask",
    "iter_verify", "wrapup_done", "next_milestone", "ms_replan",
    "ms_to_dict", "ms_from_dict",
    "parse_criteria_lines", "rule_set_milestone", "rule_set_subtask",
    "parse_iter_results", "rule_report_iter",
]


def pipeline_on(explicit: Optional[str] = None) -> bool:
    """마일스톤 파이프라인 스위치 — floor_mode 관례 동형(env 1곳, 호출부는 이 함수만 본다).
    미설정=False(기존 동작 불변)가 안전값."""
    v = (explicit if explicit is not None else os.environ.get("ORGANT_PIPELINE") or "").strip().lower()
    return v == "milestone"


# ── 개체 (계약 §1) ─────────────────────────────────────────────────────────────

@dataclass
class Criterion:
    """완수조건 한 항목. verify가 실증 절차(run으로 확인 가능한 명령/절차) — 등록 게이트가 형태를 강제."""
    desc: str
    verify: str
    passed: bool = False
    evidence: str = ""     # 마지막 검증 영수증(run 출력 요지) — 허위 충족 차단의 원자료


@dataclass
class SubTask:
    """마일스톤 안의 단위. 인원은 자발 참여(§1 — participants는 S2 릴레이가 채운다)."""
    st_id: str
    goal: str
    criteria: List[Criterion]
    participants: set = field(default_factory=set)
    backlog_ids: list = field(default_factory=list)   # Backlog 표현은 S2 소유 — 여기는 연결 id만
    status: str = "open"        # open → wrapup(조건 충족·잔여 정리) → done
    iter_n: int = 0


@dataclass
class Milestone:
    ms_id: str
    goal: str
    criteria: List[Criterion]
    subtasks: List[SubTask] = field(default_factory=list)
    status: str = "open"        # open → wrapup → done
    iter_n: int = 0
    origin: str = ""            # 이 마일스톤을 낳은 것 — 사용자 원문 or e2e 결함(복기)


# ── 완수조건 등록 게이트 (계약 §2) ──────────────────────────────────────────────

# 소망형(검증 불가) 조건의 전형 — 등록에서 거부해 '조건 권력'의 부실화를 막는다(설계 검토 갭 1).
_WISHFUL = ("잘 동작", "잘 작동", "완벽", "훌륭", "만족스럽", "좋아야", "문제없", "이상 없")


def gate_criteria(entries) -> Optional[str]:
    """완수조건 등록 게이트 — 에러 문자열(거부 사유+처방) 또는 None(통과).
    형태 요건: desc(무엇이 충족인가) + verify(run으로 실증 가능한 절차) 둘 다. 소망형 desc 거부."""
    items = list(entries or [])
    if not items:
        return "완수조건이 비어 있습니다 — 최소 1개. 각 항목은 {desc, verify}."
    seen = set()
    for e in items:
        d = str((e.get("desc") if isinstance(e, dict) else getattr(e, "desc", "")) or "").strip()
        v = str((e.get("verify") if isinstance(e, dict) else getattr(e, "verify", "")) or "").strip()
        if not d:
            return "조건에 desc(무엇이 충족인가)가 없습니다."
        if any(w in d for w in _WISHFUL) and not v:
            return (f"조건 '{d[:40]}'은(는) 소망형입니다 — 측정 가능한 문장으로 바꾸고 "
                    f"verify(실증 절차)를 붙이세요.")
        if not v:
            return (f"조건 '{d[:40]}'에 verify(실증 절차)가 없습니다 — run으로 확인 가능한 "
                    f"명령/절차를 적으세요(예: curl로 상태코드, pytest 파일, 브라우저 로드 확인).")
        key = d.lower()
        if key in seen:
            return f"조건 '{d[:40]}'이 중복입니다 — 합치거나 구체화하세요."
        seen.add(key)
    return None


# ── 생성 ───────────────────────────────────────────────────────────────────────

def _mk_criteria(entries) -> List[Criterion]:
    return [Criterion(desc=str(e["desc"]).strip(), verify=str(e["verify"]).strip())
            if isinstance(e, dict) else e for e in entries]


def _ckpt(flow):
    """Task 체크포인트 관례 동형(task_gates._ckpt) — 콜백은 SYS가 주입, 미주입이면 무해."""
    fn = getattr(flow, "checkpoint_task", None)
    if fn:
        try:
            fn()
        except Exception:
            pass


def open_milestone(flow, goal: str, criteria_entries, origin: str = ""):
    """마일스톤 개설. 게이트 통과 시 flow.milestones에 적재·체크포인트·이벤트. 실패 시 에러 문자열."""
    err = gate_criteria(criteria_entries)
    if err:
        return err
    ms = Milestone(ms_id=f"MS-{int(time.time() * 1000) % 10**9}-{len(flow.milestones) + 1}",
                   goal=str(goal or "").strip(), criteria=_mk_criteria(criteria_entries),
                   origin=str(origin or "").strip())
    flow.milestones.append(ms)
    _ckpt(flow)
    if flow.log:
        flow.log("ms_open", ms=ms.ms_id, goal=ms.goal[:80], criteria=len(ms.criteria),
                 replan=bool(origin.startswith("e2e:")))
    return ms


def open_subtask(flow, ms: Milestone, goal: str, criteria_entries):
    """SubTask 개설 — 주기 중에도 추가 허용(계약 §2 확정)."""
    err = gate_criteria(criteria_entries)
    if err:
        return err
    st = SubTask(st_id=f"{ms.ms_id}/ST-{len(ms.subtasks) + 1}",
                 goal=str(goal or "").strip(), criteria=_mk_criteria(criteria_entries))
    ms.subtasks.append(st)
    _ckpt(flow)
    if flow.log:
        flow.log("subtask_open", ms=ms.ms_id, st=st.st_id, goal=st.goal[:80])
    return st


# ── iter 검증 (계약 §2) ────────────────────────────────────────────────────────

def iter_verify(flow, obj, results):
    """한 iter의 완수조건 검증. results = [{desc, passed, evidence}] — 봇들이 run으로 실증한 결과.

    - evidence 없는 passed는 인정하지 않는다(기존 '실행 증거' 원칙 그대로 — 허위 충족 차단).
    - 전 조건 충족 → status=wrapup(잔여 정리 모드 — 남은 SubTask/백로그를 마저 정리하고 넘어간다,
      계약 §2 확정: "다 해결되지 않았더라도 완수조건 완수 시 남은 것 해결 후 다음 주기").
    - 미충족 → open 유지, 미충족 목록 반환(다음 iter의 일감).
    반환: (passed_all: bool, note: str)."""
    obj.iter_n += 1
    by_desc = {c.desc: c for c in obj.criteria}
    for r in (results or []):
        d = str(r.get("desc") or "").strip()
        c = by_desc.get(d)
        if c is None:
            continue
        ev = str(r.get("evidence") or "").strip()
        if bool(r.get("passed")) and ev:
            c.passed, c.evidence = True, ev[:400]
    remain = [c.desc for c in obj.criteria if not c.passed]
    kind = "ms" if isinstance(obj, Milestone) else "st"
    oid = getattr(obj, "ms_id", None) or getattr(obj, "st_id", "")
    if flow.log:
        flow.log("ms_iter_verify", kind=kind, id=oid, iter=obj.iter_n,
                 passed=len(obj.criteria) - len(remain), total=len(obj.criteria))
    if not remain:
        obj.status = "wrapup"
        if flow.log:
            flow.log("ms_iter_pass", kind=kind, id=oid, iter=obj.iter_n)
        _ckpt(flow)
        return True, "완수조건 전부 충족 — 잔여 정리(wrapup) 후 다음 주기로."
    _ckpt(flow)
    return False, "미충족: " + " · ".join(d[:40] for d in remain)


def wrapup_done(flow, obj) -> str:
    """잔여 정리 완료 선언 → done. wrapup 상태에서만 유효(조건 미충족 상태의 건너뛰기 차단)."""
    if obj.status != "wrapup":
        return "정리 완료 선언 불가: 아직 완수조건 검증(iter_verify)을 통과하지 않았습니다."
    obj.status = "done"
    _ckpt(flow)
    if flow.log:
        flow.log("ms_done" if isinstance(obj, Milestone) else "subtask_done",
                 id=getattr(obj, "ms_id", None) or getattr(obj, "st_id", ""))
    return "done"


def next_milestone(flow) -> Optional[Milestone]:
    """다음 진행 대상 — 미완(done 아님) 첫 마일스톤. 진행을 사람이 아니라 주기가 관할한다."""
    for ms in flow.milestones:
        if ms.status != "done":
            return ms
    return None


# ── 복기 진입점 (계약 §6 — S3의 e2e_fail이 호출) ───────────────────────────────

def ms_replan(flow, defects) -> Optional[Milestone]:
    """e2e 전수 실패 → 결함 목록으로 새 마일스톤을 연다. 조건 초안은 결함의 부정형(각 결함 해소를
    조건으로) — **확정은 회의 몫**(조건 결정은 turn-taking 회의, 계약 §4). 여기는 진입점만."""
    ds = [str(d).strip() for d in (defects or []) if str(d).strip()]
    if not ds:
        return None
    entries = [{"desc": f"결함 해소: {d[:80]}",
                "verify": f"결함 재현 절차를 재실행해 미재현 확인: {d[:120]}"} for d in ds]
    ms = open_milestone(flow, goal=f"e2e 결함 {len(ds)}건 해소", criteria_entries=entries,
                        origin="e2e:" + " | ".join(d[:60] for d in ds)[:400])
    if isinstance(ms, str):        # 게이트 거부(이론상 없음 — 방어)
        return None
    if flow.log:
        flow.log("ms_replan", ms=ms.ms_id, defects=len(ds))
    return ms


# ── 봇 도구 표면 (계약 §4 — 회의 수렴을 결정권자가 확정해 주기로 만든다) ─────────

def parse_criteria_lines(text: str):
    """봇이 쓴 조건 텍스트(한 줄 = '조건 | 실증절차')를 게이트 입력으로. 형식 오류는 게이트가 잡는다."""
    out = []
    for ln in str(text or "").splitlines():
        ln = ln.strip().lstrip("-•* ").strip()
        if not ln:
            continue
        d, _, v = ln.partition("|")
        out.append({"desc": d.strip(), "verify": v.strip()})
    return out


def _is_decider(flow, me_id) -> bool:
    """결정권자 판정 — 플래그 ON 세계에서 흐름의 To 수신자(구 리더 자리)가 결정권자로 축소 승계된다
    (계약 §1: 권한은 수렴 확정·동률·교착 3개뿐 — 이 도구는 그중 '확정'의 표면이다)."""
    return int(me_id) == int(getattr(flow, "leader", 0) or 0)


def rule_set_milestone(flow, me_id, args) -> str:
    """[결정권자 전용] 회의 수렴을 확정해 마일스톤 개설. 게이트 거부는 사유+처방을 그대로 반환."""
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    if not _is_decider(flow, me_id):
        return ("등록 거부: 마일스톤 확정은 결정권자의 몫입니다 — 회의(meet)에서 조건을 수렴한 뒤 "
                "결정권자가 등록합니다(당신은 회의에서 의견·응찰로 참여하세요).")
    goal = str(args.get("goal") or "").strip()
    if not goal:
        return "등록 거부: goal(이 주기의 목표 한 줄)이 비었습니다."
    entries = parse_criteria_lines(args.get("criteria"))
    ms = open_milestone(flow, goal, entries, origin=str(args.get("origin") or ""))
    if isinstance(ms, str):
        return f"등록 거부: {ms}"
    return (f"마일스톤 {ms.ms_id} 개설 — 목표: {goal[:60]} / 완수조건 {len(ms.criteria)}개. "
            f"조건 충족(iter 검증)이 이 주기를 닫습니다. SubTask는 set_subtask로 추가하세요.")


def rule_set_subtask(flow, me_id, args) -> str:
    """진행 중 마일스톤에 SubTask 추가 — 주기 중에도 허용(계약 §2). 등록 게이트는 동일."""
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    ms = next_milestone(flow)
    if ms is None:
        return "추가 불가: 진행 중인 마일스톤이 없습니다 — set_milestone으로 주기를 먼저 여세요."
    goal = str(args.get("goal") or "").strip()
    if not goal:
        return "등록 거부: goal이 비었습니다."
    st = open_subtask(flow, ms, goal, parse_criteria_lines(args.get("criteria")))
    if isinstance(st, str):
        return f"등록 거부: {st}"
    return (f"SubTask {st.st_id} 추가 — {goal[:60]} (마일스톤 {ms.ms_id}). "
            f"참여는 자발입니다 — 백로그 제출로 참여하세요.")


def parse_iter_results(text: str):
    """검증자가 쓴 결과 텍스트(한 줄 = '조건 | pass/fail | 증거')를 iter_verify 입력으로."""
    out = []
    for ln in str(text or "").splitlines():
        ln = ln.strip().lstrip("-•* ").strip()
        if not ln:
            continue
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) < 2:
            continue
        out.append({"desc": parts[0],
                    "passed": parts[1].lower() in ("pass", "passed", "ok", "충족", "통과", "y", "true"),
                    "evidence": parts[2] if len(parts) > 2 else ""})
    return out


def _find_subtask(ms: Milestone, key):
    """target 문자열(st_id 전체·꼬리·goal 부분일치)로 SubTask를 찾는다 — 봇이 정확한 id를 몰라도 되게."""
    k = str(key or "").strip()
    if not k:
        return None
    for s in ms.subtasks:
        if s.st_id == k or s.st_id.endswith(k) or k in s.goal:
            return s
    return None


def rule_report_iter(flow, me_id, args) -> str:
    """[iter 검증 제출 — 누구나(검증 참여자)] 진행 중 주기의 완수조건 실증 결과를 제출한다.
    조건이 전부 실증되면 주기가 스스로 wrapup으로 넘어간다 — 마감은 사람이 아니라 조건.
    target을 주면 그 SubTask의 iter 검증(통과 시 S2 잔여 백로그 정리 훅 → 자동 종료 — §12-1 접점).
    wrapup='done'은 마일스톤 잔여 정리 완료 선언(wrapup 상태에서만)."""
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    ms = next_milestone(flow)
    if ms is None:
        return "검증할 주기가 없습니다 — set_milestone으로 주기를 먼저 여세요."
    tgt = _find_subtask(ms, args.get("target"))
    if str(args.get("target") or "").strip() and tgt is None:
        return (f"대상 SubTask를 못 찾았습니다: {str(args.get('target'))[:40]} — "
                f"현재 주기의 SubTask: {', '.join(s.st_id for s in ms.subtasks) or '(없음)'}")
    if str(args.get("wrapup") or "").strip().lower() in ("done", "완료"):
        r = wrapup_done(flow, ms)
        if r != "done":
            return r
        nxt = next_milestone(flow)
        return (f"주기 {ms.ms_id} 종료. " + (f"다음 주기: {nxt.ms_id} — {nxt.goal[:60]}" if nxt
                                             else "남은 주기 없음 — Task 경계(e2e 전수)로."))
    results = parse_iter_results(args.get("results"))
    if not results:
        return "results가 비었습니다 — 한 줄에 '조건 | pass/fail | 증거(run 출력 요지)'."
    obj = tgt or ms
    ok, note = iter_verify(flow, obj, results)
    if ok and tgt is not None:
        # [S2 접점(§12-1)] SubTask 조건 충족 → 잔여 백로그 정리 훅(wrapup_done **앞**) → 자동 종료.
        # 지역 import — 모듈 의존은 backlog→milestone 단방향 유지(여기는 호출 시점 결합만).
        try:
            from .backlog import on_subtask_wrapup
            _sweep = on_subtask_wrapup(flow, tgt)
        except Exception as e:
            _sweep = f"(잔여 정리 훅 실패 — 수동 정리 필요: {str(e)[:60]})"
        wrapup_done(flow, tgt)
        return f"SubTask {tgt.st_id} iter {tgt.iter_n} 통과 — 종료. {_sweep}"
    if ok:
        return (f"iter {ms.iter_n} 통과 — 주기 {ms.ms_id}가 wrapup(잔여 정리)로 전이. "
                f"남은 SubTask·백로그를 정리한 뒤 report_iter(wrapup='done')로 닫으세요.")
    kind = f"SubTask {tgt.st_id}" if tgt is not None else f"주기 {ms.ms_id}"
    return f"{kind} iter {obj.iter_n} — {note}. 증거 없는 pass는 인정되지 않습니다."


# ── 직렬화 (계약 §9 — 최대 저장: 체크포인트 동승·재시작 후 중간 재개) ─────────────

def ms_to_dict(ms: Milestone) -> dict:
    return {"ms_id": ms.ms_id, "goal": ms.goal, "status": ms.status, "iter_n": ms.iter_n,
            "origin": ms.origin,
            "criteria": [{"desc": c.desc, "verify": c.verify, "passed": c.passed,
                          "evidence": c.evidence} for c in ms.criteria],
            "subtasks": [{"st_id": s.st_id, "goal": s.goal, "status": s.status,
                          "iter_n": s.iter_n, "participants": sorted(int(p) for p in s.participants),
                          "backlog_ids": list(s.backlog_ids),
                          "criteria": [{"desc": c.desc, "verify": c.verify, "passed": c.passed,
                                        "evidence": c.evidence} for c in s.criteria]}
                         for s in ms.subtasks]}


def ms_from_dict(d: dict) -> Milestone:
    def _crit(rows):
        return [Criterion(desc=str(r.get("desc") or ""), verify=str(r.get("verify") or ""),
                          passed=bool(r.get("passed")), evidence=str(r.get("evidence") or ""))
                for r in (rows or [])]
    ms = Milestone(ms_id=str(d.get("ms_id") or ""), goal=str(d.get("goal") or ""),
                   criteria=_crit(d.get("criteria")), status=str(d.get("status") or "open"),
                   iter_n=int(d.get("iter_n") or 0), origin=str(d.get("origin") or ""))
    for s in (d.get("subtasks") or []):
        ms.subtasks.append(SubTask(
            st_id=str(s.get("st_id") or ""), goal=str(s.get("goal") or ""),
            criteria=_crit(s.get("criteria")), participants=set(int(p) for p in (s.get("participants") or [])),
            backlog_ids=list(s.get("backlog_ids") or []), status=str(s.get("status") or "open"),
            iter_n=int(s.get("iter_n") or 0)))
    return ms
