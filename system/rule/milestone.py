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
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .evidence import (
    direct_verifier_command, looks_like_verification_command,
    normalize_verifier_command, verifier_command_hash, verifier_spec_hash,
)

__all__ = [
    "pipeline_on", "Criterion", "SubTask", "Milestone",
    "gate_criteria", "open_milestone", "open_subtask",
    "iter_verify", "wrapup_done", "next_milestone", "ms_replan",
    "ms_to_dict", "ms_from_dict",
    "parse_criteria_lines", "rule_set_milestone", "rule_set_subtask",
    "parse_iter_results", "rule_report_iter",
    "renegotiate_criterion", "approve_waiver", "rule_renegotiate",
    "extract_consensus", "flush_state_db", "canonical_parent_contract",
    "promote_final_locked_criteria", "goal_locked_release_error",
    "ratified_goal_verifier_command",
    "workspace_artifact_stamp", "write_revision", "invalidate_e2e_state",
    "work_ledger_release_error", "ensure_goal_ratification_scaffold",
    "clear_resolved_goal_ratification_objection",
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
    verify_attempts: int = 0  # SYS 구조검증 횟수 — 재시작 뒤에도 실패 상한을 보존
    # 최종 로드맵 주기에서 GOAL locked_criteria가 active 분모로 승격된 항목. 중간 주기에는 False.
    # True인 조건은 하위 회의가 포기·이월할 수 없고 실제 증거 있는 pass만 Task release를 연다.
    release_lock: bool = False
    # release_lock 증거의 발급 주체·단일 사용 영수증·검증 당시 산출물 버전. 일반 report_iter 문자열은
    # 이 필드를 만들 수 없으며, SYS 실행기가 발급한 rc=0 영수증만 채운다.
    evidence_source: str = ""
    receipt_id: str = ""
    verified_write_epoch: int = -1
    verified_artifact_stamp: str = ""
    # 실제 subprocess 원문과 그 hash, 그리고 (desc, verify) 계약 hash. target 문자열만 맞춘 무관한
    # 성공 명령의 영수증을 복원 뒤에도 진짜 실증으로 오인하지 않게 최대 저장한다.
    verified_command: str = ""
    verified_command_hash: str = ""
    verified_spec_hash: str = ""
    # canonical GOAL verify가 자연어여도 최종 마일스톤 회의가 GOAL@ 정본 marker에 별도 exact
    # command를 비준할 수 있다. canonical spec을 덮지 않고 이 immutable 결속을 별도 보존한다.
    ratified_verifier_command: str = ""
    ratified_verifier_command_hash: str = ""
    ratified_verifier_spec_hash: str = ""


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
    # GOAL 회의가 확정한 상위 완수계약의 비가변 참조. 이 목록은 이번 주기의 검증 분모가 아니다 —
    # 여러 주기로 나뉜 Task에서 첫 주기가 전체 GOAL 조건을 모두 만족해야 하는 불가능을 만들지 않으면서,
    # 하위 회의가 상위 계약을 축소·대체해도 정본 계보와 Task 최종 게이트가 사라지지 않게 보존한다.
    # 끝에 추가해 기존 positional 생성자의 네 번째 인자(subtasks) 의미도 보존한다.
    locked_criteria: List[Criterion] = field(default_factory=list)


# ── 완수조건 등록 게이트 (계약 §2) ──────────────────────────────────────────────

# 소망형(검증 불가) 조건의 전형 — 등록에서 거부해 '조건 권력'의 부실화를 막는다(설계 검토 갭 1).
_WISHFUL = ("잘 동작", "잘 작동", "완벽", "훌륭", "만족스럽", "좋아야", "문제없", "이상 없")


_SERVE_ONLY = re.compile(
    r"(http\.server|python3?\s+-m\s+http|\bserve\b|npm\s+(run\s+)?(start|dev)|"
    r"yarn\s+(start|dev)|pnpm\s+(start|dev)|vite(\s|$)|next\s+dev|node\s+server\.js|"
    r"flask\s+run|uvicorn|gunicorn|php\s+-S)", re.I)
_JUDGES = re.compile(
    r"(curl|wget|pytest|npm\s+test|jest|vitest|playwright|assert|grep|diff|exit\s*[01]|"
    r"\.py\b|\.mjs\b|\.js\b\s|test|verify|check|&&|\|\|)", re.I)


def verify_is_serve_only(v) -> bool:
    """[띄우기만 하는 명령은 실증이 아니다(2026-07-30, U-436 실측)] 서버를 기동하는 명령은
    '실행 가능'하지만 **통과/실패를 가르지 않는다** — 관문 문구는 그렇게 쓰라고 말하면서 검사는
    실행 가능성만 봐서 통과시켰다. 그 조건이 GOAL에 박히자 뒤 회의가 "기동만으로는 pass가 아니다"
    ↔ "그럼 무슨 명령이냐"로 3패스를 돌다 판이 파킹됐다(교착의 뿌리).

    기동 명령이면서 판정 신호(요청·테스트·비교·스크립트·exit 코드)가 하나도 없으면 실증이 아니다.
    기동 + 점검(`… & sleep 1; curl -f localhost:PORT/`)은 정상이다.
    """
    t = str(v or "")
    return bool(_SERVE_ONLY.search(t)) and not _JUDGES.search(t)


def gate_criteria(entries) -> Optional[str]:
    """완수조건 등록 게이트 — 에러 문자열(거부 사유+처방) 또는 None(통과).
    형태 요건: desc(무엇이 충족인가) + verify(run으로 실증 가능한 절차) 둘 다. 소망형 desc 거부."""
    items = list(entries or [])
    if not items:
        return ("완수조건이 비어 있습니다 — 결정 구획에 '- ⟦조건⟧ | 실증: ⟦run으로 확인하는 절차⟧' "
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
                        f"측정 기준(수치·= > < %·회·초·개)을 넣으세요. '확인한다'류 서술은 불가. "
                        # [화면 조건의 출구(2026-07-26 U-063·U-064 실측)] 눈으로 보는 조건에서 봇들이
                        # 명령을 못 떠올려 '띄우기만 하는' 명령이나 계획서 grep으로 우회했다. 이 판엔
                        # headless 브라우저가 이미 있으니 판정 스크립트를 만들면 된다고 알려준다.
                        f"**화면으로 보는 조건**(진입 속도·스크롤·요소 가시성·뷰포트 배치)은 headless "
                        f"브라우저로 판정하세요 — playwright와 브라우저가 이미 설치돼 있습니다"
                        f"(설치·네트워크 불필요). 페이지를 열어 확인하고 통과=exit 0, 실패=non-zero인 "
                        f"스크립트를 만들어 그 명령을 쓰세요(예: `python3 verify_ui.py`). 스크립트가 아직 "
                        f"없어도 됩니다 — 만드는 일을 이번 주기 백로그에 넣으세요. 서버를 띄우기만 하는 "
                        f"명령이나 계획 문서를 grep하는 명령은 산출물을 판정하지 않아 실증이 아닙니다.")
            continue
        if verify_is_serve_only(v):
            errs.append(f"조건 '{d[:40]}'의 실증이 **서버를 띄우기만 하는 명령**입니다: '{v[:50]}' — "
                        f"기동은 통과/실패를 가르지 않습니다. 그 뒤에 판정을 붙이세요"
                        f"(예: `python3 -m http.server 4173 --directory public & sleep 1; "
                        f"curl -fsS localhost:4173/ >/dev/null`), 또는 화면 조건이면 headless "
                        f"브라우저로 판정하는 스크립트(통과=exit 0)를 만들어 그 명령을 쓰세요 — "
                        f"스크립트가 아직 없어도 됩니다(만드는 일을 이번 주기 백로그에).")
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
        _sts = [s for s in ms.subtasks if s.status not in ("done", "superseded")] if ms else []
        st = _sts[0] if _sts else None
        bl = None
        # [백로그 단위 태깅(2026-07-10, 사용자: '텍스트가 백로그 단위로')] 이 봇이 지금 물고 있는
        # (in_progress·assignee=me) 백로그 id — 발언이 백로그 항목 밑으로 묶이는 근거.
        # B1은 SubTask마다 다시 시작한다. 첫 미완 ST를 먼저 고른 뒤 그 안에서만 찾으면 ST-1이
        # open인 동안 ST-2/B1 작업 발화가 ST-1/null로 태깅된다. 전 열린 ST의 실제 단일 활성
        # 백로그를 먼저 찾고, 같은 봇이 쥔 항목을 최우선으로 선택한다.
        _active = []
        for _st in _sts:
            _r = (getattr(flow, "backlog_relays", None) or {}).get(_st.st_id)
            for _b in (getattr(_r, "backlogs", None) or []):
                if _b.status == "in_progress":
                    _active.append((_st, _b))
        _owned = (next((x for x in _active
                        if me_id is not None and int(x[1].assignee or 0) == int(me_id)), None)
                  if me_id is not None else None)
        _hit = _owned or (max(_active, key=lambda x: float(getattr(x[1], "ts_pick", 0) or 0))
                          if _active else None)
        if _hit is not None:
            st, _b = _hit
            bl = _b.backlog_id
        # [전역 회의는 주기 소속(2026-07-21, 사용자: '전 서브태스크를 한번에 만드는 회의라면 공통
        # 흐름 하위에 둬야지')] 단계 회의(목표·주기·단위·백로그)는 특정 SubTask의 일이 아니라 주기
        # 전체의 결정인데, 종전엔 '첫 미완 SubTask'를 무조건 태깅해 화면이 전역 회의를 그 단계 폴더
        # 밑으로 접었다(U-037 실측: 백로그 회의가 ST-1 아래). 단계 회의 동안은 ms까지만 태깅한다.
        if getattr(flow, "_stage_meeting", None):
            st, bl = None, None
        # [백로그 없는 발화는 단계에 붙이지 않는다(2026-07-29, 사용자: '잘못된 데이터는 막아야지')]
        # 소속 태깅의 근거는 '지금 물고 있는 백로그'다. 그 백로그가 없을 때(막힘·파킹·인계 사이)
        # st만 남겨 보내면, 화면은 그 발화를 단계 폴더 안 백로그 행들 옆에 그린다 — 어떤 일감의
        # 기록도 아닌 대화가 백로그 아래 채팅으로 남는다(U-079 4세대 실측: ST-2 아래 발화 5건,
        # 전부 bl=None). 귀속이 없는 것은 없는 대로 보내고(공통 흐름), 단계 태그는 백로그가 있을
        # 때만 붙인다. 이 판정은 SYS 한 곳에서만 하므로 표면은 고칠 것이 없다.
        if bl is None:
            st = None
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
                       "t0": b.ts_pick or None, "t1": b.ts_done or None,
                       "act": list(getattr(b, "activity", None) or [])[-200:]})
        # [백로그=계획 목록(2026-07-10, 사용자: '미리 만들어 두는 건데')] ST 완수조건 = 등록 순간부터
        # 존재하는 계획 단위 — 전 목록을 표면에 준다(passed=✓). 릴레이 bl은 담당·진행의 보강 데이터.
        cr = [{"d": c.desc[:80], "p": bool(c.passed), "w": c.status == "waived",
               "v": (c.verify or "")[:160], "e": (c.evidence or "")[:240]} for c in st.criteria[:15]]
        sts.append({"g": st.goal[:80], "id": st.st_id, "s": st.status, "met": st_met, "total": st_total, "bl": bl, "cr": cr})
    # [완수조건 표면화(2026-07-13, 사용자: '뭐 완수했는지 보이게')] ms레벨 조건도 ✓체크리스트로
    ms_cr = [{"d": c.desc[:80], "p": bool(c.passed), "w": c.status == "waived",
              "v": (c.verify or "")[:160], "e": (c.evidence or "")[:240]} for c in ms.criteria[:15]]
    # 상위 GOAL 계약은 관측용 별도 필드 — met/total 분모에는 절대 합치지 않는다.
    locked_cr = [{"d": c.desc[:80], "v": (c.verify or "")[:160], "p": bool(c.passed),
                  "e": (c.evidence or "")[:240]}
                 for c in (getattr(ms, "locked_criteria", None) or [])[:15]]
    return {"goal": ms.goal[:140], "ms": ms.ms_id, "met": met, "total": total, "iter": ms.iter_n, "status": ms.status,
            "cr": ms_cr, "locked_cr": locked_cr, "sts": sts}


_MS_BG_TASKS = set()   # [GC 방어] fire-and-forget DB push 태스크 참조 보존
# 같은 채널/종류의 put_state를 각각 task로 띄우면 먼저 보낸 느린 응답이 나중 상태를 덮는다.
# writer 하나가 전송 중 변경을 최신 스냅샷으로 합쳐 순서대로 밀어내게 한다. loop를 key에 넣는
# 이유는 테스트의 asyncio.run 반복과 장기 러너 재기동에서 서로 다른 loop의 task를 섞지 않기 위해서다.
_MS_STATE_WRITERS = {}


async def _drain_state_db(key):
    """한 ``(loop, channel, kind)``의 최신 상태까지 순차 전송한다.

    전송 중 갱신은 ``rev``만 전진시키며, 현재 응답이 끝난 뒤 가장 최신 스냅샷 한 장을 보낸다.
    ms 스냅샷의 activity는 누적 목록이라 중간 장을 합쳐도 생각 기록은 최신 장에 보존된다.
    """
    while True:
        slot = _MS_STATE_WRITERS.get(key)
        if slot is None:
            return
        rev, put, ch, kind, data = (
            slot["rev"], slot["put"], slot["ch"], slot["kind"], slot["data"])
        try:
            await put(ch, kind, data)
        except Exception:
            # 상태 미러 순단은 본 흐름을 깨지 않는다. 다만 전송 중 더 최신 스냅샷이 들어왔다면
            # 실패한 옛 장에서 writer를 끝내지 않고 최신 장은 한 번 더 시도한다.
            pass
        slot = _MS_STATE_WRITERS.get(key)
        if slot is None or slot["rev"] == rev:
            return


def _state_writer_done(key, task):
    _MS_BG_TASKS.discard(task)
    # task 완료 콜백 전에 새 갱신이 같은 slot에 새 writer를 달 수 있다. 그 새 writer까지 옛
    # 콜백이 지우지 않도록 task identity가 같은 경우에만 정리한다.
    slot = _MS_STATE_WRITERS.get(key)
    if slot is not None and slot.get("task") is task:
        _MS_STATE_WRITERS.pop(key, None)


async def flush_state_db(flow=None, ch=None, kind=None):
    """현재 loop의 예약된 상태 미러를 최신 장까지 기다린다.

    러너의 종결/종료 경계나 테스트가 명시적으로 await할 수 있는 flush seam이다. 새 갱신이 기존
    writer 완료 직후 붙는 경합도 놓치지 않도록 해당 범위의 writer가 없어질 때까지 다시 확인한다.
    """
    import asyncio as _aio
    loop = _aio.get_running_loop()
    if ch is None and flow is not None:
        ch = getattr(flow, "user_channel", None)
    ch = int(ch) if ch is not None else None
    kind = str(kind) if kind is not None else None
    while True:
        tasks = []
        for (writer_loop, writer_ch, writer_kind), slot in list(_MS_STATE_WRITERS.items()):
            if writer_loop is not loop:
                continue
            if ch is not None and writer_ch != ch:
                continue
            if kind is not None and writer_kind != kind:
                continue
            task = slot.get("task")
            if task is not None and not task.done():
                tasks.append(task)
        if not tasks:
            return
        await _aio.gather(*tasks, return_exceptions=True)


def _push_state_db(flow, ch, kind, data):
    """[스케일아웃 상태 저장(2026-07-18, HA 설계)] guide.put_state로 채널 상태를 웹 DB에 미러 —
    sync 문맥(persist_ms_status)에서 부르므로, 실행 중 루프가 있으면 채널/종류별 단일 writer에
    최신 장을 예약한다(루프 없으면=테스트 무동작). ORGANT_STATE_DB=0이면 비활성. 파일 미러는
    별개로 유지(폴백). 반환 task는 종결 경계에서 ``flush_state_db``로 기다릴 수 있다."""
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
        ch = int(ch)
        kind = str(kind)
        key = (loop, ch, kind)
        slot = _MS_STATE_WRITERS.get(key)
        if slot is None:
            slot = {"rev": 0, "put": put, "ch": ch, "kind": kind, "data": data, "task": None}
            _MS_STATE_WRITERS[key] = slot
        slot["rev"] += 1
        slot["put"] = put
        slot["data"] = data
        task = slot.get("task")
        if task is None or task.done():
            task = loop.create_task(_drain_state_db(key))
            slot["task"] = task
            _MS_BG_TASKS.add(task)
            task.add_done_callback(lambda t, _key=key: _state_writer_done(_key, t))
        return task
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
                   locked_criteria=_mk_criteria(_goal_acceptance_entries(flow)),
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
    # 경계 판정 뒤 새 구현 주기가 생긴 순간 이전 checklist/results/verdict는 다른 산출물 버전의 값이다.
    # e2e_fail 복기뿐 아니라 수동 재수립도 동일하게 fresh e2e를 요구한다.
    invalidate_e2e_state(flow, f"새 마일스톤 {ms.ms_id}")
    # 로드맵의 마지막 주기(로드맵이 없으면 이 단일 주기)에서만 상위 GOAL 조건을 실제 검증 분모로
    # 승격한다. 중간 주기의 denominator는 그대로이고, 복원판은 같은 함수를 실행 관문에서 다시 부른다.
    promote_final_locked_criteria(flow, checkpoint=False)
    _ckpt(flow)
    if flow.log:
        flow.log("ms_open", ms=ms.ms_id, goal=ms.goal[:80], criteria=len(ms.criteria),
                 locked_criteria=len(ms.locked_criteria), replan=bool(origin.startswith("e2e:")))
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
    t = str(text)
    # [같은 마디를 두 번 알리지 않는다(2026-07-29, 사용자 지적)] 주기가 재검증으로 열렸다 닫히면
    # 완수·보고·시작 마커가 같은 문구로 다시 게시돼 화면에 같은 줄이 둘로 보였다(실측 7167/7173).
    # 상태 전이 자체는 정상이므로 막을 수 없다 — 대신 이 흐름에서 이미 알린 마디는 다시 알리지 않는다.
    if t.startswith(("[마일스톤 시작]", "[마일스톤 완수]", "[마일스톤 보고]", "[SubTask 완수]")):
        seen = getattr(flow, "_pnote_seen", None)
        if seen is None:
            seen = flow._pnote_seen = set()
        key = t[:120]
        if key in seen:
            return
        seen.add(key)
    notes.append(t)


def open_subtask(flow, ms: Milestone, goal: str, criteria_entries):
    """SubTask 개설 — 주기 중에도 추가 허용(계약 §2 확정).
    [서브태스크 게이트 제거(2026-07-22)] 조건은 선택 — 있으면 형태만 검사(있어도 완수 판정엔 안 씀,
    서브태스크 완수 = 백로그 소진), 없으면 순수 작업 영역으로 개설. 검증 게이트는 마일스톤만."""
    _ce = list(criteria_entries or [])
    if _ce and gate_criteria(_ce):
        # [불량 조건은 거부 말고 폐기(2026-07-22, GPT e2e 실측: 봇이 단위에 조건(| 실증)을 달면 형태 불량 시
        # 등록 거부 → 표결 통과해도 등록에서 막혀 재회의 무한 → 정체 종료·게임 미완). 서브태스크 조건은
        # 완수 판정에 안 쓰니(완수=백로그 소진), 불량이면 버리고 순수 작업 영역으로 개설한다(게이트 제거 완결).]
        _ce = []
    if not str(goal or "").strip():
        return "등록 거부: 작업 영역 이름(goal)이 비었습니다."
    st = SubTask(st_id=f"{ms.ms_id}/ST-{len(ms.subtasks) + 1}",
                 goal=str(goal or "").strip(), criteria=_mk_criteria(_ce))
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


def _release_boundary_ready(flow, obj) -> bool:
    """GOAL release 증거는 최종 산출물 상태에서만 발급 — 모든 하위 장부와 단위가 먼저 종결돼야 한다."""
    if not isinstance(obj, Milestone) or not obj.subtasks or _backlogs_pending(flow, obj):
        return False
    rels = getattr(flow, "backlog_relays", None) or {}
    return all(
        st.status in ("done", "superseded")
        and rels.get(st.st_id) is not None
        and bool(getattr(rels[st.st_id], "backlogs", None))
        and all(b.status in ("done", "dropped") for b in rels[st.st_id].backlogs)
        for st in obj.subtasks
    )


def work_ledger_release_error(flow, repair=False):
    """Task release 전에 모든 유효 마일스톤의 실제 작업 장부가 존재·종결했는지 확인한다.

    정상 실행기는 빈 SubTask/relay/backlog에서 검증을 시작하지 않지만, 구·손상 체크포인트가 이미
    ``done``이면 그 실행기 자체를 건너뛸 수 있다. e2e/complete 경계도 같은 불변식을 독립 확인하고,
    ``repair=True``이면 잘못 닫힌 주기/단위를 다시 열어 회의→백로그 실행 경로로 복귀시킨다.
    """
    relays = getattr(flow, "backlog_relays", None) or {}
    problems = []
    reopen_ms, reopen_st = [], []
    for ms in (getattr(flow, "milestones", None) or []):
        if ms.status == "superseded":
            continue
        subtasks = [st for st in (getattr(ms, "subtasks", None) or [])
                    if st.status != "superseded"]
        if not subtasks:
            problems.append(f"{ms.ms_id}: SubTask 0개")
            reopen_ms.append(ms)
            continue
        for st in subtasks:
            relay = relays.get(st.st_id)
            backlogs = list(getattr(relay, "backlogs", None) or []) if relay is not None else []
            if not backlogs:
                problems.append(f"{st.st_id}: 백로그 0개")
                reopen_ms.append(ms)
                reopen_st.append(st)
                continue
            pending = [b.backlog_id for b in backlogs
                       if b.status not in ("done", "dropped")]
            if pending:
                problems.append(f"{st.st_id}: 미종결 {', '.join(pending[:3])}")
                reopen_ms.append(ms)
                reopen_st.append(st)
            elif st.status not in ("done", "superseded"):
                problems.append(f"{st.st_id}: 단위 미완")
                reopen_ms.append(ms)
                reopen_st.append(st)
    if not problems:
        return None
    if repair:
        changed = False
        for ms in {id(x): x for x in reopen_ms}.values():
            if ms.status in ("done", "wrapup"):
                ms.status, ms.iter_stuck = "open", 0
                changed = True
        for st in {id(x): x for x in reopen_st}.values():
            if st.status in ("done", "wrapup"):
                st.status, st.iter_stuck = "open", 0
                changed = True
        if changed:
            invalidate_e2e_state(flow, "작업 장부 누락/미종결 복원")
            if getattr(flow, "log", None):
                flow.log("work_ledger_release_reopened", problems=len(problems))
            _ckpt(flow)
    return ("작업 장부 미완 — 마일스톤마다 SubTask가 1개 이상, 각 단위마다 백로그가 1개 이상 "
            "존재하고 모두 완료/중단되어야 합니다: " + " · ".join(problems[:6]))


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
    _unmatched, _rereported, _untrusted_release = [], [], []
    _current_epoch = write_revision(flow)
    _current_stamp = workspace_artifact_stamp(flow)
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
            if getattr(c, "release_lock", False):
                # 공개 report_iter의 문자열은 증거가 아니다. SYS direct verifier 또는 정확한 검증 challenge
                # 안에서 run 도구가 발급한 단일사용 rc=0 receipt만 private marker를 가질 수 있다.
                verified_command = str(r.get("_verified_command") or "").strip()
                command_hash = str(r.get("_verified_command_hash") or "")
                spec_hash = str(r.get("_verified_spec_hash") or "")
                trusted = (
                    bool(r.get("_sys_run_receipt_id"))
                    and bool(r.get("_sys_run_receipt"))
                    and bool(verified_command)
                    and command_hash == verifier_command_hash(verified_command)
                    and spec_hash == verifier_spec_hash(c.desc, c.verify)
                    and int(r.get("_verified_write_epoch", -2)) == _current_epoch
                    and bool(_current_stamp)
                    and str(r.get("_verified_artifact_stamp") or "") == _current_stamp
                    and _release_boundary_ready(flow, obj)
                )
                if not trusted:
                    _untrusted_release.append(c.desc[:60])
                    continue
                c.passed, c.evidence = True, str(r["_sys_run_receipt"])[:400]
                c.evidence_source = "sys_run"
                c.receipt_id = str(r["_sys_run_receipt_id"])[:120]
                c.verified_write_epoch = _current_epoch
                c.verified_artifact_stamp = _current_stamp
                c.verified_command = verified_command[:500]
                c.verified_command_hash = command_hash
                c.verified_spec_hash = spec_hash
            else:
                c.passed, c.evidence = True, ev[:400]
    if isinstance(obj, Milestone):
        _sync_goal_locked_evidence(flow, obj)
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
    if _untrusted_release:
        note += ("\n[GOAL 잠금 증거 거부] 임의 report_iter evidence나 백로그 종결 전 영수증으로는 "
                 "최종 계약을 열 수 없습니다. 모든 백로그가 끝난 뒤 SYS 자동검증이 발급한 rc=0 run "
                 "receipt로 다시 실증하세요: " + " · ".join(_untrusted_release[:4]))
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
    if obj.iter_stuck >= stuck_limit():
        if flow.log:
            flow.log("ms_iter_stuck", kind=kind, id=oid, iter=obj.iter_n, stuck=obj.iter_stuck)
        note += (f"\n[정체 — {obj.iter_stuck}회 연속 진전 없음] 반복이 결과를 못 바꾸고 있습니다. "
                 f"조건이 이번 주기 범위 밖이거나 환경상 불가라면 renegotiate_criterion(대상 조건, 사유)로 "
                 f"재협상하세요 — 로드맵에 다음 주기가 있으면 사람 없이 그 주기로 즉시 이월되고, "
                 f"못 옮길 때만 사람 승인을 구합니다. 무한 반복하지 마세요.")
    return False, note


def stuck_limit() -> int:
    """진전 없는 연속 iter 임계(기본 3). [단일 진실원(2026-07-27)] 종전엔 import 시 1회 읽는 상수와
    호출마다 읽는 sys_core가 갈려, 러너 기동 뒤 값을 바꾸면 두 곳이 어긋났다. 여기 한 곳만 읽는다."""
    try:
        return max(1, int(os.environ.get("ORGANT_ITER_STUCK_LIMIT", "3") or 3))
    except ValueError:
        return 3


_STUCK_LIMIT = stuck_limit()   # 하위호환 별칭(읽는 시점 고정 — 새 코드는 stuck_limit()을 써라)


def roadmap_done_count(flow) -> int:
    """로드맵 진척 = **계획된 주기 중** 완주한 수.

    [복기 주기는 로드맵 칸이 아니다(2026-07-27, 전수감사)] 종전엔 done인 마일스톤을 전부 셌다.
    그런데 e2e 실패로 열리는 복기 주기(origin="e2e:…")도 완주하면 그 수에 합산돼, **로드맵에 남은
    계획 단계가 있는데도 '최종 주기 도달'로 판정**됐다 — 사다리가 한 칸씩 잘려나가고 다음 계획
    주기 회의가 안 열린다. 복기는 계획에 없던 보충이므로 분모에서도 분자에서도 뺀다.
    """
    return sum(1 for m in (getattr(flow, "milestones", None) or [])
               if m.status == "done" and not str(getattr(m, "origin", "") or "").startswith("e2e:"))


def roadmap_phases(flow):
    """[로드맵 phase 정규화(2026-07-20, 사용자: '개입 최대한 줄여')] 로드맵 항목을 phase 목록으로 —
    회의 골격이 '단계: 최소버전 → 확장 → 완성' 한 줄을 유도하므로, **띄운 화살표(' → ')만** 구분자로
    분해한다(조건 파서의 '띄어쓴 파이프만' 계약과 같은 축 — phase 서술 속 '선택→결과' 같은 붙은
    화살표는 안 쪼갬). 종전엔 한 줄 로드맵이 phase 1개로 세어져 다음 주기 회의가 영영 안 열렸다."""
    out = []
    for r in (getattr(flow, "roadmap", None) or []):
        out += [p.strip() for p in re.split(r"\s+→\s+", str(r or "")) if p.strip()]
    return out


_STAMP_SKIP_DIRS = {
    ".git", ".collab", ".cache", ".mypy_cache", ".npm", ".pytest_cache",
    ".ruff_cache", ".tox", ".venv", "__pycache__", "coverage", "htmlcov",
    "node_modules", "playwright-report", "test-results", "venv",
}
_STAMP_SKIP_SUFFIXES = {
    ".coverage", ".log", ".pid", ".pyc", ".pyo", ".swp", ".tmp",
}
_STAMP_BROAD_ROOTS = {"/", "/tmp", "/var", "/var/tmp", "/home", "/root", "/opt", "/srv"}


def write_revision(flow) -> int:
    """permissions가 영속하는 Write/Edit 누계. release receipt와 현재 산출물 사이 TOCTOU 1차 epoch."""
    total = 0
    for value in (getattr(flow, "writes_by_role", None) or {}).values():
        try:
            total += int(value or 0)
        except (TypeError, ValueError):
            continue
    return total


def _stamp_root(flow) -> str:
    """스탬프 대상 작업공간의 실경로(부적격이면 빈 문자열)."""
    root = str(getattr(flow, "workspace", "") or "").strip()
    try:
        root = os.path.realpath(root)
    except OSError:
        return ""
    if not root or root in _STAMP_BROAD_ROOTS or not os.path.isdir(root):
        return ""
    return root


def _stamp_files(root):
    """manifest 대상 파일을 (상대경로, 절대경로)로 산출 — 스탬프와 스냅샷이 같은 눈을 쓴다."""
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _STAMP_SKIP_DIRS)
        for name in sorted(files):
            if name in (".DS_Store",) or any(name.endswith(s) for s in _STAMP_SKIP_SUFFIXES):
                continue
            path = os.path.join(base, name)
            yield os.path.relpath(path, root).replace(os.sep, "/"), path


def workspace_file_digests(flow) -> dict:
    """manifest 대상 파일의 {상대경로: 내용해시} — 검증 실행 전후 비교용."""
    root = _stamp_root(flow)
    if not root:
        return {}
    out = {}
    try:
        for rel, path in _stamp_files(root):
            try:
                fh = hashlib.sha256()
                with open(path, "rb") as fp:
                    for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                        fh.update(chunk)
                out[rel] = fh.hexdigest()
            except (OSError, ValueError):
                out[rel] = "ERR"
    except OSError:
        return {}
    return out


def known_verifier_commands(flow) -> set:
    """이 판에서 **회의가 검증 수단으로 결속한** 명령들(정규화) — e2e 분모의 verifier와 조건 영수증."""
    out = set()
    for item in (getattr(flow, "e2e_checklist", None) or []):
        if isinstance(item, dict):
            cmd = normalize_verifier_command(item.get("verifier_command"))
            if cmd:
                out.add(cmd)
    for ms in (getattr(flow, "milestones", None) or []):
        for c in list(getattr(ms, "criteria", None) or []) + list(
                getattr(ms, "locked_criteria", None) or []):
            for value in (getattr(c, "verified_command", ""),
                          ratified_goal_verifier_command(
                              c, getattr(flow, "workspace", ""), require_existing=False)):
                cmd = normalize_verifier_command(value)
                if cmd:
                    out.add(cmd)
    return out


def record_run_outputs(flow, before) -> int:
    """**검증 실행이 건드린 파일**을 검증 산출로 등재한다 — 스탬프에서 제외될 대상.

    [2026-07-27, U-067 실측] 봇이 만든 검증기는 실행할 때마다 리포트를 덮어쓴다
    (`artifacts/verify_ui/latest.json`). 그 파일이 authoring manifest에 들어 있으면 **검증할 때마다
    스탬프가 바뀌어 방금 선 영수증이 스스로 무효**가 되고, 무효면 주기가 다시 열려 또 검증하고…
    구조적으로 끝나지 않는다(실측: e2e 개시 3회·통과 0, 그 사이 Write/Edit 0건 — 봇이 고쳐서가
    아니라 검증이 자기를 무효화했다).

    스탬프의 물음은 '저작한 산출물이 영수증 이후 바뀌었나'이므로 검증기 자신의 리포트는 애초에
    대상이 아니다(docstring의 'authoring 입력 manifest'가 원래 뜻). 등재 대상은 **회의가 검증
    수단으로 결속한 명령**(known_verifier_commands)이 만들거나 고친 파일뿐이다 — 봇의 임의 run은
    등재하지 않으므로 `python -c`로 제품을 몰래 고쳐도 스탬프는 그대로 잡는다(원 위협 모델 유지).
    """
    after = workspace_file_digests(flow)
    before = before or {}
    touched = {rel for rel, dg in after.items() if before.get(rel) != dg}
    if not touched:
        return 0
    # [중복 증거 관측(2026-07-28, U-079 실측)] 같은 검증 결과가 이름만 바꿔 여러 번 저장된다:
    # motion-b1/motion-b2-hooks/motion-evidence가 **바이트 단위로 동일**(3개), b1-baseline/b2도 동일(2개).
    # 제품 150K에 증거 6.0MB — 같은 검사를 다시 돌려 새 이름으로 남긴 몫이다. 재실행 자체를 막으려면
    # 명령·입력 동일성 판정이 필요해 여기서 끊지 않고, **먼저 규모를 사실로 남긴다**(수리는 관측 뒤).
    try:
        _seen = {}
        for _rel in sorted(touched):
            _dg = after.get(_rel)
            if not _dg:
                continue
            _seen.setdefault(_dg, []).append(_rel)
        _dups = {d: r for d, r in _seen.items() if len(r) > 1}
        if _dups and getattr(flow, "log", None):
            flow.log("duplicate_verification_artifact",
                     groups=len(_dups), files=sum(len(r) for r in _dups.values()),
                     sample=";".join(sorted(next(iter(_dups.values())))[:3])[:160])
    except Exception:
        pass
    known = set(getattr(flow, "run_outputs", None) or ())
    known |= touched
    try:
        flow.run_outputs = known
    except Exception:
        return 0
    if getattr(flow, "log", None):
        flow.log("run_output_registered", n=len(touched), total=len(known),
                 sample=", ".join(sorted(touched)[:3])[:120])
    return len(touched)


def workspace_artifact_stamp(flow) -> str:
    """작업공간의 authoring 입력 manifest hash.

    캐시·의존성·테스트 런타임 출력은 제외하되 소스·설정·정적 자산은 내용까지 해시한다. run 도구가
    Python/sed 같은 간접 쓰기로 permissions의 Write/Edit epoch를 우회해도 최종 release/e2e가 이전
    영수증을 재사용하지 못하게 하는 2차 버전이다. 읽기 실패도 manifest에 표식으로 포함해 fail-open하지
    않는다.
    """
    root = _stamp_root(flow)
    if not root:
        return ""
    # [실행이 낳은 파일은 저작 입력이 아니다(2026-07-27, U-067 실측)] record_run_outputs 참조.
    skip_rel = set(getattr(flow, "run_outputs", None) or ())
    h = hashlib.sha256()
    try:
        for rel, path in _stamp_files(root):
            if rel in skip_rel:
                continue
            try:
                if os.path.islink(path):
                    link_hash = hashlib.sha256(
                        ("LINK:" + os.readlink(path)).encode("utf-8", "replace")
                    )
                    target = os.path.realpath(path)
                    # regular-file symlink는 링크 문자열뿐 아니라 실제 실행되는 target 내용도
                    # 결속한다. target이 바뀌었는데 receipt가 살아남는 구멍을 닫는다.
                    if os.path.isfile(target):
                        link_hash.update(b"\0TARGET:")
                        with open(target, "rb") as fp:
                            for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                                link_hash.update(chunk)
                    digest = link_hash.digest()
                else:
                    file_hash = hashlib.sha256()
                    with open(path, "rb") as fp:
                        # 큰 파일도 표본이 아니라 전체 내용을 스트리밍한다. 앞·뒤+크기 표본은
                        # 동일 크기 파일의 중간 바이트 변경을 놓쳐 release/e2e 영수증을 이전
                        # 산출물에 재사용하게 만든다. chunk 단위라 메모리는 파일 크기와 무관하다.
                        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                            file_hash.update(chunk)
                    digest = file_hash.digest()
                h.update(rel.encode("utf-8", "surrogateescape") + b"\0")
                h.update(digest)
            except (OSError, ValueError) as exc:
                h.update(rel.encode("utf-8", "surrogateescape") + b"\0ERR:")
                h.update(type(exc).__name__.encode())
    except OSError:
        return ""
    return h.hexdigest()


def invalidate_e2e_state(flow, reason: str = "") -> bool:
    """새 마일스톤/산출물 버전이 생기면 이전 경계의 checklist·results·verdict를 원자적으로 폐기."""
    had = any(getattr(flow, name, None) is not None
              for name in ("e2e_checklist", "e2e_results", "wrapup_state"))
    flow.e2e_checklist = None
    flow.e2e_results = None
    flow.wrapup_state = None
    flow._e2e_receipt_nonce = None
    # [교차검증 위임 재발송 가능하게(2026-07-28, U-079 실측)] 마감 관문의 '다른 멤버 검증 1회'는
    # 구조가 한 번 위임해 채운다(_close_cc_sent). 그런데 위임 직후 e2e가 무효화되면 판이 마감
    # 앞단으로 밀리고, 그 사이 대상 봇은 깨어나지 못한 채 위임이 흐지부지된다 — 표식만 True로 남아
    # **두 번째 위임이 영영 안 나간다**(실측: crosscheck_sent 1회 → cc 0 유지 → PM만 마감 재시도).
    # e2e 경계가 폐기되는 이 자리에서 표식도 함께 되돌린다. 교차검증이 이미 성립했으면(cc>0) 위임
    # 조건 자체가 거짓이라 재발송은 일어나지 않는다 — 중복 위임 위험 없음.
    if getattr(flow, "_close_cc_sent", False):
        flow._close_cc_sent = False
    if hasattr(flow, "_run_receipts"):
        flow._run_receipts = {}
    if had and getattr(flow, "log", None):
        flow.log("e2e_invalidated", reason=str(reason or "")[:100])
    return had


def _goal_locked_refs(flow):
    """Task의 상위 GOAL 조건 정본.

    현재 GOAL.md/current acceptance를 먼저 두고 저장된 모든 locked refs를 합친다. 같은 desc의 구
    체크포인트가 다른 verify를 들고 있으면 최신 정본이 우선한다. 저장본 일부가 존재한다는 이유로 정본의
    나머지 조건이 통째로 가려지지 않으며, 정본에서 읽히지 않는 고유 저장 조건은 보수적으로 유지한다.
    """
    canonical = _mk_criteria(_goal_acceptance_entries(flow))
    stored = []
    for ms in (getattr(flow, "milestones", None) or []):
        stored.extend(list(getattr(ms, "locked_criteria", None) or []))
    out, seen_desc = [], set()
    for c in canonical + stored:
        desc, verify = str(c.desc or "").strip(), str(c.verify or "").strip()
        if not desc or not verify or desc in seen_desc:
            continue
        seen_desc.add(desc)
        out.append(c)
    return out


def ratified_goal_verifier_command(criterion, workspace="", require_existing=True) -> str:
    """자연어 GOAL에 회의가 봉인한 exact command가 현재 canonical spec과 정확히 결속되면 반환."""
    command = normalize_verifier_command(
        getattr(criterion, "ratified_verifier_command", ""))
    if (not command
            or getattr(criterion, "ratified_verifier_command_hash", "")
            != verifier_command_hash(command)
            or getattr(criterion, "ratified_verifier_spec_hash", "")
            != verifier_spec_hash(criterion.desc, criterion.verify)):
        return ""
    return direct_verifier_command(
        command, workspace, require_existing=require_existing)


def _final_release_milestone(flow):
    """현재 상태에서 최종 GOAL 인수 분모를 맡을 마일스톤. 중간 로드맵이면 None."""
    live = [m for m in (getattr(flow, "milestones", None) or [])
            if m.status != "superseded"]
    if not live:
        return None
    phases = roadmap_phases(flow)
    done_n = roadmap_done_count(flow)
    active = next((m for m in live if m.status != "done"), None)
    if active is not None:
        if phases and done_n + 1 < len(phases):
            return None
        return active
    if phases and done_n < len(phases):
        return None
    return live[-1]


def _sync_goal_locked_evidence(flow, target=None):
    """최종 active 조건의 실증 상태를 비분모 locked 참조에도 미러한다(HUD·복구 원장 일치)."""
    target = target or _final_release_milestone(flow)
    if target is None:
        return 0
    by_key = {(c.desc.strip(), c.verify.strip()): c for c in target.criteria
              if getattr(c, "release_lock", False)}
    changed = 0
    for ms in (getattr(flow, "milestones", None) or []):
        for ref in (getattr(ms, "locked_criteria", None) or []):
            src = by_key.get((ref.desc.strip(), ref.verify.strip()))
            if src is None:
                continue
            state = (
                bool(src.passed), str(src.evidence or ""), str(src.status or "active"),
                str(getattr(src, "evidence_source", "") or ""),
                str(getattr(src, "receipt_id", "") or ""),
                int(getattr(src, "verified_write_epoch", -1)),
                str(getattr(src, "verified_artifact_stamp", "") or ""),
                str(getattr(src, "verified_command", "") or ""),
                str(getattr(src, "verified_command_hash", "") or ""),
                str(getattr(src, "verified_spec_hash", "") or ""),
                str(getattr(src, "ratified_verifier_command", "") or ""),
                str(getattr(src, "ratified_verifier_command_hash", "") or ""),
                str(getattr(src, "ratified_verifier_spec_hash", "") or ""),
            )
            old = (
                bool(ref.passed), str(ref.evidence or ""), str(ref.status or "active"),
                str(getattr(ref, "evidence_source", "") or ""),
                str(getattr(ref, "receipt_id", "") or ""),
                int(getattr(ref, "verified_write_epoch", -1)),
                str(getattr(ref, "verified_artifact_stamp", "") or ""),
                str(getattr(ref, "verified_command", "") or ""),
                str(getattr(ref, "verified_command_hash", "") or ""),
                str(getattr(ref, "verified_spec_hash", "") or ""),
                str(getattr(ref, "ratified_verifier_command", "") or ""),
                str(getattr(ref, "ratified_verifier_command_hash", "") or ""),
                str(getattr(ref, "ratified_verifier_spec_hash", "") or ""),
            )
            if old != state:
                (ref.passed, ref.evidence, ref.status, ref.evidence_source, ref.receipt_id,
                 ref.verified_write_epoch, ref.verified_artifact_stamp,
                 ref.verified_command, ref.verified_command_hash,
                 ref.verified_spec_hash, ref.ratified_verifier_command,
                 ref.ratified_verifier_command_hash,
                 ref.ratified_verifier_spec_hash) = state
                ref.block_reason = ""
                changed += 1
    return changed


def promote_final_locked_criteria(flow, checkpoint=True) -> int:
    """GOAL 잠금 조건을 **최종 주기에서만** active 검증 조건으로 승격한다.

    복원된 구 체크포인트처럼 최종 주기가 이미 done이어도 증거 없는 잠금 조건이 새로 발견되면 open으로
    되돌린다. 같은 desc의 하위 조건이 verify를 바꿨다면 중복 desc를 만들지 않고 상위 verify가 최종
    계약으로 우선한다. 정확히 같은 조건은 재사용해 불필요한 분모 중복을 막는다.
    """
    refs = _goal_locked_refs(flow)
    target = _final_release_milestone(flow)
    if not refs or target is None:
        return 0
    changed = 0
    needs_verify = False
    # 부분/구 locked snapshot도 정본 우선 합집합으로 복구한다. 정확히 같은 참조의 실증 상태는 보존하고,
    # 같은 desc의 낡은 verify는 정본으로 교체한다.
    for ms in (getattr(flow, "milestones", None) or []):
        if getattr(ms, "status", "") == "superseded":
            continue          # 폐기된 주기에 잠금 조건을 되쓰지 않는다(전수감사)
        existing = {(c.desc.strip(), c.verify.strip()): c
                    for c in (getattr(ms, "locked_criteria", None) or [])}
        merged = []
        for ref in refs:
            exact_ref = existing.get((ref.desc.strip(), ref.verify.strip()))
            merged.append(exact_ref or Criterion(desc=ref.desc, verify=ref.verify))
        if [(c.desc, c.verify) for c in merged] != [
                (c.desc, c.verify) for c in (getattr(ms, "locked_criteria", None) or [])]:
            ms.locked_criteria = merged
            changed += 1
    current_epoch = write_revision(flow)
    current_stamp = workspace_artifact_stamp(flow)
    # [비준 승계(2026-07-27, U-067 실측)] e2e 실패로 새 주기가 열리면 잠금 조건이 그 주기로 **복사**
    # 되는데, 종전엔 비준(회의가 정한 exact 검증 명령)이 따라가지 않아 새 조건이 '미비준'이 됐다.
    # 그러면 SYS는 매 바퀴 "비준하라"만 안내하고 검증은 한 번도 돌지 않는다 — 같은 세 갈래가 12번
    # 반복되며 단계가 36개까지 불어난 실측 폭주의 뿌리다. 비준은 (desc, verify) 쌍에 spec 해시로
    # 결속돼 있으므로, **같은 조건이면 같은 비준**이다(승계해도 무엇을 증명으로 인정하는지는 안 바뀐다).
    # 영수증(passed·evidence)은 승계하지 않는다 — 증명은 새 산출물에서 다시 벌어야 한다.
    _ratified_by_spec = {}
    for _ms_r in (getattr(flow, "milestones", None) or []):
        for _c_r in list(getattr(_ms_r, "criteria", None) or []) + list(
                getattr(_ms_r, "locked_criteria", None) or []):
            _cmd_r = str(getattr(_c_r, "ratified_verifier_command", "") or "")
            if not _cmd_r:
                continue
            _spec_r = verifier_spec_hash(_c_r.desc, _c_r.verify)
            if getattr(_c_r, "ratified_verifier_spec_hash", "") != _spec_r:
                continue                      # 조건이 바뀐 뒤의 낡은 비준은 승계 대상 아님
            _ratified_by_spec.setdefault(_spec_r, _c_r)

    def _inherit_ratification(_c):
        """같은 조건에 이미 선 비준이 있으면 물려준다(없으면 그대로)."""
        if str(getattr(_c, "ratified_verifier_command", "") or ""):
            return False
        src_r = _ratified_by_spec.get(verifier_spec_hash(_c.desc, _c.verify))
        if src_r is None or src_r is _c:
            return False
        _c.ratified_verifier_command = src_r.ratified_verifier_command
        _c.ratified_verifier_command_hash = src_r.ratified_verifier_command_hash
        _c.ratified_verifier_spec_hash = src_r.ratified_verifier_spec_hash
        if flow.log:
            flow.log("goal_lock_ratification_inherited", desc=str(_c.desc)[:60])
        return True

    for ref in refs:
        exact = next((c for c in target.criteria
                      if c.desc.strip() == ref.desc.strip()
                      and c.verify.strip() == ref.verify.strip()), None)
        if exact is None:
            same_desc = next((c for c in target.criteria
                              if c.desc.strip() == ref.desc.strip()), None)
            if same_desc is not None:
                # desc는 report_iter의 식별자라 중복할 수 없다. canonical 자연어 GOAL spec은 그대로
                # 복원하되, 최종 마일스톤 *회의가 비준한* same-desc exact command는 별도 immutable
                # ratification으로 먼저 보존한다(작업 전이라 script가 아직 없어도 argv 계약은 검사).
                ratified = direct_verifier_command(
                    same_desc.verify, getattr(flow, "workspace", ""),
                    require_existing=False,
                )
                if ratified and not direct_verifier_command(
                        ref.verify, getattr(flow, "workspace", ""),
                        require_existing=False):
                    existing_ratified = str(
                        getattr(same_desc, "ratified_verifier_command", "") or "")
                    if not existing_ratified or existing_ratified == ratified:
                        same_desc.ratified_verifier_command = ratified
                        same_desc.ratified_verifier_command_hash = verifier_command_hash(ratified)
                        same_desc.ratified_verifier_spec_hash = verifier_spec_hash(
                            ref.desc, ref.verify)
                if flow.log:
                    flow.log("goal_lock_contract_override", ms=target.ms_id, desc=ref.desc[:60])
                same_desc.verify = ref.verify
                same_desc.passed, same_desc.evidence = False, ""
                same_desc.status, same_desc.block_reason = "active", ""
                same_desc.evidence_source, same_desc.receipt_id = "", ""
                same_desc.verified_write_epoch, same_desc.verified_artifact_stamp = -1, ""
                same_desc.verified_command = ""
                same_desc.verified_command_hash = same_desc.verified_spec_hash = ""
                same_desc.release_lock = True
                exact = same_desc
            else:
                exact = Criterion(desc=ref.desc, verify=ref.verify, release_lock=True)
                target.criteria.append(exact)
            _inherit_ratification(exact)
            changed += 1
            needs_verify = True
        elif not getattr(exact, "release_lock", False):
            exact.release_lock = True
            changed += 1
        _inherit_ratification(exact)
        # 상위 잠금은 waived/blocked·임의 evidence·옛 산출물 영수증으로 우회할 수 없다.
        natural_lock = not direct_verifier_command(
            exact.verify, getattr(flow, "workspace", ""), require_existing=False)
        ratified_command = (
            ratified_goal_verifier_command(
                exact, getattr(flow, "workspace", ""), require_existing=True)
            if natural_lock else ""
        )
        valid_receipt = (
            exact.passed
            and str(exact.evidence or "").strip()
            and getattr(exact, "evidence_source", "") == "sys_run"
            and bool(getattr(exact, "receipt_id", ""))
            and int(getattr(exact, "verified_write_epoch", -1)) == current_epoch
            and bool(current_stamp)
            and getattr(exact, "verified_artifact_stamp", "") == current_stamp
            and bool(getattr(exact, "verified_command", ""))
            and getattr(exact, "verified_command_hash", "")
            == verifier_command_hash(getattr(exact, "verified_command", ""))
            and getattr(exact, "verified_spec_hash", "")
            == verifier_spec_hash(exact.desc, exact.verify)
            and (
                not natural_lock
                or (
                    bool(ratified_command)
                    and normalize_verifier_command(
                        getattr(exact, "verified_command", "")) == ratified_command
                )
            )
        )
        if not valid_receipt:
            if exact.passed or exact.evidence or exact.status != "active" or exact.block_reason:
                changed += 1
            exact.passed, exact.evidence = False, ""
            exact.status, exact.block_reason = "active", ""
            exact.evidence_source, exact.receipt_id = "", ""
            exact.verified_write_epoch, exact.verified_artifact_stamp = -1, ""
            exact.verified_command = ""
            exact.verified_command_hash = exact.verified_spec_hash = ""
            needs_verify = True
    if needs_verify and target.status in ("wrapup", "done"):
        target.status = "open"
        target.iter_stuck = 0
        changed += 1
        invalidate_e2e_state(flow, "GOAL 잠금 재실증 필요")
        # [무한 재개방 차단(2026-07-27, U-067 실측)] 재개방 → 재검증(통과) → 다시 재개방이 끝없이
        # 돈다. 검증이 매번 통과하는데 잠금이 매번 되돌리면 봇이 풀 문제가 아니라 계약 불일치이므로
        # 누적 3회에 사람에게 넘긴다(파킹 신호만 세우고 집행은 오케스트레이터). **성공 1회로 리셋하면
        # 안 된다** — 이 순환은 매 바퀴 '통과'를 포함하므로 카운터가 영원히 1에 머문다(실측). 리셋은
        # e2e가 실제로 판정에 닿았을 때만(rule_e2e_finish).
        _n = int(getattr(flow, "_goal_lock_reopens", 0) or 0) + 1
        try:
            flow._goal_lock_reopens = _n
        except Exception:
            _n = 1
        if flow.log:
            flow.log("goal_lock_final_reopened", ms=target.ms_id, criteria=len(refs), n=_n)
        if _n >= 3:
            try:
                flow._stage_stuck = f"GOAL 잠금 재개방 반복({_n}회) — 비준 명령과 검증 영수증 불일치"
                if flow.log:
                    flow.log("goal_lock_reopen_parked", ms=target.ms_id, n=_n)
            except Exception:
                pass
    changed += _sync_goal_locked_evidence(flow, target)
    if changed:
        _pnote(flow, f"[GOAL 최종 인수] 상위 잠금 조건 {len(refs)}건을 {target.ms_id} 실증 분모로 확정")
        if flow.log:
            flow.log("goal_lock_promoted", ms=target.ms_id, criteria=len(refs), reopened=needs_verify)
        if checkpoint:
            _ckpt(flow)
    return changed


def goal_locked_release_error(flow):
    """증거 없는 GOAL 잠금 조건이 있으면 최종 e2e/complete_task 차단 사유를 반환한다."""
    promote_final_locked_criteria(flow)
    refs = _goal_locked_refs(flow)
    if not refs:
        return None
    target = _final_release_milestone(flow)
    if target is None:
        return "GOAL 잠금 조건은 로드맵 최종 주기에서 실증해야 합니다."
    active = {(c.desc.strip(), c.verify.strip()): c for c in target.criteria
              if getattr(c, "release_lock", False)}
    current_epoch = write_revision(flow)
    current_stamp = workspace_artifact_stamp(flow)
    missing = []
    for ref in refs:
        c = active.get((ref.desc.strip(), ref.verify.strip()))
        natural_lock = bool(c) and not direct_verifier_command(
            c.verify, getattr(flow, "workspace", ""), require_existing=False)
        ratified_command = (
            ratified_goal_verifier_command(
                c, getattr(flow, "workspace", ""), require_existing=True)
            if natural_lock else ""
        )
        if (c is None or not c.passed or not str(c.evidence or "").strip()
                or getattr(c, "evidence_source", "") != "sys_run"
                or not getattr(c, "receipt_id", "")
                or int(getattr(c, "verified_write_epoch", -1)) != current_epoch
                or not current_stamp
                or getattr(c, "verified_artifact_stamp", "") != current_stamp
                or not getattr(c, "verified_command", "")
                or getattr(c, "verified_command_hash", "")
                != verifier_command_hash(getattr(c, "verified_command", ""))
                or getattr(c, "verified_spec_hash", "")
                != verifier_spec_hash(c.desc, c.verify)
                or (
                    natural_lock
                    and (
                        not ratified_command
                        or normalize_verifier_command(
                            getattr(c, "verified_command", "")) != ratified_command
                    )
                )):
            missing.append(ref)
    if not missing:
        _sync_goal_locked_evidence(flow, target)
        return None
    return ("GOAL 잠금 조건 실증 미완: "
            + " · ".join(f"{c.desc[:48]} (`{c.verify[:70]}`)" for c in missing[:5])
            + " — 최종 주기의 기존 마일스톤 검증 경로에서 실제 run 증거로 통과해야 합니다.")


def defer_criterion(flow, obj, c, reason: str):
    """[조건 이월 — 사람 개입 없는 1차 해소(2026-07-20, 사용자: '개입 최대한 줄여')] 이번 주기 범위
    밖 조건을 '다음 주기로 이월'한다 — 잣대를 버리는 게 아니라 옮기는 것: 조건이 obj.carried 원장에
    실리고 다음 open_milestone이 기계로 새 주기 잣대에 합류시킨다(봇 약속이 아니라 구조가 보증 →
    완료 참칭 불가). 성립 조건(전부): ①마일스톤 조건일 것 ②미충족일 것 ③로드맵에 받아줄 후속
    phase가 있을 것 ④이월 후에도 이번 주기에 잣대가 최소 1개 남을 것. 못 옮기면 None(호출측이
    사람 경로로 — 그때만 최후수단)."""
    if (not isinstance(obj, Milestone) or c.passed or c.status == "waived"
            or getattr(c, "release_lock", False)):
        return None
    phases = roadmap_phases(flow)
    done_n = roadmap_done_count(flow)
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
    if getattr(c, "release_lock", False):
        # [정본에서 사라진 잠금은 팀의 계약이 아니다(2026-07-27, 전수감사)] 저장본 합집합 때문에
        # GOAL 문구가 바뀌어도 옛 desc가 refs에 남고, 그 조건도 fresh 영수증을 요구한다. 그런데
        # 잠금은 이월·포기가 금지라 **출구가 사람뿐**이었다 — 지금 팀이 합의한 계약에 없는 조건이
        # 판을 영원히 막는 셈이다. 현재 정본에 없으면 잠금 취급을 풀고 일반 사다리(이월)로 보낸다.
        try:
            _canon = {str(x.desc or "").strip() for x in _mk_criteria(_goal_acceptance_entries(flow))}
        except Exception:
            _canon = set()
        if _canon and str(c.desc or "").strip() not in _canon:
            c.release_lock = False
            if flow.log:
                flow.log("goal_lock_stale_released", target=str(c.desc)[:60])
            deferred_stale = defer_criterion(flow, obj, c, reason)
            if deferred_stale:
                return deferred_stale
        # [사다리에서 떨어지던 칸(2026-07-27, U-067 실측)] 잠금 조건은 이월·포기가 금지인 게 맞지만,
        # 종전엔 여기서 그냥 되돌아 나와 **정체 카운터도 안 풀고 사람에게도 안 올렸다** — 같은 자리를
        # 12바퀴 돌며 단계가 36개까지 불어났다. 포기는 여전히 불가하되, 반복 정체는 **사람에게 넘긴다**
        # (봇이 못 푸는 자리라는 사실 자체가 사람이 볼 신호다). 카운터를 풀어 경보 반복도 멈춘다.
        obj.iter_stuck = 0
        oid_l = getattr(obj, "ms_id", None) or getattr(obj, "st_id", "")
        try:
            flow._stage_stuck = (
                f"GOAL 잠금 조건이 반복해서 실증되지 않습니다 — {c.desc[:60]}")
        except Exception:
            pass
        if flow.log:
            flow.log("goal_lock_stuck_parked", id=oid_l, target=c.desc[:60])
        esc_l = getattr(flow, "escalate_to_human", None)
        if callable(esc_l):
            try:
                esc_l(f"[GOAL 잠금 정체] '{c.desc[:60]}'이(가) 반복 검증에도 실증되지 않습니다 — "
                      f"{reason[:120]}. 조건을 바꿀지, 실증 방법을 바꿀지 판단이 필요합니다.")
            except Exception:
                pass
        return (f"상위 GOAL 잠금 조건은 이월·포기할 수 없습니다: {c.desc[:40]} — "
                f"실제 실증 `{c.verify[:80]}`을 통과해야 Task 최종 release가 열립니다. "
                f"반복 정체를 사람에게 알렸습니다.")
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
    if getattr(c, "release_lock", False):
        # 구 체크포인트·외부 복원에서 blocked_pending이 남아 있어도 사람 승인 경로가 상위 계약을
        # 포기시키는 우회로가 되면 안 된다. 잠금을 active로 복구하고 실제 verify만 출구로 둔다.
        c.status, c.block_reason = "active", ""
        _ckpt(flow)
        return (f"상위 GOAL 잠금 조건은 포기 승인할 수 없습니다: {c.desc[:40]} — "
                f"실제 실증 `{c.verify[:80]}`을 통과해야 합니다.")
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
    # [정상 사다리는 상한에 안 걸린다(2026-07-27, 전수감사)] 단계 재개설 상한은 '같은 단계가
    # 반복되는데 아무것도 완주하지 않는' 폭주를 잡는 장치다. 성공 착지까지 세면 다단계 로드맵
    # (주기마다 계획·분해 회의)이 정상 진행 중에 컷된다. **주기가 실제로 완주한 이 자리에서만**
    # 턴다 — 진전이 있었다는 뜻이므로 반복이 아니다.
    if isinstance(obj, Milestone):
        for _k in ("_stage_open_n", "_pf_repeat"):
            try:
                if getattr(flow, _k, None):
                    setattr(flow, _k, {})
            except Exception:
                pass
    _was_done = (getattr(obj, "status", "") == "done")
    obj.status = "done"
    _ckpt(flow)
    if flow.log:
        _oid = getattr(obj, "ms_id", None) or getattr(obj, "st_id", "")
        # [완수 마커 중복(2026-07-29, 사용자 지적: '보고는 왜 2개고')] 재개·재검증으로 이 경로를 다시
        # 지나면 같은 주기의 '완수' 줄이 또 게시돼 화면에 같은 제목이 두 번 뜬다(실측: 6998·7017).
        # 이미 done인 대상은 완수를 다시 알리지 않는다 — 상태 전이가 아니라 재확인이기 때문이다.
        if not _was_done:
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
        # [진척 표기도 같은 눈으로(2026-07-27, 전수감사)] 여기만 정규화 안 한 raw roadmap을 써서
        # ①한 줄 화살표 로드맵("최소버전 → 확장")이면 len=1이라 **안내가 아예 안 떴고**
        # ②숫자가 한 칸 앞섰다(1주기 완주에 "2/2 완수"). 다른 전 경로가 쓰는 roadmap_phases와
        # 복기 제외 셈법(roadmap_done_count)으로 통일한다.
        _rm = roadmap_phases(flow)
        _done_n = roadmap_done_count(flow)
        if _done_n < len(_rm):
            _pnote(flow, f"[다음 단계] 로드맵 {_done_n}/{len(_rm)} 완수 — 다음: **{_rm[_done_n][:60]}**. "
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
    rows = []
    for defect in (defects or []):
        if isinstance(defect, dict):
            line = (
                f"({defect.get('kind')}) {str(defect.get('spec') or '')[:60]} — "
                f"관측: {str(defect.get('observed') or '')[:60]}"
            )
            command = normalize_verifier_command(defect.get("verifier_command"))
            # 실패 항목에 SYS가 이미 결속한 exact verifier가 있으면 보충 마일스톤도 같은 재현
            # 명령을 계약으로 이어받는다. 자연어 절차로 되돌아가 다음 e2e_open을 막지 않는다.
            if command and not looks_like_verification_command(
                    command, getattr(flow, "workspace", "")):
                command = ""
            rows.append((line.strip(), command))
        else:
            line = str(defect).strip()
            if line:
                rows.append((line, ""))
    ds = [line for line, _command in rows if line]
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
    entries = [
        {
            "desc": f"결함 해소: {line[:80]}",
            "verify": command or f"run으로 재현 절차 재실행 → 재현 0회 확인: {line[:120]}",
        }
        for line, command in rows
    ]
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
        ln = re.sub(r"^(?:[-•]\s*|\*\s+)", "", ln.strip(), count=1).strip()
        if not ln:
            continue
        m = _crit_delim().search(ln)
        if m:
            d, v = ln[:m.start()], ln[m.end():]
        else:
            d, _, v = ln.partition("|")
        import re as _re
        v = _re.sub(r"^(?:[\w가-힣]{0,4}\s*)?(?:실증|검증|측정)\s*[:：]\s*", "", v.strip())
        desc = d.strip()
        # 조건 전체를 강조한 ``**desc**``는 표시 장식이고, desc 안쪽의 ``**API**``는
        # canonical identity 일부일 수 있다. 바깥 한 쌍만 정확히 벗긴다.
        if len(desc) >= 4 and desc.startswith("**") and desc.endswith("**"):
            desc = desc[2:-2].strip()
        out.append({"desc": desc, "verify": v.strip()})
    return out


def _goal_doc_section(text: str, heading: str) -> str:
    """GOAL.md의 한 섹션 전문. 알 수 없는 섹션도 읽되 다음 ``##``에서 정확히 멈춘다."""
    wanted = str(heading or "").strip().casefold()
    active = False
    out = []
    for line in str(text or "").splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if active:
                break
            active = m.group(1).strip().casefold() == wanted
            continue
        if active:
            out.append(line)
    return "\n".join(out).strip()


def _goal_doc(flow) -> str:
    try:
        from .._util import dossier_read
        return str(dossier_read(flow, "GOAL.md") or "")
    except Exception:
        return ""


def canonical_parent_contract(flow) -> str:
    """하위 회의에 공급할 상위 GOAL 정본 전문.

    새 문서는 표결에 붙었던 ``Ratified Decision``을 그대로 돌려준다. 구 문서·복구 상태는 고정
    섹션/TaskRef에서 무절단 합성한다. 호출자가 이 문자열을 자르지 않는 것이 계약이다.
    """
    doc = _goal_doc(flow)
    ratified = _goal_doc_section(doc, "Ratified Decision")
    if ratified:
        return ratified
    cur = getattr(flow, "current", None)
    status = getattr(cur, "status", None)
    goal = str(getattr(status, "goal", "") or "").strip()
    acceptance = str(getattr(cur, "acceptance", "") or "").strip()
    interfaces = str(getattr(cur, "interfaces", "") or "").strip()
    goal = goal or _goal_doc_section(doc, "Goal")
    acceptance = acceptance or _goal_doc_section(doc, "Acceptance")
    interfaces = interfaces or _goal_doc_section(doc, "Interfaces")
    parts = []
    if goal:
        parts.append(f"목표: {goal}")
    if interfaces:
        parts.append(f"인터페이스:\n{interfaces}")
    if acceptance:
        parts.append(f"완수조건:\n{acceptance}")
    return "\n".join(parts).strip()


def _goal_acceptance_entries(flow):
    """GOAL 정본의 실증 조건을 중복 없이 상위 잠금 참조로 복제한다."""
    doc = _goal_doc(flow)
    source = _goal_doc_section(doc, "Ratified Decision")
    cur = getattr(flow, "current", None)
    if not source:
        source = (str(getattr(cur, "acceptance", "") or "").strip()
                  or _goal_doc_section(doc, "Acceptance"))
    rows = []
    for line in str(source or "").splitlines():
        clean = re.sub(
            r"^(?:[-•]\s*|\*\s+)", "", line.strip(), count=1).strip()
        if not clean:
            continue
        if _crit_delim().search(clean):
            rows.extend(parse_criteria_lines(clean))
            continue
        # 2026-07-25 이전 GOAL.md가 쓰던 ``- 조건 (실증: 절차)``도 복구한다.
        old = re.match(r"^(.*?)\s*\((?:실증|검증|측정)\s*[:：]\s*(.*?)\)\s*$", clean)
        if old:
            old_desc = old.group(1).strip()
            if (len(old_desc) >= 4 and old_desc.startswith("**")
                    and old_desc.endswith("**")):
                old_desc = old_desc[2:-2].strip()
            rows.append({"desc": old_desc, "verify": old.group(2).strip()})
    out, seen = [], set()
    for row in rows:
        desc = str(row.get("desc") or "").strip()
        verify = str(row.get("verify") or "").strip()
        key = (desc, verify)
        if not desc or not verify or key in seen:
            continue
        seen.add(key)
        out.append({"desc": desc, "verify": verify})
    return out


_PUBLIC_CONTRACT_LINE = re.compile(
    r"^(?:(?:공개|외부)\s*)?(?:계약|인터페이스(?:\s*계약)?|API\s*계약)\s*[:：]"
    r"|^(?:public\s+)?(?:contract|interfaces?|api\s+contract)\s*[:：]",
    re.I,
)


def _public_contract_lines(text: str):
    """비준안에서 명시적으로 계약/인터페이스라 선언한 줄만 TaskRef에 옮긴다."""
    out = []
    for line in str(text or "").splitlines():
        clean = line.strip().lstrip("-•* ").strip()
        if clean and _PUBLIC_CONTRACT_LINE.match(clean) and clean not in out:
            out.append(clean)
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
                    f"그 주기의 분해라면 수렴안에 '단위: ⟦목표⟧ | ⟦실증절차⟧' 줄을 동봉하세요."), 0
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
    한 번씩만 확대(백로그당 1회 상한, 무한 wake 없음). 킥 키는 (SubTask, Bn) 쌍이다. B1은 각
    SubTask에서 반복되므로 Bn 단독 키면 첫 단계 B1이 뒤 모든 단계 B1까지 막는다."""
    mss = getattr(flow, "milestones", None) or []
    ms = next((m for m in mss if m.status not in ("done", "superseded")), None)
    if ms is None:
        return None
    store = getattr(flow, "backlog_relays", None) or {}
    kicked = getattr(flow, "_claim_kicked", None) or set()
    from .backlog import backlog_scope_key, blocked_ready_for_revisit
    rows = []
    for st in ms.subtasks:
        if st.status in ("done", "superseded"):
            continue
        r = store.get(st.st_id)
        if r is None or not r.backlogs:
            continue
        rows.extend((st, r, b) for b in r.backlogs)

    # 단일 활성은 SubTask 내부가 아니라 열린 마일스톤 전체의 규칙이다. 앞 ST에 open이 있고 뒤 ST에
    # in_progress가 있을 때 앞것을 또 집던 순회 순서 결함을 먼저 차단한다.
    if any(b.status == "in_progress" for _st, _r, b in rows):
        return None

    def _worker(st, b, *, revisit=False):
        owner = int((b.assignee if revisit else b.submitter) or b.submitter or 0)
        if owner:
            return owner
        # [무주 백로그 킥(2026-07-20, U-035 실측)] 회의 등록분은 이의 개서로 발제 귀속이 유실될 수
        # 있다(_draft_attr 키 드리프트 → submitter 0) — 무주면 킥이 침묵해 ch79형 '아무도 선점 안
        # 함' 공전이 재발한다. 적임(role_fit) 봇을 깨워 '적임자로서 선점 검토' — 배분 강제가 아니라
        # 첫 착수 신호(자기선택은 pick 시점에 유지).
        bots = {int(k): str(v or "") for k, v in (getattr(flow, "bot_info", None) or {}).items()}
        if bots:
            from ..role_fit import role_fit as _rf
            query = f"{getattr(st, 'goal', '')} {b.body}"
            return int(max(bots, key=lambda k: _rf(query, bots[k])))
        return 0

    # 실행 가능한 신규/보충 백로그가 blocked 원본보다 항상 먼저다.
    for st, _r, b in rows:
        if b.status != "open" or backlog_scope_key(st.st_id, b.backlog_id) in kicked:
            continue
        owner = _worker(st, b)
        if owner:
            return (owner, b, st.st_id)

    # blocked는 차단 직후 즉시 재선정하지 않는다. 모든 실행 가능분이 소진되고 차단 뒤 보충 작업의
    # 실제 완료가 남았을 때만 재방문한다. handoff가 응답 장애로 선택을 못 했을 때의 구조적 안전망이다.
    all_backlogs = [b for _st, _r, b in rows]
    for st, _r, b in rows:
        if blocked_ready_for_revisit(
                b, all_backlogs, backlog_scope_key(st.st_id, b.backlog_id)):
            owner = _worker(st, b, revisit=True)
            if owner:
                return (owner, b, st.st_id)
    return None


def meeting_stage(flow):
    """현 상태에서 이 회의가 정할 단 하나를 도출. 'goal'|'milestone'|'subtask'|'backlog'|None(작업 단계)."""
    _cur = getattr(flow, "current", None)
    if _cur is None:
        return None
    if not str(getattr(_cur.status, "goal", "") or "").strip():
        return "goal"                                   # ① Task 회의 — 무엇을 만들 것인가
    # [회의 하나에 결론 하나(2026-07-30, 사용자 지시)] 종전 goal 회의는 '무엇을 만들지'와 '무엇이
    # 되면 끝인지'를 한 번에 정했다 — 두 결정이 한 표결에 묶이면 먼저 쓴 사람의 골격이 통째로
    # 통과한다(U-079: 「별빛 회피」가 한 사람의 첫 편집으로 굳고 나머지는 조건만 덧붙임).
    # 만들 것을 정한 다음, 끝의 기준은 **따로 한 회의**로 정한다.
    _mss0 = getattr(flow, "milestones", None) or []
    if not str(getattr(_cur, "acceptance", "") or "").strip() and not _mss0:
        return "criteria"                               # ② 완수조건 회의 — 무엇이 되면 끝인가
    _mss = getattr(flow, "milestones", None) or []
    _open = next((m for m in _mss if m.status not in ("done", "superseded")), None)
    if _open is None:
        # ② 마일스톤 회의 — 열린 주기 없음. 단 로드맵이 소진됐으면 더 열 주기 없음(무한 마일스톤 회의
        # 방지). 첫 마일스톤(아직 아무것도 없음)이거나, 로드맵에 안 지은 단계가 남았을 때만 연다.
        # [phase 정규화(2026-07-20)] 한 줄 화살표 로드맵(구 판 복원분)도 phase 수로 바로 센다 —
        # 종전 len(raw)=1이면 2단계부터 회의가 영영 안 열렸다(이월 수신처 소멸과 같은 뿌리).
        _road = roadmap_phases(flow)
        _done_n = roadmap_done_count(flow)
        if not _mss or (_road and _done_n < len(_road)):
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
    _scoped_rows = [(st, b) for st in _alive if store.get(st.st_id) is not None
                    for b in (store.get(st.st_id).backlogs or [])]
    _rows = [b for _st, b in _scoped_rows]
    if any(b.status in ("open", "in_progress") for b in _rows):
        return None                                     # 지금 집을/진행 중 백로그 존재 → 작업 단계 우선
    if any(b.status == "blocked" for b in _rows):
        from .backlog import blocked_ready_for_revisit, backlog_scope_key
        if any(blocked_ready_for_revisit(
                b, _rows, backlog_scope_key(st.st_id, b.backlog_id))
               for st, b in _scoped_rows):
            return None                                 # 선행 보충 완료 증거가 생긴 원본 → 작업 단계에서 재개
        # 선행 필요로 멈춘 원 백로그만 남았다. 원본을 버리거나 완료 참칭하지 않고, 전 영역 보충 회의가
        # 선행 백로그를 추가하도록 연다. 선행 완료 뒤 blocked 원본을 재개해야 마일스톤이 닫힌다.
        return "backlog"
    _empty = [st for st in _alive
              if store.get(st.st_id) is None or not store.get(st.st_id).backlogs]
    if _empty:
        # [회의 하나에 목표 하나(2026-07-30, 사용자 지시)] 종전엔 한 회의가 미충원 영역 **전부**의
        # 일감을 한꺼번에 등록했다(실측: 한 회의에 26개). 그러면 참여자는 자기 영역 칸만 채우고
        # 표결은 그 묶음 전체에 대한 찬반이 된다 — "내 영역 하나 반영됐으니 찬성"이 구조적으로 나온다.
        # 영역 하나씩 연다: 그 회의의 결론은 '이 영역에서 할 일들' 하나다.
        try:
            flow._stage_target_st = _empty[0].st_id
        except Exception:
            pass
        return "backlog"                                # ④ 백로그 회의 — 이 영역의 일감
    # [백로그 소진 = 회의 트리거(2026-07-16, 잔재 감사 ①)] 전 단위의 백로그가 소진(전부 done/dropped)
    # 됐는데 주기가 아직 열려 있으면(조건 미충족) 추가 분해 회의 — 종전엔 handoff 코칭('meet를 열어라')
    # 만 있고 stage가 None이라, 봇이 meet를 불러도 결론 경로가 없었다(수렴 소진 낭비). 체인이 자동 개설.
    # [재분해에도 수렴 조건(2026-07-27, U-067 실측)] 종전엔 '조건 미충족 + 백로그 소진'만으로 무한
    # 재점화 — 같은 세 영역이 12세대 반복돼 단계가 36개까지 불었다. 앞 세대가 결과를 못 바꿨다는
    # 사실(iter_stuck)을 여기서 본다: 임계를 넘으면 또 쪼개지 말고 재협상 사다리로 흐른다.
    if (_open.status == "open"
            and int(getattr(_open, "iter_stuck", 0) or 0) < stuck_limit()
            and any(not c.passed and c.status != "waived" for c in _open.criteria)):
        return "subtask"
    return None                                          # 전 단계 완료 → 작업/검증 단계


# [통일 수렴안 + 구체 질문(2026-07-15, 사용자 가안)] 모든 회의의 산출물 = [수렴안](하나의 통일
# 메커니즘). 그 수렴안을 어떻게 가공할지는 각 단계(register_stage)의 몫. 핵심은 각 안건을 봇이 오해할
# 수 없는 **구체적 평문 질문(★)**으로 못박아, "작업 분배·일정·담당자 잡담"으로 도망치지 못하게 한다
# (라이브 ch71: 스코프 회의인데 봇들이 섹션 나누기·병렬화만 논의 — 회의 정체를 몰라서).
# [주기 = 써볼 수 있는 완성물(2026-07-27, 사용자: 'Task는 최대한으로, 마일스톤은 주기마다 산출
# 가능한 최소 단위로')] 종전 코칭은 '쪼개지 마세요'만 굵게 말해, 설계 정본("Milestone = Task를 **큰
# 주기로 나눈다**")과 반대로 주기가 1개로 수렴했다. 그러면 그 하나가 Task 전부를 증명해야 해서
# 마일스톤이 최대가 되고, 사용자는 끝날 때까지 아무것도 못 본다. 게다가 Task 경계 검사의 네 축 중
# '뒤 주기가 앞 주기를 깼는가'가 무의미해진다(주기가 하나라서).
# 금지해야 할 것은 **공정 쪼개기**(구현→검증: 두 번째는 혼자 못 쓴다)이지 사다리가 아니다.
_MILESTONE_COUNT_COACHING = (
    "**`단계:` 값에서 `→`로 나뉜 각 항목은 각각 별도 주기**이고, **각 항목은 그것만으로 사용자가 "
    "실제로 열어서 써볼 수 있는 완성물**이어야 합니다(달구지 → 자동차 → 스포츠카처럼 매번 타는 것이 "
    "나옵니다). 그 조건을 만족하면 **나눌수록 좋습니다** — 주기마다 사용자가 보고 방향을 고칠 수 "
    "있으니까요. 나누면 안 되는 것은 **한 산출물의 공정**입니다(`구현 → 검증`·`설계 → 개발`처럼 "
    "뒤 항목이 혼자서는 써볼 수 없는 것 — 그건 같은 주기의 백로그·완수조건으로 넣으세요). "
    "사용자 원문이 주기 수를 명시하면 그 수를 그대로 보존하세요."
)


_STAGE_META = {
    "goal": ("이 Task로 **무엇을 만들지**를 정한다",
             "[수렴안]\n목표: ⟦이 Task로 정확히 무엇을 만드는지 — '게임'이 아니라 '2인 턴제 카드 대전'처럼 구체적으로⟧\n[/수렴안]\n"
             # [요청을 좁히지 마라(2026-07-27, 사용자: 'Task 자체는 할 수 있는 최대한으로')] 종전엔
             # '구체적으로'만 요구해 열린 요청("게임 만들어줘")이 최소 산출물로 수렴했다(실측:
             # "1인용 단일 화면 미니게임 1종"). Task는 목적지고, 작게 가는 것은 마일스톤이 맡는다.
             "**Task는 이 요청으로 갈 수 있는 곳까지 잡습니다** — 원문이 열려 있으면 좁히지 말고 "
             "'무엇을 만들면 이 요청에 제대로 답한 것인가'로 잡으세요(작게 시작하는 것은 다음 회의의 "
             "주기가 맡습니다 — 목표를 줄이는 것이 아니라 **도달 순서를 나눕니다**).\n"
             # [나중에 돌아올 부채를 지금 알린다(2026-07-27, U-068 실측)] 여기 적은 완수조건은 그대로
             # GOAL 잠금이 되어 **마지막 주기에서 실행 가능한 명령 한 줄**로 실증해야 한다. 그 예고가
             # 없어 회의는 자연어 절차로 통과시키고, 계획 단계에서 비준 부채로 막혔다(2회 파킹).
             "**완수조건은 마지막에 `실행하면 통과/실패가 갈리는 명령 한 줄`로 증명해야 합니다** — "
             "지금은 절차로 적어도 되지만, '그 명령을 어떻게 만들지 그려지지 않는 조건'이면 지금 "
             "다시 쓰세요(예: 화면 조건이면 헤드리스 브라우저 스크립트 하나로 판정 가능한 형태로).\n"
             "★이 회의가 답할 질문 하나: **'이걸로 정확히 무엇을 만들고, 무엇이 되면 끝인가?'** "
             "— 작업을 어떻게 나눌지·누가 맡을지·일정은 **지금 논의하지 마세요**(그건 다음 회의들). "
             "지금은 '무엇을 만들지'만 정합니다. **목표를 ①→②→③ 절차로 쓰지 마세요** — 목표는 "
             "완성 실물 한 문장이고, 순서·절차는 마일스톤의 '단계:'와 백로그가 담습니다(절차형 목표는 "
             "등록이 거부됩니다)."),
    "criteria": ("**무엇이 되면 이 Task가 끝인가**를 정한다(만들 것은 이미 정해짐)",
             "[수렴안]\n조건: ⟦완수조건⟧ | 실증: ⟦실행할 exact command 또는 측정 가능한 검사⟧\n"
             "조건: ⟦완수조건⟧ | 실증: ⟦…⟧\n[/수렴안]\n"
             "★이 회의가 답할 질문 하나: **'무엇이 되면 끝인가?'** — 만들 것을 다시 정하거나 작업을 "
             "나누지 마세요. 각 조건은 마지막에 **실행하면 통과/실패가 갈리는 명령 한 줄**로 증명해야 "
             "합니다 — 그 명령이 그려지지 않는 조건이면 지금 다시 쓰세요.\n"),
    "milestone": ("이번에 **완성해서 사용자에게 보여줄 딱 하나**를 정한다(전체 말고 이번 것)",
             "[수렴안]\n단계: ⟦마일스톤 주기 목록 — 예: 혼자 써보는 최소판 → 여럿이 쓰는 확장판 → 남에게 내놓는 판⟧\n"
             "이번 주기: ⟦이번에 완성해 사용자가 실제로 써볼 수 있는 딱 하나⟧\n"
             "⟦완수조건 | 실증절차⟧\n[/수렴안]\n"
             "★이 회의가 답할 질문 하나: **'이번에 완성해서 사용자에게 보여줄 하나는 무엇인가?'** "
             "— 전체를 한 번에 만들려 하지 마세요(달구지부터). 작업 분해·담당자는 다음 회의. "
             "**완수조건은 '이번 주기' 범위만** — 뒤 단계 몫(모션 세부·디자인 토큰·폴리시 같은 완제품 "
             "사양)을 여기 넣으면 이번 주기가 영영 안 끝납니다(그건 그 단계 주기의 조건으로). "
             + _MILESTONE_COUNT_COACHING),
    # [회의 병합(2026-07-21, 사용자 결정: '2로 가자')] 작업나누기+백로그를 한 회의로 — 영역 분해와
    # 각 영역의 일감 열거를 같은 수렴안이 정한다(별도 백로그 회의 1개 제거, 계획 비용 -120~180cr).
    # 중간 소진 시 일괄 충전 회의(backlog 단계)는 존치.
    "subtask": ("이번에 만들 것을 **어떤 작업 영역들로 나눌지, 그리고 각 영역의 다음 일감 전부**를 정한다",
             "[수렴안]\n단위: ⟦작업 영역/구성요소 — 무슨 부분인지⟧\n"
             "단위: ⟦작업 영역/구성요소⟧\n"
             "백로그: [영역명] ⟦구체 작업 1⟧\n백로그: [영역명] ⟦구체 작업 …(각 영역 필요한 만큼)⟧\n[/수렴안]\n"
             "★이 회의가 답할 질문 둘: **'어떤 작업 영역(덩어리)들로 쪼개고, 각 영역의 일감은 무엇인가?'** "
             "— 영역은 **누가 맡느냐가 아니라 순수한 작업 분리**(예: 저장 계층 · 게임 로직 · 화면 UI, "
             "완수조건·실증은 붙이지 마세요 — 검증은 마일스톤에서), 일감(백로그:)은 항목마다 [영역명]을 "
             "달아 어느 영역 몫인지 명시하세요(별도 백로그 회의는 없습니다 — 처리는 각자 pick_backlog로 "
             "하나씩 선점). **일감은 '한 번에 끝나는 묶음' 단위로 굵게**(영역당 대략 3~7건), "
             "**협의·조율은 별도 항목이 아니라 그 백로그의 완료 조건**"
             "('○○와 합의 기록')으로 동봉하세요."),
    "backlog": ("미충원 작업 영역들의 **다음 일감 전부**를 한 번에 열거한다(처리는 하나씩 선점)",
             "[수렴안]\n백로그: [영역명] ⟦구체 작업 1⟧\n백로그: [영역명] ⟦구체 작업 2⟧\n백로그: [영역명] ⟦구체 작업 …(각 영역 완수에 필요한 만큼 — 줄 수 제한 없음)⟧\n[/수렴안]\n"
             "★이 회의가 답할 질문 하나: **'미충원 영역들을 완수 기준까지 끌고 가는 데 필요한 작업 항목 "
             "전부는 무엇인가?'** — 항목마다 [영역명]을 달아 어느 영역 몫인지 명시하고, 한두 개만 남기지 "
             "마세요(이 목록이 다음 iter의 연료 — 소진되면 점검 후에야 다음 회의). "
             # [일감 굵기 경제(2026-07-21, U-037 실측: 30건 잘게 쪼개기 — 건당 선점·핸드오프·보고
             # 오버헤드 5~10cr × 건수가 실작업비를 압도)] 내용 판단 아님 — 단위 경제의 형식 코칭.
             "**일감은 '실증 한 번으로 닫히는 묶음' 단위로 굵게** 잡으세요 — 파일 하나·함수 하나 "
             "수준으로 잘게 쪼개면 항목마다 선점·인계·보고 비용이 붙어 실작업보다 오버헤드가 커집니다"
             "(영역당 대략 3~7건이 보통 적당 — 상한이 아니라 경제 감각). "
             # [협의 파생 흡수(2026-07-21, U-039 실측: 34건 중 상당수가 '-협의·-수치·-수정' 꼬리 —
             # 굵기 절감을 협의 파생이 상쇄, 1963cr에 백로그 완주 1건)] 협의를 별도 항목으로 세우면
             # 선점·인계·표결 오버헤드만 남는다 — 협의는 그걸 필요로 하는 백로그의 완료 조건으로.
             "**협의·조율(스키마 맞추기·수치 합의·타 직군 확인)은 별도 백로그로 만들지 마세요** — "
             "그 협의가 필요한 백로그의 완료 조건(| 실증)에 '○○와 합의 기록'으로 동봉하세요. "
             "'-협의'·'-수치'·'-수정' 같은 꼬리 항목은 오버헤드만 남깁니다. 처리는 각자 "
             "pick_backlog로 하나씩 전담합니다."),
}


# [회의에는 이름이 있다(2026-07-30, 사용자 지적)] 종전엔 지시문 한 문장이 그대로 제목이 됐다
# ("이 Task로 무엇을 만들지와 무엇이 되면 끝인지를 정한다"). 피드에서 회의를 구분·검색·인용하려면
# 짧은 고유명이 필요하다 — 안건 설명은 본문이 들고, 제목은 이 표가 준다.
_STAGE_TITLE = {
    "goal": "Task 목표 정의",
    "criteria": "완수 기준 정의",
    "milestone": "이번 주기 정의",
    "subtask": "작업 영역 분해",
    "backlog": "일감 정의",
}


def stage_title(stage, scope="") -> str:
    """그 단계 회의의 제목. scope(영역명 등)가 있으면 뒤에 붙인다 — 같은 단계가 여러 번 열리므로."""
    base = _STAGE_TITLE.get(str(stage or ""), "회의")
    s = str(scope or "").strip()
    return f"{base} — {s[:40]}" if s else base


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
        "criteria": ("완수조건:\n- ⟦조건⟧ | 실증: ⟦실행할 exact command 또는 측정 가능한 검사⟧\n"
                     "- ⟦조건⟧ | 실증: ⟦…⟧\n"),
        "goal": ("목표: ⟦이 Task로 정확히 무엇을 만드는지 — 구체적으로⟧\n"
                 # [이 회의는 '무엇을 만들지'만 정한다(2026-07-30, 사용자 지적)] 골격에 완수조건 칸이
                 # 남아 있어, 단계를 쪼갠 뒤에도 봇들이 그 칸을 채우느라 이 회의에서 검증 명령·수치
                 # (150ms·exit 0·verify_ui.py)를 확정했다(U-436 실측). 끝의 기준은 다음 회의 몫이다.
                 # 여기서는 만들 것과, 그걸 만들 사람이 팀에 있는지만 본다.
                 "구성 점검: ⟦원문에 필요한 직군이 이 팀에 다 있는가 — 부족하면 그 직군과 recruit 계획, "
                 "충분하면 '충분' 판단 근거 한 줄⟧\n"),
        # [완수조건 = 이번 주기 범위(2026-07-20, U-035 rung1)] 최소버전 주기에 모션 타이밍·디자인
        # 토큰 등 완제품 전량이 조건으로 실려 met이 영구 미달(1/4 고정) → 재협상 dead-end로 빠지던
        # 상류 방아쇠 — 회의 골격이 스코프를 못박는다(뒤 단계 몫은 그 단계 주기의 조건).
        "milestone": ("단계: ⟦마일스톤 주기 목록 — 예: 혼자 써보는 최소판 → 여럿이 쓰는 확장판⟧\n"
                      "이번 주기: ⟦이번에 완성해 사용자에게 보여줄 딱 하나⟧\n\n완수조건:\n"
                      "(주의: **'이번 주기' 범위의 조건만** — 뒤 단계 몫(모션 세부·디자인 토큰 등 완제품 "
                      "사양)을 넣으면 이번 주기가 영영 안 끝납니다. 그건 그 단계 주기에서.)\n"
                      f"(주기 수 계약: {_MILESTONE_COUNT_COACHING})\n"
                      "(실증은 자연어 절차가 아니라 실제 실행할 **exact command**. 최종 주기에서는 자연어 "
                      "GOAL 조건을 SYS가 `GOAL@spec-hash` 정본 키로 붙입니다. 조건 문장을 다시 쓰지 말고 "
                      "각 정본 키의 `실증:` command만 비준하세요.)\n"
                      "- ⟦조건⟧ | 실증: ⟦exact command⟧\n"),
        "subtask": ("단위: ⟦작업 영역/구성요소⟧\n단위: ⟦작업 영역/구성요소⟧\n\n"
                    "백로그: [영역명] ⟦구체 작업 1⟧\n백로그: [영역명] ⟦구체 작업 2⟧\n"
                    "백로그: [영역명] ⟦구체 작업 …(각 영역 필요한 만큼)⟧\n"),
        # [선행 대기 칸을 골격에 둔다(2026-07-27, 전수감사)] 등록기는 blocked 원본마다
        # `[해결: ST::Bn]` 연결을 요구하는데 **봇이 편집하는 골격엔 그 칸이 없었다** — 골격만
        # 채우면 완성(빈칸 0·이의 0)으로 판정돼 표결까지 간 뒤 반드시 거부되는 구조였다.
        # 요구가 있는 곳에 칸도 있어야 한다(요구는 안건·프레임·거부문 세 곳에만 있었다).
        "backlog": ("백로그: [영역명] ⟦구체 작업 1⟧\n백로그: [영역명] ⟦구체 작업 2⟧\n"
                    "백로그: [영역명] ⟦구체 작업 …(필요한 만큼)⟧\n"
                    "(선행 대기(blocked) 원본이 있으면, 그걸 푸는 항목마다 끝에 `[해결: ST::Bn]`을 "
                    "달아 어느 원본을 여는지 연결하세요 — 안건에 목록이 실려 있습니다. 없으면 이 줄 무시.)\n"),
    }.get(stage)
    if not body:
        return None
    return (f"# DRAFT [stage:{stage}] — {agenda}\n"
            "(공동 결론 파일 — 규칙: ①자기 도메인 몫을 직접 편집해 채우고 구체화하세요 ②이견은 해당 줄 "
            "바로 아래 '> [이의 @직군] 한 줄'로 남기세요 ③이의를 해소한 사람이 그 이의 줄을 삭제하세요 "
            "④빈칸 표시 `⟦…⟧`가 남아 있으면 미완입니다 — 그 자리를 실제 값으로 바꾸세요(`⟦ ⟧`째 지우고 채움). "
            "**`⟦…⟧`는 오직 '지금 이 회의가 채울 곳' 표시**입니다. 참조·값·비교(예: `<500ms`, `<마일스톤 정의>`)는 "
            "그냥 평문으로 자유롭게 쓰세요 — `< >`·`[ ]`·`{ }`는 집계하지 않습니다(빈칸 아님). 뒤 단계에서 정할 "
            "세부는 `⟦ ⟧` 없이 '(후속: …)'로 쓰거나 참고 구획으로 — 결정 구획의 `⟦…⟧`만 기계 집계돼 회의가 안 "
            "닫힙니다. 단 **이 회의가 정할 그 하나(키 줄)를 통째로 '(후속: …)'로 미루면 빈칸과 같아 등록되지 "
            "않습니다.** 이 판에는 달력·날짜 스케줄러가 "
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


_STAGE_KEY = {"goal": "목표", "criteria": "조건", "milestone": "이번 주기",
              "subtask": "단위:", "backlog": "백로그:"}


def deferred_only(v):
    """[결정 없는 결정 칸(2026-07-21, U-038 실측)] 값이 '(후속: …)' 미룸 문구로 시작하면 결정이 아니라
    미룸 — 빈칸과 동형이다. 골격 규칙('지금 못 정하는 세부면 후속으로')은 세부에 쓰라는 것이지, 그
    회의가 정할 그 하나를 통째로 미루는 용도가 아니다. 내용 무판단 — 형태(미룸 전용)만 본다."""
    s = str(v or "").strip().lstrip("*").strip()
    return s.startswith("(후속") or s.startswith("후속:") or s.startswith("후속：")


def _goal_procedure_error(goal):
    """GOAL의 절차형 나열만 잡고 인라인 코드 안의 상태 전이는 보존한다.

    ``idle→working`` 같은 코드 계약은 무엇을 만들지 설명하는 도메인 값이지 작업 순서가 아니다.
    Markdown 인라인 코드 구간을 제외한 산문에 화살표 연쇄가 있거나 ①②③ 표식이 있을 때만 거부한다.
    """
    import re as _reg
    raw = str(goal or "")
    prose = _reg.sub(r"`[^`\n]+`", "", raw)
    if len(_reg.findall(r"→", prose)) < 2 and not _reg.search(r"[①②③④⑤]", prose):
        return None
    return ("'목표:'가 절차 나열(①②③·'→' 연쇄)입니다 — 목표는 순서가 아니라 **이 Task로 "
            "완성할 실물 한 문장**입니다(예: '2인 턴제 카드 대전 웹게임'). 절차·순서는 "
            "마일스톤의 '단계:'와 백로그가 담습니다.")


DRAFT_LANDED_MARK = "<!-- SYS:STAGE-LANDED -->"


def draft_should_reset(stage, existing) -> bool:
    """[흐름 재개 안전 불변식(2026-07-21, 사용자: '흐름 중엔 아무리 재시작해도 상관없다 — 재복구가
    있으니 안전하게 재개돼야')] 회의 개시가 DRAFT 골격을 새로 깔지(True), 진행분을 보존할지(False).
    같은 단계의 DRAFT가 이미 디스크에 있으면 **절대 리셋하지 않는다** — 러너 재시작(토큰·서버·사용자
    중지 등 어떤 이유든)으로 회의가 중단됐다가 재개돼도 봇들이 채워온 결론이 살아 있어야 한다.
    이 판정이 재시작-안전의 정본(회의 개시부·복구 경로가 공유). 새 단계이거나 초안 부재면 새 골격.

    [착지한 초안은 재사용 금지(2026-07-27, U-067 실측)] 등록에 성공한 초안이 파일에 그대로 남아,
    다음 같은-단계 회의가 그 완성본을 물려받아 **자리표시 0·이의 0으로 즉시 재가결**했다 —
    같은 단위 3개가 12세대 반복된 경로다. '중단된 초안'(표지 없음, 보존해야 함)과 '착지한
    초안'(표지 있음, 새로 시작해야 함)을 표지 한 줄로 가른다."""
    return (existing is None or f"[stage:{stage}]" not in str(existing)
            or DRAFT_LANDED_MARK in str(existing))


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
    ph = len(_re.findall(r"⟦[^⟧\n]{1,150}⟧", t))
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


def _unbold_draft_key(line):
    """파서 키의 Markdown 강조만 제거하고 조건 desc 안의 ``**`` identity는 보존한다."""
    value = str(line or "")
    labels = r"(?:목표|구성\s*점검|단계|이번\s*주기|단위|백로그)"
    value = re.sub(
        rf"^(\s*)\*\*({labels})\s*:\*\*",
        lambda m: f"{m.group(1)}{m.group(2)}:",
        value,
    )
    return re.sub(
        rf"^(\s*)\*\*({labels})\*\*\s*:",
        lambda m: f"{m.group(1)}{m.group(2)}:",
        value,
    )


def parse_units(lines):
    """'단위:' 항목 수집 — 등록·preflight 공용(같은 파싱 = 같은 판정).
    한 줄 정식(단위: ⟦목표⟧ | 실증: ⟦절차⟧)에 더해, 제목만 쓴 '단위:' 줄의 본문이 **바로 다음
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


# [주기는 공정이 아니라 탈 것이다(2026-07-30, 사용자 지적)] 규칙 문구는 "각 항목은 그것만으로
# 사용자가 써볼 수 있는 완성물(달구지 → 자동차 → 스포츠카)"이라고 말하는데 검사가 없어, 실측에서
# `로컬 기능 완성본 → 입력·뷰포트 완성본 → 시각 피드백 완성본 → 배포 완성본`이 그대로 통과했다
# (U-436). 그건 같은 달구지를 다듬는 공정이다 — 뒤 항목만으로는 사용자가 새로 써볼 것이 없다.
# 공정 어휘로만 이루어진 항목을 반려한다(내용 판단이 아니라 어휘 신호).
_PROCESS_WORDS = ("구현", "개발", "설계", "검증", "테스트", "QA", "리팩터", "리팩토링", "최적화",
                  "배포", "인프라", "세팅", "셋업", "환경", "빌드", "정리", "안정화", "버그",
                  "수정", "보완", "개선", "고도화", "입력", "뷰포트", "시각", "피드백", "연출",
                  "성능", "접근성", "문서", "기능")


_NARROW_RE = re.compile(r"(없음|없이|제외|미포함|하지\s*않는다|안\s*한다|최소한의|MVP|최소\s*버전|"
                        r"1차\s*범위|범위\s*축소|간단한\s*수준)", re.I)


def goal_narrowing_error(goal, origin="") -> str:
    """[Task는 갈 수 있는 곳까지(2026-07-30, 사용자 지적)] 프롬프트는 '요청을 좁히지 마라'고 말하는데
    검사가 없어, 열린 요청("게임 만들어줘")이 '화면 1개 · 로그인·서버·멀티플레이 없음'으로 등록됐다
    (U-436). 좁히는 것은 **주기(달구지→자동차→스포츠카)의 몫**이고 Task는 목적지를 잡는 자리다.

    원문에 없던 축소·제외 표현이 목표에 들어오면 반려한다(원문이 스스로 좁힌 경우는 존중).
    """
    g, o = str(goal or ""), str(origin or "")
    m = _NARROW_RE.search(g)
    if not m:
        return ""
    tok = m.group(1)
    if tok and tok in o:
        return ""                       # 사용자가 직접 그렇게 말했다면 그대로 따른다
    return (f"목표에 축소·제외 표현('{tok}')이 있습니다 — 원문에는 그런 제한이 없습니다. "
            f"**Task는 이 요청으로 갈 수 있는 곳까지** 잡고, 작게 시작하는 것은 다음 회의의 "
            f"주기가 맡습니다(달구지 → 자동차 → 스포츠카). 지금 못 만들 것 같아도 목적지로 "
            f"적으세요 — 이번에 어디까지 낼지는 주기가 정합니다.")


def roadmap_phase_is_process(phase) -> str:
    """이 주기 이름이 '한 산출물의 공정'으로만 읽히면 사유를 돌려준다(정상이면 빈 문자열)."""
    import re as _re
    t = str(phase or "").strip()
    if not t:
        return ""
    # '완성본·완성·버전' 같은 꼬리를 떼고 남는 알맹이가 공정 어휘뿐인가
    core = _re.sub(r"(완성본|완성|버전|단계|주기|판|본)\s*$", "", t).strip()
    if not core:
        return "이름이 '완성본'뿐이라 무엇을 써볼 수 있는지 알 수 없습니다."
    toks = [w for w in _re.split(r"[\s·,/]+", core) if w]
    if toks and all(any(pw in w for pw in _PROCESS_WORDS) for w in toks):
        return (f"'{t}'는 한 산출물의 공정입니다 — 그 주기만으로 사용자가 새로 써볼 것이 없습니다.")
    return ""


def roadmap_process_errors(phases) -> list:
    """로드맵 전체 검사 — 공정 항목이 있으면 그 목록과 고치는 법을 돌려준다.

    [나눴을 때만 따진다] 항목이 하나면 '공정으로 쪼갠 것'이 아니다 — 한 주기짜리 계획은 이름이
    무엇이든 그 자체가 이번에 낼 완성물이다. 검사는 둘 이상으로 나눈 로드맵에만 건다.
    """
    if len(phases or []) < 2:
        return []
    bad = [(p, why) for p in (phases or []) if (why := roadmap_phase_is_process(p))]
    if not bad:
        return []
    return [("주기 계획이 '공정 쪼개기'입니다 — " + " · ".join(w for _p, w in bad[:3])
             + " 각 주기는 **그것만으로 사용자가 열어서 써볼 수 있는 것**이어야 합니다"
               "(달구지 → 자동차 → 스포츠카). 다듬기·검증·배포는 그 주기의 백로그·완수조건으로 "
               "넣고, 주기는 '이번엔 사용자가 무엇을 새로 할 수 있게 되는가'로 나누세요.")]


def _proposal_roadmap_phases(lines):
    """마일스톤 수렴안의 ``단계:`` 값을 등록과 preflight가 똑같이 해석한다."""
    raw = [l.split(":", 1)[1].strip() for l in (lines or [])
           if l.strip().startswith("단계:") and ":" in l]
    return [phase.strip() for value in raw
            for phase in re.split(r"\s+→\s+", value) if phase.strip()]


_GOAL_RATIFY_START = "<!-- SYS:GOAL-RATIFICATION:START -->"
_GOAL_RATIFY_END = "<!-- SYS:GOAL-RATIFICATION:END -->"
_GOAL_MARKER_RE = re.compile(r"^GOAL@([0-9a-fA-F]{64})$")


def _goal_ratification_blocks(text):
    """SYS block 구간을 중첩 없이 해석한다. malformed delimiter는 내용을 건드리지 않고 오류."""
    raw = str(text or "")
    token_re = re.compile(
        re.escape(_GOAL_RATIFY_START) + "|" + re.escape(_GOAL_RATIFY_END))
    opened = None
    spans = []
    errors = []
    for match in token_re.finditer(raw):
        token = match.group(0)
        if token == _GOAL_RATIFY_START:
            if opened is not None:
                errors.append("SYS GOAL 비준 block의 START가 닫히기 전에 중복됐습니다.")
            else:
                opened = match.start()
        elif opened is None:
            errors.append("SYS GOAL 비준 block의 END에 대응하는 START가 없습니다.")
        else:
            spans.append((opened, match.end()))
            opened = None
    if opened is not None:
        errors.append("SYS GOAL 비준 block의 START에 대응하는 END가 없습니다.")
    if errors:
        return [], [], errors
    return [raw[start:end] for start, end in spans], spans, []


def _goal_ratification_marker(ref) -> str:
    """DRAFT에서만 쓰는 안정 키. 저장 Criterion identity는 계속 canonical desc/spec다."""
    return "GOAL@" + verifier_spec_hash(ref.desc, ref.verify)


def _natural_goal_refs(flow):
    """별도 final-meeting exact command 비준이 필요한 GOAL 조건만."""
    workspace = getattr(flow, "workspace", "") if flow is not None else ""
    return [
        ref for ref in _goal_locked_refs(flow)
        if not direct_verifier_command(
            ref.verify, workspace, require_existing=False)
    ]


def _unique_inline_verifier(spec, workspace="") -> str:
    """자연어 GOAL spec 안 Markdown code span 중 안전한 exact verifier가 딱 하나일 때만 추출.

    산문/substring을 명령으로 추측하지 않는다. 여러 서로 다른 명령·shell meta·실행형이 아닌 code는
    빈 값이라 회의가 marker placeholder에 명령을 명시해야 한다.
    """
    commands = []
    for code in re.findall(r"(?<!`)`([^`\n]+)`(?!`)", str(spec or "")):
        command = direct_verifier_command(
            code, workspace, require_existing=False)
        if command and command not in commands:
            commands.append(command)
    return commands[0] if len(commands) == 1 else ""


def _final_milestone_proposal(flow, proposed_phases=None) -> bool:
    phases = list(proposed_phases or roadmap_phases(flow))
    done_n = sum(
        1 for m in (getattr(flow, "milestones", None) or [])
        if getattr(m, "status", "") == "done"
    )
    return not phases or done_n + 1 >= len(phases)


def _goal_marker_not_final_note(flow, proposed_phases=None) -> str:
    """'최종 주기가 아니다' 반려를 **그들이 쓴 문장에서 기계가 읽은 값**으로 돌려준다.

    [구조 수리(2026-07-28, U-077 실측)] 종전 반려문은 규칙만 통보했다("최종 마일스톤에서만
    비준할 수 있습니다"). 팀은 초안에 '이번 주기 = 최종 마일스톤'이라고 **문장으로 선언**해 두고
    같은 초안을 10번 다시 냈다 — 선언으로는 구조가 바뀌지 않는데, 무엇이 어긋났는지 볼 방법이
    없었기 때문이다. 이제 SYS가 자기 판정의 근거(그들의 `단계:` 줄을 몇 개로 읽었는지, 지금이
    몇 번째인지)를 그대로 보여주고, **출구 두 개를 구조로 제시**한다.
    """
    phases = [str(p) for p in (proposed_phases or roadmap_phases(flow) or [])]
    done_n = sum(1 for m in (getattr(flow, "milestones", None) or [])
                 if getattr(m, "status", "") == "done")
    listed = " · ".join(f"{i + 1}) {p[:24]}" for i, p in enumerate(phases[:6])) or "(비어 있음)"
    return (
        "GOAL@ marker는 로드맵의 최종 주기에서만 비준할 수 있습니다. "
        f"이 초안의 `단계:` 줄을 SYS는 **{len(phases)}주기**로 읽었고({listed}), "
        f"이번은 그중 **{done_n + 1}번째**라 마지막이 아닙니다 — "
        "'최종 마일스톤'이라고 문장으로 적어도 주기 수는 `단계:` 줄이 정합니다. 출구는 둘입니다: "
        "①이번 주기 몫의 완수조건을 GOAL@ 없이 직접 쓰세요(사용자 수용 조건은 마지막 주기에서 비준합니다). "
        "②이 판을 한 주기로 끝낼 생각이면 `단계:` 줄을 항목 하나로 줄이세요 — 그러면 이번이 마지막이 되어 "
        "GOAL@ 비준이 열립니다."
    )


def _resolve_goal_ratification_entries(flow, entries, proposed_phases=None):
    """GOAL markers를 canonical desc 행으로 결정적으로 확장한다.

    marker는 DRAFT 편집 편의일 뿐 Milestone/receipt/checkpoint에는 남지 않는다. 따라서 기존
    ``(canonical desc, canonical GOAL verify)`` spec hash와 exact-desc one-to-one 계약은 그대로다.
    """
    rows = [dict(row) for row in (entries or [])]
    marked, ordinary = [], []
    for row in rows:
        desc = str(row.get("desc") or "").strip()
        match = _GOAL_MARKER_RE.fullmatch(desc)
        if match:
            marked.append((match.group(1).lower(), row, desc))
        elif desc.startswith("GOAL@"):
            # marker처럼 보이는 자유 문자열을 일반 criterion으로 흘려보내지 않는다. 특히 폐기된
            # 집합 shorthand나 잘린 hash가 canonical 조건인 것처럼 등록되는 우회를 fail-closed.
            marked.append(("", row, desc))
        else:
            ordinary.append(row)
    if not marked:
        return rows, []
    if flow is None:
        return ordinary, ["GOAL@ marker는 canonical GOAL 정본이 있는 실제 Task 회의에서만 쓸 수 있습니다."]
    if not _final_milestone_proposal(flow, proposed_phases):
        return ordinary, [_goal_marker_not_final_note(flow, proposed_phases)]

    refs = _natural_goal_refs(flow)
    by_key = {
        _goal_ratification_marker(ref).split("@", 1)[1]: ref
        for ref in refs
    }
    errors = []
    if not refs:
        errors.append("별도 exact command 비준이 필요한 자연어 GOAL 조건이 없어 GOAL@ marker를 쓸 수 없습니다.")
    seen = set()
    for key, _row, marker in marked:
        if not key:
            errors.append(
                f"유효하지 않은 GOAL marker `{marker[:80]}`입니다 — "
                "SYS가 붙인 full `GOAL@spec-hash` 키만 사용하세요."
            )
        elif key not in by_key:
            errors.append(f"알 수 없거나 낡은 GOAL@{key} 키입니다 — SYS 정본 블록의 키를 사용하세요.")
        elif key in seen:
            errors.append(f"GOAL@{key} 비준 행이 중복됐습니다.")
        seen.add(key)
    if errors:
        return ordinary, errors

    # 재개된 옛 DRAFT가 canonical desc를 직접 복사한 행을 이미 갖고 있어도 marker가 같은 ref를
    # 명시했다면 marker 쪽을 정본으로 삼는다. 파일 내용은 additive 보존하되 등록 입력에서만 정확한
    # desc 중복을 걷어, 복사 문자열의 identity에 더는 의존하지 않는다(부분/유사 일치는 절대 안 함).
    selected_desc = {by_key[key].desc.strip() for key, _row, _marker in marked}
    expanded = [
        row for row in ordinary
        if str(row.get("desc") or "").strip() not in selected_desc
    ]
    for key, row, _marker in marked:
        ref = by_key[key]
        expanded.append({"desc": ref.desc, "verify": row.get("verify")})
    return expanded, []


def _without_goal_ratification_block(text):
    """기존 SYS block 하나 이상을 제거하고 나머지 DRAFT를 보존."""
    raw = str(text or "")
    _blocks, spans, errors = _goal_ratification_blocks(raw)
    if errors:
        return raw
    out = raw
    for start, end in reversed(spans):
        out = out[:start].rstrip("\n") + "\n" + out[end:].lstrip("\n")
    return out


def ensure_goal_ratification_scaffold(flow, text):
    """채워진 milestone DRAFT가 최종주기로 확정된 뒤 canonical marker block을 additive 주입한다.

    첫 마일스톤 개시 때는 ``단계:``가 아직 빈칸이라 final 여부를 모른다. 그때 GOAL 조건을 일반
    criterion으로 미리 넣지 않고, 단계값이 채워진 뒤에만 이 함수가 block을 붙인다. 같은 stage의
    진행 DRAFT에도 additive라 재시작 보존 불변식과 공존한다.
    """
    raw = str(text or "")
    old_blocks, _old_spans, delimiter_errors = _goal_ratification_blocks(raw)
    if delimiter_errors:
        return raw
    old_block = "\n".join(old_blocks)
    base = _without_goal_ratification_block(raw)
    if flow is None or "[stage:milestone]" not in raw:
        return raw

    proposal = draft_to_proposal("milestone", base)
    lines = [_unbold_draft_key(line) for line in proposal.splitlines()]
    phase_values = [
        line.split(":", 1)[1].strip()
        for line in lines
        if line.strip().startswith("단계:") and ":" in line
    ]
    known_phases = roadmap_phases(flow)
    # 새 첫 회의의 단계 placeholder는 final/non-final을 아직 말하지 않는다. 복원된 후속 회의는
    # 이미 영속 roadmap이 있으므로 그 정본으로 판정할 수 있다.
    if any("⟦" in value or "⟧" in value for value in phase_values):
        if not known_phases:
            return raw
        proposed_phases = known_phases
    else:
        proposed_phases = _proposal_roadmap_phases(lines) or known_phases
    if not _final_milestone_proposal(flow, proposed_phases):
        # 명시적으로 후속 주기가 생긴 경우 final 전용 SYS scaffold는 분모 밖이므로 회수한다.
        # acceptance/flow 일시 부재와 달리 판정 가능한 정상 전이이며, 남기면 preflight가 영구 거부한다.
        return base

    refs = _natural_goal_refs(flow)
    if not refs:
        return raw
    workspace = getattr(flow, "workspace", "")

    def _entries(source):
        return parse_criteria_lines("\n".join(
            line for line in str(source or "").splitlines()
            if _crit_delim().search(line)
        ))

    # 참고 구획의 marker/legacy 문장은 결정이 아니므로 존재·command 상속 근거로 쓰지 않는다.
    outside_entries = _entries(draft_decision_region(base))
    old_marker_entries = [
        row for row in _entries(old_block)
        if str(row.get("desc") or "").strip().startswith("GOAL@")
    ]
    outside_markers = [
        row for row in outside_entries
        if _GOAL_MARKER_RE.fullmatch(str(row.get("desc") or "").strip())
    ]
    manual_by_desc = {}
    for row in outside_entries:
        desc = str(row.get("desc") or "").strip()
        if not _GOAL_MARKER_RE.fullmatch(desc):
            manual_by_desc.setdefault(desc, []).append(row)
    marker_rows = list(old_marker_entries)

    def _marker_key(row):
        match = _GOAL_MARKER_RE.fullmatch(
            str(row.get("desc") or "").strip())
        return match.group(1).lower() if match else ""

    marker_keys = {
        key for key in (
            _marker_key(row) for row in marker_rows + outside_markers
        ) if key
    }
    missing = [
        ref for ref in refs
        if _goal_ratification_marker(ref).split("@", 1)[1] not in marker_keys
    ]
    for ref in missing:
        # 구 판의 exact same-desc 행은 그 회의가 이미 고른 command이므로 1:1 marker로 안전하게
        # 이관한다. 그 외에는 **이 ref 자체**의 자연 spec 안 유일한 validated inline command만
        # prefill한다. 다른 GOAL 행의 command 공유·산문 추측·부분 desc 매칭은 없다.
        legacy = manual_by_desc.get(ref.desc.strip(), [])
        inherited = (
            direct_verifier_command(
                legacy[0].get("verify"), workspace, require_existing=False)
            if len(legacy) == 1 else ""
        )
        marker_rows.append({
            "desc": _goal_ratification_marker(ref),
            "verify": inherited or _unique_inline_verifier(
                ref.verify, workspace) or "⟦exact command⟧",
        })

    marker_lines = [
        f"- {row['desc']} | 실증: {row.get('verify') or '⟦exact command⟧'}"
        for row in marker_rows
    ]
    ref_lines = []
    for ref in refs:
        display = " ".join(str(ref.desc or "").split()).replace("⟦", "[").replace("⟧", "]")
        ref_lines.append(f"  - {_goal_ratification_marker(ref)} — {display}")
    block = "\n".join([
        _GOAL_RATIFY_START,
        "(SYS 정본 GOAL 비준: 아래 각 full `GOAL@spec-hash` 행은 정확히 한 canonical 조건에 "
        "1:1 결속됩니다. marker 오른쪽 `실증:` 명령만 회의가 선택하며, 조건 desc는 SYS가 "
        "canonical 정본으로 복원합니다.)",
        *marker_lines,
        "(canonical marker reference — 표시 desc를 조건 identity로 파싱하지 않음)",
        *ref_lines,
        _GOAL_RATIFY_END,
    ])
    decision = re.search(r"^## 결정\s*$", base, re.M)
    ref_at = (
        base.find("\n## ", decision.end())
        if decision is not None else base.find("\n## 참고")
    )
    if ref_at >= 0:
        return base[:ref_at].rstrip("\n") + "\n\n" + block + "\n" + base[ref_at:]
    return base.rstrip("\n") + "\n\n" + block + "\n"


def _milestone_verifier_errors(flow, entries, proposed_phases=None):
    """마일스톤 exact verifier/최종 GOAL 비준 계약의 단일 판정 함수.

    ``stage_preflight``와 ``register_stage``가 이 함수를 함께 써서, 비싼 표결을 끝낸 뒤에야
    등록기가 같은 형식 결함을 발견하는 이중 게이트를 만들지 않는다.
    """
    workspace = getattr(flow, "workspace", "") if flow is not None else ""
    rows = list(entries or [])
    # [무엇이 거부됐는지 말한다(2026-07-27, U-068 실측)] 종전엔 미달 '조건'만 나열해, 봇들이 자기가
    # 써넣은 명령이 문제였다는 걸 못 알아채고 같은 명령을 다시 냈다(`npm run verify …` — 이 판엔
    # npm 프로젝트가 없다). 회의는 4패스를 태우고 무진전으로 컷됐다. 반려는 본 것을 말해야 한다.
    non_exact = [
        (str(row.get("desc") or ""), " ".join(str(row.get("verify") or "").split()))
        for row in rows
        if not direct_verifier_command(
            row.get("verify"), workspace, require_existing=False)
    ]
    errors = []
    if non_exact:
        errors.append(
            "마일스톤 완수조건은 회의가 비준한 exact executable verifier command여야 합니다 "
            "(자연어 절차·나중 QA의 임의 명령 제안은 release 증거가 될 수 없음). "
            "예: `pytest -q tests/test_feature.py`, `python3 verify_feature.py`, "
            "`curl -fsS https://host/health`. "
            # [화면 조건의 실증 경로 안내(2026-07-26, U-063 실측)] 눈으로 보는 조건(첫 화면 진입·
            # 스크롤 없음·요소 가시성·뷰포트별 배치)에서 봇들은 '실행 가능한 명령'을 못 떠올려
            # `http.server`(그냥 띄우기 — 아무것도 판정 안 함) 같은 비검증 명령으로 우회하다 계획
            # 단계에서 교착했다. 이 판에는 headless 브라우저가 이미 갖춰져 있다 — 설치·다운로드
            # 없이 스크립트 한 개를 만들어 판정시키면 되고, 그 스크립트는 지금 없어도 된다
            # (이번 주기 백로그로 만들면 된다 — 명령만 정확히 못 박으면 비준된다).
            "**화면으로 보는 조건**(진입 속도·스크롤 없음·요소 가시성·모바일/데스크톱 배치)은 "
            "headless 브라우저로 판정하세요 — 이 작업공간에는 playwright와 브라우저가 이미 설치돼 "
            "있습니다(설치·네트워크 불필요). 페이지를 열어 조건을 확인하고 통과면 exit 0, 실패면 "
            "non-zero로 끝나는 스크립트를 하나 만들어 그 실행 명령을 비준하세요"
            "(예: `python3 verify_ui.py`). 스크립트가 아직 없어도 명령만 정확하면 비준됩니다 — "
            "만드는 일은 이번 주기 백로그로 넣으세요. 서버를 띄우기만 하는 명령"
            "(`python3 -m http.server …`)은 아무것도 판정하지 않아 실증이 아닙니다.\n— 거부된 줄 "
            "(왼쪽=조건, 오른쪽=지금 적힌 명령) —\n"
            + "\n".join(
                f"· {d[:55]} ← 지금 적힌 명령: "
                + (f"`{v[:70]}` (이 작업공간에서 실행 가능한 검증 명령이 아닙니다)" if v
                   else "(비어 있음)")
                for d, v in non_exact[:6])
        )

    # flow 없는 독립 파서 테스트는 GOAL 정본을 알 수 없다. 실제 회의 경로는 반드시 flow를 넘겨
    # 아래 same-desc 비준까지 표결 전에 검사한다.
    if flow is None:
        return errors
    phases = list(proposed_phases or roadmap_phases(flow))
    done_n = sum(1 for m in (getattr(flow, "milestones", None) or [])
                 if getattr(m, "status", "") == "done")
    final_cycle = not phases or done_n + 1 >= len(phases)
    if final_cycle:
        by_desc = {str(row.get("desc") or "").strip(): row for row in rows}
        missing_ratification = [
            ref.desc for ref in _goal_locked_refs(flow)
            if not direct_verifier_command(
                ref.verify, workspace, require_existing=False)
            and not (
                ref.desc.strip() in by_desc
                and direct_verifier_command(
                    by_desc[ref.desc.strip()].get("verify"),
                    workspace, require_existing=False)
            )
        ]
        if missing_ratification:
            errors.append(
                "최종 마일스톤의 자연어 GOAL 조건은 SYS가 DRAFT에 붙인 `GOAL@spec-hash` "
                "1:1 행에서 exact command를 비준해야 합니다. 조건 desc를 다시 쓰지 마세요 — "
                "marker가 canonical 자연 spec에 command만 "
                "immutable ratification으로 결속합니다. 누락: "
                + " · ".join(x[:60] for x in missing_ratification[:6])
            )
    return errors


def stage_preflight(stage, text, flow=None):
    """[등록 사전 검사(2026-07-17, ch78 실측: 표결 가결 후 등록 거부 사이클 6~9분×N — 봇 비용 낭비)]
    register_stage와 **같은 파싱**으로 표결 전에 불량을 전부 찾는다(봇 비용 0, 상태 변경 없음).
    반환: 에러 목록(list[str]) — 비면 통과. 표결·등록의 이중 발견을 게이트 시점 단일 발견으로."""
    import re as _re
    prop = draft_to_proposal(stage, text)
    lines = [_unbold_draft_key(l) for l in str(prop or "").splitlines()]
    errs = []
    if stage == "milestone":
        errs.extend(_goal_ratification_blocks(text)[2])

    def _val(prefix):
        return next((l.split(":", 1)[1].strip() for l in lines
                     if l.strip().startswith(prefix) and ":" in l), "")
    if stage == "goal":
        _goal = _val("목표")
        if not _goal:
            errs.append("'목표: ⟦이 Task로 정확히 무엇을 만드는지⟧' 줄이 필요합니다(줄 시작, 장식 없이).")
        else:
            _proc_err = _goal_procedure_error(_goal)
            if _proc_err:
                errs.append(_proc_err)
            _narrow = goal_narrowing_error(
                _goal, str(getattr(flow, "origin_request", "") or "") if flow is not None else "")
            if _narrow:
                errs.append(_narrow)
    if stage == "milestone" and not (_val("이번 주기") or _val("목표")):
        errs.append("'이번 주기: ⟦이번에 보여줄 딱 하나⟧' 줄이 필요합니다(줄 시작, 장식 없이).")
    # [회의 하나에 결론 하나(2026-07-30, 사용자 지시) — 전수 정합] goal 회의는 '무엇을 만들지'만
    # 정한다. 종전엔 여기(사전검사)가 완수조건을 요구해, 골격·등록에서 조건을 뺀 뒤에도 표결 전
    # 관문이 계속 조건을 요구했다 — 회의가 영영 안 닫히는 잔재. 조건 검사는 criteria·milestone 몫.
    if stage in ("criteria", "milestone"):
        _ct = "\n".join(l for l in lines if _crit_delim().search(l)
                        and not l.strip().startswith(("단위:", "단계:", "백로그:")))
        _entries = parse_criteria_lines(_ct)
        _proposed_phases = _proposal_roadmap_phases(lines) if stage == "milestone" else []
        if stage == "criteria" and not _entries:
            errs.append("'조건: ⟦완수조건⟧ | 실증: ⟦실행할 명령 또는 측정 가능한 검사⟧' 줄이 "
                        "1개 이상 필요합니다 — 이 회의가 정할 하나입니다.")
        if stage == "milestone":
            _entries, _marker_errs = _resolve_goal_ratification_entries(
                flow, _entries, _proposed_phases)
            errs.extend(_marker_errs)
        _e = gate_criteria(_entries)
        if _e:
            errs.extend(ln for ln in _e.splitlines() if ln.strip())
        if stage == "milestone":
            errs.extend(_milestone_verifier_errors(
                flow, _entries, _proposed_phases))
            errs.extend(roadmap_process_errors(_proposed_phases))
    if stage == "subtask":
        _units = parse_units(lines)
        if not _units:
            errs.append("'단위: ⟦작업 영역⟧' 줄이 1개 이상 필요합니다.")
        # [서브태스크 게이트 제거(2026-07-22, 사용자: '서브태스크·백로그 검증은 비용만 크다')] 단위는
        # 순수 작업 영역 grouping — 완수조건(실증) 게이트를 요구하지 않는다(서브태스크 완수 = 백로그
        # 소진). 종전 단위별 gate_criteria 검사 폐지. 검증은 마일스톤 조건만.
        # [회의 병합(2026-07-21, 사용자 결정: '2로 가자')] 이 회의가 일감 열거까지 정한다 — 백로그 줄 필수.
        if not any(l.strip().startswith("백로그:") for l in lines):
            errs.append("'백로그: [영역명] ⟦구체 작업⟧' 줄이 1개 이상 필요합니다 — 영역 분해와 각 영역의 "
                        "일감 열거를 이 한 회의가 함께 정합니다(별도 백로그 회의 없음).")
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
        # [세대 간 중복도 반려(2026-07-27, U-067 실측)] 위 검사는 **한 수렴안 안**만 본다 — 앞 세대
        # 단위가 done이 되면 목록에서 빠지므로, 같은 영역 3개를 12세대 반복해도 중복 0으로 통과했다
        # (단계 36개). 이미 있는 단위와 겹치면 새로 쪼개지 말고 그 단위를 이어가야 한다.
        _open_p = next((m for m in (getattr(flow, "milestones", None) or [])
                        if m.status not in ("done", "superseded")), None) if flow else None
        for _st_p in (getattr(_open_p, "subtasks", None) or []):
            if getattr(_st_p, "status", "") == "superseded":
                continue
            _et = _atoks(str(getattr(_st_p, "goal", "") or ""))
            for _t_p, _u_p in _uts:
                if _et and _u_p and len(_et & _u_p) / max(min(len(_et), len(_u_p)), 1) >= 0.7:
                    errs.append(f"영역 중복 — '{_t_p}'는 이미 열린 단위 {_st_p.st_id}"
                                f"('{str(getattr(_st_p, 'goal', ''))[:24]}')와 같은 영역입니다. 같은 영역을 "
                                f"다시 쪼개면 앞 세대가 한 일이 장부에서 갈라집니다 — 그 단위의 백로그로 "
                                f"이어가거나, 이번 주기에 정말 새로운 영역만 단위로 여세요.")
                    break

    if stage == "backlog" and not any(l.strip().startswith("백로그:") for l in lines):
        errs.append("'백로그: ⟦구체 작업⟧' 줄이 1개 이상 필요합니다.")
    return errs


def clear_resolved_goal_ratification_objection(flow, text, stage="milestone"):
    """구 exact-desc 복사 요구 이의가 1:1 marker로 실제 해소됐을 때만 그 시스템 행을 제거.

    재개 DRAFT의 사람 이의·다른 형식 이의는 보존한다. marker가 낡았거나 명령 placeholder라면
    preflight의 GOAL marker 오류가 남으므로 구 이의도 유지해 미완 초안이 표결로 새지 않는다.
    """
    raw = str(text or "")
    candidate = re.sub(
        r"(?m)^>\s*\[이의 @형식\]\s*최종 마일스톤은 자연어 GOAL 조건과.*(?:\n|$)",
        "",
        raw,
    )
    if candidate == raw:
        return raw, False
    try:
        unresolved = any(
            "GOAL@" in str(error) or "자연어 GOAL 조건" in str(error)
            for error in stage_preflight(stage, candidate, flow)
        )
    except Exception:
        unresolved = True
    return (raw, False) if unresolved else (candidate, True)


def stage_context(flow, stage):
    """[안건 타깃 명시(2026-07-16, 정합 감사 A)] 이 단계 회의가 딛고 선 이전 결론을 안건에 못박는다 —
    특히 백로그 회의는 단위마다 열리는데 '어느 단위' 회의인지 없으면 논의와 등록(첫 미충원 단위)이
    어긋난다. 반환: ' [대상 …: …]' 접미 또는 ''."""
    try:
        _cur = getattr(flow, "current", None)
        if stage == "milestone" and _cur is not None:
            g = str(getattr(_cur.status, "goal", "") or "").strip()
            base = f" [확정된 GOAL: {g[:80]}]" if g else ""
            natural = [
                ref.desc for ref in _goal_locked_refs(flow)
                if not direct_verifier_command(
                    ref.verify, getattr(flow, "workspace", ""),
                    require_existing=False)
            ]
            if natural:
                phases = roadmap_phases(flow)
                done_n = sum(
                    1 for m in (getattr(flow, "milestones", None) or [])
                    if getattr(m, "status", "") == "done")
                final_now = not phases or done_n + 1 >= len(phases)
                when = "이번 최종 주기에서" if final_now else "최종 주기에서"
                base += (
                    f" [{when} SYS가 DRAFT에 canonical `GOAL@spec-hash` 키를 붙입니다. "
                    "조건 desc를 다시 쓰지 말고 각 1:1 marker의 exact command만 비준하세요: "
                    + " · ".join(
                        f"{_goal_ratification_marker(ref)}={ref.desc[:36]}"
                        for ref in _natural_goal_refs(flow)[:5]
                    ) + "]"
                )
            return base
        _open = next((m for m in (getattr(flow, "milestones", None) or [])
                      if m.status not in ("done", "superseded")), None)
        if stage == "subtask" and _open is not None:
            return f" [이번 주기: {_open.goal[:80]}]"
        if stage == "backlog" and _open is not None:
            store = getattr(flow, "backlog_relays", None) or {}
            from .backlog import blocked_supplement_targets
            _scoped = [
                (st, b)
                for st in _open.subtasks if st.status not in ("done", "superseded")
                for b in (getattr(store.get(st.st_id), "backlogs", None) or [])
            ]
            # [보충으로 풀 수 없는 것은 안건에 올리지 않는다(2026-07-29, 실측)] 접기 규칙을 릴레이
            # 관문에만 뒀더니, 판이 회의 루프에 들어가면 관문을 지나지 않아 규칙이 돌지 못했다 —
            # 같은 원본을 두고 보충 회의만 4번 열렸다. 안건을 만들기 직전에 먼저 접는다.
            try:
                from .backlog import drop_unresolvable_blocked as _dropU
                _folded = _dropU(flow)
                if _folded and getattr(flow, "log", None):
                    flow.log("backlog_unresolvable_folded", backlogs=list(_folded)[:8])
            except Exception:
                pass
            _blocked = [
                (f"{st.st_id}::{b.backlog_id}", b)
                for st, b in blocked_supplement_targets(_scoped)
            ]
            if _blocked:
                _waiting = " · ".join(
                    f"{scope}({(b.block_reason or b.body)[:36]})" for scope, b in _blocked[:7])
                # [보충으로 풀 수 없는 막힘은 그렇게 말한다(2026-07-29, 사용자: '막힘 안 풀렸어')]
                # e2e 항목(condition:N) 재실증은 Task 경계에서만 가능한데, 그걸 요구하는 원본에
                # 보충을 계속 붙이면 백로그만 늘고 막힘은 그대로다(U-079 4세대: 보충 8건, 원본 여전히
                # blocked). 그 원본은 이번 주기에 어떤 보충으로도 풀리지 않는다 — 사실을 안건에 적는다.
                import re as _re_b
                _unfixable = [
                    scope for scope, b in _blocked
                    if _re_b.search(r"(condition\s*:\s*\d+|\be2e\b)",
                                   str(b.block_reason or b.body or ""), _re_b.I)
                    and _re_b.search(r"(receipt|target|challenge|봉인)",
                                    str(b.block_reason or b.body or ""), _re_b.I)
                ]
                _note = ""
                if _unfixable:
                    _note = (f" ※ {' · '.join(_unfixable[:3])}은(는) e2e 장부 재실증을 요구합니다 — "
                             f"e2e는 **Task 경계에서만** 열리므로 이번 주기엔 어떤 보충으로도 풀 수 "
                             f"없습니다. 원인 수정 작업으로 다시 쓰거나(원본을 drop_backlog) 두고, "
                             f"재실증은 주기 종료 후 자동으로 열리는 e2e에 맡기세요.")
                return (f" [선행 대기 원본: {_waiting} — 새 항목 본문 앞에 각 대상의 "
                        f"`[해결: ST::Bn]`을 붙이세요{_note}]")
            _es = [st for st in _open.subtasks if st.status not in ("done", "superseded")
                   and (store.get(st.st_id) is None or not store.get(st.st_id).backlogs)]
            if _es:
                _names = " · ".join(str(st.goal or "").split(" — ")[0].split(" | ")[0][:24] for st in _es[:7])
                _tgt = str(getattr(flow, "_stage_target_st", "") or "")
                _one = next((x for x in _es if x.st_id == _tgt), None) or _es[0]
                _lbl = str(_one.goal or "").split(" — ")[0].split(" | ")[0][:40]
                return f" [이 회의가 채울 영역: {_lbl}]"
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
    "criteria": "지금은 **무엇이 되면 이 Task가 끝인가**만 정하는 단계입니다 — 만들 것은 이미 "
            "정해졌습니다(위 목표). 그 목표가 달성됐음을 **어떻게 실제로 확인하는가**에만 답하세요. "
            "조건마다 `조건 | 실증: 실행할 exact command 또는 측정 가능한 검사`를 씁니다. "
            "무엇을 만들지 다시 정하거나, 작업을 나누거나, 담당을 정하지 마세요(그건 앞뒤 회의입니다).",
    "milestone": "지금은 **이번에 완성해서 사용자에게 보여줄 딱 하나**를 정하는 단계입니다. 전체를 한 번에 "
            "만들려 하지 말고(달구지부터), **'이번에 완성해 보여줄 하나는?'** 에만 답하세요. 작업 분해·"
            "담당자·일정은 다음 회의. 모든 완수조건의 `실증:`에는 실제 실행할 **exact command**를 "
            "쓰세요. 최종 주기라면 SYS가 붙인 각 `GOAL@spec-hash` 행에서 조건 문장은 건드리지 말고 "
            "`실증:`의 exact command만 비준하세요. " + _MILESTONE_COUNT_COACHING,
    "subtask": "지금은 이번 것을 **어떤 작업 영역(덩어리)으로 나눌지 + 각 영역의 다음 일감 전부**를 정하는 "
            "단계입니다(한 회의 — 별도 백로그 회의 없음). 영역은 개인 배정이 아니라 순수 작업 분리(예: 저장 "
            "계층·게임 로직·화면 UI), 일감은 '백로그: [영역명] ⟦작업⟧' 줄로 열거하세요. 담당은 회의가 아니라 "
            "각자 pick_backlog 선점.",
    "backlog": "지금은 **안건에 적힌 그 영역 하나**의 일감을 정하는 단계입니다(다른 영역은 각자의 "
            "회의에서 정합니다). **'이 영역을 완수하려면 무슨 작업들이 필요한가?'** 에만 답하세요 — "
            "한두 개로 끝내지 말고 목록을 채우세요(처리는 나중에 하나씩 선점). 안건에 "
            "각 항목에 **`[쓰기: 경로]`로 그 일이 고칠 파일·폴더를 선언**하세요 — 영역이 겹치지 않는 "
            "일감끼리는 동시에 진행됩니다(선언이 없으면 하나씩 순서대로). "
            "`선행 대기 원본`이 있으면 각 새 항목은 `[영역명] [해결: ST::Bn] 실제 작업` 형식으로 "
            "어느 blocked 원본의 선행인지 명시하고 모든 원본을 빠짐없이 덮어야 합니다.",
}


def stage_frame(stage):
    """매 토론 발언 턴에 주입할 스테이지 프레임(이 회의의 정체 + 작업분배 금지 가드) — 없으면 ''."""
    return _STAGE_FRAME.get(stage, "")


def _write_goal_md(flow, cur, goal, decision=""):
    try:
        from .._util import dossier_write
        dossier_write(flow, "GOAL.md", (
            f"# GOAL — Task {cur.task_id}\n\n"
            # 복구 파서는 Purpose부터 읽는다. 이 새 섹션을 앞에 두면 구 파서도 기존 다섯 섹션을
            # 오염 없이 읽고, 새 파이프라인은 표결 원문을 손실·재서술 없이 정본으로 쓸 수 있다.
            f"## Ratified Decision\n{(decision or '').strip()}\n\n"
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
    # [자리표시 가드(2026-07-16, 정합 감사 C)] 봇이 템플릿을 에코하면 '⟦…⟧' 자리표시 그대로 제출됨 —
    # 실값 없는 껍데기 등록 차단(비준 낭비 전에 여기서도 방어). 표식은 봇이 참조·값으로 안 쓰는 ⟦ ⟧(2026-07-22).
    if _re.search(r"⟦[^⟧\n]{1,150}⟧", str(prop or "")):
        return False, "수렴안에 템플릿 빈칸(⟦…⟧)이 남아 있습니다 — 실제 값으로 채워 다시 제출하세요."
    # [마크다운 키 장식 무력화] '**이번 주기:**' 같은 키만 벗긴다. 조건 desc 안의 **는 GOAL
    # canonical identity/hash 일부일 수 있으므로 전역 제거하지 않는다.
    lines = [_unbold_draft_key(l) for l in str(prop or "").splitlines()]

    def _val(prefix):
        return next((l.split(":", 1)[1].strip() for l in lines
                     if l.strip().startswith(prefix) and ":" in l), "")
    # [조건 선별 = 라벨 구분자(2026-07-17)] 맨 '|' 포함 줄 전부가 아니라 '| 실증:'류 라벨이 있는 줄만
    # 조건이다 — 파이프 든 스펙 산문(JSON enum 등)이 조건 게이트로 쓸려 들어와 등록을 막던 것 차단.
    _crit_txt = "\n".join(l for l in lines if _crit_delim().search(l)
                          and not l.strip().startswith(("단위:", "단계:", "백로그:")))
    _cur = getattr(flow, "current", None)

    if stage == "criteria":
        # [무엇이 되면 끝인가 — 이 회의의 결론 하나(2026-07-30, 사용자 지시)] 목표는 앞 회의가 정했다.
        # 여기서는 그 목표의 달성을 **무엇으로 확인하는가**만 등록한다(Task 완수조건 = GOAL 잠금).
        if _cur is None:
            return False, "열린 Task가 없습니다."
        _crits = parse_criteria_lines(_crit_txt)
        if not _crits:
            return False, ("수렴안에 완수조건이 없습니다 — 최소 1개를 "
                           "'조건 | 실증: 실행 명령 또는 측정 가능한 검사'로 제출하세요.")
        _err = gate_criteria(_crits)
        if _err:
            return False, _err
        try:
            _cur.acceptance = "\n".join(f"- {c['desc']} | 실증: {c['verify']}" for c in _crits)
        except Exception:
            return False, "완수조건을 장부에 기록하지 못했습니다."
        try:
            _goal_doc(flow)
        except Exception:
            pass
        return True, f"완수조건 {len(_crits)}개 등록 — 이제 이번 주기(마일스톤)를 정합니다."

    if stage == "goal":
        # [통일 수렴안(가안)] prop = [수렴안]의 '목표:'+조건 줄들. goal 단계는 이 수렴안을 가공해
        # Task GOAL을 세팅하고 GOAL.md를 쓴다(수렴안=통일 산출물, 가공은 이 단계의 몫).
        goal = _val("목표")
        if not goal:
            return False, "수렴안에 '목표: ⟦이 Task로 정확히 무엇을 만드는지⟧' 줄이 필요합니다."
        # [결정 없는 결정 칸 거부(2026-07-21, U-038 실측: 목표='(후속: 기획 단계에서 확정 — 담당·
        # 날짜)'가 부결 2회에도 소진-확정으로 등록 → GOAL이 빈 채 판이 굴러감)] 미룸 전용 값은 빈칸과
        # 동형 — 종결 보장(부결 소진·이월 확정)이 '결정 없는 결론'을 밀 수 없게 등록이 최종 방어선.
        if deferred_only(goal):
            return False, ("'목표:'가 후속 미룸 문구뿐입니다 — 이 회의가 정할 그 하나(무엇을 만드는지)는 "
                           "여기서 결정해야 합니다. 미룸(후속)은 세부에만 쓰세요. 결정에 필요한 직군이 "
                           "팀에 없으면 recruit로 충원하거나(후보 대기 우선), 정말 결정 불가면 그대로 "
                           "말하세요 — 사람 확인으로 넘어갑니다.")
        # [목표 = 절차 아님(2026-07-21, U-040 실측: '①컨셉 정의 → ②페이퍼 검증 → ③호흡 정량 정의'가
        # 목표로 등록 — 봇들이 2:2로 두 번 막았는데 소진 확정이 밀어붙임, 사용자: '누가봐도 이상')]
        # 절차 표기(①②③ 나열 · '→' 연쇄)가 목표 값에 오면 반려 — 목표는 '무엇을 만드는가' 한 문장,
        # 절차·순서는 로드맵('단계:')과 백로그의 몫. 내용 무판단(절차 표기라는 형태 신호만).
        _proc_err = _goal_procedure_error(goal)
        if _proc_err:
            return False, _proc_err
        if _cur is not None:
            # [회의 하나에 결론 하나(2026-07-30, 사용자 지시)] 완수조건은 다음 회의(criteria)가 정한다.
            # 여기서 함께 요구하면 두 결정이 한 표결에 묶여, 먼저 쓴 사람의 골격이 통째로 통과한다.
            # 같은 수렴안에 조건이 딸려 왔으면 버리지 않고 받아 둔다(형식이 맞을 때만).
            _crits = parse_criteria_lines(_crit_txt)
            if _crits:
                _goal_criteria_error = gate_criteria(_crits)
                if _goal_criteria_error:
                    return False, _goal_criteria_error
            try:
                _cur.status.goal = goal
            except Exception:
                pass
            if _crits:
                try:
                    # parse_criteria_lines의 반환은 dict다. 종전 속성 접근 예외가 조용히 삼켜져
                    # acceptance가 빈 채 남던 결함을 닫고, 동일 파서로 다시 읽히는 정본 문법으로 저장한다.
                    _cur.acceptance = "\n".join(
                        f"- {c['desc']} | 실증: {c['verify']}" for c in _crits)
                except Exception:
                    pass
            _ifaces = _public_contract_lines(prop)
            if _ifaces:
                _prev = [x for x in str(getattr(_cur, "interfaces", "") or "").splitlines() if x.strip()]
                for _line in _ifaces:
                    if _line not in _prev:
                        _prev.append(_line)
                _cur.interfaces = "\n".join(_prev)
            _write_goal_md(flow, _cur, goal, decision=prop)  # 비준안 전문 + 복구 파서 계약 헤더
            _ckpt(flow)   # GOAL 확정도 다른 파이프라인 전이처럼 즉시 크래시-세이프
        if flow.log:
            flow.log("task_goal_set", goal=goal[:60])
        return True, (f"[표결 확정] GOAL 확정 → {goal[:80]} · GOAL.md 작성. "
                      "다음: 마일스톤 회의를 시스템이 엽니다.")

    if stage == "milestone":
        delimiter_errors = _goal_ratification_blocks(prop)[2]
        if delimiter_errors:
            return False, "\n".join(delimiter_errors)
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
        # [phase 정규화(2026-07-20)] preflight와 동일한 파서로 한 줄 화살표를 phase 목록으로.
        stages = _proposal_roadmap_phases(lines)
        _road = ""
        milestone_entries = parse_criteria_lines(_crit_txt)
        milestone_entries, marker_errors = _resolve_goal_ratification_entries(
            flow, milestone_entries, stages)
        if marker_errors:
            return False, "\n".join(marker_errors)
        verifier_errors = _milestone_verifier_errors(flow, milestone_entries, stages)
        if verifier_errors:
            return False, "\n".join(verifier_errors)
        if stages:
            # 거부될 수 있는 검사를 전부 통과한 뒤에만 로드맵을 상태에 반영한다. 종전에는 verifier
            # 거부인데도 roadmap만 먼저 남아 다음 preflight의 최종주기 판정을 바꾸는 부분 착지가 있었다.
            flow.roadmap = stages
            _k = sum(1 for m in (getattr(flow, "milestones", None) or [])
                     if getattr(m, "status", "") == "done") + 1
            _road = ("\n계획: " + " → ".join(s[:40] for s in stages[:8])
                     + f" (이번 주기 = {_k}단계)")
        ms = open_milestone(flow, cyc or "이번 주기", milestone_entries,
                            origin=f"마일스톤 회의: {origin[:50]}")
        if isinstance(ms, str):
            return False, ms
        if flow.log:
            flow.log("ms_by_meeting", ms=ms.ms_id)
        return True, (f"[표결 확정] 마일스톤 {ms.ms_id} 등록(조건 {len(ms.criteria)}개).{_road}\n"
                      "다음: 작업 나누기 회의(영역 분해 + 일감 열거 — 한 회의)를 시스템이 엽니다.")

    if stage == "subtask":
        _open = next((m for m in (getattr(flow, "milestones", None) or [])
                      if m.status not in ("done", "superseded")), None)
        if _open is None:
            return False, "열린 마일스톤이 없습니다 — 마일스톤 회의가 먼저입니다."
        units = parse_units(lines)
        if not units:
            return False, ("수렴안에 '단위: ⟦작업 영역⟧' 줄이 1개 이상 필요합니다"
                        " — 단위는 순수 작업 영역 묶음이라 완수조건·실증을 붙이지 않습니다"
                        "(검증은 마일스톤 조건이 맡습니다).")
        # [거부 사유 은닉 봉합(2026-07-20, U-035 실측: 가결→등록 0건→'단위: 줄을 확인' 오진 → 봇이
        # 멀쩡한 단위 줄만 재확인·재가결하는 무한 사이클×2회의)] open_subtask(gate_criteria)의 단위별
        # 거부 사유를 버리지 않고 그대로 돌려준다 — 고칠 수 있는 진단만이 사이클을 끝낸다.
        _before_st_n = len(_open.subtasks)
        _before_relay_ids = set((getattr(flow, "backlog_relays", None) or {}).keys())
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
        if n == 0:
            return False, f"등록 거부 — 단위 {len(units)}건 전부 조건 게이트 불통과:\n{_etxt}"
        # [회의 병합(2026-07-21, 사용자 결정: '2로 가자')] 같은 수렴안의 '백로그:' 줄들을 방금 연
        # 단위들에 바로 분배 등록(backlog 분기 재귀 — 파싱·배분·발제 귀속 로직 재사용). 별도 백로그
        # 회의 1개 제거(계획 -120~180cr). 백로그 줄이 없거나 전부 불량이면 종전 충전 회의
        # (meeting_stage='backlog')가 뒤를 받친다 — 우아한 퇴행.
        _bl_note = ""
        if any(l.strip().startswith("백로그:") for l in lines):
            _ok_b, _note_b = register_stage(flow, "backlog", prop, origin)
            if _ok_b:
                _bl_note = " + " + str(_note_b).replace("[표결 확정] ", "").strip()
            else:
                # 병합 회의는 영역+일감이 한 결정이다. 뒤 절반의 직군 커버리지/등록 게이트가 거부하면
                # 앞 절반만 남기는 부분 커밋을 하지 않는다.
                del _open.subtasks[_before_st_n:]
                _store_b = getattr(flow, "backlog_relays", None) or {}
                for _rid_b in list(_store_b):
                    if _rid_b not in _before_relay_ids:
                        _store_b.pop(_rid_b, None)
                return False, str(_note_b)
        return True, (f"[표결 확정] 작업 영역 {n}개 등록."
                      + (f" (미등록 {len(_errs)}건 — 사유: {_etxt})" if _errs else "")
                      + (_bl_note if _bl_note
                         else " (일감 줄이 없어 백로그 충전 회의를 시스템이 엽니다.)"))

    if stage == "backlog":
        _open = next((m for m in (getattr(flow, "milestones", None) or [])
                      if m.status not in ("done", "superseded")), None)
        if _open is None:
            return False, "열린 마일스톤이 없습니다."
        from .backlog import (
            relay_for, DuplicateBacklog, BacklogError, backlog_scope_key,
            blocked_supplement_targets,
        )
        store = getattr(flow, "backlog_relays", None) or {}
        _alive_sts = [st for st in _open.subtasks if st.status not in ("done", "superseded")]
        # blocked 때문에 열린 보충 회의라면 이번 회의에서 태어나는 항목에 해결 대상 scope를 영속한다.
        # 생성 시각만으로는 작업 중 임의 등재된 무관 백로그와 구분할 수 없고, B번호만으론 ST가 겹친다.
        _scoped_existing = [
            (st, b)
            for st in _alive_sts
            for b in (getattr(store.get(st.st_id), "backlogs", None) or [])
        ]
        _blocked_scopes = [
            backlog_scope_key(st.st_id, b.backlog_id)
            for st, b in blocked_supplement_targets(_scoped_existing)
        ]
        _empty_sts = [st for st in _alive_sts
                      if store.get(st.st_id) is None or not store.get(st.st_id).backlogs]
        if not _alive_sts:
            return False, "백로그를 채울 서브태스크가 없습니다."
        items = [l.split(":", 1)[1].strip() for l in lines if l.strip().startswith("백로그:")]
        if not items:
            return False, "수렴안에 '백로그: ⟦작업 단위⟧' 줄이 필요합니다."

        # [iter 일괄 충전(2026-07-20, 사용자: '다음 회의로 백로그 여러개 다수 한번에')] 한 회의가 한
        # 영역이 아니라 미충원 영역들 몫을 함께 등록한다 — '백로그: [영역명] 항목'의 [영역명]으로 배분
        # (토큰 겹침 최고 영역), 접두 없으면 첫 미충원 영역(하위호환).
        import re as _re3

        def _dest_of(_it):
            # [이 회의는 한 영역만 채운다(2026-07-30, 사용자 지시)] 회의가 영역 하나로 열렸으면
            # 그 회의의 일감은 전부 그 영역 몫이다 — 라벨 어휘를 단위 제목과 대조해 배정하던
            # 방식(겹침 34% 임계·안 맞으면 빈 영역으로 폴백)이 통째로 필요 없어진다.
            # 실측에서 그 매칭이 어긋나 엉뚱한 영역으로 간 사례가 backlog_dest_fallback으로 남았다.
            _tgt0 = str(getattr(flow, "_stage_target_st", "") or "") if flow is not None else ""
            if _tgt0:
                _hit0 = next((x for x in _alive_sts if x.st_id == _tgt0), None)
                if _hit0 is not None:
                    _m0 = _re3.match(r"^\[([^\]]{1,40})\]\s*(.*)$", _it)
                    _body0 = (_m0.group(2) if _m0 else _it).strip()
                    if _body0:
                        return _hit0, _body0
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
                # [라벨이 안 맞으면 빈 영역부터(2026-07-27, 전수감사)] 겹침이 임계 미만이면 종전엔
                # 말없이 **첫 단위**로 몰아넣어, 라벨 어휘가 단위 제목과 다르면 전부 한 곳에 쌓이고
                # 나머지 영역이 굶었다(주석이 스스로 'ST-3/6/7 백로그 0의 근본'이라 기록한 증상).
                # 어디로 갔는지 남기고, 아직 빈 영역이 있으면 그쪽을 먼저 채운다.
                if flow is not None and getattr(flow, "log", None):
                    flow.log("backlog_dest_fallback", label=str(m3.group(1))[:24],
                             best=round(best, 2), empty_left=len(_empty_sts))
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
        # [폴백은 이 판의 팀 안에서(2026-07-27, U-067 실측)] 종전엔 **전사 로스터**에서 골라, 이 판에
        # 없는 사람이 주인이 되거나 늘 같은 사람이 뽑혔다(같은 함수 근처 _team_o는 이미 팀으로 좁혀져
        # 있어 내부 비일관이었다). 게다가 적합도가 전원 동점(시소러스 미스로 전부 0)이면 max가 늘
        # **사전 첫 키**를 줘서 한 명에게 깔때기가 됐다 — 동점은 '이번 회의에서 덜 가져간 쪽'으로 깬다.
        _fb_pool = {int(x): _bots_o[int(x)] for x in (
            getattr(getattr(flow, "current", None), "team", None)
            or getattr(flow, "project_team", None) or _bots_o.keys())
            if int(x or 0) in _bots_o} or dict(_bots_o)
        _fb_load = {}

        def _owner_fb(_st_o, _body_o):
            if not _fb_pool:
                return 0
            from ..role_fit import role_fit as _rf2
            _q2 = f"{getattr(_st_o, 'goal', '')} {_body_o}"
            _pick = max(_fb_pool, key=lambda k: (_rf2(_q2, _fb_pool[k]), -int(_fb_load.get(k, 0) or 0), -k))
            _fb_load[_pick] = int(_fb_load.get(_pick, 0) or 0) + 1
            return int(_pick)

        # [R1 원저자 귀속(2026-07-22, U-041)] 백로그 본문이 R1 독립 기고의 한 줄과 크게 겹치면 그
        # 기고자를 발제자로 — 병합 회의에서 앵커가 전사한 것을 실제 낸 사람에게 되돌린다(강제 아님).
        _r1a = getattr(flow, "_r1_attr", None) or []

        def _r1_author(_body_r):
            _qt = set(re.findall(r"[A-Za-z가-힣0-9]{2,}", str(_body_r or "")))
            if not _qt:
                return None
            _best, _who_r = 0.0, None
            for _bid, _txt in _r1a:
                _tt = set(re.findall(r"[A-Za-z가-힣0-9]{2,}", _txt))
                if not _tt:
                    continue
                _ov = len(_qt & _tt) / max(min(len(_qt), len(_tt)), 1)
                if _ov > _best:
                    _best, _who_r = _ov, _bid
            return _who_r if _best >= 0.5 else None

        # 발제 귀속은 보통 곧 전담이지만, "구현자와 다른 독립 QA/검수자"처럼 역할 분리를 명시한
        # 계약에서는 같은 발제자가 구현·검증 줄을 정리해 썼다는 이유로 한 사람이 양쪽을 수행하면
        # 독립성 자체가 사라진다. 내용별 직군 할당을 강제하지 않고, 명시된 독립 검증 항목만 실제
        # 제작 항목의 주인들과 분리한다. 적임자가 없으면 같은 사람에게 조용히 되돌리지 않고 충원·개서를
        # 요구해 명시 계약을 fail-closed한다.
        _origin_o = str(getattr(flow, "origin_request", "") or "")

        def _assignee_separation_signal(_text_i):
            """사람/소유 주체를 실제로 분리하라는 명시 계약만 잡는다.

            bare ``독립``은 '독립 실행 가능', bare ``서로 다른``은 '서로 다른 브라우저'처럼
            산출물 속성을 꾸밀 수 있다. 그런 문구와 QA가 우연히 한 요청에 있다는 이유로
            R1 원저자 귀속을 바꾸지 않는다.
            """
            _t_i = str(_text_i or "").lower()
            return bool(_re3.search(
                r"((?:서로\s*|각기\s*)?다른\s*"
                r"(?:담당자|수행자|작업자|작성자|구현자|개발자|제작자|저자|"
                r"검증자|검수자|리뷰어|사람|인원|주인|소유자)|"
                r"별도(?:의)?\s*(?:담당자|수행자|작업자|검증자|검수자|리뷰어|사람|인원)|"
                r"독립(?:적인)?\s*(?:검증자|검수자|리뷰어|qa\s*(?:담당자|수행자))|"
                r"(?:담당자|수행자|작업자|작성자|구현자|개발자|제작자|저자|"
                r"검증자|검수자|리뷰어)\s*(?:를|을|은|는|가|이)?\s*(?:분리|구분|분담)|"
                r"(?:different|separate)\s+(?:assignee|owner|reviewer|author|"
                r"implementer|developer|person|people)|"
                r"independent\s+(?:assignee|owner|reviewer|author|person)|"
                r"(?:reviewer|assignee|owner)\s+(?:different\s+from|separate\s+from)\s+"
                r"(?:the\s+)?(?:author|implementer|developer|producer)|"
                r"someone\s+other\s+than\s+(?:the\s+)?(?:author|implementer|developer|producer))",
                _t_i,
            ))

        def _review_signal(_text_i):
            _t_i = str(_text_i or "").lower()
            return bool(_re3.search(
                r"(^|[^a-z])(qa|quality\s*assurance|review)([^a-z]|$)|"
                r"(검증|검수|테스트|실증|회귀|품질\s*확인)",
                _t_i,
            ))

        def _review_role_signal(_text_i):
            _t_i = str(_text_i or "").lower()
            return bool(_re3.search(
                r"(^|[^a-z])(qa|quality\s*assurance|reviewer)([^a-z]|$)|"
                r"(검수자|검증자|테스트\s*담당|품질\s*담당|교차\s*(?:검증|검수|리뷰))",
                _t_i,
            ))

        def _production_signal(_text_i):
            _t_i = str(_text_i or "").lower()
            return bool(_re3.search(
                r"(^|[^a-z])(implement|implementation|developer|production)([^a-z]|$)|"
                r"(구현|개발|제작|생산)",
                _t_i,
            ))

        _contract_separates_review = (
            _assignee_separation_signal(_origin_o) and _review_signal(_origin_o)
        )

        def _independent_review(_body_i, _scope_i=""):
            _body_text_i = str(_body_i or "")
            _scope_text_i = str(_scope_i or "")
            # U-060: 전용 SubTask 제목이 ``독립 QA``인데 그 아래 실제 검증 행마다 'QA 담당자'를
            # 반복하지는 않는다. 명시 분리 계약 아래의 *전용 검증 단위*는 범위를 상속하되,
            # ``구현과 독립 QA``처럼 제작까지 섞인 단위는 본문 신호 없이 통째 검증으로 오인하지 않는다.
            _dedicated_review_scope_i = (
                _review_role_signal(_scope_text_i)
                and not _production_signal(_scope_text_i)
            )
            return (
                _review_signal(f"{_scope_text_i} {_body_text_i}")
                and (
                    _assignee_separation_signal(_body_text_i)
                    or (
                        _contract_separates_review
                        and (
                            _review_role_signal(_body_text_i)
                            or _dedicated_review_scope_i
                        )
                    )
                )
            )

        _team_o = [
            int(x) for x in (
                getattr(getattr(flow, "current", None), "team", None)
                or getattr(flow, "project_team", None)
                or _bots_o.keys()
            )
            if int(x or 0) in _bots_o
        ]

        def _independence_unavailable(_body_i, _producer_owners_i, _why_i):
            _producers_i = ", ".join(map(str, sorted(_producer_owners_i))) or "미상"
            return (
                "독립 검증 담당자 분리 계약을 충족할 수 없습니다 — "
                f"제작 담당({_producers_i})과 겹치는 검증 항목 "
                f"'{str(_body_i or '')[:70]}'에 {_why_i}. "
                "recruit로 QA/검수 직군을 Task 팀에 충원하거나, 제작자와 실제로 다른 담당자가 "
                "검증 항목을 맡도록 수렴안/R1 귀속을 다시 작성하세요."
            )

        def _independent_owner(_body_i, _scope_i, _base_i, _producer_owners_i):
            _base_i = int(_base_i or 0)
            if (not _independent_review(_body_i, _scope_i)
                    or _base_i not in _producer_owners_i):
                return _base_i, ""
            _cands_i = [
                mid for mid in _team_o
                if mid not in _producer_owners_i and str(_bots_o.get(mid) or "").strip()
            ]
            if not _cands_i:
                return _base_i, _independence_unavailable(
                    _body_i, _producer_owners_i,
                    "제작자가 아닌 Task 팀 후보가 없습니다",
                )
            from ..role_fit import role_fit as _rf_independent
            _fit_query_i = f"{str(_scope_i or '')} {str(_body_i or '')}".strip()
            # stable max: 동점이면 Task 팀의 기존 순서를 보존한다.
            _winner_i = max(
                _cands_i,
                key=lambda mid: _rf_independent(_fit_query_i, _bots_o[mid]),
            )
            if _rf_independent(_fit_query_i, _bots_o[_winner_i]) <= 0:
                return _base_i, _independence_unavailable(
                    _body_i, _producer_owners_i,
                    "검증 역할 적합도가 0보다 큰 후보가 없습니다",
                )
            return _winner_i, ""

        # blocked 보충은 이름/시각 추측이 아니라 원본 scope 링크로 연결한다. 같은 ST에 blocked가
        # 하나뿐이면 영역 배정만으로 안전하게 자동 연결하고, 여러 개거나 교차 영역 선행이면 회의가
        # `[해결: ST::Bn]`을 명시해야 한다. 모든 blocked가 이번 회의 항목 하나 이상과 연결돼야 등록.
        _supplement_plan = []
        _linked_scopes = set()
        for _ln_p in lines:
            _s_p = _ln_p.strip()
            if not _s_p.startswith("백로그:"):
                continue
            _it_p = _s_p.split(":", 1)[1].strip()
            _st_p, _body_p = _dest_of(_it_p)
            _links_p = []
            _rm = _re3.match(r"^\[해결\s*:\s*([^\]]{3,120})\]\s*(.*)$", _body_p)
            if _rm:
                _scope_p = _rm.group(1).strip()
                _body_p = _rm.group(2).strip()
                if _scope_p not in _blocked_scopes:
                    return False, (f"보충 대상 '{_scope_p}'가 현재 blocked 원장에 없습니다. "
                                   f"대상은 다음 중 하나를 그대로 쓰세요: {' · '.join(_blocked_scopes)}")
                _links_p = [_scope_p]
            elif _blocked_scopes:
                _same_p = [scope for scope in _blocked_scopes
                           if scope.startswith(f"{_st_p.st_id}::")]
                if len(_same_p) == 1:
                    _links_p = _same_p
                else:
                    return False, (
                        f"보충 백로그 '{_body_p[:55]}'가 해결할 blocked 원본이 모호합니다 — 본문 앞에 "
                        f"`[해결: ST::Bn]`을 붙이세요. 대상: {' · '.join(_blocked_scopes)}")
            if not _body_p:
                return False, "보충 대상 표식 뒤에 실제 작업 단위 본문이 필요합니다."
            _linked_scopes.update(_links_p)
            _supplement_plan.append((_s_p, _st_p, _body_p, _links_p))
        if _blocked_scopes:
            _unlinked = [scope for scope in _blocked_scopes if scope not in _linked_scopes]
            if _unlinked:
                return False, ("이번 보충 회의에 선행 작업이 없는 blocked 원본이 남았습니다 — 각각 최소 "
                               f"한 항목을 `[해결: ST::Bn]`으로 연결하세요: {' · '.join(_unlinked)}")

        # [직군 기회/판단 게이트(2026-07-23 ch94 → 07-24 ch95 교정)] 보장해야 하는 것은 직군별
        # **판단 기회**이지 모든 직군의 백로그 소유가 아니다. 실질 기고를 냈고 전원이 결론에 찬성했는데
        # 문구가 다른 사람 손으로 전사됐다는 이유로 자기 소유 0건을 거부하면, 수렴한 회의를 영원히 못
        # 닫는다(ch95: 개설자 브랜드가 R1 동시 wake 제외 → 브랜드 줄이 있어도 5회 동일 거부).
        # 각 대상이 ① 실질 독립 기고/여는 의견을 냈거나 ② 이유 있는 패스를 남겼는지만 확인한다.
        # 소유자 분포는 관측으로 남기되 등록 조건으로 강제하지 않는다.
        _coverage_targets = {int(x) for x in (getattr(flow, "_r1_targets", None) or set())}
        _coverage_passes = {int(k): str(v) for k, v in
                            (getattr(flow, "_r1_passes", None) or {}).items() if str(v).strip()}
        _coverage_responded = {int(x) for x in
                               (getattr(flow, "_r1_responded", None) or set())}
        if _coverage_targets:
            _predicted_owners = set()
            for _st0 in _alive_sts:
                _r0 = store.get(_st0.st_id)
                if _r0:
                    _predicted_owners.update(int(b.submitter) for b in _r0.backlogs if int(b.submitter or 0))
            for _s0, _st0, _body0, _links0 in _supplement_plan:
                try:
                    _predicted_owners.add(int(_r1_author(_body0)
                                              or _attr_of(draft_norm_line(_s0) or _s0)
                                              or _owner_fb(_st0, _body0)))
                except Exception:
                    pass
            _missing = sorted(_coverage_targets - _coverage_responded - set(_coverage_passes))
            if _missing:
                _roles = " · ".join(str(getattr(flow, "_info", lambda x: x)(x) or x)
                                    for x in _missing)
                if flow.log:
                    flow.log("backlog_role_coverage", owners=len(_predicted_owners),
                             responded=len(_coverage_responded), passes=len(_coverage_passes),
                             missing=" ".join(map(str, _missing)))
                return False, (f"직군별 판단 응답이 빠졌습니다: {_roles}. 각자는 독립 기고에서 자기 관점의 "
                               f"실질 의견을 내거나, 정말 할 일이 없으면 `[패스: 이유]`로 명시해야 합니다. "
                               f"백로그 소유를 직군마다 강제하지는 않습니다.")
            if flow.log:
                flow.log("backlog_role_coverage", owners=len(_predicted_owners),
                         responded=len(_coverage_responded), passes=len(_coverage_passes), missing="")

        # 먼저 전체 귀속을 계산해야 QA 줄이 구현 줄보다 앞에 있어도 같은 회의의 제작 주인들을 모두
        # 제외할 수 있다. 순차 등록 중 "지금까지 본 항목"만 보면 줄 순서가 독립성 결과를 바꾼다.
        _planned = []
        for _s, _st_d, _body, _supplement_for in _supplement_plan:
            # [귀속 출처를 남긴다(2026-07-27)] 한 사람에게 쏠릴 때 '정말 그가 썼나(r1·attr)'와
            # '기계가 몰아준 건가(fallback)'를 로그만으로 가를 수 없어 원인 판별이 막혔다. 값은 안
            # 바꾸고 출처만 기록한다 — 다음 판에서 실측으로 갈린다.
            _src_r1 = int(_r1_author(_body) or 0)
            _src_attr = int(_attr_of(draft_norm_line(_s) or _s) or 0) if not _src_r1 else 0
            _base_owner = _src_r1 or _src_attr or int(_owner_fb(_st_d, _body) or 0) or 0
            _attr_src = ("r1" if _src_r1 else "draft" if _src_attr
                         else "fallback" if _base_owner else "none")
            if flow.log:
                flow.log("backlog_owner_attributed", src=_attr_src, owner=int(_base_owner),
                         st=str(getattr(_st_d, "st_id", "")), body=str(_body)[:40])
            _planned.append([
                _s, _st_d, _body, _supplement_for, _base_owner,
            ])
        # 현재 회의보다 먼저 끝난 구현도 자기검증의 제작 이력이다. 현재 마일스톤의 정본 SubTask는
        # done을 포함해 전부 보되 superseded만 제외하고, 실제 수행자가 따로 있으면 submitter와 함께
        # 제외한다. 그래야 '구현 회의 → 완료 → 후속 독립 QA 회의' 순서도 한 회의 등록과 동형이다.
        _existing_producer_owners = set()
        for _st_existing in (getattr(_open, "subtasks", None) or []):
            if getattr(_st_existing, "status", "") == "superseded":
                continue
            _relay_existing = store.get(_st_existing.st_id)
            for _backlog_existing in (
                    getattr(_relay_existing, "backlogs", None) or []):
                if _independent_review(
                        getattr(_backlog_existing, "body", ""),
                        getattr(_st_existing, "goal", "")):
                    continue
                for _owner_existing in (
                        getattr(_backlog_existing, "submitter", 0),
                        getattr(_backlog_existing, "assignee", 0)):
                    if int(_owner_existing or 0):
                        _existing_producer_owners.add(int(_owner_existing))

        _producer_owners = _existing_producer_owners | {
            int(row[4])
            for row in _planned
            if int(row[4] or 0)
            and not _independent_review(row[2], getattr(row[1], "goal", ""))
        }
        for row in _planned:
            _base_owner = int(row[4] or 0)
            _new_owner, _independence_error = _independent_owner(
                row[2], getattr(row[1], "goal", ""),
                _base_owner, _producer_owners)
            if _independence_error:
                return False, _independence_error
            row[4] = _new_owner
            if int(row[4] or 0) != _base_owner and flow.log:
                flow.log(
                    "backlog_independence_reassigned",
                    by=_base_owner, to=int(row[4]), body=str(row[2])[:100],
                )

        n = 0
        _per = {}
        _skipped = 0
        for _s, _st_d, _body, _supplement_for, _who in _planned:
            try:
                # [참조·재진술 반려(2026-07-22, U-041)] 순수 참조는 submit 관문이 반려(force 무관),
                # 재진술은 중복 게이트(force=False)가 잡는다 — 병합 회의도 예외 없이(종전 force=True가
                # 두 게이트를 다 우회해 'B4'·재진술이 백로그로 태어난 것이 근본).
                # 기본 귀속 우선순위는 R1 원저자 > DRAFT 편집 저자 > 적임 폴백. 명시된 독립 검증만
                # 위에서 제작 주인과 다른 Task 팀원으로 분리한다.
                _new_b = relay_for(flow, _st_d).submit(_who, _body, force=False)
                if _supplement_for:
                    _new_b.supplement_for = list(_supplement_for)
                n += 1
                _per[_st_d.st_id] = _per.get(_st_d.st_id, 0) + 1
            except (BacklogError, DuplicateBacklog):
                _skipped += 1        # 참조·재진술·중복 — 조용히 스킵(원본이 이미 등록됨)
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
                "종결 표결의 [수렴안]에 '단위: ⟦목표⟧ | ⟦실증절차⟧' 줄로 동봉하세요. 가결되면 마일스톤과 "
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


def _bind_sys_release_receipt(flow, me_id, ms, tgt, results, receipt_id: str) -> bool:
    """run 도구의 private ledger row를 현재 SYS release challenge 한 조건에 단일사용으로 결부한다."""
    if tgt is not None:
        return False
    rid = str(receipt_id or "").strip()
    ctx = getattr(flow, "_release_verify_challenge", None) or {}
    ledger = getattr(flow, "_run_receipts", None) or {}
    row = ledger.get(rid)
    if not rid or not ctx or not row:
        return False
    if (str(ctx.get("ms_id") or "") != str(getattr(ms, "ms_id", ""))
            or str(row.get("challenge") or "") != str(ctx.get("token") or "")
            or str(row.get("evidence_for") or "") != str(ctx.get("desc") or "")
            or not ctx.get("verifier_used")
            or str(row.get("verifier_seal") or "") != str(ctx.get("verifier_seal") or "")
            or str(row.get("command_hash") or "") != str(ctx.get("verifier_command_hash") or "")
            or str(row.get("spec_hash") or "") != str(ctx.get("verifier_spec_hash") or "")
            or str(row.get("command_hash") or "")
            != verifier_command_hash(row.get("command"))
            or str(row.get("spec_hash") or "")
            != verifier_spec_hash(ctx.get("desc"), ctx.get("verify"))
            or int(row.get("actor") or 0) != int(me_id)
            or int(row.get("rc") if row.get("rc") is not None else -1) != 0
            or int(row.get("write_epoch", -2)) != write_revision(flow)
            or not row.get("artifact_stamp")
            or str(row.get("artifact_stamp")) != workspace_artifact_stamp(flow)):
        return False
    target = next((c for c in ms.criteria
                   if getattr(c, "release_lock", False)
                   and c.desc.strip() == str(ctx.get("desc") or "").strip()
                   and c.verify.strip() == str(ctx.get("verify") or "").strip()), None)
    if target is None:
        return False
    claim = next((r for r in results
                  if bool(r.get("passed"))
                  and str(r.get("desc") or "").strip() == target.desc.strip()), None)
    if claim is None:
        return False
    stderr = str(row.get("stderr") or "").strip()
    tail = str(row.get("stdout") or "").strip()
    receipt = f"SYS-RUN {rid} exit=0 `{str(row.get('command') or '')[:100]}`"
    if tail:
        receipt += "\n" + tail[-220:]
    if stderr:
        receipt += "\n[stderr] " + stderr[-120:]
    claim["evidence"] = receipt
    claim["_sys_run_receipt"] = receipt
    claim["_sys_run_receipt_id"] = rid
    claim["_verified_write_epoch"] = int(row["write_epoch"])
    claim["_verified_artifact_stamp"] = str(row["artifact_stamp"])
    claim["_verified_command"] = str(row.get("command") or "")
    claim["_verified_command_hash"] = str(row.get("command_hash") or "")
    claim["_verified_spec_hash"] = str(row.get("spec_hash") or "")
    try:
        flow._run_receipts.pop(rid, None)     # 성공 결부 순간 소진 — 다른 조건/턴에 재사용 불가
    except Exception:
        pass
    return True


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
    # [완료 단위 재검증 차단(2026-07-21, U-039 재개 실측: 앵커가 이미 done인 ST-1에 계속 report_iter →
    # iter_n++·ms_iter_pass 반복 루프로 크레딧 공회전, 릴레이는 ST-2로 안 넘어감)] 이미 done인
    # SubTask는 재검증하지 않는다 — 다음 미완 단위 백로그로 가라고 코칭(재개 시 앵커의 스테일
    # 컨텍스트가 완료 단위를 계속 닫으려는 것 근절). 미완 단위가 있으면 그리로 유도.
    if tgt is not None and getattr(tgt, "status", "") == "done":
        _nxt = next((s for s in ms.subtasks if s.status != "done"), None)
        return (f"이 단위({tgt.st_id})는 이미 완료됐습니다 — 재검증하지 않습니다. "
                + (f"다음 미완 단위 **{_nxt.st_id}**({_nxt.goal[:40]})의 백로그를 pick_backlog로 "
                   f"집어 진행하세요." if _nxt else "이 주기의 단위가 모두 완료됐습니다 — report_iter"
                   f"(wrapup='done')로 주기를 닫으세요."))
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
    _bind_sys_release_receipt(flow, me_id, ms, tgt, results, args.get("receipt"))
    # [백로그 의무화 2단(2026-07-10, 사용자)] 솔로 진행(위임 無)도 장부가 진실이도록 — 검증 제출된
    # 조건 단위를 SubTask 릴레이에 소급 등재(pass=done, fail=in_progress 보유). 위임형은 1단(자동
    # 제출)이 이미 등재하므로 매칭돼 중복 없음.
    if tgt is not None:
        try:
            from .backlog import relay_for, _match_backlog, pipeline_on as _po, BacklogError, DuplicateBacklog
            r = relay_for(flow, tgt)
            _finished_mine = False
            for it in results:
                d = str(it.get("desc") or "")[:120]
                if not d:
                    continue
                b = _match_backlog(r, d)
                _fresh = False
                if b is None:
                    try:
                        b = r.submit(int(me_id), d, force=True)
                        _fresh = True
                    except (BacklogError, DuplicateBacklog):
                        # 참조 표기·중복 desc는 새 백로그로 만들지 않는다(검증만 — churn 차단)
                        continue
                try:
                    # [무작업 일괄완료 차단(2026-07-22, U-041 실측: 게임 기획자가 B1만 작업하고 한 보고로
                    # B2·B3을 무작업 즉시 완료 — 서브태스크 전체를 한 봇이 거짓 일괄 완수)] 보고는 '지금
                    # 내가 든 백로그(in_progress·assignee=me)'나 '방금 만든 솔로 작업'만 완료한다.
                    # 선점도 안 한 등록 백로그(open, 미착수)는 보고 desc로 자동 pick+done하지 않는다 —
                    # 그건 각자 pick→작업→보고를 거쳐야 한다(릴레이가 [다음 선정]으로 이어줌).
                    _mine = (b.status == "in_progress" and int(b.assignee or 0) == int(me_id))
                    if _fresh and b.status == "open":
                        # 보고 경로도 열린 마일스톤 전체의 단일 활성 잠금을 지킨다. 다른 ST가 작업
                        # 중이면 방금 만든 항목은 open 장부로만 남고, 릴레이가 차례에 착수시킨다.
                        from .backlog import active_backlog_rows
                        if not active_backlog_rows(flow):
                            r.pick(int(me_id), b.backlog_id, int(me_id))   # 솔로: 방금 만든 것만 픽
                            _mine = True
                    if it.get("passed") and b.status != "done" and _mine:
                        r.done(int(me_id), b.backlog_id)
                        _finished_mine = True
                    # else: 등록됐지만 미착수(open, 내 것 아님) → 이 보고로 완료 안 함
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
    # [서브태스크 = 게이트 없음(2026-07-22, 사용자: '백로그·서브태스크 검증은 비용만 크고 효과 적다 —
    # 검증은 마일스톤에서 처리')] 서브태스크는 조건 검증(iter_verify)으로 닫지 않는다 — 백로그가 다
    # 소진되면 완수(작업 단위 grouping일 뿐). 위 루프가 든 백로그를 완료했고, 여기선 소진 여부만 본다.
    # 마일스톤만 실증 게이트(진짜 완수 판정)를 유지한다.
    if tgt is not None:
        _r2 = relay_for(flow, tgt)
        _left = [b for b in _r2.backlogs if b.status not in ("done", "dropped")]
        if not _left:
            try:
                from .backlog import on_subtask_wrapup
                _sweep = on_subtask_wrapup(flow, tgt)
            except Exception as e:
                _sweep = f"(정리 훅 실패: {str(e)[:50]})"
            tgt.status = "wrapup"          # 게이트 없음 — 소진이 곧 정리 완료 전제(wrapup_done 진입 조건)
            wrapup_done(flow, tgt)
            return f"SubTask {tgt.st_id} — 백로그 전부 소진, 완수. {_sweep}"
        return (f"백로그 완료 기록 — SubTask {tgt.st_id} 잔여 {len(_left)}건. 다음 수행자를 "
                f"pick_backlog(id)로 선정하세요(검증 게이트는 마일스톤에서).")
    # 대상 없음 = 마일스톤 완수조건 검증(유일한 실증 게이트)
    ok, note = iter_verify(flow, ms, results)
    if ok:
        return (f"iter {ms.iter_n} 통과 — 주기 {ms.ms_id}가 wrapup(잔여 정리)로 전이. "
                f"남은 SubTask·백로그를 정리한 뒤 report_iter(wrapup='done')로 닫으세요.")
    return f"주기 {ms.ms_id} iter {ms.iter_n} — {note}. 증거 없는 pass는 인정되지 않습니다."


# ── 직렬화 (계약 §9 — 최대 저장: 체크포인트 동승·재시작 후 중간 재개) ─────────────

def _crit_dict(c):
    return {"desc": c.desc, "verify": c.verify, "passed": c.passed, "evidence": c.evidence,
            "status": c.status, "block_reason": c.block_reason,
            "verify_attempts": c.verify_attempts,
            "release_lock": bool(getattr(c, "release_lock", False)),
            "evidence_source": str(getattr(c, "evidence_source", "") or ""),
            "receipt_id": str(getattr(c, "receipt_id", "") or ""),
            "verified_write_epoch": int(getattr(c, "verified_write_epoch", -1)),
            "verified_artifact_stamp": str(getattr(c, "verified_artifact_stamp", "") or ""),
            "verified_command": str(getattr(c, "verified_command", "") or ""),
            "verified_command_hash": str(getattr(c, "verified_command_hash", "") or ""),
            "verified_spec_hash": str(getattr(c, "verified_spec_hash", "") or ""),
            "ratified_verifier_command": str(
                getattr(c, "ratified_verifier_command", "") or ""),
            "ratified_verifier_command_hash": str(
                getattr(c, "ratified_verifier_command_hash", "") or ""),
            "ratified_verifier_spec_hash": str(
                getattr(c, "ratified_verifier_spec_hash", "") or "")}


def ms_to_dict(ms: Milestone) -> dict:
    return {"ms_id": ms.ms_id, "goal": ms.goal, "status": ms.status, "iter_n": ms.iter_n,
            "iter_stuck": ms.iter_stuck, "origin": ms.origin,
            "carried": [dict(x) for x in (ms.carried or [])],   # [이월 원장 동승] 재시작 너머 보존
            "criteria": [_crit_dict(c) for c in ms.criteria],
            "locked_criteria": [_crit_dict(c) for c in (getattr(ms, "locked_criteria", None) or [])],
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
                          status=str(r.get("status") or "active"), block_reason=str(r.get("block_reason") or ""),
                          verify_attempts=int(r.get("verify_attempts") or 0),
                          release_lock=bool(r.get("release_lock")),
                          evidence_source=str(r.get("evidence_source") or ""),
                          receipt_id=str(r.get("receipt_id") or ""),
                          verified_write_epoch=int(
                              r.get("verified_write_epoch")
                              if r.get("verified_write_epoch") is not None else -1),
                          verified_artifact_stamp=str(r.get("verified_artifact_stamp") or ""),
                          verified_command=str(r.get("verified_command") or ""),
                          verified_command_hash=str(r.get("verified_command_hash") or ""),
                          verified_spec_hash=str(r.get("verified_spec_hash") or ""),
                          ratified_verifier_command=str(
                              r.get("ratified_verifier_command") or ""),
                          ratified_verifier_command_hash=str(
                              r.get("ratified_verifier_command_hash") or ""),
                          ratified_verifier_spec_hash=str(
                              r.get("ratified_verifier_spec_hash") or ""))
                for r in (rows or [])]
    ms = Milestone(ms_id=str(d.get("ms_id") or ""), goal=str(d.get("goal") or ""),
                   criteria=_crit(d.get("criteria")), status=str(d.get("status") or "open"),
                   locked_criteria=_crit(d.get("locked_criteria")),
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
