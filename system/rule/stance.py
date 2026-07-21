"""Stance Rule — 대화 구조 2층: 발화 타입 + 인접쌍 기대 (순수 로직 — 네트워크·LLM·매체 없음).

CA-Lab [RFC-003] 3층 모델의 **2층(스탠스·구조적 기대)** 이식. 1층(floor.py)이 '누가 다음에
말하는가'라면, 2층은 '이 발화가 다음 자리에 어떤 의무를 거는가'다(Schegloff & Sacks 1973,
adjacency pair — first pair part가 next slot을 조건적으로 관련(conditionally relevant)하게 만든다).

조작화 결정 (1층의 절제 관례를 따른다):
  1. **발화 타입 = 자기 선언 마커**(발언 첫 줄 `[주장]`/`[질문]`/`[반박]`/`[지지]`) — 응찰([응찰: N])과
     같은 원칙: 판단은 봇의 지능(자기 발화의 성격은 자기가 안다), 구조는 장부만. LLM 분류 호출 0.
     무마커 발언 = 타입 없음(관용 — 규약 미준수가 의무를 만들지도, 발언을 무효화하지도 않는다).
  2. **기대(pair)를 여는 타입은 질문·반박뿐** — 주장·지지는 다음 자리를 구속하지 않는다(절제:
     모든 발화에 의무를 걸면 회의가 의무 장부가 된다). 같은 발언의 `[지명: X]`가 있으면 그 X가
     응답 의무자(targeted), 없으면 다음 타 화자의 실발언이 응답 슬롯을 채운다(untargeted — 약한 기대).
  3. **해소 = 의무자의 다음 실발언. 패스도 해소다**('응답 거절'도 명시적 응답 — 무한 의무·교착 방지).
     의무자가 대화 참여자 밖으로 사라지면 해소(fled) — 부재자에게 의무를 걸어두지 않는다.
  4. **종결 블록 = 합의 종결 직전 1회 강제**: 전원 [종료]로 닫히려는 순간 미해소 targeted pair가
     있으면 그 의무자에게 발언권을 준다(pair당 1회 — forced 표식). 1층 한계 관측(FLOOR_1F §6-2,
     "마지막 발언의 [지명: QA] 미이행")이 이 층의 존재 이유다. 종결 보장(ADR-008)은 유지 —
     강제 발언이 pair를 해소하므로(§3) 같은 pair가 두 번 막을 수 없고, 엔진 max_turns가 절대 상한.

배선(소비자 소관 — communication.py): 기본 OFF. `ORGANT_FLOOR_L2=stance`(또는 flow.floor_l2)일 때만
turn-taking 회의에 StanceFloor 래퍼가 씌워진다 — 미설정이면 코드 경로 불변(default-OFF 관례).
관측: stance_turn·pair_open·pair_resolve·pair_block_close (flow.jsonl — CA-Lab 2층 실험 원자료).
"""
import os
from dataclasses import dataclass
from typing import Callable, List, Optional

from .floor import CLOSE, SELF, Allocation, FloorPolicy, FloorState, Turn

# 발화 타입 상수 — 마커(표면 규약)와 관측(flow.jsonl)이 같은 문자열을 본다.
CLAIM = "claim"          # [주장] — 입장 제시(의무 없음)
QUESTION = "question"    # [질문] — 답 기대(인접쌍 개시)
CHALLENGE = "challenge"  # [반박] — 응답 기대(인접쌍 개시)
SUPPORT = "support"      # [지지] — 동조(의무 없음)

_MARKERS = {"[주장]": CLAIM, "[질문]": QUESTION, "[반박]": CHALLENGE, "[지지]": SUPPORT}
# [변형 흡수(2026-07-21, U-039/ch84 실측: 봇이 '[질문 @게임 기획자(id)]'로 자연 표기 — 닫힌 괄호
# 매칭만으로는 타입 인식 실패 = 2층 무위)] 이중 수용 관례: 타입 단어로 시작하는 대괄호면 수용.
_MARKER_RE = __import__("re").compile(r"^\[\s*(주장|질문|반박|지지)\b")
_MARKER_BY_WORD = {"주장": CLAIM, "질문": QUESTION, "반박": CHALLENGE, "지지": SUPPORT}
_PAIR_OPENERS = (QUESTION, CHALLENGE)


def floor_l2_mode(explicit: Optional[str] = None) -> str:
    """2층 스위치 — 명시 인자 > env ORGANT_FLOOR_L2 > 기본 off(빈 문자열). 오타는 off 폴백
    (1층 floor_mode와 같은 default-OFF 관례 — 미설정 라이브 동작 불변)."""
    v = (explicit or os.environ.get("ORGANT_FLOOR_L2") or "").strip().lower()
    return v if v == "stance" else ""


def parse_stance(text: str) -> Optional[str]:
    """발언 → 발화 타입. 첫 실질 줄이 타입 마커로 시작할 때만(자기 선언 — 본문 중간 인용은
    타입이 아니다). 무마커·[패스]는 None(타입 없음 = 의무 없음)."""
    for line in str(text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = _MARKER_RE.match(s)
        if m:
            return _MARKER_BY_WORD[m.group(1)]
        return None
    return None


@dataclass
class Pair:
    """미해소 인접쌍 하나 — first pair part의 사실과 그것이 거는 기대."""
    opened_turn: int                 # 개시 턴 번호(FloorState.turn_no 기준)
    opener: int                      # 개시 화자
    ptype: str                       # question | challenge
    target: Optional[int] = None     # 응답 의무자(같은 발언의 [지명]) — None=다음 타 화자 슬롯
    forced: bool = False             # 종결 블록으로 발언권을 이미 강제했는가(pair당 1회)
    resolved: str = ""               # ""=미해소 / answered | pass | fled


class StanceLedger:
    """인접쌍 장부 — 열림·해소만 기록하는 순수 상태(판단 없음). 갱신은 확정 턴마다 1회
    (note_turn), 조회는 pending()/pending_targeted(). 관측 이벤트를 [(이름, pair)]로 반환한다."""

    def __init__(self):
        self.pairs: List[Pair] = []

    def note_turn(self, turn_no: int, speaker: int, stype: Optional[str],
                  addressee: Optional[int], passed: bool,
                  participants: Optional[List[int]] = None):
        """확정 턴 하나의 장부 반영 — ①이 화자를 기다리던 pair 해소 ②의무자 이탈 해소
        ③이 턴이 새 pair를 여는가. 반환: [("pair_resolve"|"pair_open", Pair)] 관측 이벤트."""
        evs = []
        for p in self.pairs:
            if p.resolved:
                continue
            if p.target is not None and p.target == speaker:
                p.resolved = "pass" if passed else "answered"      # §3 패스도 해소(응답 거절)
                evs.append(("pair_resolve", p))
            elif p.target is None and speaker != p.opener and not passed:
                p.resolved = "answered"                            # 약한 기대 — 다음 실발언이 슬롯
                evs.append(("pair_resolve", p))
        if participants is not None:
            for p in self.pairs:
                if not p.resolved and p.target is not None and p.target not in participants:
                    p.resolved = "fled"                            # 부재자에게 의무를 걸어두지 않는다
                    evs.append(("pair_resolve", p))
        if not passed and stype in _PAIR_OPENERS:
            p = Pair(opened_turn=turn_no, opener=speaker, ptype=stype,
                     target=(addressee if addressee != speaker else None))
            self.pairs.append(p)
            evs.append(("pair_open", p))
        return evs

    def pending(self) -> List[Pair]:
        return [p for p in self.pairs if not p.resolved]

    def pending_targeted(self, participants=None) -> List[Pair]:
        ps = [p for p in self.pending() if p.target is not None]
        if participants is not None:
            ps = [p for p in ps if p.target in participants]
        return ps


class StanceFloor(FloorPolicy):
    """[2층 본체] 1층 정책 래퍼 — 배분은 전부 안쪽 정책에 위임하고, 딱 한 지점만 개입한다:
    **합의 종결(resolve_close_vote → CLOSE) 직전, 미해소 targeted pair의 의무자에게 발언권**
    (pair당 1회, §4). 1층 순수성 계약 그대로: 상태·턴·응찰만 보고, IO 없음.

    장부 기입은 래퍼가 아니라 **턴을 만드는 소비자의 몫**(note_turn — communication._speech가
    턴 생성 직후 호출): next_after는 예산 컷으로 잘린 마지막 턴엔 다시 호출되지 않아, 거기서
    기입하면 §6-2의 동기 사례(예산 컷 직전의 [지명] 질문)가 정확히 유실된다."""

    def __init__(self, inner: FloorPolicy, ledger: Optional[StanceLedger] = None,
                 observer: Optional[Callable[[str, Pair], None]] = None):
        self.inner = inner
        self.ledger = ledger or StanceLedger()
        self.observer = observer          # 관측 콜백(flow.log 등) — on_alloc 관례와 동일한 주입
        self.name = getattr(inner, "name", "?") + "+stance"

    def _emit(self, evs):
        if self.observer:
            for name, p in evs:
                try:
                    self.observer(name, p)
                except Exception:
                    pass                   # 관측 실패가 배분을 막지 않는다

    def next_after(self, st: FloorState, turn: Turn) -> Allocation:
        return self.inner.next_after(st, turn)

    def resolve_open(self, st: FloorState, turn: Turn, bids) -> Allocation:
        return self.inner.resolve_open(st, turn, bids)

    def resolve_close_vote(self, st: FloorState, turn: Turn, bids) -> Allocation:
        a = self.inner.resolve_close_vote(st, turn, bids)
        if a.kind != CLOSE:
            return a                       # 소생([계속]) 등은 그대로 — 개입 지점은 합의 종결뿐
        for p in self.ledger.pending_targeted(st.participants):
            if not p.forced and p.target != turn.speaker:
                p.forced = True            # pair당 1회 — 재차단 불가(의무자 발언이 §3으로 해소)
                self._emit([("pair_block_close", p)])
                lbl = "질문" if p.ptype == QUESTION else "반박"
                return Allocation(SELF, next=p.target,
                                  reason=f"2층 미해소 {lbl} — 답 슬롯(발언 의무)")
        return a
