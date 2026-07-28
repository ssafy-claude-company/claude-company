"""codex_mcp_bridge — guide 도구를 codex(GPT 봇)가 붙는 HTTP MCP 서버로 서빙.

claude-agent-sdk는 in-process MCP로 도구를 자동 연결하지만, codex CLI엔 임베드 SDK가 없어 그게 안 된다.
그래서 같은 guide 도구(SdkMcpTool)를 streamable-HTTP MCP로 내보내 codex가 `--url`로 붙게 한다.
도구 핸들러는 러너 프로세스 안에서 라이브 flow 상태를 그대로 쥐고 돌고, codex는 그걸 HTTP로 호출한다.

러너는 여러 flow·회의 턴을 병렬 실행할 수 있다. 따라서 substantive Codex 턴마다 포트를 독점 임대하고
새 브리지 인스턴스를 띄운다. 그 턴의 subprocess·stderr·MCP 서버가 모두 회수된 뒤 포트만 반환하므로,
다른 flow의 도구 closure나 죽은 MCP session이 다음 턴에 섞이지 않는다. 도구 없는 micro 턴은 브리지를
띄우지 않는다.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
import os
import signal
import socket
from typing import List, Optional

import mcp.types as _mt
from mcp.server.fastmcp import FastMCP

# SdkMcpTool.input_schema는 compact {인자명: 파이썬타입}뿐 아니라 완전한 JSON Schema도
# 정식 입력이다. 완전 schema를 compact로 다시 해석하면 nested dict가 hash 불가라 tools/list
# 전체가 죽고, Codex가 guide 도구를 하나도 보지 못한다.
_TYMAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _json_schema(sch) -> dict:
    if isinstance(sch, dict) and sch and not all(
            isinstance(value, type) for value in sch.values()):
        # JSON Schema는 ``type``/``properties``가 생략될 수도 있고 anyOf만 있을 수도 있다.
        # compact schema는 값이 Python type인 dict라는 SDK 계약으로 구분해, 합법적인 JSON
        # Schema 모양을 임의로 재해석하지 않는다(required/enum/additionalProperties도 보존).
        return dict(sch)
    props = {
        k: {"type": _TYMAP.get(v, "string")}
        for k, v in (sch or {}).items()
    }
    # required는 비워둔다(핸들러가 누락 인자를 기본값으로 처리 — codex가 부분 인자로 불러도 안전).
    return {"type": "object", "properties": props, "required": []}


class CodexToolBridge:
    """guide 도구(SdkMcpTool 리스트)를 localhost streamable-HTTP MCP로 서빙. 봇 턴마다 set_tools로 교체."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._tools: List = []
        self._host = host
        self._port = port or int(os.environ.get("ORGANT_CODEX_MCP_PORT", "8791"))
        self._server = None            # uvicorn.Server
        self._task: Optional[asyncio.Task] = None
        self._listen_socket: socket.socket | None = None
        self._mcp = FastMCP("guide", host=self._host, port=self._port)
        srv = self._mcp._mcp_server    # 저수준 Server — 동적 도구 등록(현재 턴 도구를 읽음)

        @srv.list_tools()
        async def _list():
            return [_mt.Tool(name=t.name, description=(t.description or "")[:1024],
                             inputSchema=_json_schema(t.input_schema)) for t in self._tools]

        @srv.call_tool()
        async def _call(name, arguments):
            tool = next((x for x in self._tools if x.name == name), None)
            if tool is None:
                return [_mt.TextContent(type="text", text=f"(알 수 없는 도구: {name})")]
            res = await tool.handler(arguments or {})
            out = []
            for c in (res or {}).get("content", []):
                if isinstance(c, dict) and c.get("type") == "text":
                    out.append(_mt.TextContent(type="text", text=str(c.get("text", ""))))
            return out or [_mt.TextContent(type="text", text="")]

    def set_tools(self, tools) -> None:
        """이번 봇 턴의 guide 도구로 교체(make_guide_tools 결과)."""
        self._tools = list(tools or [])

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}/mcp"

    async def start(self, timeout: float = 6.0) -> None:
        """uvicorn을 띄워 실제 bind 완료까지 기다린다. 실패·시간초과는 조용히 삼키지 않는다."""
        if self._task is not None:
            if getattr(self._server, "started", False) and not self._task.done():
                return
            raise RuntimeError(f"Codex MCP bridge {self._host}:{self._port}가 재사용 불가 상태입니다.")
        import uvicorn
        app = self._mcp.streamable_http_app()   # StreamableHTTPSessionManager lifespan 포함
        cfg = uvicorn.Config(app, host=self._host, port=self._port,
                             log_level="warning", lifespan="on", access_log=False)
        self._server = uvicorn.Server(cfg)
        # 이 서버는 러너 안의 embedded 태스크다. uvicorn.Server.serve() 기본 signal capture를
        # 여러 bridge가 병렬 설치·역순 복원하면 SIGTERM 핸들러가 죽은 인스턴스로 되감긴다.
        self._server.capture_signals = lambda: nullcontext()
        try:
            family = socket.AF_INET6 if ":" in self._host else socket.AF_INET
            self._listen_socket = socket.socket(family, socket.SOCK_STREAM)
            self._listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._listen_socket.bind((self._host, self._port))
            self._listen_socket.listen(2048)
            self._listen_socket.setblocking(False)
        except OSError as exc:
            if self._listen_socket is not None:
                self._listen_socket.close()
            self._listen_socket = None
            self._server = None
            raise RuntimeError(
                f"Codex MCP bridge {self._host}:{self._port} bind 실패"
            ) from exc
        self._task = asyncio.create_task(
            self._server.serve(sockets=[self._listen_socket])
        )
        deadline = asyncio.get_running_loop().time() + max(0.01, float(timeout))
        try:
            while True:
                if getattr(self._server, "started", False):
                    return
                if self._task.done():
                    try:
                        self._task.result()
                    except BaseException as exc:
                        raise RuntimeError(
                            f"Codex MCP bridge {self._host}:{self._port} 시작 실패"
                        ) from exc
                    raise RuntimeError(
                        f"Codex MCP bridge {self._host}:{self._port}가 bind 전에 종료됐습니다."
                    )
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise RuntimeError(
                        f"Codex MCP bridge {self._host}:{self._port} 시작 시간초과"
                    )
                await asyncio.sleep(min(0.1, remaining))
        except BaseException:
            await asyncio.shield(self.stop())
            raise

    async def stop(self) -> None:
        """서버 태스크가 끝날 때까지 회수한다. timeout이면 cancel까지 완료한 뒤 반환한다."""
        if self._server is not None:
            self._server.should_exit = True
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
            except BaseException:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        self._task = None
        self._server = None
        if self._listen_socket is not None:
            self._listen_socket.close()
        self._listen_socket = None


# ── codex 런타임 — bwrap 격리로 GPT 봇 한 턴 실행(도구는 위 브리지로 물림) ──────────────
import json  # noqa: E402

_PORT_POOL = None

# codex 실행부에서 러너 컨텍스트에 도는 guide run은 평소 숨기고 네이티브 셸(bwrap 격리)을 쓴다.
# 단, milestone 도구 표면에서는 run만 release/e2e SYS receipt를 발급할 수 있으므로 아래 필터가
# 예외로 노출한다. 네이티브 셸은 같은 검사를 실행해도 receipt를 만들 수 없다.
_CODEX_TOOL_DENY = {"run"}
# 턴 도구 표면 관측 훅 — sys_core가 flow.log로 연결한다(미연결이면 no-op).
_TURN_TOOLS_SINK = None
def set_turn_tools_sink(fn):
    global _TURN_TOOLS_SINK
    _TURN_TOOLS_SINK = fn
def _log_turn_tools(names):
    if _TURN_TOOLS_SINK:
        _TURN_TOOLS_SINK(names)
# 이 표식들은 milestone의 모든 실질 턴 도구 표면에 있다. 따라서 run은 e2e 직전뿐 아니라
# 자동 release 검증이 백로그 소진 경계에서 시작될 수 있는 milestone 실질 턴 전체에 노출된다.
# 실제 실행 권한은 run 핸들러의 workspace/단일활성/봉인/receipt/권한강등 게이트가 결정한다.
_CODEX_RECEIPT_TOOL_MARKERS = frozenset({"e2e_open", "e2e_result"})

# 포트는 재사용하지만 FastMCP 인스턴스는 턴마다 새로 만든다. pool 크기는 프로세스 시작 뒤 첫 사용 때
# ORGANT_MAX_SUBPROCS를 한 번 snapshot한다(운영 env 변경은 서비스 재시작 경계에서 반영).
_CODEX_PORT_POOL_DEFAULT = 8
_CODEX_PORT_POOL_MAX = 32
_CODEX_PORT_BASE_DEFAULT = 8791

# Codex의 --json 출력은 한 이벤트가 도구 스키마·긴 메시지를 함께 담아 asyncio의 기본
# StreamReader 줄 한계(64KiB)를 정상적으로 넘을 수 있다. 기본값은 충분히 넓히되, 잘못된 출력이
# 메모리를 무제한 점유하지 않도록 상한을 둔다. 운영 중 모델/CLI 변화는 env로 조정 가능하다.
_CODEX_STREAM_LIMIT_DEFAULT = 4 * 1024 * 1024
_CODEX_STREAM_LIMIT_MIN = 64 * 1024
_CODEX_STREAM_LIMIT_MAX = 16 * 1024 * 1024
_CODEX_STDERR_TAIL_LIMIT = 64 * 1024
_CODEX_STDERR_READ_CHUNK = 16 * 1024


def _codex_port_pool_size() -> int:
    try:
        value = int(os.environ.get("ORGANT_MAX_SUBPROCS",
                                   str(_CODEX_PORT_POOL_DEFAULT)))
    except (TypeError, ValueError):
        value = _CODEX_PORT_POOL_DEFAULT
    if value <= 0:
        value = _CODEX_PORT_POOL_DEFAULT
    return min(_CODEX_PORT_POOL_MAX, max(1, value))


def _codex_port_base(size: int) -> int:
    try:
        value = int(os.environ.get("ORGANT_CODEX_MCP_PORT",
                                   str(_CODEX_PORT_BASE_DEFAULT)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ORGANT_CODEX_MCP_PORT가 정수가 아닙니다.") from exc
    if value < 1024 or value + int(size) - 1 > 65535:
        raise RuntimeError(
            f"Codex MCP 포트 범위가 유효하지 않습니다: {value}..{value + int(size) - 1}"
        )
    return value


class CodexBridgePortPool:
    """동시 substantive 턴에 서로 다른 loopback 포트를 주는 bounded lease pool."""

    def __init__(self, *, base_port: int | None = None, size: int | None = None):
        self.size = _codex_port_pool_size() if size is None else max(1, int(size))
        if self.size > _CODEX_PORT_POOL_MAX:
            self.size = _CODEX_PORT_POOL_MAX
        self.base_port = (
            _codex_port_base(self.size) if base_port is None else int(base_port)
        )
        if self.base_port < 1024 or self.base_port + self.size - 1 > 65535:
            raise RuntimeError(
                "Codex MCP lease port 범위가 1024..65535 밖입니다."
            )
        self._available: asyncio.Queue[int] = asyncio.Queue(maxsize=self.size)
        for port in range(self.base_port, self.base_port + self.size):
            self._available.put_nowait(port)

    @asynccontextmanager
    async def lease(self):
        port = await self._available.get()
        try:
            yield port
        finally:
            self._available.put_nowait(port)

    @property
    def available(self) -> int:
        return self._available.qsize()


def get_port_pool() -> CodexBridgePortPool:
    global _PORT_POOL
    if _PORT_POOL is None:
        _PORT_POOL = CodexBridgePortPool()
    return _PORT_POOL


def _new_bridge(port: int) -> CodexToolBridge:
    return CodexToolBridge(port=port)


def _codex_stream_limit() -> int:
    try:
        value = int(os.environ.get("ORGANT_CODEX_STREAM_LIMIT",
                                   str(_CODEX_STREAM_LIMIT_DEFAULT)))
    except (TypeError, ValueError):
        value = _CODEX_STREAM_LIMIT_DEFAULT
    return min(_CODEX_STREAM_LIMIT_MAX, max(_CODEX_STREAM_LIMIT_MIN, value))


# 추론 강도(effort) 매핑 — Claude(low/medium/high/xhigh/max) → codex reasoning effort.
# low는 추론 토큰 ~0(실측)이라 최저 비용, max는 추론을 실제로 켠다(실측: 같은 과제 max=323 vs low=24 추론토큰).
# codex 5.6-luna/sol은 xhigh·max를 지원하므로 그대로 통과시킨다(종전엔 high로 깎아 max 지정이 무효였다 —
# 2026-07-23 교정). gpt-5.4는 xhigh까지만 지원 → max를 걸면 codex가 거부하니 5.4엔 max를 쓰지 않는다.
_EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high",
               "xhigh": "xhigh", "max": "max", "minimal": "minimal"}
# 위 주석의 '5.4엔 max를 쓰지 않는다'는 규율이었을 뿐 코드가 아니었다 — 구형 모델을 고른 봇은
# 매 턴 400(Invalid value: 'max'. Supported: none/minimal/low/medium/high/xhigh)으로 죽었다.
# 게다가 effort 미지정 턴은 -c를 안 붙여 **머신 전역 ~/.codex/config.toml(현재 ultra)** 을 물려받으므로,
# 앱이 아무것도 안 골라도 같은 400이 난다(2026-07-28 실측). 그래서 구형 모델엔 항상 명시 강도를 싣고,
# 지원 상한(xhigh)을 넘는 값은 깎는다. 5.6 계열은 종전 그대로(무회귀 — max·전역 상속 유지).
_LEGACY_EFFORT_CEIL = "xhigh"          # gpt-5.6 이전 세대가 받는 최고 강도
_LEGACY_EFFORT_DEFAULT = "high"        # 구형 모델인데 아무도 안 고른 경우(전역 상속 차단용 명시값)


def _clamp_effort(model, effort):
    """모델이 실제로 받는 codex reasoning effort. 모르는 모델은 건드리지 않는다(빈 값=전역 상속)."""
    ce = _EFFORT_MAP.get(str(effort or "").strip().lower()) or ""
    m = str(model or "").strip().lower()
    if not m.startswith("gpt-") or m.startswith("gpt-5.6"):
        return ce                                    # 최신 세대·비-codex 경로는 종전 동작 그대로
    if not ce:
        return _LEGACY_EFFORT_DEFAULT                # 전역 config(ultra) 상속을 끊는다
    if ce in ("max", "ultra"):
        return _LEGACY_EFFORT_CEIL
    return ce


def _usage_from_event(ev):
    """codex --json 이벤트에서 이 턴의 토큰 사용량을 뽑는다(스키마 변화 방어적).

    turn.completed.usage가 정본(턴 단위). token_count는 요청 단위 last_token_usage를 누적한다.
    """
    typ = ev.get("type")
    if typ == "turn.completed":
        u = ev.get("usage")
        if not isinstance(u, dict) or not u:
            info = ev.get("info") or {}
            u = info.get("last_token_usage") or info.get("total_token_usage") or {}
        return ("turn", u if isinstance(u, dict) else {})
    if typ == "token_count":
        info = ev.get("info") or {}
        u = info.get("last_token_usage") or {}
        return ("delta", u if isinstance(u, dict) else {})
    return (None, {})


def _bwrap_args(ws: str, read_only: bool = False) -> list:
    """작업공간만 제한적으로 노출하고 시스템·DB·크레덴셜은 차단한다.

    Task 경계 e2e는 native shell에서 산출물을 바꾸지 못하게 ro-bind한다. 봉인된 exact verifier는
    부모 러너의 guide run에서 실행되므로 빌드 캐시처럼 검사 명령에 필요한 쓰기는 그 경로에만 남는다.
    """
    workspace_bind = "--ro-bind" if read_only else "--bind"
    return ["/usr/bin/bwrap", "--clearenv",
            "--ro-bind", "/usr", "/usr",
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin",
            "--ro-bind", "/etc/ssl", "/etc/ssl", "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
            "--ro-bind", "/root/.local", "/root/.local", "--bind", "/root/.codex", "/root/.codex",
            workspace_bind, ws, ws, "--chdir", ws,
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--unshare-pid", "--die-with-parent",
            "--setenv", "PATH", "/usr/bin:/bin:/root/.local/bin",
            "--setenv", "HOME", "/root", "--setenv", "TERM", "xterm", "--setenv", "ORGANT_BOT", "1"]


async def _read_stderr_tail(
    stream,
    *,
    limit: int = _CODEX_STDERR_TAIL_LIMIT,
    on_activity=None,
) -> bytes:
    """stderr를 계속 drain하되 마지막 limit bytes만 보존한다."""
    limit = max(1, int(limit))
    tail = bytearray()
    while True:
        chunk = await stream.read(_CODEX_STDERR_READ_CHUNK)
        if not chunk:
            break
        if on_activity:
            try:
                on_activity()
            except Exception:
                pass
        if len(chunk) >= limit:
            tail[:] = chunk[-limit:]
            continue
        overflow = len(tail) + len(chunk) - limit
        if overflow > 0:
            del tail[:overflow]
        tail.extend(chunk)
    return bytes(tail)


async def _join_stderr_task(task, timeout: float = 3.0) -> bytes:
    if task is None:
        return b""
    try:
        result = await asyncio.wait_for(
            asyncio.shield(task),
            timeout=max(0.01, float(timeout)),
        )
        return bytes(result or b"")
    except asyncio.TimeoutError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return b""
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    except Exception:
        return b""


async def _terminate_process(proc, grace=3.0) -> None:
    """취소된 Codex 턴의 bwrap 프로세스 그룹을 끝까지 회수한다(자식 브라우저/셸 포함)."""
    if getattr(proc, "returncode", None) is not None:
        return
    try:
        pid = getattr(proc, "pid", None)
        if pid:
            os.killpg(int(pid), signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, AttributeError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=float(grace))
        return
    except asyncio.TimeoutError:
        pass
    try:
        pid = getattr(proc, "pid", None)
        if pid:
            os.killpg(int(pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, AttributeError):
        pass
    try:
        await proc.wait()
    except Exception:
        pass


async def _run_codex_process(
    *,
    prompt,
    cwd,
    session_id,
    model,
    effort=None,
    on_activity=None,
    on_narrate=None,
    stderr=None,
    mcp_url: str | None,
    read_only: bool = False,
    on_usage=None,
):
    """Codex subprocess 하나를 실행하고 stdout/stderr/프로세스 그룹을 전부 회수한다."""
    ws = str(cwd)
    codex = ["/root/.local/bin/codex", "exec"]
    if session_id:
        codex += ["resume", str(session_id)]
    # codex 자체 샌드박스 off(--dangerously-bypass) → bwrap 외부 샌드박스가 대체(codex 공식 권장 조합:
    # '외부 샌드박스 환경에서만 bypass 쓰라'). 헤드리스 exec에서 MCP 도구 승인이 무조건 취소되는 상류
    # 버그(openai/codex#16685)의 유일 우회이기도 하다.
    codex += ["--json", "--skip-git-repo-check",
              "--dangerously-bypass-approvals-and-sandbox"]
    if mcp_url:
        codex += ["-c", 'mcp_servers.guide.url="%s"' % mcp_url]
    if model:
        codex += ["-m", str(model)]
    _ce = _clamp_effort(model, effort)
    if _ce:   # 추론 강도를 codex에 실제 반영(종전엔 안 넘겨 무시됐음 — 사용자 지적)
        codex += ["-c", 'model_reasoning_effort="%s"' % _ce]
    args = _bwrap_args(ws, read_only=read_only) + ["--"] + codex

    stream_limit = _codex_stream_limit()
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "HOME": "/root"},
        start_new_session=True, limit=stream_limit)
    _stderr_task = (
        asyncio.create_task(
            _read_stderr_tail(proc.stderr, on_activity=on_activity)
        )
        if proc.stderr
        else None
    )
    final_text, sid, _err = "", session_id, b""
    # [토큰 계량(2026-07-28, 사용자: '토큰 소비가 기록되지 않아 크레딧이 소비되지 않는다')]
    # Claude 경로는 SDK가 결산(total_cost_usd)을 주지만 codex 경로는 아무도 안 봤다 → GPT 봇의
    # 판은 원장에 0원으로 남아 무제한이었다. 여기서 이 턴의 토큰을 모아 상위(Organt)에 넘긴다.
    usage_turn, usage_sum, api_error = {}, {}, ""
    try:
        try:
            proc.stdin.write((prompt or "").encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except Exception:
            pass
        assert proc.stdout is not None
        async for raw in proc.stdout:
            if on_activity:
                try:
                    on_activity()
                except Exception:
                    pass
            try:
                ev = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                continue
            typ = ev.get("type")
            _uk, _u = _usage_from_event(ev)
            if _uk == "turn":
                usage_turn = _u or usage_turn
            elif _uk == "delta" and _u:
                for k, v in _u.items():
                    if isinstance(v, (int, float)):
                        usage_sum[k] = usage_sum.get(k, 0) + v
            if typ in ("turn.failed", "error"):
                # 400(모델·강도 불일치 등)은 종전에 stdout 이벤트로만 오고 stderr엔 안 남아, 턴이
                # '빈 발화'로 조용히 끝났다 — 사유를 잡아 예외 문구에 싣는다(운영 진단).
                _m = ev.get("message") or ((ev.get("error") or {}).get("message") if isinstance(ev.get("error"), dict) else "")
                if _m and not api_error:
                    api_error = str(_m)[:600]
            if typ == "thread.started":
                sid = ev.get("thread_id") or sid
            elif typ == "item.completed":
                it = ev.get("item") or {}
                if it.get("type") == "agent_message":
                    _t = (it.get("text") or "").strip()
                    if _t:
                        final_text = _t
                        if on_narrate:
                            try:
                                on_narrate(_t)
                            except Exception:
                                pass
        await proc.wait()
    except asyncio.CancelledError:
        # Sys.request_cancel이 상위 턴을 취소해도 codex/bwrap/브라우저가 뒤에서 계속 도는 좀비를 남기지
        # 않는다. 정리 자체는 취소 전파에 끊기지 않게 shield하고 원 CancelledError를 그대로 올린다.
        await asyncio.shield(_terminate_process(proc))
        raise
    except ValueError as exc:
        # StreamReader가 설정 상한보다 긴 무개행 JSON 이벤트를 받으면 ValueError로 감싼
        # LimitOverrunError를 낸다. 조용히 빈 발화로 바꾸지 말고 프로세스를 회수한 뒤 실명 오류로 올린다.
        _limit_cause = exc.__cause__ or exc.__context__
        if (isinstance(_limit_cause, asyncio.LimitOverrunError)
                or ("chunk exceed the limit" in str(exc))
                or ("Separator is not found" in str(exc))
                or ("longer than limit" in str(exc))):
            raise RuntimeError(
                f"codex JSON 이벤트가 스트림 안전 한계({stream_limit} bytes)를 초과했습니다"
            ) from exc
        raise
    finally:
        if getattr(proc, "returncode", None) is None:
            await asyncio.shield(_terminate_process(proc))
        _err = await _join_stderr_task(_stderr_task)
    if stderr and _err:
        try:
            stderr(_err.decode("utf-8", "replace")[-2000:])
        except Exception:
            pass
    if on_usage:
        try:
            on_usage(dict(usage_turn or usage_sum or {}))
        except Exception:
            pass
    if proc.returncode not in (0, None):
        raise RuntimeError(
            f"codex exec 실패(rc={proc.returncode}) "
            f"{api_error or _err.decode('utf-8', 'replace')[-500:]}")
    if api_error and not final_text:
        # rc=0인데 모델이 한 마디도 못 한 경우(스트림 안 오류) — 조용한 빈 턴 대신 사유를 올린다.
        raise RuntimeError(f"codex 턴 실패: {api_error}")
    return final_text, sid


async def run_codex_turn(*, prompt, cwd, session_id, tools, model, effort=None,
                         on_activity=None, on_narrate=None, stderr=None,
                         read_only=False, on_usage=None):
    """GPT 봇 한 턴. guide 도구가 있을 때만 전용 port/bridge를 턴 수명 동안 임대한다."""
    tool_names = {
        getattr(tool, "name", None)
        for tool in (tools or [])
    }
    receipt_run_required = bool(tool_names & _CODEX_RECEIPT_TOOL_MARKERS)
    turn_tools = [
        tool for tool in (tools or [])
        if (getattr(tool, "name", None) not in _CODEX_TOOL_DENY
            or (getattr(tool, "name", None) == "run" and receipt_run_required))
    ]
    process_args = {
        "prompt": prompt,
        "cwd": cwd,
        "session_id": session_id,
        "model": model,
        "effort": effort,
        "on_activity": on_activity,
        "on_narrate": on_narrate,
        "stderr": stderr,
        "read_only": bool(read_only),
        "on_usage": on_usage,
    }
    if not turn_tools:
        return await _run_codex_process(**process_args, mcp_url=None)

    async with get_port_pool().lease() as port:
        bridge = _new_bridge(port)
        bridge.set_tools(turn_tools)
        # [도구 표면 관측(2026-07-27)] GPT 경로는 도구가 MCP로 붙어 눈에 안 보인다 — 봇이 도구를
        # 안 부를 때 '없어서'인지 '안 해서'인지 구분할 수 없어 추정으로 판을 태웠다. 턴마다 실제
        # 실린 도구 수·핵심 도구 유무를 남긴다(이름 전체는 소음이라 개수 + 관심 도구만).
        try:
            _names = sorted(getattr(t, "name", "") for t in turn_tools)
            _log_turn_tools(_names)
        except Exception:
            pass
        try:
            await bridge.start()
            return await _run_codex_process(
                **process_args,
                mcp_url=bridge.url,
            )
        finally:
            bridge.set_tools([])
            await asyncio.shield(bridge.stop())
