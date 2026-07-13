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
        # [등록 게이트 강화 — 설계 검토 #4(2026-07-09)] verify가 '확인함' 같은 빈 서술이면 churn을
        # 등록 단계로 옮길 뿐. 실행 가능 신호(명령 토큰) 또는 측정 신호(수치·비교)를 최소 1개 요구한다.
        if not _verify_is_executable(v):
            return (f"조건 '{d[:40]}'의 verify가 실행 가능한 형태가 아닙니다: '{v[:40]}' — "
                    f"run으로 돌릴 명령(curl/pytest/npm/python/grep/localhost/포트/파일경로 등)이나 "
                    f"측정 기준(수치·= > < %·회·초·개)을 넣으세요. '확인한다'류 서술은 불가.")
        key = d.lower()
        if key in seen:
            return f"조건 '{d[:40]}'이 중복입니다 — 합치거나 구체화하세요."
        seen.add(key)
    return None


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
    return {**cur, "list": out_list, "ts": time.time()}


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
            bl.append({"id": b.backlog_id, "d": (b.body or "")[:60], "s": b.status, "a": a,
                       "aid": (str(b.assignee) if b.assignee else None),
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


def persist_ms_status(flow):
    """마일스톤 현황을 상태파일(ms_status.json, 채널 키)로 미러 — 웹이 읽기 전용으로 서빙(HUD).
    브리지 payload 확장 없이 같은 호스트 파일이 통로. ORGANT_PJT 미설정(테스트)이면 무동작,
    실패는 흐름에 무해."""
    try:
        pjt = os.environ.get("ORGANT_PJT")
        ch = getattr(flow, "user_channel", None)
        if not pjt or ch is None:
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
        snap = ms_status_snapshot(flow)
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

    _unclaimed = [c for c in obj.criteria if not c.passed and c.status != "waived"]
    _unmatched = []
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
            best = max(_unclaimed, key=lambda x: len(rt & (_toks(x.desc) | _toks(x.verify))))
            _ov = rt & (_toks(best.desc) | _toks(best.verify))
            if len(_ov) >= 4:      # 우연 겹침 차단 임계 — 명령/경로 토큰 4개 이상 공유 시 동일 조건으로 본다
                c = best
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
        obj.iter_stuck = 0
        obj.status = "wrapup"
        if flow.log:
            flow.log("ms_iter_pass", kind=kind, id=oid, iter=obj.iter_n)
        _ckpt(flow)
        return True, "완수조건 전부 충족 — 잔여 정리(wrapup) 후 다음 주기로."
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
    if _unmatched:
        # 매칭 실패를 봇에게 되돌린다 — 조용한 폐기가 공회전의 뿌리였다. 조건 desc 원문을 그대로 준다.
        note += ("\n[결과 " + str(len(_unmatched)) + "건 미착지 — desc 불일치] report_iter의 results[].desc는 "
                 "아래 조건 desc를 **토씨 그대로** 복사해 쓰세요: "
                 + " / ".join("'" + c.desc[:50] + "'" for c in obj.criteria if not c.passed and c.status != "waived"))
    if obj.iter_stuck >= _STUCK_LIMIT:
        if flow.log:
            flow.log("ms_iter_stuck", kind=kind, id=oid, iter=obj.iter_n, stuck=obj.iter_stuck)
        note += (f"\n[정체 — {obj.iter_stuck}회 연속 진전 없음] 반복이 결과를 못 바꾸고 있습니다. "
                 f"조건이 환경상 달성 불가라면 결정권자가 renegotiate_criterion(대상 조건, 사유)로 "
                 f"재협상하세요 — 사람 승인으로 조건을 포기(waive)하거나 바꿉니다. 무한 반복하지 마세요.")
    return False, note


_STUCK_LIMIT = int(os.environ.get("ORGANT_ITER_STUCK_LIMIT", "3"))


def renegotiate_criterion(flow, obj, target: str, reason: str) -> str:
    """[조건 재협상 #1 — 결정권자] 달성 불가 조건을 blocked_pending으로 표시하고 사람에게 에스컬레이트한다.
    사람 승인(approve_waiver)이 오기 전엔 waive되지 않는다 — '포기'는 봇이 혼자 못 하고 사람이 승인한다
    (조건=마감권이므로 포기도 방향 결정 = 사람 몫). 승인 전까지 그 조건은 여전히 미충족으로 남되,
    iter_stuck 경보가 반복 안 되게 재협상 진행 중 표식을 둔다."""
    c = next((x for x in obj.criteria if x.desc == target or target in x.desc), None)
    if c is None:
        return f"재협상 대상 조건을 못 찾음: {target[:40]} — 현재 조건: {' · '.join(x.desc[:24] for x in obj.criteria)}"
    if c.status == "waived":
        return f"이미 포기(waived)된 조건입니다: {c.desc[:40]}"
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
    _ckpt(flow)
    return (f"조건 '{c.desc[:40]}' {'포기 승인됨(waived) — 나머지 조건으로 주기 진행' if approve else '반려됨 — 다시 충족해야 함'}.")


def wrapup_done(flow, obj) -> str:
    """잔여 정리 완료 선언 → done. wrapup 상태에서만 유효(조건 미충족 상태의 건너뛰기 차단)."""
    if obj.status != "wrapup":
        return "정리 완료 선언 불가: 아직 완수조건 검증(iter_verify)을 통과하지 않았습니다."
    obj.status = "done"
    _ckpt(flow)
    if flow.log:
        _oid = getattr(obj, "ms_id", None) or getattr(obj, "st_id", "")
        _pnote(flow, f"[{'마일스톤 완수' if isinstance(obj, Milestone) else 'SubTask 완수'}] ({_oid}) {getattr(obj, 'goal', '')[:120]}")
        flow.log("ms_done" if isinstance(obj, Milestone) else "subtask_done",
                 id=getattr(obj, "ms_id", None) or getattr(obj, "st_id", ""))
    return "done"


def next_milestone(flow) -> Optional[Milestone]:
    """다음 진행 대상 — 미완(done 아님) 첫 마일스톤. 진행을 사람이 아니라 주기가 관할한다."""
    for ms in flow.milestones:
        if ms.status not in ("done", "superseded"):
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
                "verify": f"run으로 재현 절차 재실행 → 재현 0회 확인: {d[:120]}"} for d in ds]
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


def rule_set_milestone(flow, me_id, args) -> str:
    """[누구나 — 서기] 회의 수렴을 등록해 마일스톤 개설. 확정의 실체는 회의 종결 표결(가결)이고
    등록은 기록 행위다 — 품질은 등록 게이트가 방어. 게이트 거부는 사유+처방을 그대로 반환."""
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    goal = str(args.get("goal") or "").strip()
    if not goal:
        return "등록 거부: goal(이 주기의 목표 한 줄)이 비었습니다."
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
                        r.done(int(me_id), b.backlog_id)
                except Exception:
                    pass
            tgt.backlog_ids = [x.backlog_id for x in r.backlogs]
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
                   origin=str(d.get("origin") or ""))
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
