"""Organt Core — SYS(중앙통제) + Rule(추상 규칙) 패키지. 4레포 중 코어(아무도 의존하지 않음, 역참조 0).

의존 방향: system(이 레포) ← organt(봇 런타임)·guide(전송기)·murmur(SNS 웹). 코어는 상위
레이어(organt·guide·murmur)를 import하지 않는다 — 매체 구현(Discord/murmur 전송)은 guide 레포.

모듈 구성(전부 이 레포에 실재):
- sys_core: SYS 본체(깨우기·단일흐름 lock·라우팅·흐름 수명·복구·배포 오케스트레이션).
- flow: 한 협업 흐름의 공유 상태(팀·Task·베턴·작업공간) — 도구/Rule이 공유.
- guide_tools: Organt 도구셋 조립(request·recruit·run + 리더 create_project·create_task·
  set_goal·complete_task·deploy·vote·meet·parallel_work) + 협업·품질 게이트.
- tool_names: 도구명 상수·역할별 도구셋(매체-중립 leaf).
- protocol: 구조화 메시지 계약([Request]/[Response]/[Task-XXX]·Kind·TaskStatus).
- permissions / audit: PreToolUse 권한 훅 + JSONL 감사 로그.
- deploy: 산출물 공개 배포(GitHub push + Render 생성/갱신).
- config: 환경변수 → 런타임 설정.
- _util: 도구·Rule 공용 표현/디버그 유틸(순환의존 차단용 중립 지대).
- rule/: 광역 규칙 — communication(베턴·요청 스택), floor(**1층 발언권 배분 seam** —
  turn-taking(응찰·종결표결)↔request-response(베턴 동치)↔orchestrated 정책 교체, env ORGANT_FLOOR;
  스펙 murmur/docs/FLOOR_1F_2026-07-04.md·실험 CA-Lab EXP-001/002), task(완료·인수 검증),
  project(배포 신원).
- tests/: 표준 unittest 스위트(브레인 규칙 검증).
"""
