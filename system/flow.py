"""[Core] Flow — 한 협업 흐름의 공유 상태(팀·Task·베턴·작업공간). SYS·도구·Rule이 공유(rule/는 duck-typed).
도구 인터페이스(guide_tools)에서 상태를 분리 — guide_tools는 @tool 래퍼·서버 조립만."""
import time
from typing import List, Optional

from .protocol import Kind
from .tool_names import ORIGIN
from .rule.communication import CommunicationManager
from .rule.task import TaskRef


class Flow:
    """하나의 활성 흐름(단일흐름 보존). 풀→프로젝트 팀→Task 팀으로 인력을 구조적으로 배정."""

    def __init__(self, guide, channel_id, guild_id, leader_id, bot_info=None):
        self.guide = guide
        self.user_channel = channel_id
        self.guild_id = guild_id
        self.leader = leader_id
        self.bot_info = bot_info or {}
        # [변경주입 상태] 동료 로스터는 바뀔 때만 재주입(이벤트 기반) — 안 바뀌면 대화 기억에 있음.
        self.seen_roster = {}   # {bot: 마지막으로 본 동료 로스터 문자열}
        self.comm = CommunicationManager(ORIGIN)
        self.pool = list((bot_info or {}).keys()) or [leader_id]   # 채용 가능 전체(로스터)
        if leader_id not in self.pool:
            self.pool.insert(0, leader_id)
        # 기본 프로젝트 팀 = 직군 보유자(예비 제외) — 예비는 '채용 대기'라 기본 팀에 안 넣는다(recruit로만 합류).
        # 리더는 예비여도 포함. 담당자가 create_project/create_task로 더 좁히거나 recruit로 직군을 채운다.
        self.project_team: List[int] = [m for m in self.pool if m == leader_id
                                        or not str((bot_info or {}).get(m, "")).startswith("예비")]
        self.project_channel: Optional[int] = None
        self.tasks: List[TaskRef] = []
        self.current: Optional[TaskRef] = None
        self._base = time.strftime("%H%M%S")
        self._n = 0
        self.done = False
        self.cancelled = False   # [사용자 작업 중지] 매체가 set하면 워치독·이어가기 루프가 협조적으로 중단
        self._run_task = None     #   이 흐름의 리더 태스크(SYS가 주입) — 진행 턴을 즉시 인터럽트하는 핸들
        self.final: Optional[str] = None
        self.root_id: Optional[str] = None
        self.advice = []
        self.workspace = None   # run 툴 cwd(작업공간 경로). SYS가 주입.
        self.wake = None   # async (to_id, body, kind) -> result text  (SYS가 주입)
        self.register_project = None   # (channel_id, name) -> project_id (SYS 주입)
        self.project_id = None         # [Project-XXXX] 식별번호
        self.intervention = None       # 기존 프로젝트 개입이면 그 정보(dict)
        self.origin_request = ""       # 이 흐름의 '사용자 원문 요청'(SYS 주입) — 모든 프롬프트에 '진짜 의도'로 주입.
                                       #   반드시 흐름별 격리: 전역 단일 필드면 동시 흐름이 서로의 원문을 덮어써
                                       #   한 프로젝트의 봇이 '남의 프로젝트 원문'을 진짜 의도로 받아 엉뚱한 걸
                                       #   만든다(라이브: 웹 흐름이 게임 개입 원문을 받아 게임을 짓기 시작).
        self.deployed = None           # deploy 툴이 불리면 결과 문자열(배포 강제용 추적)
        self._deploy_count = 0         # [런어웨이 차단] 흐름당 실배포 횟수 — 상한 넘으면 차단+사용자 보고로
                                       #   에스컬레이트(라이브 P-028: 깨진 배포를 코드 바꿔가며 23회 재배포한 루프 방지)
        self.pending_clarify = None    # 위임자에게 되묻기(확인요청 반환) 임시 보관
        self.pending_coordination = [] # [리더 조율 강제(2026-06-23)] 게이트가 막은 비-리더 교차도메인 Work를
                                       #   리더 다음 턴에 'SYS 확인 사실'로 주입할 큐 — 워커가 핑계로 보고하고
                                       #   리더가 묵살·재발사하던 루프(P-030 backend2↔PM 핑퐁) 차단. 리더가
                                       #   직접 그 도메인 전문가에게 위임하게 한다(sys_core continue 루프에서 소비).
        self.pending_info = {}         # [사람 중간 개입] {봇id: [텍스트]} — 흐름 진행 중 사람이 넘긴 정보를
                                       #   그 봇 *다음 턴 프롬프트*에 주입(매체가 deliver_human_info로 적재). 흐름격리
                                       #   필수(origin_request처럼 전역이면 동시 흐름 교차오염). _prompt가 읽고
                                       #   run_turn이 소비-clear. baton 프레임 아님(게이트#3 무관) — 순수 프롬프트 노트.
        self.leader_segment = 0        # 리더 턴 세그먼트 번호(시작=1, continue마다 +1) — 관측용
        self.req_results = {}          # (seg,from,to,kind,body)->응답: 같은 턴 병렬 중복요청 합치기용 캐시
        self.act_count = 0             # 작업공간 변경(run/Write/Edit) 누계 — 훅이 +1. '위임 도중 owner가 실제로
                                       #   일했나'를 wake 전후 스냅샷 차이로 판정(허위완료/독점 차단)
        self.act_by = {}               # 행위자별 작업 누계(actor→count) — 요청자 자신의 활동을 빼고 재기 위함
        self._stall_victim = None      # [막힘 흡수 차단] 하위 담당이 막혀 베턴이 위임자에게 되돌아온 순간, 막힌 사람 id를
        self._stall_victim_acts = 0    #   기록. 위임자가 '내가 하지'로 그 사람 일을 흡수하는 걸 게이트가 막고 '같은 사람 재요청'을
        self._stall_blocks = 0         #   유도(재채용 X). 막힌 사람이 다시 act하면 해제, 끝내 무응답이면 N회 후 폴백(교착 방지).
        self._gate_pass = set()        # [per-Task 게이트(2026-06-20 전수검사)] 통과한 (게이트명, task_id) 집합 —
                                       #   percept·acceptance·data_prov를 *흐름당 1회*(과의존)가 아니라 *산출물(Task)별*로
                                       #   강제한다(다중-Task서 첫 Task만 검사하던 구멍 차단). bool 플래그(X_checked)는
                                       #   *테스트 우회*로만 남긴다(프로덕션은 이 집합 + task_id로 판정 → 우회와 분리).
        self.cap_evidence = {}         # [B-21 capability ledger] 행위자→{능력: 증거 수}. audit PostToolUse가
                                       #   확장자 범주·run 도메인·배포 이력에서 파생 수집(관측만, writes_by_role
                                       #   관례의 능력판). 영속 적립은 complete_task의 _ledger_accrue가 'owner
                                       #   정당 수임(owner_delivered)+교차검증 통과' Task의 owner 몫만 — 흡수형
                                       #   (비owner) 저작은 Task 경계에서 폐기돼 적립 0(능력 세탁 차단, 부록 A-5).
        self.persist_capability = None  # (mid, {능력:수}) -> None: 장부 영속(SYS 주입 — role_profiles.json capability_ledger 키)
        self.capability_ledger = None  # [B-21 용도②] SYS 장부 읽기 참조 — _free_alternatives 후보 나열용(판정 아님)
        self.writes_by_role = {}       # [메커니즘② 저작 다양성] 직군별 파일 저작(Write/Edit, run 제외) 누계. 완료 시
                                       #   '한 직군이 산출물을 독점'(P-017: 백엔드 혼자 20중 19, 단일 app.js)을 출구
                                       #   게이트가 잡는다 — '분리 모듈은 분리 전문가가 있을 때만 존재'(라이브 규명).
        self.tentative_roles = {}      # [일로 직업 획득 — 영속 이연] 예비→직군 채용은 *잠정*(런타임 bot_info만). 영속
                                       #   (jobs.json+Discord)은 그 봇이 *첫 실작업*을 한 순간에만 — '직업=기억'을 문자
                                       #   그대로. 일 안 하면 영속 안 돼 다음 흐름에 예비로 사라짐 → '0-기억 recruit
                                       #   직군'이 구조적으로 불가(양산 래칫의 근본 차단). mid→직군명.
        self.role_earned_queue = []    # 첫 실작업으로 '획득'된 직군의 Discord 역할 부여 대기열(비동기) — SYS가 턴에서 드레인.
        self.consec_fail = 0           # 연속 '응답 실패(무응답/타임아웃)' 횟수 — 시스템 일시불안정 판별(충원 루프 차단)
        self.inflight_tasks = set()    # 진행 중 위임의 '완주 태스크'들 — CLI가 도구 호출을 포기해도 위임은
                                       #   계속 완주하며(중첩 가능), SYS가 이어가기 전에 이들의 완주를 기다린다
        self.detached_results = []     # 포기당한(detached) 위임의 완주 결과 — 이어가기 리더에게 전달
        self.handoff_inflight = {}     # [논블로킹 핸드오프] 요청자 id→그가 만든 인플라이트 위임. 중첩 위임을
                                       #   SYS가 호출 밖에서 직렬 완주시키고(블록킹 도구호출 없음 → CLI 75초
                                       #   포기·detach·비동기 churn 차단), 요청자가 활성일 때만 1건(베턴=단일).
        self.write_lease = {}          # 행위자→샌드박스(쓰기 리스, 휴면 인프라): 훅이 리스 밖 Write/Edit 거부
        # [소유-기반 도메인 경계(2026-06-23, 사용자) — 분류 아닌 *기록*] 파일 절대경로→생성 직군. 봇이
        # 새 파일을 만들면 그 직군이 owner. 타 직군이 그 파일을 Edit하려 하면 PreToolUse 훅이 막고
        # '보고/요청'으로 돌린다(키워드 분류 폐기 — 무한 하드코딩 종결). 프로젝트 단위로 projects.json에
        # 영속(복구 때 리셋 안 되게 — act_by·_gate_pass의 인메모리 결함 반복 차단). persist_owner는 SYS 주입.
        self.file_owner = {}           # realpath(str) → 직군(normalized). PostToolUse가 기록, PreToolUse가 강제.
        self.persist_owner = None      # () -> None: file_owner를 proj에 써 영속(SYS가 주입)
        self.fork_kind = {}            # [fork 수집] 행위자→Kind: 프레임 없는 가지에도 선구현 게이트 적용
        self.fork_active = 0           # [fork 동시성 가드] 수집 진행 수 — 수집 중 신규 요청/수집은 [대기]
        self.report_stash = {}         # [B-14 — report 도구(스태시형)] mid→구조화 보고 필드(result/changes/
                                       #   verify/risks/offdomain_role/experience/craft_standard). Response는
                                       #   여전히 턴 반환값 — 스태시는 기계 소비용(offdomain 인자>regex 우선,
                                       #   경험·직무기준 흡수, REPORTS.md 동봉). _deliver·run_turn이 소비-pop.
        self.vote_stash = {}           # [B-15 — cast_vote 도구] fork 가지 mid→{option,reason}. vote 수합이
                                       #   인자 우선 소비([표] regex 폴백 존치) — 무효표 소멸. 수합 시 pop.
        self.collab_pushed = set()     # [B-10 Phase B — Task Dossier dedup] 이 흐름(세션)에서 collab_notes
                                       #   *전문*을 이미 받은 멤버 id — 같은 세션 재주입(Redo·이어가기)부터
                                       #   다이제스트+MINUTES.md 참조로 dedup(ORGANT_DOC_COLLAB=1일 때만
                                       #   소비 — off면 종전 전문 재동봉 그대로). 재시작 시 리셋=전문 재push
                                       #   (안전한 방향의 과잉push — 스냅샷 영속 불필요).
        self.log = None                # (event, **fields) 콜백 — SYS가 주입(flow.jsonl 영속)
        # ── 계약 완결(M6, 2026-07-03): sys_core·rule/이 주입하던 유령속성을 여기 선언 ──
        #   전엔 선언 없이 5파일에 흩어져 할당돼, flow 계약을 알려면 크로스체크가 필요했다.
        #   기본값은 각 소비처의 getattr 기본값과 일치(동작 불변). 리스크 있는 2개는 유령 유지:
        #   last_activity(None/monotonic 충돌)·inbound_files([]/None 충돌)는 소비처가 자체 getattr.
        self._handoff = False          # 핸드오프 진행 플래그
        self._sys_dispatch = False     # SYS 디스패치 경로 표식
        self._hard_blocked = ""        # 하드블록 사유(빈=미차단)
        self._consec_fail_t0 = 0.0     # 연속실패 윈도 시작 ts
        self._deploy_block_n = 0       # 배포 차단 카운트
        self._deploy_writes = -1       # 마지막 배포 시점 쓰기 누계(-1=미배포)
        self._deployed_once = False    # 1회 이상 배포됨
        self.deploy_capped = False     # 배포 상한 도달
        self.deploy_inflight = False   # 배포 진행 중
        self.busy_consensus_held = False  # 점유 합의 보류
        self.leader_runs = 0           # 리더 run 누계(흡수 판정)
        self.channel_situation = ""    # 채널 상황 요약(SYS 주입)
        self.checkpoint_task = None    # 체크포인트 대상 Task
        self.craft_of = None           # craft 문서 소유 매핑
        self.inbound_attachments = None  # 인바운드 첨부(SYS 주입)
        self.stage_inbound = None      # 스테이징된 인바운드
        self.status_req = ""           # 상태 요청 텍스트
        self.session_scope = None      # 세션 스코프(재개 컨텍스트)
        self.user_feedback = None      # 사용자 피드백(주입)
        self.persist_role = None       # 역할 영속 콜백(SYS 주입)
        self.project_name = None       # 프로젝트 이름
        self.projects_provider = None  # 프로젝트 목록 provider(SYS 주입)
        self.floor_mode = None         # [1층 floor seam] 대화 구조 정책명 덮어쓰기(None=env ORGANT_FLOOR —
                                       #   rule/floor.floor_mode가 해석; 회의·세그먼트 경계의 발언권 배분 정책)

    def start_root(self, root_id):
        self.root_id = str(root_id)
        self.comm.request(ORIGIN, self.leader, root_id, Kind.WORK)

    def next_task_id(self) -> str:
        self._n += 1
        return f"{self._base}-{self._n}"

    async def refresh(self, task: Optional[TaskRef] = None):
        t = task or self.current
        if t and self.project_channel and t.block_id:
            await self.guide.update_status(self.project_channel, t.block_id, t.status)

    def _info(self, oid):
        return self.bot_info.get(oid, "")

    def _names(self, ids):
        return [self._info(i) or str(i) for i in ids]
