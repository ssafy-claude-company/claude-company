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
    t = t.replace("목표: ⟦", "목표: 방명록 1주기 — ⟦")
    n = {"i": 0}

    def _u(m):
        n["i"] += 1
        hint = m.group(0)
        if "절차" in hint or "exact command" in hint:
            return f"python3 verify_draft_{n['i']}.py"
        return f"등록 항목 {n['i']} 동작을 curl로 확인"
    t = re.sub(r"⟦[^⟧\n]{1,150}⟧", _u, t)
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
    assert "dissent_resolution_by_objector" in names              # 반대자가 발언권 얻어 해소(시스템 추측 아님)
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
    assert "dissent_resolution_by_objector" in names
    assert "meet_no_progress_break" not in names       # 위임 실패 직후 조기 중단 금지
    # 위임 실패 후 재토론(발언권 응찰)이 실제로 다시 돌았다 — 폴백 경로
    i_rej = next(i for i, n in enumerate(names) if n == "meet_consensus_rejected")
    assert any("발언권 응찰" in b for _, b in prompts[-(len(prompts) // 2):])
    assert "stage_confirmed" in names                  # 폴백 경유 최종 확정
    assert i_rej >= 0


def test_반대자가_발언권_얻어_직접고침_또는_적임지명(monkeypatch, tmp_path):
    """[U-044 실측(2026-07-22, 사용자: 'a b가 반대했으니 a b가 발언권을 얻는 게 맞고, 다른 전문가 몫이면
    그 직군에게 직접 발언권을 주도록 — 그 일이 누구 일인지 시스템은 모른다, 프론트 앵커 오배정처럼')]
    부결 시 시스템이 role_fit으로 담당을 추측하지 않고, 반대한 사람이 발언권을 얻어 직접 고치거나
    [지명: id]로 적임에게 넘긴다. QA(13)가 반대→자기 몫 아니라 데이터(14) 지명→14가 이어받아 해소."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "L", 12: "백엔드", 13: "QA", 14: "데이터"})
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))
    resolve_woke = []
    votes = {"r": 0}

    async def wake(to, b, k):
        if "[이의 해소" in b:                         # 반대자 발언권('[이의 해소]') + 지명받음('[이의 해소 — 지명받음]')
            resolve_woke.append(to)
            if "지명받음" not in b and to == 13:      # QA(반대자, 첫 해소턴): 자기 몫 아니라 데이터 지명
                return "이건 데이터 스키마 몫입니다. [지명: 14]"
            _resolve_objections(tmp_path)             # 지명받은 데이터(14)가 실제 해소
            return "고쳤습니다."
        if "결론 확정 표결" in b:
            votes["r"] += 1
            if votes["r"] <= 4:                       # 1라운드(4명): QA(13) 반대
                return "[반대: 데이터 스키마가 빠졌습니다]" if to == 13 else "[찬성]"
            return "[찬성]"                           # 2라운드: 전원 찬성
        if "발언권 응찰" in b:
            return "[응찰: 5] 채우겠습니다" if to == 12 else "[패스]"
        if "차례입니다" in b or "발언하세요" in b:
            _fill_draft(tmp_path)
            return "채웠습니다"
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13,14"}))
    asyncio.run(t["meet"].handler({"topic": "방명록", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    names = [e for e, _ in events]
    assert "dissent_resolution_by_objector" in names
    assert 13 in resolve_woke        # 반대자(QA=13)가 발언권을 얻었다 — 시스템 추측 아님
    assert 14 in resolve_woke        # QA의 [지명: 14]로 데이터(14)가 발언권을 이어받았다
    assert "stage_confirmed" in names


def test_반대사유_잡소리서두_뒤_진짜사유_추출(monkeypatch, tmp_path):
    """[U-044 실측(2026-07-22, 사용자: 송지안 '읽겠습니다, 지금 이 텍스트 기반으로'가 반대 사유로 떴다 —
    이거 뭐야)] 봇이 잡소리 서두(프롬프트 흉내) 뒤에 [반대: 진짜사유]를 달면, 첫 줄(잡소리)이 아니라
    전체에서 [반대:] 뒤의 진짜 사유를 뽑아 [표]에 남긴다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "L", 12: "백엔드", 13: "QA"})
    f.floor_mode = "turn-taking"
    votes = {"r": 0}
    resolve_prompts = []                           # 반대자 발언권 프롬프트(정제된 사유 담김)

    async def wake(to, b, k):
        if "결론 확정 표결" in b:
            votes["r"] += 1
            if votes["r"] <= 3 and to == 13:       # 1라운드: QA가 잡소리 서두 + 뒤에 진짜 [반대: 사유]
                return "읽겠습니다, 지금 이 텍스트 기반으로.\n[반대: 저장소 스키마가 아직 추상적]"
            return "[찬성]"
        if "[이의 해소" in b:
            resolve_prompts.append(b)
            _resolve_objections(tmp_path)
            return "고쳤습니다."
        if "발언권 응찰" in b:
            return "[응찰: 5] 채우겠습니다" if to == 12 else "[패스]"
        if "차례입니다" in b or "발언하세요" in b:
            _fill_draft(tmp_path)
            return "채웠습니다"
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({"topic": "방명록", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    shown = "\n".join(resolve_prompts)
    assert "저장소 스키마가 아직 추상적" in shown         # [반대:] 뒤 진짜 사유가 추출돼 반대자에게 전달
    assert "읽겠습니다" not in shown                     # 잡소리 서두는 사유로 안 뜬다


def test_사유_없는_표는_반려하고_재요청해_받아낸다(monkeypatch, tmp_path):
    """[U-044(2026-07-22, 사용자: '이유 없으면 기권이겠지 — 데이터가 빈다는 건 그 봇이 사용법을
    몰랐던 것. 반려해서 받아내야지')] 사유 없는 빈 표([반대]만)는 그 봇에게 반려-재요청하고, 재요청이
    담아온 사유가 반대자 발언권으로 흐른다(빈 '(사유 미기재)'로 굳지 않는다)."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "L", 12: "백엔드", 13: "QA"})
    f.floor_mode = "turn-taking"
    st = {"vote": 0, "redo": 0}
    shown = []                                     # 반대자 발언권 프롬프트(재요청 사유가 담김)

    async def wake(to, b, k):
        if "표결 반려" in b:                        # 빈 표 반려 → 이번엔 사유 담아 재제출
            st["redo"] += 1
            return "[반대: 검증 기준이 비어 있습니다]"
        if "결론 확정 표결" in b:
            st["vote"] += 1
            if st["vote"] <= 3 and to == 13:       # 1라운드 QA: 사유 없는 빈 표
                return "[반대]"
            return "[찬성: 이 결론으로 충분합니다]"    # 나머지는 사유 있는 표(반려 대상 아님)
        if "[이의 해소" in b:
            shown.append(b)
            _resolve_objections(tmp_path)
            return "고쳤습니다."
        if "발언권 응찰" in b:
            return "[응찰: 5] 채우겠습니다" if to == 12 else "[패스]"
        if "차례입니다" in b or "발언하세요" in b:
            _fill_draft(tmp_path)
            return "채웠습니다"
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({"topic": "방명록", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    assert st["redo"] >= 1                          # 사유 없는 빈 표가 반려-재요청됐다
    joined = "\n".join(shown)
    assert "검증 기준이 비어 있습니다" in joined       # 재요청이 받아낸 사유가 반대자 발언권으로 흐름
    assert "(사유 미기재)" not in joined              # 빈 사유로 굳지 않음


def test_초안_빈칸표식은_이중대괄호만_봇의_꺾쇠는_자유():
    """[U-041(2026-07-22, 사용자: '< > 이게 왜 필요하냐')] 빈칸 표식을 봇이 참조·값·비교로 안 쓰는
    ⟦ ⟧로 바꿔, 봇이 정상 문서에 쓴 < >(<500ms·<마일스톤 정의> 등)가 '미완 빈칸'으로 오집계돼 회의가
    정체하던 것(목표 회의 25분 맴돔의 원인)을 뿌리 차단. ⟦ ⟧만 빈칸으로 집계."""
    from system.rule.milestone import draft_status
    filled = ("## 결정\n목표: 2인 턴제 카드게임 (응답 <500ms, 세부는 다음 회의<마일스톤 정의>에서)\n"
              "완수조건:\n- 규칙 명시 | 실증: run으로 확인\n\n## 참고\n메모\n")
    assert draft_status(filled)[0] == 0        # 봇의 < >(참조·값·비교)는 빈칸 아님 → 표결 가능
    empty = "## 결정\n목표: ⟦이 Task로 무엇을 만들지⟧\n## 참고\n"
    assert draft_status(empty)[0] >= 1         # ⟦ ⟧ 빈칸 표식만 미완으로 집계


def test_표결전_비심의단_표결자도_도메인_점검_1턴(monkeypatch, tmp_path):
    """[U-041(2026-07-22, 사용자: '심의단 2명만 발언하고 안 말한 사람이 반대로 판 깨는 게 맞는 구조냐 —
    표결권 있으면 발언 기회도')] 심의단(members)만 토론하고 전원(_team_full)이 표결하던 것 — 표결 직전,
    발언 못 한 비심의단 표결자에게 도메인 점검 1턴. 이의를 내면 표가 아니라 초안 [이의]로 들어가 표결이
    안 열리고 다음 패스가 해소한다(우려가 반대표로 튀어 부결되던 U-041 역효과 차단)."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "L", 12: "백엔드", 13: "QA", 14: "디자이너",
                                      15: "PM", 16: "사운드 디자이너"})
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))
    st = {"asked16": 0, "raised": False}

    async def wake(to, b, k):
        if "심의 응찰" in b:                              # 12·13만 심의단, 나머지 패스 → 비심의단 표결자
            return {12: "[응찰: 8]", 13: "[응찰: 7]"}.get(to, "[패스]")
        if "표결 전 도메인 점검" in b:                     # 관문 — 비심의단 16이 여기서 처음 발언
            if to == 16:
                st["asked16"] += 1
                if not st["raised"]:
                    st["raised"] = True
                    return "사운드 UX 완수기준이 목표에 빠졌습니다"     # 1회 이의
            return "[패스]"
        if "결론 확정 표결" in b:
            return "[찬성: 결론이 충분합니다]"
        if "차례입니다" in b or "발언하세요" in b:
            _fill_draft(tmp_path); _resolve_objections(tmp_path)     # 채우고 이의 해소
            return "채웠습니다"
        if "발언권 응찰" in b:
            return "[응찰: 5] 채우겠습니다" if to in (12, 13) else "[패스]"
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13,14,15,16"}))
    asyncio.run(t["meet"].handler({"topic": "방명록 목표", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    assert st["asked16"] >= 1                            # 비심의단 표결자가 표결 전 도메인 점검을 받았다
    assert any(e == "meet_prevote_concern" for e, _ in events)   # 이의가 표결 전에 초안으로 들어감(표 아님)


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
    from system.rule.milestone import (
        Criterion, Milestone, workspace_artifact_stamp, write_revision,
    )
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
    # [백로그 없는 발화는 단계에도 안 붙는다(2026-07-29, 사용자: '잘못된 데이터는 막아야지')]
    # 종전엔 작업 국면이면 백로그가 없어도 ST를 달아, 어떤 일감의 기록도 아닌 대화가 단계 폴더
    # 안 백로그 행들 옆에 채팅으로 남았다. 귀속의 근거는 '지금 물고 있는 백로그'다.
    assert (PIPELINE_CTX.get() or {}).get("st") is None           # 진행 중 백로그 없음 → 주기까지만
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


def test_R1_브레인라이팅_독립기고_익명병합과_사전부검(monkeypatch, tmp_path):
    """[집단지능 문헌 반영(2026-07-22, 사용자: '다른 자료도 분석해 구조 개선')] ①NGT/브레인라이팅:
    토론 전 전원 병렬 독립 기고(무기억 마이크로)를 초안 '## 참고'에 익명 병합 — 첫 발언 앵커링·발언
    편중·생산 차단 축소(Woolley 기회균등 정합). [패스]는 제외, 재개설(자리표시 0)은 스킵.
    ②사전부검(브레인라이팅-프리모텀): 확정 표결에서 찬성자도 '[실패한다면: …]' 위험 1줄 — 표 사유와
    분리 수집해 참고에 기계 병합."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))

    async def wake(to, b, k):
        if "독립 기고" in b:
            return "점수 상한은 999로, 저장은 세션당 1레코드" if to == 12 else "[패스]"
        if "결론 확정 표결" in b:
            return "[찬성]\n[실패한다면: 페이퍼 검증이 형식 채우기로 흐를 위험]"
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
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({"topic": "방명록", "members": "", "rounds": "2",
                                   "my_opinion": "여는 의견"}))
    from system._util import dossier_read
    d = dossier_read(f, "DRAFT.md") or ""
    assert "[R1 독립 기고" in d and "점수 상한은 999" in d      # 기고 익명 병합(12의 내용)
    assert d.count("· ") >= 1 and "[패스]" not in d             # 패스 제외
    r1 = next(kw for e, kw in events if e == "meet_r1_brainwrite")
    assert r1.get("n") == 1 and r1.get("of") == 2               # 2명 프로브·1명 기고
    assert "[사전부검 — 실패한다면]" in d and "형식 채우기로 흐를 위험" in d   # 프리모텀 병합


def test_하위회의_R1과_실질편집턴에_GOAL_비준계약전문_무절단주입(monkeypatch, tmp_path):
    """U-052: 160자 안건 요약이 아니라 GOAL 비준 전문을 모든 하위 실질 판단에 공급한다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.floor_mode = "turn-taking"
    prompts = []
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))

    from system.rule.milestone import register_stage
    tail = "상위계약-끝꼬리-U052"
    contract = (
        "공개 계약: transition(nextState)는 금지 전이에 Error를 던지고 상태를 보존한다. "
        + ("세부계약" * 45) + tail
    )
    ok, note = register_stage(
        f, "goal",
        "목표: 호출자가 상태 전이를 통제하는 StateMachine\n"
        + contract + "\n"
        "- 금지 전이는 Error와 상태 불변 | 실증: node test_state_machine.js",
        "U-052",
    )
    assert ok, note

    async def wake(to, body, kind):
        prompts.append((to, body))
        if "독립 기고" in body:
            return "[패스: 상위 계약이 이미 명확합니다]"
        if "결론 확정 표결" in body:
            return "[찬성: 상위 계약을 보존한 이번 범위입니다]"
        if "발언권 응찰" in body:
            return "[응찰: 6] 이번 주기 범위를 채우겠습니다" if to == 12 else "[패스]"
        if "발언권 획득" in body or "차례입니다" in body:
            _fill_draft(tmp_path)
            _resolve_objections(tmp_path)
            return "이번 주기 결정 구획을 채웠습니다."
        if "종결 확인" in body:
            return "[종료]"
        return "[패스]"

    f.wake = wake
    asyncio.run(t["meet"].handler({
        "topic": "첫 상태 머신 주기", "members": "", "rounds": "2",
        "my_opinion": "상위 계약을 보존하는 최소 구현",
    }))
    r1_prompts = [body for _, body in prompts if "독립 기고" in body]
    work_prompts = [body for _, body in prompts
                    if "발언권 획득" in body or "차례입니다" in body]
    vote_prompts = [body for _, body in prompts if "결론 확정 표결" in body]
    assert r1_prompts and work_prompts and vote_prompts
    assert all("[상위 확정 계약" in body and tail in body for body in r1_prompts)
    assert all("[상위 확정 계약" in body and tail in body for body in work_prompts)
    assert all("[상위 확정 계약" in body and tail in body for body in vote_prompts)


def test_표결도중_도착한_사람개입은_실질슬롯_DRAFT재평가후_재표결(monkeypatch, tmp_path):
    """micro 표결 중 pending_info가 생겨도 다음 stage로 넘기지 않고 등록 직전 실질 턴을 연다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.floor_mode = "turn-taking"
    events, order = [], []
    f.log = lambda event, **kw: events.append((event, kw))
    injected = {"done": False}

    async def wake(to, body, kind):
        if "독립 기고" in body:
            return "[패스: 추가 의견 없음]"
        if "발언권 응찰" in body:
            return "[응찰: 6] 초안을 채우겠습니다" if to == 12 else "[패스]"
        if "사람 개입 반영 슬롯" in body:
            order.append("human")
            assert to == 11 and f.pending_info.get(11)      # 봇 개설 회의의 _team_full 밖 개설자도 팀 대상
            p = _draft_path(tmp_path)
            text = open(p, encoding="utf-8").read()
            text = text.replace("방명록 1주기", "사용자 교정 방명록", 1)
            open(p, "w", encoding="utf-8").write(text)
            f.pending_info.pop(11, None)      # 실제 런타임에선 run_turn의 ack가 성공 뒤 소비
            return "[답변] 교정을 반영해 DRAFT 목표를 수정했습니다."
        if "발언권 획득" in body or "차례입니다" in body:
            _fill_draft(tmp_path)
            _resolve_objections(tmp_path)
            return "결정 구획을 채웠습니다."
        if "종결 확인" in body:
            return "[종료]"
        if "결론 확정 표결" in body:
            order.append("vote")
            if not injected["done"]:
                injected["done"] = True
                f.pending_info.setdefault(11, []).append("목표 이름을 사용자 교정 방명록으로 바꿔주세요")
            return "[찬성: 현재 결정 구획이면 충분합니다]"
        return "[패스]"

    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    asyncio.run(t["meet"].handler({
        "topic": "방명록", "members": "", "rounds": "2", "my_opinion": "여는 의견",
    }))
    assert "human" in order, (
        order, [x for x in events if "human_info" in x[0]], f.pending_info, f.comm.done, f.comm.alive)
    hi = order.index("human")
    assert "vote" in order[:hi] and "vote" in order[hi + 1:]   # 첫 표는 폐기하고 편집 뒤 재표결
    assert not f.pending_info.get(11)
    assert str(f.current.status.goal or "").startswith("사용자 교정 방명록")
    assert "meet_human_info_slot" in [event for event, _ in events]
    assert "stage_confirmed" in [event for event, _ in events]


def test_DRAFT쓰기실패_표결중_사람개입은_등록전_실질슬롯_대화안재표결(monkeypatch, tmp_path):
    """DRAFT가 생성되지 않은 대화 수렴안 폴백도 첫 표를 폐기하고, 사람 슬롯 뒤 새 안을 재표결한다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.floor_mode = "turn-taking"
    events, order, vote_bodies = [], [], []
    f.log = lambda event, **kw: events.append((event, kw))

    from system import _util
    real_dossier_write = _util.dossier_write
    draft_writes = []

    def fail_draft_write(flow, filename, text, task_id=None):
        if filename == "DRAFT.md":
            draft_writes.append(text)
            return False
        return real_dossier_write(flow, filename, text, task_id=task_id)

    monkeypatch.setattr(_util, "dossier_write", fail_draft_write)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))

    from system.rule import milestone
    real_register_stage = milestone.register_stage
    register_calls = []

    def counted_register(flow, stage, prop, origin=""):
        register_calls.append((stage, prop))
        return real_register_stage(flow, stage, prop, origin)

    monkeypatch.setattr(milestone, "register_stage", counted_register)
    injected = {"done": False}

    async def wake(to, body, kind):
        if "발언권 응찰" in body:
            return "[응찰: 7] 수렴안을 내겠습니다" if to == 12 else "[패스]"
        if "사람 개입 반영 슬롯" in body:
            order.append("human")
            assert register_calls == []                 # 첫 표가 등록기로 내려가기 전에 슬롯이 선행
            assert "수정된 `[수렴안]` 전문" in body     # 파일 없는 폴백에 맞는 실질 재제출 지시
            assert f.pending_info.get(11)
            f.pending_info.pop(11, None)                # 실런타임 run_turn ack의 큐 소비 대역
            return (
                "[답변] 사용자 교정을 반영했습니다.\n"
                "[수렴안]\n"
                "목표: 사용자 교정 방명록\n"
                "- 글 작성과 조회가 된다 | 실증: pytest -q\n"
                "[/수렴안]"
            )
        if "발언권 획득" in body or "차례입니다" in body:
            return (
                "[수렴안]\n"
                "목표: 최초 방명록\n"
                "- 글 작성과 조회가 된다 | 실증: pytest -q\n"
                "[/수렴안]"
            )
        if "결론 확정 표결" in body:
            order.append("vote")
            vote_bodies.append(body)
            if not injected["done"]:
                injected["done"] = True
                f.pending_info.setdefault(11, []).append("목표 이름을 사용자 교정 방명록으로 바꿔주세요")
            return "[찬성: 이 Task의 목표와 실증 조건이 구체적입니다]"
        if "종결 확인" in body:
            return "[종료]"
        return "[패스]"

    f.wake = wake
    asyncio.run(t["meet"].handler({
        "topic": "방명록", "members": "", "rounds": "2", "my_opinion": "여는 의견",
    }))

    assert draft_writes and _draft_path(tmp_path) is None       # 실제 DRAFT write 실패 경로 관통
    hi = order.index("human")
    assert "vote" in order[:hi] and "vote" in order[hi + 1:]   # 첫 표 폐기 → 슬롯 → 재표결
    assert len(register_calls) == 1
    assert "사용자 교정 방명록" in register_calls[0][1]
    assert "사용자 교정 방명록" in vote_bodies[-1]             # 슬롯의 새 안을 재평가해 표결
    assert not f.pending_info.get(11)
    assert str(f.current.status.goal or "").startswith("사용자 교정 방명록")
    assert "meet_human_info_slot" in [event for event, _ in events]
    assert "stage_confirmed" in [event for event, _ in events]


def test_DRAFT쓰기실패_wakecap경계_사람개입은_등록보류하고_큐보존(monkeypatch, tmp_path):
    """표결이 wake_cap을 정확히 소진하면 pending 슬롯을 건너뛰지 않고 등록을 보류하며 큐를 보존한다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    f.floor_mode = "turn-taking"
    events, prompts = [], []
    f.log = lambda event, **kw: events.append((event, kw))

    from system import _util
    real_dossier_write = _util.dossier_write
    draft_writes = []

    def fail_draft_write(flow, filename, text, task_id=None):
        if filename == "DRAFT.md":
            draft_writes.append(text)
            return False
        return real_dossier_write(flow, filename, text, task_id=task_id)

    monkeypatch.setattr(_util, "dossier_write", fail_draft_write)

    # 2명·2R의 wake_cap은 26. 대화 발언 24회를 대본으로 채우고 최종 전원 표결 2회가 경계에
    # 닿게 해, 그 표결 중 들어온 pending_info가 실질 슬롯 없이 등록되는지를 정확히 겨눈다.
    from system.rule import floor

    async def fill_to_cap(policy, state, opening, speak, bid=None, max_turns=64,
                          on_alloc=None, speak_many=None, can_close=None):
        # [동시 발언·빈칸 종결보류] 엔진 시그니처 확장 수용
        alloc = floor.Allocation(floor.SELF, next=12, reason="wake_cap 경계 대본")
        for _ in range(24):
            await speak(12, alloc)
        return [opening]

    monkeypatch.setattr(floor, "run_conversation", fill_to_cap)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))

    from system.rule import milestone
    real_register_stage = milestone.register_stage
    register_calls = []

    def counted_register(flow, stage, prop, origin=""):
        register_calls.append((stage, prop))
        return real_register_stage(flow, stage, prop, origin)

    monkeypatch.setattr(milestone, "register_stage", counted_register)
    injected = {"done": False}

    async def wake(to, body, kind):
        prompts.append(body)
        if "결론 확정 표결" in body:
            if not injected["done"]:
                injected["done"] = True
                f.pending_info.setdefault(11, []).append("경계에서 보존돼야 할 사용자 교정")
            return "[찬성: 목표와 실증 조건이 구체적입니다]"
        if "차례입니다" in body:
            return (
                "[수렴안]\n"
                "목표: 경계 방명록\n"
                "- 글 작성과 조회가 된다 | 실증: pytest -q\n"
                "[/수렴안]"
            )
        return "[패스]"

    f.wake = wake
    asyncio.run(t["meet"].handler({
        "topic": "방명록", "members": "", "rounds": "2", "my_opinion": "여는 의견",
    }))

    assert draft_writes and _draft_path(tmp_path) is None
    assert register_calls == []                              # 슬롯 예산이 없으면 등록도 함께 보류
    assert f.pending_info.get(11) == ["경계에서 보존돼야 할 사용자 교정"]
    assert not any("사람 개입 반영 슬롯" in body for body in prompts)
    names = [event for event, _ in events]
    assert "meet_human_info_deferred_budget" in names
    assert "stage_confirmed" not in names
    assert not str(f.current.status.goal or "").strip()


def test_확정표결은_응찰한_사람들끼리(monkeypatch, tmp_path):
    """[2026-07-30, 사용자 지시 — 종전 U-039(2026-07-21) 결정을 뒤집는다]
    종전엔 발언=심의단, 찬반=팀 전원이었다. 실측(U-079 GOAL 회의): 응찰 5명이 심의했는데 게임
    기획자가 「별빛 회피」를 먼저 써 넣자 나머지는 조건만 덧붙였고, 도메인이 안 걸린 사람들은
    판단 근거 없이 찬성했다 — 한 사람의 첫 안이 사실상 독식했다. 말하는 사람과 정하는 사람을
    일치시킨다: 응찰한 사람들끼리 정하고, 혼자 응찰했으면 그 한 명이 정한다."""
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
    assert voters == {12, 13}, "응찰한 사람들끼리 정한다 — 심의단 밖은 표결에 안 들어온다"


def test_반대우세_소진은_확정안됨_다수결바닥(monkeypatch, tmp_path):
    """[U-044 실측(2026-07-22, 사용자: '찬성2·반대3인데 3회 소진으로 그대로 통과')] 완성 파일 표결
    3회 소진 이월-확정은 무한 교착 방지 장치지만, 마지막 라운드가 반대 우세(찬성<반대)면 확정하면
    안 된다 — 소수 반대는 다수결로 넘기되(교착 방지 유지) 다수가 반대하는 안은 확정 못 하게. 반대가
    다수면 그 안이 실제 지지를 못 받은 것이라 확정 대신 회의 계속(예산 천장이 정직 마감)."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "L", 12: "백엔드", 13: "QA", 14: "디자이너",
                                      15: "PM", 16: "데이터"})
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))

    async def wake(to, b, k):
        if "심의 응찰" in b:
            return {12: "[응찰: 8]", 13: "[응찰: 7]"}.get(to, "[패스]")
        if "결론 확정 표결" in b:                      # 반대 우세: 4명 반대·2명(11·16) 찬성
            return "[찬성]" if to in (11, 16) else "[반대: 지표 기준이 빠졌습니다]"
        if "[이의 해소]" in b:
            _resolve_objections(tmp_path)
            return "이의를 해소했습니다."
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
    names = [e for e, _ in events]
    assert "stage_confirmed" not in names          # 반대 우세는 소진돼도 확정 안 됨(다수결 바닥)
    assert "meet_dissent_carryover" not in names   # 이월-확정 경로가 반대 우세에선 안 열림
    assert str(f.current.status.goal or "") == ""  # 목표가 밀려서 잡히지 않았다


def test_파이프라인_마감은_주기완주와_e2e판정이_관문(monkeypatch, tmp_path):
    """[전수 감사(2026-07-21, 사용자: '안정성·실효성·협업 실익이 보장된 상태에서 e2e를 돌려야지')]
    e2e 전수가 권고 문구뿐이라 검증 없이 마감·표류 가능하던 실효성 구멍 — 마일스톤 판의
    complete_task는 ①열린 주기 0 ②로드맵 소진 ③Task 경계 e2e 판정 존재를 요구한다.
    e2e_fail은 복기 입력일 뿐 완료 판정이 아니므로 Task 마감은 e2e_pass만 허용한다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    from system.rule.milestone import (
        Criterion, Milestone, SubTask, workspace_artifact_stamp, write_revision,
    )
    from system.rule.backlog import Backlog, BacklogRelay
    ms = Milestone(ms_id="MS-1", goal="최소버전",
                   criteria=[Criterion("돈다", "run 재현", passed=True)])
    st = SubTask("ST-1", "최소버전 구현", [], status="done")
    relay = BacklogRelay(st.st_id)
    relay._pool["B1"] = Backlog("B1", "구현", 12, status="done", assignee=12)
    ms.subtasks = [st]
    f.milestones = [ms]
    f.backlog_relays = {st.st_id: relay}
    out = asyncio.run(t["complete_task"].handler({"result": "끝"}))["content"][0]["text"]
    assert "미완 주기" in out                              # 열린 주기 → 거부
    ms.status = "done"
    f.roadmap = ["프로토타입", "완성도"]
    out2 = asyncio.run(t["complete_task"].handler({"result": "끝"}))["content"][0]["text"]
    assert "로드맵에 남은 단계" in out2                     # 다음 주기 남음 → 거부
    f.roadmap = ["프로토타입"]
    out3 = asyncio.run(t["complete_task"].handler({"result": "끝"}))["content"][0]["text"]
    assert "e2e_open" in out3 and "마감 불가" in out3       # 판정 없음 → 전수 검증 코칭 거부
    f.wrapup_state = {
        "verdict": "e2e_fail", "defects": [],
        "artifact_stamp": workspace_artifact_stamp(f), "write_epoch": write_revision(f),
    }
    out4 = asyncio.run(t["complete_task"].handler({"result": "끝"}))["content"][0]["text"]
    assert "마감 불가" in out4 and "e2e_pass가 아닙니다" in out4


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
    from system.rule.milestone import deferred_only, draft_missing_key, register_stage, stage_preflight
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
    # [목표=절차 금지(2026-07-21, U-040 실측: '①정의→②검증→③정량 정의'가 목표로 확정 — 봇들이
    # 2:2로 두 번 막았는데 소진 확정이 밀었다)] ①②③ 나열·'→' 연쇄는 형태 신호로 반려.
    ok3, note3 = register_stage(f, "goal", "목표: ① 컨셉 정의 → ② 페이퍼 검증 → ③ 호흡 정량 정의\n"
                                           "- 검증 로그 | 실증: run 재현", "게임")
    assert not ok3 and "절차 나열" in note3
    ok4, _n4 = register_stage(f, "goal", "목표: 카드 대전 → 온라인 확장 가능한 웹게임\n"
                                         "- 30턴 완주 | 실증: run 재현", "게임")
    assert ok4                                                 # 단일 화살표(표현)는 허용 — 연쇄만 반려
    # [U-051 라이브 2026-07-25] 인라인 코드의 상태 전이를 화살표 수로 세어, 9명 전원 찬성한 정상
    # GOAL을 두 번 거부한 뒤 meet_gate_exhausted로 공전했다. 코드 계약은 절차 나열이 아니다.
    code_goal = ("목표: JS 상태 전이 검사기 1세트로 `idle→working`, `working→done`, "
                 "`working→stopped` 계약을 확인한다\n"
                 "- 전이 테스트 통과 | 실증: node test.js")
    ok5, note5 = register_stage(f, "goal", code_goal, "상태 전이")
    assert ok5, note5
    circled_code_goal = ("목표: JS 상태 전이 검사기 1세트로 `①idle→②working→③done` 계약을 확인한다\n"
                         "- 전이 테스트 통과 | 실증: node test.js")
    ok6, note6 = register_stage(f, "goal", circled_code_goal, "상태 전이")
    assert ok6, note6
    draft = "## 결정\n" + code_goal + "\n\n## 참고 (자유 — 판정 대상 아님)\n"
    assert stage_preflight("goal", draft) == []                 # 비싼 찬성 표결 전 검사도 같은 판정
    prose_draft = ("## 결정\n목표: 컨셉 정의 → 페이퍼 검증 → 호흡 정량 정의\n"
                   "- 검증 로그 | 실증: run 재현\n\n## 참고\n")
    assert any("절차 나열" in e for e in stage_preflight("goal", prose_draft))
    assert draft_missing_key("goal", "## 결정\n목표: (후속: 나중에)\n\n## 참고") == "목표"
    assert draft_missing_key("goal", "## 결정\n목표: 카드 대전 웹게임\n\n## 참고") is None


def test_백로그_발제귀속_R1_원저자_전사자아님(monkeypatch, tmp_path):
    """[U-041 실측(2026-07-22, 사용자: '90%가 게임 기획자 — 남의 도메인 대필')] 병합 회의에서 앵커가
    결정 구획을 독점 편집하면 백로그 발제가 전부 앵커로 쏠린다(UI·튜토리얼까지 대필 귀속). R1 독립
    기고에 원저자를 남겨, 백로그 본문이 어느 R1 기고와 크게 겹치면 그 기고자를 발제자로(전사자 아님).
    강제 배분 아님 — 각자 자기 도메인을 R1에 냈으면 그 크레딧이 그에게 간다."""
    from system.rule.milestone import register_stage
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)

    async def _w(to, b, k):
        return "[패스]"
    f.wake = _w
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "게임"
    _okm, _nm = register_stage(f, "milestone",
                               "단계: 최소버전 → 완성\n이번 주기: 카드 게임 최소버전\n"
                               "- 30턴 완주 | 실증: python3 verify_game.py", "게임")
    assert _okm, _nm
    # 프론트(13)가 R1에 UI 백로그를 냈다 — 그 원저자 기록
    f._r1_attr = [(13, "손 3장 선택 패널 렌더링과 클릭 피드백 구현")]
    # 앵커(12)가 결정 구획에 전사(백로그 줄을 12가 씀)
    f._draft_attr = {}
    ok, note = register_stage(f, "subtask",
                              "단위: UI 화면 | 실증: run 재현\n"
                              "백로그: [UI 화면] 손 3장 선택 패널 렌더링과 클릭 피드백 구현", "게임")
    assert ok, note
    st = f.milestones[-1].subtasks[0]
    b = (f.backlog_relays.get(st.st_id)).backlogs[0]
    assert int(b.submitter) == 13, f"R1 원저자(프론트 13)에 귀속돼야 — 전사자 앵커 아님, got {b.submitter}"


def test_같은발제자가_구현과_독립QA를_전사해도_수행자는_구조적으로분리(monkeypatch, tmp_path):
    """U-059: 회의 정리자가 모든 줄의 저자로 잡혀도 명시된 독립 검증은 제작자와 같은 손에 갈 수 없다."""
    from system.rule.milestone import claim_kick_target, register_stage

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    _g, f = _meet_flow(
        tmp_path,
        bots={11: "기획", 12: "백엔드", 13: "QA"},
    )
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.origin_request = (
        "구현 백로그와 독립 QA 백로그를 서로 다른 담당자에게 등록하고 실제 수행하세요."
    )
    f.current.status.goal = "상태 머신"
    okm, note = register_stage(
        f,
        "milestone",
        "이번 주기: 상태 머신 완성\n"
        "- 상태 전이 계약 충족 | 실증: node test_state_machine.js",
        "상태 머신",
    )
    assert okm, note

    # 최종 DRAFT의 세 줄을 모두 앵커가 정리한 라이브 상황. R1/DRAFT 귀속만 따르면 셋 다 11이다.
    f._r1_attr = [
        (11, "CommonJS 상태 머신 구현"),
        (11, "16개 전이 독립 QA 테스트 작성"),
        (11, "최종 실행 증거 확인"),
    ]
    f._draft_attr = {}
    ok, note = register_stage(
        f,
        "subtask",
        "단위: 상태 머신 구현과 검증\n"
        "백로그: [상태 머신 구현과 검증] CommonJS 상태 머신 구현\n"
        "백로그: [상태 머신 구현과 검증] 독립 QA로 16개 전이 테스트 작성\n"
        "백로그: [상태 머신 구현과 검증] 구현자와 다른 담당자가 최종 검증과 실행 증거 확인",
        "상태 머신",
    )
    assert ok, note
    st = f.milestones[-1].subtasks[0]
    rows = f.backlog_relays[st.st_id].backlogs
    assert [row.submitter for row in rows] == [11, 13, 13]
    assert len({row.submitter for row in rows}) == 2
    # 첫 구현은 기존 발제자부터, 이후 handoff/claim은 QA 보유분으로 이어질 구조적 원장이 섰다.
    who, backlog, st_id = claim_kick_target(f)
    assert (who, backlog.backlog_id, st_id) == (11, "B1", st.st_id)


def _독립검증_귀속판(tmp_path, bots, members):
    from system.rule.milestone import register_stage

    _g, f = _meet_flow(tmp_path, bots=bots)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": members}))
    f.origin_request = (
        "구현 백로그와 독립 QA 백로그를 서로 다른 담당자에게 등록하고 실제 수행하세요."
    )
    f.current.status.goal = "상태 머신"
    okm, note = register_stage(
        f,
        "milestone",
        "이번 주기: 상태 머신 완성\n"
        "- 상태 전이 계약 충족 | 실증: node test_state_machine.js",
        "상태 머신",
    )
    assert okm, note
    return f


def test_독립실행과_서로다른브라우저는_담당자분리계약이아님(monkeypatch, tmp_path):
    """산출물 속성의 bare '독립/서로 다른'이 기존 R1 원저자 귀속을 바꾸면 안 된다."""
    from system.rule.milestone import register_stage

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    _g, f = _meet_flow(tmp_path, bots={11: "기획", 12: "QA"})
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.origin_request = (
        "서로 다른 브라우저에서 독립 실행 가능한 상태 머신과 QA 테스트를 작성하세요."
    )
    f.current.status.goal = "상태 머신"
    okm, note = register_stage(
        f,
        "milestone",
        "이번 주기: 상태 머신 완성\n"
        "- 상태 전이 계약 충족 | 실증: node test_state_machine.js",
        "상태 머신",
    )
    assert okm, note
    f._r1_attr = [
        (11, "CommonJS 상태 머신 구현"),
        (11, "QA 브라우저 호환 테스트 작성"),
    ]
    ok, note = register_stage(
        f,
        "subtask",
        "단위: 상태 머신 구현과 검증\n"
        "백로그: [상태 머신 구현과 검증] CommonJS 상태 머신 구현\n"
        "백로그: [상태 머신 구현과 검증] QA 브라우저 호환 테스트 작성",
        "상태 머신",
    )
    assert ok, note
    rows = f.backlog_relays[f.milestones[-1].subtasks[0].st_id].backlogs
    assert [row.submitter for row in rows] == [11, 11]


def test_독립QA줄이_구현보다_먼저와도_제작자를_피함(monkeypatch, tmp_path):
    """귀속 계산은 줄 순서가 아니라 같은 회의 전체 제작자 집합을 기준으로 한다."""
    from system.rule.milestone import register_stage

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _독립검증_귀속판(
        tmp_path, {11: "기획", 12: "백엔드", 13: "QA"}, "12,13")
    f._r1_attr = [
        (11, "독립 QA로 16개 전이 테스트 작성"),
        (11, "CommonJS 상태 머신 구현"),
    ]
    ok, note = register_stage(
        f,
        "subtask",
        "단위: 상태 머신 구현과 검증\n"
        "백로그: [상태 머신 구현과 검증] 독립 QA로 16개 전이 테스트 작성\n"
        "백로그: [상태 머신 구현과 검증] CommonJS 상태 머신 구현",
        "상태 머신",
    )
    assert ok, note
    rows = f.backlog_relays[f.milestones[-1].subtasks[0].st_id].backlogs
    assert [row.submitter for row in rows] == [13, 11]


def test_독립QA_전용단위는_본문마다_QA표식을_반복하지않아도_제작자를피함(
        monkeypatch, tmp_path):
    """U-060: SubTask 범위가 독립 QA면 실제 테스트 행은 그 역할 문구를 매번 반복하지 않는다."""
    from system.rule.milestone import register_stage

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _독립검증_귀속판(
        tmp_path, {11: "기획", 12: "백엔드", 13: "QA"}, "12,13")
    f._r1_attr = [
        (11, "CommonJS 상태 머신 구현"),
        (11, "네 상태의 16개 순서쌍을 모두 새 인스턴스로 검증"),
        (11, "금지 전이 13개의 Error와 호출 전후 상태 동일 확인"),
    ]
    ok, note = register_stage(
        f,
        "subtask",
        "단위: 구현\n"
        "단위: 독립 QA\n"
        "백로그: [구현] CommonJS 상태 머신 구현\n"
        "백로그: [독립 QA] 네 상태의 16개 순서쌍을 모두 새 인스턴스로 검증\n"
        "백로그: [독립 QA] 금지 전이 13개의 Error와 호출 전후 상태 동일 확인",
        "상태 머신",
    )
    assert ok, note
    implementation, qa = f.milestones[-1].subtasks
    assert [b.submitter for b in f.backlog_relays[implementation.st_id].backlogs] == [11]
    assert [b.submitter for b in f.backlog_relays[qa.st_id].backlogs] == [13, 13]


def test_독립검증_비제작후보가_없으면_등록을_닫고_충원을_안내(monkeypatch, tmp_path):
    """명시 계약을 같은 사람 소유로 조용히 등록하지 않는다."""
    from system.rule.milestone import register_stage

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _독립검증_귀속판(tmp_path, {11: "기획"}, "")
    f._r1_attr = [
        (11, "CommonJS 상태 머신 구현"),
        (11, "독립 QA로 전이 테스트 작성"),
    ]
    ok, note = register_stage(
        f,
        "subtask",
        "단위: 상태 머신 구현과 검증\n"
        "백로그: [상태 머신 구현과 검증] CommonJS 상태 머신 구현\n"
        "백로그: [상태 머신 구현과 검증] 독립 QA로 전이 테스트 작성",
        "상태 머신",
    )
    assert not ok
    assert "독립 검증 담당자 분리 계약" in note and "recruit" in note
    assert not f.milestones[-1].subtasks


def test_독립검증_후보가_있어도_역할적합0이면_등록을_닫음(monkeypatch, tmp_path):
    """무관한 직군을 임의 검수자로 지명하는 것도 독립성 충족으로 참칭하지 않는다."""
    from system.rule.milestone import register_stage

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _독립검증_귀속판(tmp_path, {11: "기획", 12: "브랜드"}, "12")
    f._r1_attr = [
        (11, "CommonJS 상태 머신 구현"),
        (11, "독립 QA로 전이 테스트 작성"),
    ]
    ok, note = register_stage(
        f,
        "subtask",
        "단위: 상태 머신 구현과 검증\n"
        "백로그: [상태 머신 구현과 검증] CommonJS 상태 머신 구현\n"
        "백로그: [상태 머신 구현과 검증] 독립 QA로 전이 테스트 작성",
        "상태 머신",
    )
    assert not ok
    assert "역할 적합도가 0보다 큰 후보" in note and "recruit" in note
    assert not f.milestones[-1].subtasks


def test_완료된_구현회의뒤_독립QA만_추가해도_기존제작자를_피함(monkeypatch, tmp_path):
    """현재 마일스톤의 완료 SubTask도 제작 이력이라 후속 QA가 같은 사람에게 돌아갈 수 없다."""
    from system.rule.milestone import register_stage

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _독립검증_귀속판(
        tmp_path, {11: "기획", 12: "백엔드", 13: "QA"}, "12,13")
    f._r1_attr = [(11, "CommonJS 상태 머신 구현")]
    ok1, note1 = register_stage(
        f,
        "subtask",
        "단위: 상태 머신 구현\n"
        "백로그: [상태 머신 구현] CommonJS 상태 머신 구현",
        "상태 머신",
    )
    assert ok1, note1
    old_st = f.milestones[-1].subtasks[0]
    old_backlog = f.backlog_relays[old_st.st_id].backlogs[0]
    old_backlog.status = "done"
    old_backlog.assignee = 11
    old_st.status = "done"

    f._r1_attr = [(11, "독립 QA로 16개 전이 테스트 작성")]
    ok2, note2 = register_stage(
        f,
        "subtask",
        "단위: 독립 검증\n"
        "백로그: [독립 검증] 독립 QA로 16개 전이 테스트 작성",
        "상태 머신",
    )
    assert ok2, note2
    qa_st = f.milestones[-1].subtasks[-1]
    assert f.backlog_relays[qa_st.st_id].backlogs[0].submitter == 13


def test_백로그0건_직군은_판단기회_없이_회의종료불가(monkeypatch, tmp_path):
    """소유를 강제하지 않고, 실질 기고 또는 이유 있는 패스로 직군별 판단 기회만 보장한다."""
    from system.rule.milestone import register_stage
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    _g, f = _meet_flow(tmp_path, bots={11: "L", 12: "VFX", 13: "모션"})
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "게임"
    okm, _ = register_stage(f, "milestone",
                            "이번 주기: 전투 화면\n"
                            "- 전투 연출 확인 | 실증: python3 verify_battle.py", "게임")
    assert okm
    f._r1_targets = {12, 13}
    f._r1_attr = [(12, "충돌 순간 타격 VFX와 클리어 연출 구현")]
    f._r1_passes = {}
    f._r1_responded = {12}
    proposal = ("단위: 전투 연출 | 실증: run 재현\n"
                "백로그: [전투 연출] 충돌 순간 타격 VFX와 클리어 연출 구현")
    ok, note = register_stage(f, "subtask", proposal, "게임")
    assert not ok and "모션" in note and "패스: 이유" in note, (ok, note)
    assert not f.milestones[-1].subtasks

    f._r1_passes = {13: "정적 프로토타입 주기라 별도 모션 자산이 필요하지 않음"}
    ok2, note2 = register_stage(f, "subtask", proposal, "게임")
    assert ok2, note2
    st = f.milestones[-1].subtasks[0]
    assert f.backlog_relays[st.st_id].backlogs[0].submitter == 12


def test_백로그0건이어도_실질기고했으면_수렴회의를_막지않음(monkeypatch, tmp_path):
    """ch95: 개설자 브랜드가 의견을 냈고 브랜드 줄도 결론에 있었지만 소유 0건이라 5회 거부됐다.
    개인별 백로그를 강제하지 않고, 판단 기회를 행사했으면 다른 사람이 전사·수행해도 확정한다."""
    from system.rule.milestone import register_stage
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    _g, f = _meet_flow(tmp_path, bots={11: "브랜드", 12: "프론트"})
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.status.goal = "게임"
    okm, _ = register_stage(f, "milestone",
                            "이번 주기: 모바일 게임\n"
                            "- 시작 확인 | 실증: python3 verify_start.py", "게임")
    assert okm
    f._r1_targets = {11, 12}
    f._r1_responded = {11, 12}
    f._r1_passes = {}
    # 브랜드 문구를 프론트가 결정 구획에 전사해 submitter는 12가 된다.
    f._r1_attr = [(12, "첫 화면 한 문장 규칙과 시작 문구를 구현")]
    ok, note = register_stage(
        f, "subtask",
        "단위: 시작 화면 | 실증: run 재현\n"
        "백로그: [시작 화면] 첫 화면 한 문장 규칙과 시작 문구를 구현", "게임")
    assert ok, note
    st = f.milestones[-1].subtasks[0]
    assert f.backlog_relays[st.st_id].backlogs[0].submitter == 12


def test_옛_직군별소유_등록이의는_재개시_자동폐기(monkeypatch, tmp_path):
    """ch95 파킹 DRAFT의 폐기된 정책 이의가 새 규칙에서도 표결을 영구 차단하지 않는다."""
    import re
    old = ("> [이의 @등록] 직군별 백로그 선택이 빠졌습니다: 브랜드 스토리텔러. "
           "각자는 자기 백로그를 최소 1개 결정 구획에 올리세요.\n")
    draft = "## 결정\n단위: 화면\n" + old + "\n## 참고\n"
    cleaned = re.sub(
        r"(?m)^>\s*\[이의 @등록\]\s*직군별 백로그 선택이 빠졌습니다:.*(?:\n|$)", "", draft)
    assert "직군별 백로그 선택" not in cleaned
    assert "단위: 화면" in cleaned and "## 참고" in cleaned


def test_병합_참조재진술_백로그_반려_거짓완료차단(monkeypatch, tmp_path):
    """[U-041 실측(2026-07-22, 사용자: '카드 비교 규칙 백로그 아래 서브태스크 회의 내용 중복')] 병합
    회의가 'B4'·'B2 점수 공식…' 같은 의존/참조 줄과 재진술을 백로그로 등록(force=True로 중복 게이트
    우회) → 즉시 완료 캐스케이드로 마일스톤 거짓 마감. 순수 참조('B\\d'로 시작)·너무 짧은 줄은 걸러
    반려, 재진술은 submit 중복 게이트(force=False)가 잡는다 — 실작업 단위만 등록."""
    from system.rule.milestone import register_stage
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)

    async def _w(to, b, k):
        return "[패스]"
    f.wake = _w
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.status.goal = "게임"
    _okm, _nm = register_stage(f, "milestone",
                               "단계: 최소버전 → 완성\n이번 주기: 카드 게임 최소버전\n"
                               "- 30턴 완주 | 실증: python3 verify_game.py", "게임")
    assert _okm, _nm
    ok, note = register_stage(f, "subtask",
                              "단위: 게임 로직 | 실증: pytest 통과\n"
                              "백로그: [게임 로직] 승패 판정 함수 신규 구현\n"
                              "백로그: [게임 로직] B1\n"                       # 순수 참조 → 반려
                              "백로그: [게임 로직] 승패 판정 함수 신규 구현\n"  # 재진술 → 중복 게이트
                              "백로그: [게임 로직] B2 점수 공식", "게임")       # 참조 → 반려
    assert ok, note
    st = f.milestones[-1].subtasks[0]
    bls = (f.backlog_relays.get(st.st_id)).backlogs
    assert len(bls) == 1, f"실작업 1건만(참조·재진술 반려) — {[b.body[:20] for b in bls]}"
    assert "승패 판정" in bls[0].body


def test_병합회의_영역과_백로그_한번에_등록_작업직행(monkeypatch, tmp_path):
    """[회의 병합(2026-07-21, 사용자 결정: '1은 사람 수 적어서 — 2로 가자')] 작업나누기+백로그를
    한 회의로: 같은 수렴안의 '단위:'와 '백로그:' 줄을 함께 등록(영역 분배·발제 귀속 재사용),
    등록 후 meeting_stage는 별도 백로그 회의 없이 작업 단계(None) 직행 — 계획 회의 4→3개."""
    from system.rule.milestone import meeting_stage, register_stage
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)

    async def _w(to, b, k):
        return "[패스]"
    f.wake = _w
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.status.goal = "게임"
    _okm, _nm = register_stage(f, "milestone",
                               "단계: 최소버전 → 완성\n이번 주기: 리듬 게임 최소버전\n"
                               "- 30턴 완주 | 실증: python3 verify_game.py", "게임")
    assert _okm, _nm
    ok, note = register_stage(f, "subtask",
                              "단위: 게임 로직 | 실증: pytest 통과\n"
                              "단위: 화면 UI | 실증: run 재현\n"
                              "백로그: [게임 로직] 점수 계산식 구현\n"
                              "백로그: [화면 UI] 입력 처리·렌더링\n"
                              "백로그: [화면 UI] 반응형 3지점 검증", "게임")
    assert ok, note
    assert "작업 영역 2개" in note and "백로그 3개" in note      # 한 회의가 둘 다 등록
    ms = f.milestones[0]
    assert len(ms.subtasks) == 2
    store = f.backlog_relays
    _cnt = {st.st_id: len((store.get(st.st_id) or type("x", (), {"backlogs": []})).backlogs)
            for st in ms.subtasks}
    assert sorted(_cnt.values()) == [1, 2]                       # [영역명] 분배(로직 1·UI 2)
    assert meeting_stage(f) is None                              # 별도 백로그 회의 없이 작업 직행


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
                              "- 30턴 완주 | 실증: python3 verify_game.py", "게임")
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
                              "- 브라우저 30턴 완주 | 실증: python3 verify_game.py", "게임")
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
    # (병합='백로그 줄 필요' 안내 + 게이트 제거로 조건 검사 없음 — 이 테스트 주제는 단위 파싱)
    assert [e for e in stage_preflight("subtask", two_line) if "백로그" not in e] == []
    # [서브태스크 게이트 제거(2026-07-22)] 조건 없는 단위도 이제 유효(작업 영역 grouping) — 실증 불요
    errs = stage_preflight("subtask", "## 결정\n단위: 제목만 있는 단위\n백로그: [제목만] 실작업 항목\n")
    assert [e for e in errs if "실증" in e or "조건" in e] == []
    # 등록 경로도 같은 파싱 — 2줄 표기가 실제 SubTask 2개로 등록된다
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path)
    ms = open_milestone(f, "단판 가위바위보", [{"desc": "한 루프 동작", "verify": "curl로 페이지 200 확인"}])
    landed, note = register_stage(f, "subtask", two_line)
    assert landed and len(ms.subtasks) == 2
    assert ms.subtasks[0].goal == "게임 판정 로직"   # 제목만 goal — 본문·게이트가 단계 이름을 압사시키지 않게
    # [서브태스크 게이트 완결(2026-07-22, GPT e2e 실측: 제목만 단위를 '불량 조건'으로 거부 → 표결 통과해도
    # 등록 막혀 재회의 무한 → 정체 종료·게임 미완)] 제목만/조건 없는 단위도 유효한 작업 영역 — 등록되고,
    # 없는 일감은 백로그 충전 회의가 받친다(껍데기 거부 폐지 = 게이트 제거 결정의 완결).
    landed2, note2 = register_stage(f, "subtask", "## 결정\n단위: 조건 없는 단위\n")
    assert landed2 and "백로그 충전" in note2

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
    # [회의 하나에 목표 하나(2026-07-30, 사용자 지시)] 백로그 회의는 영역 하나로 열린다 —
    # 그 회의의 일감은 전부 그 영역 몫이다(라벨 어휘 대조 배정은 대상 영역이 없을 때의 폴백).
    assert by["게임 규칙"] == 3 and by["판정 로직"] == 0
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
                 "body": "가위바위보 웹게임", "repick_n": 0, "start_retry_n": 0}]

    async def pick(self, mid, done=False, unpick=False, touch=False, **kw):
        self.picks.append({"mid": mid, "done": done, "unpick": unpick, "touch": touch,
                           "start_retry": bool(kw.get("start_retry"))})
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


def test_pending뒤_stop신호면_claim과_흐름시작을_건너뜀(monkeypatch, tmp_path):
    """pending 조회 직후 들어온 stop을 단건 검사에서 consume해도 옛 스냅샷을 claim하지 않는다."""
    from system.sys_core import Sys

    real_sleep = asyncio.sleep

    async def quick_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("system.sys_core.asyncio.sleep", quick_sleep)

    class _StopAtClaimGuide(_RunGuide):
        def __init__(self):
            super().__init__()
            self.stop_checks = []

        async def check_stop(self, ch):
            self.stop_checks.append(ch)
            return True

    g = _StopAtClaimGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L"},
            workspace=str(tmp_path), max_continue=1)
    started = []

    async def must_not_start(*_args, **_kwargs):
        started.append(True)

    s.route_channel_request = must_not_start
    asyncio.run(s.run(g, leader=11, cap=1, poll=0.01, once=True))

    assert g.stop_checks == [g.ch]
    assert g.picks == [] and started == []
    assert any(e["event"] == "request_claim_skipped_stop" for e in s.flow_log)


def test_runner_active등록뒤_leader전_stop은_운반체와_점유를_회수(
    monkeypatch, tmp_path,
):
    """active Flow는 찾았지만 _run_task가 없는 경계에서도 stop이 외부 운반체를 취소해 실행을 막는다."""
    from system.sys_core import Sys

    real_sleep = asyncio.sleep

    async def quick_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("system.sys_core.asyncio.sleep", quick_sleep)
    entered = asyncio.Event()

    class _StopBeforeLeaderGuide(_RunGuide):
        def __init__(self):
            super().__init__()
            self.stop_sent = False
            self.acked = []

        async def all_stops(self):
            if entered.is_set() and not self.stop_sent:
                self.stop_sent = True
                return [{
                    "channel": self.ch,
                    "signal_id": 71,
                    "requested_at": 12.5,
                }]
            return []

        async def ack_stop(self, ch, signal_id=None, requested_at=None):
            self.acked.append((ch, signal_id, requested_at))

    g = _StopBeforeLeaderGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L"},
            workspace=str(tmp_path), max_continue=1)
    ran = []

    async def blocked_situation(*_args, **_kwargs):
        entered.set()
        await asyncio.Future()

    async def must_not_run(*_args, **_kwargs):
        ran.append(True)
        return "실행되면 안 됨"

    s._channel_situation = blocked_situation
    s.run_turn = must_not_run
    asyncio.run(s.run(g, leader=11, cap=1, poll=0.01, once=True))

    events = [e["event"] for e in s.flow_log]
    assert ran == []
    assert g.stopped == []                       # ack가 재개된 요청을 channel-wide 재중지하지 않음
    assert g.acked == [(g.ch, 71, 12.5)]         # 읽은 StopSignal 세대만 확인
    assert s.active_flows == {}
    assert not s.engaged.busy_elsewhere(11, "other")
    assert "cancel_requested" in events
    assert "flow_cancelled_before_leader" in events
    assert not getattr(s, "_flow_user_cancelled", {})


def test_runner_max_flows는_아직시작전_inflight예약까지_센다(monkeypatch, tmp_path):
    """한 poll의 서로 다른 요청도 max_flows=1이면 첫 요청만 claim한다."""
    from system.sys_core import Sys

    real_sleep = asyncio.sleep

    async def quick_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("system.sys_core.asyncio.sleep", quick_sleep)

    class _TwoGuide(_RunGuide):
        async def get_pending(self):
            if self.served:
                return []
            self.served = True
            return [
                {"msg_id": 901, "channel_id": 501, "to_id": 11, "kind": "W",
                 "body": "첫 작업", "repick_n": 0, "start_retry_n": 0},
                {"msg_id": 902, "channel_id": 502, "to_id": 12, "kind": "W",
                 "body": "둘째 작업", "repick_n": 0, "start_retry_n": 0},
            ]

    g = _TwoGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "A", 12: "B"},
            workspace=str(tmp_path), max_continue=1)
    s.max_flows = 1
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_route(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return {"mode": "ignored"}

    s.route_channel_request = blocked_route

    async def scenario():
        runner = asyncio.create_task(
            s.run(g, leader=11, cap=2, poll=0.01, once=True))
        await entered.wait()
        await real_sleep(0)
        claims = [
            p for p in g.picks
            if not p["done"] and not p["unpick"] and not p["touch"]
        ]
        assert [p["mid"] for p in claims] == [901]
        release.set()
        await asyncio.wait_for(runner, timeout=1)

    asyncio.run(scenario())


def test_runner_무지정_Info는_선거중지없이_응답_done(monkeypatch, tmp_path):
    """실제 pending 입구의 Info는 참여 응찰도 산출물-0 중지도 열지 않는다."""
    from system.sys_core import Sys

    real_sleep = asyncio.sleep

    async def quick_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("system.sys_core.asyncio.sleep", quick_sleep)

    class _InfoGuide(_RunGuide):
        async def get_pending(self):
            if self.served:
                return []
            self.served = True
            return [{"msg_id": self.mid, "channel_id": self.ch, "to_id": None,
                     "route_to": None, "kind": "I", "body": "이 기능은 왜 이래요?",
                     "repick_n": 0, "start_retry_n": 0}]

    g = _InfoGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L"},
            workspace=str(tmp_path), max_continue=1)
    elections = []

    async def must_not_elect(*_args):
        elections.append(True)
        return 11, [11]

    async def answer(_flow, _oid, _body, _kind, _role, **_kw):
        return "정상 답변"

    s._elect_proposer = must_not_elect
    s.run_turn = answer
    asyncio.run(s.run(g, leader=11, cap=1, poll=0.01, once=True))

    events = [e["event"] for e in s.flow_log]
    assert elections == []
    assert "flow_done" in events and "flow_no_deliverable" not in events
    assert any(p["done"] for p in g.picks)
    assert g.stopped == []
    assert any(c[0] == "resp" for c in g.calls)


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
    assert not any(p["done"] for p in g.picks)             # mark_stopped가 stopped+done_ts를 원자 종결
    assert not any(p["unpick"] for p in g.picks)           # 재픽 0 = intervention 재주입 0
    assert g.stopped == [500]                              # 중지 표기 — 재개 버튼 경로 생존


def test_첫실질턴_전송실패는_done아니라_즉시재시도(monkeypatch, tmp_path):
    """Task/백로그가 열리기 전 API 실패를 정상 최종발화로 접지 않는다.

    U-053 실판 회귀: Codex StreamReader 한계 오류 뒤 tasks=0인데 flow_done·done_ts가 찍혔다.
    첫 실행 실패는 오류 응답 게시 없이 원 요청을 unpick하고, 별도 관측 이벤트로 남겨야 한다.
    """
    from system.sys_core import Sys

    real_sleep = asyncio.sleep

    async def quick_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("system.sys_core.asyncio.sleep", quick_sleep)
    g = _RunGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L"},
            workspace=str(tmp_path), max_continue=1)

    async def failed_first_turn(flow, _oid, _body, _kind, _role, **_kw):
        flow._last_turn_ok = False
        flow._last_turn_error = "Separator is not found, and chunk exceed the limit"
        return "API Error: Separator is not found, and chunk exceed the limit"

    s.run_turn = failed_first_turn
    asyncio.run(s.run(g, leader=11, cap=1, poll=0.01, once=True))

    events = [e["event"] for e in s.flow_log]
    assert "flow_start_failed" in events and "request_start_retry" in events
    assert "flow_done" not in events
    assert any(p["unpick"] for p in g.picks)
    assert any(p["unpick"] and p["start_retry"] for p in g.picks)
    assert not any(p["done"] for p in g.picks)
    assert not any(c[0] == "resp" for c in g.calls)
    assert g.stopped == []


def test_첫턴실패_재시도상한은_요청payload에서_재시작후에도_유지(monkeypatch, tmp_path):
    """start_retry_n=3인 영속 요청은 새 Sys 프로세스에서도 다시 3회 돌지 않고 즉시 중지한다."""
    from system.sys_core import Sys

    real_sleep = asyncio.sleep

    async def quick_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("system.sys_core.asyncio.sleep", quick_sleep)

    class _RetriedGuide(_RunGuide):
        async def get_pending(self):
            if self.served:
                return []
            self.served = True
            return [{"msg_id": self.mid, "channel_id": self.ch, "to_id": 11, "kind": "W",
                     "body": "가위바위보 웹게임", "repick_n": 21, "start_retry_n": 3}]

    g = _RetriedGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L"},
            workspace=str(tmp_path), max_continue=1)

    async def failed_first_turn(flow, _oid, _body, _kind, _role, **_kw):
        flow._last_turn_ok = False
        flow._last_turn_error = "persistent transport failure"
        return "API Error: persistent transport failure"

    s.run_turn = failed_first_turn
    asyncio.run(s.run(g, leader=11, cap=1, poll=0.01, once=True))

    events = [e["event"] for e in s.flow_log]
    assert "request_start_failed_stopped" in events
    assert "request_start_retry" not in events
    assert not any(p["unpick"] or p["done"] for p in g.picks)
    assert g.stopped == [500]


def test_명시담당_Work가_Task0이면_완료아닌_재개가능중지(monkeypatch, tmp_path):
    """선거를 거치지 않는 To 지정 Work도 Task 0 평문 반환을 완료로 인정하지 않는다.

    중지 표식은 done_ts보다 먼저 써야 bridge의 picked+미완 조건을 통과해 재개 버튼이 살아난다.
    """
    from system.sys_core import Sys

    real_sleep = asyncio.sleep

    async def quick_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("system.sys_core.asyncio.sleep", quick_sleep)

    class _OrderedGuide(_RunGuide):
        def __init__(self):
            super().__init__()
            self.terminal_order = []

        async def pick(self, mid, done=False, unpick=False, touch=False, **kw):
            if done:
                self.terminal_order.append("done")
            return await super().pick(mid, done=done, unpick=unpick, touch=touch, **kw)

        async def mark_stopped(self, ch):
            self.terminal_order.append("stopped")
            await super().mark_stopped(ch)

    g = _OrderedGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L"},
            workspace=str(tmp_path), max_continue=1)

    async def plain_without_task(_flow, _oid, _body, _kind, _role, **_kw):
        return "검토했습니다."

    s.run_turn = plain_without_task
    asyncio.run(s.run(g, leader=11, cap=1, poll=0.01, once=True))

    events = [e["event"] for e in s.flow_log]
    assert "flow_no_deliverable" in events and "false_complete_blocked" in events
    assert "flow_done" not in events
    assert g.terminal_order == ["stopped"]
    assert not any(c[0] == "resp" for c in g.calls)


def test_오분류_Work라도_캐주얼대화는_Task0_정상응답(monkeypatch, tmp_path):
    """분류기가 Work로 보낸 좁은 캐주얼 요청은 기존 계약대로 직접 답하고 제작 게이트를 열지 않는다."""
    from system.sys_core import Sys

    real_sleep = asyncio.sleep

    async def quick_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr("system.sys_core.asyncio.sleep", quick_sleep)

    class _CasualGuide(_RunGuide):
        async def get_pending(self):
            if self.served:
                return []
            self.served = True
            return [{"msg_id": self.mid, "channel_id": self.ch, "to_id": 11, "kind": "W",
                     "body": "배고파", "repick_n": 0}]

    g = _CasualGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L"},
            workspace=str(tmp_path), max_continue=1)

    async def casual_reply(_flow, _oid, _body, _kind, _role, **_kw):
        return "뭐라도 챙겨 드세요."

    s.run_turn = casual_reply
    asyncio.run(s.run(g, leader=11, cap=1, poll=0.01, once=True))

    events = [e["event"] for e in s.flow_log]
    assert "flow_done" in events and "flow_no_deliverable" not in events
    assert any(p["done"] for p in g.picks)
    assert g.stopped == []
    assert any(c[0] == "resp" for c in g.calls)


def test_사용자중지_reap은_장부전진해도_재픽하지않음(monkeypatch, tmp_path):
    """사용자 중지는 미완 Task·장부 전진 조건보다 우선한다 — stop 직후 unpick 재시작 금지."""
    from system.guide_tools import TaskRef
    from system.protocol import TaskStatus
    from system.rule.backlog import relay_for
    from system.rule.milestone import open_milestone, open_subtask
    from system.sys_core import Sys

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    real_sleep = asyncio.sleep

    async def quick_sleep(_delay):
        await real_sleep(0.001)

    monkeypatch.setattr("system.sys_core.asyncio.sleep", quick_sleep)

    class _StopGuide(_RunGuide):
        def __init__(self):
            super().__init__()
            self.stop_sent = False

        async def all_stops(self):
            if self.served and not self.stop_sent:
                self.stop_sent = True
                return [self.ch]
            return []

    g = _StopGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L", 12: "백엔드"},
            workspace=str(tmp_path), max_continue=6)
    started = asyncio.Event()

    async def long_work(flow, _oid, _body, _kind, _role, **_kw):
        if flow.current is None:
            status = TaskStatus(task_id="stop-1", purpose="p", status="진행", goal="웹 구현",
                                owner="", group=[])
            flow.current = TaskRef(task_id="stop-1", thread_id="thr", block_id="blk",
                                   status=status, team=[11, 12], owner=0)
            ms = open_milestone(flow, "구현", [{"desc": "빌드", "verify": "pytest"}])
            st = open_subtask(flow, ms, "API", [])
            r = relay_for(flow, st)
            b = r.submit(12, "API 구현", force=True)
            r.pick(12, b.backlog_id, 12)              # 장부는 실제 전진(_prog=True)
            started.set()
        await asyncio.Future()                         # 사용자 stop이 이 턴을 취소

    s.run_turn = long_work

    async def scenario():
        task = asyncio.create_task(s.run(g, leader=11, cap=1, poll=0.01, once=True))
        await started.wait()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    events = [e["event"] for e in s.flow_log]
    assert "user_stopped_closed" in events
    assert "request_repick" not in events and "stalled_stopped" not in events
    assert not any(p["unpick"] for p in g.picks)
    assert not any(p["done"] for p in g.picks)


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
    # [배달 관문(2026-07-31)] 주기를 닫으려면 사람이 열 수 있는 것이 있어야 한다 — 이 테스트의
    # 관심사는 링크 표기이므로 정적 진입을 만들어 관문을 만족시킨다(주소는 표기 확인용 가짜).
    import pathlib
    pathlib.Path(f.workspace, "index.html").write_text("<h1>rps</h1>", encoding="utf-8")
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


def test_마일스톤회의_주기는_써볼수있는_완성물_공정쪼개기_금지():
    """[U-056 → 계약 정정(2026-07-27, 사용자: 'Task는 최대한으로, 마일스톤은 주기마다 산출 가능한
    최소 단위로')] 종전 계약은 '쪼개지 마세요'만 고정해, 설계 정본("Milestone = Task를 큰 주기로
    나눈다")과 반대로 주기가 1개로 수렴했다 — 그러면 그 하나가 Task 전부를 증명해야 해서 마일스톤이
    최대가 되고, 사용자는 끝날 때까지 아무것도 못 본다. 금지 대상은 **공정 쪼개기**(뒤 항목이 혼자
    써볼 수 없는 것)이지 사다리가 아니다. 세 표면에 같은 계약이 실린다."""
    from system.rule.milestone import stage_agenda, stage_draft_template, stage_frame

    _, agenda = stage_agenda("milestone")
    surfaces = (agenda, stage_draft_template("milestone", "안건"), stage_frame("milestone"))
    for text in surfaces:
        assert "`→`로 나뉜 각 항목은 각각 별도 주기" in text
        assert "사용자가 실제로 열어서 써볼 수 있는 완성물" in text   # 주기의 정의
        assert "나눌수록 좋습니다" in text                            # 사다리 복원
        assert "한 산출물의 공정" in text                             # 금지 대상은 이것뿐
        assert "주기 수를 명시하면 그 수를 그대로 보존" in text


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


def test_R1_미응답_커버리지는_기회재부여_뒤_묵시패스로_수렴(monkeypatch, tmp_path):
    """[2026-07-26 — ch95 실증·전수 인벤토리 1위 정체원] R1 독립 기고 응답 집합은 첫 개회에 한 번만
    수집돼 얼어붙는다. 그때 침묵한 참여자는 영영 '미응답'으로 남아 직군 커버리지가 매번 거부되고,
    회의는 문구만 고친 같은 안을 다시 낸다(ch95: 13분간 등록 0건). 기회를 한 번 더 주고, 그래도
    무응답이면 묵시 패스로 확정해 사람 없이 수렴한다 — 소유는 아무에게도 강제하지 않는다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    g, f = _meet_flow(tmp_path, bots={11: "L", 12: "백엔드", 13: "브랜드 스토리텔러"})
    f.floor_mode = "turn-taking"
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))

    async def wake(to, b, k):
        if "발언권 응찰" in b:
            return "[응찰: 5] 초안을 채우겠습니다" if to == 12 else "[패스]"
        if "발언권 획득" in b or "차례입니다" in b:
            _fill_draft(tmp_path)
            return "결정 구획을 채웠습니다."
        if "결론 확정 표결" in b:
            return "[찬성]"
        if "종결 확인" in b:
            return "[종료]"
        return "[패스]"          # 이유 없는 패스 = 미응답(ch95 재현 — 기회 미행사로 집계)
    f.wake = wake
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.status.goal = "방명록"
    from system.rule.milestone import Criterion, Milestone, SubTask
    st = SubTask(st_id="ST-1", goal="등록", criteria=[Criterion("등록 검증", "pytest 통과")])
    f.milestones = [Milestone(ms_id="MS-1", goal="1주기",
                              criteria=[Criterion("방명록 동작", "run 재현")], subtasks=[st])]
    asyncio.run(t["meet"].handler({"topic": "다음 일감 전부 열거", "members": "", "rounds": "3",
                                   "my_opinion": "여는 의견"}))
    names = [e for e, _ in events]
    # 첫 등록은 커버리지로 반려된다(기회 미행사가 사실이므로 — 이 성질은 유지)
    assert any(e == "stage_register_rejected"
               and "직군별 판단 응답이 빠졌습니다" in str(kw.get("reason") or "")
               for e, kw in events), names
    # 그 반려가 무한 반복이 아니라 한 라운드 안에서 수렴한다: 재수집 → 무응답은 묵시 패스 → 낡은 이의 정리
    _rc = next(kw for e, kw in events if e == "r1_coverage_recollected")
    assert _rc["asked"] == 2 and _rc["implicit"] == 2, _rc
    assert "stale_r1_coverage_objection_cleared" in names, names
    assert "stage_confirmed" in names, names          # 같은 회의에서 실제로 등록까지 간다
    assert names.count("stage_register_rejected") == 1, names


def test_팀에_독립검증_적합자가_없으면_구조가_충원한다(monkeypatch, tmp_path):
    """[2026-07-26 — U-062 실증] 등록기는 제작자와 겹치는 검증 항목을 fail-closed로 거부한다(옳다 —
    무관한 직군을 검수자로 세우는 것은 독립성 참칭). 그런데 팀에 적합자가 없으면 회의는 문구만 고친
    같은 안을 다시 내고 거부가 무한 반복됐다(백로그 0건). 반려문이 요구하는 '충원'을 구조가 집행한다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    # 팀(11 기획·12 브랜드)엔 검증 적합자가 없고, 로스터에만 QA(14)가 있다.
    g, f = _meet_flow(tmp_path, bots={11: "기획", 12: "브랜드", 14: "QA"})
    events = []
    f.log = lambda ev, **kw: events.append((ev, kw))
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    assert 14 not in f.current.team

    from system.rule.communication import meet as _meet   # noqa: F401 (구조 충원은 meet 내부 경로)
    from system.rule.comm_ceremonies import _recruit_join
    # 구조 충원이 쓰는 합류 경로 자체의 계약: 로스터 QA를 Task 팀에 넣는다.
    err = asyncio.run(_recruit_join(f, 14, "QA", via="구조 충원", fresh=True))
    assert err is None, err
    assert 14 in f.current.team and 14 in f.project_team

    # 충원 뒤에는 등록기가 실제로 제작자와 다른 담당자를 찾는다(더 이상 '적합자 없음' 반려 아님).
    from system.rule.milestone import register_stage
    f.origin_request = "구현 백로그와 독립 QA 백로그를 서로 다른 담당자에게 등록하세요."
    f.current.status.goal = "상태 머신"
    okm, note = register_stage(f, "milestone",
                               "이번 주기: 상태 머신 완성\n"
                               "- 상태 전이 계약 충족 | 실증: node test_state_machine.js",
                               "상태 머신")
    assert okm, note
    f._r1_attr = [(12, "CommonJS 상태 머신 구현"), (12, "독립 QA로 전이 테스트 작성")]
    ok, note2 = register_stage(f, "subtask",
                               "단위: 상태 머신 구현과 검증\n"
                               "백로그: [상태 머신 구현과 검증] CommonJS 상태 머신 구현\n"
                               "백로그: [상태 머신 구현과 검증] 독립 QA로 전이 테스트 작성",
                               "상태 머신")
    assert ok, note2
    _st = f.milestones[-1].subtasks[-1]
    _bl = f.backlog_relays[_st.st_id].backlogs
    assert _bl[-1].submitter == 14        # 검증 항목은 충원된 QA에게 — 제작자(12)와 분리


def test_형식_반려문은_해법까지_잘리지_않고_전달된다():
    """[2026-07-27 U-063·U-064 실측] 반려 사유는 DRAFT 이의로 걸려 봇의 다음 wake에 실린다 — 그런데
    150~200자에서 잘려, 봇은 **불평만 받고 '어떻게 고치라'는 해법은 못 받았다**. 화면 조건의 실증
    경로(headless 브라우저로 판정 스크립트) 안내가 통째로 사라져 봇들이 '서버 띄우기'·'계획서 grep'
    같은 비검증 명령으로 우회하다 계획 단계에서 두 판 연속 교착했다. 안내는 끝까지 전달돼야 한다."""
    import inspect
    from system.rule import communication as C
    from system.rule.milestone import _verify_is_executable, looks_like_verification_command

    src = inspect.getsource(C)
    assert '"> [이의 @형식] {e[:900]}"' in src, "형식 반려문 클립이 해법을 자른다"
    assert '"> [이의 @등록] {str(note)[:900]}"' in src, "등록 반려문 클립이 해법을 자른다"

    # 두 반려문 모두 화면 조건의 실증 경로를 담고, 900자 안에 들어간다.
    from system.rule.milestone import _milestone_verifier_errors, gate_criteria
    for fn in (_milestone_verifier_errors, gate_criteria):
        body = inspect.getsource(fn)
        assert "headless" in body, f"{fn.__name__}에 화면 조건 실증 경로 안내가 없다"

    # 게이트 자체는 건전해야 한다 — 비검증 명령은 계속 거부(거짓 완료 차단).
    assert not looks_like_verification_command('rg -n "설계" .collab/T-1/DRAFT.md')
    assert not looks_like_verification_command("python3 -m http.server 4173 --directory public")
    assert looks_like_verification_command("python3 verify_ui.py")


def test_같은_단계_반복_개설도_상한에_걸린다():
    """[2026-07-27 U-067 실측] 정체 카운터는 '착지 실패'만 셌다 — 회의가 매번 **성공**해 단계가
    바뀌면 0으로 리셋되므로, 같은 단계가 12번 다시 열려 단위 36개를 만드는 동안 한 번도 안 걸렸다.
    성공 착지도 세어 같은 단계의 반복 개설을 끊는다."""
    import inspect
    from system.sys_core import Sys

    src = inspect.getsource(Sys)
    assert "_stage_reopen_cap" in src, "같은 단계 재개설 횟수를 안 센다"
    i = src.index("_seen_st = getattr(flow, \"_stage_open_n\"")
    seg = src[i:i + 900]
    assert "stage_stall_break" in seg, "반복 개설이 상한에 안 걸린다"
    # [진척이 있으면 계수를 턴다(2026-07-29, U-079 실측)] 상한이 판 수명 전체에 누적되면 오래 도는
    # 판은 어떤 단계 회의도 못 열고 재개할 때마다 즉시 파킹된다 — 막을 것은 헛도는 재개설뿐이다.
    j = src.index("if status == \"done\":")
    assert "_stage_open_n = {}" in src[j:j + 800], "백로그 완료가 반복 계수를 초기화하지 않는다"


def test_파킹_신호는_같은_바퀴에서_소비된다():
    """[2026-07-27 U-067 실측] 실증이 파킹 신호를 세우고 False를 반환하면, 곧바로 아래 단계 회의가
    한 번 더 열려 **멈추기 직전에 단위가 3개 더 생겼다**. 신호가 서 있으면 회의를 열지 않는다."""
    import inspect
    from system.sys_core import Sys

    src = inspect.getsource(Sys)
    i = src.index("if await self._verify_exhausted_milestone(flow):")
    seg = src[i:i + 700]
    j = seg.index("_stage_pending()")
    assert "_stage_stuck" in seg[:j], "파킹 신호를 확인하기 전에 단계 회의가 먼저 열린다"


def test_이어받은_회의의_기존_줄은_첫_발언자_것이_아니다():
    """[2026-07-27 U-067 실측] 첫 발언 시점의 '이미 본 줄'이 빈 집합이라, 같은 단계 재회의(초안
    보존)에서 **첫 발언자 한 명이 파일에 이미 있던 모든 줄을 통째로 귀속**받았다 — 한 봇이 한
    회의에서 여러 건을 연속 등재하던 쏠림의 뿌리. 첫 스캔은 기존 줄을 기준선으로 깔고 시작한다."""
    import inspect
    from system.rule import communication

    src = inspect.getsource(communication)
    i = src.index('_seen_lines')
    seg = src[max(0, i - 900):i + 300]
    assert 'if "lines" not in _dstate' in seg, "이어받은 초안의 줄이 첫 발언자에게 상속된다"
    assert "_dregion(_dtxt)" in seg, "귀속은 파일 전문, 등록은 결정 구획 — 범위가 어긋난다"


def test_귀속_폴백은_이_판의_팀_안에서_고르고_동점을_분산한다():
    """[2026-07-27 U-067 실측] 폴백이 **전사 로스터**에서 골라 이 판에 없는 사람이 주인이 되거나
    늘 같은 사람이 뽑혔다(적합도 전원 동점이면 max가 사전 첫 키를 준다 → 한 명 깔때기)."""
    import inspect
    from system.rule import milestone

    src = inspect.getsource(milestone.register_stage)
    i = src.index("def _owner_fb")
    seg = src[max(0, i - 700):i + 500]
    assert "_fb_pool" in seg and "project_team" in seg, "폴백이 전사 로스터에서 고른다"
    assert "_fb_load" in seg, "적합도 동점이 늘 같은 사람에게 간다"


def test_백로그_귀속_출처가_기록된다():
    """[2026-07-27] 한 사람에게 쏠릴 때 '정말 그가 썼나'와 '기계가 몰아줬나'를 로그로 가를 수 없어
    원인 판별이 막혔다. 값은 그대로 두고 출처만 남긴다."""
    import inspect
    from system.rule import milestone

    src = inspect.getsource(milestone.register_stage)
    assert "backlog_owner_attributed" in src, "귀속 출처를 기록하지 않는다"
    i = src.index("backlog_owner_attributed")
    seg = src[max(0, i - 600):i + 300]
    for tag in ('"r1"', '"draft"', '"fallback"'):
        assert tag in seg, f"출처 구분({tag})이 없다"


def test_운영_안내는_봇의_상황요약에_들어가지_않는다():
    """[2026-07-27 U-067 실측] 파킹·중지 같은 SYS 게시는 사람에게 다음 행동을 알리는 문구인데,
    상황 요약이 발신자 0을 전부 '사람'으로 라벨해 봇에게 사람의 말처럼 들어갔다. 그 결과 마감 관문
    앞에서 봇 6명이 연달아 "`재개` 눌러서 다시 태우면 됩니다"라며 **자기 차례를 사람 차례로** 읽었다
    (마감 호출 0회). 사용자의 실제 메시지는 그대로 들어와야 한다."""
    import asyncio
    from types import SimpleNamespace
    from system.sys_prompt import channel_situation

    rows = [SimpleNamespace(body="[막혀서 멈췄어요] … '재개'를 누르면 이어서 진행해요", from_id=0,
                            message_id="1"),
            SimpleNamespace(body="게임 만들어줘", from_id=0, message_id="2"),
            SimpleNamespace(body="■ 중지됨", from_id=0, message_id="3"),
            # 열거 대신 구조로 가른다 — 목록에 없던 새 안내도 걸러져야 한다(실측: 이 문구가 빠져 있었다)
            SimpleNamespace(body="[사람 조치 필요] 작업이 멈췄어요 — '재개'를 누르면 다시 시도해요",
                            from_id=0, message_id="4")]

    class _G:
        async def read_thread(self, *a, **k):
            return rows

    out = asyncio.run(channel_situation(SimpleNamespace(guide=_G(), bot_info={}), 1))
    assert "재개" not in out, "운영 안내가 봇에게 사람의 말로 들어간다"
    assert "중지됨" not in out, "종결 표기가 대화로 섞인다"
    assert "사람 조치 필요" not in out, "목록에 없던 운영 안내가 새어 들어간다"
    assert "게임 만들어줘" in out, "사용자의 실제 요청까지 걸러졌다"


def test_이의를_지워도_등록검사가_막으면_완성이_아니다():
    """[2026-07-27 U-068 실측] 완성 판정이 '빈칸 0·이의 0'만 봐서, 봇들이 기계검사 이의를 **줄을
    지워** 해소 처리하고 명령은 그대로 뒀다(`npm run verify …` — 이 판엔 npm이 없다). 회의는
    '다 됐다', 등록 관문은 '아직'으로 갈려 표결→거부→재주입이 제자리를 돌다 무진전 컷 →
    깨끗한 판이 계획 단계에서 두 번 파킹했다. 완성 판정과 등록 검사는 같은 진실원이어야 한다."""
    import inspect
    from system.rule import communication

    src = inspect.getsource(communication)
    i = src.index("draft_blocked_by_preflight")
    seg = src[max(0, i - 1500):i + 200]
    assert "stage_preflight" in seg, "완성 판정이 등록에서 쓸 검사를 보지 않는다"
    assert "[이의 @형식]" in seg, "검사 실패가 회의로 되돌아오지 않는다(봇이 알 길이 없다)"
    # 완성 컷 직전에 걸려야 한다 — 표결까지 간 뒤 거부되면 같은 낭비가 반복된다
    j = src.index('flow.log("draft_ready"')
    assert i < j, "완성 선언 뒤에 검사하면 표결→거부 루프가 그대로다"


def test_같은_형식벽_반복은_사유와_함께_사람에게_넘어간다():
    """[2026-07-27] 봇이 이의를 지우고 명령은 안 고치는 되풀이는 패스만 태운다 — 사용량은 유한하다.
    같은 사유가 3번이면 헛돌지 말고 사람에게 넘기되, **무엇을 답해야 하는지 그대로 실어** 보낸다.
    종전 멈춤 안내는 '한 줄 알려주세요'뿐이라 판을 열어봐도 무엇을 답할지 알 수 없었다."""
    import inspect
    from system.rule import communication
    from system.sys_core import Sys

    src = inspect.getsource(communication)
    i = src.index("draft_blocked_by_preflight")
    seg = src[max(0, i - 900):i + 900]
    assert "_pf_repeat" in seg, "같은 벽의 반복을 세지 않는다"
    assert "_stage_stuck" in seg, "반복해도 사람에게 안 넘어간다"

    park = inspect.getsource(Sys)
    j = park.index("막혀서 멈췄어요")
    assert "막힌 지점" in park[j:j + 900], "멈춤 안내가 무엇을 답해야 하는지 안 보여준다"


def test_회의_골격과_등록_요구가_어긋나지_않는다():
    """[2026-07-27 전수감사] 봇이 편집하는 **골격**에 없는 것을 등록기가 요구하면, 골격만 채운
    초안이 완성(빈칸 0·이의 0)으로 판정돼 표결까지 간 뒤 반드시 거부된다 — 예산만 태우는 구조다.
    ①선행 대기(blocked) 연결 칸이 골격에 없었다 ②단위 거부문이 폐지된 계약('| 실증')을 요구했다."""
    import inspect
    from system.rule import milestone
    from system.rule.milestone import stage_draft_template

    bl = stage_draft_template("backlog", "안건")
    assert "[해결:" in bl, "등록기가 요구하는 선행 대기 연결 칸이 골격에 없다"

    reg = inspect.getsource(milestone.register_stage)
    assert "'단위: ⟦목표⟧ | ⟦실증⟧'" not in reg, "폐지된 계약 문구로 반려한다(봇이 엉뚱하게 고친다)"
    st = stage_draft_template("subtask", "안건")
    assert "단위: ⟦작업 영역" in st, "골격의 단위 형식이 바뀌었다"
