"""Floor Rule — 대화 구조(발언권 순환)의 추상 seam (순수 로직 — 네트워크·LLM·매체 없음).

CA-Lab [RFC-003] '대화 구조의 3층 모델'의 **1층(발언권 순환)** 이식:
대화의 기초 기계장치는 내용이 아니라 '누가 다음에 말하는가'다(Sacks·Schegloff·Jefferson 1974,
"locally managed, party-administered"). 이 모듈은 그 배분을 **교체 가능한 정책(FloorPolicy)** 으로
추상화한다 — 같은 대화 엔진(run_conversation) 위에서 구조를 turn-taking ↔ request-response ↔
orchestrated 로 언제든 갈아끼울 수 있어야 한다는 요구의 자리.

정책 3종 = 기존 시스템에 자리별로 하드코딩돼 있던 3구조의 정책화:
  - RequestResponseFloor: 지목=요청(스택 push), 무지목=응답(LIFO pop→요청자 복귀), 스택 비면 종료.
    CommunicationManager(단일활성 베턴)의 배분 의미론과 동형 — 동치성은 test_floor가 나란히 실행해 증명.
  - TurnTakingFloor: Sacks 순서교대 — ①현재 화자가 지명하면 그 사람 ②지명 없으면 자기선택(open):
    **후보 봇들이 각자 LLM으로 '지금 내가 발언해야 하나'를 판정해 응찰(bid)** — 관련성 판단은
    봇의 지능이고, 정책은 승자 선정 규칙(최고 응찰, 동률=침묵 오래된 순)만 갖는다 ③무응찰이면
    종결 확인 표결 — [계속: N]=발언 의무 진 반대, 전원 [종료]=합의 종결(EXP-002 절제로 '계속' 대체).
  - OrchestratedFloor: 사회자(allocator 콜백)가 전량 배분 — 회의 라운드·SYS 중앙 배분의 정책화.

순수성 계약: 정책은 상태(FloorState)·방금 끝난 턴(Turn)·응찰 결과(bids)만 보고 배분(Allocation)을
돌려준다. 응찰 수집(후보 봇 병렬 wake)·화자 깨우기 같은 IO는 전부 소비자(엔진 콜백)의 몫이다 —
1층은 배분의 기계만 책임진다(2층 스탠스·3층 시퀀스는 후속 — 이 모듈에 넣지 않는다).
"""
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# 배분 종류 상수 — 관측(flow.jsonl)과 테스트가 같은 문자열을 본다.
NOMINATE = "nominate"   # ① 지명(현재 화자가 다음을 정함) / RR의 요청·복귀도 이 종류
OPEN = "open"           # ② 자기선택 오퍼 열림(후보 순서 제안)
SELF = "self"           # ② 자기선택 성립(후보가 발언권을 가져감) — 엔진이 관측용으로 기록
CLOSE_VOTE = "close_vote"  # 종결 확인 — 무응찰 시 참여자 합의로 닫는다([계속]=발언 의무 반대)
CLOSE = "close"         # 대화 종결
# [EXP-002 절제] 종전 ③'현재 화자 계속'(CONTINUE)은 제거 — Sacks 1(c)의 전제(발화 무비용·즉시)가
# 직렬 LLM 매체에 없고(계속도 wake 1회), 실측 p(실질 이어가기|무응찰)=0.5로 손익분기 이하이며,
# 이어가기 채널은 종결 표결의 [계속: N](발언 의무)이 대체한다. lapse 카운터·표결 1회 제한도 함께
# 제거 — 규칙은 "무응찰 → 표결, 전원 [종료]일 때만 종결" 하나. 반복 상한 = 소비자 wake_cap + 엔진 max_turns.

_MODES = ("request-response", "turn-taking", "orchestrated")


def floor_mode(explicit: Optional[str] = None, default: str = "request-response") -> str:
    """대화 구조 선택 — 명시 인자 > env ORGANT_FLOOR > 기본. 미설정/오타는 기본(현행 구조)으로
    폴백해 라이브 동작 불변(default-OFF 관례). '언제든 교체'는 이 한 값을 바꾸는 것으로 성립한다."""
    v = (explicit or os.environ.get("ORGANT_FLOOR") or "").strip().lower()
    return v if v in _MODES else default


@dataclass
class Turn:
    """끝난 턴 하나의 사실(TRP 입력). 정책은 body 내용을 해석하지 않는다 — 지명/패스 같은 신호의
    추출(프롬프트 규약·마커 파싱)은 매체 쪽 소비자 책임(1층=배분, 신호 해석은 표면 규약)."""
    speaker: int
    addressee: Optional[int] = None   # 이 턴이 지명한 다음 화자(Sacks ①의 입력). None=지명 없음
    passed: bool = False              # 패스(보탤 말 없음) — 무응찰/종결 판정 입력
    body: str = ""                    # 관측·회의록용 원문(정책 미사용)
    stype: Optional[str] = None       # 발화 타입(2층 stance.py 소관 — 1층 정책은 읽지 않는다)


@dataclass
class Allocation:
    """배분 결정 — '다음 발언권이 어디로 가는가'."""
    kind: str
    next: Optional[int] = None            # nominate/continue/self 의 다음 화자
    candidates: Tuple[int, ...] = ()      # open 의 오퍼 순서(정책이 공정성 반영해 제안)
    reason: str = ""                      # 관측용(왜 이 배분인가)


class FloorState:
    """한 대화(회의·세그먼트)의 발언권 상태 — 참여자·현재 화자·침묵 장부·소진 카운터.
    정책이 갱신 없이 읽고, 기록(record)은 엔진이 턴 확정 시에만 한다(단일 진실원)."""

    def __init__(self, participants, current: Optional[int] = None):
        self.participants: List[int] = [int(p) for p in participants]
        self.current = current            # 발언권 보유자(직전 확정 턴의 화자)
        self.turn_no = 0                  # 확정 턴 수(패스 오퍼는 세지 않음)
        self.last_spoken: Dict[int, int] = {}   # id → 마지막 실발언 턴번호(공정성 장부)
        self.history: List[Turn] = []

    def record(self, turn: Turn) -> None:
        self.turn_no += 1
        self.history.append(turn)
        self.current = turn.speaker
        if not turn.passed:
            self.last_spoken[turn.speaker] = self.turn_no

    def silence_order(self, exclude=()) -> Tuple[int, ...]:
        """자기선택 오퍼 순서 — 침묵 오래된 순(무발언자 최우선, 동률=참여자 등록순).
        인간 대화의 '첫 시작자'(반응속도·눈치)를 직렬 매체에서 결정론으로 근사한 것: 오래 침묵한
        사람에게 먼저 기회가 가므로 발언 분포가 쏠리지 않는다(공정성 = 1층이 책임지는 기능)."""
        ex = set(exclude)
        return tuple(sorted((p for p in self.participants if p not in ex),
                            key=lambda p: (self.last_spoken.get(p, -1), self.participants.index(p))))


class FloorPolicy:
    """배분 정책 인터페이스 — 소비자는 이 두 메서드만 보면 된다(구조 교체 = 구현체 교체)."""
    name = "abstract"

    def next_after(self, st: FloorState, turn: Turn) -> Allocation:
        """턴 종료(TRP)마다 호출 — 다음 배분을 돌려준다."""
        raise NotImplementedError

    def resolve_open(self, st: FloorState, turn: Turn, bids) -> Allocation:
        """open의 응찰 결과 판정 — bids=[(후보 id, 응찰 강도 int)]. 수집(IO)은 소비자가 하고
        여기는 선정 규칙만. open을 내는 정책만 의미 있게 구현한다."""
        return Allocation(CLOSE, reason="open 미사용 정책")

    def resolve_close_vote(self, st: FloorState, turn: Turn, bids) -> Allocation:
        """종결 확인 표결 판정 — close_vote를 내는 정책만 의미 있게 구현한다."""
        return Allocation(CLOSE, reason="close_vote 미사용 정책")


class TurnTakingFloor(FloorPolicy):
    """[1층 본체] Sacks 순서교대의 정책화 — 중앙 배분 없이 그 자리에서 돌린다.

    ① turn.addressee(지명)가 참여자면 → 그 사람 (NOMINATE)
    ② 지명 없으면 → 자기선택 (OPEN): 후보 봇들이 **각자 LLM으로 응찰**('지금 내가 발언해야 하나'
      + 강도 0~9)하고, 정책은 최고 응찰을 선정(동률 = 침묵 오래된 순 — 발언 분포 공정성).
      인간 대화의 '먼저 입을 떼는 사람'을, 동시발화가 없는 직렬 매체에서 '가장 강하게 발언을
      원하는 사람'으로 조작화한 것 — 관련성 판단은 봇의 지능, 선정 규칙만 정책.
    ③ 무응찰이면 → **종결 확인 표결**(CLOSE_VOTE, pre-closing — Schegloff & Sacks 1973):
      `[계속: N]` 반대는 발언 의무를 지고 발언권을 받고(이 채널이 종전 '현재 화자 계속'을 대체 —
      EXP-002 절제 판정), 전원 `[종료]`면 합의 종결. 표결은 무응찰마다 열린다 — 반복 상한은
      소비자 wake_cap·엔진 max_turns(폭주 백스톱). '교착 없이 돌고 끝나는가'(ADR-008)는 유지:
      매 반복이 배분 하나를 소비하고 max_turns가 절대 상한이다."""
    name = "turn-taking"

    def __init__(self):
        # [응찰 큐(2026-07-18, wake 축소)] 한 번의 응찰 수집에서 상위 K명을 순차 배분한다 — 종전엔
        # 발언 교체마다 후보 전원을 재프로브(수집당 패널 전원 LLM 마이크로 턴, 07-17 실측 1,835회/일 =
        # 마이크로 비용의 최대 항목)했다. 깊이 = ORGANT_BID_QUEUE(기본 1 = **현행과 동일**: 승자만 쓰고
        # 나머지 응찰은 버림 — 무회귀). 큐 수명 = 이 정책 인스턴스(= 회의 1개). 지명(①)은 항상 큐보다
        # 우선하고, 이탈자·방금 화자는 pop에서 걸러진다. 신선도 비용: 큐 소비자는 직전 수집 시점의
        # 자기판단으로 발언권을 받는다 — 깊이 2~3 권장(한두 발언 지연 내).
        try:
            self._qdepth = max(1, int(os.environ.get("ORGANT_BID_QUEUE", "1") or 1))
        except ValueError:
            self._qdepth = 1
        self._queue: List[int] = []

    def next_after(self, st: FloorState, turn: Turn) -> Allocation:
        a = turn.addressee
        if a is not None and a in st.participants and a != turn.speaker:
            return Allocation(NOMINATE, next=a, reason="①현재 화자 지명")
        while self._queue:                                   # [응찰 큐] 수집 없이 다음 응찰자 배분
            c = self._queue.pop(0)
            if c in st.participants and c != turn.speaker:
                return Allocation(SELF, next=c, reason="②자기선택 — 응찰 큐")
        cands = st.silence_order(exclude=(turn.speaker,))
        if cands:
            return Allocation(OPEN, candidates=cands, reason="②자기선택 응찰")
        return self.resolve_open(st, turn, [])              # 후보 0(독백 대화) → 종결 확인 경로

    def resolve_open(self, st: FloorState, turn: Turn, bids) -> Allocation:
        order = {c: i for i, c in enumerate(st.silence_order(exclude=(turn.speaker,)))}
        positive = [(c, int(s)) for c, s in (bids or []) if int(s) > 0 and c in order]
        if positive:
            # 최고 응찰 승 — 동률이면 침묵 오래된 순(order 앞) 우대. 차순위(2..K위)는 큐에 적재해
            # 다음 배분들이 재수집 없이 소비(깊이 1이면 종전대로 승자만).
            ranked = sorted(positive, key=lambda cs: (-cs[1], order[cs[0]]))
            c, s = ranked[0]
            self._queue = [cc for cc, _ in ranked[1:self._qdepth]]
            return Allocation(SELF, next=c, reason=f"②자기선택 — 응찰 {s}")
        self._queue = []
        return Allocation(CLOSE_VOTE, candidates=st.silence_order(),
                          reason="무응찰 — 종결 확인 표결")

    def resolve_close_vote(self, st: FloorState, turn: Turn, bids) -> Allocation:
        """종결 확인 판정 — [계속: N]은 곧 발언 의무를 진 응찰이다: 반대자가 발언권을 받아 직접
        말한다(말 없이 회의만 연장하는 무한 계속 차단 — 반대=발언). 전원 [종료]면 합의 종결."""
        self._queue = []                                     # [응찰 큐] 종결 국면 — 낡은 큐 방어적 폐기
        order = {c: i for i, c in enumerate(st.silence_order())}
        positive = [(c, int(s)) for c, s in (bids or []) if int(s) > 0 and c in order]
        if positive:
            c, s = max(positive, key=lambda cs: (cs[1], -order[cs[0]]))
            return Allocation(SELF, next=c, reason=f"종결 반대 — 계속 {s}(발언 의무)")
        return Allocation(CLOSE, reason="종결 합의 — 전원 [종료]")


class RequestResponseFloor(FloorPolicy):
    """[현행 구조의 정책화] 지목=요청, 무지목=응답. 단일활성 베턴·LIFO 스택과 배분 동형:
    - 지명 턴 = 요청: (요청자→수신자) 프레임 push, 발언권은 수신자에게.
    - 무지명 턴 = 응답: top 프레임 pop, 발언권은 그 요청자에게 복귀(LIFO).
    - 스택이 비면 종료(모든 요청 닫힘 = 흐름 origin 복귀와 동형).
    CommunicationManager 자체를 감싸지 않는 이유: 그쪽은 게이트·점유(Engagement)·복구까지 진
    라이브 엔진이고, 여기는 '배분 의미론'만 최소로 재기술한 것 — 동치성은 test_floor가
    같은 대본을 양쪽에 돌려 alive 시퀀스 일치로 증명한다(표류 시 테스트가 잡음)."""
    name = "request-response"

    def __init__(self):
        self._stack: List[Tuple[int, int]] = []   # (요청자, 수신자) — comm 스택의 배분 투영

    def next_after(self, st: FloorState, turn: Turn) -> Allocation:
        a = turn.addressee
        if a is not None and a in st.participants and a != turn.speaker:
            self._stack.append((turn.speaker, a))
            return Allocation(NOMINATE, next=a, reason="요청(push)")
        if self._stack:
            frm, _ = self._stack.pop()
            return Allocation(NOMINATE, next=frm, reason="응답 → 요청자 복귀(LIFO pop)")
        return Allocation(CLOSE, reason="모든 요청 닫힘")


class OrchestratedFloor(FloorPolicy):
    """[중앙 배분의 정책화] 사회자(allocator)가 전량 결정 — 지명·자기선택 무시.
    allocator(state, turn) -> 다음 화자 id | None(종료). 현행 meet 라운드·SYS 이어가기의
    '중앙이 정한다'를 같은 seam 위의 한 정책으로 자리매김한다(비판이 아니라 선택지)."""
    name = "orchestrated"

    def __init__(self, allocator: Callable[[FloorState, Turn], Optional[int]]):
        self.allocator = allocator

    def next_after(self, st: FloorState, turn: Turn) -> Allocation:
        nxt = self.allocator(st, turn)
        if nxt is None:
            return Allocation(CLOSE, reason="사회자 종료")
        return Allocation(NOMINATE, next=int(nxt), reason="사회자 배분")


def round_robin(order) -> Callable[[FloorState, Turn], Optional[int]]:
    """고정 순서 사회자 — 미리 짠 발언 순서를 소진하면 종료(현행 meet 라운드와 동형)."""
    seq = [int(x) for x in order]
    pos = {"i": 0}

    def alloc(st: FloorState, turn: Turn) -> Optional[int]:
        if pos["i"] >= len(seq):
            return None
        v = seq[pos["i"]]
        pos["i"] += 1
        return v
    return alloc


def make_floor(mode: Optional[str] = None, *, allocator=None) -> FloorPolicy:
    """정책 팩토리 — 이름 하나로 구조를 갈아끼운다(교체 지점의 단일화).
    orchestrated는 allocator 필수(없으면 ValueError — 조용한 오배선 방지)."""
    m = floor_mode(mode)
    if m == "turn-taking":
        return TurnTakingFloor()
    if m == "orchestrated":
        if allocator is None:
            raise ValueError("orchestrated 정책은 allocator가 필요합니다")
        return OrchestratedFloor(allocator)
    return RequestResponseFloor()


async def run_conversation(policy: FloorPolicy, state: FloorState, opening: Turn, speak,
                           bid=None, max_turns: int = 64, on_alloc=None) -> List[Turn]:
    """대화 엔진 — 배분은 정책에, IO(깨우기·응찰 수집)는 콜백에 위임하는 순수 루프(매체·내용 불가지).

      speak(speaker_id, alloc) -> Turn|None : 배분받은 화자의 턴 실행. None=화자 실행 불가 → 종결.
      bid(candidates, purpose) -> [(후보 id, 강도 int)] : 응찰/표결 수집 — 소비자가 후보 봇들을
                                  (보통 병렬로) 깨워 각자 LLM으로 판정시킨 결과. purpose=OPEN이면
                                  '지금 발언 필요?'(발언권 응찰), CLOSE_VOTE면 '마쳐도 되나?'
                                  ([계속: N]=발언 의무 진 반대). 미배선(None)이면 응찰/반대 0건과
                                  동형(즉시 종결 확인 경로 — 안전 폴백).
      on_alloc(alloc)                       : 관측 콜백(flow.jsonl 기록 등) — 배분마다 호출.

    종결 보장(교착 없음): 매 반복이 정확히 한 배분을 소비하고, CLOSE·speak None·max_turns(폭주
    백스톱) 셋 중 하나로 반드시 끝난다. 종결 확인은 무응찰마다 열리되 매 반복이 배분 하나를
    소비하므로 max_turns가 절대 상한이다(소비자 wake_cap이 비용 상한).
    응찰은 확정 턴으로 record하지 않는다(발언권이 실제로 넘어간 발언만 대화 사실 — 응찰 비용
    상한은 소비자가 bid 콜백 안에서 관리)."""
    turns = [opening]
    state.record(opening)
    turn = opening
    for _ in range(max_turns):
        alloc = policy.next_after(state, turn)
        if on_alloc:
            on_alloc(alloc)
        if alloc.kind == OPEN:
            bids = (await bid(alloc.candidates, OPEN)) if bid is not None else []
            alloc = policy.resolve_open(state, turn, list(bids or []))   # ② 승자 선정 / ③ / 종결
            if on_alloc:
                on_alloc(alloc)
        if alloc.kind == CLOSE_VOTE:
            votes = (await bid(alloc.candidates, CLOSE_VOTE)) if bid is not None else []
            alloc = policy.resolve_close_vote(state, turn, list(votes or []))  # 합의 종결/소생
            if on_alloc:
                on_alloc(alloc)
        if alloc.kind == CLOSE:
            break
        turn = await speak(alloc.next, alloc)      # ①지명 / ②낙찰자 / 종결반대 / 사회자 / LIFO 복귀
        if turn is None:
            break                                              # 화자 실행 불가 → 교착 대신 종결
        state.record(turn)
        turns.append(turn)
    return turns
