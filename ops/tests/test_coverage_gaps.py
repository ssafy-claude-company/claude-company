"""커버리지 갭 보강 — 스위트가 놓친 순수 분기들만 겨냥(기존 test_*.py와 비중첩).

대상(coverage -m 실측): protocol 109·117 / project 64-66 / task 151·160-187 /
communication 188·201·236·263·275·307·311 / permissions 26-27·99-100·251-255 /
_util 12·23 / config 35-36. 분기당 1~3줄 케이스.
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import system._util as _util
from system.permissions import _within, make_pre_tool_use_hook, organt_allowed_tools
from system.protocol import _multiline_body, parse
from system.rule.communication import CommError, CommunicationManager, Frame
from system.rule.project import deploy_service_name
from system.rule.task import _has_real_dataset, _synthesizes_data


# ── protocol.py 109·117 ──────────────────────────────────────────────

def test_multiline_body_Body줄_없으면_빈문자열():
    assert _multiline_body("To: x") == ""


def test_parse_빈내용은_None():
    assert parse(message_id=1, author_id=2, mention_ids=[], reply_to_id=None,
                 content="") is None
    assert parse(message_id=1, author_id=2, mention_ids=[], reply_to_id=None,
                 content="   ") is None


# ── project.py 62-66 (deploy_service_name 이름 슬러그 폴백) ─────────────

def test_deploy_service_name_pid없고_이름있으면_슬러그():
    flow = SimpleNamespace(project_name="My App", project_id=None)
    assert deploy_service_name(flow) == "organt-my-app"


def test_deploy_service_name_슬러그가_비면_빈슬롯():
    flow = SimpleNamespace(project_name="!!!", project_id=None)
    assert deploy_service_name(flow) == ""


# ── task.py 151·158-163 (_has_real_dataset) ──────────────────────────

def test_has_real_dataset_workspace_없으면_False():
    assert _has_real_dataset(None) is False
    assert _has_real_dataset("") is False


def test_has_real_dataset_2048바이트_경계(tmp_path):
    stub = tmp_path / "stub.csv"
    stub.write_bytes(b"x" * 2048)           # 경계 자체(>2048 아님)는 증거 아님
    assert _has_real_dataset(tmp_path) is False
    stub.write_bytes(b"x" * 2049)
    assert _has_real_dataset(tmp_path) is True


def test_has_real_dataset_getsize_OSError면_True(tmp_path, monkeypatch):
    (tmp_path / "data.csv").write_bytes(b"a,b\n")
    import os
    def _boom(path):
        raise OSError("stat 실패")
    monkeypatch.setattr(os.path, "getsize", _boom)
    assert _has_real_dataset(tmp_path) is True


def test_has_real_dataset_walk_예외면_False(tmp_path, monkeypatch):
    import os
    def _boom(path):
        raise RuntimeError("walk 실패")
    monkeypatch.setattr(os, "walk", _boom)
    assert _has_real_dataset(tmp_path) is False


# ── task.py 171·177·180-187 (_synthesizes_data) ──────────────────────

def test_synthesizes_data_workspace_없으면_None():
    assert _synthesizes_data(None) is None
    assert _synthesizes_data("") is None


def test_synthesizes_data_코드확장자_아니면_무시(tmp_path):
    (tmp_path / "notes.txt").write_text("synthetic data 설명", encoding="utf-8")
    assert _synthesizes_data(tmp_path) is None


def test_synthesizes_data_열기_OSError면_건너뜀(tmp_path):
    import os
    os.symlink(str(tmp_path / "없는파일"), str(tmp_path / "broken.py"))  # 깨진 링크 → open OSError
    assert _synthesizes_data(tmp_path) is None


def test_synthesizes_data_마커_발견(tmp_path):
    (tmp_path / "gen.py").write_text("def generate_data(): pass", encoding="utf-8")
    assert _synthesizes_data(tmp_path) == ("gen.py", "generate_data")


def test_synthesizes_data_walk_예외면_None(tmp_path, monkeypatch):
    import os
    def _boom(path):
        raise RuntimeError("walk 실패")
    monkeypatch.setattr(os, "walk", _boom)
    assert _synthesizes_data(tmp_path) is None


# ── communication.py 188·201·236·263·275·307·311 ─────────────────────

def test_참여자에게_Work재요청_거부():
    comm = CommunicationManager(1)
    # 부팅 복구처럼 복원된(비-LIFO) 스택: 2는 참여자이지만 상류 위임자(ancestor)는 아님
    comm._stack.append(Frame(1, 2, "r1", "work"))
    comm._stack.append(Frame(1, 3, "r2", "work"))
    comm.alive = 3
    with pytest.raises(CommError, match="미완 Work"):
        comm.check_request(3, 2, "work")


def _done_comm():
    comm = CommunicationManager(1)
    comm.request(1, 2, "r1", kind="work")
    comm.respond(2, "accept")
    assert comm.done
    return comm


def test_종료된_흐름_respond_거부():
    comm = _done_comm()
    with pytest.raises(CommError, match="이미 종료"):
        comm.respond(2)


def test_direct_delegator_없으면_None():
    comm = CommunicationManager(1)
    assert comm.direct_delegator(2) is None          # 빈 스택
    comm.request(1, 2, "r1", kind="work")
    assert comm.direct_delegator(1) is None          # top의 수신자가 아님
    assert comm.direct_delegator(2) == 1


def test_redo_비활성_Organt_거부():
    comm = CommunicationManager(1)
    with pytest.raises(CommError, match="활성 Organt만 redo"):
        comm.redo(2, 3, "r1")


def test_종료된_흐름_escalate_거부():
    comm = _done_comm()
    with pytest.raises(CommError, match="이미 종료"):
        comm.escalate("사유")


def test_종료된_흐름_report_up_to_거부():
    comm = _done_comm()
    with pytest.raises(CommError, match="이미 종료"):
        comm.report_up_to(1, 2)


def test_report_up_to_자기자신_거부():
    comm = CommunicationManager(1)
    with pytest.raises(CommError, match="자기 자신"):
        comm.report_up_to(1, 1)


# ── permissions.py 26-27·99-100·251-255 ──────────────────────────────

ALLOWED = organt_allowed_tools([])


class _Audit:
    def __init__(self):
        self.records = []

    def record(self, event, **fields):
        self.records.append((event, fields))
        return {}


def _run(hook, tool_name, tool_input=None, cwd="/ws"):
    return asyncio.run(hook({
        "tool_name": tool_name, "tool_input": tool_input or {}, "cwd": cwd,
    }, "tu_gap", None))


def test_within_경로_ValueError면_False():
    assert _within("/ws", "bad\x00path") is False    # 널바이트 → realpath ValueError


def test_last_activity_설정실패는_삼켜짐():
    class _FrozenFlow:
        @property
        def last_activity(self):                     # setter 없음 → 대입 시 AttributeError
            return 0.0
    hook = make_pre_tool_use_hook(_Audit(), ALLOWED, flow=_FrozenFlow())
    assert _run(hook, "Read", {"file_path": "a.txt"}) == {}


class _AbsorbFlow:
    """리더 흡수 게이트(#8)까지 도달하는 최소 flow — 점유 장부 예외/바쁨 분기 겨냥."""
    def __init__(self, engagement):
        self.comm = CommunicationManager(11)
        self.comm.attach_engagement(engagement, "scope-1")
        self.leader = 11
        self.current = SimpleNamespace(owner=0, team=[])
        self.act_by = {11: 9}                        # grace(8) 초과 + 팀 합(0) 초과
        self.project_team = [22]
        self.intervention = None

    def _info(self, m):
        return ""


def test_리더흡수_동료가_타흐름_점유중이면_통과():
    class _BusyEng:
        def busy_elsewhere(self, m, scope):
            return True                              # 전원 타 흐름 → 도달 가능자 없음 → 차단 안 함
    a = _Audit()
    hook = make_pre_tool_use_hook(a, ALLOWED, actor=11, flow=_AbsorbFlow(_BusyEng()))
    assert _run(hook, "Write", {"file_path": "a.txt"}) == {}
    assert a.records == []


def test_리더흡수_점유조회_예외는_도달가능_취급되어_차단():
    class _BoomEng:
        def busy_elsewhere(self, m, scope):
            raise RuntimeError("장부 조회 실패")
    a = _Audit()
    hook = make_pre_tool_use_hook(a, ALLOWED, actor=11, flow=_AbsorbFlow(_BoomEng()))
    out = _run(hook, "Write", {"file_path": "a.txt"})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert a.records[0][1]["reason"] == "리더 흡수(팀 합보다 많이 doing)"


# ── _util.py 12·23 ────────────────────────────────────────────────────

def test_dbg_디버그_켜지면_출력(monkeypatch, capsys):
    monkeypatch.setattr(_util, "_DEBUG", True)
    _util._dbg("진단 메시지")
    assert "진단 메시지" in capsys.readouterr().out


def test_react_guide에_react_있으면_호출():
    calls = []

    class _G:
        async def react(self, channel_id, message_id, emoji):
            calls.append((channel_id, message_id, emoji))

    asyncio.run(_util._react(_G(), 10, 20, "✅"))
    assert calls == [(10, 20, "✅")]


# ── config.py 35-36 (dotenv 부재 ImportError 무시) ─────────────────────

def test_dotenv_없어도_환경변수로_로딩(monkeypatch):
    import system.config as config
    monkeypatch.setattr(config, "ROOT",
                        Path(tempfile.mkdtemp(prefix="organt-gap-config-")))
    for key in ("SYSTEM_BOT", "CHANNEL_ID", "ORGANT_MODEL", "ORGANT_WORKSPACE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SYSTEM_BOT", "tok")
    monkeypatch.setenv("CHANNEL_ID", "42")
    monkeypatch.setitem(sys.modules, "dotenv", None)   # import 시 ImportError 강제
    cfg = config.load_config()
    assert cfg.channel_id == 42 and cfg.model is None


# ── 위임 자동합류 직군 계열 dedup (communication._req_gate_team) ─────────

def test_위임_자동합류_같은직군계열_차단():
    """[위임 계열 dedup(2026-07-14, 사용자: '서버 채널엔 10명인데 Task는 11명, 기획+게임기획자')]
    선거는 계열당 1명(기획⊂게임기획자)을 뽑는데, 위임 자동합류가 이를 우회해 같은 계열을 되불렀다
    (ch69 라이브: 게임기획자 팀에 일반 기획이 위임 합류 → 11명). 자동합류 전 현 팀에 같은 계열이
    있으면 거부·리다이렉트(선거와 동일한 정규화 부분문자열 판정)."""
    from system.rule.communication import _req_gate_team
    roles = {11: "게임 기획자", 12: "백엔드", 33: "기획", 40: "배포/인프라"}

    class _G:
        async def post(self, *a):
            pass

    class _F:
        def __init__(self):
            self.current = SimpleNamespace(team=[11, 12], status=SimpleNamespace(group=""), thread_id=1)
            self.project_team = [11, 12, 33, 40]
            self.pool = [11, 12, 33, 40]
            self.guide = _G()

        def _info(self, m):
            return roles.get(m, "")

        async def refresh(self, *a):
            pass

    f = _F()
    # 리더(게임 기획자)가 일반 기획(33)에게 위임 → 같은 계열이라 거부, 팀에 안 들어감
    out = asyncio.run(_req_gate_team(f, 11, 33, "tag"))
    assert out is not None and "같은 직군 계열" in out and "게임 기획자" in out
    assert 33 not in f.current.team

    # 대조: 새 계열(배포/인프라)은 정상 자동합류(과차단 아님)
    out2 = asyncio.run(_req_gate_team(f, 11, 40, "tag"))
    assert out2 is None and 40 in f.current.team


# ── [보안 감사(2026-07-18)] Read/Glob 작업공간 경계 ──────────────────────────────
def test_Read는_작업공간_밖_비밀_거부():
    a = _Audit()
    hook = make_pre_tool_use_hook(a, ALLOWED, actor=12, role="member")
    for p in ("/etc/murmur-web.env", "/root/.ssh/id_rsa", "/proc/self/environ", "../../etc/passwd"):
        out = _run(hook, "Read", {"file_path": p}, cwd="/ws")
        assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", p
    assert any(r[1].get("reason") == "작업공간 밖 읽기/탐색" for r in a.records)


def test_Read_Glob_작업공간_안은_허용():
    hook = make_pre_tool_use_hook(_Audit(), ALLOWED, actor=12, role="member")
    assert _run(hook, "Read", {"file_path": "src/app.py"}, cwd="/ws") == {}
    assert _run(hook, "Read", {"file_path": "/ws/GOAL.md"}, cwd="/ws") == {}
    assert _run(hook, "Glob", {"pattern": "**/*.py"}, cwd="/ws") == {}          # path 없음 = cwd
    assert _run(hook, "Glob", {"pattern": "*", "path": "/etc"}, cwd="/ws")\
        .get("hookSpecificOutput", {}).get("permissionDecision") == "deny"       # 밖 탐색 거부
