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
import json
import os
import re
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
    "renegotiate_criterion", "approve_waiver", "rule_renegotiate",
    "extract_consensus",
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
    # [조건 재협상 — 설계 검토 #1(2026-07-09)] 조건 자체가 환경상 달성 불가일 때의 출구. active →
    # (결정권자 renegotiate) blocked_pending → (사람 승인) waived. waived 조건은 iter 검증에서
    # '충족'처럼 제외된다(포기가 명시적으로 기록되고 사람이 승인 — churn을 무한 반복하지 않는다).
    # [이월(2026-07-20, 사용자: '개입 최대한 줄여')] 재협상의 1차 해소는 이제 '다음 주기 이월' —
    # 상태가 아니라 이동이다(조건 객체가 이 목록에서 빠져 Milestone.carried로 옮겨지고, 다음
    # open_milestone이 기계로 합류시킴). blocked_pending(사람 대기)은 못 옮길 때만의 최후수단.
    status: str = "active"   # active | blocked_pending | waived
    block_reason: str = ""   # 왜 불가능한가(인프라 제약 등) — 사람 승인 판단의 근거


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
    iter_stuck: int = 0         # [조건 불가능 출구 #1] 진전 없는 연속 미충족 iter 수(임계 도달=재협상 안내)


@dataclass
class Milestone:
    ms_id: str
    goal: str
    criteria: List[Criterion]
    subtasks: List[SubTask] = field(default_factory=list)
    status: str = "open"        # open → wrapup → done
    iter_n: int = 0
    iter_stuck: int = 0         # [조건 불가능 출구 #1] 진전 없는 연속 미충족 iter 수
    origin: str = ""            # 이 마일스톤을 낳은 것 — 사용자 원문 or e2e 결함(복기)
    # [이월 원장(2026-07-20)] 이 주기가 '다음 주기로 이월'한 조건({desc, verify, reason}) —
    # 다음 open_milestone이 소비해 새 주기 잣대로 합류시킨다(잣대를 버리는 게 아니라 옮김).
    carried: list = field(default_factory=list)


# ── 완수조건 등록 게이트 (계약 §2) ──────────────────────────────────────────────

# 소망형(검증 불가) 조건의 전형 — 등록에서 거부해 '조건 권력'의 부실화를 막는다(설계 검토 갭 1).
_WISHFUL = ("잘 동작", "잘 작동", "완벽", "훌륭", "만족스럽", "좋아야", "문제없", "이상 없")


def gate_criteria(entries) -> Optional[str]:
    """완수조건 등록 게이트 — 에러 문자열(거부 사유+처방) 또는 None(통과).
    형태 요건: desc(무엇이 충족인가) + verify(run으로 실증 가능한 절차) 둘 다. 소망형 desc 거부."""
    items = list(entries or [])
    if not items:
        return ("완수조건이 비어 있습니다 — 결정 구획에 '- <조건> | 실증: <run으로 확인하는 절차>' "
                "형식 줄이 최소 1개 필요합니다(산문 속 '실증:'은 집계 안 됨 — 줄 단위 '| 실증:' 구분자).")
    seen = set()
    # [일괄 보고(2026-07-17, ch78 실측: 등록 거부가 첫 불량 1건만 보고 → 사이클당 1개씩 수리, 6~9분×N)]
    # fail-fast 대신 전 조건을 검사해 불량을 모두 모아 반환 — 한 사이클에 전부 고치게.
    errs = []
    for e in items:
        d = str((e.get("desc") if isinstance(e, dict) else getattr(e, "desc", "")) or "").strip()
        v = str((e.get("verify") if isinstance(e, dict) else getattr(e, "verify", "")) or "").strip()
        if not d:
            errs.append("조건에 desc(무엇이 충족인가)가 없습니다.")
            continue
        if any(w in d for w in _WISHFUL) and not v:
            errs.append(f"조건 '{d[:40]}'은(는) 소망형입니다 — 측정 가능한 문장으로 바꾸고 "
                        f"verify(실증 절차)를 붙이세요.")
            continue
        if not v:
            errs.append(f"조건 '{d[:40]}'에 verify(실증 절차)가 없습니다 — run으로 확인 가능한 "
                        f"명령/절차를 적으세요(예: curl로 상태코드, pytest 파일, 브라우저 로드 확인).")
            continue
        # [등록 게이트 강화 — 설계 검토 #4(2026-07-09)] verify가 '확인함' 같은 빈 서술이면 churn을
        # 등록 단계로 옮길 뿐. 실행 가능 신호(명령 토큰) 또는 측정 신호(수치·비교)를 최소 1개 요구한다.
        if not _verify_is_executable(v):
            errs.append(f"조건 '{d[:40]}'의 verify가 실행 가능한 형태가 아닙니다: '{v[:40]}' — "
                        f"run으로 돌릴 명령(curl/pytest/npm/python/grep/localhost/포트/파일경로 등)이나 "
                        f"측정 기준(수치·= > < %·회·초·개)을 넣으세요. '확인한다'류 서술은 불가.")
            continue
        # [로드맵 오용 차단(2026-07-13, 라이브 U-015: 조건에 M0~M3 로드맵)] 조건은 검증 단위지
        # 하위 마일스톤이 아니다 — 주기 로드맵은 마일스톤 여러 개(계획 큐잉)로.
        import re as _re
        if _re.match(r"^M\d+\b", d) or "owner=" in d:
            errs.append(f"조건 '{d[:40]}'은(는) 로드맵/배정 표기입니다 — 완수조건은 '검증 가능한 사실' 단위입니다. "
                        f"주기 계획(M0·M1…)은 set_milestone을 여러 번 불러 마일스톤으로 큐잉하고, 담당은 백로그 배분으로 정하세요.")
            continue
        key = d.lower()
        if key in seen:
            errs.append(f"조건 '{d[:40]}'이 중복입니다 — 합치거나 구체화하세요.")
            continue
        seen.add(key)
    if not errs:
        return None
    _more = f" (외 {len(errs) - 6}건)" if len(errs) > 6 else ""
    return f"불량 조건 {len(errs)}건 — 전부 고치세요:\n" + "\n".join(f"{i+1}. {m}" for i, m in enumerate(errs[:6])) + _more


# 실행 가능 신호(명령 토큰) — 하나라도 있으면 run으로 돌릴 수 있는 verify로 본다(도메인 중립).
_EXEC_TOKENS = ("curl", "http", "pytest", "npm", "node", "python", "grep", "test", "localhost",
                "127.0.0.1", "run ", "./", "manage.py", "playwright", "jest", "build", "GET ",
                "POST ", "status", "assert", "diff", "cat ", "ls ", "wget", ":300", ":800", ":500")
# 측정 신호 — 비교·수치·단위(정량 기준). 정규식 대신 부분 문자열로 가볍게.
_MEASURE_TOKENS = ("=", ">", "<", "%", "회", "초", "개", "번", "1", "2", "3", "4", "5",
                   "6", "7", "8", "9", "0", "코드 2", "200", "404", "이상", "이하", "일치")


def _verify_is_executable(v: str) -> bool:
    """verify 문자열이 '실행 가능한 명령'이나 '측정 기준'을 담는가 — 빈 서술('확인한다') 차단."""
    t = str(v or "").lower()
    return (any(tok.lower() in t for tok in _EXEC_TOKENS)
            or any(tok in v for tok in _MEASURE_TOKENS))


# ── 생성 ───────────────────────────────────────────────────────────────────────

def _mk_criteria(entries) -> List[Criterion]:
    out = []
    for e in entries:
        if not isinstance(e, dict):
            out.append(e)
            continue
        d, v = str(e["desc"]).strip(), str(e["verify"]).strip()
        # [desc 정규화(2026-07-12)] '1'·'2' 같은 무의미 desc는 어떤 표면에도 못 그려지고
        # iter 결과 매칭도 못 받는다(ch53 9차 공회전) — verify 요지를 desc로 승격.
        if v and (d.isdigit() or len(d) < 4):
            d = re.sub(r"^(run|read|grep)\s*:\s*", "", v, flags=re.I)[:60]
        out.append(Criterion(desc=d, verify=v))
    return out


def _ckpt(flow):
    """Task 체크포인트 관례 동형(task_gates._ckpt) — 콜백은 SYS가 주입, 미주입이면 무해."""
    fn = getattr(flow, "checkpoint_task", None)
    if fn:
        try:
            fn()
        except Exception:
            pass
    persist_ms_status(flow)   # [표면 미러] 모든 마일스톤 변이가 이 깔때기를 지난다 — 여기 한 곳만 훅
    _set_pipeline_ctx(flow)   # [소속 컨텍스트] 이후 이 흐름의 게시가 현재 ms/st ID를 달고 나간다


def _set_pipeline_ctx(flow, me_id=None):
    try:
        from system.protocol import PIPELINE_CTX
        ms = next((m for m in (getattr(flow, "milestones", None) or []) if m.status not in ("done", "superseded")), None)
        st = next((s for s in ms.subtasks if s.status != "done"), None) if ms else None
        bl = None
        # [백로그 단위 태깅(2026-07-10, 사용자: '텍스트가 백로그 단위로')] 이 봇이 지금 물고 있는
        # (in_progress·assignee=me) 백로그 id — 발언이 백로그 항목 밑으로 묶이는 근거.
        if st is not None and me_id is not None:
            r = (getattr(flow, "backlog_relays", None) or {}).get(st.st_id)
            for b in (getattr(r, "backlogs", None) or []):
                if b.status == "in_progress" and int(b.assignee or 0) == int(me_id):
                    bl = b.backlog_id
                    break
        # [전역 회의는 주기 소속(2026-07-21, 사용자: '전 서브태스크를 한번에 만드는 회의라면 공통
        # 흐름 하위에 둬야지')] 단계 회의(목표·주기·단위·백로그)는 특정 SubTask의 일이 아니라 주기
        # 전체의 결정인데, 종전엔 '첫 미완 SubTask'를 무조건 태깅해 화면이 전역 회의를 그 단계 폴더
        # 밑으로 접었다(U-037 실측: 백로그 회의가 ST-1 아래). 단계 회의 동안은 ms까지만 태깅한다.
        if getattr(flow, "_stage_meeting", None):
            st, bl = None, None
        PIPELINE_CTX.set({"ms": ms.ms_id, "st": (st.st_id if st else None), "bl": bl} if ms else None)
    except Exception:
        pass


def _cnt_active(criteria):
    """(충족 수, 유효 수) — waived(사람 승인 포기)는 분모에서 제외(iter_verify와 같은 셈법)."""
    act = [c for c in criteria if c.status != "waived"]
    return sum(1 for c in act if c.passed), len(act)


def ms_status_snapshot(flow):
    """[표면 — BACKLOG H] 진행 중 마일스톤의 압축 현황. murmur HUD가 그대로 그리는 모양.
    미완 마일스톤이 없으면 None(완료 = 표면에서 내려감)."""
    # [이력형 미러(2026-07-10, 사용자: '백로그 왜 다 없앴어')] 활성 하나만 담던 것을 전 마일스톤으로 —
    # 완수 주기의 조건·백로그·증거가 표면에서 내려가지 않는다(복기 주기와 공존).
    all_ms = list(getattr(flow, "milestones", []) or [])
    if not all_ms:
        return None
    out_list = []
    relays = getattr(flow, "backlog_relays", None) or {}
    for ms in all_ms[-6:]:
        out_list.append(_ms_one(flow, ms, relays))
    cur = next((m for m in reversed(out_list) if m["status"] not in ("done", "superseded")), out_list[-1])
    # [사람 대기 표면화(2026-07-18, 감사)] 조건 재협상으로 사람 승인 대기 중이면 HUD에 노출 — 교착이
    # 조용히 지속되지 않게(사람이 개입해야 할 지점이 드러난다). 플래그(awaiting_human)는 휘발 캐시라
    # 재시작이면 비는데, 조건 상태(blocked_pending)는 체크포인트를 넘으므로 거기서 파생한다(검수 수리).
    _aw = getattr(flow, "awaiting_human", None)
    if not _aw:
        _pw = pending_waivers(flow)
        if _pw:
            _aw = (f"조건 재협상 대기: '{_pw[0].desc[:40]}'"
                   + (f" — {_pw[0].block_reason[:80]}" if getattr(_pw[0], "block_reason", "") else "")
                   + " (채널에 '조건 승인' 또는 '조건 반려'로 답해주세요)")
    return {**cur, "list": out_list, "ts": time.time(), "awaiting_human": (str(_aw)[:200] if _aw else None)}


def _ms_one(flow, ms, relays):
    met, total = _cnt_active(ms.criteria)
    sts = []
    for st in ms.subtasks[:12]:
        st_met, st_total = _cnt_active(st.criteria)
        # [백로그 표면화(2026-07-10, 사용자: '서브태스크마다 쌓아둔 백로그도 보여야지')] 릴레이 장부를
        # 상태로 미러 — 피드가 단계 폴더에 B1✓·B2▶담당… 칩으로 그린다(채팅 행 아님).
        bl = []
        r = relays.get(st.st_id)
        for b in (getattr(r, "backlogs", None) or [])[:12]:
            try:
                _fmt = getattr(flow, "_info", None) or (lambda x: "")
                a = str(_fmt(b.assignee) or "") if b.assignee else ""
            except Exception:
                a = ""
            # [주인 표면화(2026-07-18, 사용자: '백로그가 누구껀지 안 뜨는데')] 발제자(submitter)=주인 —
            # 안 집힌 항목도 누구 것인지 보이게 필드로 동봉(수행자 a/aid와 별개, 권한 존재론 그대로).
            try:
                sub = str(_fmt(b.submitter) or "") if b.submitter else ""
            except Exception:
                sub = ""
            bl.append({"id": b.backlog_id, "d": (b.body or "")[:60], "s": b.status, "a": a,
                       "aid": (str(b.assignee) if b.assignee else None),
                       "sub": sub, "sid": (str(b.submitter) if b.submitter else None),
                       "t0": b.ts_pick or None, "t1": b.ts_done or None})
        # [백로그=계획 목록(2026-07-10, 사용자: '미리 만들어 두는 건데')] ST 완수조건 = 등록 순간부터
        # 존재하는 계획 단위 — 전 목록을 표면에 준다(passed=✓). 릴레이 bl은 담당·진행의 보강 데이터.
        cr = [{"d": c.desc[:80], "p": bool(c.passed), "w": c.status == "waived",
               "v": (c.verify or "")[:160], "e": (c.evidence or "")[:240]} for c in st.criteria[:15]]
        sts.append({"g": st.goal[:80], "id": st.st_id, "s": st.status, "met": st_met, "total": st_total, "bl": bl, "cr": cr})
    # [완수조건 표면화(2026-07-13, 사용자: '뭐 완수했는지 보이게')] ms레벨 조건도 ✓체크리스트로
    ms_cr = [{"d": c.desc[:80], "p": bool(c.passed), "w": c.status == "waived",
              "v": (c.verify or "")[:160], "e": (c.evidence or "")[:240]} for c in ms.criteria[:15]]
    return {"goal": ms.goal[:140], "ms": ms.ms_id, "met": met, "total": total, "iter": ms.iter_n, "status": ms.status,
            "cr": ms_cr, "sts": sts}


_MS_BG_TASKS = set()   # [GC 방어] fire-and-forget DB push 태스크 참조 보존


def _push_state_db(flow, ch, kind, data):
    """[스케일아웃 상태 저장(2026-07-18, HA 설계)] guide.put_state로 채널 상태를 웹 DB에 미러 —
    sync 문맥(persist_ms_status)에서 부르므로, 실행 중 루프가 있으면 fire-and-forget으로 스케줄한다
    (루프 없으면=테스트 무동작). ORGANT_STATE_DB=0이면 비활성. 파일 미러는 별개로 유지(폴백)."""
    if os.environ.get("ORGANT_STATE_DB", "1") in ("0", "false", "False"):
        return
    put = getattr(getattr(flow, "guide", None), "put_state", None)
    if put is None:
        return
    try:
        import asyncio as _aio
        loop = _aio.get_running_loop()
    except RuntimeError:
        return
    try:
        t = loop.create_task(put(int(ch), kind, data))
        _MS_BG_TASKS.add(t)
        t.add_done_callback(_MS_BG_TASKS.discard)
    except Exception:
        pass


def persist_ms_status(flow):
    """마일스톤 현황을 (1) 상태파일 ms_status.json 미러 + (2) 웹 DB(guide.put_state)로 이중 기록 —
    웹이 DB-우선으로 서빙(HUD). 파일은 단일머신 폴백·DB 순단 방어로 유지, DB는 다중머신 통로.
    ORGANT_PJT 미설정(테스트)이면 파일은 스킵. 실패는 흐름에 무해."""
    ch = getattr(flow, "user_channel", None)
    if ch is None:
        return
    try:
        snap = ms_status_snapshot(flow)
    except Exception:
        snap = None
    # (2) DB 미러 — 다중머신 대비(러너/웹 분리에도 통로 유지). 파일과 독립적으로 시도.
    try:
        _push_state_db(flow, ch, "ms", snap or None)
    except Exception:
        pass
    # (1) 파일 미러 — 현행 경로(같은 호스트) 및 DB 순단 폴백.
    try:
        pjt = os.environ.get("ORGANT_PJT")
        if not pjt:
            return
        d = os.path.join(pjt, "ops", "var", "organt_sns_state")
        if not os.path.isdir(d):
            return
        path = os.path.join(d, "ms_status.json")
        try:
            with open(path, encoding="utf-8") as f:
                cur = json.load(f)
        except Exception:
            cur = {}
        key = str(int(ch))
        if snap:
            cur[key] = snap
        else:
            cur.pop(key, None)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False)
        os.replace(tmp, path)
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
    # [이월 조건 기계 합류(2026-07-20, 사용자: '개입 최대한 줄여')] 이전 주기가 이월한 조건을 새
    # 주기 잣대에 자동 합류 — 이월이 '버리기'가 못 되게 하는 핵심(봇 약속이 아니라 구조가 옮김).
    # 회의가 같은 desc를 이미 실었으면 중복 주입 없이 소진만.
    _have = {x.desc.strip() for x in ms.criteria}
    _joined = 0
    for _pm in flow.milestones:
        for _it in list(getattr(_pm, "carried", None) or []):
            _d = str(_it.get("desc") or "").strip()
            if _d and _d not in _have:
                ms.criteria.append(Criterion(desc=_d, verify=str(_it.get("verify") or "")))
                _have.add(_d)
                _joined += 1
        _pm.carried = []
    if _joined:
        if flow.log:
            flow.log("criterion_carried_in", ms=ms.ms_id, n=_joined)
        _pnote(flow, f"[이월 조건 합류] {_joined}건 — 이전 주기가 다음 주기로 미룬 잣대를 이 주기가 받음")
    # [직렬 강제(2026-07-11, 사용자: '중단되고 새로 열려버렸고')] 미완 주기 공존 금지 — 새 주기가
    # 열리면 이전 미완은 superseded로 닫는다(활성 판정·태깅·스냅샷이 새 주기를 가리키게).
    for _old in flow.milestones:
        if _old.status not in ("done", "superseded"):
            _old.status = "superseded"
            _pnote(flow, f"[마일스톤 대체] ({_old.ms_id}) {_old.goal[:80]}")
            if flow.log:
                flow.log("ms_superseded", ms=_old.ms_id, by=ms.ms_id)
    flow.milestones.append(ms)
    _ckpt(flow)
    if flow.log:
        flow.log("ms_open", ms=ms.ms_id, goal=ms.goal[:80], criteria=len(ms.criteria),
                 replan=bool(origin.startswith("e2e:")))
    # [조건 상세 동봉(2026-07-10)] 피드가 '조건 N' 칩에서 목록을 보여줄 수 있게 마커에 싣는다.
    crit_lines = "\n".join(f"· {c.desc[:96]}" for c in ms.criteria[:30])
    # [ID 동봉(2026-07-10)] 표면이 제목 절두 매칭(추측) 대신 ID로 정확히 부착·완수 매칭한다.
    _pnote(flow, f"[마일스톤 시작] ({ms.ms_id}) {ms.goal[:120]} · 완수조건 {len(ms.criteria)}개"
                 + (f"\n{crit_lines}" if crit_lines else ""))
    return ms


def _pnote(flow, text):
    """[파이프라인 생애주기 → 피드(2026-07-09, 사용자)] 마일스톤·SubTask·완수 등 과정 마커를 누적.
    async 도구 래퍼가 flush_pipeline_notes로 채널에 게시(sync 함수는 await 못 하므로 누적만)."""
    notes = getattr(flow, "_pipeline_notes", None)
    if notes is None:
        notes = flow._pipeline_notes = []
    notes.append(str(text))


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
    _pnote(flow, f"[SubTask 개설] ({st.st_id}) {st.goal[:120]}")
    return st


# ── iter 검증 (계약 §2) ────────────────────────────────────────────────────────

def _backlogs_pending(flow, obj):
    """[완수 판정 보조(2026-07-14)] obj(Milestone/SubTask)의 미종결 백로그 id 목록 —
    open/in_progress/blocked(=remaining). done·dropped는 종결이라 제외. flow.backlog_relays 직접
    조회(backlog 모듈 import는 순환이라 금지 — 장부 dict만 본다)."""
    rels = getattr(flow, "backlog_relays", None) or {}
    if isinstance(obj, SubTask):
        _sts = [obj]
    else:
        _sts = [s for s in getattr(obj, "subtasks", []) if s.status not in ("done", "superseded")]
    out = []
    for st in _sts:
        r = rels.get(st.st_id)
        if r is None:
            continue
        out += [b.backlog_id for b in r.backlogs if b.status not in ("done", "dropped")]
    return out


def iter_verify(flow, obj, results):
    """한 iter의 완수조건 검증. results = [{desc, passed, evidence}] — 봇들이 run으로 실증한 결과.

    - evidence 없는 passed는 인정하지 않는다(기존 '실행 증거' 원칙 그대로 — 허위 충족 차단).
    - 전 조건 충족 → status=wrapup(잔여 정리 모드 — 남은 SubTask/백로그를 마저 정리하고 넘어간다,
      계약 §2 확정: "다 해결되지 않았더라도 완수조건 완수 시 남은 것 해결 후 다음 주기").
    - 미충족 → open 유지, 미충족 목록 반환(다음 iter의 일감).
    반환: (passed_all: bool, note: str)."""
    obj.iter_n += 1
    # [버그 A 교정] '진전'은 이번 iter에 새로 통과한 조건 유무 — 결과 적용 전 미충족 수를 기준선으로.
    _before = sum(1 for c in obj.criteria if not c.passed and c.status != "waived")
    _passed0 = {c.desc for c in obj.criteria if c.passed}   # 신규 통과 식별 기준선
    by_desc = {c.desc: c for c in obj.criteria}
    # [매칭 견고화(2026-07-12)] desc 완전일치만 인정 + 불일치 무통보 폐기가 9차 공회전(ch53 ST-1,
    # 조건 desc가 '1'·'2'로 등록된 판)의 진범 — 성실한 제출이 한 번도 착지 못했다.
    # 완전일치 → 정규화 포함 → 서수(결과 desc 선두 숫자=조건 desc) → 토큰 겹침(verify 본문 포함)
    # 순으로 착지시키고, 그래도 못 찾으면 '조용히 버리는' 대신 미매칭으로 집계해 봇에게 되돌려준다.
    def _norm(s):
        return re.sub(r"\s+", "", str(s or "").lower())

    def _toks(s):
        return set(re.findall(r"[a-z0-9가-힣_/.:-]{2,}", str(s or "").lower()))

    def _tok_hits(rt, ct):
        # [토큰 부분일치(2026-07-20, 매칭 강건화 F)] 완전 일치에 더해 3자+ 토큰의 포함('판정'⊂
        # '승패판정')도 겹침으로 센다 — 조사·합성어 표기차로 성실한 보고가 미끄러지던 것 보강.
        # 임계(4)는 유지: 우연 겹침 차단.
        n = 0
        for a in rt:
            if a in ct or (len(a) >= 3 and any(len(b) >= 3 and (a in b or b in a) for b in ct)):
                n += 1
        return n

    _unclaimed = [c for c in obj.criteria if not c.passed and c.status != "waived"]
    _unmatched, _rereported = [], []
    for r in (results or []):
        d = str(r.get("desc") or "").strip()
        c = by_desc.get(d)
        if c is None:
            nd = _norm(d)
            c = next((x for x in _unclaimed if _norm(x.desc)
                      and (_norm(x.desc) in nd or nd in _norm(x.desc))), None)
        if c is None:
            m = re.match(r"^\s*(?:조건\s*)?(\d{1,2})\b", d)
            if m:
                c = next((x for x in _unclaimed if x.desc.strip() == m.group(1)), None)
        if c is None and _unclaimed:
            rt = _toks(d) | _toks(r.get("evidence"))
            best = max(_unclaimed, key=lambda x: _tok_hits(rt, _toks(x.desc) | _toks(x.verify)))
            if _tok_hits(rt, _toks(best.desc) | _toks(best.verify)) >= 4:   # 우연 겹침 차단 임계
                c = best
        if c is None:
            # [기충족 재보고 흡수(2026-07-20, U-035 rung3 부수)] 이미 통과한 조건의 재보고(퍼지 표기)
            # 가 '미매칭'으로 계고돼 봇이 같은 보고를 반복 제출하던 공회전 차단 — 통과분과 맞으면
            # 접수됐음을 알리고 흡수한다(중복 점유·증거 덮어쓰기 없음).
            _done_pool = [x for x in obj.criteria if x.passed]
            nd = _norm(d)
            cp = next((x for x in _done_pool if _norm(x.desc)
                       and (_norm(x.desc) in nd or nd in _norm(x.desc))), None)
            if cp is None and _done_pool:
                rt = _toks(d) | _toks(r.get("evidence"))
                bestp = max(_done_pool, key=lambda x: _tok_hits(rt, _toks(x.desc) | _toks(x.verify)))
                if _tok_hits(rt, _toks(bestp.desc) | _toks(bestp.verify)) >= 4:
                    cp = bestp
            if cp is not None:
                _rereported.append(cp.desc[:40])
                continue
        if c is None:
            _unmatched.append(d[:60])
            continue
        if c in _unclaimed:
            _unclaimed.remove(c)   # 결과 여러 건이 같은 조건을 중복 점유하지 않게
        ev = str(r.get("evidence") or "").strip()
        if bool(r.get("passed")) and ev:
            c.passed, c.evidence = True, ev[:400]
    if _unmatched and flow.log:
        flow.log("iter_result_unmatched", id=getattr(obj, "ms_id", None) or getattr(obj, "st_id", ""),
                 n=len(_unmatched), descs=_unmatched[:4])
    if _rereported and flow.log:
        flow.log("iter_result_rereported", id=getattr(obj, "ms_id", None) or getattr(obj, "st_id", ""),
                 n=len(_rereported))
    # [조건 재협상 #1] waived(사람 승인 포기) 조건은 '충족'처럼 제외 — 미충족 목록에 안 남는다.
    remain = [c.desc for c in obj.criteria if not c.passed and c.status != "waived"]
    kind = "ms" if isinstance(obj, Milestone) else "st"
    oid = getattr(obj, "ms_id", None) or getattr(obj, "st_id", "")
    # [조건 충족 표면화(2026-07-10)] 백로그의 실체(조건 충족 진행)를 피드가 그릴 수 있게 —
    # 이번 iter에 '새로' 통과한 조건이 있을 때만 마커(도배 방지).
    _after = len(remain)
    _tot = sum(1 for c in obj.criteria if c.status != "waived")
    _new_pass = [c.desc for c in obj.criteria if c.passed and c.desc not in _passed0]
    # [iter 가시화(2026-07-10, 사용자: 'iter 시점이 시각적으로 보인다')] 매 검증마다 리듬 마커 —
    # 피드가 단계 안을 iter 단위로 마디 짓는다(작업들 → 검증 결과 → 다음 iter).
    _pnote(flow, f"[iter 검증] ({oid}) {obj.iter_n}차 — 충족 {_tot - _after}/{_tot}"
                 + (" · 새 통과: " + " · ".join(d[:48] for d in _new_pass) if _new_pass else "")
                 + (f" · 미충족 {_after}건" if _after else " · 전 조건 충족"))
    if flow.log:
        flow.log("ms_iter_verify", kind=kind, id=oid, iter=obj.iter_n,
                 passed=len(obj.criteria) - len(remain), total=len(obj.criteria))
    if not remain:
        # [완수 = 조건 실증 + 백로그 전부 종결(2026-07-14, 사용자: '백로그를 모두 완수하면 끝난다는
        # 표현이 맞아 — 중단으로 처리된 것은 제외')] 완수조건이 충족돼도 미종결(open/in_progress/
        # blocked) 백로그가 남아 있으면 아직 끝이 아니다 — 전부 완료(done)나 중단(dropped)돼야 닫힌다.
        # dropped(개인이 완수 불가로 접은 것)는 remaining()이 이미 제외한다.
        _pending = _backlogs_pending(flow, obj)
        if _pending:
            return False, (f"완수조건은 충족됐지만 백로그 {len(_pending)}건이 아직 처리 중입니다 "
                           f"({' · '.join(_pending[:6])}) — 백로그는 전부 완료(report_iter)나 중단"
                           f"(drop_backlog)돼야 종료됩니다. 중단은 완수 집계에서 제외됩니다.")
        obj.iter_stuck = 0
        obj.status = "wrapup"
        if flow.log:
            flow.log("ms_iter_pass", kind=kind, id=oid, iter=obj.iter_n)
        _ckpt(flow)
        return True, "완수조건 전부 충족 + 백로그 전부 종결 — 정리(wrapup) 후 다음 주기로."
    # [조건 불가능 출구 #1] 같은 조건이 진전 없이 반복 미충족이면 접근이 결과를 못 바꾸는 신호 —
    # iter_stuck을 세고 임계(기본 3) 도달 시 재협상 경로를 안내한다(무한 iter 차단).
    # [버그 A] 진전 = 이번 iter에 미충족 수가 줄었는가(새로 통과) — '과거에 하나 통과'로 영구 진전 오판 금지.
    # [버그 B] 재협상 대기(blocked_pending) 조건이 있으면 정체가 아니라 '사람 응답 대기' — 경보 스팸 정지.
    _progressed = len(remain) < _before
    _waiting = any(c.status == "blocked_pending" for c in obj.criteria)
    if _progressed:
        obj.iter_stuck = 0
    elif not _waiting:
        obj.iter_stuck += 1
    _ckpt(flow)
    note = "미충족: " + " · ".join(d[:40] for d in remain)
    if _rereported:
        note += ("\n[이미 충족된 조건 재보고 " + str(len(_rereported)) + "건 — 접수돼 있습니다] "
                 "그 조건은 다시 보고하지 말고 위 미충족 조건만 진행하세요: "
                 + " · ".join(_rereported[:3]))
    if _unmatched:
        # 매칭 실패를 봇에게 되돌린다 — 조용한 폐기가 공회전의 뿌리였다. 조건 desc 원문을 그대로 준다.
        note += ("\n[결과 " + str(len(_unmatched)) + "건 미착지 — desc 불일치] report_iter의 results[].desc는 "
                 "아래 조건 desc를 **토씨 그대로** 복사해 쓰세요: "
                 + " / ".join("'" + c.desc[:50] + "'" for c in obj.criteria if not c.passed and c.status != "waived"))
        if kind == "ms":
            # [오귀속 코칭(2026-07-21, U-036 실측: 백로그 산출물을 마일스톤 조건인 양 재표기하도록
            # 유도하던 안내)] 끝낸 것이 조건이 아니라 자기 백로그면 — 조건 desc로 바꿔 쓰는 게 아니라
            # 그 작업 단위(SubTask)에 보고하는 게 맞다(백로그를 쥔 채면 target은 자동 귀속된다).
            note += ("\n(방금 끝낸 것이 위 조건이 아니라 **당신이 집은 백로그**라면, 조건 desc로 바꿔 "
                     "쓰지 말고 report_iter(target=<SubTask id>)로 그 단위에 보고하세요 — in_progress "
                     "백로그를 쥔 상태의 무지정 보고는 자동으로 그 단위에 귀속됩니다.)")
    if obj.iter_stuck >= _STUCK_LIMIT:
        if flow.log:
            flow.log("ms_iter_stuck", kind=kind, id=oid, iter=obj.iter_n, stuck=obj.iter_stuck)
        note += (f"\n[정체 — {obj.iter_stuck}회 연속 진전 없음] 반복이 결과를 못 바꾸고 있습니다. "
                 f"조건이 이번 주기 범위 밖이거나 환경상 불가라면 renegotiate_criterion(대상 조건, 사유)로 "
                 f"재협상하세요 — 로드맵에 다음 주기가 있으면 사람 없이 그 주기로 즉시 이월되고, "
                 f"못 옮길 때만 사람 승인을 구합니다. 무한 반복하지 마세요.")
    return False, note


_STUCK_LIMIT = int(os.environ.get("ORGANT_ITER_STUCK_LIMIT", "3"))


def roadmap_phases(flow):
    """[로드맵 phase 정규화(2026-07-20, 사용자: '개입 최대한 줄여')] 로드맵 항목을 phase 목록으로 —
    회의 골격이 '단계: 최소버전 → 확장 → 완성' 한 줄을 유도하므로, **띄운 화살표(' → ')만** 구분자로
    분해한다(조건 파서의 '띄어쓴 파이프만' 계약과 같은 축 — phase 서술 속 '선택→결과' 같은 붙은
    화살표는 안 쪼갬). 종전엔 한 줄 로드맵이 phase 1개로 세어져 다음 주기 회의가 영영 안 열렸다."""
    out = []
    for r in (getattr(flow, "roadmap", None) or []):
        out += [p.strip() for p in re.split(r"\s+→\s+", str(r or "")) if p.strip()]
    return out


def defer_criterion(flow, obj, c, reason: str):
    """[조건 이월 — 사람 개입 없는 1차 해소(2026-07-20, 사용자: '개입 최대한 줄여')] 이번 주기 범위
    밖 조건을 '다음 주기로 이월'한다 — 잣대를 버리는 게 아니라 옮기는 것: 조건이 obj.carried 원장에
    실리고 다음 open_milestone이 기계로 새 주기 잣대에 합류시킨다(봇 약속이 아니라 구조가 보증 →
    완료 참칭 불가). 성립 조건(전부): ①마일스톤 조건일 것 ②미충족일 것 ③로드맵에 받아줄 후속
    phase가 있을 것 ④이월 후에도 이번 주기에 잣대가 최소 1개 남을 것. 못 옮기면 None(호출측이
    사람 경로로 — 그때만 최후수단)."""
    if not isinstance(obj, Milestone) or c.passed or c.status == "waived":
        return None
    phases = roadmap_phases(flow)
    done_n = sum(1 for m in (getattr(flow, "milestones", None) or []) if m.status == "done")
    if len(phases) < done_n + 2:            # 현 주기=phase[done_n] — 받아줄 다음 phase가 없다
        return None
    if sum(1 for x in obj.criteria if x is not c and x.status != "waived") < 1:
        return None                          # 마지막 잣대 이월 금지 — 잣대 0개 주기(빈 완료) 차단
    obj.criteria.remove(c)
    obj.carried.append({"desc": c.desc, "verify": c.verify, "reason": str(reason or "")[:200]})
    obj.iter_stuck = 0
    oid = getattr(obj, "ms_id", "")
    if flow.log:
        flow.log("criterion_deferred", id=oid, target=c.desc[:60], reason=str(reason or "")[:80])
    _pnote(flow, f"[조건 이월] '{c.desc[:60]}' → 다음 주기 — 이번 주기 범위 밖(사람 개입 없이 자체 해소)")
    _ckpt(flow)
    return (f"조건 '{c.desc[:40]}' 다음 주기로 이월했습니다 — 사람 승인 불요(로드맵 후속 단계가 받아,"
            f" 다음 주기 잣대로 자동 합류). 이번 주기는 남은 조건으로 진행하세요.")


def resolve_blocked_by_defer(flow) -> int:
    """[파킹 직전 훅(2026-07-20)] 이미 사람 대기(blocked_pending)로 굳은 조건도 이월 가능하면 사람
    없이 해소 — 이 코드 이전에 막힌 판(복원 포함)의 소급 출구. 전부 풀리면 awaiting_human도 걷는다.
    반환: 해소 건수(잔여 blocked가 있으면 호출측이 파킹 = 최후수단)."""
    n = 0
    for m in (getattr(flow, "milestones", None) or []):
        if m.status in ("done", "superseded"):
            continue
        for c in list(m.criteria):
            if c.status == "blocked_pending" and defer_criterion(flow, m, c, c.block_reason):
                n += 1
    if n and not pending_waivers(flow) and getattr(flow, "awaiting_human", None):
        flow.awaiting_human = None
    return n


def renegotiate_criterion(flow, obj, target: str, reason: str) -> str:
    """[조건 재협상 #1] 달성 불가 조건의 출구 — 해소 사다리(사람 = 최후수단, 2026-07-20 사용자:
    '개입 최대한 줄여'): ①로드맵 후속 phase가 받을 수 있으면 **이월로 즉시 자체 해소**(defer_criterion
    — 사람 없이 종결, 잣대는 다음 주기로 이동) ②못 옮길 때만(로드맵 소진·마지막 잣대) blocked_pending
    으로 사람에게 에스컬레이트. '포기(waive)'는 여전히 사람 승인 전용 — 잣대를 아예 버리는 결정만
    사람 몫이고, 제자리로 옮기는 건 구조가 보증하므로 봇 선에서 끝난다."""
    c = next((x for x in obj.criteria if x.desc == target or target in x.desc), None)
    if c is None:
        return f"재협상 대상 조건을 못 찾음: {target[:40]} — 현재 조건: {' · '.join(x.desc[:24] for x in obj.criteria)}"
    if c.status == "waived":
        return f"이미 포기(waived)된 조건입니다: {c.desc[:40]}"
    if c.passed:
        return f"이미 충족된 조건입니다(재협상 불요): {c.desc[:40]}"
    deferred = defer_criterion(flow, obj, c, reason)
    if deferred:
        return deferred
    c.status = "blocked_pending"
    c.block_reason = str(reason or "").strip()[:300]
    obj.iter_stuck = 0   # 재협상 진행 중 — 경보 반복 정지(사람 응답 대기)
    oid = getattr(obj, "ms_id", None) or getattr(obj, "st_id", "")
    if flow.log:
        flow.log("criterion_renegotiate", id=oid, target=c.desc[:60], reason=c.block_reason[:80])
    # 사람 에스컬레이트(매체가 있으면) — deliver 경로는 SYS가 주입, 없으면 로그만(관통·테스트 안전)
    esc = getattr(flow, "escalate_to_human", None)
    if callable(esc):
        try:
            esc(f"[조건 재협상 승인 요청] 마일스톤 조건 '{c.desc[:60]}'을(를) 포기/변경할지 판단 필요 — "
                f"사유: {c.block_reason[:120]}. 승인하면 그 조건 없이 주기가 진행됩니다.")
        except Exception:
            pass
    return (f"조건 '{c.desc[:40]}' 재협상 요청 — 사람 승인 대기(blocked_pending). 사유: {c.block_reason[:80]}. "
            f"승인 오면 그 조건은 포기(waive)되고 나머지로 주기가 진행됩니다. 그 사이 다른 조건을 진행하세요.")


def pending_waivers(flow):
    """[사람 대기 = 파생 사실(2026-07-18, 검수)] 흐름의 blocked_pending 조건 전부(마일스톤+서브태스크).
    '사람 승인 대기'의 진실원은 flow.awaiting_human(휘발 캐시)이 아니라 조건 상태다 — 조건은 체크포인트에
    동승·복원되므로, 러너 재시작 후에도 이 함수가 대기를 정확히 판정한다(플래그만 믿으면 재시작이
    사람 승인 경로를 끊어 교착이 재발한다)."""
    out = []
    for m in (getattr(flow, "milestones", None) or []):
        for obj in [m] + list(getattr(m, "subtasks", None) or []):
            for c in (getattr(obj, "criteria", None) or []):
                if getattr(c, "status", "") == "blocked_pending":
                    out.append(c)
    return out


def parse_waiver_reply(text: str):
    """사람 답변 → 'approve'|'deny'|None. 부정문('승인 안/않/하지/보류/아직')은 승인으로 읽지 않는다 —
    광역 `'승인' in text`가 '승인 안 할게요'까지 전 조건 waive하던 것 교정(2026-07-18 검수)."""
    t = str(text or "")
    if not t.strip():
        return None
    _neg = re.search(r"승인[^\n]{0,6}(?:안|않|하지|말|보류|어렵|못|없|아직|아니)|(?:아직|나중)[^\n]{0,8}승인", t)
    _explicit = any(k in t for k in ("조건 승인", "포기 승인", "재협상 승인"))
    if _explicit and not _neg:
        return "approve"
    if "반려" in t or "거부" in t:
        return "deny"
    if _neg:
        return None
    if "승인" in t:
        return "approve"
    return None


def approve_waiver(flow, obj, target: str, approve: bool = True) -> str:
    """[사람 승인 경로] blocked_pending 조건을 waived(포기 확정) 또는 active(반려)로. 매체(murmur)가
    사람 결정을 받아 호출하거나, SYS가 deliver_human_info의 '[조건 포기 승인/반려]'를 파싱해 부른다."""
    c = next((x for x in obj.criteria if (x.desc == target or target in x.desc)
              and x.status == "blocked_pending"), None)
    if c is None:
        return f"승인 대기 중인 재협상 조건을 못 찾음: {target[:40]}"
    c.status = "waived" if approve else "active"
    oid = getattr(obj, "ms_id", None) or getattr(obj, "st_id", "")
    if flow.log:
        if approve: _pnote(flow, f"[조건 조정] '{c.desc[:40]}' 환경 제약으로 조정(사람 승인)")
        flow.log("criterion_waiver", id=oid, target=c.desc[:60], approved=bool(approve))
    # [사람 대기 해제(2026-07-18, 감사)] 흐름에 남은 blocked_pending 조건이 없으면 awaiting_human 해제 —
    # HUD의 '사람 대기'가 사라지고 교착 출구가 닫힌다(자기완결, 호출자 무관).
    _mss = getattr(flow, "milestones", None) or []
    _any_blocked = any(x.status == "blocked_pending" for m in _mss
                       for x in (list(getattr(m, "criteria", []) or [])
                                 + [cc for st in getattr(m, "subtasks", []) for cc in getattr(st, "criteria", [])]))
    if not _any_blocked and getattr(flow, "awaiting_human", None):
        flow.awaiting_human = None
    _ckpt(flow)
    return (f"조건 '{c.desc[:40]}' {'포기 승인됨(waived) — 나머지 조건으로 주기 진행' if approve else '반려됨 — 다시 충족해야 함'}.")


def wrapup_done(flow, obj) -> str:
    """잔여 정리 완료 선언 → done. wrapup 상태에서만 유효(조건 미충족 상태의 건너뛰기 차단)."""
    if obj.status != "wrapup":
        return "정리 완료 선언 불가: 아직 완수조건 검증(iter_verify)을 통과하지 않았습니다."
    # [서브태스크 선완료 게이트(2026-07-14, 사용자: '최대 구현 조건으로 생성된 서브테스크는 다 하고
    # 끝내는걸로 했었는데')] 마일스톤 조건(cr)이 4/4여도, 그 주기가 낳은 서브태스크가 open이면 아직
    # 최대 구현이 안 끝난 것 — 닫으면 미완 ST가 빈 장부의 유령으로 남는다(ch61: 조건 4/4인데 ST 5개 open).
    # 주기는 '조건 충족 + 하위 단위 전부 완수'일 때만 닫힌다. 미완 ST는 이름을 대 잇게 지시.
    if isinstance(obj, Milestone):
        _open_sts = [s for s in obj.subtasks if s.status not in ("done", "superseded")]
        if _open_sts:
            _names = " · ".join(f"{s.st_id}({s.goal[:24]})" for s in _open_sts[:6])
            return ("마일스톤 완수 보류: 완수조건은 충족됐지만 이 주기의 SubTask "
                    f"{len(_open_sts)}건이 아직 미완입니다 — 최대 구현으로 연 하위 단위는 전부 끝나야 "
                    f"주기가 닫힙니다. 미완: {_names}. 각 SubTask를 iter_verify→wrapup_done으로 닫거나, "
                    "환경상 불가하면 renegotiate_criterion으로 그 SubTask를 정리한 뒤 다시 선언하세요.")
    obj.status = "done"
    _ckpt(flow)
    if flow.log:
        _oid = getattr(obj, "ms_id", None) or getattr(obj, "st_id", "")
        _pnote(flow, f"[{'마일스톤 완수' if isinstance(obj, Milestone) else 'SubTask 완수'}] ({_oid}) {getattr(obj, 'goal', '')[:120]}")
        flow.log("ms_done" if isinstance(obj, Milestone) else "subtask_done",
                 id=getattr(obj, "ms_id", None) or getattr(obj, "st_id", ""))
    if isinstance(obj, Milestone):
        # [주기 보고 체계(2026-07-14, 사용자: '각 마일스톤 주기마다 사용자가 체감할 수 있도록 적용하고
        # 보고')] 주기 완수 = 사용자 가시 보고 게시(조건+증거) — 로드맵이 있으면 다음 단계 회의 코칭.
        _ev = "\n".join(f"· {c.desc[:70]} — {(c.evidence or '실증 기록')[:90]}"
                        for c in obj.criteria[:8] if c.status != "waived")
        # [확인 링크 동봉(2026-07-20, 사용자: '마일스톤 끝날 때마다 배포된 링크라든지 확인할 수 있는
        # 자료')] '체감 검증하세요'로 끝나지 않게 실제 열어볼 주소를 준다 — ①라이브 배포 URL(있으면)
        # ②매체의 완성작 주소(guide가 알면 — duck-typed, 구현체 import 없음) ③'완성작' 버튼 안내.
        _url = str(getattr(flow, "_deploy_url", "") or "")
        if not _url:
            _wu = getattr(getattr(flow, "guide", None), "work_url", None)
            _pid = getattr(flow, "project_id", None)
            if callable(_wu) and _pid:
                try:
                    _url = str(_wu(str(_pid)) or "")
                except Exception:
                    _url = ""
        _chk = (f"바로 열어 확인: {_url}" if _url else "채널 상단 '완성작' 버튼에서 바로 실행해 확인하세요")
        _pnote(flow, f"[마일스톤 보고] ({obj.ms_id}) {obj.goal[:100]}\n{_ev}\n"
                     f"→ 사용자 확인 단위입니다 — {_chk}")
        _rm = list(getattr(flow, "roadmap", None) or [])
        _done_n = sum(1 for m in flow.milestones if m.status == "done")
        if _done_n < len(_rm):
            _pnote(flow, f"[다음 단계] 로드맵 {_done_n + 1}/{len(_rm)} 완수 — 다음: **{_rm[_done_n][:60]}**. "
                         f"meet 회의를 열어 다음 주기 수렴안([수렴안] 목표/조건/단위)을 확정하세요"
                         f"(사용자 보고·목표 확인 후).")
    return "done"


def next_milestone(flow) -> Optional[Milestone]:
    """다음 진행 대상 — 미완(done 아님) 첫 마일스톤. 진행을 사람이 아니라 주기가 관할한다."""
    for ms in flow.milestones:
        if ms.status not in ("done", "superseded"):
            return ms
    return None


# ── 복기 진입점 (계약 §6 — S3의 e2e_fail이 호출) ───────────────────────────────

def _replan_defect_count(origin) -> int:
    """복기 마일스톤 origin("e2e:d1 | d2 | …")의 결함 수 — 복기 진전 판정의 원자(이력에서 파생)."""
    return len([s for s in str(origin or "")[4:].split(" | ") if s.strip()])


def ms_replan(flow, defects) -> Optional[Milestone]:
    """e2e 전수 실패 → 결함 목록으로 새 마일스톤을 연다. 조건 초안은 결함의 부정형(각 결함 해소를
    조건으로) — **확정은 회의 몫**(조건 결정은 turn-taking 회의, 계약 §4). 여기는 진입점만."""
    ds = [str(d).strip() for d in (defects or []) if str(d).strip()]
    if not ds:
        return None
    # [복기 진전 게이트(2026-07-20, 사용자: '무한반복·불안정 다 잡고 e2e — 비용 트레이드오프')]
    # e2e→복기→e2e의 마지막 무상한 경로: 결함 수가 줄지 않는 복기가 이어지면(반복이 결과를 못
    # 바꿈 — 재픽·이월과 같은 '진전' 철학) 새 복기 주기를 열지 않는다. 첫 재시도는 허용, 2회
    # 연속 비개선부터 컷 — 정직 중단(사람 조치 게시는 reap 몫, 비용 최종 백스톱=크레딧 캡).
    # 판정은 마일스톤 이력(origin)에서 파생 — 무상태·재시작 생존.
    _runs = [_replan_defect_count(m.origin) for m in (getattr(flow, "milestones", None) or [])
             if str(getattr(m, "origin", "")).startswith("e2e:")]
    _stuck = 1 if (_runs and len(ds) >= _runs[-1]) else 0
    if _stuck:
        for i in range(len(_runs) - 1, 0, -1):
            if _runs[i] >= _runs[i - 1]:
                _stuck += 1
            else:
                break
    if _stuck >= 2:
        if flow.log:
            flow.log("ms_replan_stuck", rounds=_stuck, defects=len(ds))
        _pnote(flow, f"[e2e 복기 정체] 결함 {len(ds)}건이 복기 {_stuck}회째 줄지 않습니다 — 같은 접근의 "
                     "반복을 멈춥니다(사람 확인 필요: 요청 구체화 또는 재개로 방향 제시).")
        return None
    entries = [{"desc": f"결함 해소: {d[:80]}",
                "verify": f"run으로 재현 절차 재실행 → 재현 0회 확인: {d[:120]}"} for d in ds]
    ms = open_milestone(flow, goal=f"e2e 결함 {len(ds)}건 해소", criteria_entries=entries,
                        origin="e2e:" + " | ".join(d[:60] for d in ds)[:400])
    if isinstance(ms, str):        # 게이트 거부(이론상 없음 — 방어)
        return None
    if flow.log:
        flow.log("ms_replan", ms=ms.ms_id, defects=len(ds))
    return ms


# ── 봇 도구 표면 (계약 §4 — 회의 수렴을 결정권자가 확정해 주기로 만든다) ─────────

# [조건 구분자 = 라벨(2026-07-17, ch78 실측)] 템플릿 계약은 '조건 | 실증: 절차'. 맨 '|' 분리는 조건
# 본문의 파이프(JSON enum(buy|pass|…))를 구분자로 오인해 desc/verify를 엉망으로 쪼갠다 — 봇이 고칠 수
# 없는 벽. 라벨('| 실증:'류)이 있으면 거기서 분리, 없으면 종전 첫 '|' 분리(하위 호환).
_CRIT_DELIM = None


def _crit_delim():
    global _CRIT_DELIM
    if _CRIT_DELIM is None:
        import re as _re
        # 라벨('| 실증:'류 — 변형은 draft_norm_line이 정본화) 또는 띄어쓴 ' | '만 구분자.
        # 본문의 붙은 파이프(enum(buy|pass|…))는 구분자가 아니다 — 산문이 조건으로 오인되지 않게.
        _CRIT_DELIM = _re.compile(r"\|\s*[^|:\n]{0,12}?(?:실증|검증|측정)\s*[:：]|\s\|\s")
    return _CRIT_DELIM


def parse_criteria_lines(text: str):
    """봇이 쓴 조건 텍스트(한 줄 = '조건 | 실증: 절차')를 게이트 입력으로. 형식 오류는 게이트가 잡는다."""
    out = []
    for ln in str(text or "").splitlines():
        ln = ln.strip().lstrip("-•* ").strip()
        if not ln:
            continue
        m = _crit_delim().search(ln)
        if m:
            d, v = ln[:m.start()], ln[m.end():]
        else:
            d, _, v = ln.partition("|")
        import re as _re
        v = _re.sub(r"^(?:[\w가-힣]{0,4}\s*)?(?:실증|검증|측정)\s*[:：]\s*", "", v.strip())
        out.append({"desc": d.strip(), "verify": v.strip()})
    return out


# [결정권자 폐지(2026-07-09, 사용자)] 확정 권력 삭제 — 확정은 회의 종결 표결(가결=수렴안 자동 등록)이
# 하고, 이 도구는 '서기'의 표면(누구나)이다. 품질은 사람이 아니라 등록 게이트가 지킨다.
_CONSENSUS_RE = None   # 지연 컴파일(아래 extract_consensus)


def extract_consensus(text: str):
    """봇 발화에서 [수렴안]...[/수렴안] 블록(조건 | 실증절차 줄들)을 꺼낸다 — 종결 표결 동봉용."""
    global _CONSENSUS_RE
    import re as _re
    if _CONSENSUS_RE is None:
        _CONSENSUS_RE = _re.compile(r"\[수렴안\]\s*\n?(?P<body>.*?)\n?\[/수렴안\]", _re.S)
    m = _CONSENSUS_RE.search(str(text or ""))
    return m.group("body").strip() if m else None


def extract_stage_proposal(stage, text):
    """[통일 수렴안(2026-07-15, 사용자 가안)] 모든 단계의 회의 산출물 = [수렴안] 하나의 형식.
    그 수렴안을 어떻게 가공할지가 각 단계(register_stage)의 몫 — 추출은 단일 경로로 통일."""
    return extract_consensus(text)


def gate_new_cycle(flow):
    """[새 주기 공용 게이트(2026-07-13, 사용자: '대체됨은 또 왜 생긴거고')] 마일스톤 개설 전 공통 검문 —
    도구 등록(rule_set_milestone)과 회의 표결 확정(communication)이 같은 문을 지난다. U-015 라이브에서
    표결 경로가 이 검문 없이 open_milestone을 직접 불러 iter>0 미완 주기를 대체(supersede)했다.
    통과=None, 거부=사유 문자열(호출측이 그대로 회신)."""
    # [목표 선행 게이트(2026-07-13)] GOAL 없이 마일스톤이 서면 판의 정체가 영영 빈칸 - 순서를 구조로 강제.
    _cur = getattr(flow, "current", None)
    if _cur is not None and not str(getattr(_cur.status, "goal", "") or "").strip():
        return ("[마일스톤 등록 보류] 이 Task의 목표(GOAL)가 아직 확정되지 않았습니다 - 소집자가 "
                "set_goal(팀 합의)로 목표를 먼저 확정하세요. 목표 없는 마일스톤은 판의 정체를 비웁니다. "
                "(set_goal은 소집자 턴의 도구입니다 - 회의 참여 턴에는 안 보입니다.)")
    # [정밀 복구 게이트(2026-07-11, 사용자: '대체 말고 기존 걸 바로 이어가야지')] 미완 주기가 있으면
    # 새 주기 개설 금지 — 승계·재편성이라는 이름의 중복 개설이 태깅·기록·화면을 갈랐다(ch53 라이브).
    _open = next((m for m in (getattr(flow, "milestones", None) or [])
                  if m.status not in ("done", "superseded")), None)
    # 계획 단계(iter 0 — 아직 검증 전)의 다음 주기 큐잉은 허용(계약: 주기 복수 사전 확정).
    # 금지 대상은 '검증이 돌던 주기를 버리고 갈아타기'(iter>0 미완 존재)다.
    _worked = _open is not None and (getattr(_open, "iter_n", 0) > 0
                or any(getattr(st, "iter_n", 0) > 0 or st.status == "done" for st in _open.subtasks)
                or any(c.passed for c in _open.criteria))
    if flow.log and _open is not None:
        flow.log("ms_gate_check", open_ms=_open.ms_id, iter_n=_open.iter_n,
                 worked=bool(_worked), sts=len(_open.subtasks))
    if _worked:
        _met, _tot = _cnt_active(_open.criteria)
        _remain = " · ".join(c.desc[:40] for c in _open.criteria if not c.passed and c.status != "waived")
        return (f"등록 거부: 미완 주기 {_open.ms_id}(충족 {_met}/{_tot})가 진행 중입니다 — 새 주기를 열지 말고 "
                f"**그 주기를 이어가세요**(정밀 복구 원칙). 미충족: {_remain[:200] or '(없음 — wrapup 정리 후 종료)'}. "
                f"report_iter(대상 지정 가능)로 검증을 잇고, 단위가 필요하면 set_subtask로 그 주기 안에 여세요. "
                f"조건이 환경상 불가면 renegotiate_criterion이 출구입니다.")
    # [내용 있는 주기 보호(2026-07-14, U-019 라이브)] '계획 단계(iter 0) 큐잉 허용'은 open_milestone이
    # 실제로는 대체(supersede)라 빈말이었다 — 프론트의 set_milestone 한 방이 SubTask 6개 붙은 판을
    # 통째로 갈아엎었다(ms_superseded, 자기 자리가 없어 상위를 자작한 증상). 단위(SubTask)가 이미 붙은
    # 주기는 검증 전이라도 갈아타기 금지 — 대체가 허용되는 건 아무것도 안 붙은 빈 헛발 주기뿐.
    if _open is not None and any(st.status != "superseded" for st in _open.subtasks):
        return (f"등록 거부: 주기 {_open.ms_id}에 이미 단위(SubTask {len(_open.subtasks)}개)가 붙어 있습니다 — "
                f"새 주기를 열면 그 판이 통째로 대체(파기)됩니다. **그 주기 안에서 일하세요**: 내 몫의 단위가 "
                f"없으면 set_subtask로 그 주기 안에 열고, 작업은 pick_backlog(desc='내가 할 일')로 집은 뒤 "
                f"시작하세요. 다음 주기는 이 주기 완수(wrapup) 후에 엽니다.")
    return None


def register_consensus(flow, prop: str, origin: str = ""):
    """[표결 가결 → 서기 등록(2026-07-14)] 수렴안 원문을 갈라 흐름 단계에 맞게 등록한다 — 팀 판의
    주기 확정·단위 분해의 유일 경로(개인 도구는 솔로 판 한정). 반환: (Milestone|에러 str, 단위 수).

    단계 인식(사용자 설계: '마일스톤은 순차 1개, 회의 종료 시에만 생성'):
    - 열린 주기 없음 → 새 마일스톤(+동봉 '단위:' 줄) 등록. '단계:' 줄들 = 로드맵(달구지→자동차→
      스포츠카) — flow.roadmap에 보관, 각 주기 완수 보고 때 다음 단계 회의를 코칭.
    - 열린 주기 있음 → 이 수렴안은 그 주기의 **분해 회의** — '단위:' 줄만 그 주기에 추가 등록.
      단, 기존 단위의 백로그가 아직 처리 중이면 보류(경계 생성 — '종료될 때만 생성')."""
    lines = str(prop or "").splitlines()
    # [파싱 견고화(2026-07-14, 정합 감사)] ①라벨은 콜론 필수 — 콜론 없는 '목표..'/'단계..'에
    # split(":",1)[1]이 IndexError로 meet 종결부에 예외를 뿌리던 것 봉합(":" in l 가드). ②'단계'가
    # 아니라 '단계:'로만 로드맵 인식 — 콜론 없는 '단계별 …|…' 완수조건이 roadmap으로 오분류돼 조건에서
    # 소실되던 비대칭 제거(단위: 와 대칭).
    goal = next((l.split(":", 1)[1].strip() for l in lines
                 if l.strip().startswith("목표") and ":" in l), origin)
    units = [l.strip()[3:].strip() for l in lines if l.strip().startswith("단위:")]
    stages = [l.split(":", 1)[1].strip() for l in lines if l.strip().startswith("단계:")]
    crit = "\n".join(l for l in lines if "|" in l and not l.strip().startswith("단위:")
                     and not l.strip().startswith("단계:"))
    _open = next((m for m in (getattr(flow, "milestones", None) or [])
                  if m.status not in ("done", "superseded")), None)
    if _open is not None:
        # 진행 중 주기의 분해 회의 — 단위 추가만(주기 신설 금지 = 순차 1주기).
        if not units:
            return (f"진행 중 주기 {_open.ms_id}가 있어 새 주기는 열 수 없습니다(순차 1주기) — 이 회의가 "
                    f"그 주기의 분해라면 수렴안에 '단위: <목표> | <실증절차>' 줄을 동봉하세요."), 0
        _rls = getattr(flow, "backlog_relays", None) or {}
        _busy = [b.backlog_id for x in _open.subtasks if x.status not in ("done", "superseded")
                 and _rls.get(x.st_id) is not None
                 for b in _rls[x.st_id].backlogs if b.status not in ("done", "dropped")]
        if _busy:
            return (f"단위 추가 보류: 기존 백로그 {len(_busy)}건({' · '.join(_busy[:6])})이 아직 처리 중입니다 — "
                    f"단위·백로그 생성은 이전 것들이 종료(완료/중단)된 뒤에만 가능합니다(경계 생성)."), 0
        n = 0
        for u in units:
            st = open_subtask(flow, _open, u.partition("|")[0].strip(), parse_criteria_lines(u))
            if not isinstance(st, str):
                n += 1
        return _open, n
    # [GOAL 구조화(2026-07-14, 사용자: 'set_goal도 봇 지능 의지보다 구조적으로 제한')] Task GOAL은
    # 개인이 set_goal을 부를지 말지가 아니라 **회의 수렴안이 낳는다** — 첫 주기 수렴안의 '목표:' 줄이
    # 미확정 Task GOAL을 채운다. 이로써 목표 선행 게이트(gate_new_cycle)가 같은 회의 산물로 충족된다.
    _cur = getattr(flow, "current", None)
    if _cur is not None and not str(getattr(_cur.status, "goal", "") or "").strip():
        try:
            _cur.status.goal = goal
        except Exception:
            pass
    err = gate_new_cycle(flow)         # 신설 분기만 검문(목표 선행 등) — 단위 추가 분기는 위에서 자체 게이트
    if err:
        return err, 0
    if stages:
        flow.roadmap = stages          # 전체 구조(로드맵) — ckpt 동승(sys_recovery), 주기 완수마다 다음 단계 코칭
        _pnote(flow, "[로드맵] " + " → ".join(s[:40] for s in stages[:8]) + " (순차 1주기 — 각 주기 완수 시 보고 후 다음 단계 회의)")
    ms = open_milestone(flow, goal, parse_criteria_lines(crit), origin=f"회의 가결: {origin[:60]}")
    if isinstance(ms, str):
        return ms, 0
    n = 0
    for u in units:
        st = open_subtask(flow, ms, u.partition("|")[0].strip(), parse_criteria_lines(u))
        if not isinstance(st, str):
            n += 1
    # [채택 수렴안 → SYS가 파일 생성(2026-07-14, 사용자: '채택된 수렴안으로 SYS 내부에서 GOAL.md 등
    # 필요 파일 생성해줘야 해')] 봇이 "GOAL.md 없어 게이트 못 연다"던 파일을, 채택 산물로 시스템이 직접
    # materialize한다(.collab/T-<id>/ = 시스템 소유 — 봇에게 파일작성을 떠넘기지 않는다). GOAL.md는
    # 복구 파서(_parse_goal_doc) 계약 헤더 그대로 두고, 로드맵·단위·비준 기록은 CONSENSUS.md로 별도.
    try:
        from .._util import dossier_write, dossier_rel
        _cur2 = getattr(flow, "current", None)
        if _cur2 is not None:
            _acc = ("\n".join(f"- {c.desc}" + (f" (실증: {c.verify})" if getattr(c, "verify", "") else "")
                              for c in (ms.criteria or [])) or (getattr(_cur2, "acceptance", "") or ""))
            dossier_write(flow, "GOAL.md", (
                f"# GOAL — Task {_cur2.task_id}\n\n"
                f"## Purpose\n{(getattr(_cur2.status, 'purpose', '') or '').strip()}\n\n"
                f"## Goal\n{(goal or '').strip()}\n\n"
                f"## Acceptance\n{_acc.strip()}\n\n"
                f"## Standard\n{(getattr(_cur2, 'standard', '') or '').strip()}\n\n"
                f"## Interfaces\n{(getattr(_cur2, 'interfaces', '') or '').strip()}\n"))
            _units_md = "\n".join(f"- {u.partition('|')[0].strip()}" for u in units)
            dossier_write(flow, "CONSENSUS.md", (
                f"# CONSENSUS — 회의 채택 수렴안 (Task {_cur2.task_id}, 마일스톤 {ms.ms_id})\n\n"
                f"## 목표\n{(goal or '').strip()}\n\n"
                + (("## 로드맵\n" + " → ".join(stages) + "\n\n") if stages else "")
                + "## 완수조건\n" + ("\n".join(f"- {c.desc}" for c in (ms.criteria or [])) or "(없음)") + "\n\n"
                + (f"## 분해 단위(SubTask)\n{_units_md}\n\n" if _units_md else "")
                + f"## 채택\n회의 수렴안, 전원 찬성 표결로 채택 — {(origin or '')[:100]}\n"
                + f"\n(회의록 전문: {dossier_rel(_cur2.task_id)}/MINUTES.md)\n"))
    except Exception:
        pass
    return ms, n


# ── 회의 하나당 결론 하나 — 단계 체인(2026-07-14, 사용자) ────────────────────────
# GOAL → 마일스톤 → 서브태스크 → 백로그. 한 회의는 이 단계의 결론 '하나'만 정하고, SYS가 이전 결론을
# 토대로 다음 단계 회의를 연다. 겹침 방지: 단계는 상태에서 유도돼 한 번에 하나만 '현재'이고(아래 순차
# elif), 회의 자체는 단일 활성 베턴이라 두 회의가 동시에 못 돈다. 종전의 '수렴안 하나가 목표+마일스톤+
# 단위 다 만들기'(너무 큰 회의 — 라이브 ch70 32분 겉돎)를 대체한다.

def ledger_signature(flow):
    """[진전 기반 재픽(2026-07-20, 사용자: '상한 3회 경위가 옳은가')] 판 장부의 상태 서명 —
    사이클 전후 비교로 '이 사이클이 판을 전진시켰는가'를 판정한다. 발언·도구 사용(회의 소음)이
    아니라 **장부의 전진**만 센다: 목표 확정·로드맵·주기/단위 등록·상태 전이·충족조건 수·백로그 종결 수.
    재픽의 옳은 조건은 횟수가 아니라 이것 — 전진했으면 이어갈 가치가 있고(제한 없음, 상한은
    소유자 크레딧 캡이 담당), 무진전이면 한 번의 반복도 낭비다(사람 호출이 맞다).

    [무한 검증 봉합(2026-07-20, U-035 실측: iter 4·5·6차 내리 충족 1/4 고정인데 무한 continue)]
    종전엔 iter_n(검증 '시도' 횟수)을 진전으로 셌다 — 봇이 아무것도 못 채워도 report_iter만 돌리면
    iter_n↑ → '진전'으로 오판 → 정체 감지가 영영 불발(무한 루프). 진전 = **검증 결과(충족조건 수)**
    이지 시도 횟수가 아니다. iter_n을 충족조건 수(passed criteria)로 교체."""
    cur = getattr(flow, "current", None)
    sig = [bool(str(getattr(getattr(cur, "status", None), "goal", "") or "").strip()),
           tuple(getattr(flow, "roadmap", None) or [])]
    store = getattr(flow, "backlog_relays", None) or {}
    for m in (getattr(flow, "milestones", None) or []):
        sts = []
        for st in (getattr(m, "subtasks", None) or []):
            r = store.get(st.st_id)
            bl = getattr(r, "backlogs", None) or []
            sts.append((st.st_id, st.status,
                        sum(1 for b in bl if b.status in ("done", "dropped")), len(bl)))
        _passed = sum(1 for c in (getattr(m, "criteria", None) or []) if getattr(c, "passed", False))
        sig.append((m.ms_id, m.status, _passed, tuple(sts)))
    return tuple(sig)


def claim_kick_target(flow):
    """[첫 선점 킥 — 2026-07-19 e2e(P-032) 교착 수리] 작업 단계에서 '깨워 선점시킬' 다음 대상 하나를
    고른다 — (봇 id, Backlog, st_id) 또는 None(킥 불요).

    배경(라이브 실측): 회의가 백로그 5건을 등록하고 작업 단계로 전이했는데, 등록 반환문("각자
    pick_backlog로 전담하세요")은 회의를 연 앵커 세션에만 남고 이어가기도 앵커만 깨워 — 봇들이
    "선점해야 한다"고 말하면서 아무도 선점하지 않은 채 76분 공전 후 킥오프 소진 마감(ch79).
    구조 수리: 첫 착수를 SYS가 킥한다(자기 등재 원칙 그대로 — 제출자를 깨워 '네가 등재한 것을
    선점하라'. 배분권 개입 아님). 릴레이는 순차 1활성이라 첫 선점만 서면 이후는 마무리자 선정이 잇는다.

    규칙: 열린 주기의 미완 단위에서 ①in_progress가 하나라도 있으면 None(이미 손에 든 사람 있음)
    ②open 중 아직 킥 안 한(flow._claim_kicked) 첫 건의 제출자 — 봇이 킥을 씹으면 다음 open으로
    한 번씩만 확대(백로그당 1회 상한, 무한 wake 없음)."""
    mss = getattr(flow, "milestones", None) or []
    ms = next((m for m in mss if m.status not in ("done", "superseded")), None)
    if ms is None:
        return None
    store = getattr(flow, "backlog_relays", None) or {}
    kicked = getattr(flow, "_claim_kicked", None) or set()
    for st in ms.subtasks:
        if st.status in ("done", "superseded"):
            continue
        r = store.get(st.st_id)
        if r is None or not r.backlogs:
            continue
        if any(b.status == "in_progress" for b in r.backlogs):
            return None                          # 순차 1활성 — 진행 중이면 킥 불요
        for b in r.backlogs:
            if b.status != "open" or b.backlog_id in kicked:
                continue
            if int(b.submitter or 0):
                return (int(b.submitter), b, st.st_id)
            # [무주 백로그 킥(2026-07-20, U-035 실측)] 회의 등록분은 이의 개서로 발제 귀속이 유실될 수
            # 있다(_draft_attr 키 드리프트 → submitter 0) — 무주면 킥이 침묵해 ch79형 '아무도 선점 안
            # 함' 공전이 재발한다. 적임(role_fit) 봇을 깨워 '적임자로서 선점 검토' — 배분 강제가 아니라
            # 첫 착수 신호(자기선택은 pick 시점에 유지).
            _bots = {int(k): str(v or "") for k, v in (getattr(flow, "bot_info", None) or {}).items()}
            if _bots:
                from ..role_fit import role_fit as _rf
                _q = f"{getattr(st, 'goal', '')} {b.body}"
                _bid = max(_bots, key=lambda k: _rf(_q, _bots[k]))
                return (int(_bid), b, st.st_id)
    return None


def meeting_stage(flow):
    """현 상태에서 이 회의가 정할 단 하나를 도출. 'goal'|'milestone'|'subtask'|'backlog'|None(작업 단계)."""
    _cur = getattr(flow, "current", None)
    if _cur is None:
        return None
    if not str(getattr(_cur.status, "goal", "") or "").strip():
        return "goal"                                   # ① Task 회의 — GOAL 미정
    _mss = getattr(flow, "milestones", None) or []
    _open = next((m for m in _mss if m.status not in ("done", "superseded")), None)
    if _open is None:
        # ② 마일스톤 회의 — 열린 주기 없음. 단 로드맵이 소진됐으면 더 열 주기 없음(무한 마일스톤 회의
        # 방지). 첫 마일스톤(아직 아무것도 없음)이거나, 로드맵에 안 지은 단계가 남았을 때만 연다.
        # [phase 정규화(2026-07-20)] 한 줄 화살표 로드맵(구 판 복원분)도 phase 수로 바로 센다 —
        # 종전 len(raw)=1이면 2단계부터 회의가 영영 안 열렸다(이월 수신처 소멸과 같은 뿌리).
        _road = roadmap_phases(flow)
        _done = [m for m in _mss if m.status == "done"]
        if not _mss or (_road and len(_done) < len(_road)):
            return "milestone"
        return None                                     # 로드맵 소진 → 작업/완료 단계
    _sts = [st for st in _open.subtasks if st.status != "superseded"]
    if not _sts:
        return "subtask"                                # ③ 서브태스크 회의 — 단위 미분해
    store = getattr(flow, "backlog_relays", None) or {}
    # [iter 주기 정본 복원(2026-07-20, 사용자: '현존하는 모든 백로그를 끝내거나 중지하고 → 점검 →
    # 다음 회의가 다수를 한번에')] 07-17 '게으른 스캔'이 이를 '앞 영역 소진 → 다음 영역 회의'(영역당
    # 회의 1개씩 순차)로 좁혀놨던 표류 교정. 정본: **어디든 집을 백로그가 하나라도 있으면 작업 단계** —
    # 회의는 전 영역이 소진(또는 첫 시작으로 전무)됐을 때만 열리고, 그 한 회의가 미충원 영역들 몫을
    # 일괄 충전한다(iter 경계 = 소진→점검(report_iter 코칭+조건 장부)→일괄 충전).
    _alive = [st for st in _sts if st.status != "done"]
    if any((store.get(st.st_id) is not None and (store.get(st.st_id).backlogs or [])
            and not store.get(st.st_id).all_done()) for st in _alive):
        return None                                     # 집을/진행 중 백로그 존재 → 작업 단계 우선
    if any(store.get(st.st_id) is None or not store.get(st.st_id).backlogs for st in _alive):
        return "backlog"                                # ④ 백로그 회의 — 소진/전무: 다음 iter 일감 일괄 충전
    # [백로그 소진 = 회의 트리거(2026-07-16, 잔재 감사 ①)] 전 단위의 백로그가 소진(전부 done/dropped)
    # 됐는데 주기가 아직 열려 있으면(조건 미충족) 추가 분해 회의 — 종전엔 handoff 코칭('meet를 열어라')
    # 만 있고 stage가 None이라, 봇이 meet를 불러도 결론 경로가 없었다(수렴 소진 낭비). 체인이 자동 개설.
    if _alive and _open.status == "open":
        return "subtask"
    return None                                          # 전 단계 완료 → 작업/검증 단계


# [통일 수렴안 + 구체 질문(2026-07-15, 사용자 가안)] 모든 회의의 산출물 = [수렴안](하나의 통일
# 메커니즘). 그 수렴안을 어떻게 가공할지는 각 단계(register_stage)의 몫. 핵심은 각 안건을 봇이 오해할
# 수 없는 **구체적 평문 질문(★)**으로 못박아, "작업 분배·일정·담당자 잡담"으로 도망치지 못하게 한다
# (라이브 ch71: 스코프 회의인데 봇들이 섹션 나누기·병렬화만 논의 — 회의 정체를 몰라서).
_STAGE_META = {
    "goal": ("이 Task로 **무엇을 만들지**와 **무엇이 되면 끝인지**를 정한다",
             "[수렴안]\n목표: <이 Task로 정확히 무엇을 만드는지 — '게임'이 아니라 '2인 턴제 카드 대전'처럼 구체적으로>\n"
             "<완수조건 | 실증절차(run으로 확인)>\n<완수조건 | 실증절차>\n[/수렴안]\n"
             "★이 회의가 답할 질문 하나: **'이걸로 정확히 무엇을 만들고, 무엇이 되면 끝인가?'** "
             "— 작업을 어떻게 나눌지·누가 맡을지·일정은 **지금 논의하지 마세요**(그건 다음 회의들). "
             "지금은 '무엇을 만들지'만 정합니다."),
    "milestone": ("이번에 **완성해서 사용자에게 보여줄 딱 하나**를 정한다(전체 말고 이번 것)",
             "[수렴안]\n단계: <전체 로드맵 — 예: 최소버전 → 확장 → 완성>\n"
             "이번 주기: <이번에 완성해 사용자가 실제로 써볼 수 있는 딱 하나>\n"
             "<완수조건 | 실증절차>\n[/수렴안]\n"
             "★이 회의가 답할 질문 하나: **'이번에 완성해서 사용자에게 보여줄 하나는 무엇인가?'** "
             "— 전체를 한 번에 만들려 하지 마세요(달구지부터). 작업 분해·담당자는 다음 회의. "
             "**완수조건은 '이번 주기' 범위만** — 뒤 단계 몫(모션 세부·디자인 토큰·폴리시 같은 완제품 "
             "사양)을 여기 넣으면 이번 주기가 영영 안 끝납니다(그건 그 단계 주기의 조건으로)."),
    "subtask": ("이번에 만들 것을 **어떤 작업 영역(구성요소)들로 나눌지** 정한다",
             "[수렴안]\n단위: <작업 영역/구성요소 — 무슨 부분인지> | <실증절차>\n"
             "단위: <작업 영역/구성요소> | <실증절차>\n[/수렴안]\n"
             "★이 회의가 답할 질문 하나: **'이번 것을 어떤 작업 영역(덩어리)들로 쪼갤 것인가?'** — 이건 "
             "**누가 맡느냐가 아니라 순수한 작업 분리**입니다(예: 저장 계층 · 게임 로직 · 화면 UI). 한 영역을 "
             "여러 명이 나눠 할 수도 있습니다. 개인이 하나씩 맡는 건 그 영역 안의 백로그(다음 회의)입니다."),
    "backlog": ("미충원 작업 영역들의 **다음 일감 전부**를 한 번에 열거한다(처리는 하나씩 선점)",
             "[수렴안]\n백로그: [영역명] <구체 작업 1>\n백로그: [영역명] <구체 작업 2>\n백로그: [영역명] <구체 작업 …(각 영역 완수에 필요한 만큼 — 줄 수 제한 없음)>\n[/수렴안]\n"
             "★이 회의가 답할 질문 하나: **'미충원 영역들을 완수 기준까지 끌고 가는 데 필요한 작업 항목 "
             "전부는 무엇인가?'** — 항목마다 [영역명]을 달아 어느 영역 몫인지 명시하고, 한두 개만 남기지 "
             "마세요(이 목록이 다음 iter의 연료 — 소진되면 점검 후에야 다음 회의). "
             # [일감 굵기 경제(2026-07-21, U-037 실측: 30건 잘게 쪼개기 — 건당 선점·핸드오프·보고
             # 오버헤드 5~10cr × 건수가 실작업비를 압도)] 내용 판단 아님 — 단위 경제의 형식 코칭.
             "**일감은 '실증 한 번으로 닫히는 묶음' 단위로 굵게** 잡으세요 — 파일 하나·함수 하나 "
             "수준으로 잘게 쪼개면 항목마다 선점·인계·보고 비용이 붙어 실작업보다 오버헤드가 커집니다"
             "(영역당 대략 3~7건이 보통 적당 — 상한이 아니라 경제 감각). 처리는 각자 "
             "pick_backlog로 하나씩 전담합니다."),
}


def stage_agenda(stage):
    """meet()가 쓰는 (안건 설명, 수렴안 템플릿). 알 수 없으면 (None, None)."""
    m = _STAGE_META.get(stage)
    return (m[0], m[1]) if m else (None, None)


# ── 수렴안 = 공동 편집 파일(DRAFT.md) (2026-07-16, 사용자: '하나의 수렴안을 파일로 두고 고도화,
# git 코멘트처럼 상호보완으로 하나의 큰 결론에') ─────────────────────────────────────────
# 한 봇이 수렴안 전체를 한 발화로 생성·병합하는 인지 과부하(+준중앙 병합자)를 제거 — 회의 개시 때
# SYS가 골격을 깔고, 참여자들이 자기 도메인 몫을 직접 편집·이의 코멘트·해소하며 파일에서 통합된다.
# 종결 = 골격 완성(자리표시 0)+미해소 이의 0+직전 턴 무변경 → 전원 최종 표결 → 그 파일이 결론.

def stage_draft_template(stage, agenda=""):
    """회의 개시 때 SYS가 까는 DRAFT.md 골격. 알 수 없는 단계면 None."""
    body = {
        "goal": ("목표: <이 Task로 정확히 무엇을 만드는지 — 구체적으로>\n"
                 # [구성 점검 경로 평등(2026-07-21, U-038 실측: 팀 밖 게임 기획자에게 후속 담당을 걸고
                 # 가결)] set_goal 도구 경로에만 있던 '[구성 점검]'(2026-07-13 설계)을 회의 골격에도 —
                 # 자리표시라 안 채우면 초안이 완성되지 않고, 부족 직군의 정경로(recruit·후보 대기)가
                 # 그 자리에서 상기된다.
                 "구성 점검: <원문에 필요한 직군이 이 팀에 다 있는가 — 부족하면 그 직군과 recruit 계획, "
                 "충분하면 '충분' 판단 근거 한 줄>\n\n완수조건:\n"
                 "- <조건> | 실증: <run으로 확인하는 절차>\n- <조건> | 실증: <절차>\n"),
        # [완수조건 = 이번 주기 범위(2026-07-20, U-035 rung1)] 최소버전 주기에 모션 타이밍·디자인
        # 토큰 등 완제품 전량이 조건으로 실려 met이 영구 미달(1/4 고정) → 재협상 dead-end로 빠지던
        # 상류 방아쇠 — 회의 골격이 스코프를 못박는다(뒤 단계 몫은 그 단계 주기의 조건).
        "milestone": ("단계: <전체 로드맵 — 예: 최소버전 → 확장>\n"
                      "이번 주기: <이번에 완성해 사용자에게 보여줄 딱 하나>\n\n완수조건:\n"
                      "(주의: **'이번 주기' 범위의 조건만** — 뒤 단계 몫(모션 세부·디자인 토큰 등 완제품 "
                      "사양)을 넣으면 이번 주기가 영영 안 끝납니다. 그건 그 단계 주기에서.)\n"
                      "- <조건> | 실증: <절차>\n"),
        "subtask": ("단위: <작업 영역/구성요소> | 실증: <절차>\n단위: <작업 영역/구성요소> | 실증: <절차>\n"),
        "backlog": ("백로그: [영역명] <구체 작업 1>\n백로그: [영역명] <구체 작업 2>\n백로그: [영역명] <구체 작업 …(필요한 만큼)>\n"),
    }.get(stage)
    if not body:
        return None
    return (f"# DRAFT [stage:{stage}] — {agenda}\n"
            "(공동 결론 파일 — 규칙: ①자기 도메인 몫을 직접 편집해 채우고 구체화하세요 ②이견은 해당 줄 "
            "바로 아래 '> [이의 @직군] 한 줄'로 남기세요 ③이의를 해소한 사람이 그 이의 줄을 삭제하세요 "
            "④꺾쇠 자리표시가 남아 있으면 미완입니다 — **`<…>`는 '지금 이 회의가 채울 곳'에만.** 뒤 "
            "단계(서브태스크·백로그·후속 협의)에서 정할 세부는 꺾쇠 없이 '(후속: …)'로 쓰거나 참고 구획으로 "
            "— 결정 구획의 꺾쇠는 기계 집계돼 회의가 안 닫힙니다. 단 **이 회의가 정할 그 하나(키 줄)를 "
            "통째로 '(후속: …)'로 미루면 빈칸과 같아 등록되지 않습니다.** 이 판에는 달력·날짜 스케줄러가 "
            "없습니다 — 기한·마감은 날짜가 아니라 파이프라인 사건('다음 회의 전'·'이 주기 안')으로 쓰세요.\n"
            "⑤ **'## 결정' 구획만 표결·완성 판정의 대상**입니다 — 그 아래 '## 참고'엔 근거·설계 메모를 "
            "자유롭게 쌓되, 결정을 바꾸려면 결정 구획을 직접 고치세요. 등록되는 결론 = 결정 구획.)\n\n"
            "## 결정\n\n" + body + "\n## 참고 (자유 — 판정 대상 아님)\n")


def draft_decision_region(text):
    """[과녁 고정(2026-07-16, 사용자: 'SYS 내용 판단 없이 구조로')] '## 결정' 구획만 추출 — 완성 판정·
    표결·이의 기록·안정 해시가 전부 이 구획만 본다. 봇이 밖에 참고 섹션을 아무리 늘려도 반대 표면이
    안 자란다(발산 되먹임 차단). 구획 마커 없으면(구버전 초안) 전체 반환(호환)."""
    import re as _re
    t = str(text or "")
    m = _re.search(r"^## 결정\s*$", t, _re.M)   # 줄 시작 헤딩만 — 규칙 문장 속 '## 결정' 인용 오매치 방지
    if not m:
        return t
    i = m.start()
    j = t.find("\n## ", m.end())
    return t[i:j] if j > 0 else t[i:]


_STAGE_KEY = {"goal": "목표", "milestone": "이번 주기", "subtask": "단위:", "backlog": "백로그:"}


def deferred_only(v):
    """[결정 없는 결정 칸(2026-07-21, U-038 실측)] 값이 '(후속: …)' 미룸 문구로 시작하면 결정이 아니라
    미룸 — 빈칸과 동형이다. 골격 규칙('지금 못 정하는 세부면 후속으로')은 세부에 쓰라는 것이지, 그
    회의가 정할 그 하나를 통째로 미루는 용도가 아니다. 내용 무판단 — 형태(미룸 전용)만 본다."""
    s = str(v or "").strip().lstrip("*").strip()
    return s.startswith("(후속") or s.startswith("후속:") or s.startswith("후속：")


def draft_should_reset(stage, existing) -> bool:
    """[흐름 재개 안전 불변식(2026-07-21, 사용자: '흐름 중엔 아무리 재시작해도 상관없다 — 재복구가
    있으니 안전하게 재개돼야')] 회의 개시가 DRAFT 골격을 새로 깔지(True), 진행분을 보존할지(False).
    같은 단계의 DRAFT가 이미 디스크에 있으면 **절대 리셋하지 않는다** — 러너 재시작(토큰·서버·사용자
    중지 등 어떤 이유든)으로 회의가 중단됐다가 재개돼도 봇들이 채워온 결론이 살아 있어야 한다.
    이 판정이 재시작-안전의 정본(회의 개시부·복구 경로가 공유). 새 단계이거나 초안 부재면 새 골격."""
    return existing is None or f"[stage:{stage}]" not in str(existing)


def draft_missing_key(stage, text):
    """[등록 형식 기계검사(2026-07-17, ch77 밤샘 루프)] 가결돼도 등록기가 요구하는 키 줄('목표:' 등)이
    결정 구획에 없으면 거부→소진→재킥오프 무한. ready 전에 키 부재를 잡아 형식 이의로 코칭. 내용 무판단.
    [확장(2026-07-21, U-038)] 키 줄이 있어도 값이 '(후속: …)' 미룸뿐이면 부재와 동형으로 잡는다."""
    k = _STAGE_KEY.get(stage)
    if not k:
        return None
    region = draft_decision_region(text)
    for l in region.splitlines():
        ls = l.strip().lstrip("*")
        if ls.startswith(k):
            v = ls.split(":", 1)[1].strip() if ":" in ls else ""
            return k if deferred_only(v) else None
    return k


def draft_status(text):
    """DRAFT 상태 → (자리표시 수, 미해소 이의 수). '## 결정' 구획만 심사."""
    import re as _re
    t = draft_decision_region(text)
    ph = len(_re.findall(r"<[^>\n]{2,60}>", t))
    obj = len(_re.findall(r"^\s*>", t, _re.M))   # 구획 내 인용(>) 줄 = 미해소(해소=삭제)
    return ph, obj


def draft_norm_line(ln):
    """DRAFT 한 줄 정규화 — 발제 귀속(diff 추적)과 등록 파싱이 같은 키를 쓰게 하는 단일 함수."""
    import re as _re
    s = str(ln or "").strip()
    if (not s or s.startswith("#") or s.startswith("(") or s.startswith(">")
            or s == "완수조건:"):
        return None
    s = _re.sub(r"^-\s*", "", s)
    # [라벨 정본화(2026-07-17, ch78 실측)] 종전엔 '| 실증:'을 제거해 맨 '|'만 남겼는데, 그러면 조건
    # 본문의 파이프(JSON enum(buy|pass|…))와 구분자가 구별 불가 — 스펙 산문이 조건으로 오인돼 등록을
    # 막았다. 라벨을 지우지 않고 변형('검증:'·'로그 검증:'·'측정:')까지 '| 실증:'으로 통일 — 이 정본
    # 토큰이 조건 선별·분리의 유일 구분자.
    s = _re.sub(r"\|\s*(?:[\w가-힣]{0,4}\s*)?(?:실증|검증|측정)\s*[:：]\s*", "| 실증: ", s)
    return s


def draft_to_proposal(stage, text):
    """채택된 DRAFT('## 결정' 구획) → register_stage 입력 정규화. 참고 구획은 파일에 남되 등록 안 됨.
    [최상위 줄만(2026-07-17, ch78 실측)] 들여쓴 줄은 마크다운 중첩 = 위 조건의 설명 연속이지 독립
    조건·키가 아니다 — '  ⑥종료 상태: … | 【…】' 같은 하위 설명이 조건으로 오인돼 등록을 막던 것 차단."""
    region = draft_decision_region(text)
    out = [n for n in (draft_norm_line(l) for l in region.splitlines() if not l[:1].isspace())
           if n and not n.startswith("## ")]
    return "\n".join(out)


def parse_units(lines):
    """'단위:' 항목 수집 — 등록·preflight 공용(같은 파싱 = 같은 판정).
    한 줄 정식(단위: <목표> | 실증: <절차>)에 더해, 제목만 쓴 '단위:' 줄의 본문이 **바로 다음
    최상위 줄**(| 포함, 새 키 아님)에 오는 자연 표기를 흡수한다 — U-035 실측: 봇 전원이 제목/본문
    2줄로 썼고 파서가 제목만 집어 6개 단위 전부 '조건 없음'으로 전멸, 회의만 3회 재개설."""
    ls = list(lines or [])
    out = []
    for i, l in enumerate(ls):
        s = str(l).strip()
        if not s.startswith("단위:"):
            continue
        u = s[3:].strip()
        if "|" not in u:
            nx = next((str(x).strip() for x in ls[i + 1:] if str(x).strip()), "")
            if "|" in nx and not nx.startswith(("단위:", "단계:", "백로그:", "##")):
                # '|'로 잇는다 — 제목이 goal(파이프 앞)로 남고 본문은 조건 쪽으로. '—'로 이으면
                # 본문·[게이트]까지 통째 goal이 돼 단계 이름이 벽문장에 압사(U-035 표면 실측).
                u = f"{u} | {nx}" if u else nx
        if u:
            out.append(u)
    return out


def stage_preflight(stage, text):
    """[등록 사전 검사(2026-07-17, ch78 실측: 표결 가결 후 등록 거부 사이클 6~9분×N — 봇 비용 낭비)]
    register_stage와 **같은 파싱**으로 표결 전에 불량을 전부 찾는다(봇 비용 0, 상태 변경 없음).
    반환: 에러 목록(list[str]) — 비면 통과. 표결·등록의 이중 발견을 게이트 시점 단일 발견으로."""
    import re as _re
    prop = draft_to_proposal(stage, text)
    lines = [l.replace("**", "") for l in str(prop or "").splitlines()]
    errs = []

    def _val(prefix):
        return next((l.split(":", 1)[1].strip() for l in lines
                     if l.strip().startswith(prefix) and ":" in l), "")
    if stage == "goal" and not _val("목표"):
        errs.append("'목표: <이 Task로 정확히 무엇을 만드는지>' 줄이 필요합니다(줄 시작, 장식 없이).")
    if stage == "milestone" and not (_val("이번 주기") or _val("목표")):
        errs.append("'이번 주기: <이번에 보여줄 딱 하나>' 줄이 필요합니다(줄 시작, 장식 없이).")
    if stage in ("goal", "milestone"):
        _ct = "\n".join(l for l in lines if _crit_delim().search(l)
                        and not l.strip().startswith(("단위:", "단계:", "백로그:")))
        _e = gate_criteria(parse_criteria_lines(_ct))
        if _e:
            errs.extend(ln for ln in _e.splitlines() if ln.strip())
    if stage == "subtask":
        _units = parse_units(lines)
        if not _units:
            errs.append("'단위: <작업 영역> | 실증: <절차>' 줄이 1개 이상 필요합니다.")
        # [등록과 같은 깊이(2026-07-20, U-035 실측)] 존재만 보고 통과시키면 표결 가결 후 등록
        # (open_subtask=gate_criteria)에서 전멸 — 단위별 조건 게이트를 표결 전에 그대로 돌린다.
        for u in _units:
            _e = gate_criteria(parse_criteria_lines(u))
            if _e:
                errs.extend(f"단위 '{u.partition('|')[0].strip()[:36]}': {ln.strip()}"
                            for ln in _e.splitlines() if ln.strip())
        # [영역 중복 반려(2026-07-21, U-039 실측·사용자: '구조가 이상하면 반려되어야지 — 근본을
        # 해결')] 서브태스크는 '순수 작업 영역 분리'인데 near-중복 영역(백엔드 스키마 '1차'/'2차'처럼
        # 목표 토큰이 거의 같은 둘)이 나오면, 백로그 배정([영역명]→서브태스크 토큰 매칭)이 한쪽으로
        # 쏠려 다른 쪽이 굶는다(ST-3/6/7 백로그 0의 근본). 표결 전에 형식 이의로 되돌려 회의가
        # 합치거나 영역을 뚜렷이 가르게 한다 — 내용 판단 아님(토큰 겹침이라는 구조 신호만).
        import re as _reu
        def _atoks(u):   # 영역 제목(파이프 앞)의 유의미 토큰(1·2·차 같은 서수·조사 제외)
            _t = u.partition("|")[0]
            return {w for w in _reu.findall(r"[A-Za-z가-힣]{2,}", _t)
                    if w not in ("정의", "구현", "작성", "설정", "단계", "기능", "처리")}
        _uts = [(u.partition("|")[0].strip()[:36], _atoks(u)) for u in _units]
        for _i in range(len(_uts)):
            for _j in range(_i + 1, len(_uts)):
                _a, _b = _uts[_i][1], _uts[_j][1]
                # 작은 쪽 대비 겹침 — 한 영역이 다른 영역에 거의 포함('스키마 1차'⊂'스키마')되면 중복
                if _a and _b and len(_a & _b) / max(min(len(_a), len(_b)), 1) >= 0.7:
                    errs.append(f"영역 중복 — '{_uts[_i][0]}'와 '{_uts[_j][0]}'의 작업 영역이 거의 "
                                f"같습니다(백로그 배정이 한쪽으로 쏠려 다른 쪽이 빕니다). 하나로 합치거나 "
                                f"영역을 서로 뚜렷이 다른 이름으로 가르세요('1차/2차'식 세부 쪼개기 금지 — "
                                f"그건 그 영역 안의 백로그입니다).")
    if stage == "backlog" and not any(l.strip().startswith("백로그:") for l in lines):
        errs.append("'백로그: <구체 작업>' 줄이 1개 이상 필요합니다.")
    return errs


def stage_context(flow, stage):
    """[안건 타깃 명시(2026-07-16, 정합 감사 A)] 이 단계 회의가 딛고 선 이전 결론을 안건에 못박는다 —
    특히 백로그 회의는 단위마다 열리는데 '어느 단위' 회의인지 없으면 논의와 등록(첫 미충원 단위)이
    어긋난다. 반환: ' [대상 …: …]' 접미 또는 ''."""
    try:
        _cur = getattr(flow, "current", None)
        if stage == "milestone" and _cur is not None:
            g = str(getattr(_cur.status, "goal", "") or "").strip()
            return f" [확정된 GOAL: {g[:80]}]" if g else ""
        _open = next((m for m in (getattr(flow, "milestones", None) or [])
                      if m.status not in ("done", "superseded")), None)
        if stage == "subtask" and _open is not None:
            return f" [이번 주기: {_open.goal[:80]}]"
        if stage == "backlog" and _open is not None:
            store = getattr(flow, "backlog_relays", None) or {}
            _es = [st for st in _open.subtasks if st.status not in ("done", "superseded")
                   and (store.get(st.st_id) is None or not store.get(st.st_id).backlogs)]
            if _es:
                _names = " · ".join(str(st.goal or "").split(" — ")[0].split(" | ")[0][:24] for st in _es[:7])
                return f" [미충원 영역: {_names}]"
    except Exception:
        pass
    return ""


# [매 발언 턴에 스테이지 프레임(2026-07-15, 사용자: '구성원이 지금 어떤 정보를 얻고 있나')] 종결표결
# 때만 뜨던 '이 회의가 무엇을 정하는 자리인지 + 작업분배 금지'를 매 토론 턴에 주입한다 — 안 그러면
# 봇이 초반엔 안건에 답하다 몇 턴 뒤 자기 도메인 작업조직으로 드리프트한다(라이브 ch71/72). 파이프라인
# 위치도 알려 '지금은 이 단계, 그건 다음 회의'를 매 턴 상기시킨다.
_STAGE_FRAME = {
    "goal": "지금은 **이 Task로 무엇을 만들지**를 정하는 단계입니다(사용자 요청을 푸는 파이프라인의 첫 "
            "관문). 딱 그 질문 — **'정확히 무엇을 만들고, 무엇이 되면 끝인가'** — 에만 답하세요. 작업을 "
            "어떻게 나눌지·누가 맡을지·섹션 구성·일정은 **여기서 정하지 마세요**(그건 다음 회의들입니다).",
    "milestone": "지금은 **이번에 완성해서 사용자에게 보여줄 딱 하나**를 정하는 단계입니다. 전체를 한 번에 "
            "만들려 하지 말고(달구지부터), **'이번에 완성해 보여줄 하나는?'** 에만 답하세요. 작업 분해·"
            "담당자·일정은 다음 회의.",
    "subtask": "지금은 이번 것을 **어떤 작업 영역(덩어리)으로 나눌지** 정하는 단계입니다 — 개인 배정이 "
            "아니라 순수 작업 분리(예: 저장 계층·게임 로직·화면 UI). **'어떤 영역들로 쪼갤까'** 에만 답하세요. "
            "구체 작업 항목·담당은 다음(백로그) 회의.",
    "backlog": "지금은 미충원 작업 영역들의 **다음 일감 전부를 한 번에 열거**하는 단계입니다. "
            "**'미충원 영역들 완수에 필요한 작업 항목 전부는?'** 에만 답하세요 — 항목마다 [영역명]을 "
            "달고, 한두 개로 끝내지 말고 목록을 채우세요(처리는 나중에 하나씩 선점).",
}


def stage_frame(stage):
    """매 토론 발언 턴에 주입할 스테이지 프레임(이 회의의 정체 + 작업분배 금지 가드) — 없으면 ''."""
    return _STAGE_FRAME.get(stage, "")


def _write_goal_md(flow, cur, goal):
    try:
        from .._util import dossier_write
        dossier_write(flow, "GOAL.md", (
            f"# GOAL — Task {cur.task_id}\n\n"
            f"## Purpose\n{(getattr(cur.status, 'purpose', '') or '').strip()}\n\n"
            f"## Goal\n{(goal or '').strip()}\n\n"
            f"## Acceptance\n{(getattr(cur, 'acceptance', '') or '').strip()}\n\n"
            f"## Standard\n{(getattr(cur, 'standard', '') or '').strip()}\n\n"
            f"## Interfaces\n{(getattr(cur, 'interfaces', '') or '').strip()}\n"))
    except Exception:
        pass


def register_stage(flow, stage, prop, origin=""):
    """그 회의의 결론 '하나'만 등록. 반환 (landed: bool, note: str). 게이트 거부·형식 미달=(False, 사유)."""
    import re as _re
    # [자리표시 가드(2026-07-16, 정합 감사 C)] 봇이 템플릿을 에코하면 '<…>' 자리표시 그대로 제출됨 —
    # 실값 없는 껍데기 등록 차단(비준 낭비 전에 여기서도 방어).
    if _re.search(r"<[^>\n]{2,60}>", str(prop or "")):
        return False, "수렴안에 템플릿 자리표시(<...>)가 남아 있습니다 — 실제 값으로 채워 다시 제출하세요."
    # [마크다운 장식 무력화(2026-07-17, ch78 실측: '**이번 주기:**' 볼드로 키 매칭 실패 → 폴백 제목 오등록)]
    lines = [l.replace("**", "") for l in str(prop or "").splitlines()]

    def _val(prefix):
        return next((l.split(":", 1)[1].strip() for l in lines
                     if l.strip().startswith(prefix) and ":" in l), "")
    # [조건 선별 = 라벨 구분자(2026-07-17)] 맨 '|' 포함 줄 전부가 아니라 '| 실증:'류 라벨이 있는 줄만
    # 조건이다 — 파이프 든 스펙 산문(JSON enum 등)이 조건 게이트로 쓸려 들어와 등록을 막던 것 차단.
    _crit_txt = "\n".join(l for l in lines if _crit_delim().search(l)
                          and not l.strip().startswith(("단위:", "단계:", "백로그:")))
    _cur = getattr(flow, "current", None)

    if stage == "goal":
        # [통일 수렴안(가안)] prop = [수렴안]의 '목표:'+조건 줄들. goal 단계는 이 수렴안을 가공해
        # Task GOAL을 세팅하고 GOAL.md를 쓴다(수렴안=통일 산출물, 가공은 이 단계의 몫).
        goal = _val("목표")
        if not goal:
            return False, "수렴안에 '목표: <이 Task로 정확히 무엇을 만드는지>' 줄이 필요합니다."
        # [결정 없는 결정 칸 거부(2026-07-21, U-038 실측: 목표='(후속: 기획 단계에서 확정 — 담당·
        # 날짜)'가 부결 2회에도 소진-확정으로 등록 → GOAL이 빈 채 판이 굴러감)] 미룸 전용 값은 빈칸과
        # 동형 — 종결 보장(부결 소진·이월 확정)이 '결정 없는 결론'을 밀 수 없게 등록이 최종 방어선.
        if deferred_only(goal):
            return False, ("'목표:'가 후속 미룸 문구뿐입니다 — 이 회의가 정할 그 하나(무엇을 만드는지)는 "
                           "여기서 결정해야 합니다. 미룸(후속)은 세부에만 쓰세요. 결정에 필요한 직군이 "
                           "팀에 없으면 recruit로 충원하거나(후보 대기 우선), 정말 결정 불가면 그대로 "
                           "말하세요 — 사람 확인으로 넘어갑니다.")
        if _cur is not None:
            try:
                _cur.status.goal = goal
            except Exception:
                pass
            _crits = parse_criteria_lines(_crit_txt)
            if _crits:
                try:
                    _cur.acceptance = "\n".join(f"- {c.desc}" + (f" (실증: {c.verify})" if c.verify else "")
                                                for c in _crits)
                except Exception:
                    pass
            _write_goal_md(flow, _cur, goal)   # 수렴안 가공 결과를 영속 파일로(복구 파서 계약 헤더)
        if flow.log:
            flow.log("task_goal_set", goal=goal[:60])
        return True, (f"[표결 확정] GOAL 확정 → {goal[:80]} · GOAL.md 작성. "
                      "다음: 마일스톤 회의를 시스템이 엽니다.")

    if stage == "milestone":
        # [대체 방지 게이트(2026-07-16, 정합 감사 B)] open_milestone 직행은 열린 주기를 조용히 대체
        # (supersede)할 수 있다 — meeting_stage가 평시엔 막지만, 방어선(gate_new_cycle: 목표 선행·
        # 미완 주기 보호·내용 주기 보호)을 표결 경로와 동일하게 지난다(U-019 판 파기 재발 방지).
        _gerr = gate_new_cycle(flow)
        if _gerr:
            return False, _gerr
        cyc = _val("이번 주기") or _val("목표") or (str(getattr(_cur.status, "goal", "") or "") if _cur else "")
        if deferred_only(cyc):
            return False, ("'이번 주기:'가 후속 미룸 문구뿐입니다 — 이번에 완성해 보여줄 딱 하나는 "
                           "이 회의가 결정해야 합니다(미룸은 세부에만).")
        # [과정 서술 반려(2026-07-21, U-039 실측·사용자: '마일스톤 주제가 모호하게 잡힘 — 구체적으로')]
        # '이번 주기'가 '…확정되는 단계'·'…검증하는 단계'처럼 **과정 서술**이면 완성 실물이 아니라
        # 활동을 결론에 앉힌 것 — 모호함의 뿌리. 실물(무엇을 완성해 보여주나)로 다시 쓰게 반려한다
        # (내용 판단 아님 — '되는/하는 단계'라는 과정형 어미의 구조 신호만).
        import re as _rec
        if _rec.search(r"(되는|하는|위한|준비|확정|검증)\s*단계\s*$", str(cyc).strip()):
            return False, ("'이번 주기'가 '…되는/하는 단계'라는 **과정 서술**입니다 — 이번에 **완성해 "
                           "사용자에게 보여줄 실물 하나**(브라우저에서 도는 것·문서 산출물 등 완성 단위)로 "
                           "쓰세요. 그 과정(검증·확정)은 이 주기 안의 작업이지 완성 단위가 아닙니다.")
        stages = [l.split(":", 1)[1].strip() for l in lines if l.strip().startswith("단계:")]
        # [phase 정규화(2026-07-20)] 골격이 유도하는 '한 줄 화살표' 표기(최소버전 → 확장)를 등록
        # 시점에 phase 목록으로 분해 — 안 하면 로드맵이 1개로 세어져 다음 주기 회의·이월 수신처가
        # 없다(띄운 화살표만 구분자 — roadmap_phases와 같은 계약).
        stages = [p.strip() for s in stages for p in re.split(r"\s+→\s+", s) if p.strip()]
        _road = ""
        if stages:
            flow.roadmap = stages
            # [로드맵 = 회의 결론(2026-07-21, 사용자: '제목 아래 그 칸이 결론 칸 — 저건 회의의 결론에
            # 들어가야')] 종전 독립 '[로드맵]' 게시가 회의와 마일스톤 사이 고아 '계획' 블록으로 떠
            # 있었다 — 이 등록 노트가 [회의 마무리]로 회의 블록 결론 칸에 실리므로, 계획(전체 단계열과
            # 이번 주기의 좌표)은 그 결론의 한 줄로 산다(별도 게시 폐지).
            _k = sum(1 for m in (getattr(flow, "milestones", None) or [])
                     if getattr(m, "status", "") == "done") + 1
            _road = ("\n계획: " + " → ".join(s[:40] for s in stages[:8])
                     + f" (이번 주기 = {_k}단계)")
        ms = open_milestone(flow, cyc or "이번 주기", parse_criteria_lines(_crit_txt),
                            origin=f"마일스톤 회의: {origin[:50]}")
        if isinstance(ms, str):
            return False, ms
        if flow.log:
            flow.log("ms_by_meeting", ms=ms.ms_id)
        return True, (f"[표결 확정] 마일스톤 {ms.ms_id} 등록(조건 {len(ms.criteria)}개).{_road}\n"
                      "다음: 서브태스크 회의(단위 분해)를 시스템이 엽니다.")

    if stage == "subtask":
        _open = next((m for m in (getattr(flow, "milestones", None) or [])
                      if m.status not in ("done", "superseded")), None)
        if _open is None:
            return False, "열린 마일스톤이 없습니다 — 마일스톤 회의가 먼저입니다."
        units = parse_units(lines)
        if not units:
            return False, "수렴안에 '단위: <목표> | <실증>' 줄이 필요합니다."
        # [거부 사유 은닉 봉합(2026-07-20, U-035 실측: 가결→등록 0건→'단위: 줄을 확인' 오진 → 봇이
        # 멀쩡한 단위 줄만 재확인·재가결하는 무한 사이클×2회의)] open_subtask(gate_criteria)의 단위별
        # 거부 사유를 버리지 않고 그대로 돌려준다 — 고칠 수 있는 진단만이 사이클을 끝낸다.
        n = 0
        _errs = []
        for u in units:
            st = open_subtask(flow, _open, u.partition("|")[0].strip(), parse_criteria_lines(u))
            if isinstance(st, str):
                _errs.append(f"단위 '{u.partition('|')[0].strip()[:36]}' — {st.splitlines()[0][:160]}")
            else:
                n += 1
        if flow.log:
            flow.log("subtasks_by_meeting", ms=_open.ms_id, n=n, rejected=len(_errs))
        _etxt = ("\n".join(_errs))[:900]
        return (n > 0), ((f"[표결 확정] 서브태스크 {n}개 등록."
                          + (f" (미등록 {len(_errs)}건 — 사유: {_etxt})" if _errs else "")
                          + " 다음: 각 단위의 백로그 회의를 시스템이 엽니다.")
                         if n else f"등록 거부 — 단위 {len(units)}건 전부 조건 게이트 불통과:\n{_etxt}")

    if stage == "backlog":
        _open = next((m for m in (getattr(flow, "milestones", None) or [])
                      if m.status not in ("done", "superseded")), None)
        if _open is None:
            return False, "열린 마일스톤이 없습니다."
        from .backlog import relay_for
        store = getattr(flow, "backlog_relays", None) or {}
        _alive_sts = [st for st in _open.subtasks if st.status not in ("done", "superseded")]
        _empty_sts = [st for st in _alive_sts
                      if store.get(st.st_id) is None or not store.get(st.st_id).backlogs]
        if not _alive_sts:
            return False, "백로그를 채울 서브태스크가 없습니다."
        items = [l.split(":", 1)[1].strip() for l in lines if l.strip().startswith("백로그:")]
        if not items:
            return False, "수렴안에 '백로그: <작업 단위>' 줄이 필요합니다."

        # [iter 일괄 충전(2026-07-20, 사용자: '다음 회의로 백로그 여러개 다수 한번에')] 한 회의가 한
        # 영역이 아니라 미충원 영역들 몫을 함께 등록한다 — '백로그: [영역명] 항목'의 [영역명]으로 배분
        # (토큰 겹침 최고 영역), 접두 없으면 첫 미충원 영역(하위호환).
        import re as _re3

        def _dest_of(_it):
            m3 = _re3.match(r"^\[([^\]]{1,40})\]\s*(.*)$", _it)
            if m3:
                _ht = set(_re3.findall(r"[A-Za-z가-힣0-9]{2,}", m3.group(1)))
                best, hit = 0.0, None
                for _st3 in _alive_sts:
                    _gt = set(_re3.findall(r"[A-Za-z가-힣0-9]{2,}", str(getattr(_st3, "goal", "") or "")))
                    _ov = len(_ht & _gt) / max(len(_ht), 1)
                    if _ov > best:
                        best, hit = _ov, _st3
                if hit is not None and best >= 0.34 and (m3.group(2) or "").strip():
                    return hit, m3.group(2).strip()
            return (_empty_sts[0] if _empty_sts else _alive_sts[0]), _it
        # [발제자=주인(2026-07-16, 사용자: '백로그 발제한 애가 주인, 누가 발제했는지 남아야')] 회의
        # DRAFT에 그 줄을 쓴 봇을 SYS가 턴별 diff로 귀속 추적(flow._draft_attr) — 등록 시 그 봇이
        # 제출자가 되어 수행자=제출자 원칙이 회의 경로에도 이어진다. 귀속 없는 줄만 무주(자기선택).
        _attr = getattr(flow, "_draft_attr", None) or {}

        def _attr_of(_key):
            """정확 일치 → 토큰 과반 겹침 회복. [키 드리프트(2026-07-20, U-035 실측: 이의 개서로 줄이
            바뀌어 전원 무주 → 주인 표기·선점 킥 둘 다 유실)] 개서돼도 발제자가 남게."""
            import re as _re2
            _v = int(_attr.get(_key, 0) or 0)
            if _v:
                return _v
            _qt = set(_re2.findall(r"[A-Za-z가-힣0-9]{2,}", str(_key)))
            best, bid = 0.0, 0
            for _k2, _v2 in _attr.items():
                _t2 = set(_re2.findall(r"[A-Za-z가-힣0-9]{2,}", str(_k2)))
                if not _qt or not _t2:
                    continue
                _ov = len(_qt & _t2) / max(len(_qt), 1)
                if _ov > best:
                    best, bid = _ov, int(_v2 or 0)
            return bid if best >= 0.5 else 0

        # [무주 출생 금지(2026-07-20, 사용자: '선점 대기는 불가능하지 — 애초에 주인이 있어야')] 발제
        # 귀속이 끝내 실패해도 무주로 태어나지 않는다 — 적임(role_fit)을 주인으로 지정(발제자=주인
        # 원칙의 폴백. '선점 대기'는 담당 이탈 같은 예외에만 존재).
        _bots_o = {int(k): str(v or "") for k, v in (getattr(flow, "bot_info", None) or {}).items()}

        def _owner_fb(_st_o, _body_o):
            if not _bots_o:
                return 0
            from ..role_fit import role_fit as _rf2
            _q2 = f"{getattr(_st_o, 'goal', '')} {_body_o}"
            return int(max(_bots_o, key=lambda k: _rf2(_q2, _bots_o[k])))

        n = 0
        _per = {}
        for _ln in lines:
            _s = _ln.strip()
            if not _s.startswith("백로그:"):
                continue
            it = _s.split(":", 1)[1].strip()
            try:
                _st_d, _body = _dest_of(it)
                _who = _attr_of(draft_norm_line(_s) or _s) or _owner_fb(_st_d, _body)
                relay_for(flow, _st_d).submit(_who, _body, force=True)
                n += 1
                _per[_st_d.st_id] = _per.get(_st_d.st_id, 0) + 1
            except Exception:
                pass
        for _st4 in _alive_sts:
            _r4 = store.get(_st4.st_id) or relay_for(flow, _st4)
            if _st4.st_id in _per:
                _st4.backlog_ids = [b.backlog_id for b in _r4.backlogs]
        if flow.log:
            flow.log("backlogs_by_meeting", n=n,
                     sts=" ".join(f"{k}:{v}" for k, v in _per.items())[:120])
        _dist = " · ".join(f"{k.rsplit('/', 1)[-1]} {v}개" for k, v in _per.items())
        return (n > 0), (f"[표결 확정] 백로그 {n}개 등록({_dist}). 각자 pick_backlog로 전담하세요."
                         if n else "등록된 백로그가 없습니다.")

    return False, "알 수 없는 회의 단계입니다."


def rule_set_milestone(flow, me_id, args) -> str:
    """[솔로 판 전용 — 서기] 확정의 실체는 회의 종결 표결(가결 시 자동 등록)이고 이 도구는 팀 없는
    판(혼자)의 기록 행위다 — 품질은 등록 게이트가 방어. 게이트 거부는 사유+처방을 그대로 반환."""
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    # [확정 권위 게이트(2026-07-14, 사용자: '개인이 마일스톤 만들고 대체되고 난리 — 닫아야지')] 결정권자
    # 폐지(07-09)로 확정 권위는 회의 종결 표결로 이관됐는데, 이 도구는 '표결이 실제 있었나'를 검증하지
    # 않는 우회로였다 — U-019 라이브: 표결 0건인 채 백엔드·프론트·기획이 각자 직접 등록(대체 파기 포함).
    # 동료가 있는 판의 주기 확정은 표결 자동 등록(ms_confirm_by_vote)만 — 개인 등록은 솔로 판 한정.
    _team = list(getattr(getattr(flow, "current", None), "team", None) or [])
    if any(int(m) != int(me_id) for m in _team):
        return ("등록 거부: 팀 판의 마일스톤 확정은 개인 등록이 아니라 **회의 종결 표결**입니다 — meet를 "
                "열어 완수조건을 협의하고, 종결 표결 때 각자 [수렴안](목표: 한 줄 + '조건 | 실증절차' 줄들)을 "
                "동봉하세요. **가결되면 그 안이 자동 등록됩니다**(따로 등록하는 사람 없음 — 결정권자 폐지).")
    err = gate_new_cycle(flow)
    if err:
        return err
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
    """진행 중 마일스톤에 SubTask 추가 — 주기 중에도 허용(계약 §2). 등록 게이트는 동일.
    [흐름 귀속(2026-07-14)] 팀 판의 단위 분해는 개인이 아니라 회의 수렴안('단위:' 줄 동봉 → 가결 시
    자동 등록) — 한 봇의 지능이 전 도메인 몫을 카빙하던 것(U-019 백엔드 ST-1~6) 차단. 개인 도구는
    솔로 판 한정. 전담의 실체는 SubTask가 아니라 **백로그**(각자 pick_backlog(desc)로 자기 등재)."""
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    _team = list(getattr(getattr(flow, "current", None), "team", None) or [])
    if any(int(m) != int(me_id) for m in _team):
        return ("등록 거부: 팀 판의 단위(SubTask) 분해는 개인 등록이 아니라 **회의 수렴안**입니다 — meet "
                "종결 표결의 [수렴안]에 '단위: <목표> | <실증절차>' 줄로 동봉하세요. 가결되면 마일스톤과 "
                "함께 등록됩니다. 주기 중 추가 단위가 필요해도 meet 재협의가 경로입니다. 자기 몫 작업은 "
                "단위 안에서 pick_backlog(desc='내가 할 일')로 등재해 집으세요 — 전담은 백로그 단위입니다.")
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
            f"자기 몫은 pick_backlog(desc)로 등재해 집으세요 — 전담은 백로그 단위입니다.")


def rule_renegotiate(flow, me_id, args) -> str:
    """[누구나 — 조건 재협상 #1] 진행 중 주기의 달성 불가 조건을 사람 승인 대기로 올린다.
    target=조건 desc(부분일치), reason=왜 불가능한가. 정체를 겪는 현장 누구나 올린다 —
    진짜 게이트는 사람 승인(approve_waiver)이므로 올리는 권한을 독점할 이유가 없다(결정권자 폐지)."""
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    ms = next_milestone(flow)
    if ms is None:
        return "재협상할 주기가 없습니다."
    target = str(args.get("target") or "").strip()
    if not target:
        return "target(재협상할 조건)이 비었습니다."
    return renegotiate_criterion(flow, ms, target, str(args.get("reason") or ""))


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
    # [자기 백로그 기본 귀속(2026-07-21, U-036 실측: 디자이너가 target 없이 report_iter → 보고가
    # 마일스톤 조건(V0/V1 게임플레이 게이트)에 0/5 미착지, 자기 백로그는 영원히 in_progress →
    # 릴레이 정지·[다음 선정] 불발 → 이후 54분이 메타 표결 공회전)] target 미지정이고 보고자가
    # 지금 in_progress 백로그를 쥔 SubTask가 있으면 그 SubTask가 기본 대상이다 — 자기선점(릴레이)
    # 작업의 마무리 보고가 위임 경로(sync_completion)와 대칭으로 장부에 착지한다. 백로그를 안 쥔
    # 보고(주기 마감 국면의 마일스톤 검증)는 종전대로 마일스톤을 탄다(무회귀).
    if tgt is None:
        try:
            _rls = getattr(flow, "backlog_relays", None) or {}
            for _st0 in ms.subtasks:
                _r0 = _rls.get(_st0.st_id)
                if (_st0.status != "done" and _r0 is not None
                        and any(b.status == "in_progress" and b.assignee == int(me_id)
                                for b in _r0.backlogs)):
                    tgt = _st0
                    if flow.log:
                        flow.log("iter_target_inferred", st=_st0.st_id, by=int(me_id))
                    break
        except Exception:
            pass
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
    # [백로그 의무화 2단(2026-07-10, 사용자)] 솔로 진행(위임 無)도 장부가 진실이도록 — 검증 제출된
    # 조건 단위를 SubTask 릴레이에 소급 등재(pass=done, fail=in_progress 보유). 위임형은 1단(자동
    # 제출)이 이미 등재하므로 매칭돼 중복 없음.
    if tgt is not None:
        try:
            from .backlog import relay_for, _match_backlog, pipeline_on as _po
            r = relay_for(flow, tgt)
            _finished_mine = False
            for it in results:
                d = str(it.get("desc") or "")[:120]
                if not d:
                    continue
                b = _match_backlog(r, d)
                if b is None:
                    b = r.submit(int(me_id), d, force=True)
                try:
                    if b.status == "open":
                        r.pick(int(me_id), b.backlog_id, int(me_id))
                    if it.get("passed") and b.status != "done":
                        _was_mine = (b.status == "in_progress" and b.assignee == int(me_id))
                        r.done(int(me_id), b.backlog_id)
                        _finished_mine = _finished_mine or _was_mine
                except Exception:
                    pass
            tgt.backlog_ids = [x.backlog_id for x in r.backlogs]
            if _finished_mine:
                # [릴레이 이음(2026-07-21, U-036 실측)] 자기선점 백로그의 완료 보고 = 위임 완료와 같은
                # 핸드오프 순간 — [다음 선정]/[백로그 소진] 공고를 여기서도 띄운다. 종전엔 위임 경로
                # (sync_completion) 전용이라 자기선점 완료는 공고 없이 릴레이가 조용히 정지했다.
                from .backlog import handoff_note as _ho
                _ho(flow, r, int(me_id), "완료됐습니다")
                tgt.participants.add(int(me_id))
        except Exception:
            pass
    if flow.log:
        # [감사 P3 — 자기검증 가시화] 누가 결과를 제출했나(교차 검증 부재의 관측 지표)
        flow.log("iter_report_by", by=int(me_id), n=len(results),
                 id=getattr(obj, "ms_id", None) or getattr(obj, "st_id", ""))
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

def _crit_dict(c):
    return {"desc": c.desc, "verify": c.verify, "passed": c.passed, "evidence": c.evidence,
            "status": c.status, "block_reason": c.block_reason}   # [#1] 재협상 상태 동승


def ms_to_dict(ms: Milestone) -> dict:
    return {"ms_id": ms.ms_id, "goal": ms.goal, "status": ms.status, "iter_n": ms.iter_n,
            "iter_stuck": ms.iter_stuck, "origin": ms.origin,
            "carried": [dict(x) for x in (ms.carried or [])],   # [이월 원장 동승] 재시작 너머 보존
            "criteria": [_crit_dict(c) for c in ms.criteria],
            "subtasks": [{"st_id": s.st_id, "goal": s.goal, "status": s.status,
                          "iter_n": s.iter_n, "iter_stuck": s.iter_stuck,
                          "participants": sorted(int(p) for p in s.participants),
                          "backlog_ids": list(s.backlog_ids),
                          "criteria": [_crit_dict(c) for c in s.criteria]}
                         for s in ms.subtasks]}


def ms_from_dict(d: dict) -> Milestone:
    def _crit(rows):
        return [Criterion(desc=str(r.get("desc") or ""), verify=str(r.get("verify") or ""),
                          passed=bool(r.get("passed")), evidence=str(r.get("evidence") or ""),
                          status=str(r.get("status") or "active"), block_reason=str(r.get("block_reason") or ""))
                for r in (rows or [])]
    ms = Milestone(ms_id=str(d.get("ms_id") or ""), goal=str(d.get("goal") or ""),
                   criteria=_crit(d.get("criteria")), status=str(d.get("status") or "open"),
                   iter_n=int(d.get("iter_n") or 0), iter_stuck=int(d.get("iter_stuck") or 0),
                   origin=str(d.get("origin") or ""),
                   carried=[dict(x) for x in (d.get("carried") or []) if isinstance(x, dict)])
    for s in (d.get("subtasks") or []):
        ms.subtasks.append(SubTask(
            st_id=str(s.get("st_id") or ""), goal=str(s.get("goal") or ""),
            criteria=_crit(s.get("criteria")), participants=set(int(p) for p in (s.get("participants") or [])),
            backlog_ids=list(s.get("backlog_ids") or []), status=str(s.get("status") or "open"),
            iter_n=int(s.get("iter_n") or 0), iter_stuck=int(s.get("iter_stuck") or 0)))
    return ms


async def flush_pipeline_notes(flow):
    """누적된 파이프라인 생애주기 노트를 채널에 SYS 게시(sender 0) — 프론트가 흐름 마커로 렌더."""
    notes = getattr(flow, "_pipeline_notes", None)
    if not notes:
        return
    flow._pipeline_notes = []
    ch = getattr(flow, "current", None)
    tid = getattr(ch, "thread_id", None) or getattr(flow, "user_channel", None)
    for t in notes:
        try:
            await flow.guide.post(int(tid), 0, t)
        except Exception:
            pass
