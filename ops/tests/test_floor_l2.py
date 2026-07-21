"""[2층 stance seam — CA-Lab RFC-003 2층] 발화 타입·인접쌍 기대 검증.

2층의 책임 기능: **'건 기대는 답을 받는다'** — [질문]/[반박](+[지명])이 연 인접쌍의 의무자가
답하기 전엔 합의 종결이 성립하지 않는다(1층 한계 관측 FLOOR_1F §6-2의 봉합). 단 1층의 책임
(교착 없이 돌고 끝난다, ADR-008)은 그대로 — 강제는 pair당 1회·패스도 해소·max_turns 절대 상한.
기본 OFF(ORGANT_FLOOR_L2 미설정) = 코드 경로 불변(기존 test_floor 전체 + 여기 OFF 가드).
"""
import asyncio
import random

from system.guide_tools import Flow
from system.rule.floor import (CLOSE, SELF, FloorState, Turn, TurnTakingFloor,
                               run_conversation)
from system.rule.stance import (CHALLENGE, CLAIM, QUESTION, SUPPORT, StanceFloor,
                                StanceLedger, floor_l2_mode, parse_stance)
from test_sys import FakeGuide, _tools

A, B, C = 1, 2, 3


# ══ 표면 규약 — 자기 선언 마커 파싱 ══════════════════════════════════════════════

def test_parse_stance_첫_실질줄_자기선언만():
    assert parse_stance("[질문] 롤백 경로는?") == QUESTION
    assert parse_stance("[반박] 근거가 다릅니다") == CHALLENGE
    assert parse_stance("[주장] A안이 맞습니다") == CLAIM
    assert parse_stance("[지지] 동의합니다") == SUPPORT
    assert parse_stance("\n  [질문] 들여쓴 첫 실질 줄") == QUESTION
    assert parse_stance("의견입니다. [질문]은 선두가 아님") is None
    assert parse_stance("본문 먼저\n[질문] 둘째 줄은 인용일 수 있음") is None
    assert parse_stance("[패스]") is None
    assert parse_stance("") is None
    # [변형 흡수(2026-07-21, U-039 실측)] '[질문 @이름(id)]' 자연 표기 — 닫힌 괄호만 매칭하면
    # 타입 인식 실패 = 2층 무위. 타입 단어로 시작하는 대괄호는 수용(이중 수용 관례).
    assert parse_stance("[질문 @게임 기획자(1510929285370875925)] 장르가 무엇입니까?") == QUESTION
    assert parse_stance("[반박 @PM] 근거가 다릅니다") == CHALLENGE
    assert parse_stance("[질문사항] 이건 아님") is None            # 단어 경계 — 합성어 오인 금지


def test_floor_l2_mode_기본off_명시_env(monkeypatch):
    monkeypatch.delenv("ORGANT_FLOOR_L2", raising=False)
    assert floor_l2_mode() == ""                       # 기본 OFF(라이브 불변)
    assert floor_l2_mode("stance") == "stance"
    assert floor_l2_mode("오타") == ""
    monkeypatch.setenv("ORGANT_FLOOR_L2", "stance")
    assert floor_l2_mode() == "stance"


# ══ 장부 의미론 — 개시·해소(§2·§3) ══════════════════════════════════════════════

def test_장부_지명질문은_의무자_발언으로만_해소_패스도_해소():
    lg = StanceLedger()
    evs = lg.note_turn(1, A, QUESTION, B, False, participants=[A, B, C])
    assert [e for e, _ in evs] == ["pair_open"]
    lg.note_turn(2, C, None, None, False, participants=[A, B, C])       # 타인 실발언 ≠ 해소
    assert [p.target for p in lg.pending_targeted([A, B, C])] == [B]
    evs = lg.note_turn(3, B, None, None, True, participants=[A, B, C])  # 의무자 패스 = 응답 거절
    assert [(e, p.resolved) for e, p in evs] == [("pair_resolve", "pass")]
    assert lg.pending() == []


def test_장부_무지명은_다음_타화자_실발언이_슬롯():
    lg = StanceLedger()
    lg.note_turn(1, A, CHALLENGE, None, False, participants=[A, B])
    lg.note_turn(2, A, None, None, False, participants=[A, B])          # 개시자 본인 ≠ 해소
    assert len(lg.pending()) == 1
    evs = lg.note_turn(3, B, None, None, False, participants=[A, B])
    assert [p.resolved for _, p in evs] == ["answered"]


def test_장부_주장지지는_의무없음_이탈한_의무자는_fled():
    lg = StanceLedger()
    lg.note_turn(1, A, CLAIM, B, False, participants=[A, B, C])         # 주장 = pair 없음
    lg.note_turn(2, B, SUPPORT, None, False, participants=[A, B, C])    # 지지 = pair 없음
    assert lg.pairs == []
    lg.note_turn(3, A, QUESTION, C, False, participants=[A, B, C])
    evs = lg.note_turn(4, B, None, None, False, participants=[A, B])    # C가 대화에서 이탈
    assert [p.resolved for _, p in evs] == ["fled"]
    assert lg.pending() == []


# ══ 래퍼 — 합의 종결 1지점 개입(§4) ══════════════════════════════════════════════

def test_래퍼_합의종결_직전_의무자에게_발언권_pair당_1회():
    lg = StanceLedger()
    pol = StanceFloor(TurnTakingFloor(), lg)
    st = FloorState([A, B])
    lg.note_turn(1, A, QUESTION, B, False, participants=[A, B])
    t = Turn(speaker=A, passed=True)
    r = pol.resolve_close_vote(st, t, [])              # 전원 [종료]인데 미해소 → 답 슬롯 강제
    assert r.kind == SELF and r.next == B and "2층" in r.reason
    r2 = pol.resolve_close_vote(st, t, [])             # 의무자 여전히 침묵이어도 재강제 없음(1회)
    assert r2.kind == CLOSE


def test_래퍼_소생과_비종결은_무간섭():
    lg = StanceLedger()
    pol = StanceFloor(TurnTakingFloor(), lg)
    st = FloorState([A, B])
    lg.note_turn(1, A, QUESTION, B, False, participants=[A, B])
    r = pol.resolve_close_vote(st, Turn(speaker=A, passed=True), [(B, 5)])
    assert r.kind == SELF and "종결 반대" in r.reason   # [계속] 소생은 그대로(개입은 CLOSE 지점뿐)


# ══ 엔진 관통 — §6-2 재현(예산 컷 질문 → 다음 패스에서 답 받고 닫힘) ═════════════

def test_엔진_예산컷_질문이_다음_패스_종결을_막고_답을_받는다():
    """FLOOR_1F §6-2 한계 관측 재현: 예산이 [지명] 질문 직후 회의를 끊는다 → 게이트 루프의
    다음 패스에서 전원 [종료]로 닫히려는 순간 2층이 의무자에게 답 슬롯을 주고, 답 후 닫힌다."""
    lg = StanceLedger()
    pol = StanceFloor(TurnTakingFloor(), lg)
    st = FloorState([A, B])
    spoke = []

    def note(t):
        lg.note_turn(st.turn_no, t.speaker, t.stype, t.addressee, t.passed, participants=[A, B])
        return t

    # 패스 1(예산 컷): A의 [질문]+[지명: B]가 마지막 턴 — B의 지명 이행 전에 끊김
    q = note(Turn(speaker=A, addressee=B, stype=QUESTION, body="[질문] 롤백 경로는?"))
    st.record(q)

    async def speak(who, alloc):
        spoke.append((who, alloc.reason))
        return note(Turn(speaker=who, body="답: 이전 스키마 자동 복귀 절차로 갑니다."))

    async def bid(cands, purpose):
        return []                                      # 전원 무응찰·전원 [종료]

    t0 = note(Turn(speaker=A, body="(발제)"))          # 다음 패스 개시
    turns = asyncio.run(run_conversation(pol, st, t0, speak, bid=bid, max_turns=8))
    assert spoke and spoke[0][0] == B and "2층" in spoke[0][1]   # 닫히기 전 B가 답 슬롯
    assert lg.pending() == []                          # 답으로 해소 — 재강제 여지 없음
    assert len(turns) == 2                             # t0 + B의 답, 그 뒤 합의 종결


def test_무작위대본_L2_ON_항상_종결():
    """종결 보장(ADR-008)은 2층을 얹어도 그대로 — 강제 1회 규칙·패스 해소·max_turns 절대 상한."""
    for seed in range(30):
        rng = random.Random(seed)
        parts = [1, 2, 3, 4][: rng.randint(2, 4)]
        lg = StanceLedger()
        pol = StanceFloor(TurnTakingFloor(), lg)
        st = FloorState(parts)

        def note(t):
            lg.note_turn(st.turn_no, t.speaker, t.stype, t.addressee, t.passed,
                         participants=parts)
            return t

        async def speak(who, alloc):
            adr = rng.choice([None] + parts)
            return note(Turn(speaker=who, addressee=(adr if adr != who else None),
                             stype=rng.choice([None, QUESTION, CHALLENGE, CLAIM, SUPPORT]),
                             passed=(rng.random() < 0.2)))

        async def bid(cands, purpose):
            return [(c, rng.randint(0, 3)) for c in cands if rng.random() < 0.5]

        t0 = note(Turn(speaker=parts[0]))
        turns = asyncio.run(run_conversation(pol, st, t0, speak, bid=bid, max_turns=24))
        assert len(turns) <= 25                        # 교착·무한 루프 없음


# ══ 실표면(meet) 통합 — ON 배선·OFF 무회귀 ══════════════════════════════════════

def _meet_flow(bots=None):
    g = FakeGuide()
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11,
             bot_info=bots or {11: "L", 12: "백엔드", 13: "QA"})
    f.start_root("root")
    return g, f


def _l2_script(seen):
    async def wake(to, b, k):
        seen.append((to, b))
        if "1라운드" in b:
            return f"{to} 독립 의견"
        if "발언권 응찰" in b:
            return "[응찰: 7] 롤백 경로 확인 필요" if to == 12 else "[패스]"
        if "발언권 획득" in b:
            return "[질문] 마이그레이션 실패 시 롤백 경로가 있습니까?\n[지명: 13]"
        if "차례" in b or "답 슬롯" in b:
            return "[주장] 있습니다 — 이전 스키마 자동 복귀 절차로 갑니다."
        return "[패스]"
    return wake


def test_meet_L2_ON_규약주입_타입운반_인접쌍_수명주기():
    """켰을 때: ①토론 프롬프트에 타입 규약 주입 ②[질문] 발언이 stype으로 운반(stance_turn)
    ③[지명]과 함께 pair 개시 → 지명 이행 발언이 해소(pair_open→pair_resolve 관측)."""
    g, f = _meet_flow()
    f.floor_mode = "turn-taking"
    f.floor_l2 = "stance"
    logged = []
    f.log = lambda ev, **kw: logged.append((ev, kw))
    seen = []
    f.wake = _l2_script(seen)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({"topic": "저장 방식", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    disc = [b for _, b in seen if "1라운드" not in b]
    assert any("`[주장]`/`[질문]`" in b for b in disc)            # ① 규약 주입(토론 턴)
    evs = dict((e, kw) for e, kw in logged
               if e in ("stance_turn", "pair_open", "pair_resolve"))
    assert evs.get("stance_turn", {}).get("stype") in ("question", "claim")   # ② 타입 운반
    assert evs.get("pair_open", {}).get("target") == 13           # ③ 질문+지명 = 13의 답 기대
    assert evs.get("pair_resolve", {}).get("how") == "answered"   # 지명 이행 발언이 해소
    assert f.comm.alive == 11                                     # 회의 정상 종료(베턴 복귀)


def test_meet_기본OFF_규약문구_이벤트_전무(monkeypatch):
    """무회귀 가드: 플래그 미설정이면 프롬프트·관측·배분 어디에도 2층 흔적이 없다."""
    monkeypatch.delenv("ORGANT_FLOOR_L2", raising=False)
    g, f = _meet_flow()
    f.floor_mode = "turn-taking"
    logged = []
    f.log = lambda ev, **kw: logged.append(ev)
    seen = []
    f.wake = _l2_script(seen)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({"topic": "저장 방식", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    assert not any("`[주장]`/`[질문]`" in b for _, b in seen)     # 규약 문구 없음
    assert not set(logged) & {"stance_turn", "pair_open", "pair_resolve", "pair_block_close"}
