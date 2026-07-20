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
