# Codex 백엔드 완성 가이드 (봇 런타임 모델-불가지화)

목표: AI 직원(봇)을 **Claude Code SDK 말고 OpenAI Codex CLI로도** 구동. 현재 Claude에 하드코딩된
런타임을 `organt/backends.py`의 `AgentBackend` 인터페이스 뒤로 추상화한다.

## 현재 구조 (Claude-특정 지점)
- `organt/organt.py` `Organt._run_once(prompt)` — `claude-agent-sdk`의 `ClaudeSDKClient`로 한 판 실행
  → `(최종발화, session_id)` 반환. **이게 유일한 추상화 경계.**
- `organt/organt.py` `build_options()` — `ClaudeAgentOptions`(model·system_prompt=CLAUDE.md·cwd·
  allowed_tools·permission_mode·max_turns·cli_path·mcp_servers/hooks override).
- 세션: `~/.claude/projects/<cwd '/'→'-'>/<sid>.jsonl`, resume=`options.resume=sid`.
- guide 도구(run·request·meet·pick_backlog·report_iter·recruit·deploy 등): `build_guide_server`가
  **in-process MCP 서버**로 SDK에 서빙(`system/guide_tools.py`). 권한 훅: `system/permissions.py`
  `make_pre_tool_use_hook`(작업공간 밖 Write 차단·협의 Info 중 Write 차단).

## 목표 구조
`Organt._run_once`의 SDK 호출부를 `backends.py`의 백엔드로 위임. 그 위(프롬프트 조립·cwd 앵커·세션
영속·resume 판정·워치독·narrate)는 백엔드 불가지라 Organt에 남긴다. 선택 = `ORGANT_BACKEND=claude|codex`.

## 완성 단계

### 0. Claude 경로 리팩터(무해 우선)
`Organt._run_once`의 SDK 로직(ClaudeSDKClient·heartbeat·stderr·cancellation·ResultMessage 결산)을
`backends.ClaudeBackend.run_once`로 그대로 옮기고, Organt는 `select_backend(config).run_once(...)`를
호출하게 한다. **동작 바이트 동일**해야 함(회귀 0). `bash ops/verify.sh`로 확인(단, verify는 라이브
SDK 실행을 안 돌리니, 실제 러너로 판 하나 돌려 e2e 확인 필요 — 러너 재시작은 사용자 승인 후).

### 1. ⚠️ 핵심 — guide MCP 도구 브리징
Codex는 별 프로세스라 in-process MCP 서버에 못 붙는다. 두 길:
- **(권장) stdio MCP 서버로 노출**: `system/guide_tools.py`의 도구들을 `mcp` 파이썬 서버(stdio
  transport)로도 실행 가능하게 하고, codex config에 등록:
  `codex ... -c 'mcp_servers.guide={command="python",args=["-m","organt.guide_mcp_server"],env={...}}'`
  (플로우별 상태(me_id·flow)를 env/인자로 주입 — 각 봇 wake마다 다르므로 서버를 봇별로 띄우거나,
  flow 컨텍스트를 stdin 첫 메시지로 핸드셰이크).
- (대안) SSE/HTTP MCP 서버 하나를 러너가 띄우고 codex가 URL로 붙기 — 봇별 flow 라우팅을 헤더/토큰으로.
- 도구 이름: Claude는 `mcp__guide__run`. Codex도 MCP면 같은 네임스페이스로 받음 — 프롬프트·게이트의
  도구명 문자열(tool_names.py) 재사용 가능한지 확인.

### 2. 권한 훅 → sandbox/approval 매핑
Claude PreToolUse 훅(작업공간 밖 Write 차단·협의 중 Write 차단)을 codex로:
- `--sandbox workspace-write` = 작업공간 밖 쓰기 차단(경로 경계). cwd만 writable(`-C`, `--cd`).
- '협의(Info) 중 Write 차단' 같은 **의미적** 게이트는 sandbox로 안 되니, MCP 도구 서버 쪽에서
  거부하거나(도구 호출 시점에 flow 상태 보고 deny), codex의 도구-승인 콜백으로 매핑.

### 3. 세션 resume + JSON 이벤트 파싱
- `codex exec --json`의 이벤트 스키마를 **실제로 한 번 돌려 확인**: `codex exec --json "안녕"`.
  최종 assistant 메시지·session_id·usage(cost/tokens)를 뽑는 키를 `backends.py`의 TODO③에 채운다.
- resume: codex 세션(rollout, `~/.codex/sessions/…`) 재개 형식 확인(`codex resume` 또는 exec 플래그).
  `session_exists`도 그 파일 실재로 판정.
- `truncated`(턴 한도) 대응: codex의 turn/step 상한 이벤트를 max_turns 자리에 매핑.

### 4. 프롬프트/persona
- system_prompt=CLAUDE.md는 codex에선 `AGENTS.md`가 담당(루트 AGENTS.md→CLAUDE.md 심링크 이미 생성).
  단 봇은 workspace가 cwd라, 봇 workspace에도 persona가 닿게 할지(AGENTS.md 배치 or 프롬프트 주입) 결정.
- cwd 절대경로 앵커(organt.py의 매 턴 프리픽스)는 백엔드 불가지라 그대로 유지.

## 검증
- Claude 경로: `ORGANT_BACKEND` 미설정 → 현행 동일(회귀 0). `bash ops/verify.sh` + 실 판 1개.
- Codex 경로: `ORGANT_BACKEND=codex`로 봇 하나 wake → 도구 호출(run/Write)·세션 resume·결과 반환 확인.
- 최종 e2e: 판 하나가 GOAL 회의→마일스톤→백로그→빌드까지 Codex 봇으로 완주.

## 주의
- 라이브 러너(systemd organt-runner)·인프라 수정은 **사용자 승인 후**(불변식).
- 문자열 `claude-opus-4-8`을 코드·커밋·문서에 쓰지 말 것(불변식).
