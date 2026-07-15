"""[1층 floor seam — CA-Lab RFC-003] 발언권 순환 추상화 검증.

1층의 성공 기준은 인사이트가 아니라 **'교착 없이 돌고 끝나는가'**(ADR-008 — 층의 책임 기능)다.
여기서 검증하는 것:
  ① 정책 3종(turn-taking·request-response·orchestrated)이 같은 엔진 위에서 교체 가능
  ② turn-taking = Sacks 3규칙 — 지명 → 자기선택(**후보 봇의 LLM 응찰** — 관련성 판단은 봇,
     선정 규칙만 정책) → 무응찰 시 종결 확인 표결 — 이 기계적으로 성립
  ③ request-response 정책의 배분이 라이브 베턴 엔진(CommunicationManager)과 동형(동치성)
  ④ 종결 보장 — 무작위 대본에서도 항상 끝난다(교착·무한 루프 없음)
  ⑤ 실표면 통합 — meet(회의)·세그먼트 경계 open이 seam 뒤에서 동작(기본=동작 불변)
(실 LLM 봇으로 돌린 라이브 검증은 FLOOR_1F_2026-07-04.md §6 — 여기는 결정론 대본 검증.)
"""
import asyncio
import random

import pytest

from system.guide_tools import Flow
from system.rule.communication import CommunicationManager, _bid_score, _turn_signals
from system.rule.floor import (CLOSE, CLOSE_VOTE, NOMINATE, OPEN, SELF, FloorState,
                               OrchestratedFloor, RequestResponseFloor, Turn,
                               TurnTakingFloor, floor_mode, make_floor, round_robin,
                               run_conversation)
from system.sys_core import Sys
from test_sys import FakeGuide, _flow, _tools

A, B, C = 1, 2, 3


# ══ 정책 순수 로직 — Sacks 3규칙 ═══════════════════════════════════════════════

def test_TT_규칙1_지명이_다음화자():
    st = FloorState([A, B, C])
    a = TurnTakingFloor().next_after(st, Turn(speaker=A, addressee=B))
    assert a.kind == NOMINATE and a.next == B


def test_TT_규칙2_무지명은_자기선택_open_침묵오래된순():
    st = FloorState([A, B, C])
    st.record(Turn(speaker=B))          # B는 방금 발언 → 후보 순서 뒤로
    st.record(Turn(speaker=A))
    a = TurnTakingFloor().next_after(st, Turn(speaker=A))
    assert a.kind == OPEN and a.candidates == (C, B)   # 무발언 C 최우선, 화자 A 제외


def test_TT_규칙2_응찰판정_최고가_승리_동률은_침묵순():
    """②자기선택의 선정 규칙: 관련성 판단(응찰 강도)은 후보 봇의 LLM이 내고, 정책은
    '최고 응찰 승·동률=침묵 오래된 순'만 정한다 — 선정이 중앙 자의가 아니라 규칙임을 고정."""
    st = FloorState([A, B, C])
    st.record(Turn(speaker=B))                         # B 최근 발언 → 동률에서 밀림
    pol = TurnTakingFloor()
    a = pol.resolve_open(st, Turn(speaker=A), [(B, 5), (C, 5)])
    assert a.kind == SELF and a.next == C              # 동률 → 침묵 오래된 C
    a2 = pol.resolve_open(st, Turn(speaker=A), [(B, 7), (C, 5)])
    assert a2.kind == SELF and a2.next == B            # 최고 응찰 승


def test_TT_규칙3_무응찰이면_즉시_종결확인_표결():
    """[EXP-002 절제] 무응찰 → (종전 ③현재 화자 계속·lapse 카운터 없이) 곧장 종결 확인 표결.
    이어가기 채널은 표결의 [계속: N](발언 의무)이 담당한다."""
    st = FloorState([A, B])
    pol = TurnTakingFloor()
    t = Turn(speaker=A, passed=True)
    a = pol.resolve_open(st, t, [(B, 0)])              # 0점 응찰 = 무응찰
    assert a.kind == CLOSE_VOTE and set(a.candidates) == {A, B}   # 현재 화자도 표결 참여
    assert pol.resolve_close_vote(st, t, []).kind == CLOSE   # 전원 [종료]/무응답 → 합의 종결


def test_TT_종결확인_반대는_발언의무_표결은_무응찰마다():
    """[종결 = 합의 성취(pre-closing)] 표결에서 [계속: N] 반대자가 나오면 그가 발언권을 받아
    직접 말한다(말 없는 연장 불가). 표결은 무응찰마다 열린다 — 늦은 대화 유실 없음. 진짜 끝나면
    전원 [종료]가 닫는다(상한은 엔진 max_turns·소비자 wake_cap)."""
    st = FloorState([A, B])
    pol = TurnTakingFloor()
    t = Turn(speaker=A, passed=True)
    a = pol.resolve_open(st, t, [])                    # 무응찰 → 표결 #1
    assert a.kind == CLOSE_VOTE
    r = pol.resolve_close_vote(st, t, [(B, 6), (A, 0)])
    assert r.kind == SELF and r.next == B              # 반대=발언권 획득(소생)
    a2 = pol.resolve_open(st, t, [])                   # 재무응찰 → 표결 #2 (1회 제한 없음)
    assert a2.kind == CLOSE_VOTE
    assert pol.resolve_close_vote(st, t, [(A, 0), (B, 0)]).kind == CLOSE   # 전원 종료 → 닫힘


def test_지명이_참여자밖이면_무지명으로_무해화():
    st = FloorState([A, B])
    a = TurnTakingFloor().next_after(st, Turn(speaker=A, addressee=99))
    assert a.kind == OPEN                              # 자기선택 경로로 폴백


# ══ RR 정책 — 라이브 베턴 엔진과 배분 동치성 ═══════════════════════════════════

def test_RR정책_배분이_베턴엔진과_동형():
    """같은 대본(A→B 요청, B→C 요청, C 응답, B 응답)을 CommunicationManager와
    RequestResponseFloor에 나란히 — 발언권 시퀀스가 일치해야 '현행 구조의 정책화'가 성립."""
    m = CommunicationManager(A)
    pol, st = RequestResponseFloor(), FloorState([A, B, C])
    seq_comm, seq_floor = [], []

    m.request(A, B, "r1"); seq_comm.append(m.alive)
    seq_floor.append(pol.next_after(st, Turn(speaker=A, addressee=B)).next)
    m.request(B, C, "r2"); seq_comm.append(m.alive)
    seq_floor.append(pol.next_after(st, Turn(speaker=B, addressee=C)).next)
    m.respond(C); seq_comm.append(m.alive)
    seq_floor.append(pol.next_after(st, Turn(speaker=C)).next)      # 응답 → LIFO 복귀
    m.respond(B); seq_comm.append(m.alive)
    seq_floor.append(pol.next_after(st, Turn(speaker=B)).next)

    assert seq_comm == seq_floor == [B, C, B, A]
    assert m.done                                       # 베턴: 스택 비면 종료·origin 복귀
    assert pol.next_after(st, Turn(speaker=A)).kind == CLOSE   # 정책도 동형(모든 요청 닫힘)


# ══ orchestrated — 사회자 배분 ═════════════════════════════════════════════════

def test_orchestrated_사회자순서_소진시_종결():
    st = FloorState([A, B, C])
    pol = OrchestratedFloor(round_robin([B, C]))
    a1 = pol.next_after(st, Turn(speaker=A))
    a2 = pol.next_after(st, Turn(speaker=B))
    a3 = pol.next_after(st, Turn(speaker=C))
    assert (a1.kind, a1.next) == (NOMINATE, B) and (a2.kind, a2.next) == (NOMINATE, C)
    assert a3.kind == CLOSE


# ══ 선택·팩토리·신호 — '언제든 교체' 지점 ═══════════════════════════════════════

def test_floor_mode_선택규칙(monkeypatch):
    monkeypatch.delenv("ORGANT_FLOOR", raising=False)
    assert floor_mode() == "request-response"                      # 미설정 = 현행 구조(불변)
    monkeypatch.setenv("ORGANT_FLOOR", "turn-taking")
    assert floor_mode() == "turn-taking"
    assert floor_mode("orchestrated") == "orchestrated"            # 명시 인자 > env
    monkeypatch.setenv("ORGANT_FLOOR", "잘못된값")
    assert floor_mode() == "request-response"                      # 오타 = 기본 폴백(오배선 무해화)


def test_make_floor_orchestrated는_allocator_필수():
    with pytest.raises(ValueError):
        make_floor("orchestrated")
    assert make_floor("turn-taking").name == "turn-taking"
    assert make_floor("request-response").name == "request-response"


def test_bid_score_응찰_파싱():
    assert _bid_score("[응찰: 7] 테스트 관점 긴급") == 7
    assert _bid_score("[계속: 5] 모바일 레이아웃이 남았습니다") == 5   # 종결 반대 = 동형 강도
    assert _bid_score("[패스]") == 0 and _bid_score("") == 0
    assert _bid_score("패스") == 0 and _bid_score("[종료]") == 0       # 종결 찬성 = 무응찰
    # 마커 없는 실질 텍스트 = 약한 응찰 1(관용 — 규약 미준수가 발언 의지를 소멸시키지 않게)
    assert _bid_score("의견이 있습니다 — 마이그레이션 순서부터 정리해야 합니다.") == 1


# ══ 엔진 — 같은 대화, 세 구조 ═════════════════════════════════════════════════

def test_같은대화_세정책_전부완주_배분은_상이():
    """정책 교체 요구의 핵심 검증: 동일 대본이 세 구조 모두에서 '교착 없이' 완주하되,
    배분 트레이스는 구조별로 달라야 한다(추상화가 실제로 구조를 바꾼다는 증거)."""
    kinds = {}
    for mode in ("turn-taking", "request-response", "orchestrated"):
        pol = make_floor(mode, allocator=(round_robin([B, C]) if mode == "orchestrated" else None))
        st, trace = FloorState([A, B, C]), []

        async def speak(s, alloc):
            return Turn(speaker=s)                     # 무지명 발언(정책이 차이를 만든다)

        async def bid(cands, purpose):
            return [(c, 9) for c in cands]             # 전원 강응찰(자기선택 활성)

        turns = asyncio.run(run_conversation(pol, st, Turn(speaker=A), speak, bid=bid,
                                             max_turns=6, on_alloc=lambda a: trace.append(a.kind)))
        kinds[mode] = tuple(trace)
        assert turns                                   # 반환됨 = 종결됨(교착 없음)
    assert kinds["request-response"][0] == CLOSE       # 무지명=응답 → 열린 요청 없음 → 즉시 종결
    assert OPEN in kinds["turn-taking"] and SELF in kinds["turn-taking"]
    assert kinds["orchestrated"][0] == NOMINATE        # 사회자가 배분


def test_엔진_speak_None이면_교착대신_종결():
    async def speak(s, alloc):
        return None                                    # 화자 실행 불가(매체 중단)

    st = FloorState([A, B])
    turns = asyncio.run(run_conversation(OrchestratedFloor(round_robin([B, A, B])), st,
                                         Turn(speaker=A), speak, max_turns=10))
    assert len(turns) == 1                             # opening만 — 즉시 종결


def test_엔진_bid_미배선_소비자는_즉시_합의종결():
    """bid 미배선 소비자(응찰을 아직 안 붙인 표면)의 TT = 응찰 0·표결 무응답과 동형 →
    무지명 턴 후 곧장 합의 종결로 수렴(교착 없음 — 안전 폴백)."""
    st = FloorState([A, B])
    seq = []

    async def speak(s, alloc):
        seq.append(s)
        return Turn(speaker=s)

    turns = asyncio.run(run_conversation(TurnTakingFloor(), st, Turn(speaker=A), speak,
                                         max_turns=10))
    assert seq == [] and len(turns) == 1               # opening 후 open→표결→무응답→즉시 종결


def test_TT_무작위대본_30회_항상_종결():
    """교착 없음 속성 검증 — 지명·패스·응찰 강도가 뒤섞인 무작위 대본 30개가 전부 끝난다."""
    async def _one(seed):
        rng = random.Random(seed)
        parts = list(range(1, 2 + rng.randint(1, 4)))
        st = FloorState(parts)

        async def speak(s, alloc):
            r = rng.random()
            addr = rng.choice([p for p in parts if p != s]) if (r < 0.4 and len(parts) > 1) else None
            return Turn(speaker=s, addressee=addr, passed=(r > 0.8))

        async def bid(cands, purpose):
            return [(c, rng.choice([0, 0, 1, 5, 9])) for c in cands]

        turns = await run_conversation(TurnTakingFloor(), st, Turn(speaker=parts[0]),
                                       speak, bid=bid, max_turns=40)
        assert 1 <= len(turns) <= 41                   # 반환 자체가 종결의 증거

    for s in range(30):
        asyncio.run(_one(s))


# ══ 발언 신호 파싱(표면 규약) ═════════════════════════════════════════════════

def test_turn_signals_지명과_패스():
    g = FakeGuide()
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11, bot_info={11: "L", 12: "백엔드", 13: "QA"})
    addr, passed = _turn_signals(f, "정리하면 스키마가 우선입니다.\n[지명: 13]", [12, 13])
    assert addr == 13 and not passed
    assert _turn_signals(f, "[패스]", [12, 13]) == (None, True)
    assert _turn_signals(f, "패스", [12, 13])[1] is True
    addr2, _ = _turn_signals(f, "[지명: 99]", [12, 13])          # 참여자 밖 → 무해화
    assert addr2 is None


# ══ 실표면 통합 ① — meet(회의)가 seam 위에서 돈다 ═══════════════════════════════

def _meet_flow(bots=None):
    g = FakeGuide()
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11,
             bot_info=bots or {11: "L", 12: "백엔드", 13: "QA", 14: "기획"})
    f.start_root("root")
    return g, f


def test_meet_TT_응찰승자가_발언권을_얻고_지명이_다음을_정한다():
    """turn-taking 회의: R1 후 발언권이 비면 후보 봇들이 **각자 응찰**하고(관련성 판단=LLM),
    최고 응찰이 발언권을 얻어 정식 발언하며, 그 발언 속 [지명]이 다음 화자를 정한다(Sacks ①②)."""
    g, f = _meet_flow()
    f.floor_mode = "turn-taking"
    seen = []

    async def wake(to, b, k):
        seen.append((to, b))
        if "1라운드" in b:
            return f"{to} 독립 의견"
        if "발언권 응찰" in b:
            return {12: "[응찰: 3] 스키마 관점 보완 필요",
                    13: "[응찰: 8] 테스트 선행 이슈 긴급"}.get(to, "[패스]")
        if "발언권 획득" in b:
            return "마이그레이션 검증이 선행돼야 합니다 — 백엔드 인덱스 협의가 필요합니다.\n[지명: 12]"
        return "동의 — 인덱스 전략은 별도 검토로 정리하겠습니다."   # 지명받은 차례
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13,14"}))
    r = asyncio.run(t["meet"].handler({"topic": "저장 방식", "members": "", "rounds": "2", "my_opinion": "소집자 독립의견"}))
    txt = r["content"][0]["text"]
    disc = [(to, b) for to, b in seen if "1라운드" not in b]
    probes = [(to, b) for to, b in disc if "발언권 응찰" in b]
    assert {to for to, _ in probes[:2]} == {12, 13}              # ② 침묵 오래된 후보들에 병렬 응찰
    won = [(to, b) for to, b in disc if "발언권 획득" in b]
    assert won and won[0][0] == 13                               # 최고 응찰(8) 승자 = 13
    nominated = [(to, b) for to, b in disc
                 if "[회의 토론]" in b and "차례" in b and to == 12]
    assert nominated                                             # ① 지명 이행(13 → 12)
    assert "[토론]" in txt and f.comm.alive == 11
    assert {12, 13} <= f.current.participated                    # 실발언자 참여 인정


def test_meet_TT_전원무응찰이면_종결확인_거쳐_조기종결():
    """보탤 말이 없으면 예산을 다 태우지 않고 끝난다 — 자동 타임아웃이 아니라 **종결 확인
    표결**(전원 [패스]/[종료])을 거친 합의 종결로. [EXP-002 절제 후] ③계속 없이 응찰 1라운드
    → 곧장 표결이라 wake가 종전(5)보다 준다(3)."""
    g, f = _meet_flow({11: "L", 12: "백엔드", 13: "QA"})
    f.floor_mode = "turn-taking"
    seen = []

    async def wake(to, b, k):
        seen.append(b)
        return f"{to} 독립 의견" if "1라운드" in b else "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    r = asyncio.run(t["meet"].handler({"topic": "T", "members": "", "rounds": "3", "my_opinion": "소집자 독립의견"}))
    disc = [b for b in seen if "1라운드" not in b]
    # 응찰1 · 종결확인 표결2(전원) = 3 wake — 종전(③계속 경유 5 wake) 대비 절감
    assert len(disc) == 3 and sum("종결 확인" in b for b in disc) == 2
    assert f.comm.alive == 11 and "[회의록]" in r["content"][0]["text"]


def test_meet_TT_종결반대자는_발언권을_받아_직접_말한다():
    """종결 확인에서 [계속: N]을 낸 봇이 발언 의무를 지고 발언권을 받는다 — 말 없는 회의
    연장은 구조적으로 불가. 표결은 무응찰마다 열리고, 진짜 끝나면 전원 [종료]가 닫는다."""
    g, f = _meet_flow({11: "L", 12: "백엔드", 13: "QA"})
    f.floor_mode = "turn-taking"
    seen = []
    voted = {"n": 0}

    async def wake(to, b, k):
        seen.append((to, b))
        if "1라운드" in b:
            return f"{to} 독립 의견"
        if "종결 확인" in b:
            if to == 12 and voted["n"] == 0:
                voted["n"] = 1
                return "[계속: 6] 롤백 경로 리스크가 아직 안 다뤄졌습니다"
            return "[종료]"
        if "발언권 획득" in b:
            return "롤백 경로: 마이그레이션 실패 시 이전 스키마로 자동 복귀하는 절차가 빠져 있습니다."
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    r = asyncio.run(t["meet"].handler({"topic": "T", "members": "", "rounds": "3", "my_opinion": "소집자 독립의견"}))
    txt = r["content"][0]["text"]
    revived = [(to, b) for to, b in seen if "발언권 획득" in b]
    assert revived and revived[0][0] == 12             # 반대자(백엔드)가 발언권 획득
    assert "롤백 경로" in txt                           # 소생 발언이 회의록에 실림
    assert sum("종결 확인" in b for _, b in seen) >= 4  # 표결 2회(각 전원 2명) — 재표결 허용
    assert f.comm.alive == 11 and 12 in f.current.participated


def test_meet_게이트_Task회의는_GOAL만_전원찬성_채택후_등록(monkeypatch):
    """[게이트=채택된 수렴안 + 회의 하나당 하나(2026-07-14, 사용자)] 첫 회의는 Task 회의 = GOAL만
    정한다(마일스톤·단위 아님). 종료 조건 = 발언권 소진이 아니라 '수렴안이 표결로 채택됨' — ①아무도
    안 내면 전원 발언권 되살려 재응찰(게이트 전면화) ②나오면 전원 찬성 표결로 채택 ③채택 시 GOAL만
    등록(GOAL.md 생성), [회의 마무리]로 결론. 마일스톤은 이 회의에서 안 만든다(다음 회의)."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow({11: "L", 12: "백엔드", 13: "QA"})
    f.floor_mode = "turn-taking"
    seen = {"gate": 0, "ratify": 0}
    CONS = ("[종료]\n[수렴안]\n목표: 방명록 1주기\n"
            "등록 API 동작 | curl POST 후 GET 확인\n목록 표시 | playwright 로드 확인\n[/수렴안]")

    async def wake(to, b, k):
        if "수렴안 확정 표결" in b:                   # 비준 표결 → 전원 찬성
            seen["ratify"] += 1
            return "[찬성]"
        if "종결 확인" in b:
            if "채택돼야만" in b:                       # 게이트 전면화된 재응찰에서만 수렴안 제출
                seen["gate"] += 1
                return CONS
            return "[종료]"                            # 첫 패스: 수렴안 없이 종료 시도 → 게이트 미충족(재응찰)
        return "[패스]"                                # 발언권 응찰은 패스(빨리 종결확인으로)
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    r = asyncio.run(t["meet"].handler({"topic": "방명록", "members": "", "rounds": "2", "my_opinion": "여는 의견"}))
    txt = r["content"][0]["text"]
    assert seen["gate"] >= 1                           # 첫 패스 미충족 → 되살려 재응찰(게이트 전면화)에서 제출
    assert seen["ratify"] >= 1                         # 제출된 수렴안에 전원 찬성 비준
    assert "Task GOAL 확정" in txt and "GOAL.md 생성" in txt   # GOAL 단계 결론(마일스톤 아님)
    assert not (f.milestones or [])                    # 이 회의는 마일스톤을 안 만든다(다음 회의)
    assert f.current.status.goal == "방명록 1주기"     # 채택 수렴안이 Task GOAL을 채움
    assert "[회의 마무리]" in txt and "방명록 1주기" in txt   # 결론 게시(왜→결론)


def test_meet_수렴안_반대_있으면_회의_계속된다(monkeypatch):
    """[전원 찬성 아니면 부결→회의 계속] 비준 표결에서 반대가 하나라도 있으면 채택 안 되고, 게이트가
    안 열려 회의가 계속된다 — '찬성을 모두 받아야만'(사용자). 비용 천장에 닿으면 거짓 완료가 아니라
    '수렴 소진 — 사람 확인'으로 정직히 상신(허위 완료 방지)."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow({11: "L", 12: "백엔드", 13: "QA"})
    f.floor_mode = "turn-taking"
    CONS = ("[종료]\n[수렴안]\n목표: x\n동작 | curl 확인\n[/수렴안]")

    async def wake(to, b, k):
        if "수렴안 확정 표결" in b:
            return "[반대: 조건이 부실하다]" if to == 13 else "[찬성]"   # QA가 반대 → 부결
        if "종결 확인" in b:
            return CONS if "채택돼야만" in b else "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    r = asyncio.run(t["meet"].handler({"topic": "T", "members": "", "rounds": "2", "my_opinion": "여는 의견"}))
    txt = r["content"][0]["text"]
    assert not [m for m in (f.milestones or []) if m.status not in ("done", "superseded")]  # 부결 → 미등록
    assert "확정 실패 — 수렴 소진" in txt              # 거짓 완료 아닌 정직한 상신


def test_meet_기본은_종전_고정라운드_그대로():
    """ORGANT_FLOOR 미설정 = orchestrated round_robin — 발언 순서·라벨·프롬프트가 종전과 동일
    (동작 불변의 직접 검증; test_sys의 핀 테스트와 이중 안전망)."""
    g, f = _meet_flow({11: "L", 12: "백엔드", 13: "QA"})
    seen = []

    async def wake(to, b, k):
        seen.append((to, b))
        return f"{to}의 입장"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({"topic": "T", "members": "", "rounds": "3", "my_opinion": "소집자 독립의견"}))
    disc = [(to, b) for to, b in seen if "라운드] 주제" in b and "1라운드" not in b]
    assert [d[0] for d in disc] == [12, 13, 12, 13]    # 고정 순서 2R→3R
    assert "[회의 2라운드]" in disc[0][1] and "[회의 3라운드]" in disc[2][1]
    assert all("발언권" not in b for _, b in disc)      # 기본 모드엔 TT 규약·응찰 미주입(프롬프트 불변)


# ══ 실표면 통합 ② — 세그먼트 경계 open(sys_core TRP 훅) ══════════════════════════

def _seg_setup(tmp_path):
    g = FakeGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L", 12: "백엔드"},
            workspace=str(tmp_path), session_dir=str(tmp_path))
    f = _flow(g)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    return s, f


def test_세그먼트open_기본은_noop(tmp_path):
    s, f = _seg_setup(tmp_path)
    calls = []

    async def wake(to, b, k):
        calls.append(to)
        return "발언"
    f.wake = wake
    assert asyncio.run(s._floor_segment_open(f, 11)) == "" and calls == []   # 라이브 불변


def test_세그먼트open_TT_무응찰이면_리더계속과_동형(tmp_path):
    s, f = _seg_setup(tmp_path)
    f.floor_mode = "turn-taking"
    calls = []

    async def wake(to, b, k):
        calls.append((to, b))
        return "[패스]"
    f.wake = wake
    out = asyncio.run(s._floor_segment_open(f, 11))
    assert out == "" and [c for c, _ in calls] == [12]           # 응찰 1회, 무응찰 → 종전 동형
    assert "응찰" in calls[0][1] and f.comm.alive == 11           # 응찰 프롬프트·베턴 리더 유지


def test_세그먼트open_TT_응찰승자_발언은_동봉되고_참여인정(tmp_path):
    s, f = _seg_setup(tmp_path)
    f.floor_mode = "turn-taking"
    calls = []

    async def wake(to, b, k):
        calls.append((to, b))
        if "발언권 응찰" in b:
            return "[응찰: 6] 마이그레이션 순서 위험"
        return "관찰: 마이그레이션 전에 백업 경로부터 확정해야 합니다 — 지금 순서면 롤백 불가."
    f.wake = wake
    out = asyncio.run(s._floor_segment_open(f, 11))
    assert "자기선택 발언" in out and "백업 경로" in out
    assert any("발언권 획득" in b for _, b in calls)              # 낙찰자 정식 발언 경로
    assert 12 in f.current.participated and f.comm.alive == 11
