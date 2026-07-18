너는 murmur/Organt 프로젝트를 이어받는 세션이다. 두 가지가 네 임무다:
① **직전 세션(2026-07-18)의 작업을 검수**하고 ② **남은 작업을 이어간다.**
AI 직원(봇)들이 단계별 회의로 협업해 산출물을 만드는 멀티플레이 SNS 플랫폼. 작업 위치 =
/root/ClaudeCompany (= /root/murmur-stack 심링크). 사용자(이현준/도현)는 개발자가 아니다 —
디테일 완결은 전부 네 책임, 항상 존댓말로 보고한다.

## 먼저 이 순서로 읽고 정향하라 (추측 말고 파일로 확인)
1. CLAUDE.md — 프로젝트 불변식·구조·"X 하려면 어디" 인덱스.
2. ops/STATE.md — 라이브 상태·최근 커밋. 최상단 브레인/murmur HEAD 항목이 직전 세션 요지다.
3. ops/REPORTING.md — 사용자 보고 골격([결과]/[확인]/[결정]/[의미]/[미검증]/[다음]).
4. git log --oneline -30 (브레인) + git -C murmur log --oneline -20 — 직전 세션 커밋들.

## 이 시스템의 물리 (헷갈리기 쉬움)
- 단일 VPS: nginx(TLS) → gunicorn `murmur-web`(2워커) → 로컬 Postgres. 러너 systemd `organt-runner`가
  `--remote http://127.0.0.1:8000`로 웹과 **HTTP만** 통신(DB 직결 없음 — 프로세스 커넥션 0개로 확인됨).
  봇 턴 = `claude` CLI 서브프로세스. 검증 = `bash ops/verify.sh`(ALL_GREEN). 라이브 재시작은 사용자 승인 후.
- 커밋: 브레인(system+organt+guide+ops)=`/root/ClaudeCompany` 레포, murmur=`murmur/` 하위 레포. 둘 다.

## ① 검수 대상 — 직전 세션(2026-07-18)이 한 것 (커밋·배포·테스트 완료, 라이브 반영됨)
STATE.md 최상단이 상세. 요지:
- **보안**: 봇 Read/Glob 작업공간 경계 · run SSRF 차단 · monitor 채널 인가 · is_admin 비노출 ·
  SSO 1회용 nonce(마이그0026) · fail-closed(웹만·러너 제외) · nginx atelier/codex 외부차단 · admin rate-limit.
- **스케일아웃 준비(전부 플래그 off 기본·단일VPS 무회귀)**: 봇풀 세마포어·메모리입장·로그로테이션(활성) /
  ms_status·레지스트리 DB 이중화(ChannelState 마이그0027) / Redis 캐시 플래그 / 러너 분리 런북(ops/RUNBOOK-scale-out.md).
- **운영/과금**: Person.plan + UsageLedger(마이그0028) + 크레딧 요금제(settings.MURMUR_PLANS) + 보드주인
  귀속(guide report_usage → owner 원장) + 한도 게이트(MURMUR_QUOTA_ENFORCE 기본off) + 프로필 사용량 게이지.
- **감사 수리**: 봇 교착 출구 배선(escalate_to_human/approve_waiver 왕복) · 표결 파서 엄격화(_classify_vote) /
  StatsView 8초 캐시 · 파괴액션 확인 3종 · 계정 자기관리 UI(비번변경·탈퇴).

**검수 방법(중요): 라이브로 돌려 확인하지 마라.** 커밋 diff를 읽고, 테스트를 돌리고(verify.sh + 각
tests_*_fable.py), 필요하면 오프라인 예행·기존데이터 스크린샷으로 재현한다. 회귀·논리 오류·미완을 찾아
사용자에게 [결과]부터 보고. 문제 있으면 근본을 고친다(프롬프트 땜질 금지).

## ② 이어갈 작업 (감사에서 도출 — 우선순위 참고, 사용자와 조율)
- 봇 품질(신중 설계 필요): 완수조건 evidence **기계 실행화**(지금 봇 자기신고 텍스트로 품질게이트 우회 —
  milestone.py:452. SYS가 봇-작성 셸을 자동실행하는 거라 보안·아키텍처 리스크 커서 직전 세션은 미착수) ·
  회의 wake 축소(심의단 전원 프로브×재표결 곱셈이 실작업 전 수백 Haiku턴 소모) · standard 등록 필수화.
- murmur 제품(저위험): 라이브 상태 라벨 통일(화면마다 "작업 중"/"일하는 중"/"작업중" 갈림 —
  kinds.js에 단일 사전) · 알림 인박스.
- 과금 정책(사용자 결정): 요금제 숫자(MURMUR_PLANS)·강제 켜는 시점(며칠 원장 실비 대조 후 ENFORCE=1).

## 방법론·규율 (직전 세션이 사용자에게 배운 것 — 반드시 지켜라)
- **오프라인 검증 우선**: 라이브 판 40분 돌리기 = 토큰 낭비 안티패턴. 예행 스크립트·테스트로 봇 비용 0 검증.
- **추측으로 인프라 안 건드림**: 실제 프로세스/커넥션 직접 확인 후 판단(fail-closed가 러너 죽인 사고 있었음).
- **플래그 off 기본**: 새 강제·이전 기능은 무회귀 배포, 검증 후 켠다.
- 판단=Fable, 집행=Opus. 문자열 `claude-opus-4-8` 금지. 이현준 계열만 관리(변도진·atelier 불가침).
- 커밋 원자적·'왜'(사용자 지시·라이브 관측) 남김. 시작 시 `bash ops/claim.sh add <task> <파일glob>`, 착지 `bash ops/land.sh <task>`.

먼저 위 1~4를 읽고, **직전 세션 작업 검수 결과를 [결과]부터 보고**한 뒤, 어느 이어갈 작업부터 할지 사용자에게 확인하라.
