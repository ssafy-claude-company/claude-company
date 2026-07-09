# CONTRACTS — 레포 간 공개 계약 (병렬 개발의 기준)

> **이것이 4레포(system·organt·guide·murmur)의 *유일한* 공개 계약이다.** 여기 없는 것은 전부 **내부(internal) = 자유 변경**.
> 실행가능 형태·기계 검증은 [`tests/test_contracts.py`](tests/test_contracts.py)(`bash verify.sh`가 돌림). 이 문서와 그 파일은 동기 유지.

## 왜 이게 병렬 개발을 가능하게 하나 (Fable 판정 B)
두 세션이 어느 기능을 나누든:
- **내부는 자유** — 아무 파일/함수나 리팩터·고도화. 다른 세션에 영향 0(계약만 안 건드리면).
- **계약은 봉쇄** — seam은 아래 17개뿐. 넓히려면 manifest에 추가해야 하고(테스트가 강제), 그때만 소비자와 조율.
- **버전 없음(하위호환 금지)** — 외부 소비자 0·배포 대상 1(단일 VPS)이라 버전 공존 수요가 없다. shim·deprecated·버전 분기 **금지**.

## 하위호환 금지 규약
> **계약 변경 = `tests/test_contracts.py`의 `CONTRACT` 갱신 + 전 소비자를 *같은 작업 단위*에서 수정.**
> shim·deprecated 별칭·구/신 버전 분기 코드를 남기지 마라. LLM 세션은 공급자·소비자를 한 흐름에 원자적으로 고칠 수 있으므로 버저닝은 순수 비용이다.
> "주변 최신화 추종"은 SHA 스냅샷 갱신(P3)이 담당 — 작업 중엔 이웃 고정, 갱신 시점에 HEAD로 따라감.

## 공개 계약 17개 (2026-07-03 실측 seam)
의존은 전부 `system`으로 수렴(역참조 0 DAG). 상위→하위 단방향, 순환 0.

| 계약 진입점 | 공급 | 소비 |
|---|---|---|
| `system.sys_core.Sys` | system | guide·murmur |
| `system.sys_core.load_personas` / `save_personas` | system | guide / murmur |
| `system.protocol.Kind·Request·Response·parse` | system | guide·murmur |
| `system.protocol.Marker` | system | organt |
| `system.audit.AuditLog` | system | guide·murmur·organt |
| `system.audit.make_post_tool_use_hook` | system | organt |
| `system.permissions.make_pre_tool_use_hook` | system | organt |
| `system.config.Config·ROOT·load_config` | system | organt·guide·murmur |
| `system.tool_names.FLOW_TOOLS·LEADER_TOOLS` | system | organt |
| `organt.builder._make_builder` | organt | guide·murmur |
| `guide.murmur_guide.MurmurGuide` | guide | murmur |

## 계약을 바꾸려면
1. 필요한 이름을 `tests/test_contracts.py`의 `CONTRACT`에 추가/수정(미선언 import는 테스트가 FAIL로 잡음).
2. 공급 레포에서 그 이름을 노출, 소비 레포들을 **같은 커밋/작업 단위**에서 맞춤.
3. 이 표도 갱신. `bash verify.sh` 통과 확인.

## 협업 모델 (Fable 판정 — task 단위 full-context)
> **이건 ONE 앱이고 기능은 횡단적이라, 기본은 레포별 격리가 아니라 "작업(task)당 full-context 세션"이다.**
> 각 세션 = 하나의 task, **전체 트리 편집권**. 분할 축은 레포가 아니라 **task + claim(파일 표면)**.
> per-repo 1:1 편성은 폐기(횡단 기능을 느린 경로로 보내는 역설계 — LLM 세션엔 소유권 상각도 없음).

**세션 워크플로:**
1. **시작**: `claim.sh add <task-이름> <실제 만질 파일 glob...>` — 레포 통짜가 아니라 *이 task의 실제 파일 표면*을.
2. **개발**: 전 트리에서 자유 편집(계약 17 seam만 지키면 됨). 내부 루프 = `verify.sh --only <만진 레포들>`(빠름).
3. **착지 전**: `claim.sh check <task>` (남과 중첩?) + **`ops/STATE.md`(+영향받은 docs) 갱신** + **full `verify.sh` green**.
   - ⚠ **문서 갱신은 반필수**: 코드만 바꾸고 STATE 안 고치면 `verify.sh` 5단계(신선도)가 **FAIL로 잡는다**. 스탬프·기준선·영향받은 정본(RUNTIME_FACTS 등) 함께 갱신.
4. **착지**: 통합 세션의 착지 큐로(아래). 끝나면 `claim.sh release <task>`.

- **동시 세션 상한 ≈ 2~3.** full-context에선 claim 중첩 확률이 세션 수로 빠르게 커짐 → 스케일은 세션 증설이 아니라 **task 큐잉**.
- **계약 변경**: full-context라 **공급자+전 소비자를 한 세션에서 원자적으로** 수정(per-repo가 못 하던 것). manifest도 같이 갱신.

## 착지 = 세션이 스스로 (통합 세션 불필요)
각 세션이 자기 작업을 정본에 **직접 착지**한다 — 별도 통합 세션에 의존하지 않는다:
- **`bash ops/land.sh <세션>`** = 자기 브랜치(s/<세션>)를 정본 main에 병합 + STATE 스탬프 + **full `verify.sh`**.
- **동시 착지는 flock으로 직렬화**(`ops/.land.lock`) — 두 세션이 동시에 land해도 한 줄로 처리, race 없음.
- 병합 충돌 시 멈추고 수동 해결 안내(그 세션이 해결 후 재실행).
- 라이브 반영(서비스 재시작)만 사용자 승인.
- (계약 변경·claim 분쟁 같은 크로스 조율이 필요할 때만 사람이 개입. 상시 통합 세션은 불요.)

## worktree 격리 = opt-in 도구 (기본 아님)
`ops/wt.sh`는 이럴 때만: (a) 레포-로컬 대량 churn(프론트 UI 스프린트·내부 대리팩터), (b) 장수 실험 브랜치, (c) 두 세션이 같은 레포를 동시에 만져 git 격리가 필요할 때. 그 외 기본은 **정본 트리 직접 작업 또는 full-own 스택**(`wt.sh new <task> system organt guide murmur`).

## 담보 (변경 없음)
- **claim 보드**(`claim.sh`/CLAIMS.md) = 1차 분할·충돌 사전가시화.
- **계약 17 seam**(`test_contracts`) = 의존방향·seam예산·미선언 import 차단(아키텍처 규율).
- **verify.sh** = `--only` 빠른 내부 루프 / full = 착지 게이트.
