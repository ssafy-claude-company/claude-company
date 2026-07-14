"""Organt가 쓰는 Guide 도구셋 (P2P Communication + 다중 Task + 팀 배정 모델).

회사식 인력 구조: **채용 풀(전체 로스터) → 프로젝트 팀(규모 산정해 배정) → Task 팀(필요 인원)**.
- 깨어난 Organt는 `request`로 *현재 Task 팀의 동료*에게 요청한다(Info=질문/Work=작업).
- 인원이 부족하면 `recruit`로 풀에서 현재 Task에 합류시킨다("더 필요하면 더 가져온다").
SYS가 대상 동료를 중첩 베턴으로 깨워(flow.wake) 응답을 돌려준다 → 항상 1명만 활성(단일흐름).

리더(첫 Organt)는 추가로:
- create_project(name, team): 규모를 산정해 프로젝트 팀 배정 + 전용 채널 생성
- create_task(purpose, goal, members): Task에 필요한 인원 배정 + 상태블록/Thread 생성(반복 가능)
- complete_task(result): 현재 Task를 완료로 마감
대화는 '현재 Task' 스레드에서. 보고는 별도 툴이 아니라 반환값(=Response)이 origin까지 unwind.
"""
import os
import signal
import subprocess
import tempfile

import anyio

from claude_agent_sdk import create_sdk_mcp_server, tool

from ._util import _dbg, _looks_transient, _ok, _speech_clip  # noqa: F401  [_speech_clip: sys_core + PJT/tests(test_sys)가 파사드에서 직접 import — 유지, _looks_transient: system/tests(test_misc)가 파사드에서 import — 유지]

from .tool_names import ORIGIN, FLOW_TOOLS, COORD_TOOLS, LEADER_TOOLS  # noqa: F401  [FLOW/COORD/LEADER_TOOLS: guide/discord_main·PJT(organt_discord·scripts·tests) 소비 — 유지, ORIGIN: 소비 불확실 — 호환 유지]

# run 툴 안전 차단: 파괴/탈출/저장소·시스템 경로/네트워크 외 명령은 막는다(npm·node·curl·python은 허용).
_RUN_DENY = ("rm -rf", "rm -r ", "sudo", "shutdown", "reboot", "mkfs", "dd if=", ":(){",
             "git ", "/home/user/pjt", "/etc/", "/usr/", "/root", "> /", "chmod ", "chown ",
             "pkill", "kill -9 1 ", "wget ", "ssh ", "scp ", "npm publish", "history",
             # 비밀 읽기 차단(심층방어) — 권한강등이 1차 방어, 이건 비루트 폴백·명시 차단.
             ".guide_env", "/environ", "/tmp/claude-0",
             # [B-08 — Task Dossier 쓰기 보호 2중] permissions 훅은 Write/Edit만 잡는다 —
             # bash `cp/mv/sed -i/rm` 우회(_RUN_AUTHOR는 heredoc·cat>·tee만 차단)를 여기서 막는다.
             # 슬래시 포함형만 deny-tuple에(x.collaboration.js 오탐 방지) — 무슬래시는 아래 전용
             # regex(_COLLAB_RE — 단어 경계)가 처방 메시지와 함께 잡는다.
             ".collab/")

# [B-08] run 명령의 .collab 참조 판정 — '.collab' 뒤에 영숫자가 이어지면(.collaboration 등) 오탐이라
# 제외하는 단어 경계 regex. deny-tuple의 generic 메시지 대신 '어디로 기록하나' 처방을 붙이기 위한 전용 검사.
import re as _re
_COLLAB_RE = _re.compile(r"\.collab(?![0-9a-z])")
# [C9 보강 — 순수 additive] 위 '/home/user/pjt'는 표준 설치 경로 하드코딩 — 임의 경로에 설치하면 두뇌
# 소스(ORGANT_PJT)가 셸 차단에서 빠진다. 실제 설치 경로를 env에서 파생해 추가한다(비면 제외, 기존 항목 전부 유지).
_ORGANT_PJT_DENY = os.environ.get("ORGANT_PJT", "").rstrip("/").lower()
if _ORGANT_PJT_DENY:
    _RUN_DENY += (_ORGANT_PJT_DENY,)
# run으로 '파일 작성'(heredoc·cat>·tee)을 막는다 — 산출물 작성/수정은 Write/Edit로 해야 권한·협의
# 게이트(협의 중 선구현 금지)가 적용되고 '누가 무엇을 만들었나'가 기록된다. run은 실행·빌드·검증 전용.
# (이 백도어로 리더가 위임 없이 전부 혼자 작성해 독점하거나, 협의 단계 동료가 선구현하는 걸 차단.)
_RUN_AUTHOR = ("<<", "cat >", "cat>", "tee ", "tee\t")

# [run 셸 비밀 차단 — 봇 키 유출 방지] run은 작업공간 검증용 셸이지만 부모(러너) 환경을 그대로 물려받아,
# RENDER_KEY·GH_PAT 같은 배포 자격증명이 env에 있으면 `echo $RENDER_KEY`/`env`/`curl -X DELETE`로 읽혀
# 악용될 수 있다(deny-list는 rm/git/sudo만 막지 env 노출은 못 막음). deploy 도구는 *인프로세스*로 키를 쓰므로
# (os.environ 직접 읽음·서브프로세스 아님) 배포 능력은 그대로 두고, run 서브프로세스 env에서만 비밀을 지운다
# → 봇은 배포는 할 수 있어도(deploy 도구) 키를 읽을 수는 없다. PATH 등 빌드에 필요한 일반 env는 보존.
_SECRET_ENV_EXACT = {
    "RENDER_KEY", "RENDER_API_KEY", "RENDER_OWNER", "GH_PAT", "GH_USER",
    "GITHUB_TOKEN", "GITHUB_PAT", "ORGANT_GUIDE_TOKEN", "ORGANT_GUIDE_TOKENS",
}
_SECRET_ENV_SUBSTR = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "_API_KEY", "APIKEY",
                      "PRIVATE_KEY", "RENDER_KEY", "GH_PAT")


def _is_secret_env(name: str) -> bool:
    u = (name or "").upper()
    return u in _SECRET_ENV_EXACT or any(s in u for s in _SECRET_ENV_SUBSTR)


def _scrubbed_run_env() -> dict:
    """봇 run 셸용 환경 — 부모 env 복사본에서 배포·인증 비밀만 제거(PATH·HOME 등 빌드 필수 env는 유지)."""
    return {k: v for k, v in os.environ.items() if not _is_secret_env(k)}


def _run_drop_creds():
    """[권한강등 — 비밀 파일 읽기 근본차단] env-scrub는 봇 *자기 env*만 지운다 — 러너가 root면 봇 셸도
    root라 `cat .guide_env`·`cat /proc/<러너>/environ`으로 비밀(RENDER_KEY·GH_PAT·AI_API_KEY·
    ORGANT_GUIDE_TOKEN)을 우회로 읽을 수 있다(라이브 확인됨). run 셸을 비특권 사용자로 떨어뜨리면
    600 root 파일·root 프로세스 environ을 *권한 자체로* 못 읽는다(node·npm 빌드는 HOME·캐시를
    작업공간으로 잡아주면 정상). 루트가 아니면(로컬 개발) None — 이미 비특권. 사용자명은
    ORGANT_RUN_USER로 교체 가능(기본 nobody). 강등불가 시 deny-list가 폴백."""
    try:
        if os.geteuid() != 0:
            return None
        import pwd
        r = pwd.getpwnam(os.environ.get("ORGANT_RUN_USER") or "nobody")
        return (r.pw_uid, r.pw_gid)
    except (KeyError, AttributeError, OSError):
        return None


_NO_CHOWN = {"/", "/tmp", "/var", "/var/tmp", "/home", "/usr", "/etc", "/root", "/opt", "/srv"}


def _chown_tree(path, uid, gid):
    """작업공간을 강등 사용자 소유로 — 산출물·node_modules·빌드 출력 기록 가능하게. 실패는 무시(최선).
    공유/시스템 루트(/tmp 등)는 통째 chown 금지 — 격리된 흐름별 작업공간만 대상(오용·테스트 방어)."""
    try:
        rp = os.path.realpath(path)
        if rp in _NO_CHOWN or rp.count(os.sep) < 2:
            return                                          # 공유 루트 → 강등은 하되 chown은 건너뜀
        os.chown(rp, uid, gid)
        for root, dirs, files in os.walk(rp):
            for n in dirs + files:
                try:
                    os.chown(os.path.join(root, n), uid, gid, follow_symlinks=False)
                except OSError:
                    pass
    except OSError:
        pass


# [Task Rule → rule/task.py] 완료·인수 검증 게이트는 원래 §7 설계대로 rule/task로 분리(guide_tools 병합 해체)
from .rule.task import (_perceptual_essential, _wants_real_data,  # noqa: F401  [PJT/tests(test_sys)가 파사드에서 직접 import — 유지]
                        _has_real_dataset, _synthesizes_data, _is_verifier)
# [마일스톤 파이프라인 — S1(PIPELINE_REWORK_2026-07-09)] 도구 표면·회의 설명 분기가 소비.
from .rule.milestone import (pipeline_on as _pipe_on, rule_renegotiate, rule_report_iter,
                             rule_set_milestone, rule_set_subtask)
from .rule.wrapup import (rule_e2e_finish, rule_e2e_open, rule_e2e_result,
                          rule_e2e_scope)


# [스태핑 커버리지 — 리더 흡수 차단(2026-06-19, 사용자: '전문가 분배 무조건, 리더는 자기 직군만')]
# 기존 게이트(#4 owner도메인 대리구현 금지 / #6 리더독식)는 '전문가가 *있으면*' 리더 흡수를 막지만,
# 리더가 그 도메인 전문가를 *안 뽑으면*(언더스태핑) 보호할 owner가 없어 리더가 흡수한다(라이브 P-022:
# 'AI를 학습' 요청에 AI엔지니어 미투입 → 백엔드 리더가 AI·data 53건 흡수). 그래서 set_goal에서 '목표가
# *명시적으로* 부른 전문 능력을 팀이 보유했나'를 본다 — 없으면 recruit 강제(그러면 owner가 박혀 기존
# #4가 자동으로 리더를 자기 직군에 가둠). 기능 식별(능력 needs↔팀 라벨)이라 직군 타이틀 하드코딩이 아니다.
# 고신호 능력만(오발 최소). 새 능력은 (이름, needs(text)→bool, providers(label keywords)) 한 줄로 확장.
# [팀·역량 라우팅 Rule → rule/communication] guide_tools 병합 해체(re-export로 도구·tests 호환)
from .rule.communication import _say as _rule_say, vote as _rule_vote  # noqa: F401  [발언·표결 → rule/communication]
from .rule.communication import vote_stop as _rule_vote_stop  # noqa: F401  [중지 투표 → comm_ceremonies]
from .rule.communication import request as _rule_request  # noqa: F401
from .rule.communication import recruit as _rule_recruit  # noqa: F401
from .rule.communication import parallel_work as _rule_parallel_work  # noqa: F401
from .rule.communication import meet as _rule_meet  # noqa: F401
from .rule.communication import (_capability_gaps, _needed_caps_coverage, _offdomain_capability_hit,  # noqa: F401  [스택(permissions·sys_core·system/tests) + PJT/tests(test_sys) 소비 — 유지]
                                 _norm_job, _jobs_of, _JOB_SEP)  # _JOB_SEP: guide/discord_guide.py:324가 참조 표기 — 호환 유지
from .rule.communication import _clarify_hold  # [G2 — clarify 행동 잠금(B-02)] run 경로도 동일 조건




def _reap_pgroup(pgid: int):
    """프로세스그룹 pgid에 남은 프로세스를 모두 종료한다(백그라운드 서버 누수 차단).
    셸을 self-session으로 띄우면 모든 자손이 pgid==셸pid를 공유한다. 다만 리더(셸)가
    먼저 끝나 reap되면 '고아 프로세스그룹'이 돼 killpg가 안 먹으므로, /proc를 훑어
    pgid가 같은 잔여 프로세스를 PID로 직접 SIGKILL한다(이게 run 간 포트충돌의 구조적 해결)."""
    try:
        os.killpg(pgid, signal.SIGKILL)   # 리더 생존 시 빠른 경로
    except (ProcessLookupError, PermissionError, OSError):
        pass
    me = os.getpid()
    try:
        entries = [d for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return
    for d in entries:
        pid = int(d)
        if pid == me:
            continue
        try:
            with open(f"/proc/{pid}/stat", "rb") as f:
                data = f.read()
            # stat: 'pid (comm) state ppid pgrp ...' → comm의 마지막 ')' 뒤 3번째가 pgrp
            if int(data[data.rindex(b")") + 1:].split()[2]) == pgid:
                os.kill(pid, signal.SIGKILL)
        except (OSError, ValueError, IndexError):
            continue


from .rule.task import TaskRef, create_task as _rule_create_task  # noqa: F401  [Task 상태·도구로직 → rule/task; TaskRef: sys_core + PJT/tests(test_sys) 소비 — 유지]
from .rule.task import complete_task as _rule_complete_task  # noqa: F401
from .rule.task import set_goal as _rule_set_goal  # noqa: F401


from .flow import Flow  # noqa: F401  [Flow 상태 → flow.py]
# [배포 타겟 호환 — Render Node 전용(2026-06-22 P-028 규명)] deploy_sync는 Node만 빌드한다(runtime:node
# 하드코딩, package.json 필수). 흔한 사고: Node 서버가 *런타임*에 Python을 spawn/exec → Render Node 환경엔
# Python이 없어 백엔드가 안 떠 502(P-028: ECONNREFUSED:8001, 28모델 고아). 런어웨이 5회 상한은 *사후* 차단
# [Project Rule → rule/project.py] 배포 신원·적합성은 원래 §7 설계대로 분리(guide_tools 병합 해체). re-export로 호환.
from .rule.project import deploy_service_name, _deploy_infeasibility, create_project as _rule_create_project  # noqa: F401  [deploy_service_name: sys_core + PJT/tests, _deploy_infeasibility: PJT/tests(test_sys) 소비 — 유지]
from .rule.project import deploy as _rule_deploy  # noqa: F401
from .rule.project import send_file as _rule_send_file  # noqa: F401












def _holds_completion(flow, me_id, role) -> bool:
    """[완료 권한 = 리더(전 방식 복귀 — 사용자 2026-07)] complete_task(마감)는 리더가 쥔다. QA 검증은 별도로
    _gate_cross_check가 **하드 의무**로 강제한다(owner 인도 후 다른 멤버 검증 0이면 마감 보류 — 리더가 peer/QA
    검증 없이는 못 닫음). 즉 '리더 완료 + QA 검증 필수'가 원래 구조다. 앞서 시도한 'QA에게 완료권 이관'은 엉뚱한
    데를 고친 것이라 되돌림 — 라이브 P-005의 비수렴 근본은 완료권이 아니라 ① 진짜 버그(키보드 회귀) ② 목표-유계
    부재(배포 됐는데 목표 밖 이슈를 안에서 무한 수정)였다. 그건 목표-유계로 따로 해결한다."""
    return role == "leader"


def make_guide_tools(flow: Flow, me_id: int, role: str, mode: str = "collab"):
    # [G3 — 캐주얼 도구 미장착(B-06)] mode="casual"이면 협업·제작 도구(request·recruit·리더도구)를 아예
    # 장착하지 않고 run만 준다(일상 대화 턴의 오발 프로젝트를 프롬프트가 아니라 구조로 차단 — 스키마 토큰도
    # 절약). 기본값 "collab"은 현행과 동일(하위호환 — 기존 호출부 무변경).
    g = flow.guide
    tools = []

    async def _say(who, text):
        return await _rule_say(flow, who, text)   # [→ rule/communication._say] 발언을 봇 본인 명의로(가시성=실체)

    @tool("request", "현재 Task 팀의 동료 한 명에게 요청(kind: Info=질문 / Work=작업, to_id 문자열). "
          "미완 owner가 있는 일을 타인에게 새로 맡길 땐 takeover_reason(담당 교체 사유) 또는 "
          "different_deliverable(별개 산출물임을 명시) 인자를 함께(선택 — 없으면 이어가기 안내로 보류될 수 있음). "
          "직군밖 차단의 의식적 예외는 override_reason(왜 그 동료가 맡아야 하는지)로(종전 body '[직군초과: 사유]'와 동등).",
          {"to_id": str, "kind": str, "body": str, "takeover_reason": str, "different_deliverable": str,
           "override_reason": str})
    async def request(args):
        return await _rule_request(flow, me_id, role, args)
    tools.append(request)

    @tool("recruit",
          "동료가 필요하면 **지목하지 말고 '필요'를 공고**한다(진짜 채용): recruit(need='어떤 문제/"
          "일손이 필요한지') → 한가한 동료 전원이 공고를 받고 스스로 지원([지원]+지원서)하거나 "
          "패스한다 → 지원서가 돌아오면 recruit(member=지원자, reason=선발 사유)로 확정. "
          "직군을 미리 정할 필요 없다 — 문제에 집중하라(원하면 role=로 참고 표기; 직군 없는 지원자는 "
          "지원서에 [직군: 이름]을 선언). 지원하지 않은 동료의 지명은 거부된다(독단 영입 금지). "
          "지원자가 없고 role이 있으면 신규 채용(genesis) 자동. **1봇 1직업**(겸직은 예비 없음/유사 "
          "일일 때만, 최대 2) · 직군명은 기존 것 재사용 우선(변형 금지, 신설은 new_role='yes').",
          {"member": str, "need": str, "role": str, "reason": str, "new_role": str})
    async def recruit(args):
        return _ok(await _rule_recruit(flow, me_id, role, args))
    tools.append(recruit)

    # [마일스톤 파이프라인 — 공통 표면(전 참여자)] SubTask 추가(자발 참여의 문)와 iter 검증 제출은
    # 결정권자 전용이 아니다 — 현장 누구나. 플래그 OFF면 미등록(표면 불변).
    if _pipe_on():
        @tool("set_subtask",
              "진행 중 마일스톤에 분해 단위(SubTask)를 추가한다 — 팀 판에선 회의 수렴안에 '단위: 목표 | "
              "실증절차' 줄로 동봉해 **가결과 함께 등록**되는 게 정석(개인 등록은 솔로 판만). 단위는 팀 "
              "공유 컨테이너고, **전담은 백로그 단위** — 자기 몫은 pick_backlog(desc)로 등재해 집는다.",
              {"goal": str, "criteria": str})
        async def set_subtask(args):
            from .rule.milestone import flush_pipeline_notes as _flush
            _r = _ok(rule_set_subtask(flow, me_id, args))
            await _flush(flow)
            return _r
        tools.append(set_subtask)

        @tool("pick_backlog",
              "**순차 릴레이 — 한 번에 한 백로그.** desc='내가 할 일'로 내 백로그를 풀에 등재한다(st=단위 "
              "id/목표 일부로 소속 지정). 아무도 작업 중이 아니고 내 차례면 즉시 착수, 아니면 대기(내 "
              "차례는 마무리자 선정으로 온다). id='B3'는 **마무리자(직전 완료·중단자)만** — 남은 백로그 중 "
              "하나를 골라 그 제출자를 다음 수행자로 선정한다. **작업(run/Write)은 착수된 뒤에만.**",
              {"id": str, "desc": str, "st": str})
        async def pick_backlog(args):
            from .rule.backlog import relay_for, BacklogError, DuplicateBacklog, IN_PROGRESS
            from .rule.milestone import _set_pipeline_ctx
            ms = next((m for m in (getattr(flow, "milestones", None) or []) if m.status not in ("done", "superseded")), None)
            _sts = [x for x in ms.subtasks if x.status not in ("done", "superseded")] if ms else []
            if not _sts:
                return _ok("활성 SubTask가 없습니다 — 단위 분해는 회의 수렴안('단위:' 줄)으로 가결과 함께 등록됩니다.")
            bid = str(args.get("id") or "").strip()
            desc = str(args.get("desc") or "").strip()
            _stq = str(args.get("st") or "").strip()
            try:
                if bid:
                    # [선정(2026-07-14)] 마무리자가 남은 백로그를 골라 그 제출자를 다음 수행자로 — 순차.
                    r, b = None, None
                    for _x in _sts:
                        _r = relay_for(flow, _x)
                        if any(x.backlog_id == bid for x in _r.backlogs):
                            r, b = _r, _r.get(bid)
                            break
                    if b is None:
                        return _ok(f"선점 불가: 백로그 {bid}가 열린 단위 어디에도 없습니다.")
                    if b.status == IN_PROGRESS and int(b.assignee or 0) == int(me_id):
                        _set_pipeline_ctx(flow, me_id)
                        return _ok(f"백로그 {b.backlog_id}는 이미 당신이 작업 중입니다 — 이어서 하세요.")
                    _assn = int(b.submitter)
                    r.pick(int(me_id), b.backlog_id, _assn)      # relay가 배분권(마무리자)·순차 잠금 검증
                    _who = flow._info(_assn) if hasattr(flow, "_info") else _assn
                    if _assn == int(me_id):
                        _set_pipeline_ctx(flow, me_id)
                        return _ok(f"백로그 {b.backlog_id} 착수 — 작업하세요.")
                    return _ok(f"[다음 선정] {b.backlog_id} → {_who}(제출자)를 다음 수행자로 선정 — 곧 깨어나 "
                               f"착수합니다. 선정 사유는 채널에 남기세요.")
                elif desc:
                    # [순차 1명 1개(2026-07-14, 사용자: '한명씩 여러개 등록이 아닌 순차적으로 1명씩
                    # 1개씩 돌아가며 — 균등 분배')] 내가 이미 미종결(open/in_progress/blocked) 백로그를
                    # 갖고 있으면 새 등재 거부 — 한 사람이 여러 개 선점해 몰아쥐는 것 차단.
                    _mine_open = next((b.backlog_id for x in _sts if (getattr(flow, "backlog_relays", None) or {}).get(x.st_id)
                                       for b in flow.backlog_relays[x.st_id].backlogs
                                       if int(b.submitter) == int(me_id) and b.status not in ("done", "dropped")), None)
                    if _mine_open is not None:
                        return _ok(f"등재 거부: 당신은 이미 백로그 {_mine_open}(미종결)를 갖고 있습니다 — "
                                   f"백로그는 1명 1개씩 순차 등재(균등 분배)입니다. 그것을 완료(report_iter)/"
                                   f"중단(drop_backlog)한 뒤 다음을 등재하세요.")
                    _tgt = None
                    if _stq:
                        _tgt = next((x for x in _sts if _stq in x.st_id or _stq.lower() in x.goal.lower()), None)
                        if _tgt is None:
                            return _ok(f"등재 불가: '{_stq}'와 맞는 열린 단위가 없습니다. 열린 단위: "
                                       + " · ".join(f"{x.st_id}({x.goal[:20]})" for x in _sts[:8]))
                    if _tgt is None:
                        _tgt = next((x for x in _sts if int(me_id) in (getattr(x, "participants", None) or set())), _sts[0])
                    r = relay_for(flow, _tgt)
                    try:
                        b = r.submit(int(me_id), desc[:140])   # 풀에 등재(OPEN)
                    except DuplicateBacklog as e:
                        return _ok(str(e))
                    _tgt.participants.add(int(me_id))
                    _tgt.backlog_ids = [x.backlog_id for x in r.backlogs]
                    # [순차 착수 정책(2026-07-14)] 첫 착수(turn_holder None) 또는 내가 마무리자일 때만 즉시
                    # 착수 — 아니면 등재만(대기). 내 차례는 마무리자의 pick_backlog(id) 선정으로 온다.
                    _th = r.turn_holder
                    if _th is not None and int(_th) != int(me_id):
                        return _ok(f"백로그 {b.backlog_id} 등재 완료(대기) — 지금 배분권은 마무리자"
                                   f"({flow._info(_th) if hasattr(flow,'_info') else _th})에게 있습니다. "
                                   f"당신 차례는 그의 선정으로 옵니다.")
                    try:
                        r.pick(int(me_id), b.backlog_id, int(me_id))
                    except BacklogError as e:
                        return _ok(f"백로그 {b.backlog_id} 등재 완료(대기) — {e} 당신 차례가 오면 착수합니다.")
                else:
                    return _ok("id(마무리자 선정) 또는 desc(내 백로그 등재) 중 하나가 필요합니다.")
            except BacklogError as e:
                return _ok(f"선점 불가: {e}")
            _set_pipeline_ctx(flow, me_id)        # 이 턴의 이후 게시부터 이 백로그로 귀속
            return _ok(f"백로그 {b.backlog_id} 착수 — 작업하세요. 완료는 report_iter(조건 검증) 또는 위임 마무리가 장부에 반영합니다.")
        tools.append(pick_backlog)

        @tool("drop_backlog",
              "**중단**: 내 백로그(내가 제출/수행 중)를 완수 불가로 판단해 장부에서 제외한다 — 백로그는 "
              "개인 역량 안이어야 하며, 불가 판단도 본인 몫. blocked(선행 대기·재방문)와 다르게 중단은 "
              "종결이다. 중단하면 당신이 다음 선정의 담당자가 된다. id=백로그, reason=왜 불가한가(필수).",
              {"id": str, "reason": str})
        async def drop_backlog(args):
            from .rule.backlog import relay_for, BacklogError, handoff_note
            from .rule.milestone import flush_pipeline_notes as _flush
            ms = next((m for m in (getattr(flow, "milestones", None) or []) if m.status not in ("done", "superseded")), None)
            _sts = [x for x in ms.subtasks if x.status not in ("done", "superseded")] if ms else []
            bid = str(args.get("id") or "").strip()
            reason = str(args.get("reason") or "").strip()
            if not bid or not reason:
                return _ok("id와 reason(왜 완수 불가인가)이 모두 필요합니다 — 중단은 기록이 남는 종결입니다.")
            r, b = None, None
            for _x in _sts:
                _r = relay_for(flow, _x)
                if any(x.backlog_id == bid for x in _r.backlogs):
                    r, b = _r, _r.get(bid)
                    break
            if b is None:
                return _ok(f"중단 불가: 백로그 {bid}가 열린 단위 어디에도 없습니다.")
            try:
                r.drop(int(me_id), bid, reason)
            except BacklogError as e:
                return _ok(f"중단 불가: {e}")
            handoff_note(flow, r, me_id, "중단됐습니다")
            _res = _ok(f"백로그 {bid} 중단(처리 제외) — 사유가 장부에 남았습니다. 당신이 다음 선정의 "
                       f"담당자입니다: 남은 백로그 보유자들의 사유를 듣고 pick_backlog(id)로 선정하세요.")
            await _flush(flow)
            return _res
        tools.append(drop_backlog)

        @tool("report_iter",
              "진행 중 주기의 완수조건 실증 결과를 제출한다(검증 참여자 누구나). results=한 줄에 "
              "'조건 | pass/fail | 증거(run 출력 요지)' — **증거 없는 pass는 인정되지 않는다**. "
              "target=SubTask id(또는 goal 일부)를 주면 그 SubTask의 검증 — 통과 시 잔여 백로그가 "
              "자동 정리되고 SubTask가 닫힌다. 비우면 마일스톤 검증: 전부 실증되면 wrapup(잔여 정리)로 "
              "넘어가고, 정리가 끝나면 wrapup='done'으로 닫는다. 마감은 사람이 아니라 조건이다.",
              {"results": str, "target": str, "wrapup": str})
        async def report_iter(args):
            from .rule.milestone import flush_pipeline_notes as _flush
            _r = _ok(rule_report_iter(flow, me_id, args))
            await _flush(flow)
            return _r
        tools.append(report_iter)

        # [결정권자 폐지(2026-07-09, 사용자)] 확정=회의 종결 표결(가결 시 수렴안 자동 등록).
        # set_milestone은 '서기' 표면(누구나 — 표결 없이 열 때·복기 때), 재협상도 누구나(게이트=사람 승인).
        @tool("set_milestone",
              "**마일스톤**(Task의 큰 주기: 목표+완수조건)을 등록한다 — 누구나(서기 역할). 정석은 "
              "meet 회의의 종결 표결에 [수렴안]을 동봉해 가결로 자동 등록되는 것이고, 이 도구는 그 외 "
              "경로(단독 소형 주기·복기)용. goal=목표 한 줄, criteria='조건 | 실증절차' 줄들. "
              "소망형·실행 불가 조건은 등록이 거부된다. 조건 충족이 주기를 닫는다 — 사람이 아니라.",
              {"goal": str, "criteria": str})
        async def set_milestone(args):
            from .rule.milestone import flush_pipeline_notes as _flush
            _r = _ok(rule_set_milestone(flow, me_id, args))
            await _flush(flow)
            return _r
        tools.append(set_milestone)

        @tool("renegotiate_criterion",
              "[조건 재협상 — 누구나] 완수조건이 환경상 달성 불가일 때의 정식 출구. 정체 경보(진전 "
              "없는 반복)가 뜨면 무한 반복하지 말고 이걸로 올린다. target=조건(desc 일부), reason=왜 "
              "불가능한가. **사람 승인**이 오면 그 조건은 포기(waive)되고 나머지로 주기가 진행된다.",
              {"target": str, "reason": str})
        async def renegotiate_criterion(args):
            from .rule.milestone import flush_pipeline_notes as _flush
            _r = _ok(rule_renegotiate(flow, me_id, args))
            await _flush(flow)
            return _r
        tools.append(renegotiate_criterion)

    @tool("run",
          f"작업공간에서 명령을 실행해 산출물을 직접 검증(빌드/구동/테스트). cwd={flow.workspace or '작업공간 루트'} "
          f"(이 절대경로가 작업공간 — `/workspace` 아님). 60s 제한, "
          "웹 작품은 **실제 브라우저 검증 가능**: playwright+chromium 설치됨 — 예: PJT venv의 python -c로 "
          "sync_playwright 페이지 로드→로드시간·콘솔에러·스크린샷 확인('실행됨'과 '사용할 만함'은 다르다). "
          "출력 반환. 서버 구동은 'node server.js & sleep 1; curl -s localhost:3000/'처럼 백그라운드+점검으로 "
          "묶으면 됨 — run이 끝나면 백그라운드 프로세스까지 자동 정리하므로 kill 불필요(다음 run의 포트 충돌 없음). "
          "파괴·git·시스템경로 명령은 차단.",
          {"command": str})
    async def run(args):
        cmd = str(args.get("command", ""))
        _hold = _clarify_hold(flow, me_id)   # [G2 — clarify 행동 잠금(B-02)] 되묻기 답 오기 전 추측 실행 금지
        if _hold:
            return _ok(_hold)
        if not getattr(flow, "workspace", None):
            return _ok("실행 불가: 작업공간이 설정되지 않았습니다.")
        # [단일활성 구조화 — 논블로킹 핸드오프] 내가 위임을 보내 그 동료가 지금 활성(베턴=동료)인데 내가
        # solo run을 돌리면 '리더+동료 동시 실행'(이중 활성)이 된다. 핸드오프는 request를 즉시 반환하므로
        # 프롬프트가 아니라 구조로 막는다: 내 인플라이트 위임이 살아 있고 내가 비활성이면 run을 거부하고
        # 턴을 마치게 한다 — SYS가 위임을 완주시켜 결과로 나를 재개한다(활성은 언제나 한 명). 동료 자신은
        # 활성(alive==me_id)이라 이 게이트에 안 걸려 자기 작업을 정상 실행한다.
        if (any(not t.done() for t in getattr(flow, "inflight_tasks", ()))
                and flow.comm.alive != me_id and not flow.comm.done):
            return _ok("[대기] 직전 위임이 아직 진행 중입니다 — 지금 직접 실행(run)하면 동료와 동시 작업(이중 "
                       "활성)이 됩니다. 추가 행동 없이 이 턴을 마치세요. 위임이 완료되면 SYS가 그 결과와 함께 "
                       "당신을 다시 깨웁니다(그때 검증·통합하세요).")
        # [백로그 문맥 전수 보장(2026-07-13, 사용자: '백로그 단위로 일하게 설계돼 있는데')] 판(활성 ST의
        # 장부)이 열려 있으면 작업 실행은 백로그 선점 후에만 — 집지 않은 작업이 장부 밖에서 벌어져
        # 검증 시점 소급 등재(빈 완수)를 만드는 구조 구멍을 도구층에서 막는다. 장부가 아직 없는
        # 초기 탐색(협의·GOAL 확정 전)은 자유.
        try:
            from .rule.milestone import pipeline_on as _po
            if _po():
                _ms = next((m for m in (getattr(flow, "milestones", None) or []) if m.status not in ("done", "superseded")), None)
                # [열린 단계 전체 스캔(2026-07-14)] permissions 2.7과 동형 — 첫 열린 ST만 보면 내 백로그가
                # 다른 열린 단계에 있을 때 오거부, 첫 단계 슬롯만 쥐면 도메인 무관 통과. 전 열린 단계를 본다.
                # [빈 장부 구멍 봉쇄(2026-07-13, 라이브 U-013: ST 열림·백로그 0·자유 실행)] ST가 열려
                # 있으면 장부가 비어 있어도 선점 없인 실행 불가 — 안 만들면 영영 안 걸리던 구멍.
                _sts = [x for x in _ms.subtasks if x.status not in ("done", "superseded")] if _ms else []
                _rls = getattr(flow, "backlog_relays", None) or {}
                _mine = any(b.status == "in_progress" and int(b.assignee or 0) == int(me_id)
                            for x in _sts if _rls.get(x.st_id) is not None
                            for b in _rls[x.st_id].backlogs)
                if _sts and not _mine:
                    _r0 = _rls.get(_sts[0].st_id)
                    _cand = " · ".join(f"{b.backlog_id}({b.status[:4]})" for b in (_r0.backlogs[:8] if _r0 else [])) or "(비어 있음)"
                    return _ok(f"[백로그 선점 필요] 열린 단계({len(_sts)}개)가 있으면 작업은 백로그 단위입니다 — "
                               f"pick_backlog(기존 id 또는 desc='이번에 내가 할 일')로 **내 몫을 직접 등재해 집은 뒤** "
                               f"실행하세요. 한 번의 호출로 등재+착수됩니다. 집지 않은 작업은 장부·대화에 남지 "
                               f"않습니다. 현재 장부: {_cand}")
        except Exception:
            pass
        if _COLLAB_RE.search(cmd.lower()):
            # [B-08] 거부에 '어디로 기록하나' 처방 동봉(결정 지점 공급 — permissions 훅과 같은 문구).
            return _ok("실행 거부: 협의 기록(.collab/)은 시스템 소유 — meet/vote/set_goal/보고로만 "
                       "기록됩니다(열람은 Read 도구로).")
        if any(d in cmd.lower() for d in _RUN_DENY):
            return _ok(f"실행 거부(안전): 파괴/저장소/시스템 패턴 포함 — {cmd[:80]}")
        if any(p in cmd for p in _RUN_AUTHOR):
            return _ok("실행 거부: run은 '실행·빌드·검증' 전용입니다 — 파일 작성/수정은 Write/Edit 도구로 "
                       "하세요(그래야 권한·협의 게이트가 적용되고 누가 무엇을 만들었는지 기록됩니다). 예: "
                       "server.js 작성은 Write, 패키지 설치·서버 구동·curl 점검은 run. 남의 도메인 산출물을 "
                       "run으로 대신 찍어내지 말고 그 owner에게 Work로 위임하세요.")

        def _exec():
            # 자체 세션(프로세스그룹)으로 실행 → 직속 셸 종료 후 그룹째 정리한다.
            # 이게 run 간 포트 충돌(EADDRINUSE)의 구조적 해결: 'node server.js &'로 띄운
            # 백그라운드 서버가 init으로 reparent돼 누수되는 일이 없다.
            # 출력은 파이프 대신 임시파일로 — 백그라운드 자식이 파이프를 잡고 있어도 wait가 안 막힌다.
            of, ef = tempfile.TemporaryFile(), tempfile.TemporaryFile()
            env = _scrubbed_run_env()           # 봇 자기 env에서 비밀 제거
            drop = _run_drop_creds()            # root면 비특권 강등 (uid,gid) — 비밀 파일/proc 읽기 근본차단
            popen_extra = {}
            if drop:
                uid, gid = drop
                _chown_tree(str(flow.workspace), uid, gid)              # 작업공간을 강등 사용자가 쓰게
                env["HOME"] = str(flow.workspace)                       # npm·도구 dotfile 루트(쓰기 가능)
                env.setdefault("npm_config_cache", os.path.join(str(flow.workspace), ".npm"))
                popen_extra = {"user": uid, "group": gid, "extra_groups": []}   # root 보조그룹까지 제거
            p = subprocess.Popen(cmd, shell=True, cwd=str(flow.workspace),
                                 stdout=of, stderr=ef, start_new_session=True,
                                 env=env, **popen_extra)   # 배포 비밀 차단 + 비특권 강등(봇이 비밀 못 읽음)
            timed_out = False
            try:
                rc = p.wait(timeout=60)        # 직속 셸 종료까지만 대기
            except subprocess.TimeoutExpired:
                timed_out, rc = True, None
            finally:
                _reap_pgroup(p.pid)            # 백그라운드 자식까지 그룹째 정리(누수/포트충돌 차단)
                try:
                    p.wait(timeout=2)          # 셸 좀비 회수
                except Exception:
                    pass
            of.seek(0); ef.seek(0)
            out = of.read().decode("utf-8", "replace"); err = ef.read().decode("utf-8", "replace")
            of.close(); ef.close()
            return timed_out, rc, out, err

        try:
            timed_out, rc, out, err = await anyio.to_thread.run_sync(_exec)
        except Exception as e:
            return _ok(f"실행 오류: {e}")
        if timed_out:
            _dbg(f"[RUN] {me_id} `{cmd[:60]}` TIMEOUT")
            return _ok("실행 시간초과(60s) — 그룹째 정리함. 서버는 'node server.js & sleep 1; curl ...'처럼 "
                       "백그라운드로 띄우세요(포그라운드로 서버를 실행하면 멈춥니다). **큰 단일 다운로드/빌드"
                       "(수백MB+ 도구·모델)는 60초에 안 끝납니다 — 작은 패키지·에셋으로, 또는 닿는 경량 대안으로 "
                       "갈아타세요(이 환경엔 GPU 없음·Render는 Node-웹 전용).\n"
                       f"[부분 stdout]\n{out[-800:]}\n[부분 stderr]\n{err[-400:]}")
        _dbg(f"[RUN] {me_id} `{cmd[:60]}` exit={rc}")
        if flow.current is not None:
            flow.current.verified = True          # 실행 0회 완료 차단(layer1)
            flow.current.run_count += 1
            # 시스템이 직접 캡처한 영수증(에이전트 말이 아니라 실제 출력). 완료 보고에 떼어낼 수 없게 묶인다.
            errtail = ("\n[stderr] " + err[-200:]) if (err or "").strip() else ""
            flow.current.evidence = f"exit={rc} `{cmd[:50]}`\n{(out or '')[-400:]}{errtail}"
        # [기동증명 코칭 — 백그라운드 시작만 하고 끝내는 실수 감지(라이브 P-005: 백엔드가 server.js를 다음 run에서
        # curl하려고 별도 run에 `node server.js … &`로 띄웠다 reap돼 죽은 서버에 curl→무한 헤맴)] 명령이 *끝의
        # 단일 `&`로 백그라운드 시작*이면(뒤에 점검 없음), 이 프로세스는 run 종료 시 그룹째 정리돼 다음 run엔
        # 없다 → 그 자리에서 올바른 '한 run 묶기' 패턴을 처방한다(추측·재시도 루프 차단).
        _c = cmd.strip()
        _bg_only = _c.endswith("&") and not _c.endswith("&&")
        _hint = ""
        if _bg_only:
            _hint = ("\n\n⚠ 끝의 `&`로 띄운 백그라운드 프로세스(서버 등)는 **이 run이 끝나며 그룹째 정리**됐습니다 "
                     "— 다음 run엔 살아있지 않습니다(run 간 포트충돌 방지 설계). 서버 **기동증명은 반드시 한 run "
                     "안에** start→대기→점검을 묶으세요: `node server.js & sleep 1; curl -s 127.0.0.1:$PORT/헬스경로 "
                     "&& curl -s -X POST 127.0.0.1:$PORT/api/…` (별도 run으로 나누면 서버가 죽어 curl이 붙지 못합니다).")
        return _ok(f"[exit {rc}] (작업공간)\n[stdout]\n{out[-1500:]}\n[stderr]\n{err[-600:]}{_hint}")

    tools.append(run)

    if mode == "casual":
        # [G3 — B-06] 일상 대화 턴: run(사실 확인·간단 실행)만 장착 — request·recruit·리더도구 없음.
        # 캐주얼 오분류로 프로젝트·회의가 열리는 것을 도구 부재로 기계 차단(프롬프트 지시 의존 제거).
        return [run]

    if role != "leader":
        # [B-14 — report 도구(스태시형·이중 수용, BOT_ARCH_REDESIGN 2026-07-03)] 멤버 세션 장착. 구조화
        # 보고 필드를 시스템에 *스태시*할 뿐 — **Response는 여전히 턴 반환값**(모듈 docstring '보고=반환값'
        # 원칙 유지). 소비: offdomain_role은 [직군밖] 첫줄 regex보다 우선(_deliver, regex 폴백 존치),
        # experience/craft_standard는 [경험]/[직무기준] 블록과 같은 흡수 경로(run_turn, regex 폴백 존치),
        # result/changes/verify/risks는 REPORTS.md에 구조화 동봉. 미사용 봇은 종전 동작(이중 수용 — 무중단).
        @tool("report",
              "작업 보고의 구조화 필드를 시스템에 기록(선택) — 보고 본문은 여전히 턴 반환값(Response)으로 "
              "합니다. result=한 줄 결론(완료/부분/실패), changes=파일·핵심 변경, verify=검증 방법→결과, "
              "risks=남은 것·주의점. offdomain_role=이 일이 당신 직군 밖이면 필요 직군명(반려 신호), "
              "experience=이번 작업의 직군 차원 교훈 1~2줄, craft_standard=직무 기준 갱신(있을 때만).",
              {"result": str, "changes": str, "verify": str, "risks": str,
               "offdomain_role": str, "experience": str, "craft_standard": str})
        async def report(args):
            if getattr(flow, "report_stash", None) is None:
                flow.report_stash = {}
            flow.report_stash[me_id] = {
                k: str(args.get(k) or "").strip()
                for k in ("result", "changes", "verify", "risks",
                          "offdomain_role", "experience", "craft_standard")}
            return _ok("보고 필드가 기록되었습니다 — 이어서 같은 결론을 Response(턴 반환값)로 간결히 "
                       "보고하며 턴을 마치세요(이 도구가 보고를 대신하지 않습니다).")
        tools.append(report)

        if me_id in (getattr(flow, "fork_kind", None) or {}):
            # [B-15 — cast_vote 도구(fork 가지 세션 전용)] flow.fork_kind[m]은 wake 전에 세팅되므로 서버
            # 빌드 시점에 가지 식별 가능. 표는 인자로 정확히 — [표] regex 수합은 폴백 존치(이중 수용).
            @tool("cast_vote",
                  "표결 가지에서 투표를 기록 — option=선택지명(안건의 선택지 문구 그대로), reason=근거 "
                  "1~2줄. 호출 후 같은 근거를 Response로 간결히 반환하며 턴을 마치세요.",
                  {"option": str, "reason": str})
            async def cast_vote(args):
                opt = str(args.get("option") or "").strip()
                if not opt:
                    return _ok("오류: option(선택지명)이 비었습니다 — 안건의 선택지 문구를 그대로 적어주세요.")
                if getattr(flow, "vote_stash", None) is None:
                    flow.vote_stash = {}
                flow.vote_stash[me_id] = {"option": opt, "reason": str(args.get("reason") or "").strip()}
                return _ok(f"투표 기록됨: {opt} — 근거를 Response로 간결히 반환하며 턴을 마치세요.")
            tools.append(cast_vote)

    # [배포 탈중앙화(2026-07-08, 사용자: '리더만 배포권은 말도 안 되는 중앙집권')] deploy는 리더 전용이
    # 아니라 **모든 협업 멤버**에게 준다 — 검증을 끝낸 사람(대개 산출물 owner)이 직접 공개한다. 리더 전용이던
    # 탓에 워커가 수정을 끝내고도 '배포 권한 아무도 없어?'로 빙빙 돌던 교착의 근본. 보안(키 인프로세스라
    # 봇이 키를 못 읽음)·런어웨이(배포 캡·anti-thrash)는 *누가* 부르든 그대로 작동하므로 리더 독점 이유 없음.
    @tool("deploy",
          "검증을 마친 산출물을 실제로 공개 배포한다(GitHub push + Render 웹서비스 생성/갱신). "
          "name=영문 소문자·하이픈 서비스명(예: slither-multiplayer). 라이브 URL을 반환. "
          "Node 앱이어야 하고 서버는 process.env.PORT를 사용해야 함. run 검증을 끝낸 뒤 마지막에 호출. "
          "검증을 끝낸 누구나(대개 owner) 직접 배포한다 — 남에게 넘기려 멈추지 말 것. "
          "note=이번 배포의 계기·변경 한 줄(필수에 준함 — 피드에 '누가 왜 배포했나'로 남습니다).",
          {"name": str, "note": str})
    async def deploy(args):
        return await _rule_deploy(flow, args, me_id=me_id)
    tools.append(deploy)

    # [atelier(P0 B-2, 2026-07-13)] 공유 판(atelier)에 남기는 문 — deploy의 Render처럼 외부 독립
    # 서비스 클라이언트(매체 아님 — 매체중립 무관). 사용은 Organt의 선택(강제·자동 없음): 산출물
    # 설명·검증 증거를 남기거나, 판에서 승격돼 온 요청([atelier 핀 #N])을 끝냈을 때 마감 회신.
    # env(ATELIER_URL/ATELIER_TOKEN) 미설정이면 호출해도 안내만 — 협업 흐름은 막지 않는다.
    @tool("atelier",
          "사람과 같이 쓰는 공유 캔버스(atelier)에 남긴다 — 필요하다고 판단될 때만. "
          "op=note: 스티키 한 장(project,canvas,text — 산출물 설명·검증 증거·설계 메모). "
          "op=shot: 실화면 라이브 조각(project,canvas,url,text=제목,sel=CSS선택자(선택) — 배포/구현한 "
          "화면을 판에 품는다, 캡쳐 아님). "
          "op=done: 'atelier 핀'이 붙은 사람 요청을 끝낸 뒤 마감 회신(pin=요청문 [atelier 핀 #N]의 N, "
          "text=처리 한 줄). project=판 이름(요청문의 '판에서 보기' 주소 /p/<이름>/ 참조, 예: murmur), "
          "canvas=시트 이름(없으면 생성, 예: 검증-증거).",
          {"type": "object",
           "properties": {"op": {"type": "string", "enum": ["note", "shot", "done"]},
                          "project": {"type": "string"}, "canvas": {"type": "string"},
                          "text": {"type": "string"}, "url": {"type": "string"},
                          "sel": {"type": "string"}, "pin": {"type": "string"}},
           "required": ["op"]})
    async def atelier(args):
        from . import atelier_client as _atl

        def _go():
            op = str(args.get("op") or "")
            pj = str(args.get("project") or "").strip()
            cv = str(args.get("canvas") or "메모").strip()
            tx = str(args.get("text") or "").strip()
            if op == "done":
                return _atl.done(str(args.get("pin") or ""), tx)
            if not pj:
                raise RuntimeError("project(판 이름)가 필요합니다 — 예: murmur")
            if op == "shot":
                u = str(args.get("url") or "").strip()
                if not u:
                    raise RuntimeError("shot은 url(품을 실화면 주소)이 필요합니다")
                return _atl.shot(pj, cv, u, str(args.get("sel") or ""), tx)
            if not tx:
                raise RuntimeError("note는 text(남길 내용)가 필요합니다")
            return _atl.note(pj, cv, tx)

        try:
            return _ok(await anyio.to_thread.run_sync(_go))
        except Exception as e:   # 판 장애가 협업을 막으면 안 됨 — 실패는 안내로만
            return _ok(f"atelier 실패: {e}")
    tools.append(atelier)

    if role == "leader":
        @tool("create_project",
              "Project로 판단되면 전용 채널 생성 + 규모를 산정해 팀 배정"
              "(team=쉼표구분 동료 id/역할명, 본인 제외분). 비우면 풀 전체.",
              {"name": str, "team": str})
        async def create_project(args):
            # [도구=얇은 래퍼] 로직은 rule/project.py(Project Rule)에 — @tool은 계약·표현만, 규칙은 rule/가 소유(§7 복원)
            return _ok(await _rule_create_project(flow, args))
        tools.append(create_project)

        @tool("create_task",
              "Task '빈 껍데기'를 연다 — **Purpose도 비운 채 팀만 확정**한다(개인이 할 일을 미리 못 박음 = 중앙집권 "
              "방지). 이후 **배정된 팀이 모여(request Info) Purpose(풀 문제)·Goal(성공기준)을 함께 정해 set_goal로 "
              "확정**한다 — 이때 **각 직군 전문가가 *자기 도메인*의 Task·소유를 직접 제안**하게 하라(남의 "
              "도메인을 정하지 말 것 — 전문가가 자기 분야를 정의). Owner는 그 일을 Work로 받은 동료가 된다(선배정 "
              "금지). **members=이 일에 필요한 직군 동료를 당신이 직접 고른다**(자동 전원 소집 아님 — 직군 고정 방지) — "
              "고를 때 **각 동료의 누적 경험·강점(직무 기준)을 살려** 적임자에게 맡겨라. 비우면 프로젝트팀 "
              "기본, 모자란 직군은 recruit(role=)로 채운다(그 직군 전문가가 즉석 생성돼 합류).",
              {"members": str})
        async def create_task(args):
            # [도구=얇은 래퍼] 로직은 rule/task.py(Task Rule)
            return _ok(await _rule_create_task(flow, args))
        tools.append(create_task)

        @tool("set_goal",
              "팀 회의로 정한 이번 Task의 **Purpose(풀 문제)와 Goal(측정가능한 성공기준)**을 확정·기록한다. 개인 "
              "단독/선지정 금지 — **이 Task의 멤버 전원**과 meet(회의)로 'Purpose·각 도메인의 목표·성공기준'을 "
              "수렴한 결과를 적는다(1:1 request(Info)보다 meet 권장 — 앵커링↓·회의록 자동 기록). Goal엔 '무엇이 "
              "되면 성공인가'(결과·시나리오)만 쓰고 '어떤 파일·엔드포인트·스택으로 만들지'(구현 방법)는 쓰지 말 것 — "
              "그건 owner가 정한다(단, **각 산출물·파일은 정확히 한 도메인이 소유하도록 계획** — 이중 배정 금지; "
              "통합 파일(엔트리 HTML 등)도 단일 owner를 정하고 타 도메인은 그 owner에게 통합 요청한다. *먼저 만든 "
              "자가 가지는* 게 아니라 *도메인 책임자가* 소유한다). Work 위임은 확정 뒤에만 가능. acceptance(수용 "
              "계약)엔 회의에서 각 전문가가 제안한 '좋음의 구체·검증가능 조건'(훌륭한 예 대비)을 항목으로 적되, "
              "**반드시 '존재이유 테스트' 1개 이상**(이 산출물이 *진짜 그것*임을 증명하는 전체·부정형 검증 — 실패하면 "
              "핵심 목적이 깨지는 것)을 포함한다. 예: 2인 협동게임='솔로 플레이어로는 클리어 불가', 추천='무관 질의엔 "
              "상위가 달라짐', 인증='틀린 토큰은 거부'. 부품 체크(버튼 있나·이벤트 발화하나)만 적으면 *부품은 통과인데 "
              "전체는 목적 미달*인 산출물이 마감된다 — 마감이 이 항목들(특히 존재이유 테스트)의 실현을 검증한다. "
              "게이트 면제 인자(종전 마커와 동등): maximal_na(최대화 N/A 사유)·staffing_waiver(스태핑 면제 "
              "이유)·depth_solo(심도 단독 — 능력·사유). team_check=구성 점검 합의 결론(필수 게이트 — "
              "'추가 직군 불필요 — <사유>' 또는 '<직군> 부족 → recruit 예정').",
              {"purpose": str, "goal": str, "acceptance": str, "standard": str, "interfaces": str,
               "existence_test": str, "maximal_na": str, "staffing_waiver": str, "depth_solo": str,
               "team_check": str})
        async def set_goal(args):
            return await _rule_set_goal(flow, me_id, role, args)
        tools.append(set_goal)

        # [결정권자 폐지(2026-07-09)] set_milestone·renegotiate_criterion은 공통 구역(위 recruit 옆)으로
        # 이동 — 확정=종결 표결, 등록=서기(누구나), 재협상=누구나(게이트=사람 승인).

        @tool("vote",
              "팀 표결(구조적 합의): 선택지를 두고 멤버 전원의 선택+근거를 **동시에**(독립·앵커링 방지) "
              "수집·집계한다. question=안건, options='선택지1;선택지2;...', members=쉼표구분(비우면 현재 "
              "Task 팀 전원). 1:1 Info를 여러 번 도는 대신 합의를 구조화 — 결과(집계+근거)를 보고 소집자가 정리한다.",
              {"question": str, "options": str, "members": str})
        async def vote(args):
            return _ok(await _rule_vote(flow, me_id, args))
        tools.append(vote)

        @tool("vote_stop",
              "[중지 투표] 해결 불가한 판을 봇 혼자가 아니라 팀 표결로 접는다 — 백로그를 다 돌아도 "
              "마일스톤을 충족 못 하고 접근이 결과를 못 바꿀 때의 구조적 출구. target='milestone'(진행 중 "
              "마일스톤만 종결) 또는 'task'(Task 통째 — 사람 승인 상신). reason=왜 해결 불가한가. "
              "과반(도메인 관점) 찬성 시 실행. (조건 1개만 불가면 renegotiate_criterion을 쓰세요.)",
              {"target": str, "reason": str})
        async def vote_stop(args):
            return _ok(await _rule_vote_stop(flow, me_id, args))
        tools.append(vote_stop)

        @tool("meet",
              ("완전 turn-taking 회의(§4): 소집자가 주제+자기 의견을 발제하면 매 발언권이 응찰"
               "([응찰: N])로 돌아간다 — 강제 라운드 없음, 무응찰이면 종결 표결로 합의 종결. topic=주제, "
               "members=쉼표구분(비우면 현재 Task 팀 전원), rounds=발언 예산 배수(기본 2). my_opinion="
               "당신(소집자)의 독립 의견(필수) — 당신도 중재자가 아니라 한 참여자다. 완수조건을 정하는 "
               "회의라면, 수렴 뒤 결정권자가 set_milestone으로 확정한다.") if _pipe_on() else
              ("라운드로빈 회의: 1라운드는 전원의 '독립 의견'을 동시에 수집하고(앵커링 방지), 2라운드부터 "
               "서로의 발언을 보며 직렬로 토론한다(회의록 반환). topic=주제, members=쉼표구분(비우면 현재 "
               "Task 팀 전원), rounds=라운드 수(기본 2). **my_opinion=당신(소집자)의 독립 의견(필수) — "
               "당신도 중재자가 아니라 한 참여자로 자기 도메인 관점을 낸다**. 1:1 중계 없이 실제 다자 토론을 "
               "구조화 — 회의록을 보고 수렴·확정한다."),
              {"topic": str, "members": str, "rounds": str, "my_opinion": str})
        async def meet(args):
            return _ok(await _rule_meet(flow, me_id, args))
        tools.append(meet)

        @tool("parallel_work",
              "파일 영역이 겹치지 않는 **독립 Work 여러 건을 동시에** 위임(병렬 실행+직렬 통합, RFC-006). "
              "assignments=JSON 배열 '[{\"to\":\"봇id\",\"files\":\"상대경로,상대경로\",\"body\":\"지시\"}]'. "
              "각자 배정된 files에만 쓸 수 있다(쓰기 리스 — 영역 겹침은 거부). 영역이 겹치거나 순서 의존이면 "
              "request(Work) 직렬로. 조인 후 통합·검증·마감은 직렬로 진행.",
              {"assignments": str})
        async def parallel_work(args):
            return _ok(await _rule_parallel_work(flow, me_id, args))
        tools.append(parallel_work)



        @tool("list_projects",
              "회사가 진행/배포해 온 프로젝트 전체 목록(P-번호·이름·요약)을 조회 — 신규성 판단·중복 회피·"
              "기존 작품 이어가기 판단에 사실 근거가 더 필요할 때(프롬프트의 회사 이력은 최근 일부만).",
              {})
        async def list_projects(args):
            # [B-18③ — _portfolio_note push(16건 캡) 유지 + pull '보강'(BOT_ARCH_REDESIGN 2026-07-03).
            # pull '전환'은 기각(A-8: 결함 원인이 '몰라서 못 물음') — 이 도구는 캡 밖 전체 조회용 보강.
            fn = getattr(flow, "projects_provider", None)   # SYS 주입(없으면 빈 목록 — 테스트·비등록 무해)
            try:
                rows = list(fn() or []) if callable(fn) else []
            except Exception:
                rows = []
            if not rows:
                return _ok("(등록된 프로젝트가 없습니다)")
            rows.sort(key=lambda p: str(p.get("id") or ""))
            lines = []
            for p in rows:
                gist = (str(p.get("summary") or p.get("purpose") or "").strip().replace("\n", " "))[:100]
                name = (p.get("name") or "").strip()
                label = f"{name} ({p.get('id')})" if name else str(p.get("id") or "?")
                lines.append(f"- {label}" + (f" — {gist}" if gist else ""))
            return _ok(f"[회사 프로젝트 이력 — 전체 {len(lines)}건]\n" + "\n".join(lines))
        tools.append(list_projects)

        @tool("send_file",
              "산출물 파일을 사용자에게 Discord 첨부로 보낸다 — 사용자가 '파일로 받고 싶다'고 했거나 산출물이 "
              "파일 형태(이미지·문서·데이터·코드 번들 등)일 때만(항시 보내지 말 것). path=작업공간 기준 상대경로, "
              "caption=한 줄 설명(선택). 25MB 이하만 — 큰 건 deploy(배포 URL)로.",
              {"path": str, "caption": str})
        async def send_file(args):
            return await _rule_send_file(flow, me_id, args)
        tools.append(send_file)

    # [e2e 마무리 — S3 도구 표면(PIPELINE_REWORK §6)] 전 멤버 장착 — Task 경계 개시는 현장의 몫
    # (마지막 작업자/QA, §3 관례와 동형). 플래그 ON에서만 등록(OFF 라이브는 도구 자체가 없다 — 동작
    # 불변). 로직은 rule/wrapup.py(매체중립): 분모(체크리스트)·판정·복기는 구조가, 검사는 봇이.
    if _pipe_on():
        @tool("e2e_open",
              "Task 경계(모든 마일스톤 done)에서 **전수 e2e 검증을 개시**한다 — 전 마일스톤의 완수조건"
              "(최종 버전 재실증)과 사용자 원문이 검사 분모로 자동 조립돼 항목 id 목록이 반환된다. "
              "개시 후: 산출물의 노출 표면을 e2e_scope로 추가하고, 각 항목을 실제 실행으로 검사해 "
              "e2e_result로 제출하라.",
              {})
        async def e2e_open(args):
            return _ok(rule_e2e_open(flow))
        tools.append(e2e_open)

        @tool("e2e_scope",
              "e2e 분모 확장 — 산출물을 열어 파악한 **노출 표면**(surfaces: 페이지·라우트·API·명령, "
              "한 줄에 하나)과 **주 사용 경로**(arcs: 실기동 관통 시나리오, 한 줄에 하나)를 제출한다. "
              "추가된 항목 id가 반환된다 — 이 목록이 '전수'의 분모가 되므로 아는 표면을 빠뜨리지 마라.",
              {"surfaces": str, "arcs": str})
        async def e2e_scope(args):
            return _ok(rule_e2e_scope(flow, args))
        tools.append(e2e_scope)

        @tool("e2e_result",
              "e2e 항목 하나의 검사 결과 제출. item=항목 id(예: condition:1), ok=pass/fail, "
              "observed=관측한 것 한 줄, evidence=**실행 증거**(run 출력·브라우저 확인 요지 — 증거 없는 "
              "pass는 결함으로 판정된다).",
              {"item": str, "ok": str, "observed": str, "evidence": str})
        async def e2e_result(args):
            return _ok(rule_e2e_result(flow, args))
        tools.append(e2e_result)

        @tool("e2e_finish",
              "전 항목 제출 후 판정 — 전부 '증거 있는 pass'면 e2e_pass(Task 마무리 가능), 아니면 "
              "e2e_fail: 결함 목록으로 복기 마일스톤이 자동 개설된다(결함 해소가 완수조건 초안, "
              "확정은 회의). 미제출 항목은 '검사 안 됨' 결함이 된다.",
              {})
        async def e2e_finish(args):
            return _ok(rule_e2e_finish(flow))
        tools.append(e2e_finish)

    # [완료 권한 = 검수 역할(사용자 2026-07)] acceptance/'done' 판정은 QA의 일 — 종전엔 리더가 독점(complete_task
    # 리더 전용)했다. 리더의 역할은 기획·위임·조율이지 검수가 아니라, QA/PM이 '인수 PASS'로 판정해도 닫을 권한이
    # 없어 계속 검사만 하고 리더는 닫을 권한이 있는데 검증자가 아니라 계속 위임만 하는 무한 루프였다(라이브
    # P-005: QA 오은우·PM 유찬영이 인수 PASS 선언했는데 complete_task 0회, 162턴 무한). 완료 권한을 검수 역할
    # (QA)로 이관 — 검수자가 자기 검수 결과로 직접 마감(리더 독점도 SYS 강제도 아닌 탈중앙). 팀에 QA 없으면
    # 리더 폴백(옵션2, 마감 불능 방지). role별 세션이라, 이 봇이 마감권 보유자면 도구 장착.
    if _holds_completion(flow, me_id, role):
        @tool("complete_task",
              "현재 Task의 목표가 충족되면 상태블록을 완료로 마감(result 기록). 마감 전 acceptance의 **'존재이유 "
              "테스트'를 최종 사용자처럼 end-to-end로 실제 실행**해 통과 증거를 result에 남겨라 — 부품이 *있는지*가 "
              "아니라 *전체가 목적을 달성하는지*(부정형 테스트가 실제로 실패를 막는지)를 본다. 다음 Task는 create_task로. "
              "게이트 회계/면제 인자(종전 result 마커와 동등): percept_na(지각차원 없음 사유)·visual_evidence(시각 "
              "검증 — 무엇이 보였나)·data_source(데이터 출처/불가 사유)·acceptance_check(수용기준 항목별 회계)·"
              "standard_check(최대성 항목별 회계)·contrib_waiver(기여 불필요 이유).",
              {"result": str, "percept_na": str, "visual_evidence": str, "data_source": str,
               "acceptance_check": str, "standard_check": str, "contrib_waiver": str})
        async def complete_task(args):
            return await _rule_complete_task(flow, role, args)
        tools.append(complete_task)

    return tools


def build_guide_server(flow: Flow, me_id: int, role: str, mode: str = "collab"):
    return create_sdk_mcp_server("guide", "1.0.0", make_guide_tools(flow, me_id, role, mode=mode))
