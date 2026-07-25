# CLAIMS — 세션 파일 점유 보드 (P2, 중첩 사전 가시화)

> **중첩(같은 파일 편집)을 병합 전에 보이게 한다.** 계약([`CONTRACTS.md`](CONTRACTS.md))이 *seam* 충돌을 막는다면, 이 보드는 *내부 파일* 충돌을 사전 가시화한다.
> 도구: `bash claim.sh add <세션> <glob...>` / `release` / `list` / `check <세션>`.
> 세션 시작 시 자기 작업 영역을 `add`, 커밋/병합 전 `check`로 남의 점유와 겹치는지 확인, 끝나면 `release`.

## 현재 점유
| 세션 | 점유(glob) | since |
|---|---|---|
| 변도진-1 | murmur/docs/** | 2026-07-08 06:06 |
| 변도진-1 | guide/discord* | 2026-07-08 06:06 |
| 변도진-1 | guide/channels.py | 2026-07-08 06:06 |
| 변도진-1 | ops/organt_discord/** | 2026-07-08 06:06 |
| 변도진-1 | system/protocol.py | 2026-07-08 06:06 |
| 변도진-1 | organt/builder.py | 2026-07-08 06:06 |
| 이현준-2 | system/flow.py | 2026-07-09 02:36 |
| 이현준-3 | system/rule/wrapup* | 2026-07-09 03:18 |
| 이현준-2 | system/rule/task.py | 2026-07-09 03:22 |
| 이현준-2 | system/rule/task_pipeline.py | 2026-07-09 03:22 |
| 변도진-2 | system/atelier_client.py | 2026-07-13 09:45 |
| 변도진-2 | system/tool_names.py | 2026-07-13 09:45 |
