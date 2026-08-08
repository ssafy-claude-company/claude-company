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
              # [백로그 릴레이(2026-07-19, ch79 실측 — 등록↔허용 한 세트 회귀)] pick/drop/block이
              # 등록만 되고 여기 빠져 '권한 밖 도구' 거부 — 봇들이 선점하려다 전원 차단돼 작업 전이
              # 교착의 숨은 뿌리였다. 릴레이는 전 멤버 표면(자기 등재·선정·중단·차단).
              "mcp__guide__pick_backlog", "mcp__guide__drop_backlog", "mcp__guide__block_backlog",
              # [리더 폐지(2026-07-27, 사용자: '리더라는 존재 자체를 없애버리고')] 마감도 자리가
              # 아니라 관문이 지킨다 — 주기 완료·e2e 통과·교차검증·증거를 complete_task 게이트가
              # 전부 검사하므로 자격 없는 호출은 그 자리에서 거절된다. 등록(guide_tools)과 허용을
              # 한 세트로 열어, 마감 호출자 부재로 판이 못 닫히는 일이 없게 한다(U-065 교착).
              "mcp__guide__complete_task",
              "mcp__guide__deploy",   # [배포 탈중앙화(2026-07-08, 사용자)] deploy는 전원 — 검증 끝낸 owner가 직접 공개(리더 독점 폐지)
              "mcp__guide__atelier",  # [P0 B-2(2026-07-13)] 공유 판 — 전원, 사용은 자발(산출물·증거 남김, 승격 핀 done 회신)
              # [경제 감각(2026-08-04)] 요율·예산·시장 읽기 — 전원, 읽기 전용(돈이 움직이는 걸음 없음).
              "mcp__guide__economy",
              # [읽는 대신 묻는다(2026-08-07, 사용자: '직접 필요한 만큼 가져다 쓰는 정보 구조 체계가
              # 중요하겠지?')] 판의 결정 상태를 조각 단위로 답하는 창구 — 전원 표면, 읽기 전용.
              # 실측: 도구 호출 중 같은 봇의 같은 경로 재독이 27,169회(전체 읽기의 91%)였다.
              # 문서 한 채를 여는 대신 필요한 줄만 가져가게 한다.
              "mcp__guide__state"]
# 리더(코디네이터) 흐름 도구: 조율만(run 없음) — 구현·실행은 owner/QA가 한다.
COORD_TOOLS = [REQUEST_TOOL, RECRUIT_TOOL]
LEADER_TOOLS = [f"mcp__guide__{n}" for n in
                ("create_project", "create_task", "set_goal", "complete_task", "send_file",
                 "vote", "vote_stop", "meet", "parallel_work", "list_projects")]
