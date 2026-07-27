"""SYS 크래시-세이프 복구 — sys_core.Sys에서 파사드 보존 추출(LLM_DX_AUDIT 1-C ⑥).

미완 Task의 직렬화(스냅샷)·흐름 도중 체크포인트·다음 개입에서의 되살리기(복원)를 담는다.
공개 표면은 그대로 Sys 메서드(`Sys._task_snapshot`·`Sys._restore_open_task` 등)이며 그 메서드들이
여기로 위임한다 — 외부(테스트·러너)는 종전 이름을 그대로 쓴다. sys_core를 import하지 않는다(단방향).
함수 첫 인자 `sys`는 Sys 인스턴스이고, 다른 Sys 메서드 호출은 `sys._X()` 경유 — 테스트
monkeypatch·서브클래스 오버라이드 의미 보존.
"""
import copy
import re
import secrets
from typing import Optional

from ._util import doc_collab_on, dossier_read, dossier_rel
from .guide_tools import TaskRef
from .protocol import TaskStatus
from .rule.milestone import ms_from_dict, ms_to_dict   # [마일스톤 §9 — 체크포인트 동승]


def _parse_goal_doc(text) -> dict:
    """[B-11 — Task Dossier 복구] GOAL.md(rule/task.set_goal이 쓰는 고정 섹션 헤더) → 섹션 dict.
    헤더 계약이 깨져 있으면 빈 dict — 호출부가 스냅샷으로 폴백한다(무결성 검사 겸용)."""
    out, cur, buf = {}, None, []
    for ln in (text or "").splitlines():
        m = re.match(r"^##\s+(Purpose|Goal|Acceptance|Standard|Interfaces)\s*$", ln)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(ln)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


def _interject_state_snapshot(flow):
    """사람 개입 큐와 ack 재시도 장부를 한 체크포인트에 넣을 JSON-safe 값으로 만든다."""
    pending = {}
    raw_pending = getattr(flow, "pending_info", None) or {}
    if isinstance(raw_pending, dict):
        for member, entries in raw_pending.items():
            try:
                key = str(int(member))
            except (TypeError, ValueError):
                continue
            values = entries if isinstance(entries, (list, tuple)) else [entries]
            cleaned = [str(value) for value in values
                       if value is not None and str(value)]
            if cleaned:
                pending[key] = cleaned

    retry = {}
    raw_retry = getattr(flow, "_ij_retry", None) or {}
    if isinstance(raw_retry, dict):
        for member, count in raw_retry.items():
            try:
                key, value = str(int(member)), int(count)
            except (TypeError, ValueError):
                continue
            if value > 0:
                retry[key] = value
    return pending, retry


def _restore_interject_state(flow, snap) -> None:
    """구 스냅샷(키 없음)과 일부 손상 값을 빈 상태로 안전하게 복원한다."""
    pending = {}
    raw_pending = snap.get("pending_info") or {}
    if isinstance(raw_pending, dict):
        for member, entries in raw_pending.items():
            try:
                key = int(member)
            except (TypeError, ValueError):
                continue
            values = entries if isinstance(entries, (list, tuple)) else [entries]
            cleaned = [str(value) for value in values
                       if value is not None and str(value)]
            if cleaned:
                pending[key] = cleaned

    retry = {}
    raw_retry = snap.get("interject_retry") or {}
    if isinstance(raw_retry, dict):
        for member, count in raw_retry.items():
            try:
                key, value = int(member), int(count)
            except (TypeError, ValueError):
                continue
            if value > 0:
                retry[key] = value
    flow.pending_info = pending
    flow._ij_retry = retry


def task_snapshot(flow, ref) -> dict:
    """미완 Task를 다음 개입에서 '되살릴' 수 있도록 직렬화한다(상태블록·스레드·담당자·팀·목표 +
    수렴 진행 사실 전체). 원칙(2026-06-23, 사용자 '메모리 안정적으로 — field별 땜질 말고'): *사실*
    (무슨 일이 일어났나 — 작업·검증·기여, 작업공간 파일이 복구에 보존됨)은 영속, *재검 sanity*
    (verified — 새 run 증거)만 0 리셋한다. verified 리셋이 '허위완료 방지'의 단일 백스톱이므로(되살린
    직후 새 run 증거 없이는 complete_task 첫 게이트에서 막힘) 나머지 사실을 다 영속해도 완료는 fresh
    run에 묶여 안전하다. (종전 field별 영속이 act_by·contrib_checked 등을 빠뜨려 contrib 게이트가
    복구마다 '전원 idle'로 오판, 마감이 영영 안 닫히던 결함 — line 595·sys 521 주석이 'act_by 인메모리
    리셋 결함'을 알려진 결함으로 명시했으나 미수정이었음 — 을 포괄 교정.)"""
    pending_info, interject_retry = _interject_state_snapshot(flow)
    return {
        "task_id": ref.task_id,
        "thread_id": ref.thread_id,
        "block_id": ref.block_id,
        # [B-11 — Task Dossier 참조(additive)] 협의 원본(.collab/T-<id>)의 **워크스페이스-상대**
        # 경로 — 절대경로 금지(_idify_workspace가 흐름 도중 폴더를 개명). 복구가 ORGANT_DOC_COLLAB=1
        # 일 때 '내용'을 이 문서에서 우선 되살린다(사실 필드는 항상 스냅샷). 구 스냅샷은 키 부재 →
        # 종전 폴백(하위호환).
        "dossier_path": dossier_rel(ref.task_id),
        "purpose": ref.status.purpose or "",
        "goal": ref.status.goal or "",
        "owner": int(ref.owner or 0),
        "owner_name": ref.status.owner or "",
        "team": [int(x) for x in ref.team],
        # [사람 개입 crash safety] 직렬화 경계에서 큐와 ack/retry를 함께 묶는다. checkpoint는
        # Task 생성 전도 보존하도록 이 두 필드를 프로젝트 root로 옮겨 유일 정본으로 저장한다.
        # 문자열 키/값만 써 projects.json과 DB 미러 양쪽에서 JSON-safe하다.
        "pending_info": pending_info,
        "interject_retry": interject_retry,
        "result_so_far": (ref.status.result or "")[:500],
        "collab_notes": getattr(ref, "collab_notes", ""),   # 회의·표결 합의 — 재개 위임에도 동봉
        "acceptance": getattr(ref, "acceptance", ""),        # 수용 계약 — 동면·재개 너머 영속(없으면 마감 게이트가 매번 재정의 요구)
        "standard": getattr(ref, "standard", ""),            # [최대화] 최대 품질 표준(도메인 누적) — 동면 너머 영속(없으면 바가 증발, 라이브 확인)
        "interfaces": getattr(ref, "interfaces", ""),        # [협업] 도메인 간 인터페이스 계약 — 재개 마감 L2 검증이 같은 계약으로
        # [협의 명단 영속] 이게 없으면 재개마다 set_goal 게이트가 전원 재협의를 강제 —
        # 라이브: 동면 5회 흐름에서 리더가 같은 협의 질문을 5회 반복(시간·토큰 낭비의 주범).
        # 협의는 '사실'이라 영속이 옳다(검증 누계와 다름 — 그건 의도적으로 0에서 재시작).
        "participated": sorted(int(x) for x in getattr(ref, "participated", []) or []),
        # [완료 게이트 통과 영속(2026-06-23, 사용자)] _gate_pass(percept·acceptance·data_prov 회계 통과)는
        # '사실'(리더가 그 기준을 result에 회계했다)이라 복구 너머 영속한다 — 인메모리 리셋이 복구마다 그
        # 회계 재서술을 강제해 마감이 영영 안 닫히던 결함(이 환경은 컨테이너 회수·재시작이 잦음). verified·
        # cross_checks(실검증 누계)는 종전대로 0에서 재시작(재개 시 팀이 자연히 다시 채움 — 회계 재서술과 다름).
        "gate_pass": sorted(n for (n, tid) in (getattr(flow, "_gate_pass", None) or ())
                            if str(tid) == str(ref.task_id)),
        # [위임 사실 영속(2026-06-23, 사용자)] '리더가 이 Task에서 Work를 위임했다'는 *사실*이라 복구 너머
        # 영속한다 — 인메모리 리셋(work_delegated→0)이 복구마다 '리더가 위임 0회 헛돈다'로 오판해 SYS 자동
        # 위임을 반복 발동시키던 결함(특히 owner가 검증자로 잡힌 경우 QA에게 구현을 떠넘기는 상류 경로).
        "work_delegated": int(getattr(ref, "work_delegated", 0) or 0),
        "work_delegated_to": sorted(int(x) for x in getattr(ref, "work_delegated_to", []) or []),
        # [완료 검증 사실 영속(2026-06-23, 사용자 — 마감 안 닫히는 진짜 원인)] owner가 검증된 산출물을
        # *인도*했다(owner_delivered)·다른 멤버가 *교차검증*했다(cross_checks)는 '사실'이라 복구 너머 영속한다.
        # 이 둘은 complete_task에서 gate_pass(percept/acceptance/data_prov)보다 *앞단*인데, 종전엔 '되살린 직후
        # 허위완료 방지'로 0 리셋 → 컨테이너 회수·재시작 잦은 이 환경에선 *매 복구마다* 인도·교차검증
        # 핸드셰이크를 다시 요구해 마감이 영영 안 닫혔다(작업공간 파일은 복구에 보존되므로 그 검증은 유효).
        # verified(실행)는 리더가 재개 때 1회 재실행하는 저비용 sanity라 종전대로 0 리셋(완료 직전 run 증거).
        "owner_delivered": bool(getattr(ref, "owner_delivered", False)),
        "cross_checks": int(getattr(ref, "cross_checks", 0) or 0),
        # [수렴 사실 포괄 영속(2026-06-23, 사용자: '메모리 안정적으로 — field별 땜질 말고')] 게이트가 읽는
        # 진행 '사실'을 전부 영속한다. act_by(누가 Write/Edit/run 했나 — contrib 게이트의 idle 판정 입력),
        # contrib_checked(기여 게이트 통과), cross_check_offdomain(독립검증 누계), run_count·evidence(실행
        # 진행·영수증), cc_held(교차검증 보류 횟수), leader_writes(리더 독식 누계), deploy_count(배포 런어웨이
        # 캡). 종전 인메모리라 컨테이너 회수·재시작마다 0 리셋 → contrib 게이트가 '전원 idle' 오판(act_by),
        # 배포 캡 에스컬레이션 무력화(deploy_count) 등으로 마감/캡이 영영 작동 안 함. 작업·검증은 일어났고
        # 작업공간 파일이 복구에 보존되므로 이 사실들의 영속은 유효. verified만 리셋 유지(허위완료 백스톱).
        "act_by": {str(int(m)): int(flow.act_by.get(m, 0))
                   for m in ref.team if int((flow.act_by or {}).get(m, 0) or 0) > 0},
        "contrib_checked": bool(getattr(ref, "contrib_checked", False)),
        "cross_check_offdomain": int(getattr(ref, "cross_check_offdomain", 0) or 0),
        "last_verify_writes": int(getattr(ref, "last_verify_writes", -1)),
        "acceptance_pass_writes": int(getattr(ref, "acceptance_pass_writes", -1)),  # [버전 인식 acceptance] 통과 시점 저작수 — 재개 후에도 변경 감지 재검증이 일관
        "verify_fail_streak": int(getattr(ref, "verify_fail_streak", 0) or 0),       # [원리 기반 루프 신호] 연속 실패 검증 수 — 재개가 루프 감지를 잃지 않게
        "deploy_cross": int(getattr(flow, "_deploy_cross", -1)),   # [캡=맹목 스핀만] 마지막 배포 시점 교차검증 수 — 검증 주도 반복의 캡 미과금이 재개 너머 일관
        "cross_checkers": sorted(int(x) for x in (getattr(ref, "cross_checkers", None) or set())),
        "loop_escalated": bool(getattr(ref, "loop_escalated", False)),
        "run_count": int(getattr(ref, "run_count", 0) or 0),
        "evidence": (getattr(ref, "evidence", "") or "")[:500],
        "cc_held": int(getattr(ref, "cc_held", 0) or 0),
        "leader_writes": int(getattr(ref, "leader_writes", 0) or 0),
        # peer_info_pairs(owner↔owner 직접 Info 교환 쌍) — iface 게이트가 '계약을 당사자끼리 직접 확인했나'를
        # 이걸로 본다(리더 중계·추측 차단). '사실'이라 영속(iface 통과 전 죽으면 재협의 강제하던 마지막 갭).
        "peer_info_pairs": [sorted(int(x) for x in pr)
                            for pr in (getattr(ref, "peer_info_pairs", None) or ())],
        "deploy_count": int(getattr(flow, "_deploy_count", 0) or 0),
        # 배포 anti-thrash 상태(전수감사): _deployed_once·_deploy_writes·writes_by_role가 리셋되면 '코드
        # 변경 없는 재배포' 가드가 복구마다 무력화돼 재배포 런어웨이가 되살아난다 → 함께 영속.
        "deployed_once": bool(getattr(flow, "_deployed_once", False)),
        # [배포 목표 달성 영속(2026-07, 사용자)] 라이브 검증(HTTP 200+바이트 일치)까지 통과한 '배포 성공' 신호 —
        # 재개(러너 재시작·복구)돼도 '목표 실증됨=완료 인식'이 유지되게 영속(flow.deployed 문자열은 체크포인트에
        # 안 실려 restore 흐름에선 사라지므로, 그것만 보던 완료 인식이 무효였던 근본 교정).
        "deploy_live": bool(getattr(flow, "_deploy_live", False)),
        "deploy_url": str(getattr(flow, "_deploy_url", "") or ""),   # [확인 링크] 보고 동봉용 영속
        "deploy_writes": int(getattr(flow, "_deploy_writes", -1)),
        "writes_by_role": {str(k): int(v) for k, v in (getattr(flow, "writes_by_role", None) or {}).items()},
        "consec_fail": int(getattr(flow, "consec_fail", 0) or 0),
        # [재위임 런어웨이 차단] 완료된 (위임자→owner) 쌍·redo 카운트를 영속 — 복구가 이미 끝난 일을 Redo로
        # 인식해 재발사하지 않게(종전 reset_task_tracking 삭제로 churn). comm 레벨 사실.
        "delivered_pairs": [[int(a), int(b)] for (a, b) in (getattr(flow.comm, "_delivered", None) or ())],
        "redo_counts": {f"{a},{b}": int(c)
                        for (a, b), c in (getattr(flow.comm, "_redo_counts", None) or {}).items()},
        "last_work_body": getattr(ref, "last_work_body", ""),  # [정밀 복구] owner 위임 원문 — 복구가 재작문 대신 replay
        # [정밀 복구 — 전체 체인] 열린 베턴 프레임 전부(원문 포함)를 영속한다. 끊김 시 owner(레벨1)만이 아니라
        # *가장 깊은 활성 워커*(체인 끝)를 그 원문으로 재개하기 위함 — 깊은 전문가 협업이 리더로 튀지 않게.
        "active_chain": [
            {"from": int(f.from_id), "to": int(f.to_id), "kind": str(getattr(f, "kind", "work")),
             "body": (getattr(f, "body", "") or "")[:1500]}
            for f in flow.comm.open_requests
            if int(f.to_id) != int(flow.comm.origin)
        ],
    }


def checkpoint_open_task(sys, flow) -> None:
    """[크래시-세이프 Task 스냅샷] 흐름 '도중' Task 전이마다 미완 Task를 레지스트리에 영속한다 —
    종전엔 흐름 '종료' 시에만 써서, 동면(컨테이너 정지)·강제종료처럼 마감 코드가 못 도는 죽음이면
    진행 중 Task의 정체가 유실돼 복구가 '같은 Task 이어가기'가 아니라 '새 Task'로 시작했다
    (라이브 관측: 093740-1 동결 → 복구가 122245-1 신설, 옛 블록은 '진행' 박제 — 사용자 지적).
    guide의 전이 지점(create_task/set_goal/owner 확정/complete_task)이 flow.checkpoint_task로 호출."""
    ch = flow.project_channel or flow.user_channel
    if not ch or int(ch) not in sys.projects:
        return
    p = sys.projects[int(ch)]
    # [기록 보존 — 마감돼도 정체는 남는다(2026-07-14, 사용자: 'Task 목표도 갑자기 없어진걸 보면')]
    # 종전엔 마감(complete_task) 체크포인트가 open_task=None으로 덮어 목표·로스터의 유일한 홈이
    # 증발 — 완결된 판의 피드 머리(목표·인원)가 화면에서 사라졌다(ch61 라이브). 활성 스냅샷을 지우기
    # 전에 last_task로 보관한다(읽기 전용 이력 — 복구 대상 아님, 피드 해석 폴백 전용).
    if flow.current is None and p.get("open_task"):
        p["last_task"] = p["open_task"]
    if flow.current is not None:
        snap = sys._task_snapshot(flow, flow.current)
        # task_snapshot은 독립 왕복에서도 개입 상태를 포함하지만, 프로젝트 레지스트리의 유일한
        # 영속 정본은 Task 생성 전에도 존재하는 root다. 같은 원자 저장에서 root로 옮겨 중복본을
        # 만들지 않고, open_task 내부 필드는 구 버전 복원 폴백으로만 읽는다.
        p["pending_info"] = snap.pop("pending_info", {})
        p["interject_retry"] = snap.pop("interject_retry", {})
        p["open_task"] = snap
    else:
        pending_info, interject_retry = _interject_state_snapshot(flow)
        p["pending_info"] = pending_info
        p["interject_retry"] = interject_retry
        p["open_task"] = None
    if getattr(flow, "file_owner", None) is not None:   # [소유 경계 영속] Task 전이마다 같이 저장
        p["file_owner"] = dict(flow.file_owner or {})
    # [미답 질문 영속(2026-07-09)] 상시 재주입 큐가 흐름 메모리라 재시작을 못 넘던 구멍 — 라이브에서
    # 리더가 질문을 받은 채 재시작되자 질문이 증발했다. 체크포인트에 동승시켜 재개 흐름이 이어받는다.
    _uq = getattr(flow, "unanswered_questions", None)
    if _uq:
        p["unanswered_questions"] = list(_uq)[-5:]
    else:
        p.pop("unanswered_questions", None)
        p["file_permits"] = {q: sorted(d) for q, d in (getattr(flow, "file_permits", {}) or {}).items() if d}  # [단순 허락 영속]
    # [마일스톤 파이프라인 §9 — 최대 저장] 주기 상태(마일스톤·SubTask·조건·증거) 전부 동승 —
    # 크래시·재시작 후 iter 중간부터 재개하는 토대. 플래그 OFF면 빈 리스트라 무비용.
    p["milestones"] = [ms_to_dict(m) for m in (getattr(flow, "milestones", None) or [])]
    # [e2e §9] 분모·항목별 결과·최종 판정도 마일스톤과 같은 원자 체크포인트에 둔다.
    # subprocess receipt ledger/nonce는 process-local이지만, 이미 소비·검증된 결과와 verifier의
    # command/spec/artifact metadata는 복원 후 다시 무결성 검사할 수 있으므로 보존한다.
    p["e2e_checklist"] = copy.deepcopy(getattr(flow, "e2e_checklist", None))
    p["e2e_results"] = copy.deepcopy(getattr(flow, "e2e_results", None))
    p["wrapup_state"] = copy.deepcopy(getattr(flow, "wrapup_state", None))
    p["e2e_origin_context"] = list(getattr(flow, "e2e_origin_context", None) or [])
    # [로드맵 §9(2026-07-14)] 전체 구조 회의가 확정한 다단계 로드맵(달구지→자동차→스포츠카) —
    # 주기 완수마다 다음 단계 회의를 코칭하는 근거라 재시작을 넘어 살아야 한다.
    p["roadmap"] = list(getattr(flow, "roadmap", None) or [])
    # [백로그 릴레이 §9 — S2] 릴레이 장부(풀·턴 홀더·차단 이력)도 같은 원칙으로 동승 —
    # 재시작 후 '누구 배분 차례였나'까지 그대로 살아난다. OFF면 빈 dict라 무비용.
    p["backlog_relays"] = {sid: r.to_ckpt()
                           for sid, r in (getattr(flow, "backlog_relays", None) or {}).items()}
    sys._save_projects()


def _restore_e2e_state(flow, proj) -> bool:
    """persisted e2e 진행을 현재 artifact/epoch와 다시 대조해 복원한다.

    미제출 verifier seal은 process-local 권한이므로 새 nonce/seal로 재무장한다. 이미 소비된 pass
    결과는 command/spec/seal/stamp가 모두 맞을 때만 보존하고, 한 필드라도 손상·stale이면 e2e 전체를
    폐기해 fresh open으로 되돌린다.
    """
    from .rule.evidence import (
        command_matches_spec, direct_verifier_command, normalize_verifier_command,
        verifier_command_hash, verifier_spec_hash,
    )
    from .rule.milestone import workspace_artifact_stamp, write_revision

    checklist = copy.deepcopy(proj.get("e2e_checklist"))
    results = copy.deepcopy(proj.get("e2e_results"))
    wrapup = copy.deepcopy(proj.get("wrapup_state"))
    flow.e2e_origin_context = list(proj.get("e2e_origin_context") or [])
    if checklist is None:
        flow.e2e_checklist = flow.e2e_results = flow.wrapup_state = None
        flow._e2e_receipt_nonce = None
        flow._run_receipts = {}
        return False
    try:
        if not isinstance(checklist, list) or not isinstance(results, dict):
            raise ValueError("invalid containers")
        stamp, epoch = workspace_artifact_stamp(flow), write_revision(flow)
        if not stamp:
            raise ValueError("no artifact stamp")
        items = {}
        for item in checklist:
            if not isinstance(item, dict):
                raise ValueError("invalid item")
            iid = str(item.get("id") or "")
            spec = str(item.get("verifier_spec") or item.get("spec") or "")
            if not iid or iid in items or not spec:
                raise ValueError("invalid item identity")
            items[iid] = item
            command = normalize_verifier_command(item.get("verifier_command"))
            if command:
                if (item.get("verifier_command_hash") != verifier_command_hash(command)
                        or item.get("verifier_spec_hash") != verifier_spec_hash(iid, spec)
                        or not (
                            bool(direct_verifier_command(
                                command, getattr(flow, "workspace", "")))
                            if item.get("verifier_structurally_ratified")
                            else command_matches_spec(
                                command, spec, getattr(flow, "workspace", ""))
                        )):
                    raise ValueError("invalid verifier")
            elif iid in results and bool((results.get(iid) or {}).get("ok")):
                raise ValueError("unbound pass")

        for iid, row in results.items():
            if iid not in items or not isinstance(row, dict):
                raise ValueError("unknown result")
            if not bool(row.get("ok")):
                continue
            item = items[iid]
            if (row.get("evidence_source") != "sys_run"
                    or not row.get("receipt_id")
                    or row.get("artifact_stamp") != stamp
                    or int(row.get("write_epoch", -1)) != epoch
                    or not item.get("verifier_used")
                    or row.get("verifier_seal") != item.get("verifier_seal")
                    or row.get("verified_command") != item.get("verifier_command")
                    or row.get("command_hash") != item.get("verifier_command_hash")
                    or row.get("spec_hash") != item.get("verifier_spec_hash")):
                raise ValueError("stale pass")

        if wrapup is not None:
            if (not isinstance(wrapup, dict)
                    or wrapup.get("verdict") not in ("e2e_pass", "e2e_fail")
                    or wrapup.get("artifact_stamp") != stamp
                    or int(wrapup.get("write_epoch", -1)) != epoch):
                raise ValueError("stale verdict")
            if wrapup.get("verdict") == "e2e_pass":
                if set(results) != set(items) or any(
                        not bool(row.get("ok")) or not str(row.get("evidence") or "").strip()
                        for row in results.values()):
                    raise ValueError("incomplete pass verdict")

        # 실행됐지만 e2e_result로 소비되지 않은 process-local receipt는 복구할 수 없다. exact command는
        # 보존하되 새 seal로 다시 한 번 실행하게 한다.
        for iid, item in items.items():
            if iid not in results and item.get("verifier_command"):
                item["verifier_seal"] = secrets.token_hex(16)
                item["verifier_actor"] = 0
                item["verifier_epoch"] = epoch
                item["verifier_stamp"] = stamp
                item["verifier_used"] = False
        flow.e2e_checklist = checklist
        flow.e2e_results = results
        flow.wrapup_state = wrapup
        # [실행 사실 복원(2026-07-27, U-065 실측)] 마감 관문은 '이 흐름에서 산출물을 run으로 실제
        # 실행했는가'(current.verified)를 요구하고, 재개 때 그 표식을 0으로 되돌린다 — 옳은 원칙이다.
        # 그런데 여기까지 온 e2e_pass는 **전 항목이 exact command + SYS receipt로 실증됐음**이 바로
        # 위에서 검증된(‘incomplete pass verdict’ 거부) 판정이다. 즉 실행은 실제로 있었고, 그 사실만
        # 안 옮겨져 '한 번도 실행하지 않았습니다'로 마감이 막혔다(재개 7회 연속 동일 정체).
        # 새 사실을 만들지 않는다 — 검증된 실행 기록을 흐름 장부로 옮길 뿐이다.
        try:
            if (isinstance(wrapup, dict) and wrapup.get("verdict") == "e2e_pass"
                    and getattr(flow, "current", None) is not None):
                flow.current.verified = True
        except Exception:
            pass
        flow._e2e_receipt_nonce = secrets.token_hex(16)
        flow._run_receipts = {}
        return True
    except (TypeError, ValueError, OSError):
        flow.e2e_checklist = flow.e2e_results = flow.wrapup_state = None
        flow._e2e_receipt_nonce = None
        flow._run_receipts = {}
        return False


async def restore_open_task(sys, flow, proj) -> Optional[dict]:
    """프로젝트에 저장된 미완 Task가 있으면 이번 흐름에 그대로 되살린다 — 같은 상태블록·스레드·담당자
    (owner)·팀을 재부착해 '이어가기'가 사용자가 Task명을 부르지 않아도 그 Task를 잇게 한다(담당자가
    판단해 이어감). 검증 누계는 0에서 시작(verified=False 등) → 완료 전 run 재검증을 강제. 되살린
    스냅샷을 반환(없으면 None)."""
    # [사람 개입 crash safety] 프로젝트 root가 새 정본이라 Task 생성 전 개입도 먼저 되살린다.
    # root 키가 둘 다 없는 구 레지스트리는 open_task 내부의 전환기 스냅샷을 아래에서 폴백한다.
    _root_interject = ("pending_info" in proj or "interject_retry" in proj)
    if _root_interject:
        _restore_interject_state(flow, proj)
    else:
        flow.pending_info = {}
        flow._ij_retry = {}
    # [마일스톤 §9 — 복원] 주기 상태는 open_task와 독립으로 되살린다(마일스톤만 있고 미완 Task가
    # 없는 시점의 죽음도 복구). 손상 스냅샷은 빈 시작으로 저하 — 복구가 복구를 못 막게.
    try:
        flow.unanswered_questions = list(proj.get("unanswered_questions") or []) or None
    except Exception:
        flow.unanswered_questions = None
    try:
        flow.milestones = [ms_from_dict(d) for d in (proj.get("milestones") or [])]
    except Exception:
        flow.milestones = []
    try:
        flow.roadmap = list(proj.get("roadmap") or [])   # [로드맵 §9 복원(2026-07-14)]
    except Exception:
        flow.roadmap = []                                 # [갭#5 격리] 손상 roadmap이 뒤 복원을 무산시키지 않게
    # [백로그 릴레이 §9 — 복원] 마일스톤과 같은 독립 복원(릴레이만 있는 시점의 죽음도 복구).
    try:
        from .rule.backlog import BacklogRelay
        flow.backlog_relays = {str(sid): BacklogRelay.from_ckpt(d)
                               for sid, d in (proj.get("backlog_relays") or {}).items()}
    except Exception:
        flow.backlog_relays = {}
    # 복원 장부를 '누가 도구로 처음 접근할 때'까지 무콜백으로 두면 구조 드라이버가 직접 pick/done한
    # 변이가 화면 미러에 안 남는다. 마일스톤 표현과 릴레이가 모두 복원된 바로 여기서 기존 릴레이만
    # relay_for에 통과시켜 log·on_change를 재결합한다(없는 릴레이를 새로 만들지는 않음).
    try:
        from .rule.backlog import relay_for, normalize_active_backlogs
        for _ms in flow.milestones:
            for _st in _ms.subtasks:
                if _st.st_id in flow.backlog_relays:
                    relay_for(flow, _st)
        # 전역 잠금 도입 전 체크포인트에 둘 이상의 SubTask가 작업 중으로 남아 있을 수 있다.
        # 복원 직후 한 번 정규화해야 구조 드라이버와 UI가 같은 단일 active를 본다.
        normalize_active_backlogs(flow)
    except Exception:
        pass
    if flow.milestones:
        # [표면 미러] 복원 직후 1회 — HUD 공백 제거. **릴레이 복원 뒤**여야 한다(장부 복원 전에 쓰면
        # 빈 백로그로 스냅샷을 덮는다 — ch53 라이브: 스냅샷 bl 전부 소실 사인, 2026-07-10).
        try:
            from .rule.milestone import persist_ms_status
            persist_ms_status(flow)
        except Exception:
            pass
    snap = proj.get("open_task")
    if not snap:
        # [고아 장부 2차 복원(2026-07-20, U-035 실측: open_task 스냅샷 유실 → current 없이 가동 →
        # 봇이 Task 안에서 새 Task 개설·goal 재주행)] 열린 주기가 살아 있는데 활성 스냅샷이 없으면,
        # 마지막 보관본(last_task — 마감 시 이관 보관)이 곧 잃어버린 그 Task다.
        # 사용자 확정: 'Task는 사용자 요청이 낳는다' — 복원이 새 개설이 아니라 원 Task를 되살린다.
        # [서브태스크 요구 제거(2026-07-20, 근본원인 핸드오프 D)] 종전 '열린 단위까지 있어야'는 구멍 —
        # 단위 분해 전 주기에서 스냅샷이 유실되면 폴백이 침묵해 current 없이 가동됐다(create_task
        # 게이트와 같은 기준으로 정렬: 열린 마일스톤 = 미완 주기).
        _ms_alive = any(m.status not in ("done", "superseded")
                        for m in (flow.milestones or []))
        snap = proj.get("last_task") if _ms_alive else None
        if not snap:
            return None
        sys._log("open_task_fallback_last", task=snap.get("task_id"))
    # root 정본이 없는 전환기/직접 task_snapshot 왕복만 Task 내부 상태를 폴백한다.
    if not _root_interject:
        _restore_interject_state(flow, snap)
    team = [int(x) for x in snap.get("team", []) if int(x) in flow.pool]
    if flow.leader not in team:
        team = [flow.leader] + team
    group = [(f"<@{i}>", flow._info(i)) for i in team]
    status = TaskStatus(task_id=snap["task_id"], purpose=snap.get("purpose", ""),
                        status="진행", goal=snap.get("goal", ""),
                        owner=snap.get("owner_name", ""), group=group)
    ref = TaskRef(task_id=snap["task_id"], thread_id=snap["thread_id"],
                  block_id=snap["block_id"], status=status, team=team,
                  owner=int(snap.get("owner") or 0))
    if snap.get("collab_notes"):
        ref.collab_notes = snap["collab_notes"]   # 합의 기록 복원 — 재개 후 위임에도 동봉(스펙 증발 방지)
    if snap.get("acceptance"):
        ref.acceptance = snap["acceptance"]        # 수용 계약 복원 — 재개 마감이 같은 기준으로 검증(증발 방지)
    if snap.get("standard"):
        ref.standard = snap["standard"]            # [최대화] 최대 표준 복원 — 동면 재개에도 바가 유지(증발 방지)
    if snap.get("interfaces"):
        ref.interfaces = snap["interfaces"]        # [협업] 인터페이스 계약 복원 — 재개 L2 검증 일관
    # [B-11 Phase C — 복구 '내용'은 문서 우선(ORGANT_DOC_COLLAB=1, off=종전 스냅샷 전용)] '내용'
    # (goal·acceptance·협의록)은 무절단 원본(.collab/T-<id>)에서, '사실'(owner·delivered·gate_pass·
    # 누계 — 아래 전부)은 항상 스냅샷에서. 문서 무결성 실패(부재·빈 파일·헤더 계약 깨짐)는 필드 단위로
    # 스냅샷 폴백 — 문서 훼손이 복구를 못 막는다.
    if doc_collab_on():
        _gdoc = dossier_read(flow, "GOAL.md", task_id=snap["task_id"])
        _sec = _parse_goal_doc(_gdoc) if _gdoc else {}
        if _sec.get("Goal"):                       # 무결성: Goal 섹션 실재 = 문서 신뢰
            ref.status.purpose = _sec.get("Purpose") or ref.status.purpose
            ref.status.goal = _sec["Goal"]
            if _sec.get("Acceptance"):
                ref.acceptance = _sec["Acceptance"]
            if _sec.get("Standard"):
                ref.standard = _sec["Standard"]
            if _sec.get("Interfaces"):
                ref.interfaces = _sec["Interfaces"]
        _mdoc = dossier_read(flow, "MINUTES.md", task_id=snap["task_id"])
        if _mdoc:
            # 협의록은 스냅샷의 head-keep 6,000자 절단본 대신 문서 원본에서 — 6,000자 넘으면 *최신*
            # 을 남기고(head-keep 유실의 교정 방향) 잘림을 표기 + 전문 경로를 가리킨다(§17).
            _dp = snap.get("dossier_path") or dossier_rel(snap["task_id"])
            ref.collab_notes = (_mdoc if len(_mdoc) <= 6000 else
                                f"…(앞 {len(_mdoc) - 6000}자 잘림 — 전문: {_dp}/MINUTES.md)\n"
                                + _mdoc[-6000:])
    ref.participated = {int(x) for x in snap.get("participated", [])}   # 협의 명단 복원(재협의 루프 차단)
    # [완료 게이트 통과 복원(2026-06-23, 사용자)] 영속된 _gate_pass(회계 통과)를 흐름에 되살린다 — 복구마다
    # acceptance·data_prov·percept 회계를 재서술하던 낭비 차단(마감이 영영 안 닫히던 결함). 검증 누계
    # (verified·cross_checks)는 종전대로 0에서 재시작하므로, 재개 시 팀의 자연 재검증으로 마감이 완성된다.
    if snap.get("gate_pass"):
        flow._gate_pass = {(n, snap["task_id"]) for n in snap.get("gate_pass", [])}
    # [위임 사실 복원(2026-06-23, 사용자)] 복구마다 work_delegated가 0으로 리셋돼 SYS 자동위임이 '리더가
    # 위임 0회 헛돈다'로 오발동하던 것 차단 — 위임 사실을 되살린다(active_chain이 보여주듯 위임은 일어났음).
    ref.work_delegated = int(snap.get("work_delegated", 0) or 0)
    ref.work_delegated_to = {int(x) for x in snap.get("work_delegated_to", [])}
    # [완료 검증 사실 복원(2026-06-23, 사용자)] owner 인도·교차검증을 되살린다 — 복구마다 핸드셰이크를
    # 다시 요구해 마감이 안 닫히던 진짜 원인 차단(verified는 종전대로 0 — 재개 때 1회 재실행).
    ref.owner_delivered = bool(snap.get("owner_delivered", False))
    ref.cross_checks = int(snap.get("cross_checks", 0) or 0)
    # [주인 표시 복원(2026-07-27, U-065 실측)] owner id는 살아 돌아오는데 표시 이름(status.owner)이
    # 비어, 이어가기 프롬프트가 'Owner: 미정'으로 나갔다 — 봇들은 주인이 안 정해진 줄 알고 마감을
    # 미루며 "owner만 확정되면 complete_task로 마감하면 된다"만 반복했다(재개 5회 연속 동일 정체).
    # 이름은 id에서 파생되는 표시값이지 새 사실이 아니다 — 있는 사실을 이름으로 되살린다.
    if getattr(ref, "owner", 0) and not str(getattr(ref.status, "owner", "") or "").strip():
        try:
            ref.status.owner = flow._info(int(ref.owner)) or f"<@{int(ref.owner)}>"
        except Exception:
            pass
    # [소유권-리더십 화해 — 재배정 신호로만] 리더십이 재배정됐으면(proj.pending_owner_reconcile=새 리더)
    # Task 소유권을 새 리더로 넘긴다. 안 그러면 스테일 owner(예: 디자이너)가 새 리더(백엔드)의 남은 도메인
    # 쓰기를 게이트#4로 막아, 봇끼리 소유권 이전만 LIFO 베턴에 반복 거부되는 순환대기 데드락(라이브 P-005).
    # 리더=owner가 되면 게이트가 안 걸리고 이전 시도 자체가 불필요 → 데드락이 형성되지 않는다. **재배정
    # 신호가 있을 때만** 발동 — 정상 인도 흐름('owner 인도+리더 검증', leader≠owner가 정상)은 안 건드림.
    # 넘긴 뒤 owner_delivered=False(새 owner는 잔여 실작업 후 인도=허위완료 방지). 신호는 1회성(소거).
    _rec = proj.get("pending_owner_reconcile") if isinstance(proj, dict) else None
    if _rec and ref.owner and int(ref.owner) != int(_rec):
        ref.owner = int(_rec)
        ref.status.owner = flow._info(int(_rec)) or ref.status.owner
        # [인도 사실 보존(2026-07-27, U-065 실측)] 소유권 재배정은 '새 owner가 잔여 실작업 없이
        # 완료를 선언'하는 것을 막으려 인도 사실을 지운다 — 작업 도중이라면 옳다. 그러나 이 Task는
        # 이미 산출물이 있고 **Task 경계 전수 검증(e2e_pass)까지 통과**한 상태다. 그때 인도 사실을
        # 지우면 관문이 '인도된 산출물 없음'으로 보아 영영 못 닫고, 재개할 때마다 같은 자리에서
        # 멎는다(실측: 재개 3회 연속 동일 정체). 검증이 산출물의 실재를 이미 증명했으므로
        # e2e_pass 상태에서는 보존한다 — 허위 완료 방지는 e2e 증거가 대신한다.
        _verified = str(((proj or {}).get("wrapup_state") or {}).get("verdict") or "") == "e2e_pass"
        if not _verified:
            ref.owner_delivered = False
        sys._log("owner_reconciled_to_leader", task=ref.task_id, new_owner=int(_rec),
                 kept_delivered=bool(_verified))
    if isinstance(proj, dict) and proj.pop("pending_owner_reconcile", None) is not None:
        try:
            sys._save_projects()
        except Exception:
            pass
    # [수렴 사실 포괄 복원(2026-06-23, 사용자: '메모리 안정적으로')] 게이트가 읽는 진행 사실 전부 복원 —
    # act_by(누가 Write/Edit/run 했나)는 contrib 게이트의 idle 판정 입력이라, 이걸 안 되살리면 복구마다
    # '전원 idle'로 마감이 막힌다(사용자가 짚은 act_by 리셋). deploy_count는 배포 런어웨이 캡이 재시작 너머
    # 누적돼 에스컬레이션이 작동하게. verified만 0 유지(허위완료 백스톱 — 재개 직후 새 run 증거 강제).
    ref.contrib_checked = bool(snap.get("contrib_checked", False))
    ref.cross_check_offdomain = int(snap.get("cross_check_offdomain", 0) or 0)
    ref.last_verify_writes = int(snap.get("last_verify_writes", -1))
    ref.acceptance_pass_writes = int(snap.get("acceptance_pass_writes", -1))
    ref.verify_fail_streak = int(snap.get("verify_fail_streak", 0) or 0)
    flow._deploy_cross = int(snap.get("deploy_cross", -1))
    ref.cross_checkers = {int(x) for x in snap.get("cross_checkers", [])}
    ref.loop_escalated = bool(snap.get("loop_escalated", False))
    ref.run_count = int(snap.get("run_count", 0) or 0)
    if snap.get("evidence"):
        ref.evidence = snap["evidence"]
    ref.cc_held = int(snap.get("cc_held", 0) or 0)
    ref.leader_writes = int(snap.get("leader_writes", 0) or 0)
    ref.peer_info_pairs = {frozenset(int(x) for x in pr) for pr in snap.get("peer_info_pairs", [])}
    for _m, _c in (snap.get("act_by") or {}).items():
        flow.act_by[int(_m)] = int(_c)
    flow._deploy_count = int(snap.get("deploy_count", 0) or 0)
    flow._deployed_once = bool(snap.get("deployed_once", False))
    flow._deploy_live = bool(snap.get("deploy_live", False))   # 배포 목표 달성 신호 복원(완료 인식 유지)
    if snap.get("deploy_url"):
        flow._deploy_url = str(snap["deploy_url"])             # [확인 링크] 재개 후 보고에도 주소 유지
    flow._deploy_writes = int(snap.get("deploy_writes", -1))
    for _r, _w in (snap.get("writes_by_role") or {}).items():
        flow.writes_by_role[_r] = int(_w)
    _restore_e2e_state(flow, proj)
    flow.consec_fail = int(snap.get("consec_fail", 0) or 0)
    if snap.get("last_work_body"):
        ref.last_work_body = snap["last_work_body"]   # [정밀 복구] owner 위임 원문 복원 → SYS 이어가기가 replay
    # [정밀 복구 — 완료잠금(구조)] 담당(owner)이 있던 미완 Task를 되살리면, owner가 '이어가기'로 재인도하기
    # 전엔 complete를 *구조로* 막는다(종전엔 resume_continue_body 프롬프트 의존 → 모델이 잊으면 조기완료 사고:
    # 라이브 054013-1 조기완료→074010-1 신설). owner_incomplete=True가 (1) complete_task 게이트로 마감을 막고
    # (2) SYS 자동 이어가기(_auto_continue_owner)가 last_work_body 원문으로 owner를 직접 재개(리더 재작문·드리프트 차단).
    # [완료 화해(2026-06-23, 사용자)] owner가 *이미 인도*했으면(owner_delivered 영속) 복구가 미완으로 잡지
    # 않는다 — 인도 사실이 살아있으니 '이어서 끝내라'를 또 요구하지 않고 마감 가능(인도 핸드셰이크 반복 차단).
    if int(snap.get("owner") or 0) and not snap.get("owner_delivered"):
        ref.owner_incomplete = True
    # [정밀 복구 — 가장 깊은 워커 재개(#7)] 전체 체인(active_chain)이 있으면, 재개 owner를 *가장 깊은 활성
    # 워커*로 덮어쓴다 — 레벨1 owner가 아니라 끊긴 그 깊이(예: 8단 체인 끝의 디자이너)에서 재개해 깊은
    # 전문가 작업이 리더로 튀지 않게. last_work_body에 그 깊이 원문 + 체인 경로를 실어, #3의 _auto_continue_
    # owner가 그 워커를 정확히 재개하게 한다(상류 이미 끝난 부분은 작업공간 보존 → 리더가 통합).
    chain = snap.get("active_chain") or []
    if chain:
        deepest = chain[-1]
        wk = int(deepest.get("to") or 0)
        # 가장 깊은 프레임이 *진짜 더 깊은 워커*일 때만 덮어쓴다 — 리더/origin 프레임이거나 원문이 비면
        # (동기 완주로 깊은 위임이 이미 닫힘) 레벨1 owner 로직(#3)을 그대로 둔다(오발동 방지).
        if (wk and wk in flow.pool and wk != flow.leader and (deepest.get("body") or "").strip()):
            ref.owner = wk
            ref.status.owner = flow._info(wk) or f"<@{wk}>"
            path = " → ".join(f"{flow._info(c.get('from'))}→{flow._info(c.get('to'))}" for c in chain)
            ref.last_work_body = (
                f"[끊긴 깊은 전문가 체인: {path}]\n[가장 깊은 이 작업을 당신({flow._info(wk)})이 받아 진행 중 "
                f"끊겼습니다 — 작업공간에 이미 된 부분은 보존됨. 처음부터 다시 하지 말고 이어서 완성하세요]\n"
                f"{(deepest.get('body') or '')[:1200]}")
            ref.owner_incomplete = True
            if wk not in ref.team:
                ref.team.append(wk)
            # [복구 인플라이트 보존(2026-06-23, 사용자)] 죽기 전 진행 중이던 깊은 위임(→wk)을 복원했으니,
            # 리더가 이 일을 *다른 사람에게 새로 위임*(fresh)으로 덮어써 인플라이트 워커의 작업·보고를
            # 버리는 것(라이브 P-031: 황시윤 응답 없이 리더가 이서연에게 새 request)을 막는다. 리더
            # 개입 노트에 'SYS가 이 워커를 재개하니 새로 위임 말고 보고를 기다리라'를 실어 보호한다.
            snap["deep_chain_inflight"] = flow._info(wk) or str(wk)
            # [정밀 복구(2026-06-23, 사용자)] 전체 체인을 저장 → 호출부(route_channel_request)가 restore_chain
            # 으로 comm 스택을 그대로 세우고 가장 깊은 워커부터 재개(C→B→A unwind, 각자 범위 보존). 평탄화
            # (리더→C 직접)로 B가 빠지던 것 교정. 미설정이면 종전 _auto_continue_owner(평탄화)가 폴백.
            ref.precise_chain_frames = list(chain)
            sys._log("deep_chain_restored", depth=len(chain), deepest=wk, task=ref.task_id)
    flow.tasks.append(ref)
    flow.current = ref
    # 되살린 Task 멤버를 프로젝트 팀에 **합친다(union)** — 덮어쓰면 그 Task에 낀 일부 멤버로
    # project_team이 축소돼, 같은 프로젝트에서 일하던 팀원이 이후 '이 프로젝트 팀이 아님'으로
    # 거부되던 라이브 버그(복원이 팀을 좁힘 — 사용자 관측). 좁히지 않고 넓히기만 한다(리더 포함).
    for x in [flow.leader] + team:
        if x not in flow.project_team:
            flow.project_team.append(x)
    # [재위임 런어웨이 차단(2026-06-23 전수감사)] 종전엔 복구마다 reset_task_tracking()으로 _delivered(완료
    # (위임자→owner) 쌍)·_redo_counts를 *삭제* → 리더가 이미 끝난 일을 '새 위임'(Redo 아님)으로 재발사,
    # redo_limit 백스톱이 안 걸려 같은 일을 풀로 재작업하던 churn(1346 run의 동력)이었다. 삭제 대신 *복원* —
    # 완료 사실은 영속이라 복구가 이미 끝난 쌍을 재위임하면 Redo로 인식돼 한도가 작동한다(reset은 새 Task 때만).
    flow.comm._delivered = {(int(a), int(b)) for a, b in (snap.get("delivered_pairs") or [])}
    flow.comm._redo_counts = {}
    for _k, _c in (snap.get("redo_counts") or {}).items():
        try:
            _a, _b = _k.split(",")
            flow.comm._redo_counts[(int(_a), int(_b))] = int(_c)
        except Exception:
            pass
    try:
        await flow.refresh(ref)   # 상태블록을 '진행'으로 재활성(블록이 남아 있으면)
    except Exception:
        pass
    sys._log("open_task_restored", project=proj.get("id"), task=snap["task_id"],
             owner=int(snap.get("owner") or 0))
    return snap
