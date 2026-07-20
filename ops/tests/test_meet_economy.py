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
