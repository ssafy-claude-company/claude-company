"""[Core] Organt 빌더 — role에 맞는 도구·권한·훅·State를 갖춘 Organt를 만드는 빌더를 돌려준다.

매체-중립(Discord·SNS 무관): Discord 진입(main)과 SNS 러너(run_organt_sns)가 *공유*한다.
종전엔 이 빌더가 Discord 진입 모듈(main.py)에 있어, SNS 러너가 `from src.main import _make_builder`로
가져오며 discord.py를 *transitively* 끌어오던 계층 누수가 있었다 — 빌더를 Core로 옮겨 그 결합을 끊는다.
(2026-07 계층 분리: Core / Discord매체 / SNS매체 대칭화)
"""
import os
import time

from claude_agent_sdk import HookMatcher

from system.audit import AuditLog, make_post_tool_use_hook
from system.config import Config
from system.tool_names import FLOW_TOOLS, LEADER_TOOLS
from system.protocol import Marker
from .organt import Organt, build_options, load_persona, pinned_cwd

_USAGE_BG = set()   # [GC 방어] fire-and-forget 사용량 보고 태스크 참조 보존
from system.permissions import make_pre_tool_use_hook

# 워커 기본 도구(WebSearch 포함 — RFC-011 M1 자원동원). 매체-중립 Rule 자산.
WORKER_BASE_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "ToolSearch", "WebSearch", "WebFetch"]


def _make_builder(cfg: Config, audit: AuditLog, bot_info=None, model_map=None, persona_map=None, effort_map=None,
                  global_model=None, global_effort=None):
    """role에 맞는 도구·권한·훅·State를 갖춘 Organt를 만드는 빌더를 돌려준다.
    model_map({organt_id: model})이 주어지면 그 봇만 build_options에 model override를 싣는다
    (per-agent 모델 — 매체가 직원별 LLM 지정. 디스코드 경로는 model_map 미전달이라 동작 불변).
    effort_map({organt_id: effort})이 주어지면 그 봇만 추론 강도(low/medium/high/xhigh/max) override를 싣는다.
    persona_map({organt_id: persona})이 주어지면 그 봇만 기본 인격(CLAUDE.md) 뒤에 자기 개성·지침을
    덧붙인다 — 스튜디오에서 사용자가 지정한 봇별 정체성이 실제로 프롬프트에 실린다(미지정 봇은 불변).
    Config가 frozen이라 전역 cfg.model을 못 바꾸므로, override 인자로 봇별 모델을 통과시킨다."""
    bot_info = bot_info or {}
    model_map = model_map or {}
    persona_map = persona_map or {}
    effort_map = effort_map or {}
    def organt_builder(organt_id, server, role, flow=None, state_tag=None):
        # 리더도 한 명의 직원 — 구현 도구(Write/Edit)를 그대로 갖는다. 차이는 권한이 아니라
        # 역할: 목표는 팀 합의로 정하고(set_goal), Work 위임 본문은 '스펙'이 아니라
        # '측정가능한 목표'이며, 받은 owner가 구현·검증까지 끝까지 책임진다.
        allowed = [*WORKER_BASE_TOOLS, *FLOW_TOOLS]   # 기본 도구(WebSearch 포함, RFC-011 M1) + 흐름 도구
        # 턴 한도 = 폭주(무한 루프) 브레이크일 뿐, 작업을 자르는 수단이 아니다 — 끊겨도 작업·세션은
        # 보존되고 '이어서' 재위임으로 잇는다. 다만 큰 산출물(대형 클라 본체 등)이 한 위임 안에 끝나도록
        # 워커 예산을 넉넉히 두고, 운영 중 조정은 환경변수로(코드 수정·재배포 불필요).
        # 라이브 정량분석(2026-06-10): 어떤 워커도 한도 근처에 가지 않았다(최대 13회 도구호출/60턴) —
        # 미완의 원인은 한도가 아니라 도구포기·자발 중간보고였다. 한도는 '작업을 자르는 일이 절대
        # 없도록' 크게 두고(브레이크 역할만), 폭주는 활동 워치독·run 증거 게이트가 막는다.
        turns = int(os.environ.get("ORGANT_WORKER_TURNS", "300"))
        if role == "leader":
            allowed = allowed + LEADER_TOOLS
            turns = int(os.environ.get("ORGANT_LEADER_TURNS", "500"))
        # [턴 예산 캡(2026-07-21, U-036 재작업 #4 — 사용자: '하이쿠가 아니라 파이프라인 설계의 토큰
        # 낭비를 최적화')] 실측: 봇 턴 평균 output 5~8K에 디자이너 한 턴이 도구 ~35왕복·~37K 생성 —
        # 위 max_turns(300/500)는 무한루프 브레이크일 뿐이라 한 턴의 output·비용은 사실상 무상한이었다.
        # SDK max_budget_usd(초과 시 그 자리 종료 — 세션은 보존돼 다음 wake가 resume로 잇고, organt가
        # 정직 마커를 달아 미완 참칭 없음)를 턴 봉투로 씌운다. ORGANT_TURN_BUDGET_USD 기본 1.0 =
        # U-036 실측 평균 턴(~$0.06)의 ~15배 — 정상 작업·대형 파일 턴은 안 닿고 왕복 폭주 꼬리만
        # 자른다(값은 정책 — env 조정, 0=off). 효과 수치는 다음 실판 실측으로 확정.
        try:
            _tbud = float(os.environ.get("ORGANT_TURN_BUDGET_USD", "1.0"))
        except ValueError:
            _tbud = 1.0
        # state_tag: 증류(수면) 등 '작업 외 대화'는 별도 세션 파일을 써 작업 기억을 오염시키지 않는다.
        # 흐름이 있으면 세션을 '흐름 스코프'별로 분리 — 프로젝트 간 기억 오염·병렬 흐름 충돌이
        # 구조적으로 불가능(같은 봇이 두 프로젝트에서 동시에 일해도 기억이 섞이지 않음).
        scope = getattr(flow, "session_scope", None) if flow is not None else None
        tag = state_tag or (f"{scope}_{organt_id}" if scope else organt_id)
        state_path = cfg.audit_log_path.parent / f"organt_state_{tag}.json"
        label = bot_info.get(organt_id, role)   # 협업 관찰성: 로그에 '누가' 남기기
        # sdk 서버별 도구호출 타임아웃(ms) — CLI가 env(MCP_TOOL_TIMEOUT)보다 우선 적용하는 명시 설정.
        # request(동료 위임)는 동료의 중첩 작업 동안 수십 분 블록되는 게 정상 설계라 사실상 해제해 둔다.
        server = {**server, "timeout": int(os.environ.get("MCP_TOOL_TIMEOUT", "14400000"))}
        heartbeat = None
        narrate = None
        if flow is not None:
            def heartbeat():   # 메시지 수신 단위 하트비트 — 도구 훅 사이 사각(긴 단일 생성)을 메움
                try:
                    flow.last_activity = time.monotonic()
                except Exception:
                    pass

            def narrate(text):   # [진행 가시성] 봇 추론(발화) 스니펫을 '지금 생각 중' 활동으로 — 상태블록이 보인다
                try:
                    t = " ".join(str(text or "").split())
                    if not t:
                        return
                    # [응찰≠생각] 발언권 프로브 응답(응찰/패스/계속/발언권)은 시스템 메커니즘이지 봇의 진짜 작업
                    # 생각이 아니다 — activity log(💭)에 안 넣는다(사용자: "응찰 이러면서 자기 생각이 아닌데").
                    # flow._suppress_activity가 주경로(floor 구간 통째 억제), 이건 백스톱(마커 없는 산문 포함).
                    if any(m in t for m in Marker.MECHANISM_TOKENS):
                        return
                    if len(t) > 96:                  # [단어경계 컷] '.coll' 같은 중간 절단 방지 — 마지막 공백까지만
                        cut = t[:96]
                        t = (cut[:cut.rfind(" ")] if " " in cut[40:] else cut) + "…"
                    flow.note_activity(organt_id, "💭 " + t)
                except Exception:
                    pass
        on_turn = None
        if flow is not None:
            def on_turn(rec):   # [관측 v1] wake 결산 → flow.log(=Sys._log, trace_id·seq 자동 부여)로 방출
                try:
                    if getattr(flow, "log", None):
                        flow.log("turn_done", bot=organt_id, role=label, **rec)
                except Exception:
                    pass
                # [사용량 귀속(2026-07-18, 운영/과금)] 턴 비용을 채널 단위로 웹에 보고 → 보드 주인 원장 적립.
                # sync 콜백이라 실행 루프 있으면 fire-and-forget(없으면=테스트 스킵). 손실은 소폭 과소청구
                # (사용자 유리)이고 flow.jsonl에 원본이 남아 후속 대사 가능.
                try:
                    _cost = rec.get("cost_usd")
                    _rep = getattr(getattr(flow, "guide", None), "report_usage", None)
                    _ch = getattr(flow, "user_channel", None)
                    if _cost and _rep and _ch is not None:
                        import asyncio as _aio
                        _loop = _aio.get_running_loop()
                        _t = _loop.create_task(_rep(int(_ch), float(_cost), int(rec.get("tokens_out") or 0)))
                        _USAGE_BG.add(_t)

                        def _quota_check(t, _flow=flow):
                            # [판 크레딧 캡(2026-07-20)] 적립 응답이 초과+강제면 흐름에 정지 신호 —
                            # 진행 중 판이 무제한이던 구멍(ch79 $53)의 턴 단위 마개. 소비는 sys 루프.
                            _USAGE_BG.discard(t)
                            try:
                                r = t.result()
                                if isinstance(r, dict) and r.get("over") and r.get("enforce"):
                                    _flow._quota_over = True
                            except Exception:
                                pass
                        _t.add_done_callback(_quota_check)
                except Exception:   # 실행 루프 없음(테스트)·기타 → 스킵(과금은 best-effort)
                    pass
        # organt의 파일 도구(cwd)는 '현재 흐름의 작업공간'을 따른다 — 프로젝트별 폴더 분리와 정합
        # (cwd가 base 고정이면 run은 프로젝트 폴더, Write는 base로 가는 분열이 생긴다).
        cwd = str(getattr(flow, "workspace", None) or cfg.workspace_dir)
        # [세션-cwd 고정] CLI 세션 저장소는 cwd 기준 — 이미 세션이 있는 봇은 '그 세션이 시작된 cwd'로
        # 빌드해야 resume가 찾는다. 흐름 도중 create_project가 작업공간을 하위 폴더로 깎아도(카빙)
        # 세션이 안 깨진다(카빙 폴더는 원 cwd의 하위라 파일 쓰기 범위는 동일, run은 flow.workspace 사용).
        # 라이브 관측: cwd가 바뀐 리더 이어가기가 'No conversation found'로 12회 전부 헛돈 뒤 미완 종료.
        cwd = pinned_cwd(state_path) or cwd
        _bopts = dict(
            cwd=cwd, allowed_tools=allowed, mcp_servers={"guide": server}, max_turns=turns,
            hooks={
                "PreToolUse": [HookMatcher(hooks=[make_pre_tool_use_hook(audit, allowed, actor=organt_id, role=label, flow=flow)])],
                "PostToolUse": [HookMatcher(hooks=[make_post_tool_use_hook(audit, actor=organt_id, role=label, flow=flow)])],
            },
        )
        if _tbud > 0:
            _bopts["max_budget_usd"] = _tbud
        _m = model_map.get(organt_id) or (global_model or "")   # [모델] 개별 지정 우선, 없으면 전역 기본(채용 봇 포함)
        if _m:
            _bopts["model"] = _m
        _ef = (effort_map.get(organt_id) or global_effort or "").strip()   # [effort] 개별 우선, 없으면 전역 기본
        if _ef:
            _bopts["effort"] = _ef
        _pp = (persona_map.get(organt_id) or "").strip()
        if _pp:                                  # [per-agent 인격] 기본 인격 뒤에 이 봇만의 개성·지침을 덧붙임
            _bopts["system_prompt"] = (load_persona()
                                       + "\n\n[이 직원만의 개성·지침 — 스튜디오에서 사용자가 지정한 정체성]\n"
                                       + _pp)
        return Organt(cfg, build_options(cfg, **_bopts),
                      state_path=str(state_path), on_activity=heartbeat, narrate=narrate, on_turn=on_turn)
    return organt_builder
