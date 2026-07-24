# 작업 세션 — 현재 설정 & 복구 (혼란 방지)

> 세션이 헷갈리거나 끊기면 여기 보고 복구. **웹 랜덤이름(lazy-honey 등)은 무시**하고 아래 URL/pwd로 식별.

## 사람용 GPT/Codex — 공식 Remote SSH

- 로컬 컴퓨터가 Linux이므로 기본 진입은 murmur의 임시 웹 CLI가 아니라 **Codex CLI 원격 TUI →
  SSH 터널 `murmur-vps` → `/root/ClaudeCompany`**다. ChatGPT 데스크톱 Remote 호스트는
  macOS/Windows에서만 쓴다.
- 서버는 Codex 로그인·비대화형 PATH·영속 app-server Unix socket·Linux용 loopback
  `127.0.0.1:4500` 서비스·GPT-5.6-Luna/max까지 준비됨. 4500은 공인망에 열지 않는다.
- 로컬 CLI 설치·SSH 키/터널·데스크톱 대안, 검증 및 기존 링크 퇴역 게이트는
  [`CODEX_REMOTE.md`](CODEX_REMOTE.md)가 정본이다.
- 이 연결은 아래 Fable/Claude 세션과 Organt 내부 GPT 브리지(`127.0.0.1:8791`)를 대체하지 않는다.

## Claude/Fable 현재 세션 (2개, 고정)
| 이름 | worktree | tmux | 웹 접속 |
|---|---|---|---|
| **변도진** | `/root/wt/ClaudeCompany-변도진` | `tmux attach -t 변도진` | env URL → 목록에서 **ClaudeCompany-변도진** |
| **이현준** | `/root/wt/ClaudeCompany-이현준` | `tmux attach -t 이현준` | 같은 env URL → **ClaudeCompany-이현준** |

- 둘 다 **Opus4.8 · max effort · auto 승인** (web-spawn 세션은 기본값으로 뜰 수 있으니 접속 후 `/model opus`·`/effort max` 확인).
- **웹 URL은 고정이 아니다** — remote-control 재기동 시 env URL이 새로 발급된다(이 VPS 한 대 = 한 environment, 그 안에서 **이름으로 선택**). URL은 해당 tmux pane에서 확인: `tmux capture-pane -t 변도진 -p -J | grep -oE 'https://claude.ai/code[^ ]*'`.
- 어느 세션인지 확인: 그 세션에 **`pwd`** → 경로가 이름을 알려줌.
- **제일 안 헷갈림 = 터미널** `tmux attach -t 변도진`(이름 그대로 뜸). 웹은 이름으로 식별.

## 규율 (이게 안정의 핵심)
- **켠 뒤 세션을 안 건드린다.** kill·세션파일 이동·remote-control 재실행 = 다리 끊김의 원인. 한 번 켜고 냅두면 안정적.
- 나올 때 **Ctrl+B → D**(detach). **Ctrl+C 금지**(세션 죽음).

## 복구 (죽거나 끊겼을 때)
| 증상 | 복구 |
|---|---|
| 웹만 "응답 멈춤"(세션은 삼) | 그 tmux에서 `/remote-control` 한 번(새 URL 나옴). 세션은 안 죽었음 — `tmux attach -t 변도진`으로 확인 |
| tmux 세션 죽음(재부팅 등) | 아래 **재기동 스니펫**(기존 worktree 재사용). **⚠️ `ops/session.sh 변도진` 쓰지 마라** — 이름을 `/root/wt/변도진`(신규)로 매핑해 기존 `ClaudeCompany-변도진`을 안 쓰고 빈 worktree를 새로 판다(2026-07-08 실사고). |
| 세션 다 날아감 | 아래 스니펫을 변도진·이현준 각각. worktree는 `/root/wt/ClaudeCompany-<이름>`에 남아있음(작업 보존) |

**재기동 스니펫** (`<이름>`=변도진 또는 이현준):
```bash
N=변도진; W=/root/wt/ClaudeCompany-$N
tmux new-session -d -s "$N" "cd '$W' && MURMUR_ROOT='$W' claude remote-control --spawn same-dir --permission-mode auto --name '$N'; exec bash"
# URL 확인: tmux capture-pane -t "$N" -p -J | grep -oE 'https://claude.ai/code[^ ]*'
```
- **원리**: `session.sh`는 worktree 이름=`/root/wt/<T>`로 강제해서 `T=변도진`이면 신규 worktree를 판다. 기존 세션 복구는 **worktree 경로를 직접 지정**해 remote-control을 띄워야 한다.

## Fable 워커 세션 (Fable-세션2에서 포크한 6개 — 이현준-1~3 · 변도진-1~3)
| 이름 | worktree/브랜치 | 자기 세션ID | tmux |
|---|---|---|---|
| **이현준-1** | `/root/wt/이현준-1` `s/이현준-1` | `d39dd393` | `tmux attach -t 이현준-1` |
| **이현준-2** | `/root/wt/이현준-2` `s/이현준-2` | `c83fd0fa` | `tmux attach -t 이현준-2` |
| **이현준-3** | `/root/wt/이현준-3` `s/이현준-3` | `da4ba956` | `tmux attach -t 이현준-3` |
| **변도진-1** | `/root/wt/변도진-1` `s/변도진-1` | `b5cfc899` | `tmux attach -t 변도진-1` |
| **변도진-2** | `/root/wt/변도진-2` `s/변도진-2` | `475724d5` | `tmux attach -t 변도진-2` |
| **변도진-3** | `/root/wt/변도진-3` `s/변도진-3` | `9750db36` | `tmux attach -t 변도진-3` |

- 전부 **Fable 모델(claude-fable-5) · max effort · RC**. (사람 genesis 세션 `이현준`·`변도진`(번호 없음)과는 별개 — 이건 Fable 워커 복제본.)
- **최초 생성**: `Fable-세션2`(e4414f9b) → `claude --resume … --fork-session --model fable --effort max`(맥락 복제, 원본 불변) + 각자 git worktree + `--remote-control`.
- **재기동**: **`bash ops/fable.sh <이름|all>`** — 각자 **자기 세션ID로 resume**(재포크 금지 — 재포크하면 누적작업 소실) + **`--model fable --effort max` 고정** + RC. 살아있는 건 스킵.
- **기본설정(모델·effort)은 launch 플래그로 박혀 있다**(`ops/fable.sh`). `/model`·`/effort`는 런타임만 바꿔 재시작 시 상속값으로 돌아가므로, 영구 고정은 이 스크립트가 담당. 세션ID 전체는 `ops/fable.sh`의 `SID` 맵 참조.
- Korean 경로는 `.claude` 프로젝트 디렉터리에서 충돌(이현준-N·변도진-N 동수 → 같은 디렉터리)하지만, resume를 **명시 세션ID**로 하므로 조회 모호성 없음.

## 착지 (작업 반영)
각 세션이 스스로: **`bash ops/land.sh <이름>`** → 자기 브랜치를 정본 병합+전체검증(통합 세션 불필요). 라이브 재시작만 사용자 승인.

## 보고 (사용자에게)
**모든 세션은 [`ops/REPORTING.md`](REPORTING.md) 규율로 보고한다** — 보고=위임의 반환값([결과]/[확인]/[결정]/[의미]/[미검증]/[다음]), 구현 디테일 본문 금지, '검증됨'은 실 실행+관측+열람 위치가 있을 때만.

## 소스 변경 규율 (2026-07-05, 사용자 지시 — "무지성 커밋 금지, CA-Lab처럼")
- **작업 브랜치**(`docs/`·`feat/`·`refact/` — CA-Lab RFC-002 3종) → **원자 커밋**(1커밋=1변경,
  `type(scope): 제목`+한국어 본문 — 그 레포 히스토리 컨벤션을 먼저 보고 맞출 것) →
  **GitHub draft PR + 검수 동선 인라인**(REPORTING §1-1 `[검수 n/총]`) → 사용자 병합.
- 기록 문서(실험·조사 원문)는 **원문 불변 — 개정은 새 문서+주석 연결**(CA-Lab experiments 규율).
- **리뷰 요청은 사용자가 확인할 것이 있을 때만**(2026-07-08 교정: "나한테 보여줄 게 없으면 왜
  리뷰 요청하는거야"). 직접 지시 작업 = `land.sh` 직행이 기본, 코드 보증은 세션 몫(테스트·실기동).
  PR을 여는 경우 = 방향·제품 판단이 실제로 필요한 것뿐.
- **라이브 반영(정본 병합·웹/러너 재시작·배포)은 하네스가 사람 승인을 요구한다** — 자동 모드
  분류기가 세션의 자체 병합·재시작을 차단하며, 차단을 다른 각도로 우회하려는 시도 자체가 위반으로
  판정된다(2026-07-08 실측). 승인 한마디를 받고 실행하는 것이 정상 경로다.
