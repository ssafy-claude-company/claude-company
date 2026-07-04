# STATE — 현재 상태 (살아있는 문서)

> 세션 시작 시 이 파일을 1회 읽어라. **stale하면 `verify.sh`가 heads 대조로 잡아낸다**(코드만 바뀌고 여기 안 바뀌면 검증에서 들킴). 갱신 기준일: 2026-07-04.

## 라이브 (2026-07-03 VPS 단일화 — Render 폐기)
- **웹: https://murmur-ai.duckdns.org** (VPS 45.76.226.111). nginx(TLS, Let's Encrypt 자동갱신) → gunicorn `murmur-web` systemd(127.0.0.1:8000) → Django. SPA+API 한 서비스.
- **DB: 로컬 Postgres**(`murmur` DB, DATABASE_URL=`/root/ClaudeCompany/.dburl`). 영속 — 재시작해도 데이터 유지. 웹 env=`/etc/murmur-web.env`.
- 러너: systemd `organt-runner` → `--remote http://127.0.0.1:8000` | **nginx가 외부 /api/guide/* 403 차단(H3): 러너만 로컬 직결로 사용.**(같은 호스트 로컬). ORGANT_GUIDE_TOKEN은 웹 env와 일치.
- **배포 방식(Render API 아님!)**: 백엔드 변경 → `systemctl restart murmur-web`. 프론트 변경 → `cd murmur/frontend && npm run build`(gunicorn이 dist 서빙). 마이그레이션 → env 걸고 `manage.py migrate`. VPS 체크아웃(`/root/ClaudeCompany`)이 곧 소스라 git pull 불필요(여기서 편집).
- Render 웹서비스(srv-d8tnrdog4nts73d4gcfg)는 **미사용**(러너가 안 봄) — 정지/삭제 가능. 단 *봇이 만든 프로젝트 배포*(deploy.py)는 여전히 Render API 사용(별개).

## 레포 HEAD (verify.sh가 대조하는 기준선)
```
system  da3d5f5
organt  511b66d
guide  a8f33a8
murmur  b6190b0   ← 라이브 웹=murmur-ai.duckdns.org
```

## 2026-07-04 착지: 기억 시스템 ①②(증류 라이브화·관련성 주입)
- **① 축출 차단(러너 재시작 시 라이브)**: 수면 증류(경험→직무·개인 기준 압축)를 `Sys.run`에 배선 — 종전 Discord 진입(`discord_main._sleep_cycle`)에만 있어 라이브(murmur) 러너에선 **안 돌던** 것(W4 B-19가 커밋됐어도 라이브 러너 미배선)을 매체중립 위치로. **`bot_profiles`(개인층)·직군 증류 라이브 첫 가동**. **OOM 근본교정**: 증류 워커에 빈 격리 cwd(`_distill_workspace`) — 종전 빈 흐름이 `cfg.workspace_dir`(수백MB)로 폴백해 CLI 스캔 RSS 수GB OOM 유발하던 것 차단(라이브 웹 공존 VPS라 필수). 경험버퍼 `_EXP_KEEP` 12→40(env `ORGANT_EXP_KEEP`). 조절: `ORGANT_SLEEP_PERIOD`(기본600·0=끄기).
- **② 관련성 주입(첫 조각·dormant)**: `meet` R2+ 재방송을 현재 발언자 도메인 관련성으로 예산 가중(`_token_overlap_score`=`_body_overlap` 스코어판). **`ORGANT_DOC_COLLAB` 플래그-온 경로 한정** — 플래그 off(라이브)면 미작동. flip은 ① 관측 데이터 후.
- 검증: 브레인 pytest **455**·system unittest **86**·스모크(격리 cwd 실도달·meet 관련성 가중). 남은 ②: 위임 노트(6000자 flat) 관련성화 — 스펙유실 민감(P-009), first-wake-full 가드로 별도 설계.

## 봇구조 W1~W4 — 커밋됨 (2026-07-03, push·배포 대기)
- **env 플래그 default-OFF(ORGANT_DOC_COLLAB 등)·이중수용** — 플래그 없으면 라이브 동작 불변. **유일한 무조건 라이브 변화 = B-12 회의 발언 채널 clip(200→500자, 러너 재시작 시 반영)**. 브레인 스위트 455. B-19 distill_bot+bot_profiles(개인 증류, ORGANT_BOT_DISTILL_MIN=8) · B-20 peers 강점 1줄(데이터 없으면 종전 문자열=증가분 0) · B-21 capability ledger(적립=owner_delivered+교차검증 통과 Task의 owner 저작만, cover 판정 무변경 — role_profiles.json `capability_ledger` 키) · B-22 personas.json(murmur 러너 DB→JSON 미러→Discord 러너 로드→빌더; **Discord persona 경로는 이 VPS에서 라이브 비검증 — ARCHITECTURE §6, 단위 테스트 한정**). **커밋·push·배포 완료(5bf64a1 live).** Dossier 등 플래그 기능은 ORGANT_DOC_COLLAB 등 켜야 활성(현재 관측만).
- **LLM-네이티브 재구조화**: **M1~M5 완료.** 오리엔테이션 층·docs 3계급화·모순교정·인수인계 폐지·**M5 단일 진실원(PJT 미러 제거, 455 pytest가 `/root/ClaudeCompany/ops/tests`에서 실코드 직접 검증)**. 남은 M6~M8(Flow 속성 선언화·게이트 함수화·Sys 3분할)은 BACKLOG A. tests·organt_discord·오리엔테이션 파일은 메타레포(로컬 git)로 버전관리됨 — 원격 push는 disaster-recovery용(BACKLOG).

## 병렬 세션 (Fable 판정 — task 단위 full-context, CONTRACTS.md 참조)
- **기본 = 작업(task)당 full-context 세션**(전 트리 편집권). 분할 축 = task+claim(파일 glob), 레포 아님. per-repo 1:1 편성 폐기(횡단 기능 역설계).
- 시작 `claim.sh add <task> <파일glob>` → 개발(계약 17seam 준수) → 착지 전 `claim.sh check`+full `verify.sh` → 통합 세션 착지 큐로 병합.
- **동시 세션 상한 ≈2~3**(claim 중첩 확률↑, 스케일=task 큐잉). worktree(`wt.sh`)는 레포-로컬 대량작업 등 opt-in만.

## 검증 기준선 (verify.sh)
- sns: **228** OK  ·  system unittest: **86**  ·  브레인 pytest(ops/tests): **455**  ·  프론트 빌드 OK.
- 명령은 `verify.sh` 참조. venv=`/root/ClaudeCompany/.venv`(pytest·discord.py 설치됨).

## 완료 (2026-07-03 세션)
- 봇구조 W1~W4 + B-12 500 · 보안 C1/H1/H2/H3+누출 · **LLM-네이티브 M1~M8**(오리엔테이션·단일진실원·Flow계약·거대파일 분할: complete_task 536→56·sys_core 2832→1736) · OPS 로깅 · 제품 P0 ×2(AI요약·코드조각URL) · **VPS 단일화(웹·Postgres·nginx·TLS, Render 폐기)**.

## 남은 일 → **`murmur/docs/BACKLOG.md` 단일 소스** 참조
요약: OPS 알림(systemd OnFailure)·pending N+1 / 보안 H3 스코핑·SSRF-lite / 제품 B1 랜딩 프리뷰 / Discord 이행(비검증) / 테스트 잔여물 정리(신선 seed로 대부분 해소됨 — Render→Postgres 이전 시 리셋) / M9 순환임포트 · communication.py 추가 분할.
