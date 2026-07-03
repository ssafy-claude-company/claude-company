# CLAIMS — 세션 파일 점유 보드 (P2, 중첩 사전 가시화)

> **중첩(같은 파일 편집)을 병합 전에 보이게 한다.** 계약([`CONTRACTS.md`](CONTRACTS.md))이 *seam* 충돌을 막는다면, 이 보드는 *내부 파일* 충돌을 사전 가시화한다.
> 도구: `bash claim.sh add <세션> <glob...>` / `release` / `list` / `check <세션>`.
> 세션 시작 시 자기 작업 영역을 `add`, 커밋/병합 전 `check`로 남의 점유와 겹치는지 확인, 끝나면 `release`.

## 현재 점유
| 세션 | 점유(glob) | since |
|---|---|---|
| S-core | system/* | 2026-07-03 23:31 |
| S-core | system/rule/* | 2026-07-03 23:31 |
| S-core | system/tests/* | 2026-07-03 23:31 |
| S-edge | guide/* | 2026-07-03 23:31 |
| S-edge | organt/* | 2026-07-03 23:31 |
| S-edge | organt_discord/* | 2026-07-03 23:31 |
| S-app | murmur/* | 2026-07-03 23:31 |
