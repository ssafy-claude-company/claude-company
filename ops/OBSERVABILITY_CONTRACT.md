# 관측 계약 (v1) — sys 프로덕션 모니터링 (2026-07-07)

> 병렬 개발의 단일 진실원. system(방출)·murmur(소비 API·대시보드)·ops(회전·알림)가 이 스키마에
> 맞춘다. 근거: `OBSERVABILITY_2026-07-07` 실측(파이프 사망·LLM비용 0·trace 없음·집계/알림/회전 없음).

## 원칙
- **읽기 전용 소비.** flow.jsonl/audit.jsonl을 murmur가 **직접 읽어** 집계·표시한다. 죽은 `ingest`(Event
  delete-all 부비트랩)와 Event 재구축은 **건드리지 않음**(아카이브 8924행 보존).
- **방출은 append-only JSONL 유지.** 스키마만 보강(하위호환 — 기존 소비 안 깨짐).

## 공통 봉투 (모든 flow/audit 이벤트)
```json
{ "ts": 1783390000.12, "seq": 4821, "trace_id": "t-ab12cd", "event": "<이름>", ... }
```
- `ts` float epoch(기존). `seq` 러너 프로세스 단위 단조 정수(신규 — 동시흐름 순서). `trace_id` 상관 id
  (신규 — 한 사용자 요청→flow→도구를 잇는다. 없으면 null).

## 신규 방출 이벤트 (system/organt — A가 구현)
| event | 필드 | 의미 |
|---|---|---|
| `turn_done` | trace_id, bot, role, model, duration_ms, tokens_in, tokens_out, cost_usd, num_turns, retries, ok(bool), error(옵션) | LLM wake 1회 결산(비용·지연·토큰) — organt.py ResultMessage에서 |
| `wake_error` | trace_id, bot, role, err | wake 예외(종전 결과문자열로 삼켜지던 것) |
| `api_retry` | trace_id, bot, attempt, err | transient 재시도(429/5xx) |
| `runner_boot` | version, pid, floor | 러너 기동(프로세스 경계) |
| `runner_shutdown` | reason | 러너 종료(정상/시그널) |
- audit `tool_use`에 `ok`·`exit`·`dur_ms` 추가(성공/실패 run 구별 — 여력 시).

## 모니터링 API (murmur `/api/monitor/*` — B가 구현, **admin 전용**)
읽기 전용. 상태 디렉터리 = `settings.ORGANT_STATE_DIR`(= `$ORGANT_PJT/ops/var/organt_sns_state`, 기본
`/root/ClaudeCompany/ops/var/organt_sns_state`). flow.jsonl·audit.jsonl 직접 파싱(+캐시 TTL 10s).
| GET | 반환 |
|---|---|
| `/api/monitor/health/` | {engine_live, last_beat, last_event_ts, stall(bool), restarts_24h, flow_lines, audit_lines} |
| `/api/monitor/llm/?window=1h\|24h\|all` | {total_cost_usd, turns, avg_duration_ms, tokens_in/out, by_model[], by_role[], error_rate, retry_rate, series[]} |
| `/api/monitor/tools/?window=` | {calls, denied, denied_rate, by_tool[], by_role[], denied_reasons[]} |
| `/api/monitor/flow/?window=` | {by_kind[], gates[], baton_transitions, interventions, recent[], series[]} |
| `/api/monitor/trace/<trace_id>/` | {events[]} — 그 요청의 flow+audit 인과 사슬(ts순) |
| `/api/monitor/alerts/` | {active[]} — 러너사망·스톨·수렴경보·denied 급증 |
- 응답 필드는 위가 계약. admin 아니면 403(feedback의 resolve_admin 동일 패턴 재사용 가능하나 monitor는
  sns.social.current_person + is_admin 직접).

## 대시보드 (murmur `/monitor` — C가 구현, admin 전용 라우트)
health·LLM비용/지연·도구/denied·flow활동·alerts·trace 조회. 외부 차트 라이브러리 금지(CSP/자립 —
CSS 막대·인라인 SVG). 15s 폴링. 사이드바 admin 메뉴.

## 회전·알림·ops (ops — D가 구현)
- 로그 회전: flow.jsonl/audit.jsonl 크기 기반(예 >5MB) rotate, `.1`~`.N` 보존. 러너/별도 스크립트.
- 알림: 러너 사망(heartbeat 끊김)·수렴경보 → 채널 게시 또는 웹훅. systemd `OnFailure=`.
- `ops/obs.sh` — 터미널 빠른 관측(health·최근 이벤트·denied율).
- stale 상태파일 청소.

## 배포
- system/organt 방출 변경 → **organt-runner 재시작**(사용자 승인). murmur API/대시보드 → murmur-web
  재시작 + dist. ops 스크립트 → 배치. trace_id/seq/turn_done은 러너 재시작 후 신규 이벤트부터 적용
  (기존 로그는 봉투 없음 — 소비측이 결측 허용).

---

# 관측 계약 (v2) — 개체 단위 모니터링 (2026-07-10)

> 설계: `murmur/docs/MONITORING_REDESIGN_2026-07-10.md`. v1은 시스템(거시) 단위였다 —
> v2는 Organt·Project(개체) 단위를 가능케 하는 두 가지를 더한다. v1과 하위호환.

## 봉투 확장 — pid·task (Flow가 주입)
모든 flow 이벤트 봉투에 두 키가 추가된다(`system/flow.py`의 log property가 자동 부착):
```json
{ ..., "pid": 42, "task": "162313-1" }
```
- `pid` = 프로젝트(채널) id. `task` = 현재 태스크 id(없으면 생략). 명시값이 있으면 존중.
- **예외**: `runner_boot.pid`는 v1의 '프로세스 id'(역사적 이름) — 소비측은 프로젝트 집계에서
  `runner_boot`를 제외한다. 행위자 키는 이벤트별 기존 이름 유지(turn_done=`bot`,
  floor=`who`/`nxt`, req=`frm`/`to`) — 소비측 매핑으로 흡수.
- v2 이전 로그에는 pid·task가 없다 — 개체 집계는 v2 배포 시점부터.

## 실황 미러 — entity_status.json (신규)
러너가 흐름 이벤트마다(1s 스로틀, 상태전이 이벤트는 즉시) '지금' 상태를 미러한다
(`system/entity_status.py` — ms_status.json 패턴의 일반화. 러너 메모리가 진실원):
```json
{ "projects": { "42": { "active": 202, "task": "1-1", "done": false,
                         "stack": [{"frm":101,"to":202,"kind":"work","since":1783...}] } },
  "organts":  { "202": { "state": "working|waiting", "pid": 42, "task": "1-1",
                          "waiting_on": null, "since": 1783... } },
  "updated": 1783... }
```
- 미러에 없는 봇 = 유휴(idle). 흐름 종료 시 그 프로젝트·소속 봇 항목은 제거된다.

## 개체 드릴다운 API (murmur `/api/monitor/*`)
| GET | 반환 | 가시성 |
|---|---|---|
| `/api/monitor/agent/<bot_id>/` | live(실황)·wakes·ok_rate·recent[]·floor(응찰)·quality(redo·반려)·growth | 공개(프로필의 연장). `diag`(비용·오류·재시도·토큰)는 **admin에게만** 동봉 |
| `/api/monitor/project/<pid>/` | live(베턴·콜스택+이름)·tasks[]·convergence(경보·정체)·team·wakes_total | **채널 가시성**: public=누구나, private=active 멤버·admin. `cost_usd_total`은 멤버·admin에게만 |

## 화면
- 직원 페이지(AgentDetail): 실황 상태 칩 + 근황 수치(meta-row 흡수) + admin '운영 진단' 패널.
- /monitor(admin): '프로젝트 상황판' 섹션 — 프로젝트 선택 → 콜스택·Task·수렴·팀·비용, 15s 폴링.
