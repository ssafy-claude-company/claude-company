"""[회의 경제 수술 예행(2026-07-20) — e2e ch79 실측(표결 12라운드·부결 7·전원 재토론 반복) 봉합 확정]

드래프트 모드(공동 결론 파일)를 실제로 관통하는 대본 예행:
  A. 부결 → 이의 자동 기입 → **이의별 적임 해소 위임**(role_fit) → 재토론 없이(fastpath)
     완성 검사→재표결 → 확정. 부결 1회의 비용이 '전원 재토론 패스'에서 '해소 wake K + 재표결'로.
  B. 아무도 초안을 못 바꾸는 패스(결정구획 해시 무변화) → **즉시 중단 + 사람 호출**
     (meet_no_progress_break) — 수치 상한이 아니라 진전 판정(재픽과 같은 논리).
"""
import asyncio
import os
import re

from system.guide_tools import Flow
from test_sys import FakeGuide, _tools


def _meet_flow(tmp_path, bots=None):
    g = FakeGuide()
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11,
             bot_info=bots or {11: "L", 12: "백엔드", 13: "QA"})
    f.start_root("root")
    f.workspace = str(tmp_path)
    return g, f


def _draft_path(tmp_path):
    # glob은 숨김 디렉터리(.collab)를 기본 제외 — os.walk로 찾는다.
    for root, _dirs, files in os.walk(str(tmp_path)):
        if "DRAFT.md" in files:
            return os.path.join(root, "DRAFT.md")
    return None


def _fill_draft(tmp_path):
    """봇의 '파일 채움'을 대본으로 — 템플릿 구조는 유지하고 자리표시만 고유 실값으로
    (일괄 동일값은 preflight '조건 중복' 거부 — 실봇처럼 항목마다 다른 내용)."""
    p = _draft_path(tmp_path)
    if not p:
        return
    t = open(p, encoding="utf-8").read()
    t = t.replace("목표: <", "목표: 방명록 1주기 — <")
    n = {"i": 0}

    def _u(_m):
        n["i"] += 1
        return f"등록 항목 {n['i']} 동작을 curl로 확인"
    t = re.sub(r"<[^>\n]{2,60}>", _u, t)
    open(p, "w", encoding="utf-8").write(t)


def _resolve_objections(tmp_path):
    p = _draft_path(tmp_path)
    if not p:
        return
    lines = [l for l in open(p, encoding="utf-8").read().splitlines()
             if not l.strip().startswith(">")]
    open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def test_부결은_해소위임_fastpath로_재토론없이_확정(monkeypatch, tmp_path):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))
    prompts = []
    votes = {"round": 0}

    async def wake(to, b, k):
        prompts.append((to, b))
        if "[이의 해소]" in b:
            _resolve_objections(tmp_path)
            return "이의를 해소했습니다 — 검증 절차를 실측 명령으로 채웠습니다."
        if "결론 확정 표결" in b:
            if votes["round"] < 2:                     # 1라운드(2명): 13이 반대 → 부결
                votes["round"] += 1
                return "[반대: 검증 절차가 모호합니다]" if to == 13 else "[찬성]"
            return "[찬성]"                            # 2라운드: 전원 찬성
        if "발언권 응찰" in b:
            return "[응찰: 5] 초안을 채우겠습니다" if to == 12 else "[패스]"
        if "발언권 획득" in b or "차례입니다" in b:
            _fill_draft(tmp_path)
            return "결정 구획을 채웠습니다."
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({"topic": "방명록", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    names = [e for e, _ in events]
    assert names.count("meet_consensus_rejected") == 1            # 부결 1회
    assert "dissent_resolution_delegated" in names                # 이의별 해소 위임 발동
    assert "stage_confirmed" in names                             # 최종 확정(등록)
    # fastpath: 부결 이후 재토론(발언권 응찰)이 다시 돌지 않았다 — 해소 위임→재표결 직행
    i_rej = next(i for i, (to, b) in enumerate(prompts) if "결론 확정 표결" in b)
    later_bids = [b for _, b in prompts[i_rej:] if "발언권 응찰" in b]
    assert later_bids == []
    assert str(f.current.status.goal or "").startswith("방명록 1주기")


def test_해소위임_무변화면_재토론_폴백_조기중단_아님(monkeypatch, tmp_path):
    """[U-035 라이브 실측 조합 버그] 위임된 해소가 파일을 못 바꾸면 → 전원 재토론 폴백이 먼저,
    조기 중단(무진전 브레이크)은 '토론까지 돈 패스'의 무변화에만."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))
    prompts = []
    votes = {"round": 0}

    async def wake(to, b, k):
        prompts.append((to, b))
        if "[이의 해소]" in b:
            return "확인했습니다"                      # 해소 실패 대본 — 파일 무변화(no-op)
        if "결론 확정 표결" in b:
            if votes["round"] < 2:
                votes["round"] += 1
                return "[반대: 검증 절차가 모호합니다]" if to == 13 else "[찬성]"
            return "[찬성]"
        if "발언권 응찰" in b:
            return "[응찰: 5] 채우겠습니다" if to == 12 else "[패스]"
        if "발언권 획득" in b or "차례입니다" in b:
            _fill_draft(tmp_path)
            if votes["round"] >= 1:                    # 재토론 폴백 턴이 이의를 해소
                _resolve_objections(tmp_path)
            return "채웠습니다"
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({"topic": "방명록", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    names = [e for e, _ in events]
    assert "dissent_resolution_delegated" in names
    assert "meet_no_progress_break" not in names       # 위임 실패 직후 조기 중단 금지
    # 위임 실패 후 재토론(발언권 응찰)이 실제로 다시 돌았다 — 폴백 경로
    i_rej = next(i for i, n in enumerate(names) if n == "meet_consensus_rejected")
    assert any("발언권 응찰" in b for _, b in prompts[-(len(prompts) // 2):])
    assert "stage_confirmed" in names                  # 폴백 경유 최종 확정
    assert i_rej >= 0


def test_심의단_도메인커버리지_1석_구제(monkeypatch, tmp_path):
    """[U-035 실측: 게임 판 목표 회의에 게임 기획자 무발언] 안건 최고 적합 직군이 응찰했는데
    점수순에서 밀리면 1석 구제 — 자기선택(패스=존중)은 유지, 배제만 막는다. 응찰은 전수 관측."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "L", 12: "백엔드", 13: "QA", 14: "디자이너",
                                      15: "PM", 16: "게임 기획자", 17: "데이터 엔지니어"})
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))

    async def wake(to, b, k):
        if "심의 응찰" in b:                            # 고점 3명(12·13·14) + 게임 기획자는 저점 응찰
            return {12: "[응찰: 8]", 13: "[응찰: 7]", 14: "[응찰: 7]", 16: "[응찰: 2]"}.get(to, "[패스]")
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13,14,15,16,17"}))
    asyncio.run(t["meet"].handler({"topic": "가위바위보 웹게임 목표", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    ev = dict((e, kw) for e, kw in events if e in ("panel_topfit_added", "meet_panel_selected"))
    assert ev.get("panel_topfit_added", {}).get("who") == 16          # 게임 기획자 1석 구제
    assert ev.get("meet_panel_selected", {}).get("n") == 3            # cap ceil(6×⅓)=2 + 구제 1
    assert sum(1 for e, _ in events if e == "meet_panel_bid") == 6    # 응찰 전수 관측


def test_무진전_패스는_즉시중단_사람호출(monkeypatch, tmp_path):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))

    async def wake(to, b, k):                           # 아무도 초안을 못 바꿈 — 전원 침묵/종료
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({"topic": "방명록", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    names = [e for e, _ in events]
    assert "meet_no_progress_break" in names            # 무변화 반복을 수치 없이 즉시 중단
    assert "meet_gate_exhausted" not in names           # wake_cap까지 태우지 않았다
    assert any("사람 조치 필요" in str(c) for c in g.calls)   # 사람 호출 게시

def test_백로그회의는_심의단축소_예외_전원참여(monkeypatch, tmp_path):
    """[U-037/ch82 실측(2026-07-21, 사용자: 'PM이 [직업] 남의 것까지 막 발제')] 백로그 회의의 본질은
    각자 자기 도메인 몫 등재(자기 등재 원칙) — 심의단 3명으로 줄이면 판 밖 도메인 몫을 누군가 대필해
    발제 귀속·릴레이(제출자=수행자)를 타고 소유 독식(PM이 30건 중 26건 → 전 도메인 작업이 PM에게).
    백로그 단계는 전원 참여: 심의단 선발이 안 돌고, 발언권 응찰이 팀 전원에게 간다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "L", 12: "게임 기획자", 13: "PM", 14: "프론트",
                                      15: "백엔드", 16: "VFX"})
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))

    async def wake(to, b, k):
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"                                 # 내용 무관 — 참여 구조만 검증
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13,14,15,16"}))
    f.current.status.goal = "게임"
    from system.rule.milestone import Criterion, Milestone, SubTask
    st = SubTask(st_id="ST-1", goal="규칙", criteria=[Criterion("규칙 검증", "pytest 통과")])
    f.milestones = [Milestone(ms_id="MS-1", goal="최소버전",
                              criteria=[Criterion("30턴 완주", "run 재현")], subtasks=[st])]
    asyncio.run(t["meet"].handler({"topic": "다음 일감 전부 열거", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    names = [e for e, _ in events]
    assert "meet_panel_selected" not in names            # 축소 예외 — 전원이 판에 남는다
    probed = {kw.get("who") for e, kw in events if e == "floor_bid"}
    assert {12, 13, 14, 15, 16} <= probed                # 발언권 응찰이 전원에게


def test_SYS개설_회의는_개설자도_평참여자_지명이_작동(monkeypatch, tmp_path):
    """[U-037 실측(2026-07-21, 사용자: '소집자 개념조차 없는 평등한 상태여야 — 지명해도 걔가 말 안
    하던데')] 어휘 중립화(07-14)가 구조엔 못 미쳐, 회의 개설자가 참여자 목록 밖이라 지명 8건이 조용히
    증발했다(안건의 주인이 침묵한 채 '제시 대기'가 목표로 가결). SYS가 여는 단계 회의는 개설자 세션이
    유휴(흐름 태스크에서 돎) — 개설자도 평참여자: 동료의 [지명]이 실제로 그에게 발언권을 넘긴다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "게임 기획자", 12: "PM"})
    f.floor_mode = "turn-taking"
    spoke = []

    async def wake(to, b, k):
        if "발언권 응찰" in b:
            return "[응찰: 5] 보태겠습니다" if to == 12 else "[패스]"
        if "차례입니다" in b or "발언하세요" in b:
            spoke.append(to)
            if to == 12:
                return "컨셉은 안건 주인 몫입니다. [지명: 게임 기획자]"
            return "[패스]"
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    from system.rule.communication import meet as _meet
    asyncio.run(_meet(f, 11, {"topic": "목표", "my_opinion": "여는 의견", "_sys_open": True}))
    assert 11 in spoke and 12 in spoke                 # 개설자가 발언권 루프의 실참여자
    assert spoke.index(12) < spoke.index(11)           # 12의 [지명]이 11에게 발언권을 넘겼다


def test_봇개설_회의는_개설자_배제유지_지명증발_안내(monkeypatch, tmp_path):
    """봇이 툴로 직접 연 회의는 개설자 세션이 툴 결과를 기다리는 중 — 개설자 wake는 세션 경합이라
    종전 배제 유지(무회귀). 대신 참여자 밖 지명은 이제 무신호 증발이 아니라 [안내]로 다음 발언들의
    '못 본 발언'에 서빙된다(봇이 답 없는 지명을 헛기다리지 않게)."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "게임 기획자", 12: "PM"})
    f.floor_mode = "turn-taking"
    spoke, bodies = [], []

    async def wake(to, b, k):
        bodies.append(b)
        if "발언권 응찰" in b:
            return "[응찰: 5] 보태겠습니다" if to == 12 else "[패스]"
        if "차례입니다" in b or "발언하세요" in b:
            spoke.append(to)
            return "기획자님 몫입니다. [지명: 게임 기획자]" if to == 12 else "[패스]"
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    asyncio.run(t["meet"].handler({"topic": "목표", "my_opinion": "여는 의견"}))
    assert 11 not in spoke                             # 봇 개설 = 개설자 배제 유지(세션 안전)
    # [지명 구조 강제(2026-07-21, 사용자: '평문 이름 말고 구조화 — 안 맞으면 다시 보내라')] 해석
    # 불가 지명 → SYS가 정본 문법([지명: <봇id>]) 재전송을 먼저 요구(마이크로 1회 — 대본은 [패스]로
    # 접음), 그래도 무효면 [안내]가 다음 발언들에 서빙된다.
    assert any("[지명 형식 재전송]" in b and "봇 id" in b for b in bodies)
    assert any("해석하지 못해 무효" in b for b in bodies)


def test_흐름중_재시작은_진행분_보존_같은단계_재회의(monkeypatch, tmp_path):
    """[재시작-안전 불변식(2026-07-21, 사용자: '흐름 중엔 아무리 재시작해도 상관없다 — 재복구가 있어
    안전하게 재개돼야, 네 말은 모순')] 러너 재시작(토큰·서버·사용자 중지 등)으로 회의가 끊겼다
    재개돼도, 같은 단계 DRAFT가 디스크에 있으면 골격을 새로 깔지 않고 봇들이 채워온 결론을 보존한다.
    실측 근거: ch84가 8회 재시작을 거치며 milestone DRAFT가 사라지지 않고 거의 완성까지 누적됐다."""
    from system.rule.milestone import draft_should_reset
    # 새 단계·초안 부재 → 새 골격(리셋 O)
    assert draft_should_reset("milestone", None) is True
    assert draft_should_reset("milestone", "# DRAFT [stage:goal] — 다른 단계") is True
    # 같은 단계 진행분 존재 → 절대 리셋 안 함(보존)
    _inprog = "# DRAFT [stage:milestone] — …\n## 결정\n이번 주기: 리듬 게임\n- 조건 | 실증: run"
    assert draft_should_reset("milestone", _inprog) is False
    # 회의 개시 관통: 진행 DRAFT가 있는 채로 같은 단계 회의를 다시 열어도 내용 보존(덮어쓰기 0)
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.milestone import Criterion, Milestone
    from system._util import dossier_write, dossier_read, dossier_rel
    g, f = _meet_flow(tmp_path)
    f.floor_mode = "turn-taking"
    _t0 = _tools(f, 11, "leader")

    async def _w0(to, b, k):
        return "[패스]"
    f.wake = _w0
    asyncio.run(_t0["create_task"].handler({"members": "12"}))
    f.current.status.goal = "게임"
    f.milestones = [Milestone(ms_id="MS-1", goal="주기",
                              criteria=[Criterion("돈다", "run")], subtasks=[])]
    _rel = dossier_rel(f.current.task_id)
    from system.rule.milestone import meeting_stage
    _stg = meeting_stage(f)                                 # 이 flow가 열 실제 단계
    _kept = (f"# DRAFT [stage:{_stg}] — 안건\n## 결정\n단위: **리듬 게임 최소버전 코어** | 실증: run\n"
             "## 참고\n")
    dossier_write(f, "DRAFT.md", _kept)                    # 재시작 전 진행분(봇들이 채운 결론)

    async def wake(to, b, k):
        return "[종료]" if "종결 확인" in b else "[패스]"   # 아무 편집 안 함(재개 직후 스냅샷 검사가 주제)
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["meet"].handler({"topic": "안건", "members": "", "rounds": "1",
                                   "my_opinion": "여는 의견", "_sys_open": True}))
    assert "리듬 게임 최소버전" in (dossier_read(f, "DRAFT.md") or "")   # 재개설이 진행분을 안 지웠다


def test_전역회의는_SubTask태깅_생략_공통흐름_소속(monkeypatch, tmp_path):
    """[U-037 실측(2026-07-21, 사용자: '전 서브태스크를 한번에 만드는 회의라면 공통 흐름 하위에')]
    단계 회의는 주기 전체의 결정 — 턴 소속 태깅이 '첫 미완 SubTask'를 무조건 찍어 백로그 회의가
    화면에서 ST-1 폴더로 접혔다. 회의 동안 st·bl 태깅 생략(ms까지만), 종료 시 복원."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.protocol import PIPELINE_CTX
    from system.rule.milestone import Criterion, Milestone, SubTask, _set_pipeline_ctx
    g, f = _meet_flow(tmp_path, bots={11: "L", 12: "게임 기획자", 13: "PM"})
    f.floor_mode = "turn-taking"
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "게임"
    st = SubTask(st_id="ST-1", goal="규칙", criteria=[Criterion("규칙 검증", "pytest 통과")])
    f.milestones = [Milestone(ms_id="MS-1", goal="최소버전",
                              criteria=[Criterion("30턴 완주", "run 재현")], subtasks=[st])]
    _set_pipeline_ctx(f, 12)
    assert (PIPELINE_CTX.get() or {}).get("st") == "ST-1"        # 작업 국면 = 단계 태깅(종전)
    seen = {}

    async def wake(to, b, k):
        seen["flag"] = getattr(f, "_stage_meeting", None)
        _set_pipeline_ctx(f, to)
        seen["st"] = (PIPELINE_CTX.get() or {}).get("st")
        seen["ms"] = (PIPELINE_CTX.get() or {}).get("ms")
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    asyncio.run(t["meet"].handler({"topic": "다음 일감 전부", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    assert seen.get("flag") == "backlog"                          # 회의 동안 플래그
    assert seen.get("st") is None and seen.get("ms") == "MS-1"    # 태깅 = 주기까지만
    assert getattr(f, "_stage_meeting", None) is None             # 종료 시 복원


def test_무진전은_심의단확대가_먼저_그래도_무진전이면_중단(monkeypatch, tmp_path):
    """[U-037/ch82 실측(2026-07-21, 사용자: '대화가 더 필요한 상황에 결론 강제·회의 파괴가 답이냐')]
    2명 심의단이 발언만 돌다 서고, 재개설이 풀린 실이유가 '새 참여자'였다 — 무진전 1차 대응은 컷이
    아니라 같은 회의의 심의단 확대(밖에 있던 팀원 재응찰 합류, 회의당 1회). 확대 후에도 무진전이면
    그때 종전대로 정직 중단(사람 상신). 결론을 강제하지 않고 대화 상대를 늘린다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "L", 12: "백엔드", 13: "QA", 14: "디자이너",
                                      15: "PM", 16: "데이터"})
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))

    async def wake(to, b, k):
        if "심의 응찰" in b:
            if "진전 없이" in b:                            # 확대 응찰 — 밖에 있던 14가 합류
                return "[응찰: 6] 제 도메인이 걸립니다" if to == 14 else "[패스]"
            return {12: "[응찰: 8]", 13: "[응찰: 7]"}.get(to, "[패스]")   # 최초 심의단 2명
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"                                     # 아무도 초안을 못 바꿈(무진전 대본)
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13,14,15,16"}))
    asyncio.run(t["meet"].handler({"topic": "방명록", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    names = [e for e, _ in events]
    assert "meet_panel_refilled" in names                    # 1차 대응 = 확대(컷 아님)
    assert "meet_no_progress_break" in names                 # 확대 후에도 무진전 → 그때 컷
    assert names.index("meet_panel_refilled") < names.index("meet_no_progress_break")
    ref = next(kw for e, kw in events if e == "meet_panel_refilled")
    assert ref.get("n") == 1                                 # 응찰한 14만 합류(패스 존중)
    # (합류 채널 게시는 픽스처 thread_id가 문자열이라 int 캐스트 스킵 — 가시화는 로그 이벤트로 검증)


def test_확정표결은_심의단이_아니라_전원(monkeypatch, tmp_path):
    """[U-039 실측(2026-07-21, 사용자: '왜 회의는 3명만 — 의견은 못 했어도 찬반은 전체가 참여해야')]
    심의단 축소(발언 비용 처방) 후 확정 표결까지 심의단만 돌아 '찬성 2 → 확정'으로 모호한 결론이
    쉽게 가결됐다. 발언 = 심의단, 찬반(마이크로 즉답) = 팀 전원 — 비참여 도메인이 결론의 구멍
    (장르 미정 등)을 막을 표면을 갖는다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "L", 12: "백엔드", 13: "QA", 14: "디자이너",
                                      15: "PM", 16: "데이터"})
    f.floor_mode = "turn-taking"
    voters = set()

    async def wake(to, b, k):
        if "심의 응찰" in b:
            return {12: "[응찰: 8]", 13: "[응찰: 7]"}.get(to, "[패스]")
        if "결론 확정 표결" in b:
            voters.add(to)
            return "[찬성]"
        if "발언권 응찰" in b:
            return "[응찰: 5] 채우겠습니다" if to == 12 else "[패스]"
        if "차례입니다" in b or "발언하세요" in b:
            _fill_draft(tmp_path)
            _resolve_objections(tmp_path)
            return "채웠습니다"
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13,14,15,16"}))
    asyncio.run(t["meet"].handler({"topic": "방명록", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    assert {12, 13, 14, 15, 16} <= voters              # 심의단 밖(14·15·16)도 찬반 참여


def test_파이프라인_마감은_주기완주와_e2e판정이_관문(monkeypatch, tmp_path):
    """[전수 감사(2026-07-21, 사용자: '안정성·실효성·협업 실익이 보장된 상태에서 e2e를 돌려야지')]
    e2e 전수가 권고 문구뿐이라 검증 없이 마감·표류 가능하던 실효성 구멍 — 마일스톤 판의
    complete_task는 ①열린 주기 0 ②로드맵 소진 ③Task 경계 e2e 판정 존재를 요구한다.
    e2e_fail(복기 정체 포함)은 정직 마감 허용(결함은 완료 보고에)."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    from system.rule.milestone import Criterion, Milestone
    ms = Milestone(ms_id="MS-1", goal="최소버전",
                   criteria=[Criterion("돈다", "run 재현", passed=True)])
    f.milestones = [ms]
    out = asyncio.run(t["complete_task"].handler({"result": "끝"}))["content"][0]["text"]
    assert "미완 주기" in out                              # 열린 주기 → 거부
    ms.status = "done"
    f.roadmap = ["프로토타입", "완성도"]
    out2 = asyncio.run(t["complete_task"].handler({"result": "끝"}))["content"][0]["text"]
    assert "로드맵에 남은 단계" in out2                     # 다음 주기 남음 → 거부
    f.roadmap = ["프로토타입"]
    out3 = asyncio.run(t["complete_task"].handler({"result": "끝"}))["content"][0]["text"]
    assert "e2e_open" in out3 and "마감 불가" in out3       # 판정 없음 → 전수 검증 코칭 거부
    f.wrapup_state = {"verdict": "e2e_fail", "defects": []}
    out4 = asyncio.run(t["complete_task"].handler({"result": "끝"}))["content"][0]["text"]
    assert "e2e_open" not in out4 and "미완 주기" not in out4   # 판정 있으면 이 관문 통과(뒤 게이트로)


def test_결론직전_지명은_답슬롯_1턴_존중후_표결(monkeypatch, tmp_path):
    """[U-038 재작업(2026-07-21, 사용자: "'의견 부탁합니다' 했는데 그냥 결론 짓고 종료해버리는 상황
    해결해야")] 초안 완성 컷이 최종 표결로 직행하며 마지막 발언의 유효 지명을 증발시키던 것 —
    게이트가 표결 전에 그 지명자에게 답 슬롯 1턴을 준다(회의당 1회 상한 — 지명 릴레이 부활 아님).
    지명자의 편집이 초안을 되열면 회의는 자연히 계속된다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))
    prompts = []

    async def wake(to, b, k):
        prompts.append((to, b))
        if "결론 확정 표결" in b:
            return "[찬성]"
        if "답 슬롯" in b:
            return "제 관점은 이미 반영돼 있습니다 — 확정에 동의합니다."
        if "발언권 응찰" in b:
            if not any("차례" in x or "발언하세요" in x for _, x in prompts):
                return "[응찰: 5] 초안 채우겠습니다" if to == 12 else "[패스]"
            return "[응찰: 4] 마무리 확인" if to == 13 else "[패스]"
        if "차례입니다" in b or "발언하세요" in b:
            if to == 12:
                _fill_draft(tmp_path)
                _resolve_objections(tmp_path)
                return "결정 구획을 채웠습니다."
            return "확인했습니다 — 백엔드 의견 부탁합니다. [지명: 백엔드]"
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({"topic": "방명록", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    names = [e for e, _ in events]
    assert "draft_ready" in names and "meet_final_nominee_slot" in names
    slot = next(kw for e, kw in events if e == "meet_final_nominee_slot")
    assert slot.get("who") == 12                              # 지명자(백엔드=12)에게 답 슬롯
    i_ready = names.index("draft_ready")
    assert "stage_confirmed" in names                         # 슬롯 후 표결·확정 정상 완주
    _slot_prompt = next((b for to, b in prompts if to == 12 and "답 슬롯" in b), None)
    assert _slot_prompt is not None                           # 실제 그 봇에게 슬롯 프롬프트 도달


def test_결정칸_후속미룸만이면_빈칸과_동형_등록거부(monkeypatch):
    """[U-038 실측(2026-07-21, 사용자: '무슨 Task 목표 하나 못 잡고 있어 회의가')] 목표='(후속: 기획
    단계에서 확정 — 담당·달력 날짜)'가 부결 2회(찬성 0)에도 종결 보장(부결 3회 소진→이월·확정)에
    실려 그대로 등록 — GOAL이 빈 채 판이 굴렀다. 봇들의 반대는 옳았고 기계가 밀었다. 수리: 미룸 전용
    값 = 빈칸과 동형(형식 검사 — 내용 무판단) — 등록이 최종 방어선이라 종결 보장이 소진돼도 '결정이
    실린 결론'만 확정 가능. 초안 단계(draft_missing_key)에서도 같은 판정으로 가결 전 기계 이의 코칭."""
    from system.guide_tools import Flow
    from system.rule.milestone import deferred_only, draft_missing_key, register_stage
    from test_sys import FakeGuide
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    assert deferred_only("(후속: 기획·설계 단계에서 게임 정체성 확정 — 담당: 게임 기획자, 기획 마감: 2026-07-23 정오)")
    assert not deferred_only("2인 턴제 카드 대전 웹게임 (후속: 세부 밸런스)")   # 결정+세부 미룸은 정상
    f = Flow(FakeGuide(), channel_id=500, guild_id=1, leader_id=11, bot_info={11: "L"})
    ok, note = register_stage(f, "goal", "목표: (후속: 기획 단계에서 확정 — 담당: 게임 기획자)", "게임")
    assert not ok and "후속 미룸" in note and "recruit" in note
    ok2, _n2 = register_stage(f, "goal", "목표: 2인 턴제 카드 대전 웹게임\n"
                                         "- 30턴 완주 | 실증: run으로 30턴 재현", "게임")
    assert ok2, _n2                                            # 실결정은 종전대로 등록(무회귀)
    assert draft_missing_key("goal", "## 결정\n목표: (후속: 나중에)\n\n## 참고") == "목표"
    assert draft_missing_key("goal", "## 결정\n목표: 카드 대전 웹게임\n\n## 참고") is None


def test_서브태스크_영역중복은_표결전_반려(monkeypatch):
    """[U-039 실측·사용자(2026-07-21): '구조가 이상하면 반려되어야지 — 근본을 해결']] 서브태스크
    분해가 near-중복 영역(백엔드 스키마 '1차'/'2차'처럼 목표 토큰이 거의 같은 둘)을 내면 백로그
    배정이 한쪽으로 쏠려 다른 쪽이 굶는다(ST-3/6/7 백로그 0의 근본). preflight가 표결 전에
    형식 이의로 되돌려 회의가 합치거나 영역을 가르게 한다."""
    from system.rule.milestone import stage_preflight
    dup = ("단위: 백엔드 게임 상태 스키마 정의 1차 기본 필드 | 실증: pytest 통과\n"
           "단위: 백엔드 게임 상태 스키마 정의 2차 계산식 | 실증: pytest 통과")
    errs = stage_preflight("subtask", dup)
    assert any("영역 중복" in e for e in errs)
    ok = ("단위: 게임 상태 저장 계층 | 실증: pytest 통과\n"
          "단위: 화면 입력·렌더링 | 실증: run으로 확인")
    assert not any("영역 중복" in e for e in stage_preflight("subtask", ok))   # 뚜렷이 다른 영역은 통과


def test_마일스톤_과정서술은_반려_실물로(monkeypatch, tmp_path):
    """[U-039 실측·사용자: '마일스톤 주제 모호 — 구체적으로'] '이번 주기'가 '…확정되는 단계'식 과정
    서술이면 완성 실물이 아니라 활동을 결론에 앉힌 것 — 실물로 다시 쓰게 반려."""
    from system.rule.milestone import register_stage
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)

    async def _w(to, b, k):
        return "[패스]"
    f.wake = _w
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.status.goal = "게임"
    ok0, note0 = register_stage(f, "milestone",
                                "단계: 프로토타입 → 완성\n이번 주기: 페이퍼 검증으로 장르가 확정되는 단계\n"
                                "- 규격 명시 | 실증: 문서 확인", "게임")
    assert not ok0 and "과정 서술" in note0
    ok1, _n1 = register_stage(f, "milestone",
                              "단계: 프로토타입 → 완성\n이번 주기: 브라우저에서 도는 타이밍 게임 1판\n"
                              "- 30턴 완주 | 실증: run 재현", "게임")
    assert ok1                                                     # 실물은 통과


def test_목표골격_구성점검_자리와_달력사실_고지(monkeypatch):
    """[U-038 실측] ①구성 점검 경로 평등: set_goal 도구에만 있던 점검(2026-07-13 설계)을 회의 골격의
    자리표시로 — 안 채우면 초안이 완성되지 않고, 부족 직군의 정경로(recruit)가 그 자리에서 상기
    (팀 밖 게임 기획자에게 후속 담당을 걸던 우회 차단). ②달력 스케줄러 부재 고지: 기한은 날짜가
    아니라 파이프라인 사건으로(실행 불가한 '7-23 정오' 류 계획 언어 차단)."""
    from system.rule.milestone import stage_agenda, stage_draft_template
    d = stage_draft_template("goal", "안건")
    assert "구성 점검:" in d and "recruit" in d
    assert "달력" in d and "다음 회의 전" in d
    assert "미루면 빈칸과 같아 등록되지 않습니다" in d
    # [일감 굵기 경제(2026-07-21, 사용자: '1500 안으로 최적화')] 잘게 쪼갠 30건의 건당 오버헤드가
    # 실작업비를 압도 — 백로그 골격이 '실증 한 번으로 닫히는 묶음' 단위를 코칭(상한 강제 아님).
    _, btpl = stage_agenda("backlog")
    assert "굵게" in btpl and "오버헤드" in btpl
    # [협의 파생 흡수(2026-07-21, 사용자: '1500 다 쓴 건 비용 문제')] U-039 실측 34건의 상당수가
    # '-협의·-수치' 꼬리 — 협의는 별도 백로그가 아니라 부모 백로그의 완료 조건으로 동봉하게 코칭.
    assert "별도 백로그로 만들지 마세요" in btpl and "완료 조건" in btpl


def test_로드맵은_회의결론으로_독립계획블록_폐지(monkeypatch):
    """[2026-07-21, 사용자: '제목 아래 그 칸이 결론 칸 — 저건 회의의 결론에 들어가야'] 주기 회의
    등록 노트(= [회의 마무리]로 결론 칸에 실리는 텍스트)에 계획(전체 단계열+이번 주기 좌표)이
    들어가고, 독립 '[로드맵]' 게시(고아 '계획' 블록)는 사라진다."""
    from system.rule.milestone import register_stage
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.guide_tools import Flow
    from test_sys import FakeGuide
    f = Flow(FakeGuide(), channel_id=500, guild_id=1, leader_id=11, bot_info={11: "L", 12: "M"})
    f._pipeline_notes = []
    ok, note = register_stage(f, "milestone",
                              "단계: 프로토타입 → 완성도\n이번 주기: 프로토타입\n"
                              "- 브라우저 30턴 완주 | 실증: run으로 30턴 재현", "게임")
    assert ok, note
    assert "계획: 프로토타입 → 완성도" in note and "이번 주기 = 1단계" in note   # 결론 칸으로
    assert not any("[로드맵]" in str(n) for n in f._pipeline_notes)            # 독립 블록 소멸


def test_단위_2줄_자연표기_흡수_preflight_등록_동일판정(monkeypatch, tmp_path):
    """[U-035 실측] 봇 전원이 '단위:'를 제목 줄로, 조건(| 실증:)을 다음 줄로 썼다 — 파서가 제목만
    집어 6단위 전부 '조건 없음' 전멸(가결→등록 0건 무한 사이클×회의 3회). ①자연 표기 흡수
    ②preflight가 등록과 같은 깊이(단위별 게이트) ③등록 실패 사유가 오진 없이 그대로 나온다."""
    from system.rule.milestone import parse_units, stage_preflight, register_stage, open_milestone
    two_line = ("## 결정\n단위: 게임 판정 로직\n"
                "게임 로직 (선택→판정→결과) | 실증: 9가지 조합을 node 스크립트로 전수 판정 확인\n\n"
                "단위: 정적 배포\n배포 절차 | 실증: 공개 URL에서 curl 상태코드 200 확인\n")
    us = parse_units(two_line.splitlines())
    assert len(us) == 2 and us[0].startswith("게임 판정 로직 | ") and "| 실증:" in us[0]
    assert stage_preflight("subtask", two_line) == []
    # 조건 없는 단위 = 표결 전에 단위명 붙은 사유(등록에서야 전멸하던 은닉 제거)
    errs = stage_preflight("subtask", "## 결정\n단위: 제목만 있는 단위\n")
    assert errs and "제목만 있는 단위" in errs[0]
    # 등록 경로도 같은 파싱 — 2줄 표기가 실제 SubTask 2개로 등록된다
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    ms = open_milestone(f, "단판 가위바위보", [{"desc": "한 루프 동작", "verify": "curl로 페이지 200 확인"}])
    landed, note = register_stage(f, "subtask", two_line)
    assert landed and len(ms.subtasks) == 2
    assert ms.subtasks[0].goal == "게임 판정 로직"   # 제목만 goal — 본문·게이트가 단계 이름을 압사시키지 않게
    # 게이트 불통과 단위는 사유가 메시지에 그대로 나온다('단위: 줄을 확인' 오진 금지)
    landed2, note2 = register_stage(f, "subtask", "## 결정\n단위: 조건 없는 단위\n")
    assert not landed2 and "조건 없는 단위" in note2 and "단위: 줄을 확인" not in note2

def test_iter주기_정본_집을것있으면_작업_충전은_일괄배분(monkeypatch, tmp_path):
    """[2026-07-20 사용자 교정: '전부 소진/중지 → 점검 → 다음 회의가 다수 한번에'] 07-17 게으른 스캔이
    '앞 영역 소진 → 다음 영역 회의'(영역당 순차 회의)로 좁힌 표류 반전 — ①어디든 집을 백로그가 있으면
    빈 영역이 남아도 작업 단계 ②충전 회의 1번이 [영역명] 접두로 여러 영역 몫을 일괄 등록."""
    from system.rule.milestone import meeting_stage, open_milestone, open_subtask, register_stage
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "가위바위보 웹게임"
    ms = open_milestone(f, "단판", [{"desc": "한 루프 동작", "verify": "curl로 페이지 200 확인"}])
    for nm in ("게임 규칙", "판정 로직", "화면 UI"):
        open_subtask(f, ms, nm, [{"desc": nm + " 완성", "verify": "node 스크립트로 확인"}])
    assert meeting_stage(f) == "backlog"                 # 전 영역 전무 → 충전 회의
    landed, note = register_stage(f, "backlog", (
        "## 결정\n백로그: [게임 규칙] 승패 규칙 표 작성\n"
        "백로그: [판정 로직] 9가지 조합 판정 구현\n백로그: 접두 없는 항목\n"))
    assert landed and "3개" in note
    store = f.backlog_relays
    by = {st.goal: len((store.get(st.st_id).backlogs if store.get(st.st_id) else []))
          for st in ms.subtasks}
    assert by["게임 규칙"] >= 1 and by["판정 로직"] >= 1   # [영역명] 배분
    # [무주 출생 금지] 귀속 실패분도 적임 지정으로 주인을 갖고 태어난다
    assert all(int(b.submitter or 0) in (11, 12, 13)
               for st in ms.subtasks for b in ((store.get(st.st_id).backlogs if store.get(st.st_id) else [])))
    # 집을 게 생겼으면 — 빈 영역(화면 UI)이 남아 있어도 작업 단계(영역당 회의 캐스케이드 금지)
    assert meeting_stage(f) is None

def test_Task안에서_Task생성_절대금지(monkeypatch, tmp_path):
    """[2026-07-20 사용자 확정: 'Task는 새 요청이 낳는다 — Task 안에서 Task 생성은 절대 안 돼']
    미완 주기 장부가 있으면 — Task 프레임이 유실됐어도 — create_task 거부(원 Task는 시스템 복원 몫).
    (U-035 실측: 복원 구멍에서 봇이 새 Task 개설 → goal 재주행·목표 표류)"""
    from system.rule.milestone import open_milestone, open_subtask
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "가위바위보 웹게임"
    ms = open_milestone(f, "단판", [{"desc": "한 루프 동작", "verify": "curl로 200 확인"}])
    open_subtask(f, ms, "게임 규칙", [{"desc": "규칙 정의", "verify": "node 스크립트로 확인"}])
    f.current = None                                     # 복원 구멍 재현 — Task 유실
    r = asyncio.run(t["create_task"].handler({"members": "12,13"}))
    assert "새 Task 거부" in str(r) and "이어가세요" in str(r)
    assert f.current is None                             # 새 Task가 만들어지지 않았다


def test_고아장부_last_task_2차복원(monkeypatch, tmp_path):
    """[U-035 실측] open_task 스냅샷 유실 + 열린 주기 생존 → last_task 보관본으로 원 Task 복원
    (새 개설이 아니라 되살리기 — 'Task는 사용자 요청이 낳는다'의 복구면)."""
    import asyncio as _aio
    from system.rule.milestone import open_milestone, open_subtask, ms_to_dict
    from system import sys_recovery
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    t = _tools(f, 11, "leader")
    _aio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "가위바위보 웹게임"
    ms = open_milestone(f, "단판", [{"desc": "한 루프", "verify": "curl 200 확인"}])
    open_subtask(f, ms, "규칙", [{"desc": "규칙 정의", "verify": "node로 확인"}])
    _tid, _thr, _blk = f.current.task_id, f.current.thread_id, f.current.block_id
    snap = {"task_id": _tid, "thread_id": _thr, "block_id": _blk, "team": [11, 12],
            "purpose": "p", "goal": "가위바위보 웹게임", "owner": 0, "owner_name": ""}
    proj = {"open_task": None, "last_task": snap,
            "milestones": [ms_to_dict(m) for m in f.milestones],
            "backlog_relays": {}}
    f.current = None
    logs = []

    class _Sys:
        def _log(self, ev, **kw):
            logs.append(ev)
    out = _aio.run(sys_recovery.restore_open_task(_Sys(), f, proj))
    assert out and f.current is not None and f.current.task_id == _tid   # 원 Task 되살림
    assert "open_task_fallback_last" in logs and "open_task_restored" in logs

# ── 사람 대기 파킹(2026-07-20, U-035 근본원인 핸드오프 A·B·D — rung2·3·5 절단) ──────────

def _blocked_run_turn(calls):
    """seg1에서 Task+열린 주기+blocked_pending(조건 재협상=사람 대기)을 세우고 미완으로 반환하는
    대본 — 이후 세그먼트가 도는지(파킹 실패)를 calls로 계수한다."""
    from system.guide_tools import TaskRef
    from system.protocol import TaskStatus
    from system.rule.milestone import open_milestone

    async def run_turn(flow, oid, body, kind, role, **kw):
        calls.append(role)
        if len(calls) == 1:
            st = TaskStatus(task_id="t1", purpose="p", status="진행", goal="가위바위보",
                            owner="", group=[])
            flow.current = TaskRef(task_id="t1", thread_id="thr", block_id="blk",
                                   status=st, team=[11, 12], owner=0)
            ms = open_milestone(flow, "단판", [{"desc": "모션 타이밍 100ms 준수",
                                                "verify": "run 스크립트로 타이밍 측정"}])
            ms.criteria[0].status = "blocked_pending"     # 재협상 = 사람 대기(진실원=조건 상태)
            return "작업 중 (⚠ 턴 한도 도달 — 미완)"
        return "계속"
    return run_turn


def test_awaiting_human_파킹_봇발화0(monkeypatch, tmp_path):
    """[핸드오프 A — rung3 핵심 절단] blocked_pending이 서면 continue 루프는 quota_halt와 동형으로
    파킹(안내 1회+break) — 신규 봇 세그먼트 0, 단계 회의 개설 0. 종전엔 경보만 죽이고 계속 돌아
    봇들이 조율 잡담·재보고로 세그먼트를 채웠다(사용자 관측 '이상한 말')."""
    from system.sys_core import Sys
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g = FakeGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L", 12: "백엔드"},
            workspace=str(tmp_path), max_continue=6)
    calls = []
    s.run_turn = _blocked_run_turn(calls)
    asyncio.run(s.handle_user_input(500, 11, "가위바위보 웹게임", root_id="r"))
    assert len(calls) == 1                                 # 파킹 후 신규 봇 세그먼트 발화 0
    evs = [e["event"] for e in s.flow_log]
    assert "awaiting_human_parked" in evs
    assert "stage_meeting_opened" not in evs               # 회의개설-즉시미룸 재발 0
    assert s._flow_awaiting_human.get(500) is True         # reap 마감 신호 세팅
    assert sum(1 for c in g.calls if c[0] == "post" and "[사람 대기]" in str(c[3])) == 1


class _RunGuide:
    """Sys.run 관통용 최소 배달 계약 — 요청 1건을 서빙하고 픽·마감·중지 표기를 기록한다."""
    autoproject = False

    def __init__(self, ch=500, mid=900):
        self.ch, self.mid = ch, mid
        self.served = False
        self.calls, self.picks, self.stopped = [], [], []

    async def get_pending(self):
        if self.served:
            return []
        self.served = True
        return [{"msg_id": self.mid, "channel_id": self.ch, "to_id": 11, "kind": "W",
                 "body": "가위바위보 웹게임", "repick_n": 0}]

    async def pick(self, mid, done=False, unpick=False, touch=False, **kw):
        self.picks.append({"mid": mid, "done": done, "unpick": unpick, "touch": touch})
        return True

    def set_origin(self, ch):
        pass

    async def heartbeat(self):
        pass

    async def all_stops(self):
        return []

    async def check_interject(self, ch):
        return []

    async def check_stop(self, ch):
        return False

    async def mark_stopped(self, ch):
        self.stopped.append(ch)

    async def post(self, ch, sender, content, reply_to=None):
        self.calls.append(("post", ch, sender, content))
        return "m1"

    async def send_response(self, thr, sender, req, body):
        self.calls.append(("resp", body))
        return "r1"

    async def open_task(self, ch, status):
        return "blk", "thr"

    async def update_status(self, ch, blk, status):
        return blk

    async def send_request(self, thr, sender, to, kind, body):
        return "reqid"

    async def create_project_channel(self, gid, name):
        return 9001


def test_사람대기_reap은_재픽없이_전용마감(monkeypatch, tmp_path):
    """[핸드오프 A 수용 — reap] 파킹 판은 장부가 전진했어도 재픽(=원요청 재주입) 금지 — done 마감+
    중지 표기(재개 버튼 생존)+awaiting_human_closed 전용 이벤트(정체 stalled_stopped와 구분)."""
    from system.sys_core import Sys
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g = _RunGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L", 12: "백엔드"},
            workspace=str(tmp_path), max_continue=6)
    calls = []
    s.run_turn = _blocked_run_turn(calls)
    asyncio.run(s.run(g, leader=11, cap=1, poll=0.05, once=True))
    evs = [e["event"] for e in s.flow_log]
    assert "awaiting_human_parked" in evs and "awaiting_human_closed" in evs
    assert "request_repick" not in evs and "stalled_stopped" not in evs
    assert any(p["done"] for p in g.picks)                 # 요청 done — bridge 재배달 종결
    assert not any(p["unpick"] for p in g.picks)           # 재픽 0 = intervention 재주입 0
    assert g.stopped == [500]                              # 중지 표기 — 재개 버튼 경로 생존


def test_파킹판_사람답이_흐름시작에서_반영(monkeypatch, tmp_path):
    """[핸드오프 B — rung2 출구] 파킹으로 흐름이 닫힌 뒤 온 '조건 승인/반려' 답은 interject가 아니라
    새 요청으로 들어온다 — 복원 직후 파싱해 waiver를 반영한다(종전엔 이 경로가 없어 답이 증발,
    판이 재파킹되는 영구 교착). 승인=waived, 반려=active 복귀."""
    import asyncio as _aio
    from system.rule.milestone import ms_to_dict, open_milestone, pending_waivers
    from system.sys_core import Sys
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")

    def _mk(ch, ans):
        g = FakeGuide()
        s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L", 12: "백엔드"},
                workspace=str(tmp_path), max_continue=6)
        seed = Flow(g, channel_id=ch, guild_id=1, leader_id=11, bot_info={11: "L", 12: "백엔드"})
        seed.workspace = str(tmp_path)
        ms = open_milestone(seed, "단판", [{"desc": "모션 타이밍 100ms 준수",
                                            "verify": "run 스크립트로 타이밍 측정"}])
        ms.criteria[0].status = "blocked_pending"
        s.projects[ch] = {"id": f"P-{ch}", "workspace": str(tmp_path),
                          "open_task": {"task_id": "t1", "thread_id": "thr", "block_id": "blk",
                                        "team": [11, 12], "purpose": "p", "goal": "가위바위보",
                                        "owner": 0, "owner_name": ""},
                          "milestones": [ms_to_dict(m) for m in seed.milestones],
                          "backlog_relays": {}}
        got = {}

        async def run_turn(flow, oid, body, kind, role, **kw):
            got["flow"] = flow
            flow.cancelled = True                          # 반영 확인이 목적 — 세그먼트 즉시 종료
            return "확인"
        s.run_turn = run_turn
        _aio.run(s.handle_user_input(ch, 11, ans, root_id="rw"))
        return s, got["flow"]

    s1, f1 = _mk(700, "조건 승인")
    assert any(e["event"] == "waiver_reply_at_start" and e.get("answer") == "approve"
               for e in s1.flow_log)
    assert not pending_waivers(f1)                          # 대기 해소
    assert f1.milestones[0].criteria[0].status == "waived"  # 승인 = 포기 확정
    s2, f2 = _mk(701, "조건 반려")
    assert any(e["event"] == "waiver_reply_at_start" and e.get("answer") == "deny"
               for e in s2.flow_log)
    assert f2.milestones[0].criteria[0].status == "active"  # 반려 = 조건 유지(재시도)


# ── 마일스톤 보고 확인 링크 + 2단계 승계 체인(2026-07-20, e2e 사전 분석) ───────────────

def test_마일스톤보고_확인링크와_2단계승계_체인(monkeypatch, tmp_path):
    """[e2e 예행] ms1 완주 → [마일스톤 보고]에 열어볼 주소 동봉(배포 URL 우선·완성작 주소 폴백) +
    [다음 단계] 코칭 → 단계 유도가 2번째 마일스톤 회의를 열고, 이월분이 새 주기 잣대로 합류한다 —
    사용자 확인 자료·승계 체인의 관통 대본."""
    from system.rule.milestone import (iter_verify, meeting_stage, open_milestone,
                                        renegotiate_criterion, wrapup_done)
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "가위바위보 웹게임"
    f.roadmap = ["최소버전", "확장"]
    f.project_id = "P-099"
    g.work_url = lambda pid: f"https://example.test/api/projects/{pid}/works/"   # 매체 능력(duck-typed)
    ms1 = open_milestone(f, "최소버전", [
        {"desc": "한 루프 동작", "verify": "curl로 200 확인"},
        {"desc": "모션 타이밍 100ms", "verify": "run 스크립트로 측정"}])
    renegotiate_criterion(f, ms1, "모션 타이밍", "최소버전 범위 밖")             # 이월(사람 0)
    ok, _ = iter_verify(f, ms1, [{"desc": "한 루프 동작", "passed": True, "evidence": "curl 200"}])
    assert ok and wrapup_done(f, ms1) == "done"
    notes = "\n".join(f._pipeline_notes)
    assert "[마일스톤 보고]" in notes
    assert "https://example.test/api/projects/P-099/works/" in notes             # 확인 링크(완성작 폴백)
    assert "[다음 단계]" in notes                                                # 승계 코칭
    assert meeting_stage(f) == "milestone"                                       # 2번째 주기 회의 유도
    ms2 = open_milestone(f, "확장", [{"desc": "점수판 표시", "verify": "node로 확인"}])
    assert any(c.desc == "모션 타이밍 100ms" for c in ms2.criteria)              # 이월 잣대 합류
    # 배포 URL이 생기면 보고 링크는 그것이 우선(실 배포 주소가 최상의 확인 자료)
    f._deploy_url = "https://murmur-ai.duckdns.org/apps/rps/"
    ok2, _ = iter_verify(f, ms2, [{"desc": c.desc, "passed": True, "evidence": "run 출력 OK"}
                                  for c in ms2.criteria])
    assert ok2 and wrapup_done(f, ms2) == "done"
    assert "https://murmur-ai.duckdns.org/apps/rps/" in "\n".join(f._pipeline_notes)


# ── 팀 상한 = 비상 백스톱(2026-07-21, U-036 재작업 #2 — 사용자: '대충 상위 6명이 아니라 누진 임계') ──

def test_팀상한_기본0_합류통과분은_그대로(monkeypatch, tmp_path):
    """팀 크기 통제의 정본은 참여 공고의 합류 누진 임계(sys_core, test_sys 참조) — join_bidders는
    이미 그 문을 통과한 명단이라 create_task는 기본(ORGANT_TEAM_CAP=0)에서 자르지 않는다.
    414e850의 평평한 하드캡 기본 6은 사용자 반려로 비활성 강등. (로스터는 계열 병합(_slim)에 안
    걸리는 상이 직군 — 병합은 별도 검증된 종전 기능이라 여기 섞지 않는다.)"""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    bots = {11: "기획", 12: "프론트", 13: "백엔드", 14: "데이터", 15: "QA", 16: "그래픽",
            17: "VFX", 18: "모션", 19: "브랜드", 20: "사운드", 21: "인프라"}
    g, f = _meet_flow(tmp_path, bots=bots)
    f.origin_request = "턴 기반 육성 경영 게임 만들어줘"
    f.join_bidders = list(range(12, 22))            # 10명(리더 11 제외) — 임계 통과분 가정
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({}))
    assert f.current is not None
    assert len(f.current.team) == 11                # 기본 0 = 재절단 없음(정본은 합류 임계)


def test_팀상한_env설정시_비상백스톱_작동(monkeypatch, tmp_path):
    """ORGANT_TEAM_CAP은 env로 켤 때만 도는 비상 백스톱(원 요청 적합 상위 유지·리더 포함) — 곡선
    무력화 사고 시의 마지막 안전판이지 기본 경로가 아니다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    monkeypatch.setenv("ORGANT_TEAM_CAP", "6")
    bots = {11: "기획", 12: "프론트", 13: "백엔드", 14: "데이터", 15: "QA", 16: "그래픽",
            17: "VFX", 18: "모션", 19: "브랜드", 20: "사운드", 21: "인프라"}
    g, f = _meet_flow(tmp_path, bots=bots)
    f.origin_request = "턴 기반 육성 경영 게임 만들어줘"
    f.join_bidders = list(range(12, 22))
    evs = []
    f.log = lambda ev, **kw: evs.append((ev, kw))
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({}))
    assert len(f.current.team) == 6 and 11 in f.current.team
    assert any(ev == "team_capped" and kw.get("cap") == 6 for ev, kw in evs)


# ── 표결 경제(2026-07-21, U-036 실측: 표 하나에 전원이 셸·파일 조사 7~17왕복 — 판 비용 ~1/3) ──

def test_표결_수집은_즉답_무도구(monkeypatch, tmp_path):
    """vote·vote_stop 수집을 회의 확정 표결과 같은 즉답 규칙으로 통일 — micro(무도구) wake +
    '도구 호출·파일 확인 금지' 지시 + cast_vote 유도문 제거. U-036 실측: 도구 장착 표결이 전원의
    조사 턴(셸 실행·파일 읽기 7~17왕복)을 정당화해 재기동 후 54분을 태웠다."""
    g, f = _meet_flow(tmp_path)
    caps = []

    async def fake_fork(flow, me, members, body_of, kind=None, micro=False):
        caps.append((micro, body_of(list(members)[0])))
        return [(m, "[표] 반대\n지금 구성으로 계속", "") for m in members]
    from system.rule import comm_ceremonies as cc
    monkeypatch.setattr(cc, "_fork_collect", fake_fork)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    asyncio.run(t["vote"].handler({"question": "렌더 방식?", "options": "Canvas;SVG", "members": ""}))
    asyncio.run(t["vote_stop"].handler({"target": "milestone", "reason": "해결 불가 검토"}))
    assert len(caps) == 2
    for micro, body in caps:
        assert micro is True                       # 무도구 즉답 wake
        assert "즉답" in body and "도구" in body    # 지시가 문면에
        assert "cast_vote" not in body             # 도구 유도문 제거


def test_작업단계_참고표결은_릴레이로_돌려보낸다(monkeypatch, tmp_path):
    """[U-036 실측: 백로그 40건 등록 후 앵커가 '백로그 계획 재확정' 표결 4회 — 전진 0] 집을 백로그가
    남은 작업 단계의 참고 표결은 회의 가드(meet_deferred_workstage)와 동형으로 거절하고 릴레이를
    코칭한다. 중지 출구(vote_stop)는 가드 밖 — 언제나 열린다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.status.goal = "육성 게임"
    # 팀 판은 개인 set_milestone이 (설계대로) 막히므로 — 회의 등록 결과와 동형의 장부를 직접 구성
    from system.rule.milestone import Criterion, Milestone, SubTask
    st = SubTask(st_id="ST-1", goal="규칙 엔진",
                 criteria=[Criterion("규칙 검증", "pytest rules_test.py 통과")])
    f.milestones = [Milestone(ms_id="MS-1", goal="최소버전",
                              criteria=[Criterion("브라우저 30턴 완주", "run 재현")], subtasks=[st])]
    from system.rule.backlog import relay_for
    r = relay_for(f, st)
    r.submit(12, "규칙 엔진 구현")                 # open 백로그 = 작업 단계
    evs = []
    f.log = lambda ev, **kw: evs.append(ev)
    out = asyncio.run(t["vote"].handler({"question": "계획 재확정?", "options": "확정;수정", "members": ""}))
    txt = out["content"][0]["text"]
    assert "pick_backlog" in txt and "vote_stop" in txt        # 릴레이 코칭 + 출구 안내
    assert "vote_deferred_workstage" in evs

    async def fake_fork(flow, me, members, body_of, kind=None, micro=False):
        return [(m, "[표] 반대\n계속", "") for m in members]
    from system.rule import comm_ceremonies as cc
    monkeypatch.setattr(cc, "_fork_collect", fake_fork)
    out2 = asyncio.run(t["vote_stop"].handler({"target": "milestone", "reason": "불가 검토"}))
    assert "표결" in out2["content"][0]["text"]                # vote_stop은 작업 단계에도 열림


# ── e2e 복기 진전 게이트(2026-07-20, 사용자: '무한반복 다 잡고 e2e — 비용 트레이드오프') ──

def test_복기_결함이_안줄면_2회연속에서_컷(monkeypatch, tmp_path):
    """e2e→복기→e2e의 마지막 무상한 경로 — 결함 수 비개선 복기가 2회 연속이면 새 복기를 열지 않고
    정직 중단 경로로(첫 재시도는 허용 — 진전 철학은 재픽·이월과 동형). 결함이 줄면 계속 허용."""
    from system.rule.milestone import ms_replan
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    events = []
    f.log = lambda ev, **kw: events.append(ev)
    d3 = ["버튼이 500 응답", "저장이 안 됨", "화면 깨짐"]
    ms1 = ms_replan(f, d3)                                  # 1차 복기 — 허용
    assert ms1 is not None
    ms1.status = "done"
    ms2 = ms_replan(f, d3)                                  # 2차 — 같은 3건(비개선 1회째) 허용
    assert ms2 is not None
    ms2.status = "done"
    ms3 = ms_replan(f, d3)                                  # 3차 — 비개선 2회 연속 → 컷
    assert ms3 is None and "ms_replan_stuck" in events
    # 결함이 줄면(진전) 카운트 리셋 — 계속 허용
    ms4 = ms_replan(f, d3[:1])
    assert ms4 is not None


# ── 조건 이월 = 사람 없는 1차 해소(2026-07-20, 사용자: '개입 최대한 줄여') ─────────────

def test_조건이월_사람없이_자체해소(monkeypatch, tmp_path):
    """로드맵에 후속 주기가 있고 잣대가 남으면, 재협상은 사람 승인 없이 '다음 주기 이월'로 즉시
    해소된다 — 잣대를 버리는 게 아니라 옮기는 것(carried 원장). 사람 호출·사람 대기 0."""
    from system.rule.milestone import open_milestone, pending_waivers, renegotiate_criterion
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.roadmap = ["최소버전", "확장"]
    esc = []
    f.escalate_to_human = lambda m: esc.append(m)
    ms = open_milestone(f, "최소버전", [{"desc": "한 루프 동작", "verify": "curl로 200 확인"},
                                        {"desc": "모션 타이밍 100ms", "verify": "run 스크립트로 측정"}])
    out = renegotiate_criterion(f, ms, "모션 타이밍", "최소버전 범위 밖")
    assert "이월" in out and not esc                       # 사람 호출 0
    assert not pending_waivers(f)                          # 사람 대기 0
    assert [c.desc for c in ms.criteria] == ["한 루프 동작"]
    assert ms.carried and "모션 타이밍" in ms.carried[0]["desc"]


def test_이월불가면_기존_사람경로(monkeypatch, tmp_path):
    """이월은 구조가 받아줄 때만 — ①로드맵 없음(후속 주기 없음) ②마지막 잣대(잣대 0개 주기 금지)면
    종전 blocked_pending+사람 에스컬레이트(최후수단) 그대로."""
    from system.rule.milestone import open_milestone, renegotiate_criterion
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    esc = []
    f.escalate_to_human = lambda m: esc.append(m)
    ms = open_milestone(f, "단판", [{"desc": "루프 동작", "verify": "curl 확인"},
                                    {"desc": "화면 표시", "verify": "node 확인"}])
    out = renegotiate_criterion(f, ms, "화면 표시", "범위 밖")   # 로드맵 없음
    assert "승인 대기" in out and ms.criteria[1].status == "blocked_pending" and len(esc) == 1
    g2, f2 = _meet_flow(tmp_path)
    f2.roadmap = ["최소버전 → 확장"]                       # 띄운 화살표 한 줄 표기 = 2단계
    esc2 = []
    f2.escalate_to_human = lambda m: esc2.append(m)
    ms2 = open_milestone(f2, "최소버전", [{"desc": "한 루프", "verify": "curl 확인"}])
    out2 = renegotiate_criterion(f2, ms2, "한 루프", "무리")   # 마지막 잣대
    assert "승인 대기" in out2 and len(esc2) == 1


def test_이월조건은_다음주기에_기계합류(monkeypatch, tmp_path):
    """이월=버리기 아님의 핵심 — 다음 open_milestone이 이월분을 새 주기 잣대로 자동 합류(원장 소진,
    같은 desc를 회의가 이미 실었으면 중복 주입 없음). 직렬화 왕복도 원장을 보존한다."""
    from system.rule.milestone import (ms_from_dict, ms_to_dict, open_milestone,
                                        renegotiate_criterion)
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.roadmap = ["최소버전", "확장"]
    ms1 = open_milestone(f, "최소버전", [{"desc": "한 루프", "verify": "curl 확인"},
                                          {"desc": "모션 타이밍", "verify": "run 측정"}])
    renegotiate_criterion(f, ms1, "모션 타이밍", "범위 밖")
    rt = ms_from_dict(ms_to_dict(ms1))                     # 체크포인트 왕복
    assert rt.carried and rt.carried[0]["desc"] == "모션 타이밍"
    ms1.status = "done"
    ms2 = open_milestone(f, "확장", [{"desc": "점수판", "verify": "node 확인"}])
    descs = [c.desc for c in ms2.criteria]
    assert descs.count("모션 타이밍") == 1 and not ms1.carried   # 합류 1회 + 원장 소진


def test_파킹전_blocked도_이월로_소급해소(monkeypatch, tmp_path):
    """[파킹 직전 훅] 이 코드 이전에 blocked_pending으로 굳은 판(복원 포함)도 이월 가능하면 사람
    없이 풀리고 awaiting_human이 걷힌다 — 파킹은 잔여 blocked가 있을 때만."""
    from system.rule.milestone import (open_milestone, pending_waivers,
                                        resolve_blocked_by_defer)
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.roadmap = ["최소버전", "확장"]
    ms = open_milestone(f, "최소버전", [{"desc": "한 루프", "verify": "curl 확인"},
                                        {"desc": "모션 타이밍", "verify": "run 측정"}])
    ms.criteria[1].status = "blocked_pending"
    ms.criteria[1].block_reason = "최소버전 범위 밖"
    f.awaiting_human = "조건 재협상 대기: '모션 타이밍'"
    n = resolve_blocked_by_defer(f)
    assert n == 1 and not pending_waivers(f) and f.awaiting_human is None
    assert ms.carried and ms.carried[0]["desc"] == "모션 타이밍"


def test_단위분해전_주기도_새Task금지(monkeypatch, tmp_path):
    """[핸드오프 D] 서브태스크가 아직 없는 열린 주기(마일스톤만 선 판)에서도 create_task 거부 —
    종전 '열린 단위까지 있어야'가 구멍(U-035: 그 창에서 표류 Task 143035 출생·목표 표류)."""
    from system.rule.milestone import open_milestone
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "가위바위보 웹게임"
    open_milestone(f, "단판", [{"desc": "한 루프 동작", "verify": "curl로 200 확인"}])
    f.current = None                                       # 포인터 유실 재현(단위 분해 전)
    r = asyncio.run(t["create_task"].handler({"members": "12,13"}))
    assert "새 Task 거부" in str(r) and f.current is None


def test_고아장부_단위분해전에도_last_task복원(monkeypatch, tmp_path):
    """[핸드오프 D] 서브태스크 없는 열린 주기 + open_task 유실에서도 last_task 2차 복원이 발동 —
    종전 '열린 단위' 요구로 폴백이 침묵, current 없이 가동돼 표류 Task의 창이 됐다."""
    import asyncio as _aio
    from system import sys_recovery
    from system.rule.milestone import ms_to_dict, open_milestone
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    t = _tools(f, 11, "leader")
    _aio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "가위바위보 웹게임"
    open_milestone(f, "단판", [{"desc": "한 루프", "verify": "curl 200 확인"}])
    _tid = f.current.task_id
    snap = {"task_id": _tid, "thread_id": f.current.thread_id, "block_id": f.current.block_id,
            "team": [11, 12], "purpose": "p", "goal": "가위바위보 웹게임", "owner": 0, "owner_name": ""}
    proj = {"open_task": None, "last_task": snap,
            "milestones": [ms_to_dict(m) for m in f.milestones], "backlog_relays": {}}
    f.current = None
    logs = []

    class _Sys:
        def _log(self, ev, **kw):
            logs.append(ev)
    out = _aio.run(sys_recovery.restore_open_task(_Sys(), f, proj))
    assert out and f.current is not None and f.current.task_id == _tid
    assert "open_task_fallback_last" in logs and "open_task_restored" in logs


def test_마일스톤회의_완수조건_이번주기_스코프(monkeypatch):
    """[핸드오프 E — rung1 예방] 마일스톤 회의 안건·골격이 완수조건을 '이번 주기' 범위로 못박는다 —
    최소버전 주기에 완제품 사양(모션 세부·디자인 토큰)이 실려 dead-end 되던 상류 차단."""
    from system.rule.milestone import stage_agenda, stage_draft_template
    _, tpl = stage_agenda("milestone")
    assert "완수조건은 '이번 주기' 범위만" in tpl
    draft = stage_draft_template("milestone", "안건")
    assert "'이번 주기' 범위의 조건만" in draft


def test_마일스톤회의_첫주기_산출물형태_불강제(monkeypatch):
    """[U-036 재작업 #3 롤백(2026-07-21, 사용자: '기획 문서 작성이 우선이면 그 작성을 우선시하는 게
    맞지 — 잘못된 패치')] 414e850이 회의 골격에 '첫 주기=실행물, 페이퍼 금지'를 못박았던 것을 되돌림 —
    첫 주기의 산출물 형태(문서든 실행물이든)는 회의가 정할 내용이지 골격이 강제할 형식이 아니다.
    디자이너의 로직 점유는 누진 응찰 임계(#2)·role_fit로 접근한다. 이 테스트는 금지 문구의 부재를
    고정해 같은 패치의 재유입을 막는다."""
    from system.rule.milestone import stage_agenda, stage_draft_template
    _, tpl = stage_agenda("milestone")
    draft = stage_draft_template("milestone", "안건")
    for banned in ("페이퍼", "첫 주기는 실행물", "브라우저에서 실제로 조작"):
        assert banned not in tpl and banned not in draft


def test_기충족조건_재보고는_미매칭아님(monkeypatch, tmp_path):
    """[핸드오프 F] 이미 통과한 조건의 퍼지 재보고가 iter_result_unmatched로 계고돼 봇이 같은 보고를
    반복 제출하던 공회전 차단 — 흡수(접수 안내)하고 미매칭 0."""
    from system.rule.milestone import iter_verify, open_milestone
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "가위바위보"
    ms = open_milestone(f, "단판", [
        {"desc": "(기획) 단판 로직: 승/패/무 판정 규칙 명시", "verify": "node game-rules 검증 스크립트 실행"},
        {"desc": "(프론트) 버튼 클릭 결과 표시", "verify": "curl로 페이지 200 확인"}])
    ms.criteria[0].passed = True
    ms.criteria[0].evidence = "MECHANICS.md 작성 · node 검증 통과"
    events = []
    f.log = lambda ev, **kw: events.append(ev)
    ok, note = iter_verify(f, ms, [{"desc": "게임 규칙 정의 완료 — 판정 규칙 명시", "passed": True,
                                    "evidence": "node game-rules 검증 스크립트 실행 통과"}])
    assert not ok
    assert "iter_result_unmatched" not in events           # 미매칭 계고 0(재보고 루프의 연료 차단)
    assert "iter_result_rereported" in events
    assert "이미 충족된 조건 재보고" in note


def test_iter_무한검증은_진전아님(monkeypatch, tmp_path):
    """[U-035 실측: iter 4·5·6차 내리 충족 1/4 고정인데 무한 continue] 진전 = 검증 결과(충족조건 수)이지
    시도 횟수(iter_n)가 아니다 — report_iter만 헛돌면 ledger_signature 무변화(정체 감지 작동)."""
    from system.rule.milestone import open_milestone, open_subtask, ledger_signature
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "가위바위보"
    ms = open_milestone(f, "단판", [{"desc": "판정 동작", "verify": "node 스크립트로 9조합 확인"},
                                    {"desc": "화면 표시", "verify": "curl로 페이지 200 확인"}])
    open_subtask(f, ms, "규칙", [{"desc": "규칙 정의", "verify": "node로 확인"}])
    sig0 = ledger_signature(f)
    # 검증만 반복(iter_n++) — 충족·백로그 상태 불변
    ms.iter_n += 1
    ms.iter_n += 1
    assert ledger_signature(f) == sig0                   # iter_n 증가는 진전 아님(무한 검증 감지)
    # 조건 하나 실제 충족 → 진전
    ms.criteria[0].passed = True
    assert ledger_signature(f) != sig0
