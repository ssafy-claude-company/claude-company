"""[Rule — 백로그 릴레이 (파이프라인 재설계 S2)] SubTask 하나의 백로그 풀 + 릴레이 턴 상태기계.

계약: murmur/docs/PIPELINE_REWORK_2026-07-09.md §3(릴레이)·§9(저장)·§11(이벤트).
원리(§0): 배분은 결정권자가 아니라 **경계의 현장**이 맡는다 — 백로그를 마무리한 사람이 다음을 정하고,
막히면 응찰로, 무응찰이면 다시 마지막 작업자가 정한다. 결정권자는 여기 개입하지 않는다(동률·교착만).

Sacks 3규칙 완성형 (§3 확정):
  ① 지명   — 마무리자가 남은 풀을 보고 다음 (백로그, 수행자)를 정한다: pick()
  ② 응찰   — 지명 불가·부적합·거절 시 후보들이 [응찰: N]으로 자기선택: bid_round()
             (점수 파싱·수집은 호출부 몫 — 여기는 순수 선정 규칙. floor.py 응찰과 같은 분업)
  ③ 무응찰 — 마지막에 일한 작업자(마무리자)가 지정: pass_to().
             응찰이 무산돼 돌아와도 같은 사람이 재지정(결정권자 개입 없음).

접점(경계 명시):
  - Milestone/SubTask 표현은 S1 소유 — 여기는 subtask_id 문자열만 받는다(덕타이핑, mock 선진행).
  - 이벤트는 주입된 log(event, **fields) 콜백으로 §11 이름 그대로 방출(flow.jsonl 적재는 SYS 몫).
  - 상태 전부가 to_ckpt()/from_ckpt() dict로 왕복한다(§9 최대 저장) — flow _ckpt 동승은 통합 주기에.
  - **이 모듈은 어디서도 import되지 않는 동안 라이브 무영향**(ORGANT_PIPELINE 이중수용의 S2 절반 —
    배선 자체가 플래그 뒤에서만 일어난다. 배선은 S1 공유 접점).

중복 방지 게이트(§3): 직군 변형 게이트(_find_variant_job)와 동형 — 시스템이 '정답'을 정하지 않고,
겹침을 멈춰 세워 제출자가 재사용인지 진짜 새 백로그인지 명시(force)하게 한다. 판정은 위임 중복
판정과 같은 어휘 겹침(_body_overlap 재사용 — 도메인 하드코딩 없음).
"""
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .comm_helpers import _body_overlap
from .milestone import next_milestone, pipeline_on   # 단방향: milestone은 backlog를 모른다(순환 0)

# 상태 5종 — 기본 4종(§3) + dropped(2026-07-14, 사용자: '개인이 올린거니 중지가 아니라 중단으로
# 처리해서 아예 백로그 처리에서 제외'). 백로그는 개인 작업 단위라 완수 불가 판단도 본인 몫 —
# blocked(재방문 가능)와 달리 dropped는 장부에서 종결 취급(remaining 제외·재선정 불가).
OPEN, IN_PROGRESS, BLOCKED, DONE = "open", "in_progress", "blocked", "done"
DROPPED = "dropped"

# 교착 판정(§3 제안): 같은 백로그가 차단으로 2회 재방문 = 차단이 2번째 쌓이는 순간.
# (1차 차단 → 핸드오프로 재방문·재개 → 또 차단 = 선행 해소가 안 돌고 있다는 신호)
DEADLOCK_BLOCKS = 2


def backlog_scope_key(subtask_id, backlog_id) -> str:
    """런타임 집합/맵용 전역 키. B1은 SubTask마다 다시 시작하므로 ID 단독 사용은 충돌한다."""
    return f"{str(subtask_id)}::{str(backlog_id)}"


def blocked_ready_for_revisit(backlog, all_backlogs, blocked_scope) -> bool:
    """선행 보충 작업이 실제로 끝난 뒤에만 blocked 원본을 재개할 수 있다.

    blocked 직후 곧바로 다시 집으면 같은 선행 부족으로 재차 막힌다. 전체 주기의 실행 가능
    백로그(open/in_progress)를 먼저 소진하고, 차단 뒤 **새로 생성되어** 끝난 보충 백로그가 하나
    이상 있을 때만 원본을 다시 후보로 올린다. 별도 의존 그래프가 없는 현재 장부에서
    보충 회의가 새 항목에 새긴 ``supplement_for=(ST::B)`` 링크와
    ``ts_submit > blocked.ts_done``·후속 ``ts_done``이 보충 세대·실행의 단조로운 증거다.
    """
    if getattr(backlog, "status", "") != BLOCKED:
        return False
    blocked_at = float(getattr(backlog, "ts_done", 0) or 0)
    blocked_scope = str(blocked_scope or "")
    rows = list(all_backlogs or [])
    if (not blocked_scope or blocked_at <= 0
            or any(getattr(x, "status", "") in (OPEN, IN_PROGRESS) for x in rows)):
        return False
    linked = [
        x for x in rows
        if x is not backlog
        and blocked_scope in (getattr(x, "supplement_for", None) or [])
        and float(getattr(x, "ts_submit", 0) or 0) > blocked_at
    ]
    # 하나의 원본을 풀기 위해 등록된 보충 단위가 여러 개인 경우 일부만 끝난 시점에 원본을
    # 되살리면 아직 막힌 선행을 건너뛴다. 연결된 세대 전부가 종결되고, 그중 실제 완료가 하나
    # 이상 있어야 한다. dropped만 있는 세대는 "해결했다"는 증거가 아니다.
    return bool(linked) and all(
        getattr(x, "status", "") in (DONE, DROPPED)
        and float(getattr(x, "ts_done", 0) or 0) > blocked_at
        for x in linked
    ) and any(getattr(x, "status", "") == DONE for x in linked)


def drop_unresolvable_blocked(flow) -> list:
    """[주기 안에서 풀 수 없는 막힘은 접는다(2026-07-29, U-079 4세대 실측)]

    e2e 장부 항목(condition:N)의 재실증을 요구하는 백로그는 **Task 경계에서만** 가능하다. 그런데
    장부는 그 사실을 모르고 '막힘 → 보충 회의 → 보충 백로그' 사이클을 돌린다. 실측: 보충 8건이
    다 끝나도 원본은 그대로 막혔고, 회의가 4번 열려 마지막엔 새 백로그 0건으로 판이 파킹됐다.
    이 원본은 이번 주기에 어떤 보충으로도 완료될 수 없다 — 사유를 남기고 접어, 주기가 검증까지
    갈 수 있게 한다. 접힌 일감의 실증은 사라지지 않는다: Task 경계에서 e2e가 그 항목을 다시 건다.
    """
    import re as _re
    ms = next((m for m in (getattr(flow, "milestones", None) or [])
               if m.status not in (DONE, "superseded")), None)
    if ms is None:
        return []
    store = getattr(flow, "backlog_relays", None) or {}
    dropped = []
    for st in (getattr(ms, "subtasks", None) or []):
        if st.status in (DONE, "superseded"):
            continue
        for b in (getattr(store.get(st.st_id), "backlogs", None) or []):
            if getattr(b, "status", "") != BLOCKED:
                continue
            why = " ".join(str(x) for x in [getattr(b, "block_reason", ""), getattr(b, "body", "")])
            if not (_re.search(r"(condition\s*:\s*\d+|\be2e\b)", why, _re.I)
                    and _re.search(r"(receipt|target|challenge|봉인|seal)", why, _re.I)):
                continue
            b.status = DROPPED
            b.ts_done = time.time()
            try:
                line = ("[접음] e2e 장부 재실증은 Task 경계에서만 가능해 이번 주기에서는 완료할 수 "
                        "없습니다 — 주기가 닫히면 e2e가 이 항목을 다시 겁니다.")
                if not getattr(b, "activity", None) or b.activity[-1] != line:
                    b.activity.append(line)
            except Exception:
                pass
            dropped.append(b.backlog_id)
    return dropped


def revive_blocked_when_pool_exhausted(flow) -> list:
    """[막힘은 판이 비면 되돌아온다(2026-07-29, 사용자: '백로그 다 끝나면 막힘이 다시 –로')]

    blocked는 '지금은 선행이 없어 못 한다'는 표시지 폐기가 아니다. 그런데 종전엔 원본을 다시
    후보로 올리는 조건이 보충 백로그와의 `[해결: ST::Bn]` 링크에 달려 있어, 링크가 없거나 표기가
    어긋나면 실행 가능한 일이 하나도 안 남은 뒤에도 원본이 blocked로 굳어 판이 멈췄다
    (U-079 4세대: 보충 8건이 다 끝나도 원본 B1은 ⛔ 그대로).

    규칙은 판 상태 하나로 충분하다 — **열린·진행 중 백로그가 하나도 안 남으면** 그 사이 팀이
    할 수 있는 일은 다 한 것이므로, 남은 blocked를 open으로 되돌려 다시 집을 수 있게 한다.
    막힘 사유는 활동 기록에 남겨 왜 한 번 멈췄는지가 사라지지 않게 한다.
    """
    ms = next((m for m in (getattr(flow, "milestones", None) or [])
               if m.status not in (DONE, "superseded")), None)
    if ms is None:
        return []
    store = getattr(flow, "backlog_relays", None) or {}
    rows = [b for st in (getattr(ms, "subtasks", None) or [])
            if st.status not in (DONE, "superseded")
            for b in (getattr(store.get(st.st_id), "backlogs", None) or [])]
    if any(getattr(b, "status", "") in (OPEN, IN_PROGRESS) for b in rows):
        return []
    revived = []
    for b in rows:
        if getattr(b, "status", "") != BLOCKED:
            continue
        why = str(getattr(b, "block_reason", "") or "").strip()
        b.status = OPEN
        b.ts_done = 0
        b.block_reason = ""
        try:
            line = ("[막힘 해제] 실행 가능한 백로그가 모두 끝나 다시 집을 수 있습니다"
                    + (f" · 당시 사유: {why[:90]}" if why else ""))
            if not getattr(b, "activity", None) or b.activity[-1] != line:
                b.activity.append(line)
        except Exception:
            pass
        revived.append(b.backlog_id)
    return revived


def blocked_supplement_targets(scoped_rows):
    """이번 보충 회의가 직접 풀어야 할 blocked 잎 노드만 돌려준다.

    원본 O에 연결된 보충 S가 다시 blocked라면 O는 S를 기다리는 중이다. O와 S 모두에 새 일을
    강제하면 선행 그래프가 매 회의마다 불필요하게 증식한다. 아직 미종결인 연결 보충이 있는 원본은
    건너뛰고, 더 아래 연결이 없는 blocked(또는 연결분이 전부 중단돼 해결 증거가 없는 원본)만 대상이다.
    """
    scoped = list(scoped_rows or [])
    all_rows = [b for _st, b in scoped]
    targets = []
    for st, backlog in scoped:
        if getattr(backlog, "status", "") != BLOCKED:
            continue
        scope = backlog_scope_key(st.st_id, backlog.backlog_id)
        blocked_at = float(getattr(backlog, "ts_done", 0) or 0)
        linked = [
            row for row in all_rows
            if row is not backlog
            and scope in (getattr(row, "supplement_for", None) or [])
            and float(getattr(row, "ts_submit", 0) or 0) > blocked_at
        ]
        if any(getattr(row, "status", "") not in (DONE, DROPPED) for row in linked):
            continue
        # all-done 세대는 작업 단계에서 곧 재개할 것이므로 회의 대상이 아니다. 전부 dropped거나
        # 유효한 연결이 없으면 새 선행 작업이 필요하다.
        if linked and any(
                getattr(row, "status", "") == DONE
                and float(getattr(row, "ts_done", 0) or 0) > blocked_at
                for row in linked):
            continue
        targets.append((st, backlog))
    return targets


class BacklogError(Exception):
    """릴레이 규약 위반(턴·상태 전이) — comm_engine.CommError와 같은 지위."""


class DuplicateBacklog(BacklogError):
    """제출이 기존 백로그와 실질 중복 — 기존 id를 들려줘 제출자가 재사용/신규를 명시하게 한다."""

    def __init__(self, msg, existing_id=None):
        super().__init__(msg)
        self.existing_id = existing_id


@dataclass
class Backlog:
    """개인 처리 단위 하나(§1) — SubTask 안에서 중복 없이 최대한 제출된다."""
    backlog_id: str
    body: str                    # 제출 원문 — 중복 게이트·수행의 근거(압축에도 남는 사실원천)
    submitter: int
    status: str = OPEN
    assignee: Optional[int] = None
    block_count: int = 0         # 차단 누적 — DEADLOCK_BLOCKS번째에 deadlock_signal
    block_reason: str = ""
    note: str = ""               # iter 정리 등 상태 밖 메모(상태 4종을 오염시키지 않는다)
    supplement_for: list = field(default_factory=list)  # 보충 회의가 해결하려는 scoped blocked 원본들
    ts_submit: float = 0.0       # 생성 시각 — blocked 뒤 생긴 보충 세대인지 판별하는 구조 증거
    ts_pick: float = 0.0         # [창 귀속(2026-07-10)] 선정 시각 — 이 시각부터의 대화가 이 백로그
    ts_done: float = 0.0         # 완료/차단 시각 — 창의 끝
    activity: list = field(default_factory=list)  # 이 일감에 정확히 귀속된 작업 생각

    def to_dict(self) -> dict:
        return {"backlog_id": self.backlog_id, "body": self.body, "submitter": int(self.submitter),
                "status": self.status, "assignee": self.assignee, "block_count": self.block_count,
                "block_reason": self.block_reason, "note": self.note,
                "supplement_for": list(self.supplement_for or []),
                "ts_submit": self.ts_submit, "ts_pick": self.ts_pick, "ts_done": self.ts_done,
                "activity": list(self.activity or [])}

    @staticmethod
    def from_dict(d: dict) -> "Backlog":
        return Backlog(backlog_id=str(d.get("backlog_id")), body=str(d.get("body") or ""),
                       submitter=int(d.get("submitter") or 0), status=str(d.get("status") or OPEN),
                       ts_submit=float(d.get("ts_submit") or 0),
                       ts_pick=float(d.get("ts_pick") or 0), ts_done=float(d.get("ts_done") or 0),
                       assignee=d.get("assignee"), block_count=int(d.get("block_count") or 0),
                       block_reason=str(d.get("block_reason") or ""), note=str(d.get("note") or ""),
                       supplement_for=[str(x) for x in (d.get("supplement_for") or [])],
                       activity=[str(x)[:140] for x in (d.get("activity") or [])])


class BacklogRelay:
    """백로그 풀 + 턴 홀더 상태기계. 순수 로직(LLM 호출·매체 없음 — Rule은 물리법칙).

    턴 홀더 = 배분권을 쥔 사람. 규칙 ①·③의 주체다:
      - 백로그를 done한 사람이 턴 홀더가 된다(마무리자).
      - 차단 핸드오프 시 차단자가 지정한 '다음 시작자'가 턴 홀더가 된다.
      - 첫 배분(아직 아무도 일 안 함)은 턴 홀더 제약 없음 — 누가 여나는 상위(회의 종결)의 몫.
    """

    def __init__(self, subtask_id: str = "", log: Optional[Callable] = None):
        self.subtask_id = str(subtask_id)
        self._log = log
        self._pool: Dict[str, Backlog] = {}     # 제출 순서 보존
        self._seq = 0
        self.turn_holder: Optional[int] = None  # 배분권자(마무리자 또는 차단 핸드오프 지정자)
        self.closed = False                     # iter 종료 후 True — 제출·배분 불가

    # ── 관측 ──────────────────────────────────────────────────────────────
    def _emit(self, event: str, **fields) -> None:
        if self._log:
            try:
                self._log(event, subtask=self.subtask_id, **fields)
            except Exception:
                pass                            # 관측 실패가 규칙 실행을 죽이지 않는다(로그는 부수물)
        # [미러 실시간(2026-07-13)] 릴레이 변이가 밀스톤 깔때기(_ckpt)를 안 지나 화면 백로그가
        # 다음 ms 이벤트까지 낡던 랙 — 변이 즉시 미러 재게시(등재 순간 칩이 살아난다).
        cb = getattr(self, "_on_change", None)
        if cb:
            try:
                cb()
            except Exception:
                pass

    # ── 조회 ──────────────────────────────────────────────────────────────
    def get(self, backlog_id: str) -> Backlog:
        b = self._pool.get(str(backlog_id))
        if b is None:
            raise BacklogError(f"백로그 {backlog_id} 없음")
        return b

    @property
    def backlogs(self) -> List[Backlog]:
        return list(self._pool.values())

    def remaining(self) -> List[Backlog]:
        """아직 종결 아닌 것 전부(open/in_progress/blocked) — done·dropped는 처리 대상 밖."""
        return [b for b in self._pool.values() if b.status not in (DONE, DROPPED)]

    def all_done(self) -> bool:
        return bool(self._pool) and not self.remaining()

    # ── 제출 (§3 중복 방지 게이트) ─────────────────────────────────────────
    def submit(self, submitter: int, body: str, force: bool = False) -> Backlog:
        """백로그 제출. 주기 중 추가 허용(§2) — 단 iter 종료 후는 불가.

        중복 게이트: 기존 풀(상태 불문 — done과 겹쳐도 같은 일의 재제출이다)과 어휘 겹침이면
        DuplicateBacklog로 멈춰 세운다. 변형 게이트와 동형 — 시스템이 병합하지 않고, 제출자가
        force=True로 '진짜 다른 일'임을 명시해야 통과.
        """
        if self.closed:
            raise BacklogError("이 SubTask의 백로그 풀은 종료되었습니다(iter 완료) — 새 제출은 다음 주기에.")
        text = str(body or "").strip()
        if not text:
            raise BacklogError("빈 백로그는 제출할 수 없습니다 — 처리 단위를 본문으로 쓰세요.")
        # [참조 표기 반려 — 모든 경로 공통(2026-07-22, U-041 실측: 병합 회의 등록·작업 중 report_iter
        # 자동생성 양쪽에서 'B4'·'B2 점수 공식…' 같은 의존/참조 줄이 백로그로 태어나 즉시 완료로 churn
        # → 서브태스크 거짓 완수)] 순수 참조('B\d'·'#\d'·'BL-\d'로 시작)는 실작업 단위가 아니다 —
        # force와 무관하게(참조는 진짜 다른 일이 아니므로) 여기서 막는다. 모든 submit 경로의 관문.
        import re as _reb
        if _reb.match(r"^(B\s*\d+|#\s*\d+|BL[-\s]?\d+)\b", text):
            raise BacklogError("참조/의존 표기(예: 'B4'·'B2 …')는 백로그가 아닙니다 — 실제 작업 단위를 "
                               "본문으로 쓰거나, 의존은 완료 조건에 적으세요.")
        if not force:
            for ex in self._pool.values():                       # 제출 순서 = 판정 순서(결정성)
                if _body_overlap(text, ex.body):
                    raise DuplicateBacklog(
                        f"기존 백로그 {ex.backlog_id}(와)과 실질 중복으로 보입니다 — 같은 일이면 그쪽에 "
                        f"합류하고, 정말 다른 일이면 force로 명시해 다시 제출하세요.\n"
                        f"기존: {ex.body[:120]}", existing_id=ex.backlog_id)
        self._seq += 1
        b = Backlog(backlog_id=f"B{self._seq}", body=text, submitter=int(submitter),
                    ts_submit=time.time())
        self._pool[b.backlog_id] = b
        self._emit("backlog_submit", backlog=b.backlog_id, by=int(submitter),
                   forced=bool(force), body=text[:200])
        return b

    # ── 규칙 ① 지명 ───────────────────────────────────────────────────────
    def pick(self, picker: int, backlog_id: str, assignee: int) -> Backlog:
        """마무리자(턴 홀더)가 다음 (백로그, 수행자)를 정한다. 자기 지명 허용(자기 일 고르기).
        blocked 백로그의 pick = 재방문(재개)이다 — 차단 이력(block_count)은 보존된다.
        [순차 1활성(2026-07-14, 사용자: '순차 돌리기')] 이미 다른 백로그가 in_progress면 새 착수 거부 —
        한 번에 한 백로그만. 배분권·첫-자기착수 같은 정책은 도구층(pick_backlog)이 얹는다."""
        self._guard_open()
        b0 = self.get(backlog_id)
        # [돌발 자기착수(2026-07-13)] 자기가 제출한 항목을 자기가 집는 건 배분권 밖.
        # [무주 자기선택(2026-07-16)] 백로그 회의 수렴안이 만든 팀 산물(submitter=0=SYS 서기)은 누구의
        # 것도 아니다 — '집는 사람이 한다'(자기선택)로 전담이 붙는다. 종전엔 submitter==picker 조건이라
        # 무주 항목은 self-claim 불가 + 선정 시 수행자=제출자(0=SYS)로 배분이 깨졌다(회의→릴레이 접합 결함).
        _self_claim = (int(picker) == int(assignee)
                       and int(b0.submitter or 0) in (0, int(picker)))
        # 순차 잠금 — 이미 누가 작업 중이면 새 착수 불가(그 완료/중단 후 다음).
        _active = next((x for x in self.backlogs if x.status == IN_PROGRESS and x.backlog_id != backlog_id), None)
        if _active is not None:
            raise BacklogError(f"{_active.backlog_id}가 작업 중입니다(순차 1활성) — 그 완료/중단 뒤 다음이 선정됩니다.")
        if not _self_claim and self.turn_holder is not None and int(picker) != self.turn_holder:
            raise BacklogError(
                f"배분권은 마지막 작업자({self.turn_holder})에게 있습니다 — 지명은 마무리한 사람의 몫.")
        b = self.get(backlog_id)
        if b.status not in (OPEN, BLOCKED):
            raise BacklogError(f"{b.backlog_id}는 {b.status} — 지명은 open/blocked만 가능합니다.")
        b.status, b.assignee = IN_PROGRESS, int(assignee)
        # 재방문 창은 과거 작업 창과 분리한다. 종전 ``or``는 첫 선정 시각을 영구 보존해 새 생각이
        # 예전 창에 섞이고, ts_done도 남아 UI가 작업 중 항목을 이미 끝난 것으로 볼 수 있었다.
        b.ts_pick, b.ts_done = time.time(), 0.0
        self._emit("relay_pick", backlog=b.backlog_id, by=int(picker), to=int(assignee),
                   revisit=b.block_count > 0)
        return b

    def decline(self, assignee: int, backlog_id: str, reason: str = "") -> Backlog:
        """지명 거절 — 백로그를 open으로 되돌린다. 다음 경로는 응찰(규칙 ②)이 정석."""
        b = self.get(backlog_id)
        if b.status != IN_PROGRESS or b.assignee != int(assignee):
            raise BacklogError(f"{b.backlog_id}의 현재 수행자만 거절할 수 있습니다.")
        b.status, b.assignee = OPEN, None
        b.note = f"거절({assignee}): {reason}"[:200] if reason else b.note
        return b

    # ── 규칙 ② 응찰 ───────────────────────────────────────────────────────
    def bid_round(self, backlog_id: str, bids: Dict[int, int]):
        """응찰 한 라운드의 선정 규칙(순수). 점수 수집(봇 발화 [응찰: N] 파싱)은 호출부 몫.

        반환: (winner Backlog, None)      — 유일 최고점 → 즉시 배정
              (None, [동률 후보들])       — 동률 → 배정 없음, 해소는 결정권자 권한 ②(§1)
              (None, None)               — 무응찰 → 규칙 ③(pass_to)로
        """
        self._guard_open()
        b = self.get(backlog_id)
        if b.status not in (OPEN, BLOCKED):
            raise BacklogError(f"{b.backlog_id}는 {b.status} — 응찰 대상이 아닙니다.")
        clean = {int(k): int(v) for k, v in (bids or {}).items() if int(v) > 0}   # 0점 = 패스
        if not clean:
            self._emit("relay_bid", backlog=b.backlog_id, bids={}, winner=None, outcome="no_bids")
            return None, None
        top = max(clean.values())
        winners = sorted(k for k, v in clean.items() if v == top)
        if len(winners) > 1:                     # 동률 — 여기서 임의로 깨지 않는다(결정권자 권한 ②)
            self._emit("relay_bid", backlog=b.backlog_id, bids=clean, winner=None,
                       outcome="tie", tie=winners)
            return None, winners
        w = winners[0]
        b.status, b.assignee = IN_PROGRESS, w
        b.ts_pick, b.ts_done = time.time(), 0.0
        self._emit("relay_bid", backlog=b.backlog_id, bids=clean, winner=w, outcome="won")
        return b, None

    def resolve_tie(self, decider: int, backlog_id: str, assignee: int) -> Backlog:
        """동률 해소 — 결정권자 권한 ②(§1)의 표면. 배정만 하고 턴 구조는 건드리지 않는다."""
        b = self.get(backlog_id)
        if b.status not in (OPEN, BLOCKED):
            raise BacklogError(f"{b.backlog_id}는 {b.status} — 동률 해소 대상이 아닙니다.")
        b.status, b.assignee = IN_PROGRESS, int(assignee)
        b.ts_pick, b.ts_done = time.time(), 0.0
        self._emit("relay_pick", backlog=b.backlog_id, by=int(decider), to=int(assignee),
                   revisit=b.block_count > 0, tie_break=True)
        return b

    # ── 규칙 ③ 무응찰 = 마지막 작업자가 지정 ─────────────────────────────
    def pass_to(self, last_worker: int, backlog_id: str, assignee: int) -> Backlog:
        """무응찰이 돌아오면 마지막에 일한 작업자(턴 홀더)가 지정한다(§3 확정 — 결정권자 개입 없음).
        응찰이 또 무산돼도 같은 사람이 재지정 — 배분은 끝까지 현장 몫."""
        self._guard_open()
        if self.turn_holder is None:
            raise BacklogError("아직 마무리자가 없습니다 — 첫 배분은 pick(첫 턴은 홀더 제약 없음)으로.")
        if int(last_worker) != self.turn_holder:
            raise BacklogError(f"무응찰 지정권은 마지막 작업자({self.turn_holder})에게 있습니다.")
        b = self.get(backlog_id)
        if b.status not in (OPEN, BLOCKED):
            raise BacklogError(f"{b.backlog_id}는 {b.status} — 지정 대상이 아닙니다.")
        b.status, b.assignee = IN_PROGRESS, int(assignee)
        b.ts_pick, b.ts_done = time.time(), 0.0
        self._emit("relay_pass_to", backlog=b.backlog_id, by=int(last_worker), to=int(assignee),
                   reason="no_bids")
        return b

    # ── 완료 / 차단 ───────────────────────────────────────────────────────
    def done(self, worker: int, backlog_id: str, note: str = "") -> Backlog:
        """수행자가 자기 백로그를 마무리 — 그가 새 턴 홀더(다음 배분권자)가 된다."""
        b = self.get(backlog_id)
        if b.status != IN_PROGRESS or b.assignee != int(worker):
            raise BacklogError(f"{b.backlog_id}의 현재 수행자만 완료할 수 있습니다"
                               f"(현재: {b.status}/{b.assignee}).")
        b.status = DONE
        b.ts_done = time.time()
        if note:
            b.note = str(note)[:300]
        self.turn_holder = int(worker)
        self._emit("backlog_done", backlog=b.backlog_id, by=int(worker))
        return b

    def drop(self, worker: int, backlog_id: str, reason: str = "") -> Backlog:
        """[중단(2026-07-14, 사용자: '개인이 올린거니 중지가 아니라 중단으로 — 처리에서 제외')]
        본인(수행자 또는 제출자)이 완수 불가로 판단한 백로그를 장부에서 종결 제외한다. blocked와 달리
        재방문 없음. 중단자도 마무리자와 동일하게 새 턴 홀더(다음 선정의 담당자)가 된다."""
        b = self.get(backlog_id)
        if int(worker) not in (int(b.assignee or 0), int(b.submitter)):
            raise BacklogError(f"{b.backlog_id}는 본인(수행자/제출자)만 중단할 수 있습니다.")
        if b.status in (DONE, DROPPED):
            raise BacklogError(f"{b.backlog_id}는 이미 {b.status} — 중단 대상이 아닙니다.")
        _was_active = (b.status == IN_PROGRESS)   # 실제 착수분의 중단만 '마무리' — 배분권 이동
        b.status = DROPPED
        b.ts_done = time.time()
        b.note = (f"중단({worker}): {reason}"[:300] if reason else b.note)
        # [배분권 우회 봉합(2026-07-14, 정합 감사)] 착수(in_progress)한 백로그의 중단만 마무리자 자격 —
        # 대기 중 멤버가 자기 미착수(OPEN) 백로그를 버려 turn_holder(배분권)를 탈취하던 것 차단.
        if _was_active:
            self.turn_holder = int(worker)
        self._emit("backlog_dropped", backlog=b.backlog_id, by=int(worker), reason=str(reason or "")[:120])
        return b

    def block(self, worker: int, backlog_id: str, next_starter: int, reason: str = ""):
        """차단 핸드오프(§3 확정): 선행 필요 발견 → 이 백로그는 blocked로 **보존**(버리지 않는다),
        차단자가 '다음 시작자'를 지정한다(그가 새 턴 홀더 — 배분이 현장에 남는 또 하나의 경로).

        교착(§3 제안): 같은 백로그에 차단이 DEADLOCK_BLOCKS번째 쌓이면(차단→재방문→또 차단)
        deadlock_signal을 방출하고 True를 함께 돌려준다 — 중재(결정권자 권한 ③)로의 라우팅은 호출부 몫.
        반환: (Backlog, deadlock: bool)
        """
        self._guard_open()
        b = self.get(backlog_id)
        if b.status != IN_PROGRESS or b.assignee != int(worker):
            raise BacklogError(f"{b.backlog_id}의 현재 수행자만 차단을 선언할 수 있습니다.")
        b.status, b.block_count = BLOCKED, b.block_count + 1
        b.ts_done = time.time()
        b.block_reason = str(reason or "")[:300]
        self.turn_holder = int(next_starter)
        self._emit("relay_block", backlog=b.backlog_id, by=int(worker), next=int(next_starter),
                   reason=b.block_reason, block_count=b.block_count)
        deadlock = b.block_count >= DEADLOCK_BLOCKS
        if deadlock:
            self._emit("deadlock_signal", backlog=b.backlog_id, block_count=b.block_count,
                       reason=b.block_reason)
        return b, deadlock

    # ── SubTask iter (§2 — 완수조건 충족 → 잔여 정리 → 종료) ─────────────
    def close_iter(self) -> List[Backlog]:
        """완수조건 충족 판정(run 실증 게이트 — S1 소유) **이후** 호출된다. 잔여 백로그를 정리하고
        풀을 닫는다. 잔여는 done으로 참칭하지 않고 상태 그대로 + note만 남긴다(정직 — 완료와 정리는
        다른 단어다). 반환 = 정리된 잔여 목록(디스크 장부 .collab/MILESTONES.md 기록은 호출부 몫)."""
        if self.closed:
            raise BacklogError("이미 종료된 풀입니다.")
        left = self.remaining()
        for b in left:
            b.note = (b.note + " | " if b.note else "") + "iter 종료 정리(완수조건 충족, 미착수 잔여)"
        self.closed = True
        return left

    def _guard_open(self) -> None:
        if self.closed:
            raise BacklogError("이 SubTask의 백로그 풀은 종료되었습니다(iter 완료).")

    # ── 저장 (§9 최대 저장 — 크래시·재시작 후 릴레이 중간 재개) ──────────
    def to_ckpt(self) -> dict:
        """상태 전부를 JSON-안전 dict로 — flow _ckpt에 그대로 동승한다(restore_chain 관례 동형)."""
        return {"subtask_id": self.subtask_id, "seq": self._seq, "closed": self.closed,
                "turn_holder": self.turn_holder,
                "backlogs": [b.to_dict() for b in self._pool.values()]}

    @staticmethod
    def from_ckpt(d: dict, log: Optional[Callable] = None) -> "BacklogRelay":
        r = BacklogRelay(subtask_id=str((d or {}).get("subtask_id") or ""), log=log)
        r._seq = int((d or {}).get("seq") or 0)
        r.closed = bool((d or {}).get("closed"))
        th = (d or {}).get("turn_holder")
        r.turn_holder = int(th) if th is not None else None
        for bd in (d or {}).get("backlogs") or []:
            b = Backlog.from_dict(bd)
            r._pool[b.backlog_id] = b
        return r


# ══ 파이프라인 배선 — 위임축 접점 + SubTask iter 연동 (통합주기 2) ═══════════════
#
# 여기부터는 흐름(flow)과 S1 실물(Milestone/SubTask)에 붙는 층이다. 전부 pipeline_on() 뒤 —
# 플래그 미설정이면 모든 함수가 즉시 no-op/None이라 기존 동작 불변.
# 원리: 릴레이는 위임을 '대체'하지 않는다 — 위임(request)은 그대로 전송이고, 릴레이는 그 위임축의
# **장부와 턴 규칙**이다(누가 배분권을 쥐고 있고, 어느 백로그가 누구 손에 있나). 물리는 얇게.

_BACKLOG_MARK = re.compile(
    r"\[\s*백로그\s+(?:(?P<st>[^\]\s]+?)::)?(?P<bl>B\d+)\s*\]",
    re.IGNORECASE,
)


def backlog_rows(flow):
    """열린 마일스톤의 기존 릴레이 장부를 ``(SubTask, Relay, Backlog)``로 전수 조회한다.

    조회만 하는 공용 경계다. 릴레이를 새로 만들지 않으므로 단순 위임·완료 확인이 장부 상태를
    바꾸지 않는다. B번호는 SubTask마다 다시 시작하므로 전역 규칙은 항상 이 쌍을 보존한다.
    """
    if not getattr(flow, "milestones", None):
        return []
    ms = next_milestone(flow)
    if ms is None:
        return []
    store = getattr(flow, "backlog_relays", None) or {}
    out = []
    for st in (getattr(ms, "subtasks", None) or []):
        if getattr(st, "status", "") in ("done", "superseded"):
            continue
        relay = store.get(st.st_id)
        if relay is None:
            continue
        out.extend((st, relay, b) for b in relay.backlogs)
    return out


def active_backlog_rows(flow):
    """열린 마일스톤 전체의 실제 작업 중 백로그. 정상 장부라면 길이는 최대 1이다."""
    return [row for row in backlog_rows(flow) if row[2].status == IN_PROGRESS]


def normalize_active_backlogs(flow):
    """구버전 체크포인트의 다중 active를 최초 획득 우선으로 단일화한다.

    전역 잠금 도입 전에는 서로 다른 SubTask가 각각 local pick되어 둘 이상 작업 중일 수 있었다.
    가장 먼저 획득한 창을 보존하고 뒤늦게 겹친 창은 대기로 되돌린다. 차단 이력이 있는 항목은
    선행을 우회하지 않도록 blocked로 보수 복구한다. 반환은 ``(kept, reopened_rows)``다.
    """
    active = active_backlog_rows(flow)
    if len(active) <= 1:
        return (active[0] if active else None), []

    indexed = list(enumerate(active))
    _idx, kept = min(
        indexed,
        key=lambda item: (
            float(getattr(item[1][2], "ts_pick", 0) or float("inf")),
            item[0],
        ),
    )
    reopened = []
    for _st, _relay, backlog in active:
        if backlog is kept[2]:
            continue
        if int(getattr(backlog, "block_count", 0) or 0) > 0:
            backlog.status = BLOCKED
            backlog.ts_done = time.time()
        else:
            backlog.status = OPEN
            backlog.assignee = None
            backlog.ts_pick = 0.0
        backlog.note = (
            (str(getattr(backlog, "note", "") or "") + " | ")
            if getattr(backlog, "note", "") else ""
        ) + "전역 1활성 복구로 대기 전환"
        reopened.append((_st, _relay, backlog))
    if reopened and getattr(flow, "log", None):
        try:
            flow.log(
                "backlog_active_normalized",
                kept=backlog_scope_key(kept[0].st_id, kept[2].backlog_id),
                reopened=" ".join(
                    backlog_scope_key(st.st_id, b.backlog_id)
                    for st, _relay, b in reopened)[:240],
            )
        except Exception:
            pass
    return kept, reopened


def active_subtask(flow):
    """실제 작업 중 SubTask 우선, 없으면 첫 미완 SubTask. 없으면 None."""
    if not getattr(flow, "milestones", None):
        return None
    ms = next_milestone(flow)
    if ms is None:
        return None
    active = active_backlog_rows(flow)
    if len(active) == 1:
        return active[0][0]
    for st in ms.subtasks:
        if st.status not in ("done", "superseded"):
            return st
    return None


def relay_for(flow, st) -> BacklogRelay:
    """SubTask의 릴레이 get-or-create. 상태는 flow.backlog_relays(dict)에 살고, sys_recovery가
    체크포인트에 통째로 실어 재시작 후 중간 재개한다(§9). S1 접점 유지: st.backlog_ids에는
    연결 id만 미러(표현은 여기, 연결은 S1 필드 — milestone.py SubTask 주석 그대로)."""
    store = getattr(flow, "backlog_relays", None)
    if store is None:
        store = flow.backlog_relays = {}
    r = store.get(st.st_id)
    if r is None:
        r = store[st.st_id] = BacklogRelay(subtask_id=st.st_id, log=getattr(flow, "log", None))
    else:
        r._log = getattr(flow, "log", None)      # ckpt 복원분은 log가 비어 있다 — 접근 시 재바인딩
    def _persist():
        from .milestone import persist_ms_status
        persist_ms_status(flow)
    r._on_change = _persist
    st.backlog_ids = [b.backlog_id for b in r.backlogs]
    return r


def _match_backlog(relay: BacklogRelay, body: str) -> Optional[Backlog]:
    """위임 본문 → 백로그 매칭. 명시 마커([백로그 Bn])가 우선, 없으면 위임 중복 판정과 같은 어휘
    겹침(_body_overlap). done은 매칭 제외 — 완료물 재위임은 기존 Redo 기제의 몫이다."""
    m = _BACKLOG_MARK.search(str(body or ""))
    if m:
        st_hint = str(m.group("st") or "")
        if st_hint and not (
                relay.subtask_id == st_hint
                or relay.subtask_id.endswith(f"/{st_hint}")):
            return None
        b = relay._pool.get(m.group("bl").upper())
        return b if b is not None and b.status not in (DONE, DROPPED) else None
    for b in relay.backlogs:
        if b.status not in (DONE, DROPPED) and _body_overlap(body, b.body):
            return b
    return None


def _resolve_flow_backlog(flow, body: str, to: int):
    """위임 본문을 전역 ``(ST, B)``에 해석한다.

    반환은 ``(hit, error)``. 명시 ``[백로그 ST::B1]``가 최우선이며, 지역 ID만 썼을 때는
    현재 수행자·고유 제출자·본문 겹침으로 단 하나가 증명될 때만 고른다. 끝까지 둘 이상이면
    첫 SubTask를 추측하지 않고 거부한다.
    """
    text = str(body or "")
    rows = [row for row in backlog_rows(flow)
            if row[2].status not in (DONE, DROPPED)]
    mark = _BACKLOG_MARK.search(text)
    candidates = []
    if mark:
        backlog_id = mark.group("bl").upper()
        candidates = [row for row in rows if row[2].backlog_id.upper() == backlog_id]
        st_hint = str(mark.group("st") or "").strip()
        if st_hint:
            candidates = [
                row for row in candidates
                if row[0].st_id == st_hint or row[0].st_id.endswith(f"/{st_hint}")
            ]
            if len(candidates) == 1:
                return candidates[0], None
            if not candidates:
                return None, (
                    f"[릴레이] 지정한 범위 {st_hint}::{backlog_id}를 열린 장부에서 찾지 못했습니다.")
            return None, (
                f"[릴레이] {st_hint}::{backlog_id}가 여러 단위에 걸쳐 모호합니다 — 전체 SubTask ID를 쓰세요.")
    else:
        candidates = [row for row in rows if _body_overlap(text, row[2].body)]
        if not candidates:
            return None, None

    if len(candidates) == 1:
        return candidates[0], None

    assigned = [
        row for row in candidates
        if row[2].status == IN_PROGRESS and int(row[2].assignee or 0) == int(to)
    ]
    if len(assigned) == 1:
        return assigned[0], None
    owned = [row for row in candidates if int(row[2].submitter or 0) == int(to)]
    if len(owned) == 1:
        return owned[0], None
    # 마커를 제외한 실제 위임 문장이 한 후보에만 겹치면 그 범위가 증명된다.
    prose = _BACKLOG_MARK.sub(" ", text)
    overlap = [row for row in candidates if _body_overlap(prose, row[2].body)]
    if len(overlap) == 1:
        return overlap[0], None
    options = " · ".join(
        f"{st.st_id}::{b.backlog_id}" for st, _relay, b in candidates[:8])
    return None, (
        f"[릴레이] 백로그 범위가 여러 단위에 걸쳐 모호합니다({options}). "
        f"`[백로그 SubTask-ID::{candidates[0][2].backlog_id}]`처럼 범위를 명시하세요.")


def sync_delegation(flow, me_id, to, body) -> Optional[str]:
    """[위임축 접점 — communication.request가 게이트들 뒤에서 호출] Work 위임을 릴레이에 맞춘다.

    반환: None=통과 / 문자열=거부 사유(다른 게이트와 같은 코칭 반환).
    - 본문이 백로그를 가리키면 그 위임이 곧 배분이다 — 턴 규칙(§3: 배분권은 마무리자)을 검증하고
      릴레이 장부를 전이시킨다(relay_pick 이벤트·participants·backlog_ids 동기).
    - 백로그 밖 위임(일반 협의·과도기 작업)은 그대로 통과 — 릴레이는 강제 전면화가 아니라 장부다.
    - 같은 (백로그, 수행자) 재전달(이어가기·Redo)은 장부 무변화로 통과.
    """
    if not pipeline_on():
        return None
    hit, error = _resolve_flow_backlog(flow, body, int(to))
    if error:
        return error
    if hit is None:
        return None
    st, r, b = hit
    # (2026-07-13 '재발송 합류' 분기는 자기 등재 원칙으로 흡수 — 매칭 없는 위임은 아래서 전부 통과라
    #  재전송이 새 항목을 낳을 자동 등재 자체가 없다.)
    if b.status == IN_PROGRESS:
        if b.assignee == int(to):
            return None                          # 같은 배분 재전달(이어가기) — 장부 그대로
        return (f"[릴레이] {b.backlog_id}는 지금 {getattr(flow, '_info', lambda x: x)(b.assignee)} "
                f"손에 있습니다(in_progress) — 겹침 방지. 그의 완료·차단을 기다리거나 다른 백로그를 맡기세요.")
    active = active_backlog_rows(flow)
    if active:
        ast, _ar, ab = active[0]
        return (f"[릴레이] {ast.st_id}::{ab.backlog_id}가 작업 중입니다(마일스톤 전체 순차 1활성) — "
                f"그 완료·차단 뒤 {st.st_id}::{b.backlog_id}를 선정하세요.")
    if b.status == BLOCKED:
        rows = backlog_rows(flow)
        if not blocked_ready_for_revisit(
                b, [row[2] for row in rows], backlog_scope_key(st.st_id, b.backlog_id)):
            return (f"[릴레이] {st.st_id}::{b.backlog_id}는 선행 작업으로 차단됐습니다 — "
                    f"연결된 보충 백로그가 모두 종결되고 실제 완료 증거가 생긴 뒤 재개하세요.")
    if r.turn_holder is not None and int(me_id) != r.turn_holder:
        holder = getattr(flow, "_info", lambda x: x)(r.turn_holder) or r.turn_holder
        return (f"[릴레이] 배분권은 마지막 작업자({holder})에게 있습니다(§3 — 배분은 현장이 끝까지). "
                f"백로그 {b.backlog_id} 지정은 그의 몫입니다. 당신이 맡고 싶으면 응찰([응찰: N])로 나서세요.")
    try:
        r.pick(int(me_id), b.backlog_id, int(to))
    except BacklogError as e:
        return f"[릴레이] {e}"
    try:
        st.participants.add(int(to))
        st.participants.add(int(me_id))
        st.backlog_ids = [x.backlog_id for x in r.backlogs]
    except Exception:
        pass
    return None


def handoff_note(flow, r, actor, verb) -> None:
    """[다음 선정 / 백로그 소진(2026-07-14, 사용자: '끝나거나 중단되면 응찰 → 담당자 선정',
    '백로그 다 돌고 서브태스크·백로그 회의')] 백로그 종결(완료/중단) 순간:
      · 남은 백로그 있으면 → [다음 선정] 공고(응찰 → 마무리자 선정).
      · 풀 소진(남은 것 0) → [백로그 소진] 회의 코칭 — 조건 확인 후 미충족이면 meet 추가 단위 or
        vote_stop(중지 투표). 봇 혼자 판단 말고 팀 회의·표결로."""
    _info = getattr(flow, "_info", lambda x: x)
    notes = getattr(flow, "_pipeline_notes", None)
    if notes is None:
        notes = flow._pipeline_notes = []
    rem = [b for b in r.backlogs if b.status in (OPEN, BLOCKED)]
    if not rem:
        notes.append(f"[백로그 소진] {_info(actor)}의 백로그가 {verb} — 이 단위 백로그가 모두 종결됐습니다"
                     f"(완료/중단). report_iter로 마일스톤 완수조건을 확인하세요. 미충족이면 **meet**로 추가 "
                     f"단위('단위:' 줄)를 열거나, 해결 불가한 주제면 **vote_stop**(마일스톤/Task 중지 투표)으로 "
                     f"접으세요 — 혼자 판단 말고 팀 표결로.")
        return
    cand = " · ".join(f"{b.backlog_id}({_info(b.submitter)}: {b.body[:24]})" for b in rem[:8])
    # [등록 순서가 실행 순서(2026-07-29, 사용자 지시)] 종전 안내는 '내가 다음이어야 하는 이유를 알리라'는
    # 로비를 요구했다 — 순서가 이미 회의에서 정해졌다면 그 라운드는 낭비이고, 순서를 흔들기까지 한다.
    # 기본은 등재 순서대로 자동 인계이고, pick_backlog(id)는 그 순서를 **벗어나야 할 때**의 장치다.
    notes.append(f"[다음 선정] {_info(actor)}의 백로그가 {verb} — 다음은 **등재 순서대로** "
                 f"{rem[0].backlog_id}({_info(rem[0].submitter)})에게 넘어갑니다. 순서를 바꿔야 할 사유가 "
                 f"있을 때만 담당자({_info(actor)})가 pick_backlog(id)로 다른 항목을 지정하세요. "
                 f"남은 백로그(순서대로): {cand}")


def sync_completion(flow, worker) -> None:
    """[위임축 접점 — owner 실작업 인도 확정 지점에서 호출] 그 수행자의 in_progress 백로그를
    done으로 — 그가 새 턴 홀더(다음 배분권자)가 된다(backlog_done 이벤트). 백로그 밖 위임이면 no-op."""
    if not pipeline_on():
        return
    hits = [
        row for row in active_backlog_rows(flow)
        if int(row[2].assignee or 0) == int(worker)
    ]
    if len(hits) != 1:
        if len(hits) > 1 and getattr(flow, "log", None):
            flow.log("backlog_completion_ambiguous", by=int(worker), n=len(hits))
        return
    st, r, b = hits[0]
    try:
        r.done(int(worker), b.backlog_id)
        st.participants.add(int(worker))
        handoff_note(flow, r, worker, "완료됐습니다")
    except BacklogError:
        pass


def on_subtask_wrapup(flow, st) -> str:
    """[SubTask iter 연동 — §2] iter_verify 통과(status=wrapup) 후 호출: 잔여 백로그 정리 +
    디스크 장부(.collab/MILESTONES.md, §9 미러). 반환 = 장부 요지 한 줄(호출부가 채널 보고에 씀).

    호출자는 iter 구동부(S1) — wrapup_done(정리 완료 선언) **앞**에 이걸 부른다. 계약 §12-1 코멘트.
    """
    r = (getattr(flow, "backlog_relays", None) or {}).get(st.st_id)
    if r is None or r.closed:
        return "정리할 백로그 없음"
    left = r.close_iter()
    done_n = len([b for b in r.backlogs if b.status == DONE])
    lines = [f"## SubTask {st.st_id} 백로그 장부 — iter {st.iter_n} 종료(완수조건 충족)",
             f"완료 {done_n} · 잔여 정리 {len(left)}"]
    lines += [f"- [{b.status}] {b.backlog_id} {b.body[:80]} (수행: {b.assignee}, 차단 {b.block_count}회)"
              for b in r.backlogs]
    try:
        from .._util import dossier_append
        dossier_append(flow, "MILESTONES.md", "\n".join(lines))
    except Exception:
        pass                                     # 장부 실패가 정리를 죽이지 않는다(§9 미러는 보조)
    return f"백로그 {done_n} 완료, 잔여 {len(left)}건 정리(미완은 done 참칭 없이 보존)"
