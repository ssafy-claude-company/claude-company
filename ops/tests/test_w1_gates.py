"""[W1 — BOT_ARCH_REDESIGN 2026-07-03] 사고 재발점 폐쇄 게이트 검증.

B-02 [G2] clarify 행동 잠금(from==me_id 키잉) / B-03 [G4] 연속 실패 하드블록(자기치유형)
B-04 [G1] 미완 owner 보호(축소판 + SYS 예외) / B-05 [G5] 존재이유 게이트 + set_goal 보류 병합
B-06 [G3] 캐주얼 도구 미장착(좁은 판정 + run_turn 호이스트)
"""
import asyncio
import time

from test_sys import FakeGuide, _flow, _tools

from system.guide_tools import Flow, make_guide_tools
from system.protocol import Kind
from system.rule.communication import HARD_BLOCK_TRANSIENT, _body_overlap, _clarify_hold
from system.sys_core import Sys, _casual_turn


def _flow4(g):
    """3직군(백엔드×2·프론트) 흐름 — W1 게이트 전용(검증 대상 게이트만 개별 테스트에서 끔)."""
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11,
             bot_info={11: "리더", 12: "백엔드", 13: "백엔드", 14: "프론트엔드"})
    f.start_root("root")
    for a in ("gap_checked", "percept_checked", "acceptance_checked", "decomp_checked",
              "data_prov_checked", "staffing_exempt", "iface_dialogue_checked",
              "offdomain_checked", "crossdomain_checked", "existence_checked",
              "owner_protect_checked"):
        setattr(f, a, True)
    return f


# ──────────────────────────── B-02 [G2] clarify 행동 잠금 ────────────────────────────

def test_G2_clarify후_같은봇은_request_run_잠금_타봇은_통과():
    """되묻기(pending_clarify)를 남긴 봇(from==me_id)은 답을 받기 전 request/run이 [대기]로 잠긴다 —
    세팅과 소비 사이 창에서의 '추측 진행'(라이브 재발점 ②)을 프롬프트가 아니라 구조로 차단.
    키잉은 from==me_id 한정 — 위임자·제3자는 잠기지 않는다(stale 잠금 회귀 방지)."""
    g = FakeGuide()
    f = _flow4(g)
    tools11 = {t.name: t for t in make_guide_tools(f, 11, "leader")}
    asyncio.run(tools11["create_task"].handler({"purpose": "p", "members": "12,13"}))
    f.comm.request(11, 12, "r1", Kind.WORK)            # 11→12 위임 → 12의 직속위임자=11
    tools12 = {t.name: t for t in make_guide_tools(f, 12, "member")}
    r = asyncio.run(tools12["request"].handler(
        {"to_id": "11", "kind": "Info", "body": "필드명 X 맞나요?"}))
    assert "확인요청" in r["content"][0]["text"]
    assert f.pending_clarify == {"from": 12, "to": 11, "q": "필드명 X 맞나요?"}
    # 같은 봇(12)의 후속 request → 잠금
    r2 = asyncio.run(tools12["request"].handler({"to_id": "13", "kind": "Info", "body": "이건 뭐죠?"}))
    assert "[대기] 되묻기가 위임자에게 전달 중" in r2["content"][0]["text"]
    # 같은 봇(12)의 run → 잠금(추측 실행 금지)
    r3 = asyncio.run(tools12["run"].handler({"command": "echo hi"}))
    assert "[대기] 되묻기가 위임자에게 전달 중" in r3["content"][0]["text"]
    # 타봇(위임자 11·제3자 13)은 잠기지 않는다(from==me_id 키잉)
    assert _clarify_hold(f, 11) is None and _clarify_hold(f, 13) is None
    # 소비(위임자에게 표면화 — _deliver가 None으로 소거)되면 해제
    f.pending_clarify = None
    assert _clarify_hold(f, 12) is None


def test_G2_vote_meet도_같은조건으로_잠금():
    """vote/meet 경로도 같은 1조건(fork_active 게이트 동형) — 되묻기 중 수집·회의 개시 금지."""
    g = FakeGuide()
    f = _flow4(g)
    tools11 = {t.name: t for t in make_guide_tools(f, 11, "leader")}
    asyncio.run(tools11["create_task"].handler({"purpose": "p", "members": "12,13"}))
    f.pending_clarify = {"from": 11, "to": 0, "q": "?"}
    rv = asyncio.run(tools11["vote"].handler({"question": "q", "options": "a;b", "members": "12,13"}))
    assert "[대기] 되묻기가 위임자에게 전달 중" in rv["content"][0]["text"]
    rm = asyncio.run(tools11["meet"].handler({"topic": "t", "members": "12,13"}))
    assert "[대기] 되묻기가 위임자에게 전달 중" in rm["content"][0]["text"]
    f.pending_clarify = None
    assert _clarify_hold(f, 11) is None


# ──────────────────────────── B-03 [G4] 연속 실패 하드블록 ────────────────────────────

def _fail_flow(g):
    f = _flow(g)
    tools = _tools(f, 11, "leader")
    asyncio.run(tools["create_task"].handler({"purpose": "p", "members": "12"}))
    f.current.participated.add(12)
    asyncio.run(tools["set_goal"].handler({"goal": "동작"}))
    return f, tools


def test_G4_시간창내_연속실패_3회면_하드블록_세팅():
    """consec_fail>=2는 recruit만 막고 흐름은 계속 돌았다(재발점 ④ — P-031형 밤새 소각). 시간창 내
    연속 3회 무응답이면 flow._hard_blocked(연속 무응답 마커)를 세워 이어가기 루프를 정지시킨다."""
    g = FakeGuide()
    f, tools = _fail_flow(g)

    async def wake(to, b, k):
        return "API Error: crash"
    f.wake = wake
    for _ in range(2):
        asyncio.run(tools["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    assert f.consec_fail == 2 and not getattr(f, "_hard_blocked", None)   # 2회까진 종전(블립 여지)
    asyncio.run(tools["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    assert f.consec_fail == 3 and f._hard_blocked == HARD_BLOCK_TRANSIENT


def test_G4_실패2회_성공1회면_블록없음_블립회복_보존():
    """실패 2회 뒤 정상 응답 1회 → consec_fail 리셋(기존 :1612 유지) — 하드블록 미발동(블립 회복 보존)."""
    g = FakeGuide()
    f, tools = _fail_flow(g)
    seq = ["API Error: crash", "API Error: crash", "API Error: crash", "API Error: crash", "완료"]

    async def wake(to, b, k):
        return seq.pop(0)
    f.wake = wake
    # 1·2건째: transient 재시도(각 2콜) 소진 → 실패 1·2 / 3건째: 정상 응답 → 리셋
    asyncio.run(tools["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    asyncio.run(tools["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    assert f.consec_fail == 2
    asyncio.run(tools["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    assert f.consec_fail == 0 and not getattr(f, "_hard_blocked", None)


def test_G4_시간창_밖의_3회째는_블록안함():
    """연속 3회라도 첫 실패에서 시간창(ORGANT_HARDBLOCK_WINDOW)을 넘겨 흩어졌으면 '지속 불안정'이
    아니다 — 블록하지 않는다(과차단 방지)."""
    g = FakeGuide()
    f, tools = _fail_flow(g)

    async def wake(to, b, k):
        return "API Error: crash"
    f.wake = wake
    for _ in range(2):
        asyncio.run(tools["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    f._consec_fail_t0 = time.monotonic() - 10 ** 6      # 스트릭 시작이 창 밖(옛날)이라고 모의
    asyncio.run(tools["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    assert f.consec_fail == 3 and not getattr(f, "_hard_blocked", None)


def test_G4_프로브_성공시_자동해제_실패면_유지_사람조치형은_비대상(monkeypatch):
    """자기치유: 백오프 뒤 SYS 프로브 wake 1회 성공 → 해제·카운터 리셋. 실패 → 블록 유지.
    '사람 조치' 하드블록(배포 자격증명)은 프로브 비대상 — 종전 종결 동작 불변."""
    monkeypatch.setenv("ORGANT_HARDBLOCK_BACKOFF", "0")
    g = FakeGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L", 12: "M"})
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11, bot_info={11: "L", 12: "M"})
    f._hard_blocked, f.consec_fail = HARD_BLOCK_TRANSIENT, 3

    async def wake_ok(to, b, k):
        return "응답 가능합니다"
    f.wake = wake_ok
    assert asyncio.run(s._hard_block_probe(f, 11)) is True
    assert f._hard_blocked is None and f.consec_fail == 0            # 해제 + 리셋(재개)
    f._hard_blocked = HARD_BLOCK_TRANSIENT

    async def wake_bad(to, b, k):
        return "API Error: still down"
    f.wake = wake_bad
    assert asyncio.run(s._hard_block_probe(f, 11)) is False
    assert f._hard_blocked == HARD_BLOCK_TRANSIENT                   # 실패 → 유지(종결)
    f._hard_blocked = "배포 자격증명 미설정 — 소유자 금고에 키 필요(사람 조치)"
    assert asyncio.run(s._hard_block_probe(f, 11)) is False
    assert f._hard_blocked.startswith("배포 자격증명")               # 프로브가 못 풂(사람 조치 전용)


def test_G4_사용자_개입도_해제트리거_deliver_human_info():
    """사람의 진행 중 개입(deliver_human_info)은 '연속 무응답' 하드블록을 푼다(loop_escalated의
    사용자 해제 패턴 동형 — 사람이 온 것이 곧 판정·방향 신호)."""
    g = FakeGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L", 12: "M"})
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11, bot_info={11: "L", 12: "M"})
    f._hard_blocked, f.consec_fail = HARD_BLOCK_TRANSIENT, 3
    s.active_flows["P-X"] = f
    assert s.deliver_human_info(500, 12, "이 방향으로 계속해") is True
    assert f._hard_blocked is None and f.consec_fail == 0


# ──────────────────────────── B-04 [G1] 미완 owner 보호 ────────────────────────────

def test_G1_미완owner_동직군거부_별산출물통과_takeover통과_SYS우회():
    """owner_incomplete 중 타인 fresh-Work: (a)직군 교집합 or (b)body 실질 중복만 거부(축소판) —
    다도메인 단일 Task의 정상 위임(타직군·별산출물)은 통과. 탈출 2갈래(takeover_reason/
    different_deliverable)와 SYS 내부 발사(_sys_dispatch) 우회를 검증(조율 큐 유실 0)."""
    g = FakeGuide()
    f = _flow4(g)
    f.owner_protect_checked = False                     # 이 게이트만 켠다
    waked = []

    async def wake(to, b, k):
        waked.append(to)
        return "완료"                                    # 실작업 0 → owner 미착수=구조적 미완
    f.wake = wake
    tools = {t.name: t for t in make_guide_tools(f, 11, "leader")}
    asyncio.run(tools["create_task"].handler({"purpose": "p", "members": "12,13,14"}))
    f.current.participated.update({12, 14})
    asyncio.run(tools["set_goal"].handler({"goal": "동작"}))
    asyncio.run(tools["request"].handler(
        {"to_id": "12", "kind": "Work", "body": "서버 API 엔드포인트 구현"}))
    assert f.current.owner == 12 and f.current.owner_incomplete      # 미착수 → 미완 owner
    # (a) 동직군(백엔드 13) fresh-Work → 거부(owner 덮어쓰기 차단 — 재발점 ①)
    r1 = asyncio.run(tools["request"].handler(
        {"to_id": "13", "kind": "Work", "body": "데이터베이스 스키마 정리"}))
    assert "미완 owner 보호" in r1["content"][0]["text"] and 13 not in waked
    # (b) 타직군(14)이라도 body가 owner 위임 원문과 실질 중복이면 거부
    r2 = asyncio.run(tools["request"].handler(
        {"to_id": "14", "kind": "Work", "body": "서버 API 엔드포인트 구현"}))
    assert "미완 owner 보호" in r2["content"][0]["text"] and 14 not in waked
    # 타직군 + 별산출물 → 통과(다도메인 수렴의 정상 위임 과차단 금지)
    r3 = asyncio.run(tools["request"].handler(
        {"to_id": "14", "kind": "Work", "body": "화면 레이아웃 버튼 디자인"}))
    assert "미완 owner 보호" not in r3["content"][0]["text"] and 14 in waked
    # 동직군이라도 takeover_reason 명시 → 의식적 교체 통과 + per-Task 통과 기록
    r4 = asyncio.run(tools["request"].handler(
        {"to_id": "13", "kind": "Work", "body": "데이터베이스 스키마 정리",
         "takeover_reason": "12 연속 미착수 — 담당 교체"}))
    assert "미완 owner 보호" not in r4["content"][0]["text"] and 13 in waked
    assert ("owner_protect", f.current.task_id) in f._gate_pass
    # SYS 내부 발사(_auto_coordinate 경유)는 우회 — 조율 항목 조용한 유실 방지
    f._gate_pass.discard(("owner_protect", f.current.task_id))
    f._sys_dispatch = True
    r5 = asyncio.run(tools["request"].handler(
        {"to_id": "13", "kind": "Work", "body": "서버 API 엔드포인트 구현"}))
    assert "미완 owner 보호" not in r5["content"][0]["text"]
    f._sys_dispatch = False


def test_G1_같은owner_이어가기와_인도후_검증위임은_비대상():
    """같은 owner 재위임(이어가기)과 owner_delivered=True(인도 후 검증 위임)는 게이트 비대상 —
    교차검증 경로 무영향."""
    g = FakeGuide()
    f = _flow4(g)
    f.owner_protect_checked = False

    async def wake(to, b, k):
        return "완료"
    f.wake = wake
    tools = {t.name: t for t in make_guide_tools(f, 11, "leader")}
    asyncio.run(tools["create_task"].handler({"purpose": "p", "members": "12,13"}))
    f.current.participated.update({12})
    asyncio.run(tools["set_goal"].handler({"goal": "동작"}))
    asyncio.run(tools["request"].handler({"to_id": "12", "kind": "Work", "body": "서버 구현"}))
    assert f.current.owner_incomplete
    # 같은 owner(12)에게 '이어서' → 통과(권장 경로)
    r = asyncio.run(tools["request"].handler({"to_id": "12", "kind": "Work", "body": "이어서 서버 구현"}))
    assert "미완 owner 보호" not in r["content"][0]["text"]
    # 인도 후(owner_delivered=True)의 타인 Work(검증 위임) → 비대상
    f.current.owner_delivered = True
    r2 = asyncio.run(tools["request"].handler({"to_id": "13", "kind": "Work", "body": "서버 구현 검증"}))
    assert "미완 owner 보호" not in r2["content"][0]["text"]


def test_G1_body_실질중복_판정():
    assert _body_overlap("서버 API 엔드포인트 구현", "서버 API 엔드포인트 구현 이어서") is True
    assert _body_overlap("서버 API 엔드포인트 구현", "화면 레이아웃 버튼 디자인") is False
    assert _body_overlap("", "무엇이든") is False


# ──────────────────────── B-05 [G5] 존재이유 게이트 + 보류 병합 ────────────────────────

def test_G5_존재이유_빈값은_1회보류_재호출통과():
    """existence_test 빈 값 → 1회 보류(도구 description에만 있던 존재이유 테스트의 게이트화 —
    재발점 ⑤). 재호출은 통과(무한 반려 금지 — 판단은 리더)."""
    g = FakeGuide()
    f = _flow(g)
    f.existence_checked = False                          # 이 게이트만 켠다
    tools = _tools(f, 11, "leader")
    asyncio.run(tools["create_task"].handler({"purpose": "p", "members": "12"}))
    f.current.participated.add(12)
    r1 = asyncio.run(tools["set_goal"].handler({"goal": "동작"}))
    assert "존재이유" in r1["content"][0]["text"] and not f.current.status.goal
    r2 = asyncio.run(tools["set_goal"].handler({"goal": "동작"}))    # 1회 보류 → 재호출 통과
    assert f.current.status.goal == "동작"


def test_G5_존재이유_인자는_acceptance에_항목으로_박제():
    """existence_test 인자를 주면 보류 없이 통과 + acceptance에 '[존재이유]' 항목으로 영속
    (마감 게이트의 회계 근거)."""
    g = FakeGuide()
    f = _flow(g)
    f.existence_checked = False
    tools = _tools(f, 11, "leader")
    asyncio.run(tools["create_task"].handler({"purpose": "p", "members": "12"}))
    f.current.participated.add(12)
    asyncio.run(tools["set_goal"].handler(
        {"goal": "인증 동작", "existence_test": "틀린 토큰은 거부된다"}))
    assert f.current.status.goal == "인증 동작"
    assert "[존재이유] 틀린 토큰은 거부된다" in f.current.acceptance


def test_G5_보류병합_한번의_거부에_전부나열_왕복2회이내():
    """기존 직렬 보류(최대화 등)와 존재이유 보류가 **한 번의 거부**에 함께 나열된다 —
    항목당 한 왕복씩 최악 5연쇄 왕복하던 것 차단(왕복 ≤2)."""
    g = FakeGuide()
    f = _flow(g)
    f.existence_checked = False
    f.gap_checked = False                                # 최대화 보류도 켠다
    tools = _tools(f, 11, "leader")
    asyncio.run(tools["create_task"].handler({"purpose": "p", "members": "12"}))
    f.current.participated.add(12)
    r1 = asyncio.run(tools["set_goal"].handler({"goal": "동작"}))
    txt = r1["content"][0]["text"]
    assert "병합" in txt and "최대화 기준" in txt and "존재이유" in txt   # 미충족 전부 한 번에
    assert not f.current.status.goal
    r2 = asyncio.run(tools["set_goal"].handler(
        {"goal": "동작", "standard": "부품 A·B·C", "existence_test": "틀린 토큰은 거부"}))
    assert f.current.status.goal == "동작"               # 2번째 호출로 확정(왕복 ≤2)


def test_G5_마감은_존재이유_회계를_별도요구():
    """acceptance에 '[존재이유]'가 박힌 Task는 complete_task result가 그 실행 회계를 별도로 담아야
    한다 — 누락 시 거부, '[존재이유 검증]' 회계(또는 사유 있는 N/A)로 통과."""
    g = FakeGuide()
    f = _flow(g)
    f.existence_checked = False
    tools = _tools(f, 11, "leader")
    asyncio.run(tools["create_task"].handler({"purpose": "p", "members": "12"}))
    f.current.participated.add(12)
    asyncio.run(tools["set_goal"].handler(
        {"goal": "인증 동작", "existence_test": "틀린 토큰은 거부된다"}))
    f.current.verified = True                            # run 게이트 통과 모의
    r1 = asyncio.run(tools["complete_task"].handler({"result": "다 됐습니다"}))
    assert "존재이유" in r1["content"][0]["text"] and f.current is not None   # 회계 누락 → 거부
    r2 = asyncio.run(tools["complete_task"].handler(
        {"result": "[존재이유 검증] 틀린 토큰으로 e2e 요청 → 401 거부 확인"}))
    assert f.current is None                             # 회계 동봉 → 마감


# ──────────────────────── B-06 [G3] 캐주얼 도구 미장착 ────────────────────────

def test_G3_casual모드는_run만_collab기본은_전체장착():
    g = FakeGuide()
    f = _flow(g)
    names = {t.name for t in make_guide_tools(f, 11, "leader", mode="casual")}
    assert names == {"run"}                              # request·recruit·리더도구 미장착(구조 차단)
    full = {t.name for t in make_guide_tools(f, 11, "leader")}   # 기본값 collab = 현행 동일(하위호환)
    assert {"request", "recruit", "run", "create_project", "create_task",
            "set_goal", "complete_task", "vote", "meet"} <= full


def test_G3_좁은판정_캐주얼만_casual_빌드동사나_Info단독은_아님():
    assert _casual_turn("점심 맛집 추천해줘", "leader") is True          # 캐주얼 신호+빌드동사 없음
    assert _casual_turn("추천 시스템 만들어줘", "leader") is False       # 빌드 동사 → 전체 장착
    assert _casual_turn("팀 토론 진행해줘", "leader") is False           # Info 단독 경로(신호 없음) 제외
    assert _casual_turn("점심 맛집 추천해줘", "member") is False         # 워커 턴 비대상
