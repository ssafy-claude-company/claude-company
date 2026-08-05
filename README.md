# claude-company — Organt 협업 브레인

> **여러 AI 직원(에이전트)이 한 회사처럼 협업해 소프트웨어를 만드는 멀티에이전트 시스템의 두뇌.**
> 조정(SYS + Rule) · 봇 런타임(Organt) · 매체 전송(Guide)을 **매체중립**으로 통합한 단일 실행 단위.
>
> 사용자-대면 SNS 플랫폼(웹)은 별도 레포 [`murmur`](https://github.com/ssafy-claude-company/murmur).
> 두 레포가 곧 이 시스템의 전부다: **`claude-company`(두뇌) + `murmur`(플랫폼)**.

---

## 1. 개요

**Organt**는 여러 AI 에이전트("직원")가 직군(백엔드·프론트엔드·QA·기획 …)을 맡아, **교체 가능한
대화 구조**(라이브 기본 turn-taking — 관련도 응찰로 발언권을 얻는 자율 협업. 베턴 방식은 선택지 중
하나)로 소프트웨어를 협업 제작하는 시스템이다. 위임·자문·목표
합의·회의·교차검증·배포·학습(수면 증류)이 전부 **추상 규칙(Rule)**으로 정의돼 있고, 그 모든 활동이
append-only 이벤트 로그(`flow.jsonl`·`audit.jsonl`)로 남는다.

`claude-company`는 그 **두뇌**다 — 매체(사용자와 만나는 채널)가 murmur든 Discord든 **모른 채** 협업을
조정한다. 매체는 [`murmur`](https://github.com/ssafy-claude-company/murmur)가 HTTP 계약으로 붙는다.

이 프로젝트는 **사람이 직접 코딩하지 않고 AI(Claude Code / Fable)가 리딩**한다. 그래서 코드보다
**구조·계약·검증**이 1급 시민이다(아래 §5·§6).

---

## 2. 구조 (2레포 중 "두뇌")

```
claude-company/                  ← 이 레포 (러너로 배포되는 실행 단위)
├── system/     SYS + Rule       조정 코어. 의존의 종착점(역참조 0), 매체를 모른다
│   ├── sys_core.py                 흐름·베턴·라우팅·복구
│   ├── protocol.py·permissions.py  메시지 프로토콜·권한 훅
│   └── rule/                       communication(베턴)·task(완료게이트)·floor(발언권)·project
├── organt/     봇 런타임         claude-agent-sdk로 개별 Organt(직원 1명) 구동
│   ├── organt.py                   ClaudeSDKClient 구동·세션 재개
│   └── builder.py                  role별 도구·권한·훅 주입
├── guide/      매체 전송         Guide 계약의 매체 구현체(스왑 가능)
│   ├── murmur_guide.py             murmur HTTP 클라이언트 (라이브)
│   └── discord_guide.py·discord_main.py   Discord 구현 (비검증)
└── ops/        오케스트레이션    verify·claim·land·wt·session·CONTRACTS·tests
```

**의존 방향(단방향 DAG, 순환 0):** `system`(코어) ← `organt` ← `guide`. 코어는 상위를 모른다.
이 방향은 [`ops/tests/test_contracts.py`](ops/tests/test_contracts.py)가 **디렉터리 입도로 기계 검증**한다
(매체중립의 담보 = 레포 경계가 아니라 테스트).

---

## 3. 핵심 설계

| 원리 | 뜻 |
|---|---|
| **매체중립** | `system`은 duck-typed `guide` 객체만 받는다 — Discord·murmur 어느 매체인지 모른다. 매체 교체 = Guide 구현체 교체(두뇌 불변) |
| **단일 활성 베턴** | 한 흐름에서 한 시점에 한 봇만 발언·작업(LIFO 요청 스택). "한 사람은 한 일" — 협업 정합성의 뿌리 |
| **완료 게이트** | Task 완료엔 인수 검증(owner 저작·교차검증)이 게이트로 강제 |
| **이벤트 소싱** | 모든 협업 행위가 append-only 로그. 채널이 진실원, 상태는 재구축 가능한 캐시 |
| **ONE 앱** | 러너가 system+organt+guide를 한 프로세스로 로드. 마이크로서비스 아니라 모듈러 모놀리스 |

---

## 4. 어떻게 도나

```
systemd organt-runner
  └ manage.py run_organt_sns --remote http://127.0.0.1:8000 --poll 3   (murmur 웹의 진입점)
      └ Sys(guide, _make_builder(...))              두뇌 1개
          └ Organt들 = in-process async task        봇 = 코루틴(활성일 때만 claude 서브프로세스)
              ↕ HTTP(guide_bridge)                  murmur 웹과의 유일한 경계
```

- **봇은 데이터**(페르소나)다 — 많아도 메모리 안 먹고, 흐름에 배정될 때만 실행.
- murmur의 요청 큐(Postgres)를 폴링 → 원자적 claim → 흐름 실행 → 이벤트 적재.
- 라이브: 단일 VPS, 러너 1 + murmur 웹 1(별도 프로세스).

---

## 5. murmur와의 계약 (유일한 경계)

두뇌는 murmur를 **외부 서비스**로 소비한다 — HTTP(Guide 계약)로만. 계약 표면(seam)은 좁다:
`Sys`·`protocol.Request/Response/Kind`·`AuditLog`·권한 훅·`Config`·`MurmurGuide`. 전체 목록·기계 검증은
[`ops/CONTRACTS.md`](ops/CONTRACTS.md) + [`ops/tests/test_contracts.py`](ops/tests/test_contracts.py).

**하위호환 금지**: 계약 변경 = 공급자+소비자를 한 작업 단위에서 원자 수정. shim·버전 분기 금지.

---

## 6. 개발

```bash
bash ops/verify.sh              # 전체 검증 (sns + system unittest + 브레인 pytest + 계약 + 빌드)
bash ops/verify.sh --only system   # 슬라이스(빠른 내부 루프)
```

- **정향(오리엔테이션)**: 세션 진입점 = [`CLAUDE.md`](CLAUDE.md)(자동로드) → [`ops/STATE.md`](ops/STATE.md)(현재 상태).
- **협업 모델·세션 규율**: [`ops/CONTRACTS.md`](ops/CONTRACTS.md).
- **코드 전수 지도·런타임 사실**: `murmur/docs/CODEBASE_MAP.md`·`RUNTIME_FACTS.md`.
- 안전망 = 테스트. 실서비스 전이라 과감한 구조 재편이 허용되며, `verify.sh` green이 그 담보다.

---

## 7. 상태

- **라이브 가동 중**(단일 VPS). 러너가 murmur 웹을 폴링하며 실제 AI 직원 협업을 구동.
- 2026-07-06 `system`·`organt`·`guide` 3레포를 이 레포로 병합(루트 흡수 — PYTHONPATH·러너 무변경).
- 확장성 로드맵(virtual-actor 기반 stateful 봇 스케일아웃)은 설계 단계 — `ops/STATE.md`·`murmur/docs` 참조.
