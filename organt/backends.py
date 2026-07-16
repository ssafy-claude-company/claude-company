"""agent backends — 봇 런타임을 모델-불가지(Claude/Codex)로 추상화.

지금 organt/organt.py의 Organt._run_once는 claude-agent-sdk(ClaudeSDKClient)에 하드코딩돼 있다.
이 모듈은 그 실행 한 번을 backend 인터페이스로 뽑아, Claude와 Codex(OpenAI)를 갈아끼우게 한다.

경계(단 하나): `run_once(prompt, ...) -> RunResult(text, session_id, truncated, usage)`.
그 위(프롬프트 조립·세션 영속·resume 판정·워치독)는 백엔드 불가지라 Organt에 그대로 남는다.

선택: 환경변수 `ORGANT_BACKEND=claude|codex` (기본 claude — 라이브 동작 불변).

⚠️ CodexBackend는 **스캐폴드**다. 완성 절차는 organt/CODEX_BACKEND.md 참조 — 특히 guide MCP
   도구 브리징(in-process → codex가 붙는 stdio MCP 서버)이 핵심 미완 작업.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


@dataclass
class RunResult:
    """백엔드 실행 한 번의 결과 — 모델 불가지."""
    text: str = ""                     # 최종(마지막 비어있지 않은) 발화
    session_id: Optional[str] = None   # 이어가기용 세션 식별자
    truncated: bool = False            # 턴 한도 등으로 끊겼나(max_turns)
    usage: dict = field(default_factory=dict)   # cost/duration/tokens (필드명 방어적)


class AgentBackend(Protocol):
    """모델 런타임 한 판을 구동하는 백엔드. Claude/Codex가 각자 구현."""

    name: str

    async def run_once(
        self,
        prompt: str,
        *,
        cwd: str,
        session_id: Optional[str],
        system_prompt: str,
        allowed_tools: list,
        mcp_servers: Any,           # guide 도구(run/request/meet/...) — 백엔드가 자기 방식으로 연결
        hooks: Any,                 # 권한 훅(workspace-write·협의 Write 차단) — 백엔드가 매핑
        model: Optional[str],
        on_activity: Optional[Callable[[], None]],
        on_narrate: Optional[Callable[[str], None]],
        stderr: Optional[Callable[[str], None]],
    ) -> RunResult:
        ...

    def session_exists(self, session_id: Optional[str], cwd: str) -> bool:
        """세션 파일이 실재하나 — '전체(fresh) vs 델타 프롬프트' 결정론 판정(SDK 예외 텍스트 의존 회피)."""
        ...


def select_backend(config) -> "AgentBackend":
    """ORGANT_BACKEND로 백엔드 선택. 기본 claude(라이브 불변)."""
    which = (os.environ.get("ORGANT_BACKEND") or "claude").strip().lower()
    if which == "codex":
        return CodexBackend(config)
    return ClaudeBackend(config)


# ─────────────────────────────────────────────────────────────────────────────
# ClaudeBackend — 현행 로직의 얇은 래퍼(레퍼런스). 실제 구현은 organt.py의 Organt._run_once가
# 이 인터페이스로 그대로 옮겨오면 된다(heartbeat·cancellation·stderr 포함). 지금은 Organt가
# 자체 _run_once를 갖고 있으므로, 이 클래스는 '옮길 자리'를 표시하는 스텁이다.
# ─────────────────────────────────────────────────────────────────────────────
class ClaudeBackend:
    name = "claude"

    def __init__(self, config):
        self.config = config

    async def run_once(self, prompt, **kw) -> RunResult:   # noqa: D401
        raise NotImplementedError(
            "ClaudeBackend.run_once는 organt.py Organt._run_once의 SDK 로직을 이리로 옮긴 뒤 활성화. "
            "지금은 Organt가 직접 _run_once를 돌린다(ORGANT_BACKEND 미설정=현행 경로).")

    def session_exists(self, session_id, cwd) -> bool:
        from pathlib import Path
        if not session_id:
            return False
        p = (Path.home() / ".claude" / "projects" / str(cwd).replace("/", "-")
             / f"{session_id}.jsonl")
        return p.is_file()


# ─────────────────────────────────────────────────────────────────────────────
# CodexBackend — OpenAI Codex CLI(`codex exec`) 어댑터. ⚠️ 스캐폴드.
# 완성 절차: organt/CODEX_BACKEND.md. 핵심 3난제:
#   ① guide MCP 도구 브리징(in-process → codex config의 stdio MCP 서버)
#   ② 권한 훅 → codex sandbox 정책 매핑(workspace-write + 협의 Write 차단)
#   ③ 세션 resume(~/.codex/sessions rollout) + JSON 이벤트 파싱(final msg·session_id·usage)
# ─────────────────────────────────────────────────────────────────────────────
class CodexBackend:
    name = "codex"

    def __init__(self, config):
        self.config = config
        self.cli = os.environ.get("ORGANT_CODEX_CLI") or "codex"

    async def run_once(self, prompt, *, cwd, session_id, system_prompt, allowed_tools,
                       mcp_servers, hooks, model, on_activity, on_narrate, stderr) -> RunResult:
        """codex exec 한 판. 아래는 골격 — CODEX_BACKEND.md의 ①②③ 채워야 실동작."""
        import asyncio
        import json

        args = [self.cli, "exec", "--json", "-C", cwd,
                "--sandbox", "workspace-write"]
        if model:
            args += ["-m", model]
        # TODO①: guide MCP 도구 — codex config(-c 'mcp_servers.guide=...')로 stdio 서버 지정.
        # TODO②: hooks(협의 Write 차단 등) → sandbox/approval 정책으로 매핑.
        # TODO③: session_id 있으면 resume(예: args += ["resume", session_id]) — codex resume 형식 확인.
        args.append(prompt)

        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        text, sid, truncated, usage = "", session_id, False, {}
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
            # TODO③: codex --json 이벤트 스키마에 맞춰 final assistant text·session_id·usage 추출.
            #   (예상: ev.get("type")로 분기 — 'item.completed'/'agent_message'/'session.created' 등.
            #    실제 스키마는 `codex exec --json "hi"` 한 번 돌려 확인할 것.)
            _t = (ev.get("text") or ev.get("message") or "")
            if isinstance(_t, str) and _t.strip():
                text = _t.strip()
                if on_narrate:
                    try:
                        on_narrate(text)
                    except Exception:
                        pass
            _sid = ev.get("session_id") or ev.get("thread_id")
            if _sid:
                sid = _sid
        _err = (await proc.stderr.read()).decode("utf-8", "replace") if proc.stderr else ""
        await proc.wait()
        if stderr and _err:
            try:
                stderr(_err[-2000:])
            except Exception:
                pass
        if proc.returncode not in (0, None) and not text:
            raise RuntimeError(f"codex exec 실패(rc={proc.returncode}) {_err[-500:]}")
        return RunResult(text=text, session_id=sid, truncated=truncated, usage=usage)

    def session_exists(self, session_id, cwd) -> bool:
        # TODO③: codex 세션 rollout 파일 실재 판정(~/.codex/sessions/…). 정확한 경로는 codex 문서·
        #        실제 파일 확인. 우선은 보수적으로 session_id 존재 여부만.
        return bool(session_id)
