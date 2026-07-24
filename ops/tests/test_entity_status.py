"""[관측 계약 v2] Flow log 봉투(pid·task 자동 부착)와 실황 미러(entity_status) 단위 검증.

설계: murmur/docs/MONITORING_REDESIGN_2026-07-10.md §5 — 봉투는 Flow의 소유(주입부 무접촉).
"""
import json
from types import SimpleNamespace

from system.flow import Flow
from system import entity_status
from system.protocol import Kind
from system.rule.backlog import BacklogRelay


def _mk_flow():
    return Flow(guide=None, channel_id=42, guild_id=1, leader_id=101,
                bot_info={101: "리더", 202: "백엔드"})


def test_envelope_pid_task_and_explicit_respected():
    f = _mk_flow()
    rec = []
    f.log = lambda event, **kw: rec.append((event, kw))
    f.log("hello", a=1)
    assert rec[-1][1]["pid"] == 42          # pid(채널) 자동 부착
    assert "task" not in rec[-1][1]         # current 없으면 task 미부착
    f.current = SimpleNamespace(task_id="T-9")
    f.log("hello2")
    assert rec[-1][1]["task"] == "T-9"      # 현재 태스크 자동 부착
    f.log("hello3", pid=7, task="X")
    assert rec[-1][1]["pid"] == 7 and rec[-1][1]["task"] == "X"   # 명시값 존중


def test_envelope_none_sink_and_rebind():
    f = _mk_flow()
    assert f.log is None                    # 초기값 None 보존(주입 전)
    calls = []
    f.log = lambda event, **kw: calls.append(event)
    f.log("a")
    assert calls == ["a"]


def test_mirror_stack_states_and_done(tmp_path, monkeypatch):
    d = tmp_path / "ops" / "var" / "organt_sns_state"
    d.mkdir(parents=True)
    monkeypatch.setenv("ORGANT_PJT", str(tmp_path))
    f = _mk_flow()
    f.log = lambda event, **kw: None
    f.start_root("root-1")                       # ORIGIN→리더
    f.comm.request(101, 202, "r2", Kind.WORK)    # 리더→백엔드 (스택 2, alive=202)
    f.log("req_sent", frm=101, to=202)           # FORCE 이벤트 → 스로틀 무시 즉시 미러
    data = json.loads((d / "entity_status.json").read_text(encoding="utf-8"))
    pj = data["projects"]["42"]
    assert pj["active"] == 202
    assert len(pj["stack"]) == 2
    # 💭 실황 생각 — activity_log 꼬리가 working 봇과 프로젝트에 실린다
    f.note_activity(202, "💭 회귀 스위트부터 돌려 보자")
    f.log("req_rejected", frm=1, to=2)               # FORCE 이벤트로 재미러
    data = json.loads((d / "entity_status.json").read_text(encoding="utf-8"))
    assert any("회귀 스위트" in a["t"] for a in data["projects"]["42"]["activity"])
    assert any("회귀 스위트" in a["t"] for a in data["organts"]["202"]["activity"])
    assert pj["stack"][-1]["since"] > 0          # Frame.ts(관측 v2)
    assert data["organts"]["202"]["state"] == "working"
    assert data["organts"]["101"]["state"] == "waiting"
    assert data["organts"]["101"]["waiting_on"] == 202
    # 전부 응답 → 흐름 종료 → 프로젝트·소속 organt가 미러에서 사라진다(유휴 복귀)
    f.comm.respond(202)
    f.comm.respond(101)
    f.log("flow_done")
    data = json.loads((d / "entity_status.json").read_text(encoding="utf-8"))
    assert "42" not in data["projects"]
    assert "202" not in data["organts"] and "101" not in data["organts"]


def test_mirror_noop_without_pjt(monkeypatch):
    monkeypatch.delenv("ORGANT_PJT", raising=False)
    f = _mk_flow()
    f.log = lambda event, **kw: None
    f.start_root("r")
    f.log("req_sent")                            # ORGANT_PJT 없음 — 무동작(예외 없이)


def test_작업생각은_현재수행자의_유일한_백로그에만_귀속(monkeypatch):
    monkeypatch.delenv("ORGANT_PJT", raising=False)
    f = _mk_flow()
    r1, r2 = BacklogRelay("ST-1"), BacklogRelay("ST-2")
    b1 = r1.submit(101, "화면 구현")
    b2 = r2.submit(101, "API 구현")
    r1.pick(101, b1.backlog_id, 202)
    f.backlog_relays = {"ST-1": r1, "ST-2": r2}

    f.note_activity(202, "💭 컴포넌트 구조를 확인한다")
    assert b1.activity == ["[백엔드] 💭 컴포넌트 구조를 확인한다"]
    assert b2.activity == []

    # 수행자가 실제로 쥔 일감이 없으면 전역 실황에는 남되 임의 백로그에 추측 귀속하지 않는다.
    f.note_activity(101, "💭 다음 순서를 살핀다")
    assert len(f.activity_log) == 2
    assert b1.activity == ["[백엔드] 💭 컴포넌트 구조를 확인한다"]
