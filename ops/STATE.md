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
system  0cc431c
organt  511b66d
guide  e977239
murmur  fa1cac5   ← 라이브 웹=murmur-ai.duckdns.org
```
(워크트리 s/ClaudeCompany-변도진 기준 — 1층 floor seam 착지 대기. 라이브 main은 system ee9eebf·murmur b6190b0.)

## 봇구조 W1~W4 — 커밋됨 (2026-07-03, push·배포 대기)
- **env 플래그 default-OFF(ORGANT_DOC_COLLAB 등)·이중수용** — 플래그 없으면 라이브 동작 불변. **유일한 무조건 라이브 변화 = B-12 회의 발언 채널 clip(200→500자, 러너 재시작 시 반영)**. 브레인 스위트 455. B-19 distill_bot+bot_profiles(개인 증류, ORGANT_BOT_DISTILL_MIN=8) · B-20 peers 강점 1줄(데이터 없으면 종전 문자열=증가분 0) · B-21 capability ledger(적립=owner_delivered+교차검증 통과 Task의 owner 저작만, cover 판정 무변경 — role_profiles.json `capability_ledger` 키) · B-22 personas.json(murmur 러너 DB→JSON 미러→Discord 러너 로드→빌더; **Discord persona 경로는 이 VPS에서 라이브 비검증 — ARCHITECTURE §6, 단위 테스트 한정**). **커밋·push·배포 완료(5bf64a1 live).** Dossier 등 플래그 기능은 ORGANT_DOC_COLLAB 등 켜야 활성(현재 관측만).
- **LLM-네이티브 재구조화**: **M1~M5 완료.** 오리엔테이션 층·docs 3계급화·모순교정·인수인계 폐지·**M5 단일 진실원(PJT 미러 제거, 455 pytest가 `/root/ClaudeCompany/ops/tests`에서 실코드 직접 검증)**. 남은 M6~M8(Flow 속성 선언화·게이트 함수화·Sys 3분할)은 BACKLOG A. tests·organt_discord·오리엔테이션 파일은 메타레포(로컬 git)로 버전관리됨 — 원격 push는 disaster-recovery용(BACKLOG).

## 1층 floor seam — 대화 구조 추상화 + turn-taking (2026-07-04 커밋, 라이브 기본 불변)
- **발언권 순환(누가 다음에 말하는가)을 교체 가능한 정책으로 추상화** — `system/rule/floor.py`
  (TurnTakingFloor=Sacks ①지명 ②자기선택=**후보 봇 병렬 LLM 응찰**([응찰: N] — 최고 응찰 승·동률=침묵순)
  ③계속/소진 종결 · RequestResponseFloor=현행 베턴 동치(테스트 결박) · OrchestratedFloor=사회자).
  통합: meet R2+ 발언 순서(기본=종전 라운드 그대로)·리더 세그먼트 경계 TRP(기본 no-op).
  **ORGANT_FLOOR 미설정=라이브 동작 불변** — turn-taking 전환은 러너 env 추가+재시작(사용자 승인).
  실 LLM 봇 라이브 실행으로 검증(FLOOR_1F §6). 스펙: `murmur/docs/FLOOR_1F_2026-07-04.md`.
  후속(2층·위임 경로 응찰 확대·CA-Lab 실험)=BACKLOG G.

## 병렬 세션 (Fable 판정 — task 단위 full-context, CONTRACTS.md 참조)
- **기본 = 작업(task)당 full-context 세션**(전 트리 편집권). 분할 축 = task+claim(파일 glob), 레포 아님. per-repo 1:1 편성 폐기(횡단 기능 역설계).
- 시작 `claim.sh add <task> <파일glob>` → 개발(계약 17seam 준수) → 착지 전 `claim.sh check`+full `verify.sh` → 통합 세션 착지 큐로 병합.
- **동시 세션 상한 ≈2~3**(claim 중첩 확률↑, 스케일=task 큐잉). worktree(`wt.sh`)는 레포-로컬 대량작업 등 opt-in만.

## 검증 기준선 (verify.sh)
- sns: **228** OK  ·  system unittest: **86**  ·  브레인 pytest(ops/tests): **477**(+test_floor 22)  ·  프론트 빌드 OK.
- 명령은 `verify.sh` 참조. venv=`/root/ClaudeCompany/.venv`(pytest·discord.py 설치됨).

## 완료 (2026-07-03 세션)
- 봇구조 W1~W4 + B-12 500 · 보안 C1/H1/H2/H3+누출 · **LLM-네이티브 M1~M8**(오리엔테이션·단일진실원·Flow계약·거대파일 분할: complete_task 536→56·sys_core 2832→1736) · OPS 로깅 · 제품 P0 ×2(AI요약·코드조각URL) · **VPS 단일화(웹·Postgres·nginx·TLS, Render 폐기)**.

## 남은 일 → **`murmur/docs/BACKLOG.md` 단일 소스** 참조
요약: OPS 알림(systemd OnFailure)·pending N+1 / 보안 H3 스코핑·SSRF-lite / 제품 B1 랜딩 프리뷰 / Discord 이행(비검증) / 테스트 잔여물 정리(신선 seed로 대부분 해소됨 — Render→Postgres 이전 시 리셋) / M9 순환임포트 · communication.py 추가 분할.
