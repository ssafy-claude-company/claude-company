# STATE — 현재 상태 (살아있는 문서)

> 세션 시작 시 이 파일을 1회 읽어라. **stale하면 `verify.sh`가 heads 대조로 잡아낸다**(코드만 바뀌고 여기 안 바뀌면 검증에서 들킴). 갱신 기준일: 2026-07-03.

## 라이브 (2026-07-03 VPS 단일화 — Render 폐기)
- **웹: https://45.76.226.111.sslip.io** (VPS 45.76.226.111). nginx(TLS, Let's Encrypt 자동갱신) → gunicorn `murmur-web` systemd(127.0.0.1:8000) → Django. SPA+API 한 서비스.
- **DB: 로컬 Postgres**(`murmur` DB, DATABASE_URL=`/root/murmur-stack/.dburl`). 영속 — 재시작해도 데이터 유지. 웹 env=`/etc/murmur-web.env`.
- 러너: systemd `organt-runner` → `--remote http://127.0.0.1:8000`(같은 호스트 로컬). ORGANT_GUIDE_TOKEN은 웹 env와 일치.
- **배포 방식(Render API 아님!)**: 백엔드 변경 → `systemctl restart murmur-web`. 프론트 변경 → `cd murmur/frontend && npm run build`(gunicorn이 dist 서빙). 마이그레이션 → env 걸고 `manage.py migrate`. VPS 체크아웃(`/root/murmur-stack`)이 곧 소스라 git pull 불필요(여기서 편집).
- Render 웹서비스(srv-d8tnrdog4nts73d4gcfg)는 **미사용**(러너가 안 봄) — 정지/삭제 가능. 단 *봇이 만든 프로젝트 배포*(deploy.py)는 여전히 Render API 사용(별개).

## 레포 HEAD (verify.sh가 대조하는 기준선)
```
system  9643a8f
organt  511b66d
guide   a666939
murmur  682f156   ← 라이브=05ef04d(docs 미배포 무방)
```

## 봇구조 W1~W4 — 커밋됨 (2026-07-03, push·배포 대기)
- **env 플래그 default-OFF(ORGANT_DOC_COLLAB 등)·이중수용** — 플래그 없으면 라이브 동작 불변. **유일한 무조건 라이브 변화 = B-12 회의 발언 채널 clip(200→500자, 러너 재시작 시 반영)**. PJT 스위트 441. B-19 distill_bot+bot_profiles(개인 증류, ORGANT_BOT_DISTILL_MIN=8) · B-20 peers 강점 1줄(데이터 없으면 종전 문자열=증가분 0) · B-21 capability ledger(적립=owner_delivered+교차검증 통과 Task의 owner 저작만, cover 판정 무변경 — role_profiles.json `capability_ledger` 키) · B-22 personas.json(murmur 러너 DB→JSON 미러→Discord 러너 로드→빌더; **Discord persona 경로는 이 VPS에서 라이브 비검증 — ARCHITECTURE §6, 단위 테스트 한정**). **커밋·push·배포 완료(5bf64a1 live).** Dossier 등 플래그 기능은 ORGANT_DOC_COLLAB 등 켜야 활성(현재 관측만).
- **LLM-네이티브 재구조화**: **M1~M5 완료.** 오리엔테이션 층·docs 3계급화·모순교정·인수인계 폐지·**M5 단일 진실원(PJT 미러 제거, 441 pytest가 `/root/murmur-stack/tests`에서 실코드 직접 검증)**. 남은 M6~M8(Flow 속성 선언화·게이트 함수화·Sys 3분할)은 BACKLOG A. tests·organt_discord·오리엔테이션 파일은 메타레포(로컬 git)로 버전관리됨 — 원격 push는 disaster-recovery용(BACKLOG).

## 검증 기준선 (verify.sh)
- sns: **224** OK  ·  system unittest: **86**  ·  PJT pytest: **441**(W4 후; 봇구조 진행에 따라 증가)  ·  프론트 빌드 OK.
- 명령은 `verify.sh` 참조. venv=`/root/murmur-stack/.venv`(pytest·discord.py 설치됨).

## 남은 일 (요약 — 상세는 docs 날짜문서)
- 봇구조 W2~W4 + Final 완료·커밋·배포.
- LLM-네이티브 M2~M6+ (docs 정리·PJT 은퇴·거대파일 분할).
- 운영(OPS_PERF): Postgres 전환·프로덕션 로깅·장애 알림 (재배포 시 SQLite 초기화 문제 잔존).
- 제품 P0(PRODUCT_ASSESSMENT): 랜딩 무예고·AI요약 집계버그·XXXX 플레이스홀더.
- Discord SYS.run 이행(설계만, 토큰 없어 비검증).
- 라이브 테스트 잔여물 수동 정리: 계정 `fable_e2e_a/b/c`·`vault*`·`rechk*`, 채널 U-008~U-017 (삭제 API 없음).
- ClaudeCompany 단일화: `/root/ClaudeCompany` 아래 실사용 레포 정리 + PJT 미러 이관/제거(M5).

## 알려진 드리프트 (교정 대기 — M3)
- `murmur/AGENTS.md §4` "push=자동배포"는 **틀림** → 실제 수동 Render API(CLAUDE.md 참조).
- 테스트 수가 일부 문서에 144/58로 남음 → 실제 sns 213.
