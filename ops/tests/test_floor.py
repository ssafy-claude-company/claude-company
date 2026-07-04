"""[1층 floor seam — CA-Lab RFC-003] 발언권 순환 추상화 검증.

1층의 성공 기준은 인사이트가 아니라 **'교착 없이 돌고 끝나는가'**(ADR-008 — 층의 책임 기능)다.
여기서 검증하는 것:
  ① 정책 3종(turn-taking·request-response·orchestrated)이 같은 엔진 위에서 교체 가능
  ② turn-taking = Sacks 3규칙(지명→자기선택→계속/소진 종결)이 기계적으로 성립
  ③ request-response 정책의 배분이 라이브 베턴 엔진(CommunicationManager)과 동형(동치성)
  ④ 종결 보장 — 무작위 대본에서도 항상 끝난다(교착·무한 루프 없음)
  ⑤ 실표면 통합 — meet(회의)·세그먼트 경계 open이 seam 뒤에서 동작(기본=동작 불변)
"""
import asyncio
import random

import pytest

from system.guide_tools import Flow
from system.rule.communication import CommunicationManager, _turn_signals
from system.rule.floor import (CLOSE, CONTINUE, NOMINATE, OPEN, SELF, FloorState,
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


def test_TT_규칙3_소진시_현재화자_계속_그리고_한계시_종결():
    st = FloorState([A, B])
    pol = TurnTakingFloor(lapse_limit=2)
    t = Turn(speaker=A, passed=True)
    a1 = pol.on_open_exhausted(st, t)                  # 소진 1회 → ③ 현재 화자 계속
    assert a1.kind == CONTINUE and a1.next == A and st.lapses == 1
    a2 = pol.on_open_exhausted(st, t)                  # 소진 2회 연속 → 자연 종결
    assert a2.kind == CLOSE and st.lapses == 2


def test_TT_실발언은_소진카운터_리셋():
    st = FloorState([A, B])
    pol = TurnTakingFloor()
    st.lapses = 1
    pol.next_after(st, Turn(speaker=A))                # 실발언(무지명) → 리셋 후 open
    assert st.lapses == 0


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


# ══ 선택·팩토리 — '언제든 교체' 지점 ═══════════════════════════════════════════

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

        async def offer(c):
            return Turn(speaker=c)                     # 오퍼 오면 즉시 자기선택

        turns = asyncio.run(run_conversation(pol, st, Turn(speaker=A), speak, offer=offer,
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


def test_엔진_오퍼없는_소비자는_전원패스와_동형():
    """offer 미배선 소비자(위임 경로 초기 상태)의 TT = open이 곧바로 ③계속/소진 경로 —
    현행과 동형으로 안전하게 돈다(자기선택은 오퍼를 배선한 표면부터 점진 활성)."""
    st = FloorState([A, B])
    seq = []

    async def speak(s, alloc):
        seq.append(s)
        return Turn(speaker=s, passed=(len(seq) >= 2))     # 두 번째부터 할 말 없음

    turns = asyncio.run(run_conversation(TurnTakingFloor(), st, Turn(speaker=A), speak,
                                         max_turns=10))
    assert seq and all(s == A for s in seq)            # ③ 현재 화자 계속만 발생
    assert len(turns) <= 4                             # 소진 누적 → 종결(무한 루프 없음)


def test_TT_무작위대본_30회_항상_종결():
    """교착 없음 속성 검증 — 지명·패스·자기선택이 뒤섞인 무작위 대본 30개가 전부 끝난다."""
    async def _one(seed):
        rng = random.Random(seed)
        parts = list(range(1, 2 + rng.randint(1, 4)))
        st = FloorState(parts)

        async def speak(s, alloc):
            r = rng.random()
            addr = rng.choice([p for p in parts if p != s]) if (r < 0.4 and len(parts) > 1) else None
            return Turn(speaker=s, addressee=addr, passed=(r > 0.8))

        async def offer(c):
            return Turn(speaker=c, passed=(rng.random() < 0.5))

        turns = await run_conversation(TurnTakingFloor(), st, Turn(speaker=parts[0]),
                                       speak, offer=offer, max_turns=40)
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

def _meet_flow():
    g = FakeGuide()
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11,
             bot_info={11: "L", 12: "백엔드", 13: "QA"})
    f.start_root("root")
    return g, f


def test_meet_TT_지명이_다음_발언자를_정한다():
    """turn-taking 회의: R1 후 첫 발언권은 자기선택 오퍼(침묵 오래된 순)로 열리고,
    발언 속 [지명]이 다음 화자를 정한다(Sacks ①) — 사회자 고정 라운드가 아니라."""
    g, f = _meet_flow()
    f.floor_mode = "turn-taking"
    seen = []

    async def wake(to, b, k):
        seen.append((to, b))
        if "1라운드" in b:
            return f"{to} 독립 의견"
        if to == 12:
            return "스키마 먼저 확정해야 합니다. QA 관점 확인 필요.\n[지명: 13]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    r = asyncio.run(t["meet"].handler({"topic": "저장 방식", "members": "", "rounds": "2"}))
    txt = r["content"][0]["text"]
    disc = [(to, b) for to, b in seen if "1라운드" not in b]
    assert disc[0][0] == 12 and "발언권 오퍼" in disc[0][1]      # ② 자기선택 오퍼(침묵 오래된 12 먼저)
    assert disc[1][0] == 13 and "차례" in disc[1][1]             # ① 지명 이행(13이 다음 화자)
    assert "[토론]" in txt and "(패스)" in txt                    # 회의록에 토론·패스 기록
    assert f.comm.alive == 11 and 12 in f.current.participated   # 베턴 리더 복귀·참여 인정


def test_meet_TT_전원패스면_조기_자연종결():
    """고정 라운드에선 불가능하던 것: 보탤 말이 없으면 예산을 다 태우지 않고 소진으로 끝난다."""
    g, f = _meet_flow()
    f.floor_mode = "turn-taking"
    seen = []

    async def wake(to, b, k):
        seen.append(b)
        return f"{to} 독립 의견" if "1라운드" in b else "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    r = asyncio.run(t["meet"].handler({"topic": "T", "members": "", "rounds": "3"}))
    disc = [b for b in seen if "1라운드" not in b]
    assert len(disc) < 4                               # 예산 (3-1)×2=4 미만에서 소진 종결
    assert f.comm.alive == 11 and "[회의록]" in r["content"][0]["text"]


def test_meet_기본은_종전_고정라운드_그대로():
    """ORGANT_FLOOR 미설정 = orchestrated round_robin — 발언 순서·라벨·프롬프트가 종전과 동일
    (동작 불변의 직접 검증; test_sys의 핀 테스트와 이중 안전망)."""
    g, f = _meet_flow()
    seen = []

    async def wake(to, b, k):
        seen.append((to, b))
        return f"{to}의 입장"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({"topic": "T", "members": "", "rounds": "3"}))
    disc = [(to, b) for to, b in seen if "라운드] 주제" in b and "1라운드" not in b]
    assert [d[0] for d in disc] == [12, 13, 12, 13]    # 고정 순서 2R→3R
    assert "[회의 2라운드]" in disc[0][1] and "[회의 3라운드]" in disc[2][1]
    assert all("발언권 규약" not in b for _, b in disc)  # 기본 모드엔 TT 규약 미주입(프롬프트 불변)


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


def test_세그먼트open_TT_패스면_리더계속과_동형(tmp_path):
    s, f = _seg_setup(tmp_path)
    f.floor_mode = "turn-taking"
    calls = []

    async def wake(to, b, k):
        calls.append((to, b))
        return "[패스]"
    f.wake = wake
    out = asyncio.run(s._floor_segment_open(f, 11))
    assert out == "" and [c for c, _ in calls] == [12]           # 오퍼 1회, 기여 없음 → 종전 동형
    assert "발언권 오퍼" in calls[0][1] and f.comm.alive == 11    # 베턴 리더 복귀(프레임 닫힘)


def test_세그먼트open_TT_실발언은_동봉되고_참여인정(tmp_path):
    s, f = _seg_setup(tmp_path)
    f.floor_mode = "turn-taking"

    async def wake(to, b, k):
        return "관찰: 마이그레이션 순서가 위험합니다 — 먼저 백업 경로를 확정해야 합니다."
    f.wake = wake
    out = asyncio.run(s._floor_segment_open(f, 11))
    assert "자기선택 발언" in out and "백업 경로" in out
    assert 12 in f.current.participated and f.comm.alive == 11
