"""[Core] 도구명 상수 — MCP 도구 식별자·역할별 도구셋. 매체-중립 leaf 모듈.
guide_tools에서 분리: organt_runtime.builder가 guide_tools를 통째로 import하던 결합을 끊어
run 도구를 organt_runtime(Organt 개인)으로 옮길 수 있게 한다(순환 차단)."""

ORIGIN = 0
REQUEST_TOOL = "mcp__guide__request"
RECRUIT_TOOL = "mcp__guide__recruit"
RUN_TOOL = "mcp__guide__run"
# 모든 Organt 공통 흐름 도구(요청/채용/실행검증). 리더 전용 셋업 도구는 LEADER_TOOLS.
# [W3] report(B-14 — 멤버 보고 스태시)·cast_vote(B-15 — fork 가지 표결)는 멤버 세션에만 장착되지만,
# 허용목록은 공통 상수에 둔다(장착=guide_tools, 허용=여기 — 등록만 되고 허용에서 빠지면 런타임 권한거부).
FLOW_TOOLS = [REQUEST_TOOL, RECRUIT_TOOL, RUN_TOOL,
              "mcp__guide__report", "mcp__guide__cast_vote",
              # [파이프라인 §6 — e2e 마무리(S3)] 전 멤버 표면. 플래그 OFF면 등록 자체가 없어
              # 허용목록에 있어도 무해(호출 불가) — 이중수용 관례.
              "mcp__guide__e2e_open", "mcp__guide__e2e_scope",
              "mcp__guide__e2e_result", "mcp__guide__e2e_finish",
              # [파이프라인 §2·§3 — 공통 표면(통합주기 3 정합)] SubTask 추가(자발 참여의 문)와
              # iter 실증 제출은 현장 누구나 — 배치4에서 공통 구역으로 옮긴 등록과 허용을 한 세트로.
              "mcp__guide__set_subtask", "mcp__guide__report_iter",
              # [결정권자 폐지(2026-07-09, 사용자)] 확정=종결 표결(자동 등록)이므로 set_milestone은
              # '서기'(누구나) 표면이고, 재협상도 누구나(진짜 게이트=사람 승인) — 둘 다 공통 이동.
              "mcp__guide__set_milestone", "mcp__guide__renegotiate_criterion",
              "mcp__guide__deploy",   # [배포 탈중앙화(2026-07-08, 사용자)] deploy는 전원 — 검증 끝낸 owner가 직접 공개(리더 독점 폐지)
              "mcp__guide__atelier"]  # [P0 B-2(2026-07-13)] 공유 판 — 전원, 사용은 자발(산출물·증거 남김, 승격 핀 done 회신)
# 리더(코디네이터) 흐름 도구: 조율만(run 없음) — 구현·실행은 owner/QA가 한다.
COORD_TOOLS = [REQUEST_TOOL, RECRUIT_TOOL]
LEADER_TOOLS = [f"mcp__guide__{n}" for n in
                ("create_project", "create_task", "set_goal", "complete_task", "send_file",
                 "vote", "vote_stop", "meet", "parallel_work", "list_projects")]
