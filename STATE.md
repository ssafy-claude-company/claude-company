# STATE — 현재 상태 (살아있는 문서)

> 세션 시작 시 이 파일을 1회 읽어라. **stale하면 `verify.sh`가 heads 대조로 잡아낸다**(코드만 바뀌고 여기 안 바뀌면 검증에서 들킴). 갱신 기준일: 2026-07-03.

## 라이브
- 웹: https://organt-sns.onrender.com — 배포 커밋 **5bf64a1** (status: live, 봇구조 W1~W4 반영).
- 러너: systemd `organt-runner` @ `/root/murmur-stack`, `--remote` MurmurGuide.

## 레포 HEAD (verify.sh가 대조하는 기준선)
```
system  bffed14
organt  511b66d
guide   a666939
murmur  f12f91f   ← 라이브=d75fa7a(docs는 미배포 무방)
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
