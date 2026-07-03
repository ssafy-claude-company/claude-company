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

## 세션 워크플로 (P1 완료 — 격리 검증)
- **내부 루프(빠름)**: `bash verify.sh --only <system|guide|organt|murmur>` — 자기 레포 소유 테스트 + 계약만(guide 0.5초·organt 0.35초). 다른 세션의 테스트를 안 돌리고 안 깨뜨림.
- **병합 전(통합 게이트)**: `bash verify.sh` — 전체(sns 228·system 86·pytest 455·계약·빌드). 여기서 green이어야 병합.
- 소유 분류: guide={channels,guide_queue,names,roster,discord_guide,recovery}, organt={organt,persona}, system=나머지. `test_contracts`는 모든 슬라이스에 포함.

## 세션 소유권 (P2 예정 — claim 보드)
같은 파일을 두 세션이 잡는 충돌은 `STATE.md`의 claim 섹션으로 사전 가시화(구현 예정).
