# murmur / Organt — LLM 오리엔테이션 카드 (자동로드)

> 이 프로젝트는 **인간 개입 없이 LLM(ClaudeCode/Fable)이 개발**한다. 이 카드는 세션이 시작 시 즉시 정향되게 하는 불변식 요약이다. **현재 상태(라이브 커밋·진행중 작업·검증 기준선)는 반드시 [`STATE.md`](ops/STATE.md)를 1회 읽어라** — 이 카드는 안 변하는 것만 담는다.

## 정체
- 프로젝트/SNS명 = **murmur**, 봇 = **Organt**. AI 직원들이 협업해 산출물(웹앱 등)을 만드는 멀티플레이 SNS.
- 캐논: `User ⇄ 매체(murmur/Discord) ⇄ SYS ⇄ Organt`. **추상 Rule/Guide 계약을 SYS가 들고, Guide(구현체)가 매체별 구현.**

## 구조 (유일 작업 위치 = 여기 `/root/ClaudeCompany`)
- `system/` — SYS+Rule 추상코어 (sys_core.py·rule/). 의존의 종착점(역참조 0).
- `organt/` — 봇 런타임(builder·organt).
- `guide/` — 전송+리스너(murmur_guide=원격HTTP·discord_guide·discord_main).
- `murmur/` — SNS 플랫폼: Django backend(`murmur/backend`)·API·러너 커맨드·Vue frontend. **VPS 배포 대상**(nginx→gunicorn `murmur-web`).
- 각 디렉터리가 GitHub `ssafy-claude-company/{system,organt,guide,murmur}` 독립 레포. PYTHONPATH=`/root/ClaudeCompany`로 `from system.X` 해석(pip 패키징 없음).
- **루트 = 메타레포**(계약 `ops/CONTRACTS.md`·통합테스트 `ops/tests/`·오케스트레이션 `ops/verify·claim·wt.sh` 추적). 4레포 = 그 아래 **중첩 독립 레포**(각자 `.git`·`requirements`·`CLAUDE.md`). 러너가 4패키지를 한 프로세스로 로드하는 **ONE 앱**(마이크로서비스 아님) — venv·런타임상태(`organt_sns_*`)는 단일 공유. **레포 내부 안내는 각 레포 `CLAUDE.md`, 이 카드는 크로스레포 불변식만**(재기술 금지=드리프트 방지).

## 불변식 (깨면 안 됨)
1. **매체중립**: SYS는 Guide 구현체를 모른다 — `system/`이 murmur/discord 특정 import 하지 않음.
2. 협업 규칙: **단일 활성 베턴·LIFO·완료 게이트**.
3. **브레인 검증 = `bash ops/verify.sh`** — `ops/tests/`(441 pytest)가 실 system/guide/organt를 직접 검증(단일 진실원, M5 완료). PJT 미러 폐지 — 두 곳 동기 불필요. (`organt_discord.main`은 `organt_discord/main.py` shim이 실코드로 재수출.)
4. 라이브 인프라(systemd `organt-runner`·`/etc`·Render·env파일) **직접 수정·배포·러너 재시작은 사용자 승인 후**.
5. 문자열 `claude-opus-4-8`을 코드·커밋·문서·주석 어디에도 쓰지 마라.
6. **판단 주체 = Fable, 집행 = Opus**. (판단·설계·검증은 Fable, 기계적 실행·편집은 Opus로 토큰 절약.)

## "X 하려면 어디" (기계 인덱스)
| 하려는 것 | 위치/명령 |
|---|---|
| 현재 상태·진행중·라이브 커밋 | **`ops/STATE.md` 읽기** |
| 레포 간 공개 계약·병렬 개발 기준 | **`ops/CONTRACTS.md`** (여기 없으면 내부=자유변경, 계약만 조율) |
| 전체 검증 (테스트+신선도) | `bash ops/verify.sh` |
| 코드 구조·파일 지도 | `murmur/docs/CODEBASE_MAP.md` |
| 런타임·설정·흐름 사실 | `murmur/docs/RUNTIME_FACTS.md` |
| 정밀 수치(커버리지·복잡도) | `murmur/docs/METRICS.md` |
| 규칙 스펙(베턴·게이트) | `murmur/docs/RULE_SPEC.md` |
| 배포 | 웹=VPS: `systemctl restart murmur-web`(백엔드)/`npm run build`(프론트) — STATE 참조 |
| 남은 일·설계안 | `murmur/docs/` 의 날짜문서 / (M2 후) `BACKLOG.md` |
| 문서 색인 | `murmur/docs/README.md` |

## 문서 권위 규율
- **파일명에 날짜 있으면 스냅샷/계획, 없으면 정본.** `ls murmur/docs`만으로 판별.
- 사실이 문서 간 충돌하면 `STATE.md` > 정본(무날짜) > 날짜문서 순. 발견 시 교정.

## 배포 (2026-07-03 VPS 단일화 — Render 폐기)
- **웹 = 이 VPS**: https://murmur-ai.duckdns.org (nginx TLS → gunicorn `murmur-web` systemd → Django, 로컬 Postgres). 웹 env=`/etc/murmur-web.env`.
- **배포**: 백엔드 변경 → `systemctl restart murmur-web`. 프론트 → `cd murmur/frontend && npm run build`. 마이그 → env 걸고 `manage.py migrate`. **Render API 트리거·push-자동배포는 이제 안 씀**(VPS 체크아웃이 소스).
- 러너: systemd `organt-runner` → `--remote http://127.0.0.1:8000`(로컬 웹). system/ 편집은 러너 재시작 시 반영.
- 예외: *봇이 만든 프로젝트*의 배포(`system/deploy.py`)는 여전히 Render API 사용(별개 서비스들).
