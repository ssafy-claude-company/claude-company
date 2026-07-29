"""[W3 — BOT_ARCH_REDESIGN 2026-07-03] 도구 계약화 → 프롬프트 다이어트.

B-14 report 도구(스태시형·이중 수용: 인자 > regex, [직군밖]/[경험] regex 폴백 존치, Response=반환값)
B-15 cast_vote 도구(fork 가지 전용 장착, [표] regex 폴백 존치 — 무효표 소멸)
B-16 게이트 마커 인자화(set_goal·complete_task·request — 마커/인자/양쪽 3경로 이중 수용)
B-17 A(중복) 프롬프트 삭제(각 백스톱 게이트 실재 확인 후 — 사실통지 축소)
B-18 B 이동(축소판 — _PRINCIPLE 3블록→PLAYBOOK+1줄 참조·워커 push 유지·에세이 압축·list_projects 보강)
"""
import asyncio
import os

from test_sys import FakeGuide, _flow, _tools

from system.guide_tools import Flow, make_guide_tools
from system.protocol import Kind
from system.sys_core import Sys, _CONTINUE_BODY


def _flow3(g, tmp_path=None, bot_info=None):
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11,
             bot_info=bot_info or {11: "리더", 12: "백엔드", 13: "QA"})
    f.start_root("root")
    if tmp_path is not None:
        f.workspace = str(tmp_path)
    for a in ("gap_checked", "percept_checked", "acceptance_checked", "decomp_checked",
              "data_prov_checked", "staffing_exempt", "iface_dialogue_checked",
              "offdomain_checked", "crossdomain_checked", "existence_checked",
              "owner_protect_checked", "team_checked"):
        setattr(f, a, True)
    return f


# ─────────────────────────── B-14 report 도구 ───────────────────────────

def test_B14_report_멤버장착_리더미장착_스태시():
    f = _flow(FakeGuide())
    member = _tools(f, 12, "member")
    leader = _tools(f, 11, "leader")
    assert "report" in member and "report" not in leader
    out = asyncio.run(member["report"].handler(
        {"result": "완료", "changes": "a.js", "verify": "run ok", "risks": "없음",
         "offdomain_role": "", "experience": "교훈1"}))
    # Response는 여전히 턴 반환값 — 도구는 스태시일 뿐임을 안내한다.
    assert "Response" in out["content"][0]["text"]
    assert f.report_stash[12]["changes"] == "a.js" and f.report_stash[12]["experience"] == "교훈1"


def _delegated(f, wake, members="12"):
    """create_task→set_goal→Work 위임 1건 헬퍼(참여 게이트 충족)."""
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": members}))
    for m in [x for x in f.current.team if x != 11]:
        f.current.participated.add(m)
    asyncio.run(t["set_goal"].handler({"goal": "g"}))
    return t


def test_B14_offdomain_인자가_regex보다_우선_반려():
    """report(offdomain_role=…) 인자만으로(첫줄 [직군밖] regex 없이) 직군밖 반려가 성립하고
    소유가 해제된다 — 인자 > regex."""
    g = FakeGuide()
    f = _flow3(g)

    async def wake(to, b, k):
        f.act_count += 1
        f.report_stash[to] = {"offdomain_role": "AI 엔지니어"}
        return "이 일은 제 전문이 아닙니다"          # 마커 없음 — 인자 단독 경로
    t = _delegated(f, wake)
    r = asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "AI 모델"}))
    txt = r["content"][0]["text"]
    assert "직군밖 반려" in txt and "AI 엔지니어" in txt
    assert f.current.owner == 0                       # 소유 해제(채용 전문가가 새 owner)


def test_B14_직군밖_regex_폴백_존치():
    g = FakeGuide()
    f = _flow3(g)

    async def wake(to, b, k):
        f.act_count += 1
        return "[직군밖] 사운드 디자이너\n이건 제 직군 밖입니다"
    t = _delegated(f, wake)
    r = asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "효과음"}))
    assert "직군밖 반려" in r["content"][0]["text"] and "사운드 디자이너" in r["content"][0]["text"]


def test_B14_stale_스태시는_wake전에_소거():
    """이전 턴(fork 가지 등)에서 남은 스태시가 이번 위임의 보고로 오인되지 않는다."""
    g = FakeGuide()
    f = _flow3(g)
    f.report_stash[12] = {"offdomain_role": "유령 직군"}    # stale

    async def wake(to, b, k):
        f.act_count += 1
        return "정상 구현·검증 완료"
    t = _delegated(f, wake)
    r = asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    assert "직군밖" not in r["content"][0]["text"]
    assert f.current.owner_delivered is True


def test_offdomain_부정센티넬은_반려아님_소유유지(monkeypatch, tmp_path):
    """[라이브 P-016 근본버그(2026-07-14)] 봇이 offdomain_role='해당없음'/'없음'(=직군밖 아님)으로 답하면
    종전엔 non-empty라 *직군밖 반려*로 오분류돼 파일 소유가 유령 직군 '해당없음'으로 이전, app.js 등
    10개 파일이 아무도 못 고쳐 7시간 교착. 이제 부정 센티넬은 '반려 없음'과 동치 — 소유 무변경."""
    import os
    from system.rule.communication import _norm_offdomain
    assert _norm_offdomain("해당없음") == "" and _norm_offdomain("없음") == "" and _norm_offdomain("N/A") == ""
    assert _norm_offdomain("AI 엔지니어") == "AI 엔지니어"          # 진짜 직군명은 통과
    for sentinel in ("해당없음", "없음", "N/A", "-"):
        g = FakeGuide()
        f = _flow3(g)
        fp = os.path.join(str(tmp_path), "app.js"); open(fp, "w").write("//x")
        f.file_owner = {os.path.realpath(fp): "프론트"}          # 프론트 소유 파일
        async def wake(to, b, k, _s=sentinel):
            f.act_count += 1
            f.report_stash[to] = {"offdomain_role": _s}          # 부정 센티넬 응답
            return "정상 구현·검증 완료"
        t = _delegated(f, wake)
        r = asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "app.js 수정"}))
        assert "직군밖" not in r["content"][0]["text"], f"센티넬 {sentinel}이 반려로 오분류됨"
        assert f.file_owner.get(os.path.realpath(fp)) == "프론트", f"센티넬 {sentinel}이 소유를 유령 이전"


def test_B14_REPORTS에_구조화필드_동봉(tmp_path):
    from system._util import dossier_rel
    g = FakeGuide()
    f = _flow3(g, tmp_path)

    async def wake(to, b, k):
        f.act_count += 1
        f.report_stash[to] = {"result": "완료", "changes": "server.js", "verify": "curl 200",
                              "risks": "없음"}
        return "[결과] 완료 / 구현했습니다"
    t = _delegated(f, wake)
    asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    doc = open(os.path.join(str(tmp_path), dossier_rel(f.current.task_id), "REPORTS.md"),
               encoding="utf-8").read()
    assert "[report 도구 — 구조화 필드]" in doc and "[변경] server.js" in doc


def test_B14_재요청훅_기본on_보고계약없으면_1회반려(monkeypatch):
    """[표식 없음 = 반려(2026-07-29, 사용자: '봇 실수로 값 안넣은거지 반려로 다시 받는게 맞아')]
    도구 미호출 AND 보고 계약 미검출 응답은 **기본으로** 1회 반려(재요청)한다. =0으로만 끈다."""
    def _run(expect_wakes):
        g = FakeGuide()
        f = _flow3(g)
        calls = []

        async def wake(to, b, k):
            calls.append(b)
            f.act_count += 1
            return "그냥 산문 보고 — 마커도 도구도 없음"
        t = _delegated(f, wake)
        asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
        assert len(calls) == expect_wakes
        return calls

    monkeypatch.delenv("ORGANT_REPORT_REASK", raising=False)
    calls = _run(2)                                    # 기본 on — 1회만 반려
    assert "보고 형식 재요청" in calls[1]
    monkeypatch.setenv("ORGANT_REPORT_REASK", "0")
    _run(1)                                            # 명시적으로만 끈다


def test_B14_경험_스태시_흡수_regex폴백_존치():
    """run_turn이 report 스태시의 experience/craft_standard를 [경험]/[직무기준] 블록과 같은 경로로
    흡수한다([격리] 전부 보고 봇 자신의 개인 풀·개인 기준으로 영속) — 블록 regex 폴백은 종전 그대로."""
    g = FakeGuide()
    f = Flow(g, channel_id=1, guild_id=1, leader_id=11, bot_info={11: "L", 12: "백엔드"})
    f.start_root("root")

    class _Worker:
        async def handle(self, prompt):
            f.report_stash[12] = {"experience": "빌드 캐시는 루트에", "craft_standard": "기준 v9"}
            return "본문 보고"

    s = Sys(g, guild_id=1, organt_builder=lambda oid, srv, role, flow=None: _Worker(),
            bot_info={11: "L", 12: "백엔드"})
    import system.sys_core as sc
    _orig = sc.build_guide_server
    sc.build_guide_server = lambda *a, **k: object()
    try:
        out = asyncio.run(s.run_turn(f, 12, "b", Kind.WORK, "member"))
    finally:
        sc.build_guide_server = _orig
    assert out == "본문 보고"                            # Response는 그대로(스태시가 본문을 안 바꿈)
    assert "빌드 캐시는 루트에" in (s.bot_experience.get(12) or [])
    assert s.bot_profiles.get(12) == "기준 v9"          # [격리] 자기 개인 기준으로(직군 공용 아님)
    assert not s.role_profiles.get("백엔드")            # ★직군 공용엔 안 감
    # 소비된 키는 pop — offdomain 등 나머지 필드 소비(_deliver)와 분리
    assert "experience" not in f.report_stash[12]


# ─────────────────────────── B-15 cast_vote ───────────────────────────

def test_B15_cast_vote_fork가지에만_장착():
    f = _flow(FakeGuide())
    assert "cast_vote" not in _tools(f, 12, "member")
    f.fork_kind[12] = Kind.INFO                        # 가지 세션(서버 빌드 전 세팅)
    assert "cast_vote" in _tools(f, 12, "member")


def test_B15_표결_인자우선_regex폴백_무효표소멸():
    """가지 A는 cast_vote 도구만(마커 없는 산문 반환), 가지 B는 종전 [표] 마커 — 둘 다 집계된다."""
    g = FakeGuide()
    f = _flow3(g)

    async def wake(to, b, k):
        if to == 12:
            f.vote_stash[12] = {"option": "B안", "reason": "성능이 낫다"}
            return "고민 끝에 결정했습니다"             # 마커 없음 — 종전엔 무효표
        return "[표] A안\n호환성이 낫다"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    r = asyncio.run(t["vote"].handler({"question": "안건", "options": "A안;B안"}))
    txt = r["content"][0]["text"]
    assert "A안: 1관점" in txt and "B안: 1관점" in txt and "무효" not in txt
    assert not f.vote_stash                            # 수합 시 pop(다음 표결 오염 없음)


# ─────────────────────────── B-16 마커 인자화 ───────────────────────────

def test_B16_setgoal_스태핑면제_마커·인자·양쪽_3경로():
    def _hold(kw):
        g = FakeGuide()
        f = _flow3(g, bot_info={11: "리더(백엔드)", 12: "프론트엔드"})
        f.staffing_exempt = False
        t = _tools(f, 11, "leader")
        asyncio.run(t["create_task"].handler({"members": "12"}))
        f.current.participated.add(12)
        r = asyncio.run(t["set_goal"].handler(
            {"goal": "머신러닝 모델 학습으로 예측", **kw}))
        return r["content"][0]["text"], f
    # 무인자 → 보류(regex 경로의 전제) / 마커 / 인자 / 양쪽 전부 통과
    txt, _ = _hold({})
    assert "스태핑 커버리지" in txt
    txt, _ = _hold({"acceptance": "[스태핑 면제: 리더가 커버]"})   # 종전 마커 경로(폴백 존치)
    assert "정의 확정" in txt
    txt, f = _hold({"staffing_waiver": "리더가 ML 역량 보유"})
    assert "정의 확정" in txt
    assert "[스태핑 면제: 리더가 ML 역량 보유]" in f.current.status.goal   # 인자→마커 합성(기록 동등)
    txt, _ = _hold({"staffing_waiver": "사유", "acceptance": "[스태핑 면제: 이미 마커]"})
    assert "정의 확정" in txt


def test_B16_setgoal_최대화NA·심도단독_인자():
    g = FakeGuide()
    f = _flow3(g, bot_info={11: "리더", 12: "AI 엔지니어"})
    f.gap_checked = False
    f.staffing_exempt = False
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.participated.add(12)
    goal = "머신러닝 모델 학습으로 예측"          # AI 능력 요구 — 커버 1명(심도)·standard 없음(최대화)
    r = asyncio.run(t["set_goal"].handler({"goal": goal}))
    txt = r["content"][0]["text"]
    assert "최대화 기준" in txt and "협업 깊이" in txt          # 병합 보류(무인자)
    r = asyncio.run(t["set_goal"].handler(
        {"goal": goal, "maximal_na": "단순 스크립트", "depth_solo": "AI/ML — 1명으로 충분"}))
    assert "정의 확정" in r["content"][0]["text"]


def test_B16_complete_인자경로_전게이트(tmp_path):
    """complete_task 인자 6종이 종전 result 마커와 동등하게 게이트를 연다(percept·visual·data·
    acceptance·standard·contrib) — 마커는 마감 기록에도 종전처럼 남는다."""
    g = FakeGuide()
    f = _flow3(g, tmp_path)
    (tmp_path / "index.html").write_text("<html></html>")            # 시각 런타임
    (tmp_path / "train.py").write_text("df = make_synthetic()")      # 데이터 합성 흔적
    f.origin_request = "공공데이터를 받아 AI 학습"                     # 데이터출처 게이트 발동 조건
    for a in ("percept_checked", "acceptance_checked", "data_prov_checked"):
        setattr(f, a, False)
    f.visual_checked = False
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.verified = True
    f.current.owner = 12
    f.current.owner_delivered = True
    f.current.status.owner = "백엔드"
    f.current.cross_checks = 1
    f.current.cross_check_offdomain = 1
    f.current.standard = "부품A\n부품B"
    r = asyncio.run(t["complete_task"].handler({"result": "done"}))
    assert "마감 보류" in r["content"][0]["text"]                     # 무인자 — 종전 보류(regex 전제)
    r = asyncio.run(t["complete_task"].handler({
        "result": "done",
        "percept_na": "오디오 차원 없음(정적 웹)",
        "visual_evidence": "스크린샷으로 레이아웃·색 확인",
        "data_source": "data.go.kr 벌크 CSV",
        "acceptance_check": "- 항목1: 충족(실측)\n- 항목2: 충족",
        "standard_check": "- 부품A: 있음\n- 부품B: 있음",
        "contrib_waiver": "QA는 이번 산출물엔 검증 불필요"}))
    assert "완료 마감" in r["content"][0]["text"] and f.current is None
    done = f.tasks[-1]
    assert "[지각차원 없음:" in done.status.result                    # 인자→마커 합성이 기록에 남음


def test_B16_request_override_reason_직군초과와_동등():
    g = FakeGuide()
    f = _flow3(g, bot_info={11: "리더", 12: "백엔드", 14: "AI 엔지니어"})
    f.offdomain_checked = False

    async def wake(to, b, k):
        f.act_count += 1
        return "구현 완료"
    t = _delegated(f, wake, members="12,14")
    body = "머신러닝 모델 학습시키기"
    r = asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": body}))
    assert "직군밖 — 능력 미스매치" in r["content"][0]["text"]        # 무인자 — 종전 차단
    r = asyncio.run(t["request"].handler(
        {"to_id": "12", "kind": "Work", "body": body, "override_reason": "AI가 타흐름 점유"}))
    assert "직군밖" not in r["content"][0]["text"]                    # 인자 = [직군초과] 마커와 동등
    sent = [c for c in g.calls if c[0] == "req"][-1]
    assert "[직군초과: AI가 타흐름 점유]" in sent[3]                   # 마커 합성이 위임 기록에 남음


# ─────────────────────────── B-17 프롬프트 다이어트 ───────────────────────────

def test_B17_리더프롬프트_시스템강제_삭제_존치블록_보존():
    s = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={11: "리더", 12: "백엔드"})
    p = s._prompt("게임 만들어줘", Kind.WORK, "leader", 11, 11, None)
    assert "(시스템 강제)" not in p                    # 8블록 삭제(백스톱: 게이트·훅 실재)
    for gone in ("[구현은 위임", "[전문 능력은 흡수", "[재요청은 Redo로", "[협업 인터페이스]",
                 "[owner가 일하는 중엔", "[검증·배포]"):
        assert gone not in p
    # 게이트 백스톱 없는 첫-시도 형성 문구는 존치(설계 조건 — "위임은 측정가능한 목표로" 류)
    for keep in ("[퍼실리테이터", "[당신의 위치", "측정가능한 목표", "[팀 구성]", "[처리 갈래]",
                 "[무응답 시 독점 금지]"):
        assert keep in p


def test_B17_CONTINUE_BODY_행동지시_삭제():
    assert "이어서 계속" in _CONTINUE_BODY             # 사실 지시(연속 실행)는 유지
    assert "비동기" not in _CONTINUE_BODY              # 행동지시 단락 삭제(백스톱: [대기] 게이트·자동 이어가기)


def test_B17_멤버_교차도메인_단락_축소():
    s = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={11: "리더", 12: "백엔드"})
    p = s._prompt("구현", Kind.WORK, "member", 12, 11, None)
    assert "[이니셔티브의 방향]" in p                  # 축소판(2줄 사실)
    assert "야심은 보고로, 코디네이션은 리더에게" not in p   # 장문 행동지시 삭제(백스톱: 교차도메인 게이트+_auto_coordinate)


# ─────────────────────────── B-18 B 이동(축소판) ───────────────────────────

def test_B18_PRINCIPLE_이동과_워커_레이아웃_push_유지():
    s = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={11: "리더", 12: "백엔드"})
    # 이동 3블록은 _PRINCIPLE에서 빠지고 1줄 PLAYBOOK 참조로 대체
    for moved in ("[완성도 기준]", "[자원 동원", "[안 닿으면 최선의 차선책"):
        assert moved not in Sys._PRINCIPLE and moved in Sys._PLAYBOOK_PRINCIPLES
    # [B-map] .collab/ 어포던스(PLAYBOOK 참조 포함)는 persona(system_prompt·압축 무관)로 내구 이전 —
    # PRINCIPLE 중복 제거. 봇은 persona로 PLAYBOOK 위치를 안다.
    from organt.organt import load_persona
    assert ".collab/PLAYBOOK.md" in load_persona()
    # 레이아웃 관례: 워커(멤버) push 유지, 리더는 PLAYBOOK 참조(A-6 — 리더만 이동)
    member_p = s._prompt("구현", Kind.WORK, "member", 12, 11, None)
    leader_p = s._prompt("게임 만들어줘", Kind.WORK, "leader", 11, 11, None)
    assert "[작업공간 레이아웃]" in member_p
    assert "[작업공간 레이아웃]" not in leader_p
    # 리더 env는 하드 경계 3사실 1줄 + PLAYBOOK 참조로 축소(막다른 길 사실은 인라인 유지)
    assert "GPU 없음" in leader_p and ".collab/PLAYBOOK.md" in leader_p
    assert "[이 환경의 능력·경계(사실) — 닿는 범위에서" not in leader_p


def test_B18_PLAYBOOK_자리표시자_승격_사람편집_보존(tmp_path):
    g = FakeGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "리더", 12: "백엔드"},
            workspace=str(tmp_path))
    f = _flow3(g, tmp_path)
    pb = os.path.join(str(tmp_path), ".collab", "PLAYBOOK.md")
    os.makedirs(os.path.dirname(pb), exist_ok=True)
    open(pb, "w", encoding="utf-8").write("자리표시자입니다")          # B-09 스캐폴드 잔재
    s._write_dossier_scaffold(f)
    body = open(pb, encoding="utf-8").read()
    assert "자원 동원" in body and "GPU 없음" in body and "[작업공간 레이아웃]" in body   # 실내용 승격
    open(pb, "a", encoding="utf-8").write("\n사람 편집 흔적")
    s._write_dossier_scaffold(f)                                       # 실내용은 재작성 안 함(정적)
    assert "사람 편집 흔적" in open(pb, encoding="utf-8").read()


def test_B18_setgoal_에세이_압축():
    """qbar/creative/gapcheck ~2K→~700자 압축(이동 아님 — 타이밍 가치 보존, A-7). 핵심 신호
    (품질 축→acceptance·발산→수렴·작동≠좋음·범주적 부재→신규 구축)는 유지."""
    g = FakeGuide()
    f = _flow3(g)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.participated.add(12)
    txt = asyncio.run(t["set_goal"].handler({"goal": "게임"}))["content"][0]["text"]
    tail = txt[txt.index("[품질 차원]"):]
    assert len(tail) < 1100                                            # 압축(종전 ~2K자)
    for keep in ("acceptance", "2~3개", "작동≠좋음", "범주적 부재", "신규 구축", "훌륭한 예"):
        assert keep in tail


def test_B18_list_projects_pull_보강_및_push캡_유지():
    g = FakeGuide()
    f = _flow3(g)
    rows = [{"id": f"P-{i:03d}", "name": f"프로젝트{i}", "summary": f"요약{i}"} for i in range(1, 21)]
    f.projects_provider = lambda: rows
    t = _tools(f, 11, "leader")
    out = asyncio.run(t["list_projects"].handler({}))["content"][0]["text"]
    assert "전체 20건" in out and "P-001" in out and "P-020" in out    # 캡 없는 전체 pull
    # push(_portfolio_note)는 현행 16건 캡 유지 + 잘렸을 때만 pull 도구 안내 1줄
    s = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={11: "리더"})
    s.projects = {i: r for i, r in enumerate(rows)}
    note = s._portfolio_note()
    assert "P-005" in note and "P-004" not in note                     # 최근 16건만
    assert "list_projects" in note
    s.projects = {1: rows[0]}
    assert "list_projects" not in s._portfolio_note()                  # 캡 안 잘리면 안내 없음
