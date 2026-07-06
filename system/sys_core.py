"""SYS — Organt 주도 + P2P Communication.

User 입력 → SYS가 담당(리더)을 깨움 → Organt가 판단·행동(파일/Guide 도구).
필요하면 어떤 Organt든 `request`로 동료를 부르고, SYS가 그 동료를 중첩 베턴으로
깨워(run_turn) 응답을 돌려준다. 항상 1명만 활성(단일흐름) → 사이드이펙트·토큰 절약.

SYS는 얇다: 깨우기(wake) 제공 + 단일흐름 lock + 라우팅. 베턴/권한 강제는 Rule·Hook.
Organt 생성(모델·권한·State)은 organt_builder로 주입받는다.

[M8 파사드 보존 분할(LLM_DX_AUDIT 1-C)] Sys의 응집 그룹 3개는 별 모듈로 추출됐고 이 파일의
메서드는 *이름·시그니처 불변*의 위임만 남는다(외부 소비자는 종전 Sys.X 그대로):
  · 프롬프트 조립(_prompt·_craft_note·_portfolio_note·_env_note·_status_text·원칙 상수) → sys_prompt.py
  · 영속·레지스트리(projects/jobs/profiles/personas·등록·토픽·피드백·file_owner)          → sys_store.py
  · 크래시-세이프 복구(_task_snapshot·_checkpoint_open_task·_restore_open_task)           → sys_recovery.py
오케스트레이션(run·run_turn·handle_user_input·route_channel_request·이어가기/증류 엔진)은 여기 남는다.
"""
import asyncio
import glob
import json
import os
import re
import time
import logging
from typing import Dict, Optional

from ._util import doc_collab_on, dossier_read, dossier_rel
from .audit import CAP_MIN   # noqa: F401 — 재수출(파사드 보존: 기존 소비자는 sys_core 이름을 봄)
from .rule.communication import CommError, Engagement
from .guide_tools import Flow, TaskRef, build_guide_server, make_guide_tools   # noqa: F401 — TaskRef 재수출(사용처는 sys_recovery로 이동)
from .protocol import Kind, Request, TaskStatus, format_response   # noqa: F401 — TaskStatus 재수출(사용처는 sys_recovery로 이동)
from . import sys_prompt
from . import sys_store
# [M8 파사드 보존 추출] 프롬프트 조립 그룹은 sys_prompt.py로, 영속·레지스트리 그룹은 sys_store.py로
# 이동 — 아래 이름은 재수출(소비자: sys_core 내부 + 러너/테스트의 `from system.sys_core import …`).
from .sys_prompt import _CASUAL_HINTS, _BUILD_VERBS, _CONTINUE_BODY, _casual_turn   # noqa: F401
from .sys_store import _init_artifact_repo, load_personas, save_personas   # noqa: F401
from . import sys_recovery
from .sys_recovery import _parse_goal_doc   # noqa: F401


log = logging.getLogger("organt.sys")

class Sys:
    def __init__(self, guide, guild_id, organt_builder, bot_info: Optional[Dict[int, str]] = None,
                 workspace=None, projects_path=None, session_dir=None, max_continue=6,
                 jobs_path=None, seed_path=None):
        self.guide = guide
        self.guild_id = guild_id
        self.organt_builder = organt_builder   # (organt_id, guide_server, role) -> Organt
        self.bot_info = bot_info or {}
        # 로스터 원본 라벨(직군). recruit(role=…)로 '예비'를 런타임 직군으로 채용하면 bot_info가 바뀌므로,
        # 새 흐름 시작 때 이걸로 원복한다(예비는 다음 흐름에서 다른 직군으로 다시 채용 가능).
        self._roster_labels = dict(self.bot_info)
        # '직업 기억' 디스크 영속: recruit한 직군(예: 게임 기획자)을 jobs.json에 저장해, 프로세스 재시작
        # 뒤에도 '예비'로 원복되지 않게 한다(매번 다른 봇이 그 직군으로 뽑히던 문제의 근본 해결; 1봇 1직군).
        # Discord 역할(권한)도 또 다른 영속 진실원이라, main이 시작 때 역할에서 복원해 bot_info에 미리 반영한다.
        self.jobs_path = jobs_path
        self._load_jobs()
        self._origin_request = ""   # 이번 흐름의 '사용자 원문 요청'(담당자 paraphrase 아닌 원문) — 모든 프롬프트에 주입
        self.workspace = workspace             # run 툴 cwd(작업공간 경로)
        self.session_dir = session_dir         # organt_state_*.json 위치(새 요청마다 세션 초기화)
        self.persist_identity = None           # [채용 제네시스] (mid, name, persona)->None: 리크루터가 빚은 이름·인격을 매체 DB에 영속(러너 주입 — 로컬 ORM / 원격 guide_bridge). 미주입이면 bot_profiles만 러너-로컬 영속.
        # 턴 한도로 미완 시 같은 세션으로 이어가는 최대 횟수(ORGANT_MAX_CONTINUE로 운영 조정 가능).
        self.max_continue = int(os.environ.get("ORGANT_MAX_CONTINUE", max_continue))
        # 워커 턴 '침묵' 타임아웃(초): 도구 활동(last_activity)이 이 시간 동안 '한 번도' 갱신되지 않으면
        # (=진짜 행) 포기하고 '인프라 실패'로 반환한다. 벽시계 총 실행시간이 아니라 '무활동' 기준이라,
        # 오래 걸려도 일하는 워커는 안 자르고 완전히 멈춘 것만 끊는다(일하는 owner 절단·좀비의 근본 교정).
        self.turn_timeout = int(os.environ.get("ORGANT_TURN_TIMEOUT", "480"))   # 기본 8분(무활동 기준)
        # 흐름 '무진행(행)' 워치독: 요청·파일작성·실행 등 어떤 진행도 이 시간(초) 동안 없으면 흐름이 행으로
        # 멈춘 것(리더 서브프로세스 행 포함 — 리더 턴엔 타임아웃이 없어 생기는 구멍)으로 보고 자동 중단·보고한다.
        # 워커 타임아웃(turn_timeout=8분)보다 넉넉히 커야 워커 1회 행→복구를 '무진행'으로 오인하지 않는다.
        # [2026-06 상향] 요금제(구독) 환경에선 레이트리밋으로 첫 토큰까지 수 분 침묵하는 턴이 생긴다 —
        # 12분은 그 '살아서 대기'를 '행'으로 오인해 잘 돌아가는 흐름을 끊었다(+ organt.py stderr 하트비트로
        # 보강). 여유를 줘 오발을 줄이되, 진짜 행은 결국 잡게 20분으로(여전히 max_age 3h 백스톱 안).
        self.idle_timeout = int(os.environ.get("ORGANT_IDLE_TIMEOUT", "1200"))   # 기본 20분(>8분 워커 타임아웃)
        # [병렬 작업(Feat 4단계)] 흐름 '안'의 단일활성(베턴)은 불변 — 완화는 '서로 다른 프로젝트'의
        # 흐름 동시 진행만. 같은 스코프(프로젝트/신규)는 직렬 큐. 흐름 간 안전은 임의 숫자 상한이
        # 아니라 **전역 점유 장부(Engagement)** 가 보장한다: 한 봇은 한 시점에 한 흐름에만 참여
        # (리더 포함 — 같은 리더의 프로젝트들은 자연히 직렬). 동시 작업량의 자연 한도 = 직원 수.
        # ORGANT_MAX_FLOWS는 토큰 동시 사용을 묶고 싶을 때만 쓰는 운영 노브(기본 0=무제한,
        # 1=종전과 동일한 완전 직렬).
        self.active_flows: Dict[str, Flow] = {}   # scope(P-XXX|main) → 진행 중 Flow
        self.max_flows = int(os.environ.get("ORGANT_MAX_FLOWS", "0"))
        self.engaged = Engagement(is_live=self._scope_live)   # 봇 단위 전역 점유(흐름 간 배타성)
        self.queue = []                        # 진행 중 들어온 명령(순차 처리 대기)
        self.flow_log = []
        self.flow_log_path = (os.path.join(session_dir, "flow.jsonl") if session_dir else None)
        self.projects_path = projects_path     # 레지스트리 영속 경로(없으면 인메모리)
        self.seed_path = seed_path             # 커밋된 시드(리클레임으로 디스크 유실 시 폴백)
        self.projects: Dict[int, dict] = {}    # channel_id → 프로젝트 컨텍스트(개입 진입점)
        # 직군별 '직무 기준'(craft profile): {직군: 기준 텍스트}. 시스템이 정답을 정하지 않는다 —
        # 각 직군의 전문가(그 봇)가 첫 작업 때 스스로 작성하고(보고의 [직무기준] 블록을 SYS가 흡수),
        # Discord(sys-roles)에 영속돼 이후 모든 작업 프롬프트에 자기검수 기준으로 주입된다.
        # QA·백엔드·프론트·런타임 채용 직군 모두 같은 메커니즘 하나로 '각자의 일'이 고도화된다.
        self.role_profiles: Dict[str, str] = {}
        self.role_experience: Dict[str, list] = {}   # 직군별 '일하며 쌓인 경험' 풀 — 수면 증류로 직무 기준에 통합(공용 플라이휠)
        self.bot_experience: Dict[int, list] = {}    # [개인별 학습] 봇 자신이 겪은 최근 교훈 — 직군 공용이 아닌 개인 정체성
        # [B-19 — 3층 정체성 ③(BOT_ARCH_REDESIGN 2026-07-03)] 봇id→수면 증류된 '개인 기준'(≤600자).
        # bot_experience(원석)가 8건+ 쌓이면 distill_bot이 압축·영속하고 풀을 비운다 — 직군 공용
        # 플라이휠(role_profiles)의 개인판(닫힌 플라이휠이 개인 층에도 생김).
        self.bot_profiles: Dict[int, str] = {}
        # [B-21 capability ledger] 봇id→{능력명(_CAPS 표시명): 검증된 저작 수}. 적립은 rule/task
        # _ledger_accrue(owner 정당 수임+교차검증 통과 Task의 owner 저작만) — cover 판정 비편입 4용도
        # (peers 강점줄·_free_alternatives 후보 나열·recommend 투영·관측) 전용.
        self.capability_ledger: Dict[int, Dict[str, int]] = {}
        self.profiles_path = (os.path.join(session_dir, "role_profiles.json") if session_dir else None)
        self._load_profiles()
        self._proj_n = 0
        self._load_projects()

    def _scope_live(self, scope) -> bool:
        """점유 장부의 유령 자가 치유용: 그 스코프의 흐름이 아직 살아 있는가.
        '__distill__'(수면 증류)은 흐름이 아닌 짧은 점유라 항상 살아있다고 본다(finally에서 해제)."""
        if scope == "__distill__":
            return True
        f = self.active_flows.get(str(scope))
        return f is not None and not f.done

    # ── [M8 파사드 보존 추출] 영속·레지스트리(projects/jobs/topic/reconcile) 구현·이력 주석은
    # sys_store.py 참조 — 아래 메서드는 이름·시그니처를 보존한 위임만 남는다(외부·테스트 호출부 불변).

    def _load_projects(self):
        """프로젝트 레지스트리 디스크 복원(시드 폴백·큐 복원) — sys_store.load_projects로 추출(위임만)."""
        return sys_store.load_projects(self)

    def _save_projects(self):
        """레지스트리+대기열 원자 저장 — sys_store.save_projects로 추출(위임만)."""
        return sys_store.save_projects(self)

    def _load_jobs(self):
        """jobs.json '직업 기억' 복원 — sys_store.load_jobs로 추출(위임만)."""
        return sys_store.load_jobs(self)

    def _save_jobs(self):
        """jobs.json 원자 저장 — sys_store.save_jobs로 추출(위임만)."""
        return sys_store.save_jobs(self)

    def _persist_job(self, mid, role):
        """채용 직군 메모리+디스크 영속 — sys_store.persist_job로 추출(위임만)."""
        return sys_store.persist_job(self, mid, role)

    def _persist_capability(self, mid, ev):
        """[B-21] 검증 실적 장부 영속 — sys_store.persist_capability로 추출(위임만)."""
        return sys_store.persist_capability(self, mid, ev)

    def _register_project(self, channel_id, name, workspace, leader, purpose="",
                          origin_msg="", reuse_ok=None) -> str:
        """프로젝트 등록·신원 규칙(P-XXX 부여/재사용/하이재킹 가드) — sys_store.register_project로 추출(위임만)."""
        return sys_store.register_project(self, channel_id, name, workspace, leader,
                                          purpose=purpose, origin_msg=origin_msg, reuse_ok=reuse_ok)

    # 레지스트리의 Discord 영속(채널 토픽) — 우선순위: 런타임 디스크 > 채널 토픽 > 커밋 시드.
    _TOPIC_RE = sys_store._TOPIC_RE

    @staticmethod
    def _topic_for(p) -> str:
        return sys_store.topic_for(p)

    @classmethod
    def parse_project_topic(cls, topic) -> Optional[dict]:
        return sys_store.parse_project_topic(topic)

    def _spawn_topic_write(self, channel_id, topic: str):
        return sys_store.spawn_topic_write(self, channel_id, topic)

    def _sync_topic(self, channel_id):
        """등록/리더 재지정 때 채널 토픽 기록(best-effort) — sys_store.sync_topic로 추출(위임만)."""
        return sys_store.sync_topic(self, channel_id)

    def _clear_topic(self, channel_id):
        return sys_store.clear_topic(self, channel_id)

    async def reconcile_projects_from_discord(self):
        """부팅 시 채널 토픽으로 레지스트리 보강 — sys_store.reconcile_projects_from_discord로 추출(위임만)."""
        return await sys_store.reconcile_projects_from_discord(self)

    def _stage_inbound(self, flow) -> None:
        """[파일 전송 — 인바운드] 사용자가 첨부한 파일을 작업공간 inbox/로 옮긴다(워크스페이스가 준비됐을 때 1회)
        — 봇이 Read/run으로 사용하게. create_project가 워크스페이스를 만든 직후 + 매 턴 시작에 호출(멱등)."""
        atts = getattr(flow, "inbound_attachments", None)
        ws = getattr(flow, "workspace", None)
        if not atts or not ws:
            return
        inbox = os.path.join(str(ws), "inbox")
        try:
            os.makedirs(inbox, exist_ok=True)
            names = []
            for item in atts:
                try:
                    name, data = item
                    safe = os.path.basename(str(name)) or "file"
                    with open(os.path.join(inbox, safe), "wb") as fh:
                        fh.write(data)
                    names.append(safe)
                except Exception:
                    continue
            flow.inbound_files = list(getattr(flow, "inbound_files", []) or []) + names
            flow.inbound_attachments = []   # 1회만(중복 staging 방지)
            self._log("inbound_files_staged", files=names)
        except Exception as e:
            self._log("inbound_stage_error", err=str(e)[:100])

    def _write_dossier_scaffold(self, flow) -> None:
        """[B-09 Phase A — Task Dossier 스캐폴드] 흐름 시작(워크스페이스 확정 직후)에 `.collab/`에
        PLAYBOOK.md(정적 — 없을 때 1회)와 craft/<직군>.md(role_profiles 미러 — 수면 증류가 다음 흐름에
        반영되도록 흐름 시작마다 재작성)를 둔다. 관측 전용(주입 무변경 — 봇 프롬프트는 아직 이 문서를
        참조하지 않음), 전부 best-effort(실패가 흐름을 못 막음). 신규 흐름 폴더(new-…)는 프로젝트 등록 때
        os.replace로 개명돼도 .collab이 통째로 함께 이동한다(경로는 항상 상대 해석)."""
        try:
            from ._util import _atomic_write
            ws = str(getattr(flow, "workspace", "") or "")
            if not ws or not os.path.isdir(ws):
                return
            base = os.path.join(ws, ".collab")
            os.makedirs(os.path.join(base, "craft"), exist_ok=True)
            pb = os.path.join(base, "PLAYBOOK.md")
            # [B-18① — B 이동] 프롬프트에서 이동한 정적 원칙의 문서 원본(정적 — 워크스페이스 생성 시 1회,
            # 이후 재호출에 덮이지 않음). 단 B-09 시절의 '자리표시자'는 실내용으로 1회 승격한다 — 프롬프트
            # 1줄 참조가 실재 내용을 가리켜야 하므로('표기만 있고 접근 불가' 금지, §17 정신).
            try:
                _pb_old = open(pb, encoding="utf-8").read()
            except OSError:
                _pb_old = ""
            if not _pb_old or "자리표시자" in _pb_old:
                _atomic_write(pb, (
                    "# PLAYBOOK — 협업 원칙(시스템 소유 · 정적)\n\n"
                    "프롬프트에서 이동한 정적 원칙(BOT_ARCH_REDESIGN 2026-07-03 W3 B-18) — 산출물 착수 전 한 번 Read.\n\n"
                    "## 이 환경의 능력·경계(사실)\n\n" + self._env_note().strip() + "\n\n"
                    "## 원칙(완성도·자원 동원·차선책·레이아웃)\n\n" + self._PLAYBOOK_PRINCIPLES + "\n"))
            # [원문 내구 홈] 사용자 원문(의도)을 디스크에 박제 — 프롬프트는 first_wake에 1회만 각인하고,
            # 압축·긴 흐름에도 안 사라지도록 여기 두어 컨텍스트 지도가 가리킨다(되살리기 대신 내구 배치).
            _orig = (getattr(flow, "origin_request", "") or getattr(self, "_origin_request", "") or "").strip()
            if _orig:
                _atomic_write(os.path.join(base, "ORIGIN.md"),
                              "# 사용자 원문 요청 — 진짜 의도(담당자 요약·해석 아님)\n\n" + _orig
                              + "\n\n받은 지시가 이 원문과 어긋나 보이면 원문 의도를 우선하세요.\n")
            jobs = {j.strip() for v in (flow.bot_info or {}).values()
                    for j in str(v or "").split("·")
                    if j.strip() and not j.strip().startswith("예비")}
            for j in sorted(jobs):
                p = self.role_profiles.get(j)
                if not p:
                    continue
                safe = re.sub(r"[\\/\0]", "-", j)[:60]
                _atomic_write(os.path.join(base, "craft", f"{safe}.md"),
                              f"# 직무 기준 — {j} (role_profiles 미러 — 수면 증류 시 갱신)\n\n{p}\n")
            self._write_team_dossier(flow)   # 초기 로스터 디스크화(이후 recruit 변경은 run_turn이 갱신)
        except Exception:
            pass

    def _write_team_dossier(self, flow) -> None:
        """[로스터 내구 홈] 현재 팀(동료 id·직군·강점)을 `.collab/TEAM.md`에 미러 — recruit로 바뀌면 갱신.
        프롬프트는 로스터 '변경 시에만' 주입하므로, 압축 후 멤버가 동료를 잊어도 디스크에서 복구하게 한다.
        best-effort·변경 시에만 기록(flow 캐시 대조)."""
        try:
            from ._util import _atomic_write
            ws = str(getattr(flow, "workspace", "") or "")
            if not ws or not os.path.isdir(ws):
                return
            info = getattr(flow, "bot_info", None) or self.bot_info or {}
            lines = []
            for i, lbl in info.items():
                prof = (self.bot_profiles.get(i) or "").strip()
                strength = next((ln.lstrip("-•* ").strip() for ln in prof.splitlines() if ln.strip()), "") if prof else ""
                lines.append(f"- {i}: {lbl}" + (f" — 강점: {strength[:80]}" if strength else ""))
            body = ("# 팀 로스터 — 이 협업의 동료(id·직군). recruit 시 갱신.\n\n"
                    "누구에게 request(위임·질문)할지 헷갈리면 여기서 확인하세요.\n\n" + "\n".join(lines) + "\n")
            if getattr(flow, "_team_written", None) == body:
                return
            os.makedirs(os.path.join(ws, ".collab"), exist_ok=True)
            _atomic_write(os.path.join(ws, ".collab", "TEAM.md"), body)
            flow._team_written = body
        except Exception:
            pass

    def _task_snapshot(self, flow, ref) -> dict:
        """미완 Task 직렬화(사실 영속·verified만 0 리셋) — sys_recovery.task_snapshot로 추출(위임만)."""
        return sys_recovery.task_snapshot(flow, ref)

    def _status_text(self, flow, t0, final=None) -> str:
        """[Rule/Status — 상태 가시화] 흐름 상태 계기판 본문 — sys_prompt.status_text로 추출(위임만)."""
        return sys_prompt.status_text(self, flow, t0, final=final)

    @staticmethod
    def _idify_workspace(workspace, pid, name) -> str:
        """[신원=번호] 흐름 임시 폴더(new-…) → p-00n-슬러그 개명 — sys_store.idify_workspace로 추출(위임만)."""
        return sys_store.idify_workspace(workspace, pid, name)

    @staticmethod
    def _same_purpose(a, b) -> bool:
        """두 목표 원문의 '같은 작품' 판정(토큰 겹침) — sys_store.same_purpose로 추출(위임만)."""
        return sys_store.same_purpose(a, b)

    def _similar_projects(self, text) -> str:
        """새 요청과 유사한 기존 프로젝트 후보 알림 — sys_store.similar_projects로 추출(위임만)."""
        return sys_store.similar_projects(self, text)

    def _checkpoint_open_task(self, flow) -> None:
        """[크래시-세이프] Task 전이마다 미완 Task 스냅샷 영속 — sys_recovery.checkpoint_open_task로 추출(위임만)."""
        return sys_recovery.checkpoint_open_task(self, flow)

    def _save_file_owner(self, flow) -> None:
        """[소유 경계 영속] flow.file_owner → 프로젝트 레지스트리 — sys_store.save_file_owner로 추출(위임만)."""
        return sys_store.save_file_owner(self, flow)

    def _seed_file_owner(self, flow) -> None:
        """[전환기 시딩] audit 이력 최초 작성자로 file_owner 1회 시딩 — sys_store.seed_file_owner로 추출(위임만)."""
        return sys_store.seed_file_owner(self, flow)

    async def _restore_open_task(self, flow, proj) -> Optional[dict]:
        """저장된 미완 Task를 이번 흐름에 되살린다(사실 복원·verified만 0 리셋·깊은 체인 재개) —
        sys_recovery.restore_open_task로 추출(위임만)."""
        return await sys_recovery.restore_open_task(self, flow, proj)

    def _load_profiles(self):
        """role_profiles.json 복원(직무기준·경험·개인기준·실적) — sys_store.load_profiles로 추출(위임만)."""
        return sys_store.load_profiles(self)

    def _save_profiles(self):
        """role_profiles.json 원자 저장 — sys_store.save_profiles로 추출(위임만)."""
        return sys_store.save_profiles(self)

    def _log(self, event, **f):
        rec = {"event": event, "ts": time.time(), **f}
        self.flow_log.append(rec)
        # [관측성 — journald 노출(2026-06, 사용자)] 구조화 이벤트를 stderr(fd2)로도 한 줄 흘린다 → systemd가
        # 저널에 담는다. flow.jsonl(파일)만으론 운영 중 '왜 멈췄나'(flow_idle_aborted·queued·flow_done 등)를
        # 사후에 `journalctl -u organt-runner`로 못 보던 갭 교정. 파일 영속은 종전대로 유지.
        try:
            os.write(2, ("[organt] " + json.dumps(rec, ensure_ascii=False, default=str) + "\n").encode("utf-8", "replace"))
        except Exception:
            pass
        if self.flow_log_path:   # 메모리만이던 continue_incomplete/flow_done/req_sent를 디스크로 영속(관측)
            try:
                with open(self.flow_log_path, "a", encoding="utf-8") as fp:
                    fp.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            except OSError:
                pass

    # [M8 파사드 보존 추출] 정적 원칙 3블록의 원문(이력 주석 포함)은 sys_prompt.py로 이동 —
    # 클래스 속성은 같은 문자열 객체를 가리킨다(`Sys._PRINCIPLE` 등 외부·테스트 소비 불변).
    _PLAYBOOK_PRINCIPLES = sys_prompt.PLAYBOOK_PRINCIPLES
    _PRINCIPLE_LAYOUT = sys_prompt.PRINCIPLE_LAYOUT
    _PRINCIPLE = sys_prompt.PRINCIPLE

    def _craft_note(self, me, first_wake=True) -> str:
        """직무 기준·개인 기준·[경험] 요청 노트 — sys_prompt.craft_note로 추출(위임만).
        first_wake=False(resume)면 전부 생략 — 정체성은 대화 기억+role_profiles, [경험]은 report 툴이 담보."""
        return sys_prompt.craft_note(self, me, first_wake)

    def _portfolio_note(self) -> str:
        """회사 이력(프로젝트 목록) 노트 — sys_prompt.portfolio_note로 추출(위임만)."""
        return sys_prompt.portfolio_note(self)

    async def _channel_situation(self, channel_id, exclude_root=None, limit=14) -> str:
        """채널 최근 대화 요약 노트 — sys_prompt.channel_situation으로 추출(위임만)."""
        return await sys_prompt.channel_situation(self, channel_id, exclude_root=exclude_root, limit=limit)

    def _env_note(self) -> str:
        """이 환경의 능력·경계(사실) 노트 — sys_prompt.env_note로 추출(위임만)."""
        return sys_prompt.env_note(self)

    def _prompt(self, body, kind, role, me, leader_id=None, flow=None, first_wake=True):
        """턴 프롬프트 조립(캐주얼/리더/멤버 분기) — sys_prompt.prompt로 추출(위임만).
        first_wake=True(첫 wake)면 정적 지식·앵커를 1회 가르치고, resume(False)면 동적 task만 —
        나머지는 되살리기가 아니라 내구 구조(persona·게이트·디스크·report 툴)가 담보."""
        return sys_prompt.prompt(self, body, kind, role, me, leader_id=leader_id, flow=flow, first_wake=first_wake)

    async def _await_with_idle_watchdog(self, task, flow):
        """task(리더 실행)를 기다리되, flow.last_activity가 idle_timeout 동안 안 바뀌면(=흐름 전체 무진행=행)
        task를 취소한다(→ CancelledError). 요청·파일작성·실행 등 진행이 일어나는 한 아무리 길어도 안 끊는다
        — 고정 타임아웃이 아니라 '무진행' 기준이라, 오래 걸리는 정상 빌드는 보호하고 멈춘 것만 해소한다.
        (리더 턴엔 turn_timeout이 없어 생기던 '리더 행' 구멍을 메운다.)"""
        poll = max(1, min(20, self.idle_timeout))

        async def _wd():
            while not task.done():
                await asyncio.sleep(poll)
                if getattr(flow, "cancelled", False) and not task.done():
                    task.cancel()              # [사용자 작업 중지] 안전망 — 보통은 request_cancel이 즉시 취소
                    return
                idle = time.monotonic() - getattr(flow, "last_activity", time.monotonic())
                if idle > self.idle_timeout and not task.done():
                    self._log("flow_idle_abort", idle=int(idle), timeout=self.idle_timeout)
                    task.cancel()
                    return

        wd = asyncio.create_task(_wd())
        try:
            return await task
        finally:
            wd.cancel()

    _PROFILE_RE = re.compile(r"\[직무기준\]\s*(?P<job>[^\n]+)\n(?P<body>.*?)\n?\[/직무기준\]", re.S)
    _EXP_RE = re.compile(r"\[경험\]\s*(?P<job>[^\n]+)\n(?P<body>.*?)\n?\[/경험\]", re.S)
    # 경험(원석) 보존 상한 — ① 증류 라이브화로 5/8건에서 상시 드레인되므로, 이 버퍼는 '증류 사이클
    # 사이 헤드룸'이다(다직군 폭주 시 주기 전 FIFO 유실 방지). **중요도 판정은 FIFO(최신순)가 아니라
    # 증류(전문가 자기판단)가 한다** — 버퍼는 전문가가 검토하기 전 유실만 막는 역할. 주입은 여전히
    # craft_note의 exp[-6:]라 프롬프트 무증가. env로 조정(ORGANT_EXP_KEEP).
    _EXP_KEEP = int(os.environ.get("ORGANT_EXP_KEEP", "40"))

    async def _absorb_role_profiles(self, text: str, me=None) -> str:
        """보고 속 [직무기준]·[경험] 블록을 흡수한다 — 메모리·Discord(sys-roles)에 영속하고 본문에서 제거.
        직군 기준은 공용 풀(role_*)로, 경험은 공용 풀 + 보고한 봇(me)의 개인 풀(bot_experience) 양쪽에."""
        if not text or ("[직무기준]" not in text and "[경험]" not in text):
            return text
        absorbed, learned = [], []

        def _take(m):
            job = (m.group("job") or "").strip()
            body = (m.group("body") or "").strip()
            if len(body) > 1500:
                # 하드캡은 '줄 단위'로 — 문장 중간 절단은 양질 데이터를 지키려던 장치가 데이터를
                # 훼손하는 역설(절단된 반쪽 원칙이 매 턴 주입됨). 마지막 완전한 줄까지만 남긴다.
                cut = body[:1500]
                body = cut[:cut.rfind("\n")] if "\n" in cut else cut
            if job and body:
                self.role_profiles[job] = body
                absorbed.append((job, body))
            return ""

        def _learn(m):
            job = (m.group("job") or "").strip()
            body = (m.group("body") or "").strip()[:600]
            if job and body:
                # '없음' 류는 버린다 — 의무 섹션의 탈출구이지 경험이 아니다(저장하면 다음 프롬프트
                # 주입과 증류 원료가 노이즈로 오염된다). 괄호 안내문 재복창도 같은 이유로 컷.
                lines = [ln.strip() for ln in body.splitlines()
                         if ln.strip() and ln.strip().rstrip(".") not in
                         ("없음", "없다", "-", "특이사항 없음", "(교훈 또는 '없음')")]
                if not lines:
                    return ""
                cur = self.role_experience.setdefault(job, [])   # 공용 풀 — 수면 증류로 직무 기준에 통합(플라이휠 유지)
                cur.extend(lines)
                del cur[:-self._EXP_KEEP]   # 최근 N줄만(압축은 기억 증류의 몫)
                if me is not None:          # [개인별 학습] 보고한 봇의 개인 경험 풀에도 — 직군 공용과 별개로 자기 정체성
                    bc = self.bot_experience.setdefault(int(me), [])
                    bc.extend(lines)
                    del bc[:-self._EXP_KEEP]
                learned.append((job, len(lines)))
            return ""

        out = self._PROFILE_RE.sub(_take, text)
        out = self._EXP_RE.sub(_learn, out).strip()
        if absorbed or learned:
            self._save_profiles()   # 디스크 영속(사용자 디스코드를 시스템 데이터로 오염시키지 않음)
            for job, body in absorbed:
                self._log("role_profile_saved", job=job, size=len(body))
            for job, n in learned:
                self._log("role_experience_saved", job=job, lines=n)
        return out or "(직무 기준/경험이 기록되었습니다.)"

    _DISTILL_MIN = int(os.environ.get("ORGANT_DISTILL_MIN", "5"))   # 증류 발동 최소 경험 줄 수

    def pick_distill_job(self):
        """증류가 필요한 직군 하나를 고른다 — 경험이 가장 많이 쌓인 직군부터(없으면 None)."""
        jobs = self.pick_distill_jobs()
        return jobs[0] if jobs else None

    # [위생 증류 발동선] 기준이 이 길이를 넘으면 새 경험이 없어도 '정리 전용' 수면 대상 — 기준은
    # 매 턴 주입되므로 비대=주의 분산이고, 하드캡 절단 사고 전에 전문가 스스로 통합·다이어트하게 한다.
    _HYGIENE_AT = int(os.environ.get("ORGANT_HYGIENE_AT", "1100"))

    def pick_distill_jobs(self):
        """증류 후보 직군들 — ① 경험이 쌓인 직군(많은 순) ② 기준이 비대해진 직군(위생 증류,
        경험 0이어도). [병렬] 일부 전문가가 흐름에 묶여 있어도 가용한 다음 후보가 자기계발한다."""
        cands = sorted(((len(v), k) for k, v in self.role_experience.items()
                        if len(v) >= self._DISTILL_MIN), reverse=True)
        jobs = [k for _, k in cands]
        for job, prof in self.role_profiles.items():
            if job not in jobs and len(prof or "") > self._HYGIENE_AT:
                jobs.append(job)               # 정리 전용 수면 — 쌓기가 아니라 솎아내기
        return jobs

    # [B-19] 개인 증류 발동선 — 개인 경험(원석)이 이 수 이상 쌓인 봇이 수면(distill_bot) 대상(설계 명세 8).
    _BOT_DISTILL_MIN = int(os.environ.get("ORGANT_BOT_DISTILL_MIN", "8"))

    def pick_distill_bots(self):
        """[B-19] 개인 기준 증류 후보 봇들 — 개인 경험이 임계(8건+) 쌓인 봇, 많은 순.
        직군 증류(pick_distill_jobs)와 동형의 개인판(수면 사이클이 소비)."""
        return [m for _, m in sorted(((len(v), m) for m, v in self.bot_experience.items()
                                      if len(v) >= self._BOT_DISTILL_MIN), reverse=True)]

    def _bot_of_job(self, job):
        """그 직군을 보유한 봇(겸직 포함)을 찾는다 — 증류는 그 직군의 전문가 본인이 한다."""
        for mid, label in self.bot_info.items():
            if any(j.strip() == job for j in str(label or "").split("·")):
                return mid
        return None

    async def distill_role(self, job) -> bool:
        """[수면 — 기억 증류] 직군의 '최근 경험'을 그 전문가 봇이 직무 기준으로 압축한다.
        시스템은 내용을 정하지 않는다(전문가 자기정의 원칙) — 일반화 가치가 있는 교훈만 기준에
        흡수시키고, 증류된 경험 로그는 비운다. 증류 대화는 별도 세션(state_tag)이라 작업 기억을
        오염시키지 않는다. [병렬] '시스템 전체 유휴'가 아니라 **그 전문가 봇이 유휴**일 때 증류한다
        (회사가 일하는 중에도 한가한 직원은 자기계발 — 전역 점유 장부로 흐름과의 겹침을 차단)."""
        mid = self._bot_of_job(job)
        exp = self.role_experience.get(job) or []
        hygiene = len(self.role_profiles.get(job) or "") > self._HYGIENE_AT   # 정리 전용 수면 자격
        if mid is None or (len(exp) < self._DISTILL_MIN and not hygiene):
            return False
        if self.engaged.holder(mid) is not None:
            return False                                  # 그 전문가가 흐름 참여 중 → 이번 주기 스킵
        self.engaged.engage(mid, "__distill__")           # 증류 중 흐름이 이 봇을 집어가지 않게 점유
        try:
            return await self._distill_role_inner(job, mid, exp)
        finally:
            self.engaged.release(mid, "__distill__")
            # 증류 점유 때문에 큐로 밀린 요청이 있으면 이어서 처리(흐름 종료 드레인과 같은 판정).
            item = self._pop_runnable_queued()
            if item is not None:
                asyncio.ensure_future(self.handle_user_input(*item))

    async def _distill_role_inner(self, job, mid, exp) -> bool:
        cur = self.role_profiles.get(job, "(아직 없음)")
        flow = Flow(self.guide, 0, self.guild_id, mid, self.bot_info)   # 도구 형식용 빈 흐름(깨우기 없음)
        flow.workspace = self._distill_workspace()   # [OOM 근본교정] 격리 빈 cwd — builder가 cfg.workspace_dir(대형 트리)로 폴백 못 하게
        server = build_guide_server(flow, mid, "member")
        try:
            organt = self.organt_builder(mid, server, "member", flow, state_tag=f"distill_{mid}")
        except TypeError:
            organt = self.organt_builder(mid, server, "member", flow)   # 구형 빌더 호환(테스트 등)
        # [수면의 본질 = 정리(인간 수면의 기억 통합·솎아냄)] 더 많이 아는 게 아니라 더 선명하게.
        # LLM 특성: 기준은 매 턴 프롬프트에 주입되므로 길이=주의 분산 — 양질 소수 원칙이 효력의 조건.
        # 구조가 예산(원칙 수·길이)을 강제하고, 무엇을 남길지는 전문가가 정한다(자기정의 보존).
        raw = ("\n".join(f"- {e}" for e in exp) if exp
               else "(이번 수면은 새 경험 없음 — **정리 전용**: 기존 기준의 중복을 합치고 군더더기를 빼 더 선명하게)")
        prompt = (
            f"[자기계발 시간 — 직무 기준 증류] 당신은 '{job}' 전문가입니다. 도구를 쓰지 말고 텍스트로만 답하세요.\n\n"
            f"현재 직무 기준:\n{cur}\n\n"
            f"최근 실작업에서 쌓인 경험(원석):\n{raw}\n\n"
            f"수면의 본질은 '쌓기'가 아니라 '정리'입니다 — 전문가의 힘은 긴 규칙집이 아니라 소수의 깊은 "
            f"원칙입니다. 일반화 가치가 있는 교훈만 골라 기준에 녹이되:\n"
            f"- 새 교훈이 기존 원칙과 겹치면 **별도 추가가 아니라 기존 원칙에 합쳐** 더 일반적인 한 원칙으로.\n"
            f"- **예산: 원칙 최대 8개, 각 2줄 이내, 전체 1,000자 이내** — 넘치면 가장 덜 일반적인 원칙을 버리세요.\n"
            f"- 일회성 디테일·특정 프로젝트 한정 사항은 버리세요.\n"
            f"반드시 아래 형식만으로 답하세요:\n[직무기준] {job}\n(개선된 기준 줄들)\n[/직무기준]"
        )
        try:
            out = await organt.handle(prompt)
        except Exception as e:
            self._log("role_distill_failed", job=job, err=str(e)[:80])
            return False
        await self._absorb_role_profiles(out, me=mid)    # [직무기준] 블록 흡수(영속 포함)
        if self.role_profiles.get(job) and self.role_profiles.get(job) != cur:
            self.role_experience[job] = []               # 증류 완료 — 원석 비움
            self._save_profiles()
            self._log("role_distilled", job=job, used=len(exp))
            # 증류 세션은 일회성 — 다음 증류가 깨끗하게 시작하도록 제거
            if self.session_dir:
                try:
                    os.remove(os.path.join(str(self.session_dir), f"organt_state_distill_{mid}.json"))
                except OSError:
                    pass
            return True
        self._log("role_distill_noop", job=job)
        return False

    # [B-19] [개인기준] 블록 파서 — distill_role의 [직무기준] 관례 동형(헤더는 봇 라벨, 본문만 소비).
    _BOT_PROFILE_RE = re.compile(r"\[개인기준\]\s*(?P<who>[^\n]*)\n(?P<body>.*?)\n?\[/개인기준\]", re.S)

    async def distill_bot(self, mid) -> bool:
        """[B-19 — 수면, 개인 기준 증류] distill_role 동형의 개인판: 봇 자신의 경험(원석 8건+)을 그 봇이
        '개인 기준'(≤600자)으로 압축한다 → bot_profiles[mid] 영속·원석 풀 비움. `__distill__` 의사스코프
        점유·유휴 판정·별도 세션(state_tag) 이식 — 흐름 참여 중인 봇은 스킵하고, 증류 대화는 작업 기억을
        오염시키지 않는다. User-initiated-only 비위반(기존 직군 증류와 동일 지위)."""
        mid = int(mid)
        exp = self.bot_experience.get(mid) or []
        if len(exp) < self._BOT_DISTILL_MIN:
            return False
        if self.engaged.holder(mid) is not None:
            return False                                  # 그 봇이 흐름 참여 중 → 이번 주기 스킵
        self.engaged.engage(mid, "__distill__")           # 증류 중 흐름이 이 봇을 집어가지 않게 점유
        try:
            return await self._distill_bot_inner(mid, exp)
        finally:
            self.engaged.release(mid, "__distill__")
            item = self._pop_runnable_queued()            # 점유로 밀린 요청 드레인(직군 증류와 동일 판정)
            if item is not None:
                asyncio.ensure_future(self.handle_user_input(*item))

    async def _distill_bot_inner(self, mid, exp) -> bool:
        label = str(self.bot_info.get(mid, "")) or f"봇{mid}"
        cur = self.bot_profiles.get(mid, "(아직 없음)")
        flow = Flow(self.guide, 0, self.guild_id, mid, self.bot_info)   # 도구 형식용 빈 흐름(깨우기 없음)
        flow.workspace = self._distill_workspace()   # [OOM 근본교정] 격리 빈 cwd — builder가 cfg.workspace_dir(대형 트리)로 폴백 못 하게
        server = build_guide_server(flow, mid, "member")
        try:
            organt = self.organt_builder(mid, server, "member", flow, state_tag=f"bdistill_{mid}")
        except TypeError:
            organt = self.organt_builder(mid, server, "member", flow)   # 구형 빌더 호환(테스트 등)
        raw = "\n".join(f"- {e}" for e in exp)
        prompt = (
            f"[자기계발 시간 — 개인 기준 증류] 당신은 '{label}'입니다. 도구를 쓰지 말고 텍스트로만 답하세요.\n\n"
            f"현재 개인 기준:\n{cur}\n\n"
            f"당신이 최근 실작업에서 직접 얻은 경험(원석):\n{raw}\n\n"
            f"수면의 본질은 '쌓기'가 아니라 '정리'입니다 — 직군 공용 기준과 겹치는 일반론은 버리고, "
            f"**당신 자신**의 작업 방식·강점·반복 함정만 남기세요:\n"
            f"- 겹치는 교훈은 별도 추가가 아니라 합쳐 더 일반적인 한 원칙으로.\n"
            f"- **예산: 전체 600자 이내** — 넘치면 가장 덜 중요한 줄을 버리세요.\n"
            f"- 일회성 디테일·특정 프로젝트 한정 사항은 버리세요.\n"
            f"반드시 아래 형식만으로 답하세요:\n[개인기준] {label}\n(개선된 개인 기준 줄들)\n[/개인기준]"
        )
        try:
            out = await organt.handle(prompt)
        except Exception as e:
            self._log("bot_distill_failed", bot=mid, err=str(e)[:80])
            return False
        m = self._BOT_PROFILE_RE.search(out or "")
        body = (m.group("body") or "").strip() if m else ""
        if len(body) > 600:
            # 하드캡은 '줄 단위'로 — 문장 중간 절단 방지(_absorb_role_profiles 관례 동형).
            cut = body[:600]
            body = cut[:cut.rfind("\n")] if "\n" in cut else cut
        if body and body != cur:
            self.bot_profiles[mid] = body
            self.bot_experience[mid] = []                 # 증류 완료 — 원석 비움
            self._save_profiles()
            self._log("bot_distilled", bot=mid, used=len(exp))
            if self.session_dir:                          # 증류 세션은 일회성 — 다음 증류가 깨끗하게 시작
                try:
                    os.remove(os.path.join(str(self.session_dir), f"organt_state_bdistill_{mid}.json"))
                except OSError:
                    pass
            return True
        self._log("bot_distill_noop", bot=mid)
        return False

    # ── [채용 제네시스 — '채용 전문' Organt가 신규 봇 정체성을 빚는다] ──
    # 하드코딩 이름풀·빈 persona 대신, 리크루터(도메인 전문가)가 회사 맥락에 맞춰 이름·인격·초기 개인
    # 기준을 생성한다. distill_bot 동형(로컬 claude CLI·격리 cwd 재사용) — 새 자원 0. 리크루터 자신의
    # '채용' 직무 기준도 증류로 성장한다. 안전: '채용' 직군 봇이 있고 정체성 없는 신규 봇이 있을 때만
    # 작동(기존 경험 보유 봇엔 무영향 — pick_onboard_bots가 거른다).
    _ONBOARD_NAME_RE = re.compile(r"\[이름\]\s*(?P<v>[^\n]+)")
    _ONBOARD_PERSONA_RE = re.compile(r"\[인격\]\s*(?P<v>.*?)(?=\n\[개인기준\]|\Z)", re.S)

    def _pick_recruiter(self):
        """'채용/인사' 직군을 가진 Organt(리크루터). 여러 명이면 첫 번째, 없으면 None(기능 dormant)."""
        for mid, label in self.bot_info.items():
            if any("채용" in j or j.strip() == "인사" for j in str(label or "").split("·")):
                return int(mid)
        return None

    def pick_onboard_bots(self):
        """정체성 미완 신규 봇 — 직군은 있으나(예비 아님) 경험·개인기준이 아직 0(갓 생성). 기존 봇
        (경험 보유)은 distill 대상이지 onboard 대상이 아니다(생성 정체성이 학습을 덮어쓰지 않게)."""
        out = []
        for mid, label in self.bot_info.items():
            lbl = str(label or "").strip()
            if not lbl or lbl.startswith("예비"):
                continue
            if self.bot_experience.get(int(mid)) or self.bot_profiles.get(int(mid)):
                continue
            out.append(int(mid))
        return out

    async def onboard_bot(self, new_mid, recruiter_mid=None, role=None) -> bool:
        """[채용 제네시스] 신규 봇의 정체성을 리크루터가 생성. distill_bot 동형(engage·격리 세션).
        role 인자: 리더 recruit는 예비를 flow-로컬 tentative로 승격해 self.bot_info엔 아직 '예비'라,
        첫-사용 훅이 흐름 기준 역할을 넘긴다(없으면 self.bot_info 폴백)."""
        new_mid = int(new_mid)
        role = (role or self.bot_info.get(new_mid) or "").strip()
        if not role or role.startswith("예비"):
            return False
        if self.bot_experience.get(new_mid) or self.bot_profiles.get(new_mid):
            return False                                  # 이미 일했거나 온보딩됨
        recruiter = recruiter_mid if recruiter_mid is not None else self._pick_recruiter()
        if recruiter is None or int(recruiter) == new_mid:
            return False                                  # 리크루터 없음/자기 자신
        recruiter = int(recruiter)
        if self.engaged.holder(recruiter) is not None:
            return False                                  # 리크루터 흐름 참여 중 → 이번 주기 스킵
        self.engaged.engage(recruiter, "__distill__")     # 배경 자기업무 점유(distill과 동형 always-live)
        try:
            return await self._onboard_inner(new_mid, role, recruiter)
        finally:
            self.engaged.release(recruiter, "__distill__")
            item = self._pop_runnable_queued()
            if item is not None:
                asyncio.ensure_future(self.handle_user_input(*item))

    async def _onboard_inner(self, new_mid, role, recruiter) -> bool:
        flow = Flow(self.guide, 0, self.guild_id, recruiter, self.bot_info)   # 도구 형식용 빈 흐름
        flow.workspace = self._distill_workspace()        # [OOM 근본교정] 격리 빈 cwd(증류와 공유)
        server = build_guide_server(flow, recruiter, "member")
        try:
            organt = self.organt_builder(recruiter, server, "member", flow, state_tag=f"onboard_{new_mid}")
        except TypeError:
            organt = self.organt_builder(recruiter, server, "member", flow)
        role_std = self.role_profiles.get(role) or "(이 직군의 회사 기준이 아직 없음 — 좋은 시작 기준을 당신이 잡으세요)"
        taken = sorted({(v or "").split("·")[0].strip() for v in self.bot_info.values()
                        if v and not str(v).startswith("예비")})
        prompt = (
            f"[채용 — 신규 직원 온보딩] 당신은 이 회사의 채용 전문가입니다. 도구를 쓰지 말고 텍스트로만 답하세요.\n\n"
            f"새로 합류하는 직원의 직군: {role}\n"
            f"이 직군의 회사 축적 기준(이 사람이 이어받을 토대):\n{role_std}\n\n"
            f"기존 팀 이름(중복 금지): {', '.join(taken) or '(없음)'}\n\n"
            f"이 직원을 '한 사람'으로 빚으세요 — 빈 껍데기가 아니라 처음부터 개성과 초기 전문성을 가진 직원으로. "
            f"일반론 금지, 이 직군·이 사람 특유의 구체로:\n"
            f"- 이름: 기존과 안 겹치는 고유한 한국식 사람 이름(직군과 무관한 정체성).\n"
            f"- 인격(persona): 이 사람만의 태도·일하는 방식·강점·버릇 3~5줄(직군 전문성을 체화한 구체적 개성).\n"
            f"- 초기 개인 기준: 위 직군 기준을 '이 사람만의 시작 관점·강점'으로 특화한 3~5줄(이후 실경험으로 발전).\n"
            f"반드시 아래 형식만으로 답하세요:\n[이름] (고유 이름)\n[인격]\n(여러 줄)\n[개인기준] {role}\n(줄들)\n[/개인기준]"
        )
        try:
            out = await organt.handle(prompt)
        except Exception as e:
            self._log("onboard_failed", bot=new_mid, err=str(e)[:80])
            return False
        name = ""
        m = self._ONBOARD_NAME_RE.search(out or "")
        if m:
            name = (m.group("v") or "").strip().strip("()")[:100]
        persona = ""
        mp = self._ONBOARD_PERSONA_RE.search(out or "")
        if mp:
            persona = (mp.group("v") or "").strip()[:5000]
        prof = ""
        mb = self._BOT_PROFILE_RE.search(out or "")
        if mb:
            prof = (mb.group("body") or "").strip()[:600]
        if not (persona or prof):
            self._log("onboard_noop", bot=new_mid)
            return False
        if prof:
            self.bot_profiles[new_mid] = prof             # 초기 개인 기준(러너-로컬 영속)
            self._save_profiles()
        if self.persist_identity:                         # 이름·persona → 매체 DB(러너 주입 콜백; sync/async 모두 수용)
            try:
                _r = self.persist_identity(new_mid, name, persona)
                if asyncio.iscoroutine(_r):
                    await _r
            except Exception as e:
                self._log("onboard_persist_failed", bot=new_mid, err=str(e)[:80])
        self._log("bot_onboarded", bot=new_mid, by=recruiter, named=bool(name), has_persona=bool(persona))
        if self.session_dir:
            try:
                os.remove(os.path.join(str(self.session_dir), f"organt_state_onboard_{new_mid}.json"))
            except OSError:
                pass
        return True

    def _distill_workspace(self):
        """[OOM 근본교정] 증류(수면) 워커의 격리 cwd — 빈 전용 디렉터리를 만들어 돌려준다. 증류는
        도구 없이 텍스트로만 답하므로(파일 불요) cwd는 비어 있으면 된다. 종전엔 빈 흐름(workspace=None)
        이라 builder(organt/builder.py:70)가 cfg.workspace_dir(수백MB 산출물 트리)로 폴백 → CLI 시동
        스캔이 RSS를 수GB로 부풀려 머신 전역 OOM(라이브 관측 2026-06-23, discord_main _sleep_cycle 주석).
        빈 격리 폴더로 그 스캔을 원천 차단한다. session_dir 없으면(테스트·목빌더) None — 실 CLI를
        안 띄우므로 무해."""
        if not self.session_dir:
            return None
        try:
            d = os.path.join(str(self.session_dir), ".distill_cwd")
            os.makedirs(d, exist_ok=True)
            return d
        except OSError:
            return None

    async def _distill_cycle_once(self) -> None:
        """[자기업무 사이클 1회] ⓪ 채용 온보딩(신규 봇 정체성 생성) + ① 직군 증류 + ② 개인 증류 —
        각 유휴 대상 1건(비용 제어). 배경 자기업무는 '매체'가 아니라 '브레인'의 행위라 여기(Sys)가
        소유한다 — 종전엔 Discord 진입(discord_main._sleep_cycle)에만 있어 라이브(murmur) 러너에선
        안 돌아, 경험이 압축되지 못하고 _EXP_KEEP FIFO로 잘려나가기만 했다(중요한 기억 유실). 실패는
        삼켜 사이클 루프를 보존한다(한 예외가 다음 주기를 안 죽이게)."""
        try:
            # [채용 제네시스] 정체성 없는 신규 봇을 리크루터가 온보딩(일 투입 전 무장). 리크루터 미배치
            # ('채용' 직군 봇 없음)면 후보가 있어도 onboard_bot이 조용히 no-op(dormant — LLM 미호출).
            for mid in self.pick_onboard_bots():
                if self.engaged.holder(mid) is not None:
                    continue
                if await self.onboard_bot(mid):
                    break
            for job in self.pick_distill_jobs():
                mid = self._bot_of_job(job)
                if mid is None or self.engaged.holder(mid) is not None:
                    continue                 # 그 전문가는 흐름 참여 중 → 다음 후보
                await self.distill_role(job)
                break
            for mid in self.pick_distill_bots():
                if self.engaged.holder(mid) is not None:
                    continue                 # 그 봇은 흐름 참여 중 → 다음 후보
                await self.distill_bot(mid)
                break
        except Exception:
            import traceback
            self._log("distill_cycle_error", err=traceback.format_exc()[:300])

    async def _sleep_loop(self, period: int) -> None:
        """[수면 사이클] period초마다 증류 1회. run()이 (once가 아니고 period>0·session_dir 있을 때만)
        백그라운드로 스폰한다. 프로세스 수명과 함께 살고 종료 시 함께 정리된다(forever 러너 관례)."""
        while True:
            await asyncio.sleep(period)
            await self._distill_cycle_once()

    async def _drain_inflight(self, flow) -> str:
        """완주 중인 위임(detach 포함)이 있으면 끝까지 기다리고, 도착한 위임 결과를 이어가기 리더에게
        전달할 본문으로 돌려준다(없으면 ''). CLI가 도구 호출을 포기해도 deliver 태스크는 계속 돌므로
        — 일하는 owner를 자르지 않고 결과를 회수하는 게 단일활성·작업 보존의 핵심이다."""
        tasks = [t for t in getattr(flow, "inflight_tasks", ()) if not t.done()]
        err_note = ""
        if tasks:
            self._log("await_inflight_delegation", n=len(tasks))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # [위임 실패 가시화(2026-06, 사용자)] 인플라이트 위임 턴이 예외로 죽으면 종전엔 gather가
            # 조용히 삼켜(return_exceptions) 리더가 결과도 에러도 못 받고 무진행으로 멎어 잘렸다(라이브:
            # 배승우→진서우 VFX 위임이 세션도 못 만들고 죽은 뒤 흐름 자동중단). 예외를 ① 로그로 남기고
            # ② 리더에게도 알려 기다리지 말고 재위임/우회하게 한다.
            errs = [r for r in results
                    if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError)]
            for e in errs:
                self._log("inflight_delegation_error", err=f"{type(e).__name__}: {e}"[:300])
            if errs:
                err_note = ("\n\n[위임 실패 — 방금 맡긴 작업이 실행 중 오류로 끝나지 못했습니다("
                            + "; ".join(f"{type(e).__name__}: {e}" for e in errs)[:300]
                            + "). 기다리지 말고 같은 일을 다시 request(Work)로 보내거나, 같은 직군의 다른 "
                            "동료에게 맡기세요(필요하면 recruit).]")
        res = getattr(flow, "detached_results", None)
        if res:
            out = ("\n\n[도착한 결과 — 직전에 진행하던 작업(동료 위임·배포·표결 등)이 끝나 결과가 도착했습니다] "
                   "백그라운드로 계속 돌지 않습니다(결과와 함께 멈춤). 결과가 완성이면 검증 후 진행/보고하고, "
                   "**위임 산출물이 미완(남은 파일·'⚠ 턴 한도')이면 기다리지 말고 즉시 같은 owner에게 "
                   "request(Work)로 '이어서'**를 보내세요(그래야만 작업이 계속됩니다). 배포 결과면 라이브 URL을 "
                   "확인해 보고하세요.\n"
                   + "\n".join(res[-3:]))
            del res[:]
            return out + err_note
        return err_note

    async def _auto_continue_owner(self, flow, lead, limit=None) -> str:
        """[구조적 이어가기] 현재 Task의 위임이 '구조적으로 미완'(owner_incomplete — 턴한도·무활동
        타임아웃으로 끊김)이면, 리더(LLM)의 판단·기억에 맡기지 않고 **SYS가 직접** 같은 owner에게
        '이어서'를 보낸다 — 미완 이어가기는 판단이 아니라 기계적 행동이므로 구조가 보장한다(리더가
        '비동기 작업 중' 오인으로 폴링하며 이어가기 예산을 태우던 결함의 구조적 차단). 호출은 리더
        명의의 표준 request 파이프라인(베턴·게이트·기록·Discord 게시 동일)을 그대로 쓴다. 완성되면
        (owner_incomplete 해제) 결과 요약을 돌려줘 리더가 '판정'(검증·마감)만 하게 한다."""
        out = []
        # [활동 기반 — 진행 중인 긴 작업은 안 자름] n은 폭주 절대 안전망일 뿐(종전 24는 *진행 중인 대형
        # 작업*을 잘랐음 — P-010류, 목표=최대 품질과 모순). 무진행이면 아래 break가 즉시 잡으므로, 이 수는
        # '진행 중인 정당한 사슬'을 자르지 않는 넉넉한 한도로 둔다.
        n = int(os.environ.get("ORGANT_AUTO_CONTINUE", "100")) if limit is None else limit
        _orig = (getattr(flow.current, "last_work_body", "") or "").strip() if flow.current else ""
        if _orig:
            # [정밀 복구 — 드리프트 차단] 리더가 재작문한 위임이 아니라 *원래 보냈던 위임 원문* 그대로 이어 보낸다
            # (부팅 복구 5:13≠5:47 드리프트 차단). owner는 원래 받았던 그 지시로 정확히 재개한다.
            body = ("[SYS 자동 이어가기 — 처음부터 다시 하지 말 것] 직전에 이 작업으로 위임받았습니다(원문 그대로):\n"
                    f"{_orig}\n\n[이어가기] 작업공간에서 이미 된 부분은 그대로 두고 남은 부분만 마저 끝내 완성하세요.")
        else:
            body = ("[SYS 자동 이어가기 — 처음부터 다시 하지 말 것] 직전 작업이 도중에 끊겼습니다. "
                    "작업공간을 확인해 이미 된 부분은 그대로 두고, 남은 부분만 마저 끝내 완성하세요.")
        while n > 0:
            ref = flow.current
            if (ref is None or not getattr(ref, "owner", 0) or not getattr(ref, "owner_incomplete", False)
                    or flow.comm.alive != lead or flow.comm.done):
                break
            n -= 1
            acts_before = flow.act_count
            self._log("sys_auto_continue", task=ref.task_id, owner=ref.owner, left=n)
            tools = {t.name: t for t in make_guide_tools(flow, lead, "leader")}
            try:
                res = await tools["request"].handler(
                    {"to_id": str(ref.owner), "kind": "Work", "body": body})
                txt = (res.get("content") or [{}])[0].get("text", "")
                # [핸드오프 — SYS 내부 호출은 결과까지 동기 회수] 프로덕션 request는 즉시 '[위임됨]'을 반환하고
                # 동료 작업을 인플라이트로 등록한다. SYS 내부 호출은 75초 도구호출이 아니라 블록 가능하므로,
                # 여기서 그 인플라이트를 완주시켜 *실제 결과*를 받고 베턴을 리더로 복귀시킨다(동기처럼). 안 그러면
                # 리더 run_turn이 owner 인플라이트와 동시에 돌아 이중 활성이 되고, 진행 판정도 빈 '[위임됨]'을 본다.
                if "[위임됨" in (txt or ""):
                    _d = await self._drain_inflight(flow)
                    if _d:
                        txt = _d
            except Exception as e:
                txt = f"(자동 이어가기 처리 오류: {e})"
                out.append(txt)
                break
            from .guide_tools import _speech_clip as _sc
            out.append(_sc(txt, 4000))   # 침묵 한계 금지 — 이어가기 결과도 내용이 곧 산출물
            # 진행이 전혀 없는데 여전히 미완이면(크래시 반복 등) 같은 호출을 더 박지 않는다 — 환경 문제.
            if flow.current is not None and flow.current.owner_incomplete and flow.act_count == acts_before:
                break
        if out:
            return ("\n\n[SYS 자동 이어가기 — 미완이던 위임을 시스템이 같은 담당자에게 이어 보내 받은 결과]\n"
                    + "\n".join(out))
        return ""

    async def _resume_precise_chain(self, flow, frames) -> str:
        """[정밀 복구 재개(2026-06-23, 사용자)] 끊긴 깊은 위임 체인을 *채팅 재발행/평탄화 없이* 복원·재개한다.

        restore_chain으로 comm 스택을 원래대로 세우고(alive=가장 깊은 워커), 그 워커부터 깨운 뒤 응답으로
        베턴이 올라올 때마다 부모를 *통합*으로 깨운다(재위임 아님) → C→B→A 자연 unwind, **각자 범위 보존**
        (C는 C 일, B는 B의 통합, A는 A의 통합). 종전 평탄화(리더→C 직접 1요청)가 B를 빼먹어 C/리더가 B 일까지
        떠안던 '범용적 잘못된 구현'(사용자) 교정. 끊긴 C에서 바로 재개 — A→B→C를 채팅으로 다시 치지 않는다.

        튼튼함(평탄화 폴백에 안 기댐 — 사용자 경고 반영): ① 워커가 재개 턴 중 *재위임*하면 drain_inflight로
        완주 대기 후 베턴 복귀, ② 워커가 *실패*해도 그 결과를 부모가 받아 자기 범위에서 처리(평탄화 아님),
        ③ 무진행/엣지는 guard로 끊고 베턴을 리더로 복구해 호출부의 리더 턴이 최종 판정하게 한다."""
        from .guide_tools import _speech_clip as _sc
        flow.comm.restore_chain(frames)          # 스택 내부 복원 — alive=가장 깊은 워커
        out, guard = [], 0
        while flow.comm.alive != flow.leader and not flow.comm.done and guard < 32:
            worker = flow.comm.alive
            top = flow.comm.open_requests[-1] if flow.comm.open_requests else None
            own = (getattr(top, "body", "") or "")
            if guard == 0:
                wbody = ("[SYS 정밀 복구 — 이어가기, 처음부터 다시/새 위임 하지 말 것] 직전에 이 작업으로 "
                         "위임받아 진행 중 끊겼습니다(원문 그대로):\n" + own +
                         "\n작업공간에 이미 된 부분은 그대로 두고 남은 부분만 마저 끝내 보고하세요.")
            else:
                wbody = ("[SYS 정밀 복구 — 당신 위임의 하위 작업이 끝나 돌아왔습니다] 당신이 맡았던 일(원문):\n"
                         + own + "\n\n그 일부를 맡긴 하위 작업의 결과:\n" + str(out[-1])[:1500] +
                         "\n\n이 하위 결과를 당신 일에 *통합·검증*하고 보고하세요(처음부터 다시/새 위임 말 것).")
            guard += 1
            acts_before = flow.act_count
            self._log("precise_resume_wake", worker=worker, level=guard, stack=len(flow.comm.open_requests))
            try:
                res = await flow.wake(worker, wbody, Kind.WORK)
            except Exception as e:
                res = f"(워커 {worker} 재개 실패: {e})"
            _d = await self._drain_inflight(flow)    # 재개 턴 중 재위임 → 완주 대기(베턴 복귀)
            if _d:
                res = (res or "") + "\n" + _d
            out.append(_sc(res or "", 4000))
            if flow.comm.alive == worker and not flow.comm.done:
                try:
                    flow.comm.respond(worker, "accept", res or "")   # 부모에 올림(C→B→A 자연 unwind)
                except CommError:
                    break
            elif flow.comm.alive != worker and not flow.comm.done:
                break                                # 비정상 베턴 — 아래 안전망이 리더로 복구
            if flow.comm.alive == worker and flow.act_count == acts_before and guard > 1:
                break                                # 무진행 안전망(같은 워커·작업 0)
        # 안전망: 베턴이 리더에 못 닿았으면 리더로 복구(드문 엣지 — 정밀이 대부분 처리, 평탄화 *기본* 아님)
        g2 = 0
        while flow.comm.alive != flow.leader and not flow.comm.done and len(flow.comm.open_requests) > 0 and g2 < 32:
            try:
                flow.comm.escalate("정밀 복구 마무리 — 리더로 베턴 복구")
            except CommError:
                break
            g2 += 1
        self._log("precise_resume_done", levels=len(out), alive=flow.comm.alive, done=flow.comm.done)
        return "\n\n".join(x for x in out if x.strip())

    async def _auto_delegate_owner(self, flow, lead) -> str:
        """[헛돎 발생 차단 — 구조적 위임(2026-06-15)] 리더가 현재 Task의 designated owner(스냅샷 복원
        등으로 지정됨)에게 **위임을 0건** 하고 솔로 run만 반복해 독식 차단(leader_runs>3)에 막힌 정체면,
        SYS가 직접 그 owner에게 '첫 위임'을 발사해 일이 굴러가게 한다. _auto_continue_owner는 '이미 위임된
        뒤 미완'만 잡으므로 '위임 0건'인 정체는 구조적 빈틈이었다(라이브: 신예준 P-014 — 거부 11·위임 0·
        헛돎). 헛돎을 '한도 N회 종결'로 사후 차단하지 않고 **발생 자체에서** 막는다(한도는 backstop). 위임이
        한 번 나가면 work_delegated>0이라 재발사 안 됨(1회). 리더는 완성본을 받아 판정만."""
        ref = flow.current
        if (ref is None or not getattr(ref, "owner", 0)
                or getattr(flow, "leader_runs", 0) <= 3
                or sum(getattr(t, "work_delegated", 0) for t in getattr(flow, "tasks", [])) != 0
                or flow.comm.alive != lead or flow.comm.done):
            return ""
        self._log("sys_auto_delegate", task=ref.task_id, owner=int(ref.owner),
                  leader_runs=int(getattr(flow, "leader_runs", 0)))
        tools = {t.name: t for t in make_guide_tools(flow, lead, "leader")}
        body = ("[SYS 자동 위임 — 리더가 위임 없이 헛돌아 시스템이 담당 owner에게 직접 맡김] 이 Task의 "
                "담당입니다. 작업공간에서 이미 된 부분은 두고 남은 부분을 직접 구현하고 run으로 검증해 보고하세요.")
        try:
            res = await tools["request"].handler({"to_id": str(ref.owner), "kind": "Work", "body": body})
            txt = (res.get("content") or [{}])[0].get("text", "")
            # [핸드오프 — SYS 내부 호출은 결과까지 동기 회수] 즉시 '[위임됨]'이면 인플라이트를 완주시켜 실제
            # 결과를 받고 베턴을 리더로 복귀(이중 활성·빈 결과 차단). _auto_continue_owner와 동일 이유.
            if "[위임됨" in (txt or ""):
                _d = await self._drain_inflight(flow)
                if _d:
                    txt = _d
        except Exception as e:
            return f"\n(SYS 자동 위임 처리 오류: {e})"
        from .guide_tools import _speech_clip as _sc
        return "\n\n[SYS 자동 위임 — 리더 헛돎 차단, 담당자에게 직접 발사한 결과]\n" + _sc(txt, 4000)

    async def _auto_coordinate(self, flow, lead) -> str:
        """[리더 조율 강제 — 구조(2026-06-23, 사용자)] 게이트가 막아 pending_coordination에 쌓인 교차도메인
        일을, 리더(LLM)가 프롬프트(coord_note)를 무시하고 안 집을 때 — SYS가 리더 명의로 *직접* 그 도메인
        전문가에게 위임한다. 워커가 보고로 올린 cross-domain needs를 리더가 안 비우면 영영 미배정(라이브
        P-030/P-031 정지의 직접 원인: 리더가 owner에만 재위임하고 프론트·데이터·AI needs 방치 → 그 일이 아무
        에게도 안 감). 리더는 게이트 면제라 그 전문가에게 통과한다. _auto_delegate_owner와 같은 동기 회수 —
        결과를 리더가 판정만. 프롬프트 의존(무시되던) 제거하고 구조로 큐를 비운다. 같은 도메인은 1회만."""
        coord = list(getattr(flow, "pending_coordination", None) or [])
        if not coord or flow.comm.alive != lead or flow.comm.done:
            return ""
        flow.pending_coordination = []          # SYS가 처리하므로 소비
        from .guide_tools import _speech_clip as _sc
        tools = {t.name: t for t in make_guide_tools(flow, lead, "leader")}
        out, seen = [], set()
        # [G1 예외 — SYS 내부 발사(B-04)] 조율 큐는 게이트를 이미 통과해 리더에게 이관된 일 — G1(미완 owner
        # 보호)이 이 발사를 거부하면 조율 항목이 조용히 유실된다. _sys_dispatch로 그 게이트만 우회(finally 복원).
        flow._sys_dispatch = True
        try:
            for c in coord:
                try:
                    to = int(c.get("to") or 0)
                except (TypeError, ValueError):
                    continue
                if not to or to in seen:            # 같은 도메인 중복 위임 방지
                    continue
                seen.add(to)
                body = (f"[SYS 조율 — {c.get('req_role')}이(가) 막혀 리더가 당신({c.get('to_role')}) 도메인에 배정] "
                        f"{(c.get('body') or '')}\n작업공간에 이미 된 부분은 두고 당신 도메인 부분을 직접 구현·검증해 보고하세요.")
                self._log("auto_coordinate", to=to, frm=c.get("requester"))
                try:
                    res = await tools["request"].handler({"to_id": str(to), "kind": "Work", "body": body})
                    txt = (res.get("content") or [{}])[0].get("text", "")
                    if "[위임됨" in (txt or ""):
                        _d = await self._drain_inflight(flow)
                        if _d:
                            txt = _d
                    out.append(_sc(txt or "", 3000))
                except Exception as e:
                    out.append(f"(조율 위임 오류 {to}: {e})")
                if flow.comm.alive != lead or flow.comm.done:
                    break
        finally:
            flow._sys_dispatch = False
        if out:
            return "\n\n[SYS 조율 — 막혔던 교차도메인 일을 해당 도메인 전문가에게 직접 배정한 결과]\n" + "\n".join(out)
        return ""

    async def _floor_segment_open(self, flow, lead) -> str:
        """[1층 floor seam — 세그먼트 경계의 발언권 open(ORGANT_FLOOR=turn-taking일 때만)] 리더
        이어가기 직전은 종전엔 '중앙(SYS)이 무조건 리더 계속'이던 TRP다 — turn-taking이면 여기서
        Sacks ②자기선택을 연다: 팀원(회전 순 최대 ORGANT_FLOOR_OFFERS명, 기본 3)이 **각자 LLM으로
        '지금 내가 보태야 하나'를 응찰 판정**(병렬 _fork_collect)하고, 최고 응찰자가 발언권을 받아
        정식 발언(comm 프레임 — 베턴·점유 담보 동일)한다. 무응찰이면 종전과 동형(③리더 계속).
        실발언은 리더 continue 본문에 동봉(통합·판정은 리더 몫 — 발언권이지 결정권이 아님).
        기본(미설정)은 즉시 ''(no-op) — 라이브 동작 불변(default-OFF 관례)."""
        from .rule.floor import floor_mode
        if floor_mode(getattr(flow, "floor_mode", None)) != "turn-taking":
            return ""
        if (flow.current is None or flow.comm.done or flow.comm.alive != lead
                or flow.cancelled or getattr(flow, "_hard_blocked", None)):
            return ""
        from .rule.communication import (BusyInOtherFlow, CommError, _bid_score,
                                         _fork_collect, _is_spare, _turn_signals)
        from .guide_tools import _speech_clip as _sc
        team = [m for m in getattr(flow.current, "team", []) if m != lead and not _is_spare(flow, m)]
        if not team:
            return ""
        rot = flow.leader_segment % len(team)
        cap = max(1, int(os.environ.get("ORGANT_FLOOR_OFFERS", "3") or "3"))
        cands = (team[rot:] + team[:rot])[:cap]      # 회전 = 응찰 기회 공정성(장부 없는 결정론)

        def _probe_body(c):
            return ("[발언권 응찰 — 자기선택] 진행 중인 작업 상황에 **지금** 보태야 할 관찰·우려·"
                    "제안이 있는지 스스로 판단하세요. 있으면 `[응찰: N]`(N=1~9, 필요 강도)과 한 줄 "
                    "요지만, 없으면 `[패스]`만 답하세요.")
        bids = []
        for m, res, _note in await _fork_collect(flow, lead, cands, _probe_body):
            s = 0 if res is None else _bid_score(res)
            bids.append((int(m), s))
            self._log("floor_bid", surface="segment", who=int(m), score=s)
        pos = sorted((b for b in bids if b[1] > 0), key=lambda b: -b[1])   # 동률=회전 순(stable)
        if not pos:
            self._log("floor_alloc", surface="segment", policy="turn-taking", kind="continue", nxt=int(lead))
            return ""                                # ③ 무응찰 → 리더 계속(종전과 동형)
        w = pos[0][0]
        try:
            flow.comm.request(lead, w, "floor-open", Kind.INFO)
        except (BusyInOtherFlow, CommError):
            return ""                                # 낙찰자 점유 경합 등 — 이번 경계는 그냥 리더 계속
        self._log("floor_alloc", surface="segment", policy="turn-taking", kind="self",
                  nxt=int(w), reason=f"응찰 {pos[0][1]}")
        try:
            res = await flow.wake(
                w, "[발언권 획득 — 응찰 선정] 방금 응찰한 관찰·우려·제안을 3~7줄로 발언하세요"
                   "(구체적으로 — 무엇이, 왜, 어떻게 하자는 것인지).", Kind.INFO)
        except Exception as e:
            res = f"(발언 실패: {e})"
        try:
            flow.comm.respond(w, "accept", res or "")
        except CommError:
            pass
        _, passed = _turn_signals(flow, res, team)
        if passed or not (res or "").strip():
            return ""                                # 낙찰 후 변심([패스]) — 리더 계속
        if w in flow.current.team and w != flow.leader:
            flow.current.participated.add(w)         # 자기선택 발언 = 실질 협의 인정(meet와 동형)
        return ("\n\n[1층 발언권 open — 세그먼트 경계에서 팀원이 응찰로 발언권을 얻어 발언했습니다"
                "(통합·판정은 당신 몫)]\n"
                f"[자기선택 발언 — {flow._info(w) or w}] {_sc(res, 2000)}")

    async def _hard_block_probe(self, flow, lead) -> bool:
        """[G4 — 연속 실패 하드블록 자기치유(B-03)] '연속 무응답' 하드블록만 대상: 백오프 뒤 SYS 프로브
        wake 1회 — 성공(비일시오류 응답)하면 블록·카운터를 풀고 True(이어가기 재개), 실패면 False(종결 유지).
        배포 자격증명 등 '사람 조치' 하드블록은 프로브 대상이 아니다(즉시 False — 종전 종결 동작 불변).
        loop_escalated 해제(사람 개입 시)와 짝을 이루는 자동 해제 경로 — 밤새 소각과 영구 정지 사이의 중간값."""
        from .rule.communication import HARD_BLOCK_TRANSIENT
        from ._util import _looks_transient
        if not str(getattr(flow, "_hard_blocked", "") or "").startswith(HARD_BLOCK_TRANSIENT):
            return False
        backoff = float(os.environ.get("ORGANT_HARDBLOCK_BACKOFF", "60"))
        self._log("hard_block_probe", backoff=int(backoff))
        await asyncio.sleep(backoff)                      # 백오프 — 일시 불안정이 지나가게
        if flow.cancelled or flow.wake is None:
            return False
        who = (flow.current.owner if (flow.current is not None and getattr(flow.current, "owner", 0))
               else lead)
        try:
            res = await flow.wake(who, "[SYS 프로브 — 환경 회복 확인] 작업하지 말고 한 줄로만 응답하세요: "
                                       "지금 응답 가능합니까?", Kind.INFO)
        except Exception as e:
            res = f"api error: {e}"
        if (res or "").strip() and not _looks_transient(res):
            flow._hard_blocked = None
            flow.consec_fail = 0
            self._log("hard_block_cleared", by="probe", who=int(who))
            return True
        self._log("hard_block_probe_failed", who=int(who))
        return False

    async def _run_until_silent(self, coro_factory, flow) -> str:
        """coro를 실행하되, '도구 활동(flow.last_activity)이 turn_timeout 동안 한 번도 갱신되지 않은'
        경우(=진짜 행)에만 취소하고 TimeoutError를 낸다. 도구가 하나라도 돌면 시계가 갱신되어 무한정
        허용된다 → '퀄리티 있게 오래 일하는 owner'는 안 자르고 '완전히 멈춘 것'만 끊는다(벽시계 고정
        타임아웃이 일하는 워커를 잘라 좀비·미완을 만들던 결함의 근본 교정)."""
        flow.last_activity = time.monotonic()
        task = asyncio.ensure_future(coro_factory())
        poll = max(1, min(15, self.turn_timeout))
        timed_out = False

        async def _wd():
            nonlocal timed_out
            while not task.done():
                await asyncio.sleep(poll)
                idle = time.monotonic() - getattr(flow, "last_activity", time.monotonic())
                if idle > self.turn_timeout and not task.done():
                    timed_out = True
                    task.cancel()
                    return

        wd = asyncio.ensure_future(_wd())
        try:
            return await task
        except asyncio.CancelledError:
            if timed_out:
                raise asyncio.TimeoutError   # 무활동(행)으로 우리가 끊은 것
            raise                            # 외부(상위 흐름)에서 취소 — 그대로 전파
        finally:
            wd.cancel()
            if not task.done():              # 외부 취소·타임아웃 어느 쪽이든 내부 task 누수 방지
                task.cancel()

    async def run_turn(self, flow: Flow, organt_id, body, kind, role) -> str:
        # 에이전트가 죽으면(SDK 메시지리더 크래시·서브프로세스 SIGTERM 등) 같은 세션으로 되살려 재시도.
        # State는 organt_id별 파일에 영속되므로 새 인스턴스가 세션을 이어간다(전체 워크플로우 보호).
        flow.last_activity = time.monotonic()   # 진행 신호(턴 시작) — 무진행 워치독 갱신
        self._stage_inbound(flow)               # [파일 전송] 사용자 첨부를 작업공간 inbox/로(워크스페이스 준비됐으면, 멱등)
        # [일로 직업 획득 — Discord 역할 비동기 부여] 첫 실작업으로 '획득'된 직군을 Discord 역할로 영속한다
        # (jobs.json은 권한 훅이 이미 동기로 박음; Discord는 리클레임 복원용 — 비동기라 여기 턴 경계에서 드레인).
        _q = getattr(flow, "role_earned_queue", None)
        if _q:
            _fn = getattr(self.guide, "assign_job_role", None)
            while _q:
                _mid, _lbl = _q.pop(0)
                if _fn and getattr(flow, "guild_id", None):
                    try:
                        await _fn(flow.guild_id, _mid, _lbl)
                        self._log("role_earned", member=int(_mid), role=_lbl)
                    except Exception:
                        pass
        # [채용 제네시스 — 첫-사용 훅] 봇이 첫 실턴을 갖기 직전, 직군은 있으나(이 흐름 기준) 정체성이
        # 없으면 리크루터가 먼저 온보딩한다 — 리더 recruit(flow-로컬 tentative)·직접(Studio)·복구·재시작
        # 모든 경로를 '첫 사용' 한 지점에서 커버(배경 사이클은 유휴 봇 선제 보강용). 가드 순서상 이미
        # 정체성 있는 봇은 _pick_recruiter 전에 단락 — 핫패스 비용은 dict 조회뿐.
        _rl = (getattr(flow, "bot_info", None) or {}).get(organt_id) or self.bot_info.get(organt_id)
        if (_rl and not str(_rl).startswith("예비")
                and not self.bot_experience.get(organt_id) and not self.bot_profiles.get(organt_id)
                and self._pick_recruiter() not in (None, organt_id)):
            try:
                await self.onboard_bot(organt_id, role=str(_rl))
            except Exception:
                pass
            flow.last_activity = time.monotonic()   # 온보딩 소요를 무진행으로 오인 안 하게 진행 신호 갱신
        last = ""
        # [G3 — 캐주얼 도구 미장착(B-06)] 좁은 캐주얼 판정(캐주얼 신호+빌드동사 없음, 리더 턴)이면 협업·제작
        # 도구를 아예 장착하지 않는다(mode="casual": run만) — "도구 쓰지 마세요" 프롬프트 의존(재발점 ③)을
        # 구조로 대체. Info 단독은 전체 장착 유지(팀 토론 진행 경로 보존). 기본값 collab = 현행 동일(하위호환).
        _mode = "casual" if _casual_turn(body, role) else "collab"
        for attempt in range(3):
            server = build_guide_server(flow, organt_id, role, mode=_mode)
            organt = self.organt_builder(organt_id, server, role, flow)
            # [적당히 — wake-aware 규칙 주입(막힘↔성능)] resume(직전 대화 기억)이 보존되면 델타(핵심 규칙만),
            # fresh 세션이면 전체(봇이 처음 배움). 세션 존재는 organt._session_in_store 결정론이라 추측 없이
            # 정확 — resume 신뢰성은 라이브 로그로 확인됨('No conversation found'=0, 사전점검이 헛돌이 제거).
            # 규칙은 게이트 백스톱이 담보라 델타여도 막힘 낮음. task·cwd(백스톱 없음)는 매 턴 full(앵커).
            # [내구적 배치 — 되살리기 없음] first_wake면 전부 1회 가르치고, 이후 resume는 동적 task만.
            # 정적 지식·앵커는 재주입(반복)이 아니라 압축에도 살아남는 구조가 담보한다: 원칙·단일활성=persona
            # (system_prompt)·게이트, 원문·기준=디스크(Dossier·PLAYBOOK), [경험]=report 툴 필드. 대화 기억이
            # 살아있으면 거기서, 압축돼도 그 구조들에서 — 같은 텍스트를 스케줄로 다시 밀어넣지 않는다.
            _first_wake = flow is None or not getattr(organt, "will_resume", lambda: False)()
            if flow is not None:
                self._write_team_dossier(flow)   # 로스터가 recruit로 바뀌었으면 TEAM.md 갱신(변경 시에만 기록)
            try:
                # '…입력 중' 표시: 깨어난 Organt가 응답·작업을 작성하는 동안 현재 Task 스레드
                # (없으면 유저 채널)에 가시화. guide에 typing 없으면(테스트 등) 그냥 건너뜀.
                ch = (flow.current.thread_id if flow.current else None) or flow.user_channel
                tcm = getattr(self.guide, "typing", None)

                async def _do():
                    if tcm is not None:
                        async with tcm(ch, organt_id):
                            return await organt.handle(self._prompt(body, kind, role, organt_id, flow.leader, flow, first_wake=_first_wake))
                    return await organt.handle(self._prompt(body, kind, role, organt_id, flow.leader, flow, first_wake=_first_wake))

                # 리더 턴은 '흐름 전체'(중첩 워커 포함)를 품으므로 여기선 타임아웃 안 건다 — 상위 무진행
                # 워치독이 흐름 전체를 본다. 워커(비-리더) 턴은 '도구 활동이 turn_timeout 동안 완전히 멈춘'
                # 경우(진짜 행)에만 끊는다 — 일하는 동안은 무한정 허용(하트비트). 끊기면 '인프라 실패'로 반환.
                if role == "leader":
                    _out = await self._absorb_role_profiles(await _do(), me=organt_id)
                else:
                    _out = await self._absorb_role_profiles(await self._run_until_silent(_do, flow), me=organt_id)
                # [B-14 — report 스태시 흡수(인자 > regex; [경험]/[직무기준] 블록 폴백 존치)] 이 턴에 봇이
                # report 도구로 남긴 experience/craft_standard를 종전 블록 텍스트로 합성해 같은 흡수 경로
                # (_absorb_role_profiles — 영속·'없음' 필터 포함)로 소비한다. 두 키만 pop — 나머지 필드
                # (offdomain_role 등)는 _deliver가 소비한다.
                _st = (getattr(flow, "report_stash", None) or {}).get(organt_id) if flow is not None else None
                if _st and (_st.get("experience") or _st.get("craft_standard")):
                    _jobs = [j.strip() for j in str(self.bot_info.get(organt_id, "")).split("·")
                             if j.strip() and not j.strip().startswith("예비")]
                    if _jobs:
                        _syn = ""
                        _exp = str(_st.pop("experience", "") or "").strip()
                        if _exp:
                            _syn += f"[경험] {_jobs[0]}\n{_exp}\n[/경험]\n"
                        _cs = str(_st.pop("craft_standard", "") or "").strip()
                        if _cs:
                            _syn += f"[직무기준] {_jobs[0]}\n{_cs}\n[/직무기준]\n"
                        if _syn:
                            await self._absorb_role_profiles(_syn, me=organt_id)
                if flow is not None:   # [사람 개입] 주입된 노트 소비-clear(턴 성공 후 1회 — revive 재시도엔 유지돼 재주입)
                    try:
                        (flow.pending_info or {}).pop(organt_id, None)
                    except Exception:
                        pass
                return _out
            except asyncio.TimeoutError:
                self._log("agent_timeout", organt=organt_id, role=role, sec=self.turn_timeout)
                return (f"API Error: timeout — 동료({organt_id}) 서브프로세스가 {self.turn_timeout}s 동안 "
                        f"도구 활동이 전혀 없어(행) 끊겼습니다. 단일흐름이라 인프라 문제로 간주(크래시와 동일) — "
                        f"대체 채용 말고, 진행하던 일이 있으면 같은 담당자에게 '이어서' 재요청하거나 보고하세요.")
            except Exception as e:
                last = f"(에이전트 {organt_id} 처리 실패: {e})"
                self._log("agent_revive", organt=organt_id, attempt=attempt + 1, err=str(e)[:100])
                await asyncio.sleep(2 * (attempt + 1))
        return last

    async def _ensure_deploy(self, flow, lead, result):
        """배포 가능한 산출물(package.json)인데 deploy가 안 불렸고 자격증명·배포 슬롯(등록 프로젝트)이
        있으면, 리더에게 의존하지 않고 **SYS가 직접 deploy_sync로 배포**한다(리더가 빼먹는 누락 구멍 차단).
        미등록 흐름은 슬롯이 없어("") 자연 스킵 — 배포 신원은 프로젝트가 보증한다(사용자 설계).
        deploy_sync가 라이브 URL 실제 응답까지 확인하므로, 거짓 성공이 아니라 진짜 배포가 보장된다."""
        ws = str(flow.workspace) if flow.workspace else ""
        # 품질 게이트: 흐름이 미완으로 끝나거나(중단될 Task가 남음) 이 흐름에서 '완료'된 Task가 하나도
        # 없으면 강제 배포하지 않는다 — 미완·실패 산출물이 흐름 종료마다 자동으로 라이브를 덮던 것 차단.
        completed = any(getattr(getattr(t, "status", None), "status", "") == "완료"
                        for t in getattr(flow, "tasks", []))
        if flow.current is not None or not completed:
            return result
        deployable = bool(ws) and os.path.exists(os.path.join(ws, "package.json"))
        gh, ghu = os.environ.get("GH_PAT"), os.environ.get("GH_USER")
        rk, owner = os.environ.get("RENDER_KEY"), os.environ.get("RENDER_OWNER")
        from .guide_tools import deploy_service_name
        name = deploy_service_name(flow)   # [멀티 프로젝트] 프로젝트별 결정적 서비스명(env 고정 제거)
        # [cap 우회 차단(2026-06-23 전수감사; 리뷰F3 교정)] 배포 런어웨이 cap(5회)이 걸리면 SYS 강제배포를
        # *건너뛴다* — 종전엔 cap이 flow.deployed를 안 세팅해 여기가 6번째 배포를 강제하던 결함. deploy_capped는
        # 인메모리라 재시작 후 풀리지만 _deploy_count(≥5)는 영속되므로, 둘 중 하나라도 서면 막아 *재시작 너머*
        # cap을 보장한다(리뷰F3: deploy_capped만 보면 복구 후 6번째 배포가 1회 새던 잔여 구멍). 사용자에 에스컬레이트.
        if getattr(flow, "deploy_capped", False) or getattr(flow, "_deploy_count", 0) >= 5:
            self._log("ensure_deploy_skipped_capped", count=getattr(flow, "_deploy_count", 0))
            try:
                await self.guide.post(
                    flow.user_channel, 0,
                    "[배포 중단 — 런어웨이 차단] 프로젝트 배포를 5회 초과 시도했습니다(코드 수정으론 안 고쳐지는 "
                    "*배포 구조/타겟 문제* 가능성). 자동 재배포를 멈췄습니다 — 확인이 필요합니다.")
            except Exception:
                pass
            return result
        if flow.deployed or not (deployable and name and gh and ghu and rk and owner):
            return result
        try:
            import anyio
            from .deploy import deploy_sync
            dep = await anyio.to_thread.run_sync(deploy_sync, ws, name, gh, ghu, rk, owner)
            flow.deployed = dep
            self._log("ensure_deploy", forced=True)
            return f"{result}\n\n[배포(SYS 강제)] {dep}"
        except Exception as e:
            return f"{result}\n\n(SYS 배포 강제 중 오류: {e})"

    def _close_flow(self, flow, leader_id, result):
        """베턴을 origin까지 닫는다. 정상이면 리더가 alive→clean close, 비정상(중간 미응답)이면
        열린 프레임을 위로 강제 정리(escalate)해 교착 없이 종료한다."""
        comm = flow.comm
        if not comm.done and comm.alive == leader_id and len(comm.open_requests) == 1:
            comm.respond(leader_id, "accept", result)        # 정상 종료
            return
        guard = 0
        while not comm.done and guard < 64:                   # 비정상: 강제 드레인
            guard += 1
            try:
                comm.escalate("흐름 종료 강제 정리(중간 미응답)")
            except CommError:
                break

    def record_user_feedback(self, channel_id, text):
        """[RFC-011 M3] 사용자 피드백을 프로젝트에 누적·영속 — sys_store.record_user_feedback로 추출(위임만)."""
        return sys_store.record_user_feedback(self, channel_id, text)

    def _aggregate_feedback(self, proj):
        """[크로스-프로젝트 취향] 이 프로젝트+과거 피드백 집계 — sys_store.aggregate_feedback로 추출(위임만)."""
        return sys_store.aggregate_feedback(self, proj)

    def _valid_leader(self, proj):
        """리더 부재 시 가용 봇 자동 재배정(영속) — sys_store.valid_leader로 추출(위임만)."""
        return sys_store.valid_leader(self, proj)

    async def handle_user_input(self, channel_id, leader_id, user_text, root_id=None, attachments=None) -> dict:
        proj = self.projects.get(int(channel_id))   # 이 채널이 등록된 프로젝트면 '개입'(이어지는 작업)
        # [신규×신규 병렬 완화] 신규 요청도 고유 스코프로 동시 진행한다 — 과거 'main' 직렬은 등록
        # 경합 방지용이었으나 전역 점유·스코프 선점·원자 등록 이후 근거가 소멸(라이브: 서로 다른
        # 리더에게 보낸 두 신규가 직렬돼 병렬 의도가 좌절). 같은 리더면 전역 점유가 자연 직렬화한다.
        scope_key = proj["id"] if proj else f"new-{int(time.time() * 1000)}"
        live = {k: f for k, f in self.active_flows.items() if not f.done}
        # 이 흐름을 이끌 봇(전망치): 명시 To(리더 재지정 포함)가 로스터에 있으면 그 봇, 아니면 등록 리더.
        # 게이트에서 미리 계산해야 '리더가 타 흐름 참여 중'을 흐름을 띄우기 전에 거를 수 있다.
        prospective_lead = (leader_id if (leader_id and leader_id in self.bot_info)
                            else (self._valid_leader(proj) if proj else leader_id))
        # [병렬] 큐로 보내는 세 조건(버리지 않음 — 흐름 내 규약은 불변): ① 같은 스코프 진행 중(직렬)
        # ② 운영 노브 상한(설정 시에만) ③ 리더가 타 흐름 점유 중(한 직원은 한 번에 한 흐름 — 같은
        # 리더의 프로젝트들은 자연 직렬이 되고, 이것이 임의 흐름 수 상한을 대체하는 구조적 안전이다).
        if (scope_key in live
                or (self.max_flows > 0 and len(live) >= self.max_flows)
                or self.engaged.busy_elsewhere(prospective_lead, scope_key)):
            self.queue.append((channel_id, leader_id, user_text, root_id))
            self._save_projects()   # [큐 영속] 적재 즉시 디스크 — 죽어도 대기 요청 유실 안 되게
            self._log("queued", text=user_text[:80], depth=len(self.queue), scope=scope_key,
                      lead_busy=bool(self.engaged.busy_elsewhere(prospective_lead, scope_key)))
            # [Rule/Status — 침묵하는 큐 금지] 접수 사실을 즉시 보이게 한다(라이브: 큐에 든 요청이
            # 아무 표시 없이 조용해 사용자가 '못 들은 것'으로 체감). 묻기 전에 보여야 한다.
            try:
                await self.guide.post(
                    channel_id, 0,
                    f"⏸ 접수됨 — 대기열 {len(self.queue)}번째. 담당 동료가 진행 중인 작업을 마치면 "
                    f"자동으로 시작합니다(따로 다시 보내지 않으셔도 됩니다).",
                    reply_to=root_id)
            except Exception:
                pass
            return {"mode": "queued", "queued": len(self.queue)}
        # 세션 초기화는 '새 최상위 요청'에만 한다 — 기존 프로젝트 '개입(이어서/수정)'에선 건너뛴다.
        # [근본] 개입은 진행 중이던 팀·위임·owner를 '이어가야' 하는데, 세션을 지우면 리더와 동료가 그 기억을
        # 통째로 잃고(resume할 session_id가 사라짐) 처음부터 다시 계획한다 — 이게 사용자가 본 '리더가 직전
        # 위임(예: 장도현→김민준)을 무시하고, 팀을 일부만 다시 부르고, 혼자 검토·마무리하던' 행동의 근본 원인이다.
        # 개입 본문엔 새 요청/증상이 명시되므로 '이미 했다' 앵커링도 생기지 않는다(앵커링 방지 목적은 새 요청에만
        # 유효). 컨테이너 리클레임으로 세션 파일이 이미 사라졌으면 어차피 새로 시작하니 무해하다(그건 별개 유실).
        # [세션 스코프] 봇 세션 파일을 흐름 스코프별로 분리한다(organt_state_<scope>_<bot>.json) —
        # 프로젝트 간 기억 교차 오염이 '구조적으로' 불가능(병렬 동시 흐름에서도 안전). 새 요청은
        # 고유 스코프로 시작하므로 '이미 했다' 앵커링도 구조적으로 차단(리셋 불필요). 같은 프로젝트
        # 개입은 그 프로젝트 스코프 파일을 resume — 기억이 이어진다.
        session_scope = proj["id"] if proj else scope_key   # 신규는 흐름 스코프=세션 스코프(단일 정체성)
        if proj:
            self._log("intervention_keep_sessions", project=proj["id"])
        # 이전 흐름의 런타임 채용(예비→직군) 라벨 원복 — dict는 그대로 두고 내용만 갱신(빌더 클로저가 참조 중).
        self.bot_info.clear()
        self.bot_info.update(self._roster_labels)
        self._origin_request = (user_text or "").strip()   # 원문 보존 — 담당자가 요약·해석하기 전 '사용자가 실제로 한 말'
        # 리더 재지정(사용자 요청): 개입 시 [Request] To로 현 리더와 '다른' 봇을 명시하면 그 봇을 이 프로젝트의
        # 새 담당자로 갱신한다 — 게임 프로젝트인데 '백엔드'가 담당자로 고정되던 문제 해소(기획자 등으로 담당 이양
        # 가능). 평문 개입은 main이 to_id를 현 리더로 채우므로, leader_id != proj.leader면 '명시적 지정'으로 본다.
        if proj and leader_id and leader_id != proj.get("leader") and leader_id in self.bot_info:
            self._log("leader_reassigned", project=proj["id"], old=proj.get("leader"), new=leader_id)
            proj["leader"] = leader_id
            self._save_projects()
            self._sync_topic(channel_id)   # 토픽(서버 영속)에도 반영 — 리클레임 후 시드로 원복되지 않게
        lead = self._valid_leader(proj) if proj else leader_id
        flow = Flow(self.guide, channel_id, self.guild_id, lead, self.bot_info)
        flow._handoff = True   # [논블로킹 핸드오프] 프로덕션은 위임을 즉시-반환 핸드오프로(75초 detach·비동기 churn
                               #   차단). 동료 작업은 SYS가 호출 밖에서 직렬 완주시켜 결과로 잇는다. (테스트는 기본 동기.)
        flow.inbound_attachments = list(attachments or [])   # [파일 전송] 사용자 첨부 — 워크스페이스 준비 시 inbox/로 staging
        flow.stage_inbound = lambda: self._stage_inbound(flow)  # create_project가 워크스페이스 만든 직후 즉시 staging(turn1 가용)
        flow.session_scope = session_scope
        # [교차오염 차단 — 흐름별 원문 스냅샷] 사용자 원문을 흐름 객체에 '박제'한다. self._origin_request는
        # 다음 개입이 오면 덮어쓰이는 전역 단일 필드라, 동시 흐름이 있으면 먼저 돌던 흐름의 봇들이 _prompt에서
        # '나중 개입의 원문'을 진짜 의도로 받아 엉뚱한 작업으로 새 버린다(라이브 관측: P-016 웹 흐름이 진행 중일
        # 때 P-015 게임 개입이 도착→웹 리더가 '게임성을 강화해'를 자기 원문으로 받아 게임을 짓기 시작). 여기서
        # 박제하면(이후 await로 다른 개입이 끼어들어도) 이 흐름의 모든 프롬프트는 자기 원문만 본다.
        flow.origin_request = self._origin_request
        # [RFC-011 M3] 이 프로젝트에 누적된 사용자 취향(반복된 비평·요구)을 흐름에 부착 — set_goal·검증이
        # '상용 수준'의 외부 앵커로 되돌린다(사용자 자신의 말이라 하드코딩 0, 회차가 쌓일수록 기준 상승).
        # [크로스-프로젝트 취향 누적 — '사용자=유일 불만족 엔진'을 영속화(2026-06-20)] 종전엔 *이 프로젝트*
        # 피드백만 봐서, 한 작품서 고친 걸(자동위치·URL거짓·깊이 등) 다음 작품서 *또 틀렸다*. 사용자 교정은
        # 작품을 가로질러 유효하므로, 과거 프로젝트들의 피드백도 끌어와 '이 사용자가 반복 요구하는 기준'으로
        # 함께 주입한다 — 게이트를 불만마다 새로 다는 대신(끝없음), 인간 신호가 표준으로 쌓여 스스로 개선.
        flow.user_feedback = self._aggregate_feedback(proj)   # 이 프로젝트 + 과거 작업의 취향(크로스-프로젝트 표준)
        # [선점 — 레이스 봉쇄] 게이트 통과 직후·첫 await 이전에 스코프를 점유한다. 등록이 늦으면
        # (개입 복원 등 await 사이) 같은 채널의 연속 메시지가 둘 다 게이트를 통과해 '같은 프로젝트에
        # 흐름 2개'가 생길 수 있다(작업공간·베턴 이중화). 병렬 도입 전부터 있던 창을 함께 봉쇄.
        self.active_flows[scope_key] = flow
        # [전역 점유 — 리더 선점] 같은 sync 블록에서 리더를 장부에 등록 + 흐름의 comm을 장부에 연결.
        # 다른 프로젝트의 동시 시작이 같은 리더를 집어가는 레이스가 구조적으로 불가능해진다
        # (asyncio 단일 스레드 — 게이트 검사~여기까지 await 없음). start_root의 재등록은 멱등.
        self.engaged.engage(lead, scope_key)
        flow.comm.attach_engagement(self.engaged, scope_key)
        # [상황 인지] 이 채널의 최근 대화를 흐름에 부착 — 봇이 '지금 이 채널·이 상황'을 알고 답하게 한다.
        # 스코프 점유(active_flows·engage) 뒤라 await 안전(이중 흐름 레이스 없음 — 위 [선점] 불변식 유지).
        # 신규 흐름은 세션이 비어 채널 맥락을 모르므로, 이게 없으면 눈앞의 회사 포트폴리오에 앵커링한다.
        try:
            flow.channel_situation = await self._channel_situation(channel_id, exclude_root=root_id)
        except Exception:
            flow.channel_situation = ""
        def _reg(ch, name):
            # [신원 재사용 권한] 개입(proj)은 자기 프로젝트 연장이 자명 → 무제한(None).
            # 메인 채널 신규 흐름은 사용자 원문에 명시된 P-번호만 재사용 가능(주소 지정의 이치).
            # 흐름에 박제된 원문 사용(전역 self._origin_request는 동시 개입에 덮어쓰여 — 이 closure는
            # 흐름 도중 실행되므로 전역을 읽으면 '남의 프로젝트 원문'으로 등록될 수 있다).
            _orig = (getattr(flow, "origin_request", "") or self._origin_request or "")
            reuse_ok = None if proj is not None else {
                f"P-{m}" for m in re.findall(r"[Pp]-?(\d{3})", _orig)}
            pid = self._register_project(ch, name, flow.workspace, flow.leader,
                                         purpose=_orig,  # 존재 이유 = 사용자 원문
                                         origin_msg=root_id or "",      # 원요청 링크(부팅 복구의 개입 라우팅 근거)
                                         reuse_ok=reuse_ok)
            p0 = self.projects.get(int(ch))
            if p0 is not None and status_mid and not p0.get("origin_status"):
                # [시초 계기판 영속] 원요청의 상태 메시지(채널·id·시작 시각)를 프로젝트에 기록 —
                # 졸업 재개가 새 계기판을 달지 않고 이 시초를 되살린다(사용자 설계).
                p0["origin_status"] = {"channel": status_ch, "id": str(status_mid),
                                       "started": int(time.time() - (time.monotonic() - status_t0))}
                self._save_projects()
            p = self.projects.get(int(ch))
            if p and p.get("workspace"):
                flow.workspace = p["workspace"]   # id-개명(p-00n-슬러그)/재사용(기존 산출물) 결과 채택
                self._stage_inbound(flow)         # [파일 전송] 워크스페이스 생긴 즉시 사용자 첨부를 inbox/로(turn1 가용)
            return pid
        flow.register_project = _reg
        # '기억'(직업 고정): 예비가 recruit로 직군을 받으면 그 직업을 다음 흐름에도 유지하도록 로스터 라벨에 반영
        # — 흐름 시작 때 _roster_labels로 원복되므로, 여기에 기록해야 채용한 직업이 지속된다(1봇 1직업의 연속성).
        flow.persist_role = self._persist_job   # 채용한 직군을 메모리+디스크(jobs.json)에 영속(재시작에도 유지)
        flow.persist_capability = self._persist_capability   # [B-21] 품질 게이트 통과 Task의 owner 실적 영속(complete_task→_ledger_accrue가 호출)
        flow.capability_ledger = self.capability_ledger      # [B-21 용도②] _free_alternatives 후보 나열용 장부 읽기 참조(판정 아님)
        flow.craft_of = lambda job: (self.role_profiles.get(str(job).strip(), "") or "")   # [RFC-008 P0] 직군 직무기준 → 검증 루브릭 조회
        flow.projects_provider = lambda: list(self.projects.values())   # [B-18③] list_projects pull 도구의 데이터 소스(push 캡 16건의 보강)
        flow.checkpoint_task = lambda: self._checkpoint_open_task(flow)   # Task 전이마다 크래시-세이프 영속
        flow.persist_owner = lambda: self._save_file_owner(flow)          # [소유 경계] 새 파일 귀속 시 영속
        body = user_text
        if proj:                                     # 기존 프로젝트 개입 — 맥락 유지(재생성 X)
            flow.project_channel = int(channel_id)   # 기존 채널 재사용 → create_project는 no-op
            flow.workspace = proj["workspace"]
            # [소유 경계 복원/시딩] 저장된 file_owner를 흐름에 싣고(복구에도 유지), 비어 있으면(추적 첫 시작)
            # audit 이력의 최초 작성자로 1회 시딩 — 기존 파일도 올바른 직군 소유로(분류 아닌 생성 기록 기반).
            flow.file_owner = dict(proj.get("file_owner") or {})
            if not flow.file_owner:
                self._seed_file_owner(flow)
            flow.project_id, flow.intervention = proj["id"], proj
            flow.project_name = proj.get("name")   # 배포 슬롯 유도(프로젝트별 결정적 서비스명)
            # 미완 Task 되살리기: 저장된 '진행 중' Task가 있으면 같은 블록·스레드·owner로 재부착(flow.current).
            # → 사용자가 Task명을 부르지 않아도 담당자가 '그 일'을 이어가게 한다(사용자 요청 반영).
            try:
                resumed = await self._restore_open_task(flow, proj)
            except Exception:
                resumed = None   # 복원 실패는 흐름 자체를 막지 않는다(스코프 유령화 방지)
            # [회로차단기 해제 — 사람이 방향을 줬을 때만(2026-06-23 S1a)] 수렴 경보로 검증이 멈춘(loop_escalated)
            # Task에 *사람의 새 개입*이 오면 그게 곧 '② 방향 제시'다 — 경보를 풀어 검증을 재개한다. 단 *부팅 복구
            # 자동 이어가기*([부팅 복구/[SYS 마커)는 사람 판정이 아니므로 풀지 않는다(리클레임마다 풀려 다시 밤새
            # 태우는 것 방지 — 경보는 사람이 답할 때까지 영속).
            _unescalated_by_user = False
            if (resumed and flow.current is not None
                    and getattr(flow.current, "loop_escalated", False)
                    and not (user_text or "").lstrip().startswith(("[부팅 복구", "[SYS"))):
                flow.current.loop_escalated = False
                _unescalated_by_user = True
                self._log("loop_escalated_cleared_by_user", project=proj.get("id"),
                          task=getattr(flow.current, "task_id", "?"))
                try:
                    self._checkpoint_open_task(flow)
                except Exception:
                    pass
            resume_note = ""
            if _unescalated_by_user:
                resume_note += ("[수렴 경보 해제 — 사용자가 방향을 제시함] 이전에 교차검증 루프로 '사람 판정 대기'였던 "
                                "이 Task에 방금 사용자 개입이 왔습니다. 경보를 풀었으니 이 지시를 새 방향으로 삼아 "
                                "진행하세요(검증 재개 가능). 같은 루프로 되돌아가지 말고 이 지시에 맞춰 고치세요.\n\n")
            if resumed:
                resume_note = (
                    f"[진행 중이던 Task 복원됨 — '더 진행해'의 대상일 가능성이 큼] 이 프로젝트엔 아직 끝나지 않은 "
                    f"Task가 남아 있어 **상태블록·스레드·담당자(owner)를 그대로 되살렸습니다** — 사용자가 Task명을 "
                    f"일일이 부르지 않아도 '진행 중인 그 일'을 가리키는 것이니, 당신이 판단해 이어가세요:\n"
                    f"  · Task {resumed['task_id']} / Owner: {resumed.get('owner_name') or '(미정)'} / "
                    f"팀: {flow._names(flow.current.team) if flow.current else ''}\n"
                    f"  · Purpose: {resumed.get('purpose') or '(미정)'}\n"
                    f"  · Goal: {resumed.get('goal') or '(미정)'}\n"
                    f"  · 지금까지(직전 보고): {(resumed.get('result_so_far') or '(기록 없음)')[:200]}\n"
                    f"→ 사용자의 요청이 이 Task의 연장이면(대개 그렇습니다) **새 Task를 또 열지 말고 이 Task를 이어서** "
                    f"끝내세요: 남은 부분을 owner에게 request(Work)로 맡기고(이미 정해진 팀·owner 존중 — 가로채 혼자 "
                    f"마무리 금지), run으로 검증한 뒤 complete_task로 **이 블록**을 마감하세요. 만약 사용자가 **명백히 "
                    f"다른 새 작업**을 원한 거면, 이 Task를 먼저 적절히 마무리(complete_task)한 뒤 새 Task를 여세요(당신 판단).\n\n")
                if resumed.get("deep_chain_inflight"):
                    # [복구 인플라이트 보존(2026-06-23, 사용자)] 죽기 전 진행 중이던 깊은 위임을 SYS가 그 워커로
                    # 재개한다 — 리더가 같은 일을 *다른 사람에게 새로 위임*(fresh)으로 덮어써 그 워커의 작업·보고를
                    # 버리지 않게 강하게 못박는다(라이브: 황시윤 응답 없이 리더가 이서연에게 새 request).
                    resume_note += (
                        f"[★인플라이트 보존 — 절대 새로 위임하지 말 것] 죽기 직전 **진행 중이던 위임(→ "
                        f"{resumed.get('deep_chain_inflight')})**이 복원됐고, **SYS가 그 워커를 이어서 재개**합니다. "
                        f"그 워커의 작업·보고가 아직 살아 있으니 — **이 일을 다른 사람에게 새로 request(Work)로 "
                        f"넘기지 마세요**(그러면 그 워커의 진행분과 보고가 버려집니다). 그 워커가 이어서 끝내 보고를 "
                        f"올릴 때까지 **기다렸다가**, 받은 보고로 판정·통합·검증만 하세요. 정말 그 워커가 부적합하다고 "
                        f"판단되면 새로 맡기기 전에 먼저 그 사정을 보고에 남기세요.\n\n")
                if doc_collab_on() and dossier_read(flow, "GOAL.md", task_id=resumed["task_id"]):
                    # [B-11 Phase C — 재개 참조화(ORGANT_DOC_COLLAB=1)] 복구·재개의 근거를 리더 기억·요약
                    # 재작문이 아니라 무절단 원본(Dossier)으로 — 재개는 작업공간 Read 전제 경로라 pull-risk 최소.
                    _dp = resumed.get("dossier_path") or dossier_rel(resumed["task_id"])
                    resume_note += (
                        f"[협의·보고 원본 — Task Dossier] 작업공간 `{_dp}/`에 GOAL.md(목표·수용계약)·"
                        f"MINUTES.md(회의·표결 전문)·REPORTS.md(작업 보고 전문)가 있습니다. 이어가기 전 "
                        f"**GOAL.md와 REPORTS.md를 Read**해 원문 기준으로 판정·통합하세요(기억·요약 재작문 금지).\n\n")
            # [Project.Context 주입 — docs Project.md "Organts는 Context를 숙지한다"] ① 프로젝트 목표
            # (사용자 원문 — 존재 이유)와 ② 직전 흐름의 마감 요약을 리더에게 준다. 목표가 없으면
            # '마지막 미완 Task 마감 = 프로젝트 끝'으로 시야가 좁아진다(라이브 관측: 아트 Task만 닫고
            # 멀티·배포가 남은 프로젝트를 종료 보고). 기록만 되고 읽는 곳이 없던 단절(감사 발견)의 복원.
            purpose_note = ""
            if (proj.get("purpose") or "").strip():
                purpose_note = (
                    f"[프로젝트 목표 — 사용자 원문(이 프로젝트의 존재 이유)] {proj['purpose'].strip()}\n"
                    f"(이번 개입·복원 Task를 마감해도 **이 목표에 남은 부분이 있으면 새 Task로 이어가거나, "
                    f"남은 일을 보고 끝에 명시**하세요 — Task 하나의 마감이 프로젝트의 끝이 아닙니다)\n\n")
            ctx_note = ""
            if (proj.get("summary") or "").strip():
                ctx_note = (f"[프로젝트 최근 맥락 — 직전 흐름의 마감 보고] {proj['summary'].strip()}\n"
                            f"(핵심 결정·방향성 참고용 — 사용자의 이번 요청이 우선합니다)\n\n")
            body = (
                f"[프로젝트 {proj['id']} 개입 — 기존 산출물 수정] 이미 작업공간·산출물이 있습니다. create_project 다시 만들지 마세요.\n"
                f"사용자가 보고한 요청/증상: {user_text}\n\n"
                f"{purpose_note}"
                f"{ctx_note}"
                f"{resume_note}"
                f"[이어지는 작업 — 처음부터 다시 짜지 말 것(중요)] 당신은 이 프로젝트에서 일한 **이전 세션 맥락을 그대로 "
                f"이어갑니다**. 직전에 진행 중이던 Task·목표·위임(누가 누구에게 무엇을 맡겼는지)·owner·팀 구성이 있었다면 "
                f"**그 상태를 이어받아 계속**하세요 — 팀을 처음부터 다시 짜거나 일부만 다시 부르지 말고(이미 정해진 팀·"
                f"owner를 존중), **이미 누군가에게 위임해 둔 일을 당신이 가로채 혼자 검토·마무리하지 마세요**(그 owner가 "
                f"끝내게 하고, 끝내 무응답이면 사용자에게 보고). 기억이 비어 있을 때(예: 환경 재시작으로 맥락 유실)만 "
                f"작업공간을 Read/run으로 확인해 현재 상태를 복원한 뒤 이어가세요.\n\n"
                # [B-17 — 개입 레시피(#32) 사실통지 축소] ①~④ 단계 지시를 삭제 — 백스톱 실재: 개입에서
                # Task 미개설 run·위임 0 run 반복은 permissions #7이, 목표 확정 전 수정은 #5가, 타 도메인
                # 대리구현·독식은 #4·#6·#9가 기계 차단하고 각 거부 메시지가 다음 행동 처방을 동봉한다.
                f"[개입 구조 — 사실] 새 증상·새 요청이면 개입도 Task 구조(create_task→합의·set_goal→owner "
                f"위임→검증·complete)로 갑니다 — Task 없이 run, 목표 확정 전 수정, 위임 없는 단독 반복 run은 "
                f"훅이 차단하며 거부 메시지가 다음 행동을 안내합니다. "
                f"동작·물리·판정 문제는 server.js, 색·레이아웃·그리기 순서만 public/입니다.")
            self._log("intervention", project=proj["id"], text=user_text[:60])
        else:
            # [흐름 격리 — 시작부터 고유 폴더] 신규 흐름이 작업공간 루트에서 시작하면 다른 프로젝트
            # 폴더들이 다 보여 남의 산출물을 뒤지고 이어받는 오염이 생긴다(라이브: 모션 팀이 지진
            # 산출물을 발견·개조). 흐름마다 고유 폴더에서 시작하고, 프로젝트 등록 때 P-번호 이름
            # (p-00n-슬러그)으로 개명한다 — **이름이 아니라 번호가 신원**(사용자 제안).
            try:
                flow.workspace = os.path.join(self.workspace, scope_key)
                os.makedirs(flow.workspace, exist_ok=True)
            except OSError:
                flow.workspace = self.workspace
            # [공급 원칙 — 유사 프로젝트 알림] 같은 요청의 재전송이 리더의 이름 짓기 운(한글/영문)에
            # 따라 '기존 이어가기 vs 신설'로 갈리던 비결정성(라이브: 동일 원문 → P-006 중복 신설).
            # 판단은 리더 몫, 정보는 구조가 — 신설 전에 알아야 할 사실을 결정 지점에 공급한다.
            sim = self._similar_projects(user_text)
            if sim:
                body = (f"[유사 프로젝트 존재 — 참고] {sim}\n"
                        f"단어가 비슷해도 이 요청은 **새 작품으로 등록됩니다**(메인 채널 새 요청 = 신규가 기본). "
                        f"사용자가 위 프로젝트의 연장을 원했다면 원문에 P-번호가 있거나 그 프로젝트 채널에 "
                        f"직접 개입했을 것입니다 — 기존 작품을 임의로 이어받지 마세요(신원·작업공간 하이재킹 금지). "
                        f"겹치는 아이디어는 새 작업공간에서 새로 구현하세요.\n\n") + body
        # [B-09 Phase A — Task Dossier 스캐폴드] 워크스페이스가 정해진 직후(개입=기존, 신규=방금 생성)
        # PLAYBOOK(1회)·craft 미러(직전 수면 증류 반영)를 .collab/에 — 관측 전용, best-effort.
        self._write_dossier_scaffold(flow)
        if root_id is not None:
            flow.start_root(root_id)
        flow.wake = lambda to, b, k: self.run_turn(flow, to, b, k, "member")
        flow.log = self._log                       # 관측: req_sent 등을 flow.jsonl로 영속
        flow.last_activity = time.monotonic()
        # [Rule/Status — 상태 가시화] 흐름 시작과 함께 그 채널에 상태 메시지 1개를 System Bot으로
        # 올리고, 진행 동안 '수정'으로만 조용히 갱신한다(알림 0 — Guide/Discord.md). 시스템이 멈추면
        # 갱신도 멈춰 '마지막 활동'의 정체가 사용자에게 박제 신호가 된다(오늘 동면 관측의 직접 해법).
        # edit 능력이 없는 가이드(테스트 등)에선 통째로 생략 — 갱신 못 하는 거짓 계기판을 안 만든다.
        flow.status_req = (user_text or "").strip()
        status_t0 = time.monotonic()
        status_mid, status_updater = None, None
        status_ch = int(channel_id)
        if getattr(self.guide, "edit_message", None):
            # [시초 계기판 되살리기 — 사용자 설계: "재개는 시초가 살아나게만 하면 된다"] 같은
            # 원요청의 재개(졸업 라우팅: root_id == origin_msg)는 새 상태 메시지를 또 달지 않고
            # **원요청 채널의 시초 상태 메시지를 이어서 갱신**한다. 시작 시각도 시초의 것을
            # 유지 — 재개마다 '작업 중 0분'부터 새로 재고 동면 1회당 계기판이 1개씩 쌓이던
            # 노이즈(라이브 관측) 제거. 시초가 사라졌으면(삭제 등) 새로 단다(폴백).
            # [개입 대시보드 재사용 — 중복 금지, 사용자 설계 "개입은 그대로 남게"] 등록 프로젝트의
            # 흐름(졸업 재개든 프로젝트 채널 평문 개입이든)은 그 프로젝트의 **단일 대시보드**를 잇는다.
            # 종전엔 root_id==origin_msg(졸업 재개)일 때만 재사용해, 새 개입은 매번 새 대시보드를 달았다
            # (라이브 2026-06-13: 동면 후 개입마다 '작업 중 0분' 계기판이 1개씩 누적 — 사용자 지적).
            # 개입(proj 존재)이면 무조건 재사용한다. 신규 흐름(proj None)만 새로 단다.
            o = (proj.get("origin_status") if proj else None) or {}
            if o.get("id"):
                try:
                    t0_resume = time.monotonic() - max(0.0, time.time() - float(o.get("started") or time.time()))
                    await self.guide.edit_message(int(o["channel"]), o["id"],
                                                  self._status_text(flow, t0_resume))
                    status_ch, status_mid, status_t0 = int(o["channel"]), str(o["id"]), t0_resume
                except Exception:
                    status_mid = None
            if status_mid is None:
                try:
                    status_mid = await self.guide.post(channel_id, 0, self._status_text(flow, status_t0))
                    status_ch = int(channel_id)
                    # 개입에 새 대시보드를 달았으면(시초가 없거나 삭제됨) 프로젝트에 기록 → 다음 개입이
                    # 재사용(중복 누적 방지 + 시초 삭제 시 자가 치유). 신규 흐름(proj None)은 _reg가 기록.
                    if proj is not None and status_mid:
                        proj["origin_status"] = {"channel": status_ch, "id": str(status_mid),
                                                 "started": int(time.time() - (time.monotonic() - status_t0))}
                        self._save_projects()
                except Exception:
                    status_mid = None
            if status_mid:
                async def _status_updates():
                    period = int(os.environ.get("ORGANT_STATUS_PERIOD", "60"))
                    while not flow.done:
                        await asyncio.sleep(period)
                        if flow.done:
                            break
                        try:
                            await self.guide.edit_message(status_ch, status_mid,
                                                          self._status_text(flow, status_t0))
                        except Exception:
                            pass               # Discord 순단이 흐름을 건드리지 않게(best-effort)
                status_updater = asyncio.create_task(_status_updates())

        async def _run_leader():
            flow.leader_segment = 1
            acts_seg = flow.act_count          # 세그먼트 실작업 기준점(활동 기반 예산 — 첫 턴 포함)
            # [정밀 복구 재개(2026-06-23, 사용자)] 끊긴 깊은 위임 체인이 복원됐으면, 채팅 재발행/평탄화 없이
            # 가장 깊은 워커부터 재개하고 C→B→A로 unwind(각자 범위 보존)한 뒤 그 통합 결과를 리더에 넘긴다 —
            # 리더는 판정·마감만. 평탄화(리더→C 직접, B 빠짐)로 C/리더가 B 일까지 떠안던 것 교정. 정밀 경로가
            # 처리하면 _auto_continue_owner(평탄화)는 owner_incomplete 해제로 중복 안 돈다. 예외 시 로그만(평탄화
            # *기본* 폴백 아님 — 정밀 재개 내부가 워커 실패/재위임을 부모 통합·drain으로 튼튼히 처리).
            _body = body
            _pf = getattr(flow.current, "precise_chain_frames", None) if flow.current else None
            if _pf:
                flow.current.precise_chain_frames = []
                try:
                    _po = await self._resume_precise_chain(flow, _pf)
                    if flow.current:
                        flow.current.owner_incomplete = False
                    if _po:
                        _body = body + ("\n\n[정밀 복구 — 끊겼던 깊은 위임 체인을 워커들이 *각자 범위에서* 이어 "
                                        "완성한 결과입니다. 당신은 이걸 통합·판정·검증하고 마감만 하세요 — 이미 된 "
                                        "일을 다시 위임하지 마세요]\n" + _po)
                except Exception as _e:
                    self._log("precise_resume_failed", err=str(_e)[:150])
            result = await self.run_turn(flow, lead, _body, Kind.WORK, "leader")
            # 구조적 연속 실행: 턴 한도로 작업이 끊겼으면(진행 중 Task가 남았거나 '턴 한도' 표시)
            # 같은 세션으로 이어서 완료까지 재호출한다 — '턴 한도 = 무조건 中断' 결함 해소.
            cont = 0
            while ((flow.current is not None or "턴 한도 도달" in (result or ""))
                   and cont < self.max_continue and not flow.cancelled):   # [사용자 중지] 이어가기 멈춤
                # [하드블록 종결/자기치유(B-03 G4)] 봇이 못 푸는 인프라 벽(배포 자격증명 등)에 막히면 재시도
                # 루프를 멈춘다 — 가짜 진행(재검증)으로 며칠씩 빙빙 돌다 무진행 컷나던 것 차단. 단 '연속
                # 무응답'형은 자기치유: 백오프 뒤 SYS 프로브 wake 1회 성공 시 해제하고 계속(실패면 종결 유지).
                if getattr(flow, "_hard_blocked", None):
                    if not await self._hard_block_probe(flow, lead):
                        break
                # [단일활성 복원] 리더 턴이 끝났는데 위임이 아직 '완주 중'이면(CLI가 도구 호출을 포기해
                # detach됐거나, 턴 한도로 끊겼지만 deliver 태스크는 살아 있음) — 그 위임을 죽이지 않고
                # **끝까지 기다린다**. 일하는 owner를 드레인으로 자르던 것(작업 유실·재위임 churn·'오유진
                # 2회 호출')의 근본 교정. 완주가 프레임을 닫으므로 대개 베턴도 자연 복귀한다.
                drained = await self._drain_inflight(flow)
                # 그래도 베턴이 굳어 있으면(진짜 고아 프레임) 강제 복구(escalate-drain)한 뒤 이어간다.
                if flow.comm.alive != lead and not flow.comm.done:
                    guard = 0
                    # origin 프레임은 남긴다(스택 1장에서 멈춤) — 이어가기 준비 드레인이 흐름
                    # 자체를 종료(comm.done)시켜 이후 요청이 전부 막히는 것 방지.
                    while (flow.comm.alive != lead and not flow.comm.done
                           and len(flow.comm.open_requests) > 1 and guard < 64):
                        try:
                            flow.comm.escalate("continue 전 베턴 복구(위임 고아 정리)")
                        except CommError:
                            break
                        guard += 1
                    self._log("baton_recover_continue", alive=flow.comm.alive, recovered=(flow.comm.alive == lead))
                # [구조적 이어가기] 미완(턴한도·타임아웃) 위임은 리더 판단에 맡기지 않고 SYS가 직접
                # 같은 owner에게 이어 보낸다 — 리더는 완성본을 받아 '판정'(검증·마감)만 한다.
                drained += await self._auto_continue_owner(flow, lead)
                # [헛돎 발생 차단] 리더가 designated owner에게 위임 0건이고 솔로 독식에만 막혀 헛돌면,
                # SYS가 직접 owner에게 첫 위임을 발사한다(위 _auto_continue_owner의 '위임 0건' 빈틈 메움).
                drained += await self._auto_delegate_owner(flow, lead)
                # [리더 조율 강제 — 구조(2026-06-23, 사용자)] 워커가 막혀 큐에 쌓인 교차도메인 일을 리더가
                # 무시하던 것(라이브 P-030/P-031: owner에만 재위임, 프론트·데이터·AI needs 방치 → 정지)을 SYS가
                # 직접 그 전문가에게 위임해 메운다(프롬프트 의존 제거). 결과는 drained로 리더에 전달(판정만).
                drained += await self._auto_coordinate(flow, lead)
                # [1층 floor seam] 세그먼트 경계 TRP — turn-taking이면 여기서 자기선택 open(기본은 no-op).
                drained += await self._floor_segment_open(flow, lead)
                # [활동 기반 예산 — "작업 중이면 얼마가 걸리든 안 끊는다"(확립 원칙)의 세그먼트 적용]
                # 직전 세그먼트에 실작업(act_count 증가)이나 위임 완주 도착(drained)이 있었으면 예산을
                # 소모하지 않는다 — 예산의 목적은 '무진행 루프 차단'이지 '대형 작업 총량 제한'이 아니다.
                # 라이브 P-010: 동면 재개 5회+재협의 루프가 예산 12를 태워 '진행 중인' 작업이 마감 직전
                # 절단(사용자: "왜 작업 도중에 끊겼지"). 무진행 정체는 종전대로 이 예산+워치독이 잡는다.
                progressed = (flow.act_count > acts_seg) or bool(str(drained).strip())
                if not progressed:
                    cont += 1
                else:
                    cont = 0   # [연속 무진행 한도] 진행이 확인되면 리셋 — 예산은 '정체 감지기'다
                               # (사용자: "예산도 너무 작다" — 숫자가 아니라 의미를 교정: 진행하는 한
                               # 무제한, 연속 12회 헛돌 때만 정체로 종결).
                acts_seg = flow.act_count
                flow.leader_segment += 1
                self._log("continue_incomplete",
                          task=(flow.current.task_id if flow.current else None), attempt=cont,
                          seg=flow.leader_segment, progressed=progressed)
                # [기억 구멍 무력화] 이어가기마다 팀·소유의 '시스템 사실'을 재주입한다 — 외부 절단
                # (SIGTERM)으로 직전 턴이 세션에 안 남으면 리더가 자기 팀 구성을 잊고 '참여 중인가요?'
                # 재확인·팀 밖 호출을 반복했다(라이브 관측). 기억은 흔들려도 사실은 SYS가 들고 있다.
                team_note = ""
                if flow.current is not None:
                    try:
                        team_note = (
                            f"[시스템 기록 — 현재 Task {flow.current.task_id}] "
                            f"팀: {flow._names(flow.current.team)} / Owner: {flow.current.status.owner or '미정'} / "
                            f"Goal: {(flow.current.status.goal or '미확정')[:80]}\n"
                            f"[프로젝트 팀 전체] {flow._names(flow.project_team)} — 이 명단 밖 동료는 이 프로젝트 "
                            f"구성원이 아닙니다(필요하면 recruit로 합류부터).\n\n")
                    except Exception:
                        team_note = ""   # 사실 주입은 best-effort — 형식이 다른 Task여도 이어가기는 진행
                # [리더 조율 강제(2026-06-23, 사용자)] 게이트가 막아 리더에게 올린 교차도메인 조율 큐를
                # 'SYS가 확인한 사실'로 주입한다 — 워커가 막혔다고 보고한 걸 리더가 '핑계'로 묵살하고 같은
                # 워커에게 일만 재발사하던 루프(라이브 P-030 backend2↔PM 핑퐁)를 끊는다. 리더가 *직접* 해당
                # 도메인 전문가에게 위임하게 한다(이게 리더의 조율 책임 — '단순 분배'가 아니라).
                # (조율 큐는 위 _auto_coordinate가 SYS 명의로 *직접 위임*해 처리·소비함 — 그 결과가 drained에
                #  담겨 리더에게 전달되니, 여기선 프롬프트 주입[리더가 무시하던 coord_note]을 제거한다. 리더는
                #  SYS가 배정한 그 교차도메인 결과를 통합·판정만 한다. 프롬프트→구조 전환의 핵심 지점.)
                result = await self.run_turn(flow, lead, _CONTINUE_BODY + team_note + drained,
                                             Kind.WORK, "leader")
            # 이어가기 한도 소진/마감 후에도 완주 중인 위임이 있으면 그 결과까지 받아 보고에 붙인다
            # (작업 유실 방지 — 마지막 위임이 마감 직전에 끝나는 경우).
            drained = await self._drain_inflight(flow)
            if drained:
                result = (result or "") + drained
            return result

        leader_task = asyncio.create_task(_run_leader())
        flow._run_task = leader_task   # [사용자 작업 중지] request_cancel이 즉시 인터럽트할 핸들
        try:
            # 무진행(행) 워치독: idle_timeout 동안 진행이 0이면 리더 턴 취소(리더-행 구멍 메움). 진행 중이면 무제한.
            result = await self._await_with_idle_watchdog(leader_task, flow)
        except asyncio.CancelledError:
            for t in list(getattr(flow, "inflight_tasks", ())):   # 흐름 중단 시 완주 태스크도 정리(누수 방지)
                if not t.done():
                    t.cancel()
            if getattr(flow, "cancelled", False):   # [사용자 작업 중지] — 무진행 타임아웃과 구분
                result = ("(사용자가 작업을 중지했습니다 — 진행 중이던 흐름을 멈췄습니다. 지금까지 산출물은 "
                          "작업공간에 남아 있고, 미완 작업은 다음에 이어갈 수 있습니다.)")
                self._log("flow_user_stopped", project=flow.project_channel is not None)
            else:
                result = (f"(흐름 자동 중단: 약 {self.idle_timeout // 60}분간 아무 진행(요청·파일작성·실행)이 없어 '행'으로 "
                          f"판단했습니다 — 리더/동료 서브프로세스가 멈춘 듯합니다(환경 불안정). 지금까지 산출물은 작업공간에 "
                          f"남아 있습니다. 다시 시도하거나 반복되면 잠시 뒤 재요청하세요.)")
                self._log("flow_idle_aborted")
        except Exception as e:                     # 리더가 죽어도 흐름은 닫고 보고한다
            result = f"(리더 처리 중 오류: {e})"
        # 배포 강제: 배포 가능한 산출물인데 deploy를 안 불렀으면 리더에게 '배포만' 한 번 더(누락 방지).
        # 여기부터 마감 꼬리는 어떤 실패에도 끊기면 안 된다 — 끊기면 스코프·전역 점유가 유령으로
        # 남아 그 프로젝트(와 그 리더의 다른 프로젝트)가 영영 큐에 갇힌다(병렬에서 반경 확대).
        try:
            result = await self._ensure_deploy(flow, lead, result)
        except Exception as e:
            self._log("ensure_deploy_failed", err=str(e)[:80])
        # 리더의 반환값 = 사용자에게 가는 Response(=보고). origin 프레임을 닫아 시작점 복귀.
        try:
            await self.guide.post(flow.user_channel, lead, format_response(result),
                                  reply_to=flow.root_id)
        except Exception as e:
            self._log("final_post_failed", err=str(e)[:80])
        self._close_flow(flow, lead, result)
        flow.done, flow.final = True, result
        # [Rule/Status] 종결 확정 — 마지막 수정으로 ✅/⏸를 박고 갱신을 멈춘다(이후 불변).
        if status_updater is not None:
            status_updater.cancel()
        if status_mid is not None:
            try:
                mark = "⏸ 중단(미완 Task 이어가기 가능)" if flow.current is not None else "✅ 완료"
                await self.guide.edit_message(status_ch, status_mid,
                                              self._status_text(flow, status_t0, final=mark))
            except Exception:
                pass
        # 안전망: 리더가 complete_task로 명시적으로 닫지 않은 현재 Task는 '중단'으로 표시한다
        # (허위 완료 금지 — owner가 실제로 안 끝냈을 수 있으므로 '완료'로 둔갑시키지 않음).
        # 동시에, 그 미완 Task를 프로젝트 레지스트리에 스냅샷으로 남겨 '다음 개입'에서 같은 Task로
        # 되살릴 수 있게 한다(사용자가 Task명 안 불러도 '더 진행해'가 그 Task를 잇게 — 근본 구조).
        open_task_snap = None
        if flow.current is not None:
            flow.current.status.status = "중단"
            flow.current.status.result = (result or "")[:500]
            try:
                await flow.refresh(flow.current)   # Discord 실패가 마감 꼬리를 끊지 않게(유령 스코프 방지)
            except Exception:
                pass
            open_task_snap = self._task_snapshot(flow, flow.current)
            flow.current = None
        # 프로젝트 요약 + 미완 Task 영속 갱신(다음 개입 때 맥락·이어가기 대상으로 제공).
        # current가 None(=complete_task로 마감했거나 Task 자체가 없었음)이면 open_task를 비운다(완료 처리).
        if flow.project_channel:
            p = self.projects.get(int(flow.project_channel))
            if p:
                p["summary"] = (result or "")[:600]   # Project.Context — 개입 프롬프트에 주입됨
                p["open_task"] = open_task_snap
                self._save_projects()
        # 신규 흐름이 프로젝트를 등록했으면 세션을 프로젝트 스코프로 '승격'(리네임) — 다음 개입이
        # 이 흐름의 기억을 그대로 잇는다(흐름 도중엔 스코프 고정이라 회의 기억도 안 끊김).
        if flow.project_id and session_scope != flow.project_id and self.session_dir:
            for fp in glob.glob(os.path.join(str(self.session_dir),
                                             f"organt_state_{session_scope}_*.json")):
                try:
                    os.replace(fp, fp.replace(f"_{session_scope}_", f"_{flow.project_id}_"))
                except OSError:
                    pass
        self._log("flow_done", project=flow.project_channel is not None,
                  tasks=len(flow.tasks), comm_done=flow.comm.done)
        self.active_flows.pop(scope_key, None)
        # [전역 점유 해제 안전망] 이 흐름의 모든 점유를 일괄 해제 — 정상 경로는 respond/escalate가
        # 대칭으로 풀지만, 예외·강제 종료로 남은 점유가 있어도 여기서 회사 풀로 돌려보낸다.
        self.engaged.release_scope(scope_key)
        # [임시 폴더 위생] 프로젝트로 승격되지 못한 흐름 폴더(new-…)가 비어 있으면 정리 — 루트에
        # 빈 껍데기가 쌓이지 않게(산출물이 있으면 보존: 사용자가 살펴볼 수 있게 남긴다).
        if not flow.project_id:
            try:
                ws = str(flow.workspace or "")
                if os.path.basename(ws.rstrip("/")).startswith("new-") and not os.listdir(ws):
                    os.rmdir(ws)
            except OSError:
                pass
        # 큐 드레인: 지금 시작 가능한(스코프 비충돌·리더 가용) 첫 명령을 이어서 처리.
        item = self._pop_runnable_queued()
        if item is not None:
            return await self.handle_user_input(*item)
        return {"mode": "flow", "flow": flow}

    def _pop_runnable_queued(self):
        """큐에서 '지금 시작 가능한' 첫 항목을 꺼낸다(없으면 None) — 시작 가능 = 스코프 비충돌 +
        운영 상한 여유 + 그 흐름을 이끌 봇이 타 흐름에 점유돼 있지 않음(게이트와 같은 판정).
        흐름 종료와 수면 증류 종료(점유 해제 시점들)가 공용으로 쓴다."""
        live = {k for k, f in self.active_flows.items() if not f.done}
        for i, item in enumerate(list(self.queue)):
            ch = int(item[0])
            p = self.projects.get(ch)
            k = p["id"] if p else "main"
            lead_q = (item[1] if (item[1] and item[1] in self.bot_info)
                      else (self._valid_leader(p) if p else item[1]))
            if (k not in live
                    and (self.max_flows <= 0 or len(live) < self.max_flows)
                    and not self.engaged.busy_elsewhere(lead_q, k)):
                self.queue.pop(i)
                self._save_projects()   # [큐 영속] 소비 즉시 디스크 — 재시작 시 같은 요청 중복 처리 방지
                return item
        return None

    def request_cancel(self, channel_id) -> bool:
        """사용자 '작업 중지' — 해당 채널의 활성 흐름을 협조적으로 취소한다(매체/러너가 사용자 트리거로 호출).
        진행 중인 리더 턴을 즉시 인터럽트(task.cancel→CancelledError, 깨끗한 중단 경로 재사용)하고
        이어가기 루프를 멈춘다. 활성 흐름이 없으면 False. 단일 이벤트루프 가정(러너가 같은 루프에서 호출)."""
        cid = int(channel_id)
        for f in list(self.active_flows.values()):
            if getattr(f, "user_channel", None) == cid and not f.done:
                f.cancelled = True
                t = getattr(f, "_run_task", None)
                if t is not None and not t.done():
                    t.cancel()
                self._log("cancel_requested", channel=cid)
                return True
        return False

    def deliver_human_info(self, channel_id, target_id, text) -> bool:
        """사람의 '진행 중 개입(정보 전달)' — 활성 흐름의 대상 봇 *다음 턴 프롬프트*에 노트로 주입한다.
        큐로 미루지 않고(개입의 핵심) 흐름에 부착만 — baton 프레임/wake를 만들지 않으므로 single-alive·게이트 불변
        (게이트는 도구호출만 보고 프롬프트 텍스트는 안 봄). 대상이 흐름 팀에 없으면 리더로. 리더는 항상 인지한다
        (라우터·사용자 응답 주체). 매체/러너가 같은 이벤트루프에서 호출. 활성 흐름 없으면 False(매체가 폴백 큐잉)."""
        text = (str(text or "")).strip()
        if not text:
            return False
        cid = int(channel_id)
        for f in list(self.active_flows.values()):
            if getattr(f, "user_channel", None) == cid and not f.done:
                lead = f.leader
                bi = f.bot_info or {}
                tgt = int(target_id) if (target_id and int(target_id) in bi) else lead
                if getattr(f, "pending_info", None) is None:
                    f.pending_info = {}
                f.pending_info.setdefault(tgt, []).append(text)            # 대상 봇이 다음 턴에 직접 본다
                if tgt != lead:                                            # 리더는 '누구에게 갔는지' 인지(라우터·응답)
                    f.pending_info.setdefault(lead, []).append(f"[{bi.get(tgt, tgt)}에게 전달됨] {text}")
                # [G4 해제 — 사용자 개입(B-03)] '연속 무응답' 하드블록은 사람의 진행 중 개입도 해제 트리거다
                # (loop_escalated의 사용자 해제 패턴과 동형 — 사람이 온 것 자체가 '판정·방향'의 신호).
                try:
                    from .rule.communication import HARD_BLOCK_TRANSIENT
                    if str(getattr(f, "_hard_blocked", "") or "").startswith(HARD_BLOCK_TRANSIENT):
                        f._hard_blocked = None
                        f.consec_fail = 0
                        self._log("hard_block_cleared", by="user", channel=cid)
                except Exception:
                    pass
                try:
                    f.last_activity = time.monotonic()                     # 무진행 워치독이 안 끊게
                except Exception:
                    pass
                self._log("human_info_delivered", channel=cid, target=tgt)
                return True
        return False

    def _flow_idle(self, channel_id):
        """이 채널 활성 흐름의 '무진행 시간'(초) — 봇 활동(last_activity)이 멈춘 지. 흐름 없으면 None.
        SYS.run이 정체 기준 슬롯 회수(컷·재개)를 판단하는 신호."""
        for f in list(self.active_flows.values()):
            if getattr(f, "user_channel", None) == int(channel_id) and not getattr(f, "done", False):
                la = getattr(f, "last_activity", None)
                return None if la is None else max(0.0, time.monotonic() - la)
        return None

    async def run(self, guide, leader, cap=4, poll=3.0, stall_timeout=900, max_age=7200, once=False):
        """[매체 무관 실행 루프] Guide 배달계약(get_pending·pick·heartbeat·all_stops·check_interject·
        check_stop·set_origin)으로 요청을 받아 동시 흐름으로 처리한다. 하트비트·정체컷·재개는 SYS가
        *결정*하고, 매체 실행은 guide가 *구현*한다(추상↔구현). 러너/리스너는 Sys(guide,builder) 만들고
        이 run()만 부르면 됨 — 진입이 얇아진다(폴링·pick 로직은 여기·Guide로 이관)."""
        import traceback
        inflight, seen, cut_resumes, last_beat = {}, set(), {}, 0.0
        log.info("요청 폴링 시작(동시 처리 — 상한 %d)", cap)
        # [수면 — 기억 증류 라이브화] 자기증류(경험→직무·개인 기준 압축)를 브레인 실행 루프에 배선한다.
        # 종전엔 Discord 진입에만 있어 라이브(murmur) 러너에선 안 돌던 것을 매체중립 위치(Sys.run)로 —
        # 유휴 봇만·주기당 1건(비용 제어). once(부트스트랩)면 미기동, period<=0이면 비활성(env로 조정).
        # 프로세스 수명과 함께 사는 데몬 태스크(discord_main의 background gather 관례와 동형).
        _sleep_task = None
        _sp = int(os.environ.get("ORGANT_SLEEP_PERIOD", "600"))
        if not once and _sp > 0 and self.session_dir:
            _sleep_task = asyncio.create_task(self._sleep_loop(_sp))
        while True:
            try:
                _now = asyncio.get_event_loop().time()
                # ── 하트비트 + 진행(picked_ts) 갱신(8초 throttle) ──
                if _now - last_beat > 8:
                    try:
                        await guide.heartbeat()
                        for _mid in list(inflight):
                            _idle = self._flow_idle(inflight[_mid]["ch"])
                            await guide.pick(_mid, touch=True, idle=int(_idle) if _idle is not None else 0)
                    except Exception:
                        pass
                    last_beat = _now
                # ── 완료 reap + 정체컷·재개(무진행 기준 슬롯 회수) ──
                for _mid, _info in list(inflight.items()):
                    if _info["task"].done():
                        del inflight[_mid]
                        try:
                            _info["task"].result()
                            await guide.pick(_mid, done=True)
                            log.info("✓ 처리 완료: msg_id=%s", _mid)
                        except asyncio.CancelledError:
                            log.info("■ 중지됨: msg_id=%s", _mid)
                        except Exception as _e:
                            log.error("✗ 처리 실패 msg_id=%s: %s\n%s", _mid, _e, traceback.format_exc())
                    else:
                        idle = self._flow_idle(_info["ch"])
                        stalled = idle is not None and idle > stall_timeout
                        too_old = _now - _info.get("t0", _now) > max_age
                        if (stalled or too_old) and not _info.get("cut"):
                            try:
                                _info["cut"] = True
                                _info["task"].cancel()
                                _why = f"무진행 {int(idle)}s" if stalled else f"최대수명 {max_age}s"
                                _n = cut_resumes.get(_mid, 0) + 1
                                cut_resumes[_mid] = _n
                                if _n <= 5:
                                    seen.discard(_mid)
                                    await guide.pick(_mid, unpick=True)
                                    log.info("⏱ %s — 체크포인트 후 재개 예약(%d/5): msg=%s ch=%s", _why, _n, _mid, _info["ch"])
                                else:
                                    log.info("⏱ %s — 재개 상한 도달(%d회), 중단: msg=%s ch=%s", _why, _n - 1, _mid, _info["ch"])
                            except Exception:
                                pass
                # ── 작업 중지(전역 스캔 — 도는 흐름 취소 + 픽 요청 종결) ──
                try:
                    for _sch in await guide.all_stops():
                        cancelled = self.request_cancel(_sch)
                        await guide.mark_stopped(_sch)
                        log.info("■ 작업 중지 — ch=%s(흐름취소=%s)", _sch, cancelled)
                except Exception:
                    pass
                # ── 진행 중 흐름 개입 폴 ──
                for _mid, _info in list(inflight.items()):
                    _ch = _info["ch"]
                    try:
                        for _x in await guide.check_interject(_ch):
                            ok = self.deliver_human_info(_ch, _x.get("target_id"), _x.get("text"))
                            log.info("✎ 사람 개입 %s — ch=%s", "주입" if ok else "미주입(흐름없음)", _ch)
                    except Exception:
                        pass
                # ── 빈 봇 요청 픽 → 동시 흐름(상한까지) ──
                pend = [p for p in await guide.get_pending() if p["msg_id"] not in seen]
                busy_ch = {_i["ch"] for _i in inflight.values()}
                busy_lead = set()
                for m in pend:
                    if len(inflight) >= cap:
                        break
                    mid = m["msg_id"]
                    to_id = int(m["to_id"]) if m["to_id"] else (int(m["route_to"]) if m.get("route_to") else leader)
                    ch = int(m["channel_id"])
                    if ch in busy_ch or to_id in busy_lead or self.engaged.holder(to_id) is not None:
                        continue
                    seen.add(mid)
                    kind = Kind.WORK if (m["kind"] or "W") == "W" else Kind.INFO
                    req = Request(to_id=to_id, kind=kind, body=m["body"], from_id=0, message_id=str(mid))
                    try:
                        await guide.check_stop(ch)
                    except Exception:
                        pass
                    if not await guide.pick(mid):
                        seen.discard(mid)
                        continue
                    guide.set_origin(ch)
                    inflight[mid] = {"task": asyncio.create_task(self.route_channel_request(ch, req)), "ch": ch, "t0": _now}
                    busy_ch.add(ch)
                    busy_lead.add(to_id)
                    log.info("▶ 요청 처리(동시 %d/%d): ch=%s to=%s kind=%s body=%r", len(inflight), cap, ch, to_id, m["kind"], m["body"][:42])
                if once and not inflight and not pend:
                    log.info("[--once] 대기·진행 요청 없음 — 종료.")
                    return
                await asyncio.sleep(2)
            except KeyboardInterrupt:
                log.info("종료 신호 — 폴링 중단.")
                return
            except Exception as e:
                log.error("폴링 루프 오류: %s\n%s", e, traceback.format_exc())
            await asyncio.sleep(poll)

    async def route_channel_request(self, channel_id, request: Request, root_id=None) -> dict:
        if request.to_id is None:
            self._log("ignored", reason="To 없음")
            return {"mode": "ignored"}
        return await self.handle_user_input(channel_id, request.to_id, request.body,
                                            root_id=request.message_id,
                                            attachments=getattr(request, "attachments", None))
