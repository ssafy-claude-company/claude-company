"""기능4 검증: Organt 본체 옵션 구성 (네트워크/LLM 없이 구조만 확인).

실제 LLM 파일 생성은 라이브 데모(scripts/demo)로 실측한다.
"""
from pathlib import Path

from system.config import Config
from organt.organt import Organt, _is_transient_api_error, _strip_decoration, build_options


def _cfg(model=None) -> Config:
    return Config(
        system_bot_token="s", channel_id=1,
        model=model, workspace_dir=Path("/tmp/ws"),
        audit_log_path=Path("/tmp/audit.jsonl"),
    )


def test_옵션_작업공간이_cwd():
    assert build_options(_cfg()).cwd == "/tmp/ws"


def test_옵션_파일툴_허용():
    allowed = set(build_options(_cfg()).allowed_tools)
    assert {"Read", "Write", "Edit"}.issubset(allowed)


def test_옵션_권한모드_파일쓰기가능():
    assert build_options(_cfg()).permission_mode == "acceptEdits"


def test_옵션_모델_config반영():
    assert build_options(_cfg(model="opus")).model == "opus"
    assert build_options(_cfg(model=None)).model is None


def test_옵션_override_주입():
    # 기능5·6에서 mcp_servers/hooks/allowed_tools를 주입하는 경로.
    opts = build_options(_cfg(), allowed_tools=["Read"], max_turns=3)
    assert opts.allowed_tools == ["Read"]
    assert opts.max_turns == 3


def test_organt_기본옵션_인격_CLAUDEmd():
    sp = Organt(_cfg()).options.system_prompt
    assert isinstance(sp, str) and "Organt" in sp


def test_Codex세션은_rollout저장소에서_재개판정(monkeypatch, tmp_path):
    """GPT sid를 Claude 저장소에서 찾다 매 턴 폐기하지 않고 Codex 날짜별 rollout을 인식한다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sid = "019f9e5d-40f6-7380-bc64-c71b628d69a4"
    rollout = (
        tmp_path / ".codex" / "sessions" / "2026" / "07" / "26"
        / f"rollout-2026-07-26T12-00-00-{sid}.jsonl"
    )
    rollout.parent.mkdir(parents=True)
    rollout.write_text("{}\n", encoding="utf-8")
    o = Organt(_cfg())
    o._codex_model = "gpt-test"
    o.session_id = sid

    assert o._session_in_store() is True
    assert o.will_resume() is True


def test_Codex_e2e는_작업공간을_readonly로_격리(monkeypatch, tmp_path):
    """전용 E2E 턴의 native shell은 ro-bind이고, 쓰기는 부모 guide run에만 남는다."""
    import asyncio
    from organt import codex_mcp_bridge as bridge_mod

    captured = {}

    async def fake_process(**kwargs):
        captured.update(kwargs)
        return "ok", "sid"

    monkeypatch.setattr(bridge_mod, "_run_codex_process", fake_process)
    assert asyncio.run(bridge_mod.run_codex_turn(
        prompt="e2e", cwd=str(tmp_path), session_id=None, tools=[],
        model="gpt-test", read_only=True,
    )) == ("ok", "sid")
    assert captured["read_only"] is True

    args = bridge_mod._bwrap_args(str(tmp_path), read_only=True)
    mount = args.index(str(tmp_path))
    assert args[mount - 1] == "--ro-bind" and args[mount + 1] == str(tmp_path)


def test_일시적_API오류_판별():
    assert _is_transient_api_error("API Error: 529 Overloaded. ...") is True
    assert _is_transient_api_error("API Error: 429 rate_limit") is True
    assert _is_transient_api_error("API Error: Stream closed") is True          # 제어 스트림 닫힘 = 일시(재시도)
    assert _is_transient_api_error("API Error: process exited") is True
    assert _is_transient_api_error("API Error: 400 invalid request") is False   # 영구 오류는 재시도 안 함
    assert _is_transient_api_error("백엔드 완성했습니다") is False               # 정상 응답


def test_빈응답_무응답은_재시도(monkeypatch):
    """서브프로세스가 발화 없이 조용히 죽어 빈 응답('')이 오면 handle이 resume 재시도 → 다음 시도에 응답이
    오면 그걸 반환한다. (동료가 '무응답'으로 보여 리더가 충원·재처리로 churn하던 silent-failure 경로 차단.)"""
    import asyncio
    o = Organt(_cfg())
    calls = {"n": 0}

    async def fake_run_once(prompt):
        calls["n"] += 1
        return ("", None) if calls["n"] == 1 else ("서버 구현 완료", None)  # 1차 빈 응답 → 2차 성공

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(o, "_run_once", fake_run_once)
    monkeypatch.setattr("organt.organt.asyncio.sleep", _no_sleep)   # 백오프 대기 제거(빠른 테스트)
    out = asyncio.run(o.handle("서버 만들어줘"))
    assert out == "서버 구현 완료" and calls["n"] == 2           # 빈 응답 후 재시도해 성공


def test_보고_장식수평선_제거():
    # '---' 같은 장식 수평선만 제거하고 내용은 보존
    out = _strip_decoration("백엔드 완료\n---\n프론트 연동됨")
    assert out == "백엔드 완료\n프론트 연동됨"
    assert _strip_decoration("결과만\n***\n___") == "결과만"


def test_메시지수신마다_하트비트_on_activity(monkeypatch):
    """_run_once가 메시지를 받을 때마다 on_activity를 호출한다 — 도구 호출이 없는 긴 모델 생성
    (거대 파일 단일 Write 직전의 장문 작성)이 침묵 워치독에 '행'으로 오인되지 않게, 도구 훅
    (Pre/Post) 사이 사각을 메시지 단위 하트비트로 메운다."""
    import asyncio
    beats = {"n": 0}
    o = Organt(_cfg(), on_activity=lambda: beats.__setitem__("n", beats["n"] + 1))

    class _FakeClient:
        def __init__(self, options):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def query(self, prompt):
            pass

        async def receive_response(self):
            for _ in range(3):
                yield object()   # 메시지 3건 — 타입 무관, 수신 자체가 활동 신호

    monkeypatch.setattr("organt.organt.ClaudeSDKClient", _FakeClient)
    asyncio.run(o._run_once("p"))
    assert beats["n"] == 3


def test_Codex턴취소는_프로세스그룹까지_회수(monkeypatch, tmp_path):
    """Task 중지로 상위 await가 취소되면 codex/bwrap 자식이 뒤에서 계속 작업하지 않는다."""
    import asyncio
    import signal
    import pytest
    from organt import codex_mcp_bridge as bridge_mod

    entered = asyncio.Event()

    class _Stdin:
        def write(self, _data):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

    class _Stdout:
        def __aiter__(self):
            return self

        async def __anext__(self):
            entered.set()
            await asyncio.Future()

    class _Stderr:
        async def read(self, _size=-1):
            await proc.exited.wait()
            return b""

    class _Proc:
        pid = 424242

        def __init__(self):
            self.returncode = None
            self.stdin, self.stdout, self.stderr = _Stdin(), _Stdout(), _Stderr()
            self.exited = asyncio.Event()

        async def wait(self):
            await self.exited.wait()
            return self.returncode

    proc = _Proc()
    spawn_kw = {}
    signals = []

    async def fake_spawn(*_args, **kwargs):
        spawn_kw.update(kwargs)
        return proc

    def fake_killpg(pid, sig):
        signals.append((pid, sig))
        proc.returncode = -int(sig)
        proc.exited.set()

    monkeypatch.setattr(bridge_mod.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(bridge_mod.os, "killpg", fake_killpg)

    async def scenario():
        task = asyncio.create_task(bridge_mod.run_codex_turn(
            prompt="오래 작업", cwd=str(tmp_path), session_id=None, tools=[],
            model="gpt-test"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert spawn_kw["start_new_session"] is True
    assert signals == [(proc.pid, signal.SIGTERM)]
    assert proc.returncode == -signal.SIGTERM


def test_Codex_JSON단일이벤트_64KiB초과도_온전히수신(monkeypatch, tmp_path):
    """Codex --json의 한 이벤트가 asyncio 기본 64KiB를 넘어도 정상 agent_message를 잃지 않는다."""
    import asyncio
    import sys
    from organt import codex_mcp_bridge as bridge_mod

    real_spawn = asyncio.create_subprocess_exec
    spawn_kw = {}
    script = (
        "import json,sys\n"
        "sys.stdin.buffer.read()\n"
        "print(json.dumps({'type':'thread.started','thread_id':'sid-large'}))\n"
        "print(json.dumps({'type':'item.completed','item':"
        "{'type':'agent_message','text':'가'*100000}}, ensure_ascii=False))\n"
    )

    async def fake_spawn(*_args, **kwargs):
        spawn_kw.update(kwargs)
        return await real_spawn(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr(bridge_mod.asyncio, "create_subprocess_exec", fake_spawn)
    text, sid = asyncio.run(bridge_mod.run_codex_turn(
        prompt="큰 응답", cwd=str(tmp_path), session_id=None, tools=[],
        model="gpt-test"))

    assert sid == "sid-large"
    assert text == "가" * 100000
    assert spawn_kw["limit"] > 64 * 1024


def test_Codex_JSON이_설정상한초과하면_실명오류와_프로세스회수(monkeypatch, tmp_path):
    """무개행 이벤트는 무제한 버퍼링하지 않는다 — 운영 상한을 넘으면 조용한 유실 대신 명시 오류."""
    import asyncio
    import sys
    import pytest
    from organt import codex_mcp_bridge as bridge_mod

    real_spawn = asyncio.create_subprocess_exec
    seen = {}
    script = (
        "import sys,time\n"
        "sys.stdin.buffer.read()\n"
        "sys.stdout.write('x'*100000)\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )

    async def fake_spawn(*_args, **kwargs):
        proc = await real_spawn(sys.executable, "-c", script, **kwargs)
        seen["proc"] = proc
        return proc

    monkeypatch.setenv("ORGANT_CODEX_STREAM_LIMIT", str(64 * 1024))
    monkeypatch.setattr(bridge_mod.asyncio, "create_subprocess_exec", fake_spawn)
    with pytest.raises(RuntimeError, match="스트림 안전 한계"):
        asyncio.run(bridge_mod.run_codex_turn(
            prompt="상한 검사", cwd=str(tmp_path), session_id=None, tools=[],
            model="gpt-test"))
    assert seen["proc"].returncode is not None


def test_Codex_포트lease는_AB격리_C대기_턴마다새bridge(monkeypatch, tmp_path):
    """동시 substantive 턴은 각자 bridge/tools를 갖고, pool 초과 턴만 포트 반환까지 기다린다."""
    import asyncio
    from types import SimpleNamespace
    from organt import codex_mcp_bridge as bridge_mod

    pool = bridge_mod.CodexBridgePortPool(base_port=23000, size=2)
    instances = []
    active = {}
    entered = {name: asyncio.Event() for name in ("A", "B", "C")}
    release = {name: asyncio.Event() for name in ("A", "B", "C")}
    observed = {}

    class _Bridge:
        def __init__(self, port):
            self.port = port
            self.url = f"http://127.0.0.1:{port}/mcp"
            self.tools = []
            self.started = False
            self.stopped = False
            instances.append(self)

        def set_tools(self, tools):
            self.tools = list(tools or [])

        async def start(self):
            self.started = True
            active[self.port] = self

        async def stop(self):
            self.stopped = True
            if active.get(self.port) is self:
                del active[self.port]

    async def fake_process(**kwargs):
        name = kwargs["prompt"]
        port = int(kwargs["mcp_url"].split(":")[-1].split("/")[0])
        bridge = active[port]
        observed[name] = {
            "port": port,
            "tools": [tool.name for tool in bridge.tools],
            "bridge": id(bridge),
        }
        entered[name].set()
        await release[name].wait()
        return f"done-{name}", f"sid-{name}"

    monkeypatch.setattr(bridge_mod, "get_port_pool", lambda: pool)
    monkeypatch.setattr(bridge_mod, "_new_bridge", _Bridge)
    monkeypatch.setattr(bridge_mod, "_run_codex_process", fake_process)

    async def scenario():
        def turn(name):
            return bridge_mod.run_codex_turn(
                prompt=name,
                cwd=str(tmp_path),
                session_id=None,
                tools=[SimpleNamespace(name=f"tool-{name}")],
                model="gpt-test",
            )

        task_a = asyncio.create_task(turn("A"))
        task_b = asyncio.create_task(turn("B"))
        await asyncio.gather(entered["A"].wait(), entered["B"].wait())
        task_c = asyncio.create_task(turn("C"))
        await asyncio.sleep(0)
        assert not entered["C"].is_set()

        release["A"].set()
        await entered["C"].wait()
        release["B"].set()
        release["C"].set()
        return await asyncio.gather(task_a, task_b, task_c)

    results = asyncio.run(scenario())
    assert results == [
        ("done-A", "sid-A"),
        ("done-B", "sid-B"),
        ("done-C", "sid-C"),
    ]
    assert observed["A"]["port"] != observed["B"]["port"]
    assert observed["C"]["port"] == observed["A"]["port"]
    assert observed["C"]["bridge"] != observed["A"]["bridge"]
    assert observed["A"]["tools"] == ["tool-A"]
    assert observed["B"]["tools"] == ["tool-B"]
    assert observed["C"]["tools"] == ["tool-C"]
    assert pool.available == 2
    assert not active
    assert len(instances) == 3
    assert all(bridge.stopped and bridge.tools == [] for bridge in instances)


def test_Codex_무도구micro는_MCP와_portlease를_건너뜀(monkeypatch, tmp_path):
    from organt import codex_mcp_bridge as bridge_mod

    async def fake_process(**kwargs):
        assert kwargs["mcp_url"] is None
        return "micro-ok", "sid-micro"

    def forbidden_pool():
        raise AssertionError("micro 턴이 port pool을 건드렸습니다.")

    monkeypatch.setattr(bridge_mod, "get_port_pool", forbidden_pool)
    monkeypatch.setattr(bridge_mod, "_run_codex_process", fake_process)
    result = __import__("asyncio").run(bridge_mod.run_codex_turn(
        prompt="micro",
        cwd=str(tmp_path),
        session_id=None,
        tools=[],
        model="gpt-test",
    ))
    assert result == ("micro-ok", "sid-micro")


def test_Codex_bridge는_완전JSONschema와_receipt_run을_보존(monkeypatch, tmp_path):
    """한 full-schema 도구가 tools/list 전체를 죽이지 않고, e2e receipt 발급용 run도 숨기지 않는다."""
    import asyncio
    from types import SimpleNamespace
    from organt import codex_mcp_bridge as bridge_mod

    full_schema = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["status", "register"]},
            "payload": {"type": "object", "additionalProperties": True},
        },
        "required": ["op"],
    }
    converted = bridge_mod._json_schema(full_schema)
    assert converted == full_schema
    assert converted["required"] == ["op"]
    assert converted["properties"]["op"]["enum"] == ["status", "register"]
    assert bridge_mod._json_schema({
        "properties": {"value": {"type": "integer"}},
    }) == {"properties": {"value": {"type": "integer"}}}
    assert bridge_mod._json_schema({
        "anyOf": [{"type": "string"}, {"type": "null"}],
    }) == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert bridge_mod._json_schema({
        "type": "object", "additionalProperties": False,
    }) == {"type": "object", "additionalProperties": False}
    assert bridge_mod._json_schema({"command": str}) == {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": [],
    }

    class _Bridge:
        def __init__(self, _port):
            self.url = "http://127.0.0.1:23210/mcp"
            self.tools = []

        def set_tools(self, tools):
            self.tools = list(tools or [])

        async def start(self):
            pass

        async def stop(self):
            pass

    bridge_box = {}

    def new_bridge(port):
        bridge_box["value"] = _Bridge(port)
        return bridge_box["value"]

    expected_names = {"value": ["atelier", "run", "e2e_open"]}

    async def fake_process(**_kwargs):
        names = [tool.name for tool in bridge_box["value"].tools]
        assert names == expected_names["value"]
        return "ok", "sid"

    pool = bridge_mod.CodexBridgePortPool(base_port=23210, size=1)
    monkeypatch.setattr(bridge_mod, "get_port_pool", lambda: pool)
    monkeypatch.setattr(bridge_mod, "_new_bridge", new_bridge)
    monkeypatch.setattr(bridge_mod, "_run_codex_process", fake_process)
    tools = [
        SimpleNamespace(name="atelier", input_schema=full_schema),
        SimpleNamespace(name="run", input_schema={"command": str}),
        SimpleNamespace(name="e2e_open", input_schema={}),
    ]
    assert asyncio.run(bridge_mod.run_codex_turn(
        prompt="e2e", cwd=str(tmp_path), session_id=None, tools=tools,
        model="gpt-test",
    )) == ("ok", "sid")
    assert pool.available == 1

    # milestone capability가 없는 일반 협업 도구셋에서는 native shell과 겹치는 Guide run을 숨긴다.
    expected_names["value"] = ["atelier"]
    assert asyncio.run(bridge_mod.run_codex_turn(
        prompt="collab", cwd=str(tmp_path), session_id=None,
        tools=[
            SimpleNamespace(name="atelier", input_schema=full_schema),
            SimpleNamespace(name="run", input_schema={"command": str}),
        ],
        model="gpt-test",
    )) == ("ok", "sid")
    assert pool.available == 1

    # e2e_result도 같은 milestone capability 표식이다.
    expected_names["value"] = ["run", "e2e_result"]
    assert asyncio.run(bridge_mod.run_codex_turn(
        prompt="result", cwd=str(tmp_path), session_id=None,
        tools=[
            SimpleNamespace(name="run", input_schema={"command": str}),
            SimpleNamespace(name="e2e_result", input_schema={"item": str}),
        ],
        model="gpt-test",
    )) == ("ok", "sid")
    assert pool.available == 1

    async def fake_native_process(**kwargs):
        assert kwargs["mcp_url"] is None
        return "native", "sid-native"

    monkeypatch.setattr(bridge_mod, "_run_codex_process", fake_native_process)
    assert asyncio.run(bridge_mod.run_codex_turn(
        prompt="casual", cwd=str(tmp_path), session_id=None,
        tools=[SimpleNamespace(name="run", input_schema={"command": str})],
        model="gpt-test",
    )) == ("native", "sid-native")
    assert pool.available == 1


def test_Codex_substantive비정상도_bridge정지_clear_port반환(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    import pytest
    from organt import codex_mcp_bridge as bridge_mod

    pool = bridge_mod.CodexBridgePortPool(base_port=23100, size=1)
    instances = []
    calls = {"n": 0}

    class _Bridge:
        def __init__(self, port):
            self.port = port
            self.url = f"http://127.0.0.1:{port}/mcp"
            self.tools = []
            self.stopped = False
            instances.append(self)

        def set_tools(self, tools):
            self.tools = list(tools or [])

        async def start(self):
            pass

        async def stop(self):
            self.stopped = True

    async def fake_process(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("subprocess failed")
        return "recovered", "sid-new"

    monkeypatch.setattr(bridge_mod, "get_port_pool", lambda: pool)
    monkeypatch.setattr(bridge_mod, "_new_bridge", _Bridge)
    monkeypatch.setattr(bridge_mod, "_run_codex_process", fake_process)
    tool = SimpleNamespace(name="meet")

    with pytest.raises(RuntimeError, match="subprocess failed"):
        asyncio.run(bridge_mod.run_codex_turn(
            prompt="first", cwd=str(tmp_path), session_id=None,
            tools=[tool], model="gpt-test"))
    assert pool.available == 1
    assert instances[0].stopped and instances[0].tools == []

    result = asyncio.run(bridge_mod.run_codex_turn(
        prompt="second", cwd=str(tmp_path), session_id=None,
        tools=[tool], model="gpt-test"))
    assert result == ("recovered", "sid-new")
    assert len(instances) == 2
    assert instances[0] is not instances[1]
    assert instances[0].port == instances[1].port == 23100
    assert instances[1].stopped and instances[1].tools == []
    assert pool.available == 1


def test_Codex_substantive취소도_bridge정지후_port반환(monkeypatch, tmp_path):
    """사용자 중지가 subprocess 턴을 취소해도 MCP 서버가 내려가기 전에 lease를 재사용하지 않는다."""
    import asyncio
    from types import SimpleNamespace
    import pytest
    from organt import codex_mcp_bridge as bridge_mod

    pool = bridge_mod.CodexBridgePortPool(base_port=23150, size=1)
    entered = asyncio.Event()
    bridge_box = {}

    class _Bridge:
        def __init__(self, port):
            self.port = port
            self.url = f"http://127.0.0.1:{port}/mcp"
            self.tools = []
            self.stopped = False
            bridge_box["value"] = self

        def set_tools(self, tools):
            self.tools = list(tools or [])

        async def start(self):
            pass

        async def stop(self):
            await asyncio.sleep(0)
            self.stopped = True

    async def blocked_process(**_kwargs):
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(bridge_mod, "get_port_pool", lambda: pool)
    monkeypatch.setattr(bridge_mod, "_new_bridge", _Bridge)
    monkeypatch.setattr(bridge_mod, "_run_codex_process", blocked_process)

    async def scenario():
        task = asyncio.create_task(bridge_mod.run_codex_turn(
            prompt="cancel", cwd=str(tmp_path), session_id=None,
            tools=[SimpleNamespace(name="meet")], model="gpt-test"))
        await entered.wait()
        assert pool.available == 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    bridge = bridge_box["value"]
    assert bridge.stopped and bridge.tools == []
    assert pool.available == 1


def test_Codex_큰stderr는_bounded_tail로_drain(monkeypatch, tmp_path):
    import asyncio
    import sys
    from organt import codex_mcp_bridge as bridge_mod

    real_spawn = asyncio.create_subprocess_exec
    real_tail = bridge_mod._read_stderr_tail
    seen = {}
    callbacks = []
    script = (
        "import json,sys\n"
        "sys.stdin.buffer.read()\n"
        "sys.stderr.write('z'*(2*1024*1024)+'TAILMARK')\n"
        "sys.stderr.flush()\n"
        "print(json.dumps({'type':'item.completed','item':"
        "{'type':'agent_message','text':'done'}}))\n"
    )

    async def fake_spawn(*_args, **kwargs):
        return await real_spawn(sys.executable, "-c", script, **kwargs)

    async def tracked_tail(stream, **kwargs):
        value = await real_tail(stream, **kwargs)
        seen["tail"] = value
        return value

    monkeypatch.setattr(bridge_mod.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(bridge_mod, "_read_stderr_tail", tracked_tail)
    text, _sid = asyncio.run(bridge_mod.run_codex_turn(
        prompt="stderr",
        cwd=str(tmp_path),
        session_id=None,
        tools=[],
        model="gpt-test",
        stderr=callbacks.append,
    ))
    assert text == "done"
    assert len(seen["tail"]) == bridge_mod._CODEX_STDERR_TAIL_LIMIT
    assert seen["tail"].endswith(b"TAILMARK")
    assert callbacks and callbacks[-1].endswith("TAILMARK")
    assert len(callbacks[-1].encode("utf-8")) <= 2000


def test_Codex_port_pool크기는_subprocess상한을_1회snapshot_clamp(monkeypatch):
    from organt import codex_mcp_bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "_PORT_POOL", None)
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS", "3")
    pool = bridge_mod.get_port_pool()
    assert pool.size == 3

    monkeypatch.setenv("ORGANT_MAX_SUBPROCS", "20")
    assert bridge_mod.get_port_pool() is pool
    assert bridge_mod.get_port_pool().size == 3

    monkeypatch.setattr(bridge_mod, "_PORT_POOL", None)
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS", "999")
    assert bridge_mod.get_port_pool().size == bridge_mod._CODEX_PORT_POOL_MAX

    monkeypatch.setattr(bridge_mod, "_PORT_POOL", None)
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS", "0")
    assert bridge_mod.get_port_pool().size == bridge_mod._CODEX_PORT_POOL_DEFAULT


def test_Codex_bridge_real_port충돌은_runner_SystemExit아닌_명시오류():
    import asyncio
    import socket
    import pytest
    from organt import codex_mcp_bridge as bridge_mod

    occupied = socket.socket()
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    port = occupied.getsockname()[1]
    try:
        bridge = bridge_mod.CodexToolBridge(port=port)
        with pytest.raises(RuntimeError, match="bind 실패"):
            asyncio.run(bridge.start(timeout=0.1))
    finally:
        occupied.close()


def test_Codex_bridge_start실패와_timeout은_명시오류후회수(monkeypatch):
    import asyncio
    import pytest
    import uvicorn
    from organt import codex_mcp_bridge as bridge_mod

    class _FailedServer:
        started = False
        should_exit = False

        def __init__(self, _cfg):
            pass

        async def serve(self, sockets=None):
            raise OSError("bind failed")

    monkeypatch.setattr(uvicorn, "Server", _FailedServer)
    bridge = bridge_mod.CodexToolBridge(port=23200)
    with pytest.raises(RuntimeError, match="시작 실패"):
        asyncio.run(bridge.start(timeout=0.1))
    assert bridge._task is None and bridge._server is None

    class _HungServer:
        started = False

        def __init__(self, _cfg):
            self.should_exit = False

        async def serve(self, sockets=None):
            while not self.should_exit:
                await asyncio.sleep(0)

    monkeypatch.setattr(uvicorn, "Server", _HungServer)
    bridge = bridge_mod.CodexToolBridge(port=23201)
    with pytest.raises(RuntimeError, match="시간초과"):
        asyncio.run(bridge.start(timeout=0.01))
    assert bridge._task is None and bridge._server is None


def test_Codex_embedded_uvicorn은_러너_signal_handler를_가로채지않음(monkeypatch):
    import asyncio
    from contextlib import contextmanager
    import uvicorn
    from organt import codex_mcp_bridge as bridge_mod

    class _SignalCapturingServer:
        capture_calls = 0

        def __init__(self, _cfg):
            self.started = False
            self.should_exit = False

        @contextmanager
        def capture_signals(self):
            type(self).capture_calls += 1
            yield

        async def serve(self, sockets=None):
            with self.capture_signals():
                self.started = True
                while not self.should_exit:
                    await asyncio.sleep(0)

    monkeypatch.setattr(uvicorn, "Server", _SignalCapturingServer)

    async def scenario():
        bridge = bridge_mod.CodexToolBridge(port=23202)
        await bridge.start(timeout=0.1)
        await bridge.stop()

    asyncio.run(scenario())
    assert _SignalCapturingServer.capture_calls == 0


def test_턴예산_초과는_정직마커로_반환(monkeypatch):
    """[U-036 재작업 #4(2026-07-21)] SDK가 max_budget_usd 초과로 턴을 끊으면(error_max_budget_usd)
    '턴 한도 도달(예산 상한)' 정직 마커를 달아 반환 — ①미완 참칭 없음 ②'턴 한도 도달' 문구 족이라
    이어가기 신호와 정합(흐름은 다음 wake의 세션 resume로 잇는다) ③빈 발화여도 마커로 비-공백이 돼
    handle의 빈응답 재시도(예산 재소진 루프)에 안 빠진다."""
    import asyncio
    o = Organt(_cfg())

    class _RM:
        subtype = "error_max_budget_usd"
        stop_reason = ""

    class _FakeClient:
        def __init__(self, options):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def query(self, prompt):
            pass

        async def receive_response(self):
            yield _RM()   # 발화 없이 예산 컷 결산만 — 최악(빈 발화) 경로

    monkeypatch.setattr("organt.organt.ClaudeSDKClient", _FakeClient)
    monkeypatch.setattr("organt.organt.ResultMessage", _RM)
    out, _sid = asyncio.run(o._run_once("p"))
    assert "턴 한도 도달(예산 상한)" in out                    # 정직 마커 + 비-공백(재시도 차단)


def test_마이크로는_무기억_신선호출_세션미저장(monkeypatch, tmp_path):
    """[프로브 무기억화(2026-07-22, U-041 실측: 프로브 회당 ~1cr = 세션 캐시 재읽기 — 판당 430cr/
    원장 27%)] 즉답 턴(응찰·표결)은 프롬프트가 자족적이고 정체성은 시스템 프롬프트 몫 — micro는
    ①resume 없이 신선 호출 ②그 호출이 만든 세션 id를 본세션에 저장하지 않는다(기억 포크 방지).
    일반 턴은 종전대로 resume. ORGANT_MICRO_FRESH=0이면 종전 동작(예비 스위치)."""
    from organt.organt import Organt
    monkeypatch.delenv("ORGANT_MICRO_FRESH", raising=False)
    o = Organt.__new__(Organt)
    o.options = __import__("claude_agent_sdk").ClaudeAgentOptions()
    o.session_id = "sess-main"
    normal = o._options_for_call(micro=False)
    assert getattr(normal, "resume", None) == "sess-main"       # 일반 턴 = 세션 이어감(무회귀)
    fresh = o._options_for_call(micro=True)
    assert getattr(fresh, "resume", None) in (None, "")          # micro = 신선(캐시 재읽기 0)
    monkeypatch.setenv("ORGANT_MICRO_FRESH", "0")
    legacy = o._options_for_call(micro=True)
    assert getattr(legacy, "resume", None) == "sess-main"        # 스위치 오프 = 종전 resume


def test_세션_cwd고정_pinned_cwd(tmp_path):
    """[세션-cwd 고정] CLI 세션 저장소는 cwd 기준 — 상태 파일에 '세션이 시작된 cwd'를 영속하고,
    다음 빌드는 그 cwd로 resume한다(흐름 도중 작업공간 카빙에도 세션 불멸). 디렉터리가 사라졌으면
    None(새 출발)."""
    import json as _json
    from organt.organt import pinned_cwd
    st = tmp_path / "organt_state_x.json"
    st.write_text(_json.dumps({"session_id": "s1", "cwd": str(tmp_path)}), encoding="utf-8")
    assert pinned_cwd(st) == str(tmp_path)                      # 살아있는 cwd → 고정
    st.write_text(_json.dumps({"session_id": "s1", "cwd": str(tmp_path / "없는폴더")}), encoding="utf-8")
    assert pinned_cwd(st) is None                               # 사라진 cwd → 고정 해제
    st.write_text(_json.dumps({"cwd": str(tmp_path)}), encoding="utf-8")
    assert pinned_cwd(st) is None                               # 세션 없으면 고정 무의미
    assert pinned_cwd(tmp_path / "없음.json") is None


def test_세션저장시_cwd_함께영속(tmp_path):
    import asyncio
    import json as _json
    o = Organt(_cfg(), state_path=str(tmp_path / "st.json"))
    o._save_session_id("sid-1")
    d = _json.loads((tmp_path / "st.json").read_text(encoding="utf-8"))
    assert d["session_id"] == "sid-1" and d["cwd"] == "/tmp/ws"   # build_options(_cfg()).cwd


def test_사전점검_저장소에없는_세션은_스폰전_폐기(monkeypatch, tmp_path):
    """[사전 점검 — 결정론] resume 대상이 '이 cwd의' CLI 저장소에 실재하지 않으면(레거시 상태 파일·
    cwd 불일치·유실) 스폰하기 전에 세션을 폐기하고 새로 시작한다 — 에러 텍스트에 기대지 않으므로
    'No conversation found' 영구 헛돌이(라이브 12회×2 관측)가 원천 차단된다."""
    import asyncio
    import json as _json
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    st = tmp_path / "st.json"
    st.write_text(_json.dumps({"session_id": "dead-sid"}), encoding="utf-8")   # 레거시(cwd 미기록)
    o = Organt(_cfg(), state_path=str(st))
    assert o.session_id == "dead-sid"
    calls = {"n": 0}

    async def fake_run_once(prompt):
        calls["n"] += 1
        assert o.session_id is None            # 스폰 시점엔 이미 새 출발(스테일 폐기 후)
        return ("기획 이어서 완료", "sid-new")

    monkeypatch.setattr(o, "_run_once", fake_run_once)
    out = asyncio.run(o.handle("이어서 진행"))
    assert out == "기획 이어서 완료" and calls["n"] == 1          # 헛스폰 0회 — 한 번에 전진
    assert _json.loads(st.read_text(encoding="utf-8"))["session_id"] == "sid-new"


def test_마커_안전망_저장소판정을_비껴간_스테일도_새세션(monkeypatch, tmp_path):
    """[이중 안전망] 저장소엔 파일이 있는데도 CLI가 'No conversation found'를 내는 변종(레이아웃
    변화 등)은 stderr 마커로 잡아 — 같은 세션 재시도 대신 즉시 새 세션으로 전진한다."""
    import asyncio
    import json as _json
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    store = fake_home / ".claude" / "projects" / "-tmp-ws"      # _cfg().workspace_dir="/tmp/ws"의 슬러그
    store.mkdir(parents=True)
    (store / "dead-sid.jsonl").write_text("{}", encoding="utf-8")   # 사전 점검은 통과하게
    st = tmp_path / "st.json"
    st.write_text(_json.dumps({"session_id": "dead-sid", "cwd": "/tmp/ws"}), encoding="utf-8")
    o = Organt(_cfg(), state_path=str(st))
    calls = {"n": 0}

    async def fake_run_once(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return ("API Error: ... [stderr] No conversation found with session ID: dead-sid", None)
        return ("기획 이어서 완료", "sid-new")

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(o, "_run_once", fake_run_once)
    monkeypatch.setattr("organt.organt.asyncio.sleep", _no_sleep)
    out = asyncio.run(o.handle("이어서 진행"))
    assert out == "기획 이어서 완료" and calls["n"] == 2          # 1회 마커 → 즉시 새 세션 성공
    assert o.session_id == "sid-new"                              # 죽은 세션 폐기·새 세션 영속
    assert _json.loads(st.read_text(encoding="utf-8"))["session_id"] == "sid-new"


def test_봇_샌드박스에서_임시공간과_브라우저검증이_된다(tmp_path):
    """[2026-07-26 U-063 실측] 봇 샌드박스의 tmpfs가 root 0755로 생겨 권한강등된 봇이 임시 디렉터리를
    하나도 못 만들었다 — 임시파일을 쓰는 보통 도구가 전부 죽고(브라우저 검증 포함), 봇은 원인을 못 보니
    검증을 포기하고 '서버만 띄우는' 비검증 명령으로 우회하다 계획 단계에서 교착했다(게임 판 U-063).
    화면 조건을 스스로 실증할 수 있어야 파이프라인이 '눈으로 보는 목표'를 다룰 수 있다."""
    import asyncio, os, shutil
    import pytest
    if os.geteuid() != 0 or not shutil.which("bwrap"):
        pytest.skip("격리 실행(bwrap+root) 환경에서만 의미 있는 계약")
    from system.guide_tools import run_workspace_command

    (tmp_path / "index.html").write_text(
        '<!doctype html><meta name=viewport content="width=device-width">'
        '<body style="margin:0"><div id=start style="height:200px">시작</div></body>',
        encoding="utf-8")
    (tmp_path / "verify_ui.py").write_text(
        "from playwright.sync_api import sync_playwright\n"
        "import sys, pathlib\n"
        "with sync_playwright() as p:\n"
        "    b = p.chromium.launch()\n"
        "    pg = b.new_page(viewport={'width':360,'height':800})\n"
        "    pg.goto('file://' + str(pathlib.Path('index.html').resolve()))\n"
        "    ok = pg.is_visible('#start') and not pg.evaluate(\n"
        "        'document.body.scrollHeight > window.innerHeight')\n"
        "    b.close()\n"
        "sys.exit(0 if ok else 1)\n", encoding="utf-8")

    ok_tmp = asyncio.run(run_workspace_command(
        str(tmp_path), "mkdir -p /tmp/probe.$$ && echo TMP_OK"))
    assert "TMP_OK" in (ok_tmp[2] or ""), ok_tmp      # 임시공간을 쓸 수 있다

    res = asyncio.run(run_workspace_command(str(tmp_path), "python3 verify_ui.py"))
    assert res[1] == 0, res                            # 화면 조건을 봇이 스스로 판정한다
