# 작업 세션 — 현재 설정 & 복구 (혼란 방지)

> 세션이 헷갈리거나 끊기면 여기 보고 복구. **웹 랜덤이름(lazy-honey 등)은 무시**하고 아래 URL/pwd로 식별.

## 현재 세션 (2개, 고정)
| 이름 | worktree | tmux | 웹 URL |
|---|---|---|---|
| **변도진** | `/root/wt/ClaudeCompany-변도진` | `tmux attach -t 변도진` | `https://claude.ai/code/session_01LxLT5czZFP2JNVHWdpcEmj` |
| **이현준** | `/root/wt/ClaudeCompany-이현준` | `tmux attach -t 이현준` | `https://claude.ai/code/session_01HKuXgXgXE1QqDC7nms3Wkj` |

- 둘 다 **Opus4.8 · max effort · auto 승인**.
- 어느 세션인지 확인: 그 세션에 **`pwd`** → 경로가 이름을 알려줌.
- **제일 안 헷갈림 = 터미널** `tmux attach -t 변도진`(이름 그대로 뜸). 웹은 URL로만 식별.

## 규율 (이게 안정의 핵심)
- **켠 뒤 세션을 안 건드린다.** kill·세션파일 이동·remote-control 재실행 = 다리 끊김의 원인. 한 번 켜고 냅두면 안정적.
- 나올 때 **Ctrl+B → D**(detach). **Ctrl+C 금지**(세션 죽음).

## 복구 (죽거나 끊겼을 때)
| 증상 | 복구 |
|---|---|
| 웹만 "응답 멈춤"(세션은 삼) | 그 tmux에서 `/remote-control` 한 번(새 URL 나옴). 세션은 안 죽었음 — `tmux attach -t 변도진`으로 확인 |
| tmux 세션 죽음(재부팅 등) | `bash ops/session.sh 변도진` 로 재생성(worktree 재사용, Opus·max·auto·정향 자동) |
| 세션 다 날아감 | `bash ops/session.sh <이름>` 2번(변도진·이현준). worktree는 `/root/wt/`에 남아있음 |

## 착지 (작업 반영)
각 세션이 스스로: **`bash ops/land.sh <이름>`** → 자기 브랜치를 정본 병합+전체검증(통합 세션 불필요). 라이브 재시작만 사용자 승인.

## 보고 (사용자에게)
**모든 세션은 [`ops/REPORTING.md`](REPORTING.md) 규율로 보고한다** — 보고=위임의 반환값([결과]/[확인]/[결정]/[의미]/[미검증]/[다음]), 구현 디테일 본문 금지, '검증됨'은 실 실행+관측+열람 위치가 있을 때만.

## 소스 변경 규율 (2026-07-05, 사용자 지시 — "무지성 커밋 금지, CA-Lab처럼")
- **작업 브랜치**(`docs/`·`feat/`·`refact/` — CA-Lab RFC-002 3종) → **원자 커밋**(1커밋=1변경,
  `type(scope): 제목`+한국어 본문 — 그 레포 히스토리 컨벤션을 먼저 보고 맞출 것) →
  **GitHub draft PR + 검수 동선 인라인**(REPORTING §1-1 `[검수 n/총]`) → 사용자 병합.
- 기록 문서(실험·조사 원문)는 **원문 불변 — 개정은 새 문서+주석 연결**(CA-Lab experiments 규율).
- `land.sh` 직행(main 직병합)은 사용자가 흐름 중 직접 지시한 작업에 한함 — 그 외 기본은 PR 게이트.
