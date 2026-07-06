# system — SYS + Rule 코어 (에이전트 안내)

## 역할

Organt 플랫폼의 **코어 레포**: SYS(중앙통제 `sys_core.py`) + Rule(추상 규칙 `rule/`) + 도구셋(`guide_tools.py`).
**상위 레이어가 이 레포에 의존하고, 이 레포는 아무에게도 의존하지 않는다(역참조 0):**

```
system(코어)  ←  organt(봇 런타임) · guide(전송기) · murmur(SNS 웹)
```

- **상위 레이어 import 금지.** `system/` 코드에서 `organt`·`guide`·`murmur`를 import하지 않는다.
  매체 구현(Discord/murmur 전송기)은 guide 레포 소관 — 여기는 매체-중립 계약(`protocol.py`)만.
- 모듈 구성·목적은 [`__init__.py`](__init__.py) docstring이 1차 문서다(전 `.py`에 한국어 docstring).

## 4레포 토폴로지 · PYTHONPATH

배포 체크아웃 루트 `/root/ClaudeCompany/`에 4레포가 나란히 클론돼 있고(각자 `.git`),
크로스-레포 임포트(`from system.X`)는 pip 패키징 없이 **PYTHONPATH=/root/ClaudeCompany** 으로 해결한다.

```
/root/ClaudeCompany/
  system/   organt/   guide/   murmur/    ← 4레포 (이 파일이 있는 곳이 system)
  .venv/                                  ← 러너 전용 venv
  organt_sns_state/  organt_sns_workspace/ ← 런타임 상태·작업공간 (건드리지 말 것)
```

## ⚠️ 라이브 경고

**이 체크아웃(`/root/ClaudeCompany/system/`)이 라이브 러너(systemd `organt-runner`)의 import 소스다.**
편집 즉시 다음 러너 재시작에 반영된다. **systemd 유닛·`/etc`·nginx·env 파일은 절대 수정 금지.**
배포·서비스 재시작·push는 담당(메인) 승인 하에만.

## 검증 — 단일 진입점

```bash
bash /root/ClaudeCompany/ops/verify.sh                  # 전체(착지 게이트) — sns·unittest·브레인 pytest·빌드·STATE 신선도
bash /root/ClaudeCompany/ops/verify.sh --only system    # 내부 루프 — system 슬라이스 + 계약 테스트만(빠름)
```

브레인 pytest 스위트 본체는 메타레포 `ops/tests/`에 있고 **실 system/guide/organt를 직접 검증**한다
(단일 진실원). *(종전 이 절의 "PJT pytest·venv에 pytest 없음" 안내는 2026-07-04 폐기 — PJT는
`/root/_archive`로 은퇴했고 메인 venv에 pytest가 설치돼 있다. 현재 상태는 항상 `ops/STATE.md`.)*

## 사실 문서 (murmur 레포 docs)

- [코드베이스 전수 지도](../murmur/docs/CODEBASE_MAP.md) — 파일 인벤토리·의존 방향·토폴로지
- [런타임 사실](../murmur/docs/RUNTIME_FACTS.md) — 라이브 배포·테스트 실행 사실
