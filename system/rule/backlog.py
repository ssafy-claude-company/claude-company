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

# 상태 4종 (§3 확정 — 이 밖의 상태 없음)
OPEN, IN_PROGRESS, BLOCKED, DONE = "open", "in_progress", "blocked", "done"

# 교착 판정(§3 제안): 같은 백로그가 차단으로 2회 재방문 = 차단이 2번째 쌓이는 순간.
# (1차 차단 → 핸드오프로 재방문·재개 → 또 차단 = 선행 해소가 안 돌고 있다는 신호)
DEADLOCK_BLOCKS = 2


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
    ts_pick: float = 0.0         # [창 귀속(2026-07-10)] 선정 시각 — 이 시각부터의 대화가 이 백로그
    ts_done: float = 0.0         # 완료/차단 시각 — 창의 끝

    def to_dict(self) -> dict:
        return {"backlog_id": self.backlog_id, "body": self.body, "submitter": int(self.submitter),
                "status": self.status, "assignee": self.assignee, "block_count": self.block_count,
                "block_reason": self.block_reason, "note": self.note,
                "ts_pick": self.ts_pick, "ts_done": self.ts_done}

    @staticmethod
    def from_dict(d: dict) -> "Backlog":
        return Backlog(backlog_id=str(d.get("backlog_id")), body=str(d.get("body") or ""),
                       submitter=int(d.get("submitter") or 0), status=str(d.get("status") or OPEN),
                       ts_pick=float(d.get("ts_pick") or 0), ts_done=float(d.get("ts_done") or 0),
                       assignee=d.get("assignee"), block_count=int(d.get("block_count") or 0),
                       block_reason=str(d.get("block_reason") or ""), note=str(d.get("note") or ""))


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
        """아직 done이 아닌 것 전부(open/in_progress/blocked)."""
        return [b for b in self._pool.values() if b.status != DONE]

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
        if not force:
            for ex in self._pool.values():                       # 제출 순서 = 판정 순서(결정성)
                if _body_overlap(text, ex.body):
                    raise DuplicateBacklog(
                        f"기존 백로그 {ex.backlog_id}(와)과 실질 중복으로 보입니다 — 같은 일이면 그쪽에 "
                        f"합류하고, 정말 다른 일이면 force로 명시해 다시 제출하세요.\n"
                        f"기존: {ex.body[:120]}", existing_id=ex.backlog_id)
        self._seq += 1
        b = Backlog(backlog_id=f"B{self._seq}", body=text, submitter=int(submitter))
        self._pool[b.backlog_id] = b
        self._emit("backlog_submit", backlog=b.backlog_id, by=int(submitter),
                   forced=bool(force), body=text[:200])
        return b

    # ── 규칙 ① 지명 ───────────────────────────────────────────────────────
    def pick(self, picker: int, backlog_id: str, assignee: int) -> Backlog:
        """마무리자(턴 홀더)가 다음 (백로그, 수행자)를 정한다. 자기 지명 허용(자기 일 고르기).
        blocked 백로그의 pick = 재방문(재개)이다 — 차단 이력(block_count)은 보존된다."""
        self._guard_open()
        b0 = self.get(backlog_id)
        # [돌발 자기착수(2026-07-13)] 자기가 제출한 항목을 자기가 집는 건 배분권 밖 —
        # 작업 턴 백로그 게이트(집지 않으면 실행 불가)가 배분권 대기로 교착하지 않는 출구.
        _self_claim = (b0.submitter == int(picker) == int(assignee))
        if not _self_claim and self.turn_holder is not None and int(picker) != self.turn_holder:
            raise BacklogError(
                f"배분권은 마지막 작업자({self.turn_holder})에게 있습니다 — 지명은 마무리한 사람의 몫.")
        b = self.get(backlog_id)
        if b.status not in (OPEN, BLOCKED):
            raise BacklogError(f"{b.backlog_id}는 {b.status} — 지명은 open/blocked만 가능합니다.")
        b.status, b.assignee = IN_PROGRESS, int(assignee)
        b.ts_pick = b.ts_pick or time.time()
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
        self._emit("relay_bid", backlog=b.backlog_id, bids=clean, winner=w, outcome="won")
        return b, None

    def resolve_tie(self, decider: int, backlog_id: str, assignee: int) -> Backlog:
        """동률 해소 — 결정권자 권한 ②(§1)의 표면. 배정만 하고 턴 구조는 건드리지 않는다."""
        b = self.get(backlog_id)
        if b.status not in (OPEN, BLOCKED):
            raise BacklogError(f"{b.backlog_id}는 {b.status} — 동률 해소 대상이 아닙니다.")
        b.status, b.assignee = IN_PROGRESS, int(assignee)
        b.ts_pick = b.ts_pick or time.time()
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
        b.ts_pick = b.ts_pick or time.time()
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

_BACKLOG_MARK = re.compile(r"\[\s*백로그\s+(B\d+)\s*\]")


def active_subtask(flow):
    """진행 중 마일스톤의 첫 미완 SubTask — 지금 릴레이가 붙는 판. 없으면 None."""
    if not getattr(flow, "milestones", None):
        return None
    ms = next_milestone(flow)
    if ms is None:
        return None
    for st in ms.subtasks:
        if st.status != "done":
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
        b = relay._pool.get(m.group(1))
        return b if b is not None and b.status != DONE else None
    for b in relay.backlogs:
        if b.status != DONE and _body_overlap(body, b.body):
            return b
    return None


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
    st = active_subtask(flow)
    if st is None:
        return None
    r = relay_for(flow, st)
    b = _match_backlog(r, body)
    if b is None:
        # [백로그 의무화(2026-07-10, 사용자: 'SubTask 하위로 그냥 일하는 것처럼 보인다 — 아니어야지')]
        # 장부 밖 Work 위임을 통과시키던 선택제가 계층(ST→백로그→작업)을 비웠다 — 매칭 없는 위임은
        # 거부(마찰) 대신 **자동 제출**로 장부에 등재하고 그 위임을 곧 배분으로 만든다(장부=진실).
        try:
            b = r.submit(int(me_id), str(body or "")[:200], force=True)
            r.pick(int(me_id), b.backlog_id, int(to))
            st.participants.add(int(to)); st.participants.add(int(me_id))
            st.backlog_ids = [x.backlog_id for x in r.backlogs]
        except Exception:
            pass
        return None
    if b.status == IN_PROGRESS:
        if b.assignee == int(to):
            return None                          # 같은 배분 재전달(이어가기) — 장부 그대로
        return (f"[릴레이] {b.backlog_id}는 지금 {getattr(flow, '_info', lambda x: x)(b.assignee)} "
                f"손에 있습니다(in_progress) — 겹침 방지. 그의 완료·차단을 기다리거나 다른 백로그를 맡기세요.")
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


def sync_completion(flow, worker) -> None:
    """[위임축 접점 — owner 실작업 인도 확정 지점에서 호출] 그 수행자의 in_progress 백로그를
    done으로 — 그가 새 턴 홀더(다음 배분권자)가 된다(backlog_done 이벤트). 백로그 밖 위임이면 no-op."""
    if not pipeline_on():
        return
    st = active_subtask(flow)
    if st is None:
        return
    r = (getattr(flow, "backlog_relays", None) or {}).get(st.st_id)
    if r is None:
        return
    for b in r.backlogs:
        if b.status == IN_PROGRESS and b.assignee == int(worker):
            try:
                r.done(int(worker), b.backlog_id)
                st.participants.add(int(worker))
            except BacklogError:
                pass
            break


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
