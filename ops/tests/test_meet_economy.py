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
