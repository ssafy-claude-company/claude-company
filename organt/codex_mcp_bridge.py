"""codex_mcp_bridge — guide 도구를 codex(GPT 봇)가 붙는 HTTP MCP 서버로 서빙.

claude-agent-sdk는 in-process MCP로 도구를 자동 연결하지만, codex CLI엔 임베드 SDK가 없어 그게 안 된다.
그래서 같은 guide 도구(SdkMcpTool)를 streamable-HTTP MCP로 내보내 codex가 `--url`로 붙게 한다.
도구 핸들러는 러너 프로세스 안에서 라이브 flow 상태를 그대로 쥐고 돌고, codex는 그걸 HTTP로 호출한다.

러너는 단일 베턴(한 번에 봇 하나)이라 브리지는 '현재 턴의 도구' 한 벌만 들고, 봇 턴마다 set_tools로 교체한다.
서버는 러너 asyncio 루프 안 태스크로 뜬다 — _run_once가 codex 서브프로세스를 await하는 동안 같은 루프가
들어오는 도구 호출을 처리하므로(단일 베턴 = flow 동시변경 없음) 스레드 경합이 없다.
"""
from __future__ import annotations

import asyncio
import os
from typing import List, Optional

import mcp.types as _mt
from mcp.server.fastmcp import FastMCP

# SdkMcpTool.input_schema는 {인자명: 파이썬타입} — JSON schema로 변환(대부분 str).
_TYMAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _json_schema(sch) -> dict:
    props = {k: {"type": _TYMAP.get(v, "string")} for k, v in (sch or {}).items()}
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

    async def start(self) -> None:
        """러너 루프 안에서 uvicorn 서버 태스크를 띄운다(이미 떠 있으면 무동작)."""
        if self._task is not None:
            return
        import uvicorn
        app = self._mcp.streamable_http_app()   # StreamableHTTPSessionManager lifespan 포함
        cfg = uvicorn.Config(app, host=self._host, port=self._port,
                             log_level="warning", lifespan="on", access_log=False)
        self._server = uvicorn.Server(cfg)
        self._task = asyncio.ensure_future(self._server.serve())
        for _ in range(60):                      # 기동 대기(≤6s)
            if getattr(self._server, "started", False):
                return
            await asyncio.sleep(0.1)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception:
                pass
        self._task = None
        self._server = None


# ── codex 런타임 — bwrap 격리로 GPT 봇 한 턴 실행(도구는 위 브리지로 물림) ──────────────
import json  # noqa: E402

_BRIDGE = None

# codex 실행부에서 러너 컨텍스트(격리 밖)에 도는 도구는 codex에 주지 않는다 — codex의 파일·셸 조작은
# 네이티브 도구(bwrap 격리)로 하게 하고, guide 도구는 협업(회의·표결·백로그·위임)만 브리지로 노출.
_CODEX_TOOL_DENY = {"run"}

# 추론 강도(effort) 매핑 — Claude(low/medium/high/xhigh/max) → codex reasoning effort.
# low는 추론 토큰 ~0(실측)이라 최저 비용, max는 추론을 실제로 켠다(실측: 같은 과제 max=323 vs low=24 추론토큰).
# codex 5.6-luna/sol은 xhigh·max를 지원하므로 그대로 통과시킨다(종전엔 high로 깎아 max 지정이 무효였다 —
# 2026-07-23 교정). gpt-5.4는 xhigh까지만 지원 → max를 걸면 codex가 거부하니 5.4엔 max를 쓰지 않는다.
_EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high",
               "xhigh": "xhigh", "max": "max", "minimal": "minimal"}


def get_bridge():
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = CodexToolBridge()
    return _BRIDGE


def _bwrap_args(ws: str) -> list:
    """작업공간만 rw, 시스템·DB·크레덴셜은 차단(검증된 프로파일). --clearenv로 env 비밀까지 제거."""
    return ["/usr/bin/bwrap", "--clearenv",
            "--ro-bind", "/usr", "/usr",
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin",
            "--ro-bind", "/etc/ssl", "/etc/ssl", "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
            "--ro-bind", "/root/.local", "/root/.local", "--bind", "/root/.codex", "/root/.codex",
            "--bind", ws, ws, "--chdir", ws,
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--unshare-pid", "--die-with-parent",
            "--setenv", "PATH", "/usr/bin:/bin:/root/.local/bin",
            "--setenv", "HOME", "/root", "--setenv", "TERM", "xterm", "--setenv", "ORGANT_BOT", "1"]


async def run_codex_turn(*, prompt, cwd, session_id, tools, model, effort=None,
                         on_activity=None, on_narrate=None, stderr=None):
    """GPT 봇 한 턴 — codex를 bwrap 외부 샌드박스로 띄우고 guide 도구는 브리지로 물려 실행.
    반환 (최종발화, session_id). resume=`codex exec resume <id>`(세션 연속성)."""
    ws = str(cwd)
    bridge = get_bridge()
    await bridge.start()
    bridge.set_tools([t for t in (tools or []) if t.name not in _CODEX_TOOL_DENY])

    codex = ["/root/.local/bin/codex", "exec"]
    if session_id:
        codex += ["resume", str(session_id)]
    # codex 자체 샌드박스 off(--dangerously-bypass) → bwrap 외부 샌드박스가 대체(codex 공식 권장 조합:
    # '외부 샌드박스 환경에서만 bypass 쓰라'). 헤드리스 exec에서 MCP 도구 승인이 무조건 취소되는 상류
    # 버그(openai/codex#16685)의 유일 우회이기도 하다.
    codex += ["--json", "--skip-git-repo-check",
              "--dangerously-bypass-approvals-and-sandbox",
              "-c", 'mcp_servers.guide.url="%s"' % bridge.url]
    if model:
        codex += ["-m", str(model)]
    _ce = _EFFORT_MAP.get(str(effort or "").strip().lower())
    if _ce:   # 추론 강도를 codex에 실제 반영(종전엔 안 넘겨 무시됐음 — 사용자 지적)
        codex += ["-c", 'model_reasoning_effort="%s"' % _ce]
    args = _bwrap_args(ws) + ["--"] + codex

    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "HOME": "/root"})
    try:
        proc.stdin.write((prompt or "").encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
    except Exception:
        pass

    final_text, sid = "", session_id
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
    _err = b""
    if proc.stderr:
        try:
            _err = await proc.stderr.read()
        except Exception:
            pass
    await proc.wait()
    if stderr and _err:
        try:
            stderr(_err.decode("utf-8", "replace")[-2000:])
        except Exception:
            pass
    return final_text, sid
