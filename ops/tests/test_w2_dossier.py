"""[W2 — BOT_ARCH_REDESIGN 2026-07-03] Task Dossier — 협의 원본의 단일 기록(.collab/).

B-07 배포 제외 선행(.collab 유출 차단 — append-if-missing) / B-08 .collab 쓰기 보호(2중)
B-09 [Phase A] SYS 문서 쓰기(GOAL/MINUTES/REPORTS/craft/PLAYBOOK — 관측 전용·원자·무절단)
B-10 [Phase B] 위임 dedup(ORGANT_DOC_COLLAB=1, 기본 off=기존동작) — 첫 wake 전문·재주입 다이제스트+참조
B-11 [Phase C] meet R2+ 압축 주입·스냅샷 dossier_path·복구 '내용은 문서 우선, 사실은 스냅샷'
B-12 post_document 매체 조건부 clip(실구현=200자+ref / 폴백=500자·참조 없음)
"""
import asyncio
import os

from test_sys import FakeGuide, _flow, _tools

from system import deploy as dp
from system._util import (doc_collab_on, dossier_append, dossier_read, dossier_rel,
                          dossier_write)
from system.guide_tools import Flow, make_guide_tools
from system.permissions import make_pre_tool_use_hook, organt_allowed_tools
from system.protocol import Kind
from system.rule.communication import _say_speech
from system.sys_core import Sys, _parse_goal_doc


def _doss(f, name):
    """현재 Task의 Dossier 문서 절대경로."""
    return os.path.join(str(f.workspace), dossier_rel(f.current.task_id), name)


def _flow3(g, tmp_path):
    """리더+2직군, 작업공간 있는 흐름 — meet/vote/위임 문서화 테스트용."""
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11,
             bot_info={11: "리더", 12: "백엔드", 13: "QA"})
    f.start_root("root")
    f.workspace = str(tmp_path)
    for a in ("gap_checked", "percept_checked", "acceptance_checked", "decomp_checked",
              "data_prov_checked", "staffing_exempt", "iface_dialogue_checked",
              "offdomain_checked", "crossdomain_checked", "existence_checked",
              "owner_protect_checked"):
        setattr(f, a, True)
    return f


# ──────────────────────────── B-07 배포 제외 선행 ────────────────────────────

def _deploy_stub(tmp_path, monkeypatch, with_gitignore):
    ws = tmp_path
    (ws / "package.json").write_text('{"scripts": {"start": "node server.js"}}')
    (ws / "server.js").write_text("require('http')")
    os.makedirs(ws / ".collab" / "T-1", exist_ok=True)
    (ws / ".collab" / "T-1" / "MINUTES.md").write_text("협의 원본 — 공개 push 금지")
    if with_gitignore:
        (ws / ".gitignore").write_text("node_modules/\n*.log\n")
    # GitHub API 첫 호출에서 실패시켜 push 이전(=스테이징 직후)에 중단 — 네트워크 없이 검증.
    monkeypatch.setattr(dp, "_http", lambda *a, **k: (500, {"message": "stub"}))
    out = dp.deploy_sync(str(ws), "svc-x", "pat", "user", "rk", "own")
    assert "배포 실패(GitHub repo)" in out
    return ws


def test_B07_기존_gitignore에_collab_append_후_스테이징_제외(tmp_path, monkeypatch):
    """append-if-missing: 기존 레포의 .gitignore에 `.collab/`를 더하고, git add -A 스테이징에서
    협의 원본이 제외된다(공개 GitHub push 유출 차단 — B-09 첫 쓰기보다 선행하는 이유)."""
    ws = _deploy_stub(tmp_path, monkeypatch, with_gitignore=True)
    gi = (ws / ".gitignore").read_text()
    assert ".collab/" in gi and "node_modules/" in gi          # 기존 내용 보존 + append
    rc, files = dp._git(["ls-files"], str(ws))
    assert rc == 0 and ".collab" not in files                  # 스테이징(=push 대상)에서 제외


def test_B07_gitignore_없으면_collab_포함해_생성(tmp_path, monkeypatch):
    ws = _deploy_stub(tmp_path, monkeypatch, with_gitignore=False)
    assert ".collab/" in (ws / ".gitignore").read_text()
    rc, files = dp._git(["ls-files"], str(ws))
    assert rc == 0 and ".collab" not in files


def test_B07_이미_있으면_중복_append_안함(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text(".collab/\n")
    _deploy_stub(tmp_path, monkeypatch, with_gitignore=False)   # 이미 존재 — 그대로
    assert (tmp_path / ".gitignore").read_text().count(".collab") == 1


# ──────────────────────────── B-08 .collab 쓰기 보호(2중) ────────────────────────────

class _Audit:
    def __init__(self):
        self.records = []

    def record(self, event, **fields):
        self.records.append((event, fields))
        return {}


def test_B08_permissions_collab_Write_Edit_거부_처방동봉():
    """봇의 Write/Edit가 .collab/(협의 원본)을 향하면 거부 — 거부 사유에 기록 경로 처방
    ('meet/vote/set_goal/보고로만')을 동봉한다. 워크스페이스의 다른 경로는 종전대로 통과."""
    a = _Audit()
    hook = make_pre_tool_use_hook(a, organt_allowed_tools())
    for tool in ("Write", "Edit"):
        out = asyncio.run(hook({"tool_name": tool, "cwd": "/ws",
                                "tool_input": {"file_path": ".collab/T-1/GOAL.md"}}, "t1", None))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "meet/vote/set_goal/보고" in out["hookSpecificOutput"]["permissionDecisionReason"]
    out = asyncio.run(hook({"tool_name": "Write", "cwd": "/ws",
                            "tool_input": {"file_path": "/ws/.collab/x.md"}}, "t2", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"   # 절대경로도 동일
    ok = asyncio.run(hook({"tool_name": "Write", "cwd": "/ws",
                           "tool_input": {"file_path": "public/app.js"}}, "t3", None))
    assert ok == {}                                                    # 정상 산출물 경로는 통과


def test_B08_run_셸_우회도_차단_Read는_잠기지_않음(tmp_path):
    """permissions 훅은 Write/Edit만 잡는다 — bash `sed -i`/`cp`/`rm` 우회를 run 게이트가 막는다
    (거부에 같은 처방). .collab 무관 명령은 통과(실행됨)."""
    f = _flow3(FakeGuide(), tmp_path)
    t = {x.name: x for x in make_guide_tools(f, 11, "leader")}
    asyncio.run(t["create_task"].handler({"members": "12"}))
    for cmd in ("sed -i 's/a/b/' .collab/T-1/GOAL.md", "cp x.md .collab/T-1/",
                "rm .collab/T-1/MINUTES.md"):
        r = asyncio.run(t["run"].handler({"command": cmd}))
        txt = r["content"][0]["text"]
        assert "실행 거부" in txt and "meet/vote/set_goal/보고" in txt
    ok = asyncio.run(t["run"].handler({"command": "echo hello"}))
    assert "[exit 0]" in ok["content"][0]["text"]
    # 오탐 방지: '.collaboration' 같은 유사 이름은 협의 기록이 아님 — 단어 경계 판정으로 통과
    ok2 = asyncio.run(t["run"].handler({"command": "echo x.collaboration.js"}))
    assert "[exit 0]" in ok2["content"][0]["text"]


# ──────────────────────────── B-09 [Phase A] SYS 문서 쓰기 ────────────────────────────

def test_B09_set_goal이_GOAL_md_멱등_재작성(tmp_path):
    g = FakeGuide()
    f = _flow3(g, tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.participated.add(12)
    asyncio.run(t["set_goal"].handler({
        "purpose": "풀 문제", "goal": "측정가능한 성공", "acceptance": "[존재이유] 없으면 깨짐",
        "standard": "최대 표준", "interfaces": "GET /api 계약"}))
    doc = open(_doss(f, "GOAL.md"), encoding="utf-8").read()
    for sec in ("## Purpose", "## Goal", "## Acceptance", "## Standard", "## Interfaces"):
        assert sec in doc
    assert "측정가능한 성공" in doc and "[존재이유] 없으면 깨짐" in doc
    # 멱등 재작성: 두 번째 set_goal이 문서를 최신 상태로 전체 재작성(누적 acceptance 포함)
    asyncio.run(t["set_goal"].handler({"goal": "더 정밀한 성공", "acceptance": "추가 항목"}))
    doc2 = open(_doss(f, "GOAL.md"), encoding="utf-8").read()
    assert "더 정밀한 성공" in doc2 and "추가 항목" in doc2 and "[존재이유] 없으면 깨짐" in doc2


def test_B09_meet가_MINUTES_md에_전문_append(tmp_path):
    """회의 발언 전문(무절단)이 MINUTES.md에 append — collab_notes의 6,000자 head-keep 캡이
    '표기만 남고 내용은 소멸'시키던 것의 내용 보존 원본. R1 즉시 기록 + 라운드별 블록."""
    g = FakeGuide()
    f = _flow3(g, tmp_path)
    long = "협의근거 " * 400                       # 2,000자 — clip(1,500) 너머 내용 보존 확인
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))

    async def wake(to, b, k):
        return f"{to}의 입장: {long}"
    f.wake = wake
    asyncio.run(t["meet"].handler({"topic": "저장 방식", "members": "", "rounds": "2"}))
    doc = open(_doss(f, "MINUTES.md"), encoding="utf-8").read()
    assert "## 회의 — 저장 방식 [1R 독립의견]" in doc and "[2R 토론]" in doc
    assert long in doc                             # 전문 무절단(잘림 표기 자체가 없어야 함)
    assert "안전망에서 잘림" not in doc
    # collab_notes(주입 경로)는 종전 그대로 6,000자 캡 — Phase A는 주입 무변경
    assert len(f.current.collab_notes) <= 6100


def test_B09_vote가_MINUTES_md에_전문_append(tmp_path):
    g = FakeGuide()
    f = _flow3(g, tmp_path)
    long = "선택근거 " * 300                       # 1,500자 — 채널 clip(400) 너머 보존 확인
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))

    async def wake(to, b, k):
        return f"[표] Canvas\n{long}"
    f.wake = wake
    asyncio.run(t["vote"].handler({"question": "렌더?", "options": "Canvas;SVG", "members": ""}))
    doc = open(_doss(f, "MINUTES.md"), encoding="utf-8").read()
    assert "## 표결 — 렌더?" in doc and long in doc


def test_B09_deliver가_REPORTS_md에_Work응답_전문_append(tmp_path):
    g = FakeGuide()
    f = _flow3(g, tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.participated.add(12)
    asyncio.run(t["set_goal"].handler({"goal": "동작"}))
    report = "[결과] 완료\n[변경] server.js\n[검증] run 통과\n[리스크] 없음\n" + "상세 " * 500

    async def wake(to, b, k):
        f.act_count += 1
        f.act_by[to] = f.act_by.get(to, 0) + 1     # 실작업 → '인도'
        return report
    f.wake = wake
    asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    doc = open(_doss(f, "REPORTS.md"), encoding="utf-8").read()
    assert "Work 응답(인도)" in doc and report in doc          # 전문 보존(채팅 clip과 무관)
    # Info·크래시 응답은 기록 대상 아님 — 파일에 Work 1건만
    assert doc.count("## ") == 1


def test_B09_스냅샷에_dossier_path_상대경로(tmp_path):
    g = FakeGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L", 12: "M"},
            workspace=str(tmp_path))
    f = _flow3(g, tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    snap = s._task_snapshot(f, f.current)
    assert snap["dossier_path"] == f".collab/T-{f.current.task_id}"
    assert not os.path.isabs(snap["dossier_path"])             # 개명(_idify_workspace) 대응 — 상대만


def test_B09_스캐폴드_PLAYBOOK_1회_craft_미러_재작성(tmp_path):
    g = FakeGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "리더", 12: "백엔드"},
            workspace=str(tmp_path))
    s.bot_profiles[12] = "v1 기준"          # [격리] 미러 소스 = 그 직군 보유 봇(12)의 개인 기준
    f = _flow3(g, tmp_path)
    s._write_dossier_scaffold(f)
    pb = os.path.join(str(tmp_path), ".collab", "PLAYBOOK.md")
    craft = os.path.join(str(tmp_path), ".collab", "craft", "백엔드.md")
    assert os.path.exists(pb) and "v1 기준" in open(craft, encoding="utf-8").read()
    # PLAYBOOK은 정적(1회) — 재호출에 덮이지 않음. craft는 증류 반영 위해 재작성.
    open(pb, "a", encoding="utf-8").write("사람 편집 흔적")
    s.bot_profiles[12] = "v2 증류됨"        # 개인 증류 반영 → 재미러
    s._write_dossier_scaffold(f)
    assert "사람 편집 흔적" in open(pb, encoding="utf-8").read()
    assert "v2 증류됨" in open(craft, encoding="utf-8").read()


def test_B09_워크스페이스_없으면_전부_무해_no_op():
    f = _flow(FakeGuide())
    f.workspace = None
    assert dossier_write(f, "GOAL.md", "x") is False
    assert dossier_append(f, "MINUTES.md", "x") is False
    assert dossier_read(f, "MINUTES.md") is None


# ──────────────────────────── B-10 [Phase B] 위임 dedup(플래그) ────────────────────────────

def _delegation_flow(tmp_path, monkeypatch=None, flag=False):
    if monkeypatch is not None:
        if flag:
            monkeypatch.setenv("ORGANT_DOC_COLLAB", "1")
        else:
            monkeypatch.delenv("ORGANT_DOC_COLLAB", raising=False)
    g = FakeGuide()
    f = _flow3(g, tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.participated.add(12)
    asyncio.run(t["set_goal"].handler({"goal": "동작하는 앱", "acceptance": "[존재이유] 핵심",
                                       "interfaces": "계약 " * 300}))   # 1,500자 — GOAL.md도 기록됨
    f.current.collab_notes = "회의 합의 " * 120                          # ~600자 전문
    dossier_append(f, "MINUTES.md", "## 회의 — 주제\n[1R] 백엔드: 합의 전문")
    bodies = []

    async def wake(to, b, k):
        bodies.append(b)
        return "작업 진행 … 턴 한도 도달"        # 미완 → delivered 아님 → 다음 Work가 '재주입'(fresh)
    f.wake = wake
    return f, t, bodies


def test_B10_플래그_off면_기존동작_매번_전문_재동봉(tmp_path, monkeypatch):
    f, t, bodies = _delegation_flow(tmp_path, monkeypatch, flag=False)
    asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "이어서 완성"}))
    assert all("[팀 협의 기록(회의·표결)" in b for b in bodies)          # 둘 다 전문
    assert all("변경분 다이제스트" not in b for b in bodies)
    assert all("계약 계약" in b for b in bodies)                        # iface도 종전(1,500 전문)
    assert not f.collab_pushed                                          # off면 추적도 미기록


def test_B10_플래그_on_첫wake_전문_재주입은_다이제스트_참조(tmp_path, monkeypatch):
    """dedup 모델: 첫 wake엔 collab_notes *전문*(pull-risk 0%인 push 유지), 같은 세션 재주입부터
    기계 생성 다이제스트(회의록 꼬리+acceptance 원문) + MINUTES.md 참조. iface는 ≤400자+GOAL.md 참조."""
    f, t, bodies = _delegation_flow(tmp_path, monkeypatch, flag=True)
    asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    assert "[팀 협의 기록(회의·표결)" in bodies[0] and 12 in f.collab_pushed   # 첫 wake 전문
    asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "이어서 완성"}))
    b2 = bodies[1]
    assert "변경분 다이제스트" in b2 and "MINUTES.md 를 Read" in b2
    assert "[팀 협의 기록(회의·표결)" not in b2                          # 전문 재동봉 제거
    assert "[존재이유] 핵심" in b2                                       # acceptance는 원문 유지
    # iface: 관련 계약 ≤400자 + GOAL.md 참조(첫·재주입 공통)
    assert all("GOAL.md 를 Read" in b and "400자 안전망에서 잘림" in b for b in bodies)


def test_B10_문서가_없으면_플래그_on이어도_전문_push_안전폴백(tmp_path, monkeypatch):
    """참조가 가리킬 MINUTES.md가 없으면 dedup하지 않는다 — '표기만 있고 접근 불가' 금지."""
    monkeypatch.setenv("ORGANT_DOC_COLLAB", "1")
    g = FakeGuide()
    f = _flow3(g, tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.participated.add(12)
    asyncio.run(t["set_goal"].handler({"goal": "동작"}))
    f.current.collab_notes = "합의"
    f.collab_pushed.add(12)                       # 이미 push된 멤버라도
    bodies = []

    async def wake(to, b, k):
        bodies.append(b)
        return "작업 … 턴 한도 도달"
    f.wake = wake
    asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))
    assert "[팀 협의 기록(회의·표결)" in bodies[0]                       # MINUTES 부재 → 전문 폴백
    assert "변경분 다이제스트" not in bodies[0]


def test_B10_검증_루브릭은_플래그_on이어도_전문_유지(tmp_path, monkeypatch):
    """루브릭 참조화는 기각(A-3 — '게이트-메시지-only 0회 발동' 실측이 직접 주입의 근거)."""
    monkeypatch.setenv("ORGANT_DOC_COLLAB", "1")
    g = FakeGuide()
    f = _flow3(g, tmp_path)
    f.craft_of = lambda job: "루브릭전문 " * 100 if job == "백엔드" else ""   # ~600자
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    for m in (12, 13):
        f.current.participated.add(m)
    asyncio.run(t["set_goal"].handler({"goal": "동작"}))
    dossier_append(f, "MINUTES.md", "## 회의")
    f.current.collab_notes = "합의"
    bodies = []

    async def wake(to, b, k):
        bodies.append((to, b))
        f.act_count += 1
        f.act_by[to] = f.act_by.get(to, 0) + 1
        return "[결과] 완료"
    f.wake = wake
    asyncio.run(t["request"].handler({"to_id": "12", "kind": "Work", "body": "구현"}))   # owner 인도
    assert f.current.owner_delivered
    asyncio.run(t["request"].handler({"to_id": "13", "kind": "Work", "body": "검증해줘"}))  # 검증 위임
    verify_body = bodies[1][1]
    assert "산출물 품질 기준" in verify_body and "루브릭전문" in verify_body   # 전문 주입 유지


# ──────────────────────────── B-11 [Phase C] meet 축소·복구 문서 소스 ────────────────────────────

def test_B11_플래그_on_meet_R2는_전원압축_직전전문_MINUTES참조(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANT_DOC_COLLAB", "1")
    g = FakeGuide()
    f = _flow3(g, tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    long = "의견 " * 500                            # 발언 ~1,500자
    seen = []

    async def wake(to, b, k):
        seen.append((to, b))
        return f"{to}의 입장: {long}"
    f.wake = wake
    asyncio.run(t["meet"].handler({"topic": "T", "members": "", "rounds": "2"}))
    r2 = [b for _, b in seen if "2라운드" in b]
    assert r2 and all("[전원 1R 요지 — 시스템 압축]" in b for b in r2)     # 전원 가시성(200자 압축)
    assert all("[직전 발언 전문]" in b and "MINUTES.md" in b for b in r2)  # 직전 1발언 전문+참조
    assert all("12의 입장" in b and "13의 입장" in b for b in r2[-1:])     # 전원 발언자 노출
    # 종전 재방송(minutes[-8:] 1,500자 클립 원문 나열)은 없음 — 주입이 ~3K자로 축소
    assert all(len(b) < 6000 for b in r2)


def test_B11_플래그_off_meet_R2는_종전_재방송(tmp_path, monkeypatch):
    monkeypatch.delenv("ORGANT_DOC_COLLAB", raising=False)
    g = FakeGuide()
    f = _flow3(g, tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    seen = []

    async def wake(to, b, k):
        seen.append((to, b))
        return f"{to}의 입장"
    f.wake = wake
    asyncio.run(t["meet"].handler({"topic": "T", "members": "", "rounds": "2"}))
    r2 = [b for _, b in seen if "2라운드" in b]
    assert r2 and all("[1R]" in b and "[전원 1R 요지" not in b for b in r2)


def _mk_sys_restore(tmp_path, goal_doc=None, minutes=None):
    g = FakeGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L", 12: "M"},
            workspace=str(tmp_path))
    f = _flow(g)
    ws = os.path.join(str(tmp_path), "ws")
    os.makedirs(ws, exist_ok=True)
    f.workspace = ws
    tid = "120000-1"
    d = os.path.join(ws, ".collab", f"T-{tid}")
    os.makedirs(d, exist_ok=True)
    if goal_doc is not None:
        open(os.path.join(d, "GOAL.md"), "w", encoding="utf-8").write(goal_doc)
    if minutes is not None:
        open(os.path.join(d, "MINUTES.md"), "w", encoding="utf-8").write(minutes)
    snap = {"task_id": tid, "thread_id": "thr", "block_id": "blk",
            "purpose": "스냅퍼포스", "goal": "스냅골", "owner": 0, "owner_name": "",
            "team": [12], "collab_notes": "스냅협의록", "acceptance": "스냅수용",
            "dossier_path": f".collab/T-{tid}"}
    return s, f, {"id": "P-001", "open_task": snap}


_GOODDOC = ("# GOAL — Task 120000-1\n\n## Purpose\n문서목적\n\n## Goal\n문서골\n\n"
            "## Acceptance\n[존재이유] 문서수용\n\n## Standard\n\n\n## Interfaces\n\n")


def test_B11_복구_내용은_문서_우선_사실은_스냅샷(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANT_DOC_COLLAB", "1")
    s, f, proj = _mk_sys_restore(tmp_path, goal_doc=_GOODDOC, minutes="## 회의\n전문협의록")
    proj["open_task"]["owner_delivered"] = True                 # '사실' 필드
    asyncio.run(s._restore_open_task(f, proj))
    assert f.current.status.goal == "문서골" and f.current.status.purpose == "문서목적"
    assert f.current.acceptance == "[존재이유] 문서수용"
    assert "전문협의록" in f.current.collab_notes               # 협의록도 문서(무절단 원본)에서
    assert f.current.owner_delivered is True                    # 사실은 항상 스냅샷


def test_B11_문서_훼손시_스냅샷_폴백(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANT_DOC_COLLAB", "1")
    s, f, proj = _mk_sys_restore(tmp_path, goal_doc="깨진 문서 — 헤더 계약 없음")
    asyncio.run(s._restore_open_task(f, proj))
    assert f.current.status.goal == "스냅골"                    # 무결성 실패 → 스냅샷
    assert f.current.collab_notes == "스냅협의록"


def test_B11_플래그_off면_문서_있어도_스냅샷_전용_기존동작(tmp_path, monkeypatch):
    monkeypatch.delenv("ORGANT_DOC_COLLAB", raising=False)
    s, f, proj = _mk_sys_restore(tmp_path, goal_doc=_GOODDOC, minutes="## 회의\n전문협의록")
    asyncio.run(s._restore_open_task(f, proj))
    assert f.current.status.goal == "스냅골" and f.current.collab_notes == "스냅협의록"


def test_B11_구_스냅샷_dossier_path_없어도_복구_회귀없음(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANT_DOC_COLLAB", "1")
    s, f, proj = _mk_sys_restore(tmp_path)                      # 문서 자체가 없음
    del proj["open_task"]["dossier_path"]                       # 구 스냅샷(키 부재)
    asyncio.run(s._restore_open_task(f, proj))
    assert f.current.status.goal == "스냅골"


def test_B11_parse_goal_doc_왕복():
    sec = _parse_goal_doc(_GOODDOC)
    assert sec["Goal"] == "문서골" and sec["Purpose"] == "문서목적"
    assert sec["Acceptance"] == "[존재이유] 문서수용" and sec["Standard"] == ""
    assert _parse_goal_doc("아무 헤더 없음") == {}               # 무결성 실패 = 빈 dict


# ──────────────────────────── B-12 매체 조건부 clip ────────────────────────────

class _DocGuide(FakeGuide):
    """post_document 실구현 매체(murmur 흉내) — 전문을 받아 ref를 돌려준다."""
    async def post_document(self, channel_id, sender_id, title, body):
        self.calls.append(("doc", channel_id, sender_id, title, body))
        return "/api/docs/7/"


class _BrokenDocGuide(FakeGuide):
    """post_document가 죽는 매체(구 배포 백엔드 404 등) — clip 폴백을 검증."""
    async def post_document(self, channel_id, sender_id, title, body):
        raise RuntimeError("404")


def _speech_fixture(guide, tmp_path):
    f = _flow3(guide, tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12"}))
    f.current.thread_id = 700                                   # FakeGuide thread_id가 비수치라 지정
    return f


def test_B12_post_document_매체는_500자clip_전문ref(tmp_path):
    g = _DocGuide()
    f = _speech_fixture(g, tmp_path)
    full = "발언 " * 300                                         # ~900자
    asyncio.run(_say_speech(f, 12, "[회의 1R]", full))
    docs = [c for c in g.calls if c[0] == "doc"]
    posts = [c for c in g.calls if c[0] == "post"]
    assert docs and docs[0][4] == full                          # 전문이 문서로
    assert posts and "…[전문: /api/docs/7/]" in posts[-1][3]     # 채널엔 참조
    assert "500자 안전망에서 잘림" in posts[-1][3]                # clip은 '표기된' 500자(2026-07-03 200→500)


def test_B12_폴백_매체는_500자clip_참조문구_없음(tmp_path):
    for g in (FakeGuide(), _BrokenDocGuide()):                  # 미구현·실패 모두 폴백
        f = _speech_fixture(g, tmp_path)
        asyncio.run(_say_speech(f, 12, "[회의 1R]", "발언 " * 300))
        posts = [c for c in g.calls if c[0] == "post"]
        assert posts and "500자 안전망에서 잘림" in posts[-1][3]
        assert "[전문:" not in posts[-1][3]                     # 닿을 수 없는 참조 금지(A-10)


def test_B12_meet_채널발언이_매체조건부로_게시(tmp_path):
    g = _DocGuide()
    f = _flow3(g, tmp_path)
    t = _tools(f, 11, "leader")
    asyncio.run(t["create_task"].handler({"members": "12,13"}))
    f.current.thread_id = 700

    async def wake(to, b, k):
        return f"{to}의 발언 " + "근거 " * 300
    f.wake = wake
    asyncio.run(t["meet"].handler({"topic": "T", "members": "", "rounds": "1"}))
    posts = [c[3] for c in g.calls if c[0] == "post" and "[회의 1R]" in str(c[3])]
    assert posts and all("…[전문: /api/docs/7/]" in p for p in posts)
    assert len([c for c in g.calls if c[0] == "doc"]) == 2      # 발언자별 전문 문서
