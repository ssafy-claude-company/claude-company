# STATE — 현재 상태 (살아있는 문서)

> 세션 시작 시 이 파일을 1회 읽어라. **stale하면 `verify.sh`가 heads 대조로 잡아낸다**(코드만 바뀌고 여기 안 바뀌면 검증에서 들킴). 갱신 기준일: 2026-07-06.

## 자격증명·환경변수 모델 (2026-07-06 라이브 적용 — migrate 0022~0024 + 웹 재시작 완료)
- **granular 환경변수 grant** (860bee0 live): 개인 금고(PersonSecret) **private**, 프로젝트/봇에 **명시 부여만** 노출. `ProjectEnv`·`AgentEnv`(금고 FK 참조=로테이션전파·CASCADE revoke, or 직접입력). `deploy_creds`/sns_guide → `resolve_env_for`(우선순위 opt-in금고<봇env<프로젝트grant). `Project.share_anchor_vault` opt-in(anchor 본인만 토글·transfer 리셋). **per-contributor**: 멤버 각자 추가·**내가 추가한 것만 삭제**(added_by)·소유자 전체·남의것 덮어쓰기 차단. UI=`EnvEditor.vue`(Channel 멤버팝업·AgentDetail). 무중단 마이그 0024(기존 프로젝트 배포4키 자동 grant).
- **confused-deputy 차단 + owner 단일의존 제거** (442bfc7 live): `deploy_creds`가 채널 picked 요청자(payload.requester_id)를 owner/active멤버 검증(제3자가 owner 키로 배포 트리거 차단). `Project.deploy_account`(이관 앵커) + `/projects/{pid}/transfer/`(소유권·배포앵커 이관, owner 전용·active멤버 대상). sns 275·brain 483.

## 라이브 (2026-07-03 VPS 단일화 — Render 폐기)
- **웹: https://murmur-ai.duckdns.org** (VPS). nginx(TLS, Let's Encrypt 자동갱신) → gunicorn `murmur-web` systemd(127.0.0.1:8000) → Django. SPA+API 한 서비스.
- **DB: 로컬 Postgres**(`murmur` DB, DATABASE_URL=`/root/ClaudeCompany/.dburl`). 영속 — 재시작해도 데이터 유지. 웹 env=`/etc/murmur-web.env`.
- 러너: systemd `organt-runner` → `--remote http://127.0.0.1:8000` | **nginx가 외부 /api/guide/* 403 차단(H3): 러너만 로컬 직결로 사용.**(같은 호스트 로컬). ORGANT_GUIDE_TOKEN은 웹 env와 일치.
- **배포 방식(Render API 아님!)**: 백엔드 변경 → `systemctl restart murmur-web`. 프론트 변경 → `cd murmur/frontend && npm run build`(gunicorn이 dist 서빙). 마이그레이션 → env 걸고 `manage.py migrate`. VPS 체크아웃(`/root/ClaudeCompany`)이 곧 소스라 git pull 불필요(여기서 편집).
- Render 웹서비스(srv-d8tnrdog4nts73d4gcfg)는 **미사용**(러너가 안 봄) — 정지/삭제 가능. 단 *봇이 만든 프로젝트 배포*(deploy.py)는 여전히 Render API 사용(별개).

## 레포 구조 (2026-07-06 병합)
- **2레포**: `claude-company`(브레인 = system+organt+guide+ops, GitHub public) + `murmur`(SNS 플랫폼). 루트 흡수 방식(PYTHONPATH·러너·디렉터리 무변경). 옛 system/organt/guide·DEPR*·PJT 레포는 **GitHub 아카이브됨**. 로컬 root 레포는 원격 미연결(히스토리에 .dburl 있어 — 향후 push는 깨끗한 클론으로).

## 레포 HEAD (verify.sh가 대조하는 기준선)
```
claude-company  e01a551+ ← 브레인 — 2026-07-10 반쪽등록 복구+중복 deploy 제거+PIPELINE_CTX(러너 라이브)
murmur  8289131   ← 라이브 웹=murmur-ai.duckdns.org — 마일스톤 피드 구조화(아래 2026-07-10 항목)
```

## 마일스톤 피드 구조화 (2026-07-10 이현준 세션 — 러너·웹 라이브)
- **피드 뼈대 = 원문 → [준비](협의 묶음, 보라) → [마일스톤]×N(초록)**. 마일스톤 블록 안: 단계(SubTask)
  폴더(담당 owner 칩·참여자 아바타·충족 m/N·상태) → 백로그 행(유형 배지+주제 제목+결과 2단+발화자,
  같은 ST/GOAL 표지 연속 과정 병합) → 행 클릭 시 **그 자리** 인라인(FeedBlock.vue 컴팩트 렌더러).
- **마커 계약(rule/milestone.py)**: 시작/개설/완수 마커에 `(ms_id|st_id)` 동봉 + 시작 마커에 완수조건
  목록 동봉(조건 칩 열람) + `[조건 충족] (id) m/N — 신규 통과 desc`(신규 통과 시만). 표면은 ID 우선
  부착, 구 마커(07-10 이전)는 제목 절두 폴백 — U-010 기존 3블록은 조건 칩 비활성(원천 부재).
- 요지 추출 `_gist/_voteSum`(표결=집계+이유·회의=결론·보고=[결과] 줄·한국어 줄 우선)이 접힘 미리보기와
  백로그 행 공용. 다음 마일스톤 등록 시 조건 열람·충족 카운터 실측 확인 필요.
- **복구 체계 구멍 수리(2026-07-10, ch51 라이브 사인 — 봇 에스컬레이션 처방 채택)**: ① guide_tools
  leader 블록의 중복 deploy({name,command,url}) 재정의 제거(탈중앙화 leftover — leader 세션에서 정본
  deploy를 가려 'command required' 교착) ② rule/project.create_project 반쪽등록 복구(project_channel은
  섰는데 project_id=None이면 재호출이 등록만 마저 완료 — 종전엔 영구 차단). 미등록 흐름은
  checkpoint_open_task가 조기 반환해 **마일스톤이 재시작마다 죽던 것**의 뿌리. ③ 발언 소속 태깅
  PIPELINE_CTX(protocol, 계약 선언)로 피드가 ID 부착. 남은 갭: 등록 없는 흐름의 체크포인트는 여전히
  skip(등록 의존) — 구조 분리는 후속.

## 진짜 채용 — 지명제 폐지, 공고·지원·선발 (2026-07-09 착지 → **러너 라이브**, 08:56 재기동분)
- **recruit 전면 재설계**(`rule/comm_ceremonies.recruit`): 리더·팀이 독단 영입하지 않는다.
  ① 공고 `recruit(need='문제/일손')` → 한가한 동료 전원 깨워 지원 받음(role은 참고만 — 후보
  필터 아님, **매직넘버 상한 폐지**) ② 지원 `[지원]`+지원서 또는 `[패스]`(자기선택, 본인 명의
  채널 게시) ③ 선발 `recruit(member=지원자)` — **지원 안 한 봇 지명 거부**. 유찰=genesis 폴백.
  직군=지원자 속성(보유>[직군:X] 선언>공고 role). 관측 `recruit_posted/apply/pass/awarded/genesis`.
- 근거: 발언권 1층(응찰=자기선택)의 멤버십판. 사용자 교정 반영("role에 얽매이지 마라·문제 중심·
  임의 숫자 금지"). 유지 게이트: placeholder 직군 거부·변형 차단·1봇1직업(겸직 유사일만·최대2).
- 대본 검증: 브레인 pytest 591 · system 87. **실 LLM 관통 검증 완료(2026-07-09, 격리 스택 —
  자체 sqlite·실 sonnet 봇 4명)**: 발제자 응찰 4봇 실응찰(PM 9점 선출) → 보안 공고 → 채용봇이
  "공격자 시각을 심을 인격을 빚겠다" 실지원서 → 선발까지 완주. 발견 결함(지원 마커 중복) 즉시
  수정(fd3d75d). 라이브 러너(08:56~, murmur-stack)에 실려 있음 — 첫 무지정 요청부터 응찰·채용 발동.
- **배치A/B 착지·라이브(2026-07-09)**: 갭5 발제자 응찰(`sys_core._elect_proposer` — 무지정 요청은
  is_leader 지정이 아니라 봇 [응찰: N] 자기선택, 관측 `propose_bid/pass/elected`) · is_leader 은퇴
  (러너 로스터가 플래그 안 읽음) · 직군 정규화 `_same_job`(약칭 병합) · 온보딩 표식 DB 진실원
  `Agent.onboarded_at`(마이그 0025 **라이브 적용, 29/30 백필** — 재온보딩 구조 차단). **murmur-web만
  구코드(07:20 기동 > 착지 07:59)** — 재시작 승인어 대기(재시작 시 roster onboarded 노출·web쪽 활성).

## 봇구조 W1~W4 — 커밋됨 (2026-07-03, push·배포 대기)
- **env 플래그 default-OFF(ORGANT_DOC_COLLAB 등)·이중수용** — 플래그 없으면 라이브 동작 불변. **유일한 무조건 라이브 변화 = B-12 회의 발언 채널 clip(200→500자, 러너 재시작 시 반영)**. 브레인 스위트 455. B-19 distill_bot+bot_profiles(개인 증류, ORGANT_BOT_DISTILL_MIN=8) · B-20 peers 강점 1줄(데이터 없으면 종전 문자열=증가분 0) · B-21 capability ledger(적립=owner_delivered+교차검증 통과 Task의 owner 저작만, cover 판정 무변경 — role_profiles.json `capability_ledger` 키) · B-22 personas.json(murmur 러너 DB→JSON 미러→Discord 러너 로드→빌더; **Discord persona 경로는 이 VPS에서 라이브 비검증 — ARCHITECTURE §6, 단위 테스트 한정**). **커밋·push·배포 완료(5bf64a1 live).** Dossier 등 플래그 기능은 ORGANT_DOC_COLLAB 등 켜야 활성(현재 관측만).
- **LLM-네이티브 재구조화**: **M1~M5 완료.** 오리엔테이션 층·docs 3계급화·모순교정·인수인계 폐지·**M5 단일 진실원(PJT 미러 제거, 455 pytest가 `/root/ClaudeCompany/ops/tests`에서 실코드 직접 검증)**. 남은 M6~M8(Flow 속성 선언화·게이트 함수화·Sys 3분할)은 BACKLOG A. tests·organt_discord·오리엔테이션 파일은 메타레포(로컬 git)로 버전관리됨 — 원격 push는 disaster-recovery용(BACKLOG).

## 1층 floor seam — 대화 구조 추상화 + turn-taking (2026-07-04 착지·라이브 적용)
- **발언권 순환(누가 다음에 말하는가)을 교체 가능한 정책으로 추상화** — `system/rule/floor.py`
  (TurnTakingFloor=Sacks ①지명 ②자기선택=**후보 봇 병렬 LLM 응찰**([응찰: N] — 최고 응찰 승·동률=침묵순)
  ③무응찰=**종결 확인 표결**([종료]/[계속: N]=발언 의무 반대 — 합의 종결. EXP-002 절제: '현재 화자
  계속'·lapse 제거, 표결은 무응찰마다·상한=wake_cap/max_turns) · RequestResponseFloor=베턴 동치 ·
  OrchestratedFloor=사회자).
  통합: meet R2+ 발언 순서·리더 세그먼트 경계 TRP. 실 LLM 봇 라이브 실행으로 검증(FLOOR_1F §6-2).
- **라이브 러너 env `ORGANT_FLOOR=turn-taking` 적용(2026-07-04, 사용자 승인)** — 시스템 작동
  구조=turn-taking. 되돌림=env 값 제거+재시작(한 줄). 코드 폴백=request-response(오배선 안전값 —
  테스트는 두 정책 모두 명시 고정). 스펙: `murmur/docs/FLOOR_1F_2026-07-04.md`.
- **1층 마무리(2026-07-05)**: 실 검증 4회(라이브 E2E U-002·U-003 — 합의 종결 2회 관측) ·
  CA-Lab 실험 기록 [EXP-001] draft PR(#12, 검수 대기) · system/murmur GitHub push 완료.
  **2층은 1층 phase 사용자 검수 후**(CA-Lab 규율). 잔여 관측·후속=BACKLOG G.

## 컨텍스트 주입 — 내구적 배치 (2026-07-06 착지·라이브 적용)
- **되살리기(반복 주입) 제거 + first_wake 1회 교육**: 정체성·원칙·원문·[경험]·운영은 그 봇 첫 wake에만 주입(`will_resume` 판정), resume는 **동적 task만**. 정적 지식·앵커는 반복이 아니라 **압축에도 살아남는 구조**가 담보 — 단일활성·`.collab/` 어포던스=**persona(system_prompt)**, verify·owner·iface·베턴=**게이트**, 원문·기준·로스터=**디스크(`.collab/ORIGIN.md`·`PLAYBOOK.md`·`TEAM.md`, [경험]=report 툴 필드)**. 동료는 로스터 변경(이벤트) 시에만 주입.
- **큐레이션 조립기**(assemble_context): 키 dedup·예산·우선순위로 PRINCIPLE 정리. **①증류 라이브화**(distill_role/bot 배선·수면 루프) · **②채용 제네시스 활성**(ensure_recruiter→리크루터 '주시안' role=채용 생성, 신규 봇 persona/이름 온보딩). floor seam과 공존(communication.py는 floor 구조, meet-relevance는 후속 재적용).
- 수치: 일반 resume 턴 ~269자(옛 매턴 전량 ~2,600 대비), 항해-필수 사실 8/8 내구·최악 재접지 1,338토큰 1회(유계). **순응률(실 pull·grounding)은 flow 로그 관측 대상.** 롤백 SHA: system 880a965·organt 511b66d·guide e977239·murmur 7a07575·meta 2b81da6.

## 봇별 완전 격리 — 직군 공용 학습 폐지 (2026-07-06 라이브 적용 — 러너·웹 재시작·migrate 0020 완료)
- **학습·기억·기준 = 봇 개인 단위만**: [경험]→`bot_experience[me]`, [직무기준]→`bot_profiles[me]`, 주입='자기 개인 기준'뿐(같은 직군 동료 기준도 안 받음 — 오염 차단). 수면 = 온보딩+개인 증류(distill_bot)만(직군 증류 삭제). 검증 루브릭·craft 미러 = 그 직군 보유 봇의 개인 기준(`_job_standard`).
- **신규·빈 봇 = 기계적 시드 없이 리크루터 온보딩**이 직군 유산(role_profiles, 동결)을 '이 사람의 시작 기준'으로 빚음 — "증류 안 된 봇이 아니라 학습을 거친 봇이 튀어나온다". 기존 빈 봇도 수면 사이클의 `pick_onboard_bots`가 자연 온보딩. role_experience=레거시 동결.
- 격리 스모크 4/4(같은직군 미주입·자기풀만 영속·제네시스 기준 보유 탄생·봇별 상이) · 브레인 478 · system 86 · sns 260.
- **웹 표면도 개인화(fe3ef5d)**: Agent.craft·craft_distills(마이그 0020) + 러너 persist_craft 동기(흡수·개인증류·온보딩) + AgentDetail '이 직원의 노하우'(직군 RoleProfile 표시 폐기) + 추천 지표 개인화. 기존 증류분 2봇(이서준·장건우) craft 백필 완료 — UI에서 봇별 상이 확인 가능.

## 병렬 세션 (Fable 판정 — task 단위 full-context, CONTRACTS.md 참조)
- **기본 = 작업(task)당 full-context 세션**(전 트리 편집권). 분할 축 = task+claim(파일 glob), 레포 아님. per-repo 1:1 편성 폐기(횡단 기능 역설계).
- 시작 `claim.sh add <task> <파일glob>` → 개발(계약 17seam 준수) → 착지 전 `claim.sh check`+full `verify.sh` → **bash ops/land.sh <세션>**으로 스스로 정본 병합(통합 세션 불필요).
- **동시 세션 상한 ≈2~3**(claim 중첩 확률↑, 스케일=task 큐잉). worktree(`wt.sh`)는 레포-로컬 대량작업 등 opt-in만.

## 검증 기준선 (verify.sh)
- sns: **321**(2026-07-09 현재 **3건 실패** — RequeueStuck·PendingQueue·PendingQueryBudget, 변도진-1 브리지 착지/WIP 영역, 세션에 통지됨)  ·  system unittest: **86**  ·  브레인 pytest(ops/tests): **591**  ·  프론트 빌드 OK.

## 파이프라인 재설계 — **라이브 영구 적용 (2026-07-09, 사용자 승인)**
- **`ORGANT_PIPELINE=milestone`이 라이브 기본값** — `/etc/organt-runner.env`에 영구 반영. 이제 **러너 재시작 자유**(누가 언제 재시작해도 새 파이프라인 유지). probe 격리 러너(`/root/wt/probe`) 폐지 — 단일 러너 복원. ON/OFF 이중수용 코드는 그대로(끄려면 env 한 줄 제거).
- **관통 실증**: U-008(TODO 웹)이 새 파이프라인으로 완주(`flow_done t-369`, probe에서 관측) — 발제(권한 0)→응찰 회의→마일스톤 12조건 등록→위임까지. 회의 표결 자동 등록(ms_confirm_by_vote) 경로는 아직 라이브 미관측. 채널 49는 OFF-러너 오염 이력으로 은퇴(중지 처리) — 새 검증은 새 채널로.
- **계약 정본**: `murmur/docs/PIPELINE_REWORK_2026-07-09.md`(+§12-1·12-2 접점 코멘트). 리더 해체 — 진행=주기(iter)·마감=완수조건·배분=백로그 릴레이(지명→응찰→마지막 작업자). 결정권자 폐지(확정=종결 표결).
- **통합주기 3까지 착지(master)**: S1 개체·ckpt·완전TT 회의·확정 도구·결정권자 프레임·report_iter / S2 릴레이 상태기계+**위임축 배선(위임=배분)** / S3 e2e 아크·도구 4종·복기 실배선. SubTask iter↔백로그 정리 훅 접붙임(§12-1). 도구 등록↔허용목록(tool_names) 한 세트 규율(S3 발견 결함 → 회귀 가드 테스트). **전부 `ORGANT_PIPELINE=milestone` 안 — OFF(현 라이브) 불변.**
- **§5 물리 재편 3축 착지(통합주기 5)**: 흐름 루프가 주기 관할(S1)·완수조건 실증이 마감(S2)·SYS 자동장치 ON 우회(S3). 이제 리더 해체가 코드로 완성 — 남은 건 **통째 관통**(U-검증 1건, 플래그 ON 격리 probe). 전부 OFF 불변.
- **라이브-e2e 접점(§12-3, 운영 세션)**: RPG 실전 관측에서 갭 4건 등재 — 조건 '달성 불가' 출구 / 개입·질문의 주기 내 좌석 / 표면(murmur) 접기 문법 후속 / 조건 품질 게이트. 상보 장치 4종은 라이브 착지됨(3연속-실패 차단기 등) — 병합 시 의미 정합 확인.
- 명령은 `verify.sh` 참조. venv=`/root/ClaudeCompany/.venv`(pytest·discord.py 설치됨).

## 완료 (2026-07-03 세션)
- 봇구조 W1~W4 + B-12 500 · 보안 C1/H1/H2/H3+누출 · **LLM-네이티브 M1~M8**(오리엔테이션·단일진실원·Flow계약·거대파일 분할: complete_task 536→56·sys_core 2832→1736) · OPS 로깅 · 제품 P0 ×2(AI요약·코드조각URL) · **VPS 단일화(웹·Postgres·nginx·TLS, Render 폐기)**.

## 남은 일 → **`murmur/docs/BACKLOG.md` 단일 소스** 참조
요약: OPS 알림(systemd OnFailure)·pending N+1 / 보안 H3 스코핑·SSRF-lite / 제품 B1 랜딩 프리뷰 / Discord 이행(비검증) / 테스트 잔여물 정리(신선 seed로 대부분 해소됨 — Render→Postgres 이전 시 리셋) / M9 순환임포트 · communication.py 추가 분할.
