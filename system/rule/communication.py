"""Communication Rule — 단일 활성 '베턴'과 요청 스택(순수 로직, 네트워크 없음).

docs(Rule/Communication.md):
- 흐름은 User(SMS)에서 시작한다. Organt는 스스로 흐름을 시작하지 않는다.
- 활성(alive) Organt은 항상 1명. Request 시 sender sleep / receiver wake.
- Work Request는 이미 미완 Work를 가진(흐름에 참여 중인) Organt에게 보낼 수 없다
  (겹침·순환 방지 = busy-guard).
- Response는 스택 역순(LIFO)으로 close. 모든 요청이 닫히면 흐름은 시작점(origin)으로
  복귀하고 종료된다. → 항상 1명만 활성(단일흐름) = 토큰 절약·사이드이펙트 감소.
- Work Response 불만족 시 Redo, 한계 초과 시 위로 상신(escalate).

[병렬 — docs Communication.md 13–14행 "여럿(병렬)은 이 제약을 완화하는 Feature로 둔다"]
완화는 '서로 다른 흐름의 동시 진행'뿐이고, 흐름 '안'의 단일활성(베턴)은 불변이다. 흐름 간
안전은 흐름 수 상한(임의 숫자) 같은 가드가 아니라 **점유의 배타성**으로 보장한다: 전역 점유
장부(Engagement)에 의해 한 직원(봇)은 한 시점에 한 흐름에만 참여한다(현실의 '한 사람은 한
회의에만'). 동시 작업량의 자연 한도 = 직원 수. 장부는 SYS가 소유하고 흐름의 comm에
attach_engagement로 붙는다 — 모든 점유/해제는 request/respond/escalate 안에서만 일어나
(vote·meet·복구 경로 포함) 등록·해제가 구조적으로 대칭이다.

메시지 인코딩/파싱(`[Request]`/`[Response]` 포맷)은 protocol.py가 담당한다.
"""
import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import List, Optional

from ..protocol import Kind


# [G4 — 연속 실패 하드블록 시간창(B-03)] 연속 무응답 3회가 이 창(초) 안에 몰렸을 때만 하드블록 — 워커 무활동
# 타임아웃(기본 480s) 3회 + 오버헤드를 여유 있게 덮는다(창 밖으로 흩어진 실패는 '연속 불안정'이 아님).
_HARD_BLOCK_WINDOW = float(os.environ.get("ORGANT_HARDBLOCK_WINDOW", "3600"))
# [G4] 자기치유 대상 마커 — 이 값으로 시작하는 _hard_blocked만 SYS 프로브가 해제한다(배포 자격증명 등
# '사람 조치' 하드블록은 프로브로 못 풂 — sys_core._hard_block_probe와 문자열 계약).
HARD_BLOCK_TRANSIENT = "연속 무응답 — 환경 불안정"














# ══ [분할 — 응집 헬퍼 재수출(2026-07-03)] 팀·역량 라우팅/직군·예비/멤버 해석/응답 실질성/협업 실행
# (fork·발언) 헬퍼는 comm_helpers.py로 추출 — 내용·순서 불변, 여기서 재수출해 기존 import 경로
# (`from system.rule.communication import _is_spare` 등)와 이 모듈 내 참조를 그대로 보존한다(파사드).
from .comm_engine import (  # noqa: F401 (M9 베턴엔진 재수출)
    CommError, RedoLimitExceeded, BusyInOtherFlow, Engagement, Frame, CommunicationManager)
from .comm_helpers import (  # noqa: F401
    _CAPS, _HOLLOW_PING, _JOB_SEP, _SPARE_LABEL, _add_members, _body_overlap,
    _capability_gaps, _clarify_hold, _find_variant_job, _fork_collect,
    _free_alternatives, _group_of, _is_spare, _is_substantive, _job_tokens,
    _jobs_of, _kw, _needed_caps_coverage, _norm_job, _same_job, _offdomain_capability_hit,
    _resolve_members, _say, _say_speech, _uniq)
from .comm_ceremonies import vote, vote_stop, parallel_work, recruit  # noqa: F401 (M9 재수출)




# [1층 floor seam — 발언 신호(표면 규약)] 지명/패스 마커. 파싱은 매체 표면 규약이라 정책(floor.py)이
# 아니라 대화 Rule 소관 — 정책은 내용 불가지(Turn.addressee/passed 신호만 받는다).
_NOMINATE_RE = re.compile(r"\[\s*지명\s*[:：]\s*([^\]\n]{1,40})\]")

# [직군밖 부정 센티넬(2026-07-14, 라이브 P-016 근본버그)] 봇이 offdomain_role에 "이 일은 직군밖 아님"을
# '해당없음'·'없음'·'N/A' 같은 부정어로 답하면, 종전엔 문자열이 비어있지 않아 *직군밖 반려*로 오분류돼
# 파일 소유가 유령 직군('해당없음')으로 이전됐다(app.js 등 10개 파일이 아무도 못 고치는 상태 → 7시간 교착).
# 이 값들은 '반려 없음'과 동치로 취급한다(빈 값). 도메인명이 실제로 이렇게 생기는 일은 없다.
_OFFDOMAIN_NEGATIONS = {"", "해당없음", "해당 없음", "없음", "없다", "무", "n/a", "na", "none", "null",
                        "-", "–", "—", ".", "해당사항없음", "해당 사항 없음", "not applicable", "적용안됨"}


def _norm_offdomain(val) -> str:
    """offdomain_role 인자·regex 캡처를 정규화 — 부정 센티넬이면 ''(반려 아님), 아니면 원문 strip."""
    s = str(val or "").strip()
    return "" if s.lower() in _OFFDOMAIN_NEGATIONS else s


def _turn_signals(flow, res, allowed):
    """발언 텍스트 → (지명 대상 id|None, 패스 여부). 지명은 allowed(대화 참여자) 안에서만 해석 —
    참여자 밖 지명·해석 불가 이름은 '지명 없음'으로 무해화(자기선택 경로로 넘어간다)."""
    t = (res or "").strip()
    passed = bool(t.startswith("[패스]") or re.match(r"^\[?\s*패스\s*\]?\s*$", t))
    addressee = None
    m = _NOMINATE_RE.search(t)
    if m:
        ids = _resolve_members(m.group(1), flow, allowed)
        addressee = ids[0] if ids else None
    return addressee, passed


# [1층 — ②자기선택 응찰/종결표결 파싱] `[응찰: N]`(발언권)·`[계속: N]`(종결 반대 — 동형 강도).
# 판단은 후보 봇의 LLM이 하고, 여기는 그 판정의 표면 인코딩만 읽는다.
from ..protocol import Marker as _Marker
_BID_RE = _Marker.BID_RE          # [마커 사전 이관 1차] 정의 정본은 protocol.Marker — 여기는 참조만


def _bid_score(res) -> int:
    """응찰/표결 텍스트 → 강도(0~9). [패스]·[종료]·빈 응답=0. 마커 없는 실질 텍스트는 약한
    응찰(1)로 관용 — 규약 미준수가 발언 의지를 소멸시키지 않게(이중수용 관례와 같은 정신)."""
    t = (res or "").strip()
    if (not t or t.startswith("[패스]") or t.startswith("[종료]")
            or re.match(r"^\[?\s*(패스|종료)\s*\]?\s*$", t)):
        return 0
    m = _BID_RE.search(t)
    if m:
        return max(0, min(9, int(m.group(1))))
    return 1 if _is_substantive(t) else 0


async def meet(flow, me_id, args):
    """[Communication Rule 로직] meet — guide_tools에서 이관(평문 반환, @tool이 _ok 래핑)."""
    from .._util import _speech_clip, _react, _dbg
    from .task import _ckpt
    g = flow.guide
    if flow.current is None:
        return ("오류: 진행 중인 Task가 없습니다. create_task 먼저 여세요.")
    members = _resolve_members(args.get("members", ""), flow, flow.current.team) or \
              [m for m in flow.current.team if m != me_id]
    members = [m for m in members if m != me_id and not _is_spare(flow, m)]
    if not members:
        return ("오류: 회의할 멤버가 없습니다.")
    _hold = _clarify_hold(flow, me_id)   # [G2 — clarify 행동 잠금(B-02)]
    if _hold:
        return _hold
    if (any(not x.done() for x in getattr(flow, "inflight_tasks", ()))
            and flow.comm.alive != me_id and not flow.comm.done):
        return ("[대기] 직전 위임이 아직 진행 중입니다 — 회의는 그 결과를 받은 뒤 여세요.")
    if getattr(flow, "fork_active", 0) > 0:
        return ("[대기] 다른 의견 수집이 진행 중입니다 — 그 결과를 받은 뒤 여세요(중첩 수집 금지).")
    # [SYS 자동 개시(2026-07-14, 사용자: '기계적 단계는 SYS가 돌려')] SYS가 첫 회의를 자동으로 열 때는
    # (봇이 도구를 부른 게 아니라) comm alive 가드를 우회한다 — 개시자를 alive로 세워 정상 진행.
    _sys_open = bool(args.get("_sys_open"))
    if _sys_open:
        try:
            flow.comm.alive = me_id; flow.comm.done = False
        except Exception:
            pass
    elif flow.comm.done or flow.comm.alive != me_id:
        return (f"지금은 회의를 열 수 없습니다(활성={flow.comm.alive}) — 진행 중인 요청의 "
                   f"응답을 받은 뒤 다시 시도하세요.")
    topic = str(args.get("topic", "")).strip()
    # [리더도 참여자 — 중재자 아님(2026-07-08, 사용자: '리더가 하나의 참여자가 아닌 중재자로 전락')]
    # 소집자(리더)도 자기 도메인 관점의 독립 의견을 내야 회의를 연다 — 진행자로만 빠져 남의 의견만
    # 수합·확정하던 중앙집권 구조 해소. R1 브랜치엔 이 의견이 안 보이므로 앵커링 0(동료는 독립적으로).
    my_view = str(args.get("my_opinion", "")).strip()
    if not my_view:
        return ("[회의 — 당신도 참여자입니다] 소집자(당신)도 이 주제에 **자기 도메인 관점의 독립 의견**을 "
                "내야 회의를 엽니다 — meet(topic=…, my_opinion='당신의 입장 3~5줄, 근거와 함께')로 다시 "
                "여세요. 리더는 의견을 수합·확정만 하는 중재자가 아니라 **동등한 한 참여자**입니다.")
    try:
        rounds = max(1, min(3, int(str(args.get("rounds", "2")).strip() or "2")))
    except ValueError:
        rounds = 2

    async def _run_meet():
        from .._util import doc_collab_on, dossier_append, dossier_rel
        minutes = []
        r1_full = []       # [B-11] (발언자, 전문) — R2+ '전원 1R 압축' 합성 원료(시스템 기계 압축)
        last_full = None   # [B-11] 직전 1발언 (발언자, 전문) — R2+에 전문 주입
        # 1라운드 = 독립 의견 fork(동시 수집) — 첫 입장은 서로를 안 보는 게 앵커링 없는
        # 진짜 다양성이고, 동시 수집이라 회의 비용도 준다(회의가 싸져야 자주 연다 = 협동성).
        def body_r1(m):
            return (f"[회의 1라운드 — 독립 의견] 주제: {topic}\n(이 라운드에선 동료 발언이 "
                    f"보이지 않습니다 — 앵커링 방지)\n당신({flow._info(m)})의 전문 관점 "
                    f"입장을 3~5줄(최대 1000자)로, 근거와 함께.")
        # [마일스톤 파이프라인 §4 — 완전 turn-taking(2026-07-09 확정)] 강제 R1(전원 의무 발화) 폐지:
        # 소집자 발제 후 첫 발화부터 응찰. 트레이드오프 관측(§8 민감 접근): R1은 발산(앵커링 방지)
        # 장치였다 — 제거가 의견 다양성에 주는 영향은 floor_bid 분포로 관측해 데이터로 판단한다.
        from .milestone import extract_consensus as _ms_extract
        from .milestone import pipeline_on as _ms_on
        _no_r1 = _ms_on()
        conv_props = []   # [결정권자 폐지] 종결 표결에 동봉된 수렴안들 — 가결 시 자동 등록 원료
        _gate_unmet = {"on": False}   # [게이트=수렴안 채택] 재응찰 시 종결표결 프롬프트를 게이트로 전면화
        if not _no_r1:
            for m, res, note in await _fork_collect(flow, me_id, members, body_r1):
                cut = _speech_clip(res or note)   # 회의록·채널 발언은 같은 내용(기록 일치)
                line = f"[1R] {flow._info(m) or m}: {cut}"
                minutes.append(line)
                _who = flow._info(m) or m
                r1_full.append((_who, res or note))
                last_full = (_who, res or note)
                # [B-12] 매체 조건부: post_document 매체=200자+전문 ref / 폴백 매체=500자 clip.
                await _say_speech(flow, m, "[회의 1R]", res or note)  # 본인 명의 발언
                if res is not None and m in flow.current.team and m != flow.leader:
                    flow.current.participated.add(m)        # 회의 발언 = 실질 협의 인정
            # [리더도 참여자] 소집자의 독립 의견도 회의록·채널에 본인 명의로 — R1 브랜치엔 이미 안 보였으므로
            # 앵커링 0(동료는 독립 수집 완료). 리더가 '남 의견만 모으는 중재자'가 아니라 한 참여자로 발언.
            minutes.append(f"[1R] {flow._info(me_id) or me_id}(소집자): {_speech_clip(my_view)}")
            r1_full.append((flow._info(me_id) or me_id, my_view))
            last_full = (flow._info(me_id) or me_id, my_view)
            await _say_speech(flow, me_id, "[회의 1R]", my_view)
            # [B-09 Phase A — Task Dossier] R1 종료 즉시 MINUTES.md에 전문 기록(append-only·무절단) —
            # collab_notes 6,000자 head-keep이 '캡 도달 후 새 기록 통째 유실'하던 것의 내용 보존 원본.
            dossier_append(flow, "MINUTES.md",
                           f"## 회의 — {topic} [1R 독립의견]\n"
                           + "\n".join(f"[1R] {w}: {t}" for w, t in r1_full))
        else:
            # [중립 어휘(2026-07-14, 사용자: '소집자 의견 이러니 자기가 리더인줄 아나')] '발제/소집자'는
            # 폐지된 발제자 권위처럼 읽힌다 — 회의 연 사람도 한 참여자일 뿐이라 '회의 시작 / 여는 의견'으로
            # 중립화(권한 착시 제거). 기능은 동일: 주제 제시 + 자기 의견, 이후 전 발언이 응찰.
            _preface = topic + (f"\n[여는 의견] {my_view}" if str(my_view or "").strip() else "")
            minutes.append(f"[회의 시작] {flow._info(me_id) or me_id}: {_speech_clip(_preface)}")
            await _say_speech(flow, me_id, "[회의 시작]", _preface)
            dossier_append(flow, "MINUTES.md",
                           f"## 회의 — {topic} [완전 TT(§4): 강제 R1 없음]\n"
                           f"{flow._info(me_id) or me_id}: {_preface}")
        # ══ [1층 floor seam — R2+ 발언권 순환의 정책화(CA-Lab RFC-003 1층)] 토론의 발언 '순서'는
        # FloorPolicy가 정하고, 발언 1회의 실행(베턴 프레임·wake·회의록·게시·참여 인정)은 정책과
        # 무관한 단일 경로(_speech)다 — 베턴·점유 담보(Engagement·BusyInOtherFlow)는 그대로(1층은
        # '누가 다음에 말하는가'만 갈아끼운다). 구조 교체는 ORGANT_FLOOR 하나로:
        #   미설정(기본) = orchestrated round_robin — 종전과 동일한 고정 라운드 순서·문맥 주입·
        #   기록(동작 불변; 사회자 중앙 배분이 같은 seam 위의 한 정책으로 자리만 바뀐 것).
        #   turn-taking = Sacks ①지명(발언 끝 [지명: 이름]) ②자기선택 = **LLM 응찰**(후보 봇들이
        #   병렬로 '지금 내가 발언해야 하나'를 스스로 판정, [응찰: N] — 최고 응찰이 발언권 획득,
        #   동률=침묵 오래된 순) ③계속 — 무응찰 소진 시 조기 자연 종결(고정 라운드에선 불가능).
        from .floor import (CLOSE_VOTE, SELF, FloorState, Turn, floor_mode, make_floor,
                            round_robin, run_conversation)
        mode = floor_mode(getattr(flow, "floor_mode", None), default="orchestrated")
        tt = (mode == "turn-taking")
        schedule = [(r, m) for r in range(2, rounds + 1) for m in members]
        budget = len(schedule)                # 토론 발언 예산 — 정책 불문 종전 라운드 비용과 동형
        wakes = {"n": 0}
        # 총 wake 상한(발언+응찰) — TT 비용·폭주 백스톱. 응찰은 open마다 후보 전원(≤인원-1)이라
        # 상한을 인원 배수로 잡는다(회의는 소수 인원 표면 — 응찰이 곧 '전원이 눈치보는' 비용).
        # [게이트 회의는 재응찰 여지 확보(2026-07-14, 사용자: '상한 두지 마라')] 수렴안 채택이 유일
        # 출구라 여러 패스가 필요할 수 있어 파이프라인 회의는 비용 천장을 4배로 — 인위적 라운드 상한은
        # 없고, 이 천장은 무의미 무한스핀(응찰 소진 후 no-op 반복) 방지용 비용 바닥일 뿐이다.
        wake_cap = budget * (len(members) + 1) * (4 if _no_r1 else 1) + 2
        sched_i = {"i": 0}                    # orchestrated 라벨(r)용 — allocator 소비 순서와 1:1
        block = {"label": None, "items": []}  # [B-09] MINUTES.md 블록 버퍼(라운드/토론 단위 flush)

        def _flush_minutes():
            # [B-09] 블록(라운드) 단위 전문 append — 크래시해도 끝난 블록까지는 원본 보존(종전 동일).
            if block["items"]:
                head = "[토론]" if tt else f"[{block['label']} 토론]"
                dossier_append(flow, "MINUTES.md", f"## 회의 — {topic} {head}\n"
                               + "\n".join(f"[{block['label']}] {w}: {t}" for w, t in block["items"]))
                block["items"] = []

        def _ctx_txt():
            if doc_collab_on() and r1_full:
                # [B-11 Phase C — meet R2+ 재방송 축소(ORGANT_DOC_COLLAB=1)] minutes[-8:]×1,500자
                # 재방송(~12K자/발언자) 대신: 전원 1R 발언자별 ~200자 압축(시스템 기계 합성 — 전원
                # 가시성 유지, '직전 2발언' 앵커링 기각 A-9) + 직전 1발언 전문 + MINUTES.md 참조(~3K자).
                comp = "\n".join(f"- {w}: {_speech_clip(t, 200)}" for w, t in r1_full)
                _lw, _lt = last_full if last_full else ("", "(아직 발언 없음)")
                return (f"[전원 1R 요지 — 시스템 압축]\n{comp}\n"
                        f"[직전 발언 전문] {_lw}: {_lt}\n"
                        f"(발언 전문 전체: 작업공간 {dossier_rel(flow.current.task_id)}/MINUTES.md"
                        f" — 필요할 때만 Read)")
            return "\n".join(minutes[-8:]) or "(아직 발언 없음)"

        def _mk_body(m, r, won=False):
            """토론 발언 프롬프트 — r(int)=종전 라운드 문구(바이트 동일), r=None=TT(발언권 규약 동봉,
            won=응찰 낙찰 발언)."""
            log_txt = _ctx_txt()
            if flow.log:
                # [B-09 Phase A 관측 지표] meet 재방송 자수 — R2+ 축소(B-11)의 절감 검산 베이스라인.
                flow.log("meet_r2_inject", chars=len(log_txt), r=(r or 0),
                         compressed=bool(doc_collab_on() and r1_full))
            if r is not None:
                return (f"[회의 {r}라운드] 주제: {topic}\n지금까지의 발언:\n{log_txt}\n\n"
                        f"당신({flow._info(m)})의 차례입니다 — 앞 발언에 동의/반박/보완하며 "
                        f"당신 전문 관점의 입장을 3~5줄(최대 1000자)로. 맹목적 동의 금지(근거 필수). 이미 기록된 실측은 재실행하지 말고 원문(파일:줄·수치) 인용으로 갈음하세요.")
            head = ("[회의 토론 — 발언권 획득(당신의 응찰이 선정됨)] 방금 응찰한 그 관점을 지금 발언하세요."
                    if won else "[회의 토론]")
            return (f"{head} 주제: {topic}\n지금까지의 발언:\n{log_txt}\n\n"
                    f"당신({flow._info(m)})의 차례입니다 — 앞 발언에 동의/반박/보완하며 "
                    f"당신 전문 관점의 입장을 3~5줄(최대 1000자)로. 맹목적 동의 금지(근거 필수). 이미 기록된 실측은 재실행하지 말고 원문(파일:줄·수치) 인용으로 갈음하세요.\n"
                    f"[발언권 규약] 특정 동료의 답이 꼭 필요하면 발언 마지막 줄에 `[지명: 이름]` — "
                    f"이 주제에 더 보탤 것이 없으면 본문 대신 `[패스]`만.")

        async def _speech(m, body, label):
            """발언 1회 — 정책 불문 단일 실행 경로. 반환 Turn(지명·패스 신호) / None=회의 중단."""
            nonlocal last_full
            if flow.comm.done or flow.comm.alive != me_id or wakes["n"] >= wake_cap:
                return None
            try:
                flow.comm.request(me_id, m, "meet", Kind.INFO)
            except BusyInOtherFlow as e:
                # 멤버 단위 사유(라운드 사이에 타 흐름이 데려감) — 회의를 끊지 않고 그
                # 멤버만 건너뛴다(부분 진행). 베턴 경합(아래)과 달리 시스템 문제가 아니다.
                minutes.append(f"[{label}] {flow._info(m) or m}: (타 흐름({e.holder_scope}) "
                               f"참여 중 — 이 라운드 불참)")
                return Turn(speaker=m, passed=True, body="(타 흐름 참여 중 — 불참)")
            except CommError as e:
                minutes.append(f"(회의 중단 — 베턴 경합: {str(e)[:60]})")
                return None
            wakes["n"] += 1
            try:
                res = await flow.wake(m, body, Kind.INFO)
            except Exception as e:
                res = f"(발언 실패: {e})"
            try:
                flow.comm.respond(m, "accept", res)
            except CommError:
                pass
            addressee, passed = _turn_signals(flow, res, members) if tt else (None, False)
            _who = flow._info(m) or m
            if passed:
                minutes.append(f"[{label}] {_who}: (패스)")
                return Turn(speaker=m, passed=True, body=res or "")
            minutes.append(f"[{label}] {_who}: {_speech_clip(res)}")
            if block["label"] != label:
                _flush_minutes()
                block["label"] = label
            block["items"].append((_who, res))
            last_full = (_who, res)
            await _say_speech(flow, m, "[회의]" if tt else f"[회의 {label}]", res)  # 본인 명의([B-12] 매체 조건부)
            if m in flow.current.team and m != flow.leader:
                flow.current.participated.add(m)    # 회의 발언 = 실질 협의 인정
            return Turn(speaker=m, addressee=addressee, body=res or "")

        async def _speak(speaker, alloc):
            if tt:
                return await _speech(speaker, _mk_body(speaker, None, won=(alloc.kind == SELF)), "토론")
            r, m2 = schedule[min(sched_i["i"], len(schedule) - 1)]
            sched_i["i"] += 1
            return await _speech(m2, _mk_body(m2, r), f"{r}R")

        async def _bid(cands, purpose):
            """[②자기선택 = LLM 응찰 / 종결 확인 표결(병렬)] 각 후보 봇이 '지금 내가 발언해야
            하나'(OPEN) 또는 '마쳐도 되나'(CLOSE_VOTE — [계속]=발언 의무 진 반대)를 **스스로 LLM으로
            판정**한다 — 판단은 봇의 지능, 선정 규칙(최고 강도·동률=침묵 오래된 순)은 정책, 병렬
            수집(점유·게이트·부분 조인)은 _fork_collect 재사용. 응찰·표결은 회의록에 안 남는다 —
            발언권을 받은 발언만 대화 사실(낙찰자·반대자는 _speak 경로로 정식 발언)."""
            if wakes["n"] >= wake_cap:
                return []
            def body_of(c):
                if purpose == CLOSE_VOTE:
                    # [결정권자 폐지 — 종결 표결이 곧 확정(2026-07-09, 사용자)] 파이프라인 회의에서
                    # [종료] 투표는 수렴안(완수조건 초안)을 동봉한다 — 가결되면 그 안이 그대로 등록된다
                    # (사람이 아니라 표결+등록 게이트가 확정). 확정 발화 권력의 비인격 대체.
                    _conv = ("\n마쳐도 된다면 `[종료]` 다음 줄에 이 회의의 수렴안을 동봉하세요:\n"
                             "[수렴안]\n단계: <전체 로드맵 1단계(완전한 MVP)>\n단계: <2단계(확장)>\n"
                             "목표: <이번(첫) 주기의 목표 한 줄>\n<조건 | 실증절차(run으로 확인)>\n"
                             "<조건 | 실증절차>\n단위: <분해 단위 목표> | <실증절차>\n"
                             "단위: <분해 단위 목표> | <실증절차>\n[/수렴안]\n"
                             "('단계:' 줄 = 전체 구조 로드맵(예: 달구지→자동차→스포츠카) — 첫 주기는 완전한 "
                             "MVP로, 주기는 순차 1개씩 완수·보고 후 다음을 엽니다. '단위:' 줄 = 이 주기의 "
                             "SubTask 분해 — **참여 도메인마다 자기 몫 단위**를 넣으세요(한 도메인이 전부 "
                             "카빙 금지). 진행 중 주기가 이미 있으면 이 수렴안은 그 주기의 단위 추가로 "
                             "등록됩니다. 동료가 이미 낸 수렴안에 동의하면 그대로 복사·수정해 제출 — 가결 시 "
                             "최다 지지안이 등록됩니다. 등록 후 각자 pick_backlog(desc)로 자기 백로그를 "
                             "등재해 전담하세요 — 백로그는 개인 역량 안의 작업 단위입니다)"
                             if _no_r1 else " 마쳐도 되면 `[종료]`만.")
                    _gate = ("\n\n**이 회의는 [수렴안]이 채택돼야만 끝납니다 — 발언권 소진으로는 안 "
                             "끝납니다.** 아직 채택된 수렴안이 없습니다. 마치려면 반드시 위 형식의 "
                             "[수렴안]을 동봉하세요(누구든). 없으면 회의는 닫히지 않고 다시 열립니다."
                             if (_no_r1 and _gate_unmet["on"]) else "")
                    return (f"[회의 — 종결 확인] 주제: {topic}\n지금까지의 발언:\n{_ctx_txt()}\n\n"
                            f"발언이 소진됐습니다. 이 회의를 마쳐도 됩니까? 당신({flow._info(c)})이 "
                            f"판단하세요. 더 다뤄야 할 것이 있으면 `[계속: N]`(N=1~9)과 무엇인지 한 줄만 "
                            f"— 발언권을 받아 직접 발언하게 됩니다.{_conv}{_gate}")
                return (f"[회의 — 발언권 응찰] 주제: {topic}\n지금까지의 발언:\n{_ctx_txt()}\n\n"
                        f"지금 발언권이 비어 있습니다. 당신({flow._info(c)})이 **지금** 발언할 필요가 "
                        f"있는지 스스로 판단하세요. 있으면 `[응찰: N]`(N=1~9, 필요 강도)과 한 줄 이유만 "
                        f"답하세요 — 발언 내용은 발언권을 받은 뒤에 말합니다. 없으면 `[패스]`만.")
            out = []
            for m, res, note in await _fork_collect(flow, me_id, list(cands), body_of):
                wakes["n"] += 1
                s = 0 if res is None else _bid_score(res)
                out.append((m, s))
                if purpose == CLOSE_VOTE and _no_r1 and res:
                    # [종결 표결 동봉 수렴안 수집] 가결 시 자동 등록의 원료 — 제출 순서 보존.
                    _c = _ms_extract(res)
                    if _c:
                        conv_props.append(_c)
                if flow.log:
                    flow.log("floor_bid", surface="meet", who=m, score=s,
                             vote=(purpose == CLOSE_VOTE))
            return out

        def _on_alloc(a):
            if flow.log:   # 관측: 배분 이벤트 영속 — CA-Lab 1층 실험(발언 분포·소진)의 원자료
                flow.log("floor_alloc", surface="meet", policy=mode, kind=a.kind,
                         nxt=a.next, reason=(a.reason or "")[:40])

        async def _ratify_vote(prop):
            """[수렴안 확정 표결(2026-07-14, 사용자: '찬성을 모두 받아야만 수렴안이 제시될 수 있다')]
            제출된 수렴안에 전원 찬성이어야 채택 — 반대가 하나라도 있으면 회의는 계속된다(앵커 독재
            폐지·게이트=채택). 표결은 회의록에 안 남는다(채택 결과만 결론으로 게시)."""
            if wakes["n"] >= wake_cap:
                return False
            def _rbody(c):
                return (f"[회의 — 수렴안 확정 표결] 주제: {topic}\n제출된 수렴안:\n{prop}\n\n"
                        f"이 수렴안으로 이 주기를 확정합니까? 당신({flow._info(c)})의 판단: 찬성이면 "
                        f"`[찬성]`, 이견이 있으면 `[반대: 무엇을 고쳐야 하는지 한 줄]`. **전원 찬성이어야 "
                        f"확정되며, 반대가 하나라도 있으면 회의가 계속됩니다.**")
            _yes, _no = 0, 0
            for m, res, note in await _fork_collect(flow, me_id, list(members), _rbody):
                wakes["n"] += 1
                t = str(res or "")
                if "반대" in t:
                    _no += 1
                elif "찬성" in t or _is_substantive(t):
                    _yes += 1
                if flow.log:
                    flow.log("consensus_ratify_vote", who=m, oppose=("반대" in t))
            return _no == 0 and _yes >= 1

        st = FloorState(members)
        if not _no_r1:
            for m in members[:-1]:
                st.record(Turn(speaker=m, body="(1R)"))     # R1 발언을 침묵 장부에 반영(오퍼 공정성)
        policy = (make_floor("turn-taking") if tt
                  else make_floor("orchestrated", allocator=round_robin([m for _, m in schedule])))
        # [§4] 완전 TT의 시작 턴 = 소집자 발제(내용 발화가 아니라 주제 제시) — 이후 전 발언이 응찰.
        _t0 = (Turn(speaker=me_id, body="(발제)") if _no_r1
               else Turn(speaker=members[-1], body="(1R 마지막 발언)"))
        # [게이트 = 채택된 수렴안(2026-07-14, 사용자: '회의에 상한 두지 말고 — 수렴안 채택돼야만 끝난다')]
        # 종료 조건을 '발언권 소진'이 아니라 '수렴안이 표결로 채택됨'으로 바꾼다. 파이프라인 TT 회의는
        # 한 패스 돌린다 → 이번에 [수렴안]이 제출됐나? 없으면 전원 발언권 되살려 재응찰(인위적 상한 없음).
        # 있으면 그 안에 전원 찬성 표결(_ratify_vote) → 통과하면 register_consensus로 채택·등록(GOAL.md
        # 생성) → 종료. 부결·등록거부면 회의 계속. 앵커 특권·거짓 완료·봇 파일작성 떠넘기기 없이 게이트가
        # 유일 출구. 비용 천장(wake_cap, 4배)에 닿으면 무의미 스핀 대신 정직히 상신(거짓 완료 아님).
        from collections import Counter
        from .milestone import register_consensus as _ms_reg
        _confirm_note = ""
        _landed_ms, _landed_units, _landed_new = None, 0, True   # 착지 마일스톤 — 회의 마무리 결론 게시용
        _pipe = bool(_no_r1 and tt)
        _pass = 0
        while True:
            _pass += 1
            _before = len(conv_props)
            await run_conversation(policy, st, _t0,
                                   _speak, bid=(_bid if tt else None),
                                   max_turns=(budget if tt else budget + 1), on_alloc=_on_alloc)
            _flush_minutes()
            if flow.current is None or not _pipe:
                break                                       # 솔로/orchestrated = 단일 패스(종전 동작)
            _fresh = conv_props[_before:]                   # 이번 패스에 제출된 수렴안 후보
            if _fresh:
                _top = Counter(_fresh).most_common(1)[0][0]
                if await _ratify_vote(_top):                # 전원 찬성이어야 채택
                    _pre_open = next((m.ms_id for m in (getattr(flow, "milestones", None) or [])
                                      if m.status not in ("done", "superseded")), None)
                    _ms, _n_st = _ms_reg(flow, _top, topic)  # 등록 + GOAL.md 생성(milestone.py)
                    if not isinstance(_ms, str):
                        _landed_ms, _landed_units, _landed_new = _ms, _n_st, (_pre_open != _ms.ms_id)
                        if _landed_new:
                            _confirm_note = (f"\n\n[표결 확정] 수렴안 채택(전원 찬성) → 마일스톤 {_ms.ms_id} "
                                             f"등록(조건 {len(_ms.criteria)}개, 단위 {_n_st}개) · GOAL.md 생성. "
                                             "각자 pick_backlog(desc='내가 할 일')로 자기 백로그를 전담하세요.")
                        else:
                            _confirm_note = (f"\n\n[표결 확정] 수렴안 채택 → 진행 중 주기 {_ms.ms_id}에 단위 "
                                             f"{_n_st}개 추가. 각자 pick_backlog(desc='내가 할 일')로 전담하세요.")
                        if flow.log:
                            flow.log("ms_confirm_by_vote", ms=_ms.ms_id, passes=_pass, subtasks=_n_st)
                        break                               # 채택 완료 — 회의 종료
                    # 채택됐지만 등록 품질 게이트가 보류(실증불가 조건 등) — 사유 남기고 회의 계속
                    if flow.log:
                        flow.log("ms_register_rejected", reason=str(_ms)[:80])
                    await _say_speech(flow, me_id, "[회의]",
                                      f"수렴안이 채택됐으나 등록 게이트가 보류했습니다 — {_ms} (다듬어 재수렴)")
                elif flow.log:
                    flow.log("meet_consensus_rejected", passes=_pass)
            elif flow.log:
                flow.log("meet_gate_unmet", passes=_pass)
            _gate_unmet["on"] = True                        # 재응찰: 종결표결에 게이트 전면화
            _t0 = Turn(speaker=me_id, body="(수렴안 미채택 — 회의 계속)")
            if wakes["n"] >= wake_cap:                      # 비용 바닥 — 상한 아님, 무한 무의미 스핀 방지
                if flow.log:
                    flow.log("meet_gate_exhausted", passes=_pass)
                break
        if flow.current is not None:
            record = f"[회의] {topic} ({rounds}R)\n" + "\n".join(minutes)
            flow.current.collab_notes = _speech_clip(
                (getattr(flow.current, 'collab_notes', '') + '\n\n' + record).strip(), 6000)
            _ckpt(flow)   # 합의는 크래시-세이프(재개 위임에도 동봉되도록 스냅샷에 포함)
        if _pipe and _landed_ms is None and not _confirm_note:
            # 게이트 미충족으로 비용 소진 종료 — 거짓 완료로 넘기지 않고 정직히 상신(사용자 확인 필요)
            if flow.log:
                flow.log("ms_consensus_empty", topic=str(topic)[:60], members=len(members))
            _confirm_note = ("\n\n[확정 실패 — 수렴 소진] 회의가 수렴안을 채택하지 못한 채 발언 예산을 "
                             "소진했습니다. **거짓 완료로 넘기지 않습니다** — 현재 팀 구성·요구로는 수렴이 "
                             "어렵습니다. 요구 명확화나 팀 재구성 등 사람 확인이 필요합니다.")
        # [회의 마무리 결론 게시(2026-07-14, 사용자: '회의를 접었을 때 발제된 이유와 결론이 보이면
        # 좋겠다')] 마일스톤 랜드마크([마일스톤 시작])는 회의 블록 밖 별도 메시지라, 회의가 접히면
        # 요약(topic+마지막 발언)에 결론이 안 보였다(마지막 발언=중간 토론). 수렴안이 착지하면 그
        # 결론(목표·로드맵·조건·단위)을 [회의 마무리] 발언으로 블록 안에 넣어, 접힌 회의가 '왜 열렸나
        # (발제 topic)+무엇으로 맺었나(결론)'로 읽히게 한다. collab_kind가 [회의 마무리]=meeting이라 같은 블록.
        if _landed_ms is not None:
            try:
                if _landed_new:
                    _rm = " → ".join(s[:24] for s in (getattr(flow, "roadmap", None) or [])[:6])
                    _cd = " · ".join((getattr(c, "desc", "") or "")[:40] for c in (_landed_ms.criteria or [])[:5])
                    _concl = (f"결론 — 이 주기 목표: {_landed_ms.goal[:120]}"
                              + (f"\n로드맵: {_rm}" if _rm else "")
                              + (f"\n완수조건: {_cd}" if _cd else "")
                              + (f"\n분해 단위 {_landed_units}개 → 각자 백로그로 전담" if _landed_units else ""))
                else:
                    _concl = (f"결론 — 진행 중 주기({_landed_ms.ms_id})에 분해 단위 {_landed_units}개 추가 "
                              "→ 각자 백로그로 전담")
                await _say_speech(flow, me_id, "[회의 마무리]", _concl)
                minutes.append(f"[회의 마무리] {flow._info(me_id) or me_id}: {_concl}")
            except Exception as _e:
                if flow.log:
                    flow.log("meet_conclusion_post_failed", err=str(_e)[:100])
        return (f"[회의록] 주제: {topic} ({rounds}라운드, {len(members)}명)\n"
                   + "\n".join(minutes)
                   + (_confirm_note if _no_r1 else
                      "\n\n(수렴·확정은 당신(리더)의 몫 — 합의점을 정리해 set_goal/결정에 반영하세요.)"))

    inner = asyncio.ensure_future(_run_meet())
    flow.inflight_tasks.add(inner)
    inner.add_done_callback(flow.inflight_tasks.discard)
    try:
        return await asyncio.shield(inner)
    except asyncio.CancelledError:
        if not inner.done():
            if flow.log:
                flow.log("delegation_detached", to="meet", seg=flow.leader_segment)

            def _hand(t):
                # [중지 레이스 가드(2026-07-14)] 사용자 중지로 detach된 회의가 취소되면 t.result()가
                # CancelledError(=BaseException, Exception 아님)를 던져 콜백이 이벤트루프에 미처리 예외로
                # 남았다(라이브 traceback). BaseException까지 삼켜 중지 시 조용히 무시한다.
                try:
                    flow.detached_results.append(f"회의 완료 → {_speech_clip(t.result()['content'][0]['text'], 4000)}")
                except BaseException:
                    pass
            inner.add_done_callback(_hand)
        raise







def _req_gate_spare(flow, to, tag):
    """[게이트] 직군 미배정(예비) 봇에게는 위임/질의 불가 — 말로 '너는 X야' 하고 일을 시키는 걸 구조적으로 막는다.
    먼저 recruit(role='직군')로 실제 직군을 부여해야 그 봇이 일할 수 있다(말로만 배정 차단).
    에러문자열 or None(통과)."""
    from .._util import _dbg
    if _is_spare(flow, to):
        _dbg(f"{tag} ✗거부:직군 미배정(예비)")
        return (f"요청 거부: {flow._info(to) or to}는 아직 직군 미배정('예비')입니다 — 말로 직군을 정하지 말고 "
                f"recruit(member='{to}', role='직군명')으로 직군을 실제로 부여한 뒤 요청하세요(직군이 부여돼야 일을 맡길 수 있음).")
    return None


def _req_gate_clarify_to_delegator(flow, me_id, to, kind, body, tag):
    """[게이트] 위임자에게 되묻기(확인요청 반환): 직속 위임자에게 Info로 물으면 '재진입 불가' 에러 대신
    베턴을 위임자에게 질문과 함께 돌려준다 — 위임자가 답하고 그 일을 다시 맡긴다(협업 가능).
    발동 시 pending_clarify·history를 세우고 안내문 반환. 에러문자열 or None(통과)."""
    from .._util import _dbg
    if kind == Kind.INFO and to == flow.comm.direct_delegator(me_id) and to != me_id:
        flow.pending_clarify = {"from": me_id, "to": to, "q": body}
        flow.comm.history.append(("clarify", me_id, to, "pending", Kind.INFO))
        _dbg(f"{tag} ↩확인요청→위임자")
        return (f"확인요청을 직속 위임자({flow._info(to)})에게 전달했습니다. 지금 이 턴을 즉시 "
                f"마치고(추가 도구 호출·추측 진행 금지) 짧게 반환하세요 — 위임자가 답한 뒤 이 작업을 "
                f"당신에게 다시 맡깁니다.")
    return None


async def _req_gate_team(flow, me_id, to, tag):
    """[게이트] 팀 소속 — 프로젝트 팀원이면 이 Task에 자동 합류(Task 내 관련 인원을 최소화할 이유는 없다).
    회사 풀에만 있으면 정보가 있는 거부(팀 내 같은 직군 대안 동봉), 풀에도 없으면 거부.
    [원인 교정 — 정보가 있는 거부] 리더가 회사 풀(전체 로스터)과 프로젝트 팀을 혼동해
    팀 밖 동료를 반복 호출하던 라이브 관측(7회 우회, SIGTERM 기억구멍이 증폭)의 뿌리:
    거부가 '안 된다'만 말하고 '그 직군이 팀에 누구인지'를 안 알려줘 같은 실수가 반복됐다.
    올바른 대안(팀 내 같은 직군)과 현재 팀 명단을 동봉해 첫 거부에서 바로 교정되게 한다.
    에러문자열 or None(통과)."""
    from .._util import _dbg
    if to not in flow.current.team:
        if to in flow.project_team:
            flow.current.team.append(to)
            flow.current.status.group = _group_of(flow, flow.current.team)
            await flow.refresh()
            _dbg(f"{tag} +Task자동합류(프로젝트팀원)")
            # [합류 가시화(2026-07-08, 사용자: '암묵적 봇 호출 — 갑자기 튀어나옴')] 팀 밖 봇이 위임으로
            # 조용히 합류하면 관찰자에겐 정체불명 발화로 보인다(라이브: 박지호 QA가 무표식 등장). 합류
            # 사실을 SYS 명의 한 줄로 피드에 남긴다 — 행동 무변경, 가시성만(best-effort).
            try:
                await flow.guide.post(int(flow.current.thread_id), 0,
                                      f"[합류] {flow._info(to) or ''}(id {to}) — 위임으로 이 Task에 합류.")
            except Exception:
                pass
        elif to in flow.pool:
            same = [m for m in flow.project_team
                    if m != me_id and not _is_spare(flow, m)
                    and ({_norm_job(j) for j in _jobs_of(flow._info(to) or "")}
                         & {_norm_job(j) for j in _jobs_of(flow._info(m) or "")})]
            alt = (" 같은 직군의 **팀 내 동료**: "
                   + ", ".join(f"{flow._info(m)}(id {m})" for m in same)
                   + " — 이들에게 요청하세요(재시도 금지)." if same else
                   " 팀에 그 직군이 없습니다 — 정말 필요하면 recruit(member=…, role=…)로 합류시킨 뒤 요청하세요.")
            _dbg(f"{tag} ✗거부:프로젝트밖")
            return (f"요청 거부: {to}({flow._info(to)})는 이 프로젝트 팀이 아닙니다 — 회사 풀에는 "
                    f"있지만 이 프로젝트 구성원이 아닙니다(팀은 create_project 때 당신이 구성했습니다)."
                    f"{alt} 현재 프로젝트 팀: {flow._names(flow.project_team)}")
        else:
            return f"요청 거부: {to}는 채용 풀에 없습니다. 풀: {flow._names(flow.pool)}"
    return None


async def _req_gate_serialize(flow, me_id, to, kind, body, tag):
    """[게이트] 직렬화 — 베턴이 내 차례가 될 때까지 대기(거부 아님). 서로 다른 동료로의 병렬 요청은 순차
    처리되며, 첫 요청이 길게(중첩 협의·긴 구현) 걸려도 베턴은 결국 돌아오므로 위임이 끊기지 않는다. 데드라인은
    교착 안전장치 — 게임처럼 한 동료가 10분+ 작업하는 경우까지 넉넉히(1시간) 둬 '활성=동료' 반려가
    안 뜨게 한다(이전 600초는 긴 작업 중 병렬요청이 타임아웃돼 무서운 '거부' 노이즈를 냈다).
    포함 순서: ① detach 완주 중 새 요청 즉시 안내 ② fork 동시성 가드 ③ 베턴 대기 루프
    ④ 같은 턴 병렬 중복 합침(idempotent) ⑤ 대기 한도 초과 소프트 보류. 에러문자열 or None(통과)."""
    from .._util import _dbg, _speech_clip
    import anyio
    import time
    # 직전 위임이 detach 상태로 완주 중이면(도구 호출은 포기됐지만 위임은 계속) 새 요청을 길게
    # 재우지 않고 즉시 안내한다 — 리더가 '보류' 헛돌이 대신 턴을 마치게(시스템이 완주 후 다시 깨움).
    if (any(not t.done() for t in getattr(flow, "inflight_tasks", ()))
            and flow.comm.alive != me_id and not flow.comm.done):
        return ("[대기] 직전 위임이 아직 진행 중입니다 — 추가 요청을 보내지 말고 이 턴을 간결히 "
                "마치세요. 위임이 완료되면 시스템이 그 결과와 함께 당신을 다시 깨웁니다.")
    # [fork 동시성 가드] 의견 수집(표결·회의 1R)이 도는 동안엔 새 요청을 보내지 않는다 — fork 중엔
    # 베턴(alive)이 리더에 머물러, CLI가 같은 턴에 병렬 도구 호출(vote+request)을 내면 수집 가지와
    # 같은 동료를 이중으로 깨워 '같은 봇 두 턴'(세션 충돌)이 될 수 있다(직렬 vote 시절엔 alive 이동이
    # 자연 차단). 수집은 조인이 보장돼 짧으므로 대기 안내가 정답.
    if getattr(flow, "fork_active", 0) > 0:
        return "[대기] 의견 수집(표결/회의)이 진행 중입니다 — 수집 결과를 받은 뒤 요청하세요."
    deadline = time.monotonic() + 3600
    while flow.comm.alive != me_id and not flow.comm.done and time.monotonic() < deadline:
        await anyio.sleep(0.05)
    # 같은 턴에 '같은 동료에게 같은 요청'을 다발로 보낸 병렬 중복은 합친다(idempotent): 동료를 다시
    # 깨우지 않고 직전 응답을 그대로 재사용한다 → 반사적 중복 wake 차단(직렬화는 유지, 중복만 제거).
    dupkey = (flow.leader_segment, me_id, to, str(getattr(kind, "value", kind)), body)
    if dupkey in flow.req_results:
        if flow.log:
            flow.log("dup_parallel_merged", frm=me_id, to=to,
                     kind=str(getattr(kind, "value", kind)), seg=flow.leader_segment)
        _dbg(f"{tag} ⇉병렬중복 합침(동료 재호출 없이 같은 응답 재사용)")
        return (f"[{to} 응답] {_speech_clip(flow.req_results[dupkey], 4000)}\n"
                f"(같은 턴에 이미 보낸 동일 요청 — 동료를 다시 호출하지 않고 같은 응답을 재사용)")
    # 대기 한도까지 베턴이 안 돌아옴(동료가 비정상적으로 오래 작업) — 규약위반이 아니므로 무서운 '거부'
    # 안내를 사용자에게 띄우지 않고 조용히 '보류'로 소프트 반환(리더는 응답 받은 뒤 다시 시도).
    if flow.comm.alive != me_id and not flow.comm.done:
        _dbg(f"{tag} ⏸보류:대기 한도 초과(활성={flow.comm.alive})")
        return (f"[보류] {flow._info(to) or to}가 아직 작업 중이라 지금은 보내지 않았습니다 — 그 동료의 "
                f"응답을 받은 뒤 다시 요청하세요(오류 아님).")
    return None


def _req_gate_protocol(flow, me_id, to, kind, tag):
    """[게이트] 규약 검증(check_request) — 검증→점유는 await 없이 인접 실행 → 형제 요청과 경합하지 않음(원자적).
    [전역 점유] BusyInOtherFlow는 규약 위반이 아니라 '그 동료가 지금 다른 흐름에서 일하는 중' — 무서운 '거부'
    대신 가용 대안(같은 직군 동료·채용)을 안내한다. 같은 동료 재시도(폴링)는 금지 문구로 차단.
    에러문자열 or None(통과)."""
    from .._util import _dbg
    try:
        flow.comm.check_request(me_id, to, kind)
    except BusyInOtherFlow as e:
        if flow.log:
            flow.log("req_busy_elsewhere", frm=me_id, to=to, holder=str(e.holder_scope or ""),
                     kind=str(getattr(kind, "value", kind)), seg=flow.leader_segment)
        _dbg(f"{tag} ⏸점유:타 흐름({e.holder_scope})")
        return (f"[동료 점유] {flow._info(to) or to}는 지금 다른 흐름({e.holder_scope})에서 일하는 "
                f"중입니다 — 같은 동료에게 재시도하며 기다리지 마세요(폴링 금지). "
                f"{_free_alternatives(flow, me_id, to)}.")
    except CommError as e:
        if flow.log:   # 관측: 거부 시점의 베턴 상태(alive)·요청자를 영속 기록 → 원인 규명
            flow.log("req_rejected", frm=me_id, to=to, kind=str(getattr(kind, "value", kind)),
                     alive=flow.comm.alive, seg=flow.leader_segment, reason=str(e)[:70])
        _dbg(f"{tag} ✗거부:규약 ({e})")
        return f"요청 거부(규약): {e}"
    return None


def _req_gate_goal(flow, kind, goal, tag):
    """[게이트] Work 위임은 Goal 확정 뒤에만 — '목표 합의(set_goal) → 분배' 순서를 구조적으로 강제(선분배 금지).
    Info(합의용)는 언제든 허용 → Goal을 정하는 논의 자체는 막지 않는다. 에러문자열 or None(통과)."""
    from .._util import _dbg
    if kind == Kind.WORK and not goal:
        _dbg(f"{tag} ✗거부:Goal미확정")
        return ("Work 위임 거부: 이 Task의 Goal이 아직 확정되지 않았습니다. 먼저 동료와 request(Info)로 "
                "목표를 합의하고 set_goal로 확정한 뒤 Work로 맡기세요(목표는 팀 합의의 산물 — 선분배 금지).")
    return None


def _req_gate_owner_protect(flow, me_id, to, kind, body, args, tag):
    """[게이트] G1 — 미완 owner 보호(B-04, 축소판). 미완 owner(owner_incomplete)가 있는데 *타인*에게 fresh-Work를
    보내면 — (a) 그 타인이 owner와 직군 교집합이거나 (b) 본문이 owner 위임 원문(last_work_body)과 실질 중복일
    때만 — 거부한다(복구 직후 owner 덮어쓰기·작업/보고 유실의 재발점 ①을 프롬프트("★절대 새로 위임 금지")가
    아니라 구조로 차단). 다도메인 단일 Task 수렴의 정상 위임(타직군·별산출물)은 통과. owner_delivered=True
    (인도 후 검증 위임)는 비대상 — 교차검증 무영향. SYS 내부 발사(_auto_coordinate)는 _sys_dispatch로 우회
    (거부 시 조율 큐 조용한 유실 방지). 탈출 2갈래: takeover_reason(교체)/different_deliverable(별산출물) —
    의식적 명시 시 per-Task 통과 기록(_gate_pass). owner_protect_checked는 테스트 우회 플래그.
    에러문자열 or None(통과)."""
    from .._util import _dbg
    from .task import _ckpt
    if (kind == Kind.WORK and flow.current is not None and flow.current.owner
            and getattr(flow.current, "owner_incomplete", False)
            and to != flow.current.owner
            and not getattr(flow.current, "owner_delivered", False)
            and not getattr(flow, "_sys_dispatch", False)
            and not getattr(flow, "owner_protect_checked", False)
            and ("owner_protect", flow.current.task_id) not in flow._gate_pass):
        _own_jobs = {_norm_job(j) for j in _jobs_of(flow._info(flow.current.owner) or "")} - {""}
        _to_jobs = {_norm_job(j) for j in _jobs_of(flow._info(to) or "")} - {""}
        _dup = bool(_own_jobs & _to_jobs) or _body_overlap(body, getattr(flow.current, "last_work_body", ""))
        if _dup:
            _esc = (str(args.get("takeover_reason") or "").strip()
                    or str(args.get("different_deliverable") or "").strip())
            if _esc:
                flow._gate_pass.add(("owner_protect", flow.current.task_id))
                _ckpt(flow)
                if flow.log:
                    flow.log("owner_protect_override", to=to, reason=_esc[:60], seg=flow.leader_segment)
            else:
                if flow.log:
                    flow.log("owner_protect_blocked", to=to, owner=int(flow.current.owner),
                             seg=flow.leader_segment)
                _dbg(f"{tag} ✗거부:미완 owner 보호(G1)")
                _own_name = flow._info(flow.current.owner) or flow.current.owner
                return (
                    f"위임 보류(미완 owner 보호): 이 Task의 owner({_own_name})가 **미완(이어가기 대기)** 상태인데 "
                    f"같은 일로 보이는 fresh-Work를 다른 동료에게 보내려 합니다 — 그 owner의 진행분·보고가 "
                    f"덮여 유실됩니다. 셋 중 하나로: ① (권장) **같은 owner({_own_name})에게 request(Work)로 "
                    f"'이어서'**를 보내 남은 부분을 마저 끝내게 하세요(진행분 보존). ② 정말 담당 교체가 필요하면 "
                    f"인자 **takeover_reason='<사유>'**를 함께 보내세요(의식적 교체). ③ 이 위임이 별개 산출물이면 "
                    f"인자 **different_deliverable='<무엇이 다른지>'**를 함께 보내세요.")
    return None


def _req_gate_loop_escalated(flow, to, kind):
    """[게이트] 회로차단기 정지(2026-06-23 S1a 보강) — 경보는 '멈추게'도 해야 한다. loop_escalated가 켜졌는데도
    검증 cross-check Work가 또 들어오면(=사람이 아직 판정 안 함) 새 검증 워커를 띄우지 않는다. 종전
    회로차단기는 경보만 1회 띄우고 흐름은 그대로 루프→사람 부재 시 밤새 토큰을 태웠다(라이브 P-031: 경보
    후에도 검증 계속). 검증 위임을 *보류*하고 '① complete_task 마감 / ② 사용자 방향 제시'를 기다린다.
    owner 수정 Work·complete_task는 안 막으므로 데드락이 아니다(리더가 언제든 마감으로 빠져나갈 수 있음).
    정상 e2e는 12회 전에 수렴하므로 이 블록에 닿지 않는다(병리적 루프에서만 작동). 에러문자열 or None(통과)."""
    from .task import _is_verifier
    if (kind == Kind.WORK and flow.current and getattr(flow.current, "loop_escalated", False)
            and _is_verifier(flow._info(to) or "") and int(to) != (flow.current.owner or -1)):
        if flow.log:
            flow.log("loop_escalated_block", to=to, cross=flow.current.cross_checks)
        return (
            f"[수렴 경보 — 검증 보류] 이 Task는 교차검증 {flow.current.cross_checks}회로 *사람 판정 대기 중*입니다 "
            f"(이미 사용자에게 에스컬레이트됨). 추가 검증을 띄우지 마세요 — 같은 문제를 반복 검증하는 루프입니다. "
            f"**① 검증이 충분하면 complete_task로 마감**하거나, **② 사용자가 방향을 제시할 때까지 기다리세요**. "
            f"(코드를 *고친* 뒤의 재검증·다른 작업은 사용자 개입으로 경보가 해제된 뒤 가능합니다.)")
    return None


def _req_gate_reverify(flow, to, kind):
    """[게이트] 검증 종료상태 — 재검증 dedup(2026-06-23 전수감사, 사용자 '검증 집계'; 리뷰F1 교정). *이미 이 산출물을
    독립검증한 그 검증자*(to in cross_checkers)에게, *코드가 변경되지 않았는데*(writes 불변) 또 검증을 맡기려
    하면 막는다 — 복구마다·결함 못 고친 채 "최종 검증"을 반복 요청하던 무한 루프(P-031 ~13회, 1346 run) 차단.
    ※ 리뷰F1: 'to in cross_checkers'로 좁혀 — *아직 검증 안 한* 검증자에게 새 작업·새 검증을 시키는 건 통과
    (검증자에게 새 Work 주는 것까지 막던 회귀 차단). 코드를 *고친 뒤*(writes 증가)·*첫* 검증도 통과.
    에러문자열 or None(통과)."""
    from .task import _is_verifier
    if (kind == Kind.WORK and flow.current and _is_verifier(flow._info(to) or "")
            and int(to) in getattr(flow.current, "cross_checkers", set())
            and getattr(flow.current, "last_verify_writes", -1) >= 0
            and sum(int(v) for v in (flow.writes_by_role or {}).values()) == flow.current.last_verify_writes
            and not getattr(flow, "reverify_checked", False)):
        if flow.log:
            flow.log("reverify_dedup", to=to, cross=flow.current.cross_checks)
        return (
            f"재검증 보류(이 검증자는 이미 이 코드를 검증함 — 변경 0): {flow._info(to) or to}는 이미 이 산출물을 "
            f"독립 교차검증했고(팀 교차검증 {flow.current.cross_checks}회), 그 뒤 **코드가 한 줄도 안 바뀌었습니다**"
            f"(Write/Edit 0). 같은 검증자에게 같은 코드를 또 검증시키는 건 무한 '최종 검증' 루프입니다 — 둘 중 "
            f"하나로 진행하세요: ① 검증이 충분하면 **complete_task로 마감**(교차검증 게이트는 이미 통과). ② 검증에서 "
            f"나온 결함이 있으면 그 owner에게 Work로 ***고치게* 한 뒤**(코드가 바뀌면) 다시 검증하세요. (아직 검증 "
            f"안 한 *다른* 검증자에게 맡기거나, 검증자에게 *새 작업*을 주는 건 막지 않습니다.)")
    return None


def _req_gate_crossdomain(flow, me_id, to, kind, goal, body, me_is_leader, tag):
    """[게이트] 비-리더 교차도메인 Work — 구조적 조율 단일화(2026-06-22, 사용자: '주어진 일과 무관한 일을
    다른 도메인에 시키는 이상한 협업'은 구조 문제다). 비-리더는 *받은 일*을 한다 — 같은 도메인 동료에게
    분담(서브태스킹)하거나 검증자(QA)에게 검증을 맡기는 건 자유고, 막히거나 궁금한 건 request(Info)로
    어느 도메인 전문가에게든 *자문*(자유·권장)한다. 그러나 *다른 도메인의 새 Work*를 직접 여는 것은
    리더의 조율 역할이다(SINGLE FLOW·중앙 조율). 프롬프트로 '하지 마'가 아니라 구조로 막고 리더로 보낸다.
    검증·자문을 막는 게 아니라 '의미없는 교차도메인 Work 위임'만 막는다(사용자 설계 방향).
    [회귀 교정(2026-06-23, 사용자: '대화가 난장판') — 'not same_domain' 블랭킷 차단 제거] 단지 도메인이
    다르다는 이유만으로 정상 교차도메인 협업(적임자에게 위임)까지 막아 리더 조율 큐로 보냈고, 그게
    '[SYS 조율 — 막혀 배정]'·'[직군초과]' 난장판의 뿌리였다(라이브: crossdomain_blocked 140건 중 다수가
    caps=[] = 능력 미스매치 없는 false-positive — 프론트엔드→데이터엔지니어 정상 협업까지 차단). 설계
    의도는 '의미없는 교차도메인 위임만 차단'인데 구현이 *모든* 교차도메인 Work를 막았다. 진짜 능력
    미스매치(cap_hit — 그 능력을 가진 다른 전문가가 있는데 못 가진 이에게 맡김)일 때만 리더로 돌린다.
    에러문자열 or None(통과)."""
    from .._util import _dbg
    from .task import _is_verifier
    if (kind == Kind.WORK and goal and not me_is_leader and to != flow.leader
            and not getattr(flow, "crossdomain_checked", False)):
        my_jobs = {_norm_job(j) for j in _jobs_of(flow._info(me_id) or "")} - {""}
        to_jobs = {_norm_job(j) for j in _jobs_of(flow._info(to) or "")} - {""}
        to_verifier = _is_verifier(flow._info(to) or "")
        cap_hit = _offdomain_capability_hit(flow, to, body)   # 같은 도메인이라도 내 도메인 밖 능력 요구면 hit
        if cap_hit and not to_verifier:
            if flow.log:
                flow.log("work_crossdomain_blocked", frm=me_id, to=to, my=sorted(my_jobs),
                         to_jobs=sorted(to_jobs), caps=list(cap_hit.keys()), seg=flow.leader_segment)
            _dbg(f"{tag} ✗보류→리더조율큐:비리더 교차도메인")
            # [리더 조율 강제(2026-06-23, 사용자)] 막힌 교차도메인 Work를 그냥 거부하지 않고 '리더 조율
            # 큐'에 적재한다 — 워커가 이를 '핑계'로 보고하고 리더가 묵살·재발사하던 라이브 루프(P-030
            # backend2↔PM 핑퐁)를 끊기 위함. sys_core continue 루프가 이 큐를 리더 다음 턴에 'SYS 확인
            # 사실'로 주입해 리더가 *직접* 그 도메인 전문가에게 위임하게 한다. 같은 (요청자→대상)은 중복 적재 X.
            try:
                if not any(c.get("requester") == me_id and c.get("to") == to
                           for c in flow.pending_coordination):
                    flow.pending_coordination.append({
                        "requester": me_id, "req_role": flow._info(me_id) or str(me_id),
                        "to": to, "to_role": flow._info(to) or str(to),
                        "to_jobs": sorted(to_jobs), "body": (body or "")[:500]})
            except Exception:
                pass
            return (
                f"위임 보류(교차도메인 — **조율 큐로 이관됨**): 당신({flow._info(me_id)})은 다른 도메인의 "
                f"새 작업을 직접 맡길 수 없어, 이 요청을 **앵커에게 조율 사안으로 올렸습니다** — 앵커가 그 도메인 "
                f"전문가에게 직접 배정합니다. 지금 이 턴은 **당신 도메인의 일을 계속**하세요(막힌 그 부분은 앵커가 "
                f"처리하니 기다리거나 다른 동료에게 떠넘기지 마세요). 질문·QA 검증은 그대로 자유입니다.")
    return None


def _req_gate_offdomain(flow, to, kind, goal, body, me_is_leader):
    """[게이트] 직군밖 사전 차단 — 리더 라우팅. 능력표로 *위임 전에* 능력 미스매치를 잡아 그 전문가에게 리다이렉트
    (흡수의 씨앗 차단). 리더는 조율 권한이 있어 직접 적임자에게 보낸다(비-리더는 교차도메인 게이트가
    이미 리더로 돌렸다). 상세·근거는 _offdomain_capability_hit 참고. offdomain_checked는 테스트 우회 플래그.
    에러문자열 or None(통과)."""
    if kind == Kind.WORK and goal and me_is_leader and not getattr(flow, "offdomain_checked", False):
        _hit = _offdomain_capability_hit(flow, to, body)
        if _hit:
            if flow.log:
                flow.log("work_offdomain_blocked", to=to, caps=list(_hit.keys()), seg=flow.leader_segment)
            _who = "; ".join(f"{n} → {flow._names(ms)}" for n, ms in _hit.items())
            return (
                f"위임 거부(직군밖 — 능력 미스매치): 이 작업은 **{', '.join(_hit)}** 능력이 필요한데 "
                f"{flow._info(to) or to}의 직군 밖입니다. 그 능력을 가진 전문가가 팀에 있습니다 — {_who}. "
                f"**그 전문가에게 위임**하세요(범용·비전문이 떠안으면 흡수 — placeholder 품질). 정말 {to}가 "
                f"맡아야 할 합당한 이유가 있으면 body에 '[직군초과: <사유>]'를 적어 다시 보내세요.")
    return None


async def request(flow, me_id, role, args):
    """[Rule 로직] request — guide_tools에서 이관(평문 반환, @tool이 _ok 래핑).
    [M7 게이트 파이프라인화] 전제 게이트들은 모듈 프라이빗 헬퍼(_req_gate_* — 에러문자열 or None)의
    순차 호출 파이프라인. 게이트 순서·조건·에러문자열은 추출 전과 완전 동일하며, 첫 에러가 곧 반환이다.
    게이트 수정 시 해당 헬퍼만 편집하면 된다 — 순서 변경은 이 본문에서. 게이트 통과 뒤는 실행부:
    프레임 생성(Redo/신규)·위임 계약(owner_body) 합성·전송·기록·_deliver(위임 완주, detach-safe)."""
    from .._util import _looks_transient
    from .._util import _dbg, _ok, _react, _speech_clip
    from ..protocol import Kind
    from .task import _LOOP_ESCALATE_CROSS, _ckpt
    import asyncio
    import re
    import time
    g = flow.guide
    to = int(args["to_id"])
    kind = Kind.WORK if str(args["kind"]).strip().lower().startswith("w") else Kind.INFO
    body = args["body"]
    # [B-16 — 게이트 마커 인자화(이중 수용: 인자 > regex, regex 폴백 존치)] override_reason 인자를 종전
    # '[직군초과: 사유]' 마커로 합성 — 이후 게이트(_offdomain_capability_hit)·기록이 종전 경로 그대로 소비.
    _ovr = str(args.get("override_reason") or "").strip()
    if _ovr and "[직군초과" not in (body or ""):
        body = f"[직군초과: {_ovr}] {body}"
    tag = f"[REQ] {me_id}({flow._info(me_id)})→{to}({flow._info(to)}) {getattr(kind, 'value', kind)}"
    if flow.current is None:
        _dbg(f"{tag} ✗거부:Task없음")
        return _ok("오류: 진행 중인 Task가 없습니다. (create_task로 먼저 여세요.)")
    _msg = _req_gate_spare(flow, to, tag)
    if _msg:
        return _ok(_msg)
    # [G2 — clarify 행동 잠금(B-02)] 내가 되묻기를 남긴 상태면 답을 받기 전 추가 요청(추측 진행) 금지.
    _hold = _clarify_hold(flow, me_id)
    if _hold:
        _dbg(f"{tag} ✗잠금:clarify 대기")
        return _ok(_hold)
    _msg = _req_gate_clarify_to_delegator(flow, me_id, to, kind, body, tag)
    if _msg:
        return _ok(_msg)
    _msg = await _req_gate_team(flow, me_id, to, tag)
    if _msg:
        return _ok(_msg)
    if flow.wake is None:
        return _ok("오류: 시스템 준비 안 됨")
    _msg = await _req_gate_serialize(flow, me_id, to, kind, body, tag)
    if _msg:
        return _ok(_msg)
    # 같은 턴 병렬 중복 합침의 캐시 키 — _deliver가 응답 저장에 쓴다(직렬화 게이트 통과 직후라 값 동일).
    dupkey = (flow.leader_segment, me_id, to, str(getattr(kind, "value", kind)), body)
    _msg = _req_gate_protocol(flow, me_id, to, kind, tag)
    if _msg:
        return _ok(_msg)
    goal = (flow.current.status.goal or "").strip()
    _msg = _req_gate_goal(flow, kind, goal, tag)
    if _msg:
        return _ok(_msg)
    me_is_leader = (me_id == flow.leader)
    _msg = _req_gate_owner_protect(flow, me_id, to, kind, body, args, tag)
    if _msg:
        return _ok(_msg)
    _msg = _req_gate_loop_escalated(flow, to, kind)
    if _msg:
        return _ok(_msg)
    _msg = _req_gate_reverify(flow, to, kind)
    if _msg:
        return _ok(_msg)
    _msg = _req_gate_crossdomain(flow, me_id, to, kind, goal, body, me_is_leader, tag)
    if _msg:
        return _ok(_msg)
    _msg = _req_gate_offdomain(flow, to, kind, goal, body, me_is_leader)
    if _msg:
        return _ok(_msg)
    # [마일스톤 파이프라인 §3 — 위임축 접점(S2, 통합주기 2)] Work 위임을 백로그 릴레이의 장부·턴
    # 규칙에 맞춘다(배분권=마무리자·겹침 방지·participants/backlog_ids 동기). 플래그 OFF면 즉시
    # None(기존 동작 불변) — 백로그와 무관한 위임도 그대로 통과(릴레이는 전면 강제가 아니라 장부).
    if kind == Kind.WORK:
        from .backlog import sync_delegation as _bl_sync
        _msg = _bl_sync(flow, me_id, to, body)
        if _msg:
            return _ok(_msg)
    # Work Response → Accept/Redo (docs Communication.md §5). 이미 이 owner가 '완료 응답'까지 낸
    # 산출물을 같은 위임자가 또 Work로 보내면, 그건 '새 위임'이 아니라 직전 산출물의 Redo다.
    # → 새 프레임이 아니라 redo()로 처리한다(한계까지만, 초과 시 반복 위임 거부). 이로써 '되풀이
    #   위임'이 구조적으로 '직전 결함을 고치는 보완'으로만 성립한다(반사적 중복요청 차단·정당한 보완 허용).
    is_redo = kind == Kind.WORK and flow.comm.delivered_work(me_id, to)
    owner_body = body
    if is_redo:
        try:
            frame = flow.comm.redo(me_id, to, "pending", body=body)    # 베턴 점유 + Redo 카운트(한계 시 RedoLimitExceeded)
        except RedoLimitExceeded:
            _dbg(f"{tag} ✗재위임 한도초과")
            # [품질>토큰 — 리더 셀프 마무리 권유 제거] 종전 안내("직접 Write/Edit로 마무리")는
            # Redo 실패의 끝에서 중앙집권·비전문 마감을 권하는 셈이었다(탈중앙·전문화 역행).
            return _ok(f"재위임 거부(Redo 한도 초과): {to}({flow._info(to)})는 이미 이 산출물을 여러 번 "
                       f"보완했습니다. 같은 사람에게 같은 식으로 또 떠넘기지 마세요 — 품질 경로는: "
                       f"① 검증자(타 멤버)의 결함 보고로 **무엇이 왜 미달인지 정밀화**해 마지막 1회를 명확히 맡기거나 "
                       f"② 같은 직군의 **다른 전문가**(없으면 recruit)에게 결함 보고와 함께 맡기거나 "
                       f"③ goal이 이미 충족이면 complete_task, 끝내 미달이면 사용자에게 정직하게 보고하세요"
                       f"(비전문 직접 마무리로 덮지 말 것).")
        owner_body = (f"[보완 요청(Redo) — 직전 산출물이 목표에 못 미쳐 되돌아왔습니다] 고칠 구체적 결함: {body}\n"
                      f"[이 Task의 Goal] {goal}\n결함만 정확히 고치고 run으로 재검증해 그 증거와 함께 보고하세요.")
    else:
        frame = flow.comm.request(me_id, to, "pending", kind, body=body)   # 베턴 점유(alive→to) + 원문(정밀복구)
        if kind == Kind.WORK:
            # 위임의 '계약'은 리더가 매번 새로 쓰는 스펙이 아니라 팀 합의로 확정된 Goal이다(스펙 리파인
            # 루프=재요청의 뿌리를 끊는다). owner가 그 목표를 끝까지(구현+검증) 책임진다.
            owner_body = (f"[위임 — 이 목표를 끝까지 책임지는 owner는 당신입니다] 이 Task의 Goal: {goal}\n"
                          f"직접 구현하고 run으로 '목표가 충족됨'을 검증한 뒤(리더에게 되넘기지 말 것), "
                          f"그 실행 증거와 함께 간결히 보고하세요.\n"
                          f"큰 목표는 **수직 슬라이스 우선**: '끝까지 관통하는 최소 동작 버전'을 먼저 만들어 "
                          f"검증하고 그 위에 살을 붙이세요 — 마지막 통합 몰빵 금지(오차를 일찍 드러내는 것이 "
                          f"빠른 길입니다. RFC-005: 검증 신호는 연속적이어야 한다).\n"
                          f"보고는 다음 골격으로(보고 계약 — 받은 쪽이 산출물을 재탐색하지 않아도 되게): "
                          f"[결과] 한 줄 결론(완료/부분/실패) / [변경] 파일·핵심 변경 목록 / "
                          f"[검증] 방법→결과 / [리스크] 남은 것·주의점.\n"
                          f"단, 이 Goal에 **당신 직군의 전문성으로 만드는 게 아닌 범주**가 섞여 있으면 — "
                          f"코드로 흉내낼 수 있다고 당신 일인 게 아닙니다('할 수 있다'와 '그 분야 전문성으로 "
                          f"잘한다'는 다릅니다 — 비전문 자급은 placeholder일 뿐) — 어설프게 떠안지 말고 보고 "
                          f"**첫 줄**에 `[직군밖] 필요직군명` 을 적어 반려하세요. 리더가 그 직군을 채용하거나 "
                          f"실제 제작 자원으로 충족합니다(전문화 원칙: '구현 가능'이 아니라 '전문성 정합'으로 판단).\n"
                          f"[요청 맥락] {body}")
            # [B-10 Phase B — 위임 계약 dedup(ORGANT_DOC_COLLAB=1, 기본 off=기존동작)] 절감의 본체는
            # '같은 세션 재주입'이다: 멤버 세션 *첫 wake엔 전문 1회 push*(pull-risk 실측 0% — 참조만 주면
            # 안 읽는다), 같은 흐름의 재주입(Redo·이어가기)부터 기계 생성 다이제스트+문서 참조로 dedup.
            # 문서(Dossier)가 실재할 때만 참조화한다 — '표기만 있고 접근 불가' 금지(§17 정신).
            from .._util import doc_collab_on, dossier_read, dossier_rel
            _dedup = (doc_collab_on() and to in getattr(flow, "collab_pushed", set()))
            iface = (getattr(flow.current, "interfaces", "") or "").strip()
            if iface:
                # [협업 — 인터페이스 직접 전달·합의(2026-06-22 사용자: '전문가끼리 서로 대화하는가')] 종전엔
                # interfaces가 Task에만 저장되고 owner에게 전달 안 돼(여기 누락) owner가 계약을 못 보고 추측
                # → 통합 불일치(P-028 API 미스매치). 이제 계약을 owner에게 주고, 맞물리는 부분은 그 도메인
                # owner에게 *직접 request(Info)*로 확인하게 한다(리더 중계·추측 금지).
                if doc_collab_on() and dossier_read(flow, "GOAL.md") is not None:
                    # [B-10] 플래그 on + GOAL.md 실재: 관련 계약 ≤400자 + GOAL.md 참조(원본).
                    owner_body += (f"\n[도메인 간 인터페이스 계약 — 준수]\n{_speech_clip(iface, 400)}"
                                   f"\n(계약 전문·목표 원본: 작업공간 "
                                   f"{dossier_rel(flow.current.task_id)}/GOAL.md 를 Read)")
                else:
                    owner_body += f"\n[도메인 간 인터페이스 계약 — 준수]\n{_speech_clip(iface, 1500)}"
                owner_body += (f"\n[직접 합의 — 리더 중계 금지] 당신 작업이 다른 도메인과 맞물리면(데이터 포맷·"
                               f"API·이벤트 타이밍 등) 추측하거나 리더에게 되묻지 말고 **그 도메인 owner에게 "
                               f"직접 request(Info)**로 계약을 확인·합의하세요 — 전문가끼리 직접 소통합니다.")
            notes = getattr(flow.current, "collab_notes", "")
            if notes and _dedup and dossier_read(flow, "MINUTES.md") is not None:
                # [B-10] 재주입 dedup: 변경분 다이제스트(기계 생성 — 회의록 꼬리 + acceptance 원문,
                # 리더-요약 금지 A-2) + MINUTES.md 참조. 루브릭(아래)은 전문 유지(A-3 — 참조화 기각).
                _tail = notes if len(notes) <= 1200 else "…" + notes[-1200:]
                _acc = (getattr(flow.current, "acceptance", "") or "").strip()
                owner_body += (f"\n[팀 협의 — 변경분 다이제스트(전문은 이 세션 첫 위임에서 이미 받음)]\n{_tail}"
                               + (f"\n[수용 계약(acceptance) — 원문]\n{_acc}" if _acc else "")
                               + f"\n(회의·표결 전문 원본: 작업공간 "
                                 f"{dossier_rel(flow.current.task_id)}/MINUTES.md 를 Read)")
            elif notes:
                # [스펙 증발 방지] 회의·표결의 합의는 리더 머릿속이 아니라 위임 계약에 실린다 —
                # 라이브 P-009: 9직군이 회의로 정한 스펙(상태머신·SLA·타이밍 계약)이 구현자에게
                # 전달되지 않아(스코프 단절·리더 요약 의존) 결과물 품질로 이어지지 못함.
                owner_body += f"\n[팀 협의 기록(회의·표결) — 구현·검증 시 이 합의를 준수]\n{_speech_clip(notes, 6000)}"   # 저장 한도(6000)와 일치 — 전달에서 합의가 또 잘리지 않게(품질>토큰)
                if doc_collab_on():
                    flow.collab_pushed.add(to)   # [B-10] 첫 wake 전문 push 완료 — 재주입부터 dedup
            # [RFC-008 P0 — 검증 위임에 루브릭 자동 주입] owner 인도 후 '다른 멤버'에게 가는 Work =
            # 검증 위임 → owner 산출물 도메인의 직무 기준을 루브릭으로 동봉. 라이브 P-010 1차에서 루브릭이
            # complete_task 거부 메시지에만 있어 0회 발동(검증이 카운트되면 게이트를 안 탐) — 검증자에게
            # 직접 주입해야 'owner 도메인 기준 채점'이 실제로 일어난다. '돌아가는가'가 아니라 '충분한가'.
            if (getattr(flow.current, "owner_delivered", False) and flow.current.owner
                    and to != flow.current.owner and callable(getattr(flow, "craft_of", None))):
                owner_job = (flow._info(flow.current.owner) or "").strip()
                rub = [flow.craft_of(j) for j in owner_job.split("·") if j.strip()]
                rub = [r for r in rub if r]
                if rub:
                    # [발견2 완화] owner 인도 후 타 멤버 Work가 '검증'인지 '후속 구현'인지 구조로 완벽히
                    # 구분 불가(의도의 문제) — 메시지가 양쪽을 다 커버해 오발동을 무해화한다: 검증 위임이면
                    # 채점, 후속 구현이면 같은 기준을 '참고'(통합 시 품질 인식). 어느 쪽이든 owner 도메인
                    # 기준이 주입되는 건 손해가 아니다('충분한가'의 눈을 공유).
                    owner_body += (f"\n[산출물 품질 기준 — '{owner_job}' 도메인. 이 요청이 **검증**이면 산출물을 "
                                   f"'사용자처럼 실제로 사용·플레이'하며 아래 각 항목을 충족/미달로 채점하고 미달은 "
                                   f"구체적 결함으로 보고하세요(돌아가는가 아니라 '충분한가'). 이 요청이 **후속 "
                                   f"구현/통합**이면 아래 기준을 참고해 같은 품질 수준을 맞추세요:\n"
                                   + _speech_clip("\n---\n".join(rub), 2500))
            # [배포 신선도 — 버전 정체성(2026-07-08, 사용자: '로컬을 서버에 적용 안 했을 뿐인데 재작성하려
            # 했다')] 검증·회의가 '라이브'를 현재 코드로 앵커하는데 라이브≠로컬(미배포 변경)이면 옛 버전의
            # 결함을 현재 결함으로 오진해 이미 고친 걸 재작성한다(라이브 P-005: 로컬에 있는 단일축 수정을
            # 뿌리째 재구현 위임 — 사람이 우연히 diff 떠서야 정정). 시스템이 아는 사실(_deploy_writes vs
            # 현재 저작수)을 위임에 기계 주입 — 봇이 진단 전에 버전부터 확인하게.
            _dw = getattr(flow, "_deploy_writes", -1)
            if getattr(flow, "_deployed_once", False) and _dw >= 0:
                _cw = sum(int(v) for v in (flow.writes_by_role or {}).values())
                if _cw > _dw:
                    owner_body += (f"\n[배포 신선도 경고 — 시스템 실측] 마지막 배포 이후 로컬 변경 "
                                   f"{_cw - _dw}건이 **라이브에 미반영**입니다. 라이브에서 보는 동작·코드는 "
                                   f"옛 버전일 수 있습니다 — **결함 진단·검증 전에 로컬과 라이브가 같은 버전인지"
                                   f"(diff/바이트) 먼저 확인**하세요. 라이브에서 재현된 결함이 로컬에 이미 "
                                   f"고쳐져 있으면 그건 코드 결함이 아니라 *배포 지연*입니다(재작성 금지 — "
                                   f"배포·확인이 처방).")
            # [회귀 보존 — 이미 검증 통과한 산출물(2026-07-08)] 산출물이 이전 라운드에 교차검증을 통과한
            # 적 있으면(cross_checks>0) 이번 수정/검증은 *이미 되던 것*을 깨지 않는지 함께 봐야 한다 —
            # 한 기준을 고치며 다른 기준을 깨는 반쪽수정(오실레이션)이 반복의 원인. 완료 시점의 버전인식
            # acceptance 재검증(task_gates)과 짝을 이루는 *수정 시점* 사전 경고 — '같은 직군이 뭘 했는지
            # 안 본다'의 예방판(발동은 prior 검증이 있을 때만이라 첫 인도엔 무발동, 노이즈 0).
            if getattr(flow.current, "cross_checks", 0) > 0:
                owner_body += (f"\n[회귀 보존 — 이미 검증 통과한 산출물({flow.current.cross_checks}회 교차검증)] "
                               f"이 산출물은 이전 라운드에 검증을 거쳤습니다. 당신의 작업이 **이미 되던 것을 깨지 "
                               f"않는지** 반드시 함께 확인하세요 — 한 기준을 고치며 다른 기준을 깨는 *반쪽수정*이 "
                               f"이 산출물이 안 닫히던 원인입니다. 인도(또는 검증) 전에 **acceptance 전 항목이 현재 "
                               f"버전에서 동시에 충족**되는지 회귀 확인하세요(고친 부분만이 아니라 전에 되던 것까지).")
            if flow.log:
                # [B-09 Phase A 관측 지표] 위임 주입 자수 — '흐름당 재주입 자수·dedup 절감'(§4 정량
                # 재확인, B-10 되돌림 조건의 베이스라인)을 flow.jsonl에서 직접 계산할 수 있게.
                flow.log("work_inject", chars=len(owner_body), dedup=bool(notes and _dedup),
                         to=to, seg=flow.leader_segment)
    thread_id = flow.current.thread_id
    # Owner = 그 일을 Work로 받은 동료(수신=소유). 선배정이 아니라 요청으로 owner가 떠오른다 —
    # 이 Task에 아직 owner가 없을 때 첫 Work-request 수신자가 책임자가 된다(중앙집권 방지).
    if kind == Kind.WORK and not flow.current.owner:
        flow.current.owner = to
        flow.current.status.owner = flow._info(to) or f"<@{to}>"
        await flow.refresh(flow.current)
        _ckpt(flow)                       # 크래시-세이프: owner 확정 영속(복구 때 같은 담당이 잇게)
    req = await g.send_request(thread_id, me_id, to, kind, body)
    frame.request_id = str(req)                              # 실제 메시지 id로 기록 갱신
    if kind == Kind.WORK and flow.current:
        flow.current.work_delegated_to.add(to)   # 누가 위임했든(리더든 peer든) 'Work를 실제로 받은' 멤버 기록
        if to == flow.current.owner:
            # [정밀 복구] owner에게 보낸 Work 원문 보관(레벨1 fallback).
            flow.current.last_work_body = body
        if me_id == flow.leader:
            flow.current.work_delegated += 1   # 리더의 구현 위임 카운트 — 0이면 '자문만 받고 독식'(권한 훅이 차단)
        # [정밀 복구 — 체인 깊이 영속] 모든 Work 위임마다 체크포인트 → 스냅샷의 active_chain이 *현재 깊이*를
        # 반영. 끊김 시 가장 깊은 활성 워커(체인 끝)를 그 원문으로 재개(리더로 안 튐). 깊은 전문가 협업 보존.
        _ckpt(flow)
    _dbg(f"{tag} ✓전송 req={req}{' (Redo)' if is_redo else ''}")
    if flow.log:   # 관측: 모든 요청을 '보낸 순서'대로 영속 기록(중첩 PostToolUse 타이밍에 안 묻힘)
        flow.log("req_sent", frm=me_id, to=to, kind=str(getattr(kind, "value", kind)),
                 seg=flow.leader_segment, redo=is_redo, body=body[:60])
    # Task 정의 '실질 협의' 참여 기록 — 보낸 쪽·받은 쪽 모두(누가 물었든: 리더든 peer든). 빈 핑은 제외.
    # → set_goal 게이트가 'peer 협의도 합의로 인정'하고 '빈 핑은 불인정'하게 만든다(허브 완화·실질 강제).
    if kind == Kind.INFO and flow.current and _is_substantive(body):
        for x in (me_id, to):
            if x in flow.current.team and x != flow.leader:
                flow.current.participated.add(x)
        # [협업 — 전문가 간 직접 대화(2026-06-22 사용자 설계)] 양쪽 다 비-리더 팀원이면 owner↔owner 직접
        # Info(리더 경유 아님) — 쌍으로 기록. 인터페이스 계약을 '리더 중계·추측'이 아니라 *당사자끼리*
        # 합의했는지 마감 게이트(iface_dialogue)가 본다.
        if (me_id != flow.leader and to != flow.leader
                and me_id in flow.current.team and to in flow.current.team):
            flow.current.peer_info_pairs.add(frozenset((me_id, to)))
    # ── 위임 완주 보장(detach-safe) ─────────────────────────────────────────
    # 여기서부터의 '깨우기→응답 처리→프레임 close'는 별도 태스크(_deliver)로 돌고, 도구 호출
    # 자체는 shield로 감싼다. CLI가 (자체 한도 등으로) 이 도구 호출을 포기·취소해도 위임은
    # 끝까지 완주하고 규약(베턴·게이트·기록)이 일관되게 닫힌다 — 라이브 관측: 위임 포기가
    # '이중 활성'(리더+사슬 동시 작업)과 리더의 '비동기 작업 중' 오인을 만들던 결함의 차단.
    # 완주 결과는 flow.detached_results로 남아 SYS가 이어가기 리더에게 전달한다.
    detached = {"on": False}

    async def _deliver():
        runs_before = flow.current.run_count if flow.current else 0
        acts_before = flow.act_count   # 위임 도중 owner(단일흐름이라 깨운 동료만 활성)가 실제로 일했는지 측정
        mine_before = flow.act_by.get(me_id, 0) if getattr(flow, "act_by", None) is not None else 0
        # [B-14 — report 스태시] stale 소거: 이전 턴(fork 가지 등)에서 남은 스태시가 이번 위임의 보고로
        # 오인되지 않게 wake 전에 비운다(소비는 wake 후 fresh pop만).
        (getattr(flow, "report_stash", None) or {}).pop(to, None)
        _body_local = owner_body
        result = ""
        _nest_guard = 0
        while True:
            try:
                result = await flow.wake(to, _body_local, kind)     # 동료 깨워 응답(중첩 베턴)
                if _looks_transient(result):                        # 일시 오류면 한 번 더(답으로 취급 X)
                    result = await flow.wake(to, _body_local, kind)
            except Exception as e:
                result = f"(동료 처리 중 오류: {e})"
            # [중첩 위임 — 동기처럼 완주(논블로킹 핸드오프)] `to`가 자기 턴에서 다른 동료에게 핸드오프했으면
            # SYS가 그 하위 위임을 호출 *밖*에서 완주시키고 `to`를 그 결과로 이어간다 — 블록킹 도구호출 없이
            # 중첩이 직렬로 완주(75초 미닿음). 판정은 `to`의 출력 문자열('[위임됨' 등 — 봇 표현은 못 믿음)이
            # 아니라 **handoff_inflight[to]에 실제로 하위 위임이 등록됐다는 사실**로 한다(견고). _nest_guard는
            # 폭주 백스톱(같은 `to`가 끝없이 재위임만 하는 병적 경우) — 정상 사슬은 한참 못 미친다.
            _sub = (getattr(flow, "handoff_inflight", None) or {}).pop(to, None)
            _nest_guard += 1
            if _sub is None or _nest_guard > 50:
                if _sub is not None and flow.log:
                    flow.log("handoff_nest_guard", to=to, depth=_nest_guard)
                break
            try:
                _sr = await _sub
                _srt = _sr["content"][0]["text"] if isinstance(_sr, dict) else str(_sr)
            except Exception as e:
                _srt = f"(하위 위임 오류: {e})"
            _body_local = ("[당신이 맡긴 위임의 결과가 도착했습니다 — 이어서 통합·검증·완성하세요(추가 위임이 "
                           f"더 필요하면 한 번에 하나씩, 끝나면 보고로 응답):\n{_speech_clip(_srt, 4000)}")
        # 깨운 동료가 '나(위임자)에게 확인요청'을 남기고 턴을 마쳤으면, 그 질문을 응답으로 표면화 →
        # 내가 답을 정해 다시 맡긴다(되묻기가 에러가 아니라 협업으로 흐름). 이는 '완료'가 아니므로
        # delivered로 기록하지 않는다(되묻기 후 재위임은 Redo가 아니라 '첫 구현').
        was_clarify = False
        if (flow.pending_clarify and flow.pending_clarify.get("to") == me_id
                and flow.pending_clarify.get("from") == to):
            q = flow.pending_clarify["q"]
            flow.pending_clarify = None
            was_clarify = True
            result = (f"[확인요청 from {flow._info(to)}] {q}\n"
                      f"(→ 답을 정한 뒤, 이 작업을 {flow._info(to)}에게 request(Work)로 다시 맡기세요)")
        failed = _looks_transient(result)
        # [B-14 — report 스태시 소비(인자 > regex)] 이번 wake에서 워커가 report 도구로 남긴 구조화 필드.
        _stash = (getattr(flow, "report_stash", None) or {}).pop(to, None)
        # [B-14 — 보고 재요청 훅(1회)] '도구 미호출 AND regex 폴백 미검출'일 때만 — 전환기 워커 턴 2배
        # 방지. **ORGANT_REPORT_REASK 미설정=off(기존동작 불변)** — 워커 턴을 늘리는 행동 변화라 플래그
        # 뒤에 둔다(default-OFF + 이중수용 관례). 되묻기·크래시·턴한도 미완은 보고가 아니므로 제외.
        if (kind == Kind.WORK and not was_clarify and not failed and _stash is None
                and (os.environ.get("ORGANT_REPORT_REASK") or "").strip().lower() in ("1", "true", "yes", "on")
                and "턴 한도 도달" not in (result or "")
                and not re.search(r"\[\s*(결과|직군밖|경험|직무기준)\s*\]", result or "")):
            if flow.log:
                flow.log("report_reask", to=to, seg=flow.leader_segment)
            try:
                _r2 = await flow.wake(
                    to, "[SYS — 보고 형식 재요청(1회)] 방금 응답에 보고 계약이 없습니다. 추가 작업 없이, "
                        "report 도구로 구조화 필드를 기록하거나 [결과]/[변경]/[검증]/[리스크] 골격으로 "
                        "한 번만 다시 보고하세요.", kind)
            except Exception:
                _r2 = ""
            _stash = (getattr(flow, "report_stash", None) or {}).pop(to, None)
            if (_r2 or "").strip() and not _looks_transient(_r2):
                result = (result + "\n\n[보고 재요청 응답]\n" + _r2).strip()
        # [직군밖 반려 — 전문화의 구조 채널] 도메인 적합성은 시스템이 키워드로 판정하지 않는다 —
        # 그 분야 전문가(수신 owner)가 판정한다(자기정의 원칙). owner가 첫 줄에 '[직군밖] 필요직군'
        # 을 적으면: 실패도 미완도 아닌 '올바른 반려'로 분류하고, 소유를 해제하며, 리더에게 채용을
        # 구조적으로 지시한다 — 관계없는 직군이 일을 흡수해 어설픈 산출물을 내던 경로(라이브:
        # ML이 백엔드에 묶여 감)의 차단. [B-14] report 인자 offdomain_role이 이 첫줄 regex보다
        # 우선한다(이중 수용 — regex 폴백 존치).
        _off_arg = _norm_offdomain((_stash or {}).get("offdomain_role"))   # 부정 센티넬('해당없음' 등)=반려 아님
        refused_m = re.match(r"^\s*\[직군밖\]\s*([^\n]*)", result or "")
        # regex 캡처도 센티넬이면 반려 무효(봇이 '[직군밖] 해당없음'처럼 자기모순으로 적어도 오이전 차단).
        _off_regex = _norm_offdomain(refused_m.group(1)) if refused_m else ""
        refused = bool(kind == Kind.WORK and not was_clarify and not failed and (_off_regex or _off_arg))
        if refused and flow.current is not None and flow.current.owner == to:
            flow.current.owner = 0                 # 소유 해제 — 채용된 전문가가 새 owner가 되게
            flow.current.status.owner = ""
            flow.current.owner_incomplete = False
            _ckpt(flow)
        # [파일 P2P 이전 — 전문가 이전(사용자)] '[직군밖] X' 반려는 '이 산출물은 X 도메인'이라는 P2P 선언이다.
        # 반려한 봇이 쥔 **파일 lock(file_owner)을 지목 직군 X로 이전**한다 → X 전문가가 리더 위임 없이(탈중앙)
        # 그 파일을 소유·편집(게이트#9 통과 + #3의 '자기 도메인 파일' 면제 통과). 종전엔 task 소유만 풀고 파일
        # lock은 남아, 스캐폴드를 만든 직군(프론트 server.js)이 영구 lock → 올바른 도메인(백엔드)이 못 고쳐
        # '내용을 텍스트로 넘김→[직군밖] 거절' 데드락(라이브). X 미지목이면 해제(다음 편집자 재귀속).
        if refused and getattr(flow, "file_owner", None):
            _refdoms = {_norm_job(j) for j in _jobs_of(flow._info(to) or "") if j.strip()} - {""}
            _need = _off_regex or _off_arg                      # 이미 센티넬 정규화됨(유령 직군 이전 차단)
            _target = _norm_job(_need) if _need else ""
            _touched = [p for p, d in list(flow.file_owner.items()) if d in _refdoms]
            for _p in _touched:
                if _target and not _target.startswith("예비"):
                    flow.file_owner[_p] = _target        # 지목 직군 X로 P2P 이전 → X가 소유·구현
                else:
                    del flow.file_owner[_p]               # 미지목 → 해제(다음 편집자 재귀속)
            if _touched and getattr(flow, "persist_owner", None):
                try:
                    flow.persist_owner()
                except Exception:
                    pass
        # [파일 권한 승낙 — 주인이 직접 이양(2026-07-08, 사용자: '남의 파일은 물어보고 주인이 승낙해야')]
        # 게이트#9로 막힌 봇이 파일 주인에게 request로 '편집 권한'을 요청하면, 주인이 응답에 '[권한 이양 X]'로
        # 승낙한다 → 주인이 쥔 파일 lock(file_owner)을 요청 도메인 X로 이양(리더 경유 아님 — 파일 주인과 직접
        # 합의). [직군밖](밀어내기=Work 반려)의 대칭 — 이건 '당겨오기 요청'에 대한 owner 승낙이라 kind·owner
        # 무관하게 처리한다. X가 이후 그 파일을 소유·편집(게이트#9·#3의 '자기 도메인' 통과). 종전엔 승낙 경로가
        # 없어 게이트#9가 '수정 요청'만 안내→튕김. (이양은 도메인 단위 — [직군밖]과 동일 입도.)
        _grant_m = re.search(r"\[\s*권한\s*(?:이양|양도|넘김|부여)\s*([^\]\n]+?)\s*\]", result or "")
        if _grant_m and getattr(flow, "file_owner", None):
            _gdoms = {_norm_job(j) for j in _jobs_of(flow._info(to) or "") if j.strip()} - {""}
            _gtarget = _norm_job(_grant_m.group(1).strip())
            if _gdoms and _gtarget and not _gtarget.startswith("예비") and _gtarget not in _gdoms:
                _gtouched = [p for p, d in list(flow.file_owner.items()) if d in _gdoms]
                for _p in _gtouched:
                    flow.file_owner[_p] = _gtarget      # 주인 승낙 → 요청 도메인으로 이양
                if _gtouched:
                    if flow.log:
                        flow.log("file_grant_transfer", frm=int(to), to_dom=_gtarget, files=len(_gtouched))
                    if getattr(flow, "persist_owner", None):
                        try:
                            flow.persist_owner()
                        except Exception:
                            pass
        # [단순 허락 — 담당은 안 넘기고 편집권만(2026-07-08, 사용자)] 주인이 '[편집 허락 X]'로 답하면 소유(담당)는
        # 그대로 두고 X에게 그 파일 *편집 권한*만 준다(file_permits). 완전 이양(위 [권한 이양])과 구분 — 주인이
        # 계속 그 파일 책임자이되, X도 편집 가능(게이트#9·#3이 owner+permits를 통과로 인정). 공유 산출물(app.js:
        # 프론트 UX + 백 로직)처럼 둘 다 정당히 손대는 경우의 최소 침습 해법(멀티오너 데이터모델 불필요).
        _permit_m = re.search(r"\[\s*(?:편집\s*)?허락\s*([^\]\n]+?)\s*\]", result or "")
        if _permit_m and getattr(flow, "file_owner", None) is not None:
            _pdoms = {_norm_job(j) for j in _jobs_of(flow._info(to) or "") if j.strip()} - {""}
            _ptarget = _norm_job(_permit_m.group(1).strip())
            if _pdoms and _ptarget and not _ptarget.startswith("예비") and _ptarget not in _pdoms:
                _ptouched = [p for p, d in list(flow.file_owner.items()) if d in _pdoms]
                if getattr(flow, "file_permits", None) is None:
                    flow.file_permits = {}
                for _p in _ptouched:
                    flow.file_permits.setdefault(_p, set()).add(_ptarget)   # 편집권만 부여 — 소유(담당)는 유지
                if _ptouched:
                    if flow.log:
                        flow.log("file_grant_permit", frm=int(to), to_dom=_ptarget, files=len(_ptouched))
                    if getattr(flow, "persist_owner", None):
                        try:
                            flow.persist_owner()
                        except Exception:
                            pass
        # owner가 '위임 도중 실제로 일했나' — 단일흐름이라 깨운 동료(+그 하위)만 활성이므로 wake 전후
        # act_count(run/Write/Edit) 증가 = owner 작업. 거짓이면 owner는 깨어났지만 착수 전/계획만 하고
        # 곧장 반환한 것(허위완료의 씨앗). 이걸로 '검증된 인도'와 '빈 응답'을 가른다.
        # '요청자 자신'의 활동(detach 뒤 리더가 모델 쪽에서 돌린 폴링 run 등)은 빼고 잰다 —
        # 위임 측정창의 인도 신호(owner_acted)가 이중 활성 잔재로 오염되지 않게(허위완료 차단 정확성).
        mine_delta = (flow.act_by.get(me_id, 0) - mine_before) if getattr(flow, "act_by", None) is not None else 0
        owner_acted = (flow.act_count - acts_before) > mine_delta
        # 진짜 행(무활동)으로 끊긴 인프라 타임아웃인데 owner가 그 전에 실제로 작업을 했다면, 한 작업은
        # 작업공간에 남아 있다 → '실패'로 끝내 유실시키지 말고 '이어가기'(미완)로 처리한다. (하트비트
        # 타임아웃이 일하는 워커는 안 자르므로 드문 경우지만, 안전망으로 작업 유실·허위완료를 막는다.)
        infra_timeout = (kind == Kind.WORK and not was_clarify
                         and "api error: timeout" in (result or "").lower())
        resumable_timeout = infra_timeout and owner_acted
        # 동료가 'turn 한도'로 미완 반환했나(Work) — 그러면 이 Task는 완료로 못 닫고(complete_task 거부),
        # 같은 owner에게 '이어서(continuation)' 재위임해 끝내야 한다(허위완료→다음 Task churn 차단). 미완은
        # delivered(accept)로 안 쳐서 respond 마커를 'incomplete'로 두면, 재위임이 Redo 한도에 안 걸린다
        # (이어가기는 '직전 결함 보완'이 아니라 '남은 작업 마저 하기'이므로 횟수 제한 없이 계속 가능).
        incomplete = (kind == Kind.WORK and not was_clarify and not failed and not refused
                      and "턴 한도 도달" in (result or "")) or resumable_timeout
        # 미완 게이트(owner_incomplete)는 '의미 있는 신호'로만 갱신한다: 미완 신호면 True, owner가
        # '실작업을 담은 정상 응답'으로 마무리하면 False(이어가기 완료 = 게이트 자동 해제). 크래시(failed)
        # ·실작업 없는 응답은 완료의 증거가 아니므로 직전 상태를 유지한다 — 타임아웃 미완이 후속 크래시/
        # 빈 응답으로 풀려 미완인 채 complete가 통과되는 구멍 차단.
        if kind == Kind.WORK and not was_clarify and flow.current:
            if incomplete:
                flow.current.owner_incomplete = True
            elif not failed and owner_acted:
                flow.current.owner_incomplete = False
        is_owner_work = (kind == Kind.WORK and not was_clarify and not failed and not incomplete
                         and not refused
                         and flow.current is not None and to == flow.current.owner)
        # owner가 Work를 받고도 실작업(run/Write) 0회로 곧장 반환 = 착수 전/계획만 = '인도 아님'.
        premature = is_owner_work and not owner_acted
        if premature and flow.current is not None:
            # 미착수도 '구조적 미완'이다 — 마커를 세워 complete를 막고, 리더 세그먼트가 여기서
            # 끝나도 SYS 자동 이어가기가 같은 owner를 다시 깨운다(판단이 아니라 기계적 행동).
            flow.current.owner_incomplete = True
        if is_owner_work and owner_acted and _is_substantive(result):
            flow.current.owner_delivered = True   # 이 owner가 실작업+응답을 냈다 → complete_task 허용 근거
            # [파이프라인 §3 — 위임축 접점(S2)] 실작업 인도 = 그 수행자의 백로그 마무리. 릴레이 장부
            # done + 배분권이 그에게 이동(backlog_done 이벤트). 플래그 OFF·백로그 밖 위임이면 no-op.
            from .backlog import sync_completion as _bl_done
            _bl_done(flow, to)
            _ckpt(flow)              # [인도 사실 영속] 복구가 인도 핸드셰이크를 다시 요구하지 않게(마감 닫힘)
            # [다음 선정 즉시 게시(2026-07-14)] 완료가 낳은 핸드오프 공고(handoff_note)를 다음 도구
            # 호출까지 묵히지 않는다 — 응찰·선정이 이 경계에서 바로 시작되도록.
            from .milestone import flush_pipeline_notes as _fl_notes
            await _fl_notes(flow)
        # [B-09 Phase A — Task Dossier] Work 응답 전문([결과]/[변경]/[검증]/[리스크])을 REPORTS.md에
        # append(무절단 원본). 채팅 clip·스냅샷 result_so_far 500자 절단과 무관하게 보고 원문이 보존된다.
        # 크래시(transient)·되묻기(clarify)는 보고가 아니므로 제외. 실패해도 흐름 무해(best-effort).
        if kind == Kind.WORK and not was_clarify and not failed and flow.current is not None:
            from .._util import dossier_append
            _mark = ("직군밖 반려" if refused else
                     "미완(이어가기 대기)" if (incomplete or premature) else "인도")
            # [B-14] report 도구 스태시의 구조화 필드를 보고 원본에 동봉(기계 소비 가능한 구조 기록).
            _struct = ""
            if _stash and any((_stash.get(k) or "").strip()
                              for k in ("result", "changes", "verify", "risks")):
                _struct = "\n[report 도구 — 구조화 필드]\n" + "\n".join(
                    f"[{lbl}] {_stash.get(k)}"
                    for k, lbl in (("result", "결과"), ("changes", "변경"),
                                   ("verify", "검증"), ("risks", "리스크"))
                    if (_stash.get(k) or "").strip())
            dossier_append(flow, "REPORTS.md",
                           f"## {flow._info(to) or to} → {flow._info(me_id) or me_id} — Work 응답({_mark})\n"
                           f"{result}{_struct}")
        try:
            await g.send_response(thread_id, to, req, result)
            await _react(g, thread_id, req, "⚠️" if failed else "✅")  # 상태=이모지(해소/실패)
            _dbg(f"{tag} {'⚠실패' if failed else ('…미완' if (incomplete or premature) else '✓응답')} len={len(result)}")
        finally:
            # 프레임 close = 베턴 복귀(누수 방지). 정상이면 alive==to 라 그대로 닫힌다. 미완·미착수(premature)는
            # 'accept'로 안 쳐서 delivered로 기록 안 함 → 같은 owner 재위임이 Redo 한도에 안 걸리고 '실제 첫 인도'로 성립.
            # 크래시(failed)도 'accept'가 아니다 — 인프라 실패가 '완료 인도'로 기록되면 직후 재요청이
            # Redo(보완)로 둔갑해 한도를 태우고 owner에게 '직전 산출물 결함' 프레임으로 잘못 전달된다.
            try:
                flow.comm.respond(to, "clarify" if was_clarify else
                                  ("refused" if refused else
                                   "incomplete" if (incomplete or premature) else
                                   "failed" if failed else "accept"), result)
            except CommError:
                # to의 중첩 하위요청이 응답 없이 끝나(크래시/이탈) 베턴이 to에 '굳은' 비정상 상황 →
                # me_id(요청자)가 다시 alive 될 때까지 위 프레임을 강제 close. 흐름 교착(굳음) 방지.
                _stuck = flow.comm.alive
                if flow.log:
                    flow.log("baton_recover", me=me_id, stuck_alive=_stuck, to=to)
                # [막힘 흡수 차단 — 막힌 사람 기록] 베턴이 막힌 하위 담당에서 위임자에게 되돌아온다. 위임자가
                # '내가 하지'로 그 사람 일을 흡수하지 못하게 막힌 사람을 기록 — 게이트가 '같은 사람 재요청'을
                # 유도(재채용 X). 막힌 사람이 다시 일하면 해제. (origin/리더 자신이 막힌 건 흡수 대상 아님.)
                # *새* victim일 때만 기준치·카운터 초기화 — 같은 사람이 반복해 막히면 카운터가 누적돼 N회 후
                # 게이트가 폴백(통과)하므로, 진짜 죽은 동료에 무한 재요청·무한 차단으로 빌드가 얼지 않는다.
                if (_stuck and _stuck != flow.comm.origin and _stuck != getattr(flow, "leader", None)
                        and getattr(flow, "_stall_victim", None) != _stuck):
                    flow._stall_victim = _stuck
                    flow._stall_victim_acts = (getattr(flow, "act_by", None) or {}).get(_stuck, 0)
                    flow._stall_blocks = 0
                guard = 0
                # origin 프레임(스택 마지막 1장)은 여기서 닫지 않는다 — 핸들러 레벨 복구가
                # 흐름 자체를 종료시키면 안 됨(origin 마감은 SYS의 _close_flow 책임). detach로
                # 프레임 순서가 어긋난 최악 타이밍에 흐름이 통째로 드레인되던 위험 차단.
                while (not flow.comm.done and flow.comm.alive != me_id
                       and len(flow.comm.open_requests) > 1 and guard < 30):
                    flow.comm.escalate("베턴 굳음 안전복구")
                    guard += 1
        if failed:
            if resumable_timeout:
                # owner가 작업을 진행하다 '무활동'으로 끊긴 경우 — 한 작업은 작업공간에 보존돼 있다.
                # 실패로 끝내지 말고 같은 owner에게 '이어서' 재위임(연속). owner_incomplete=True라 complete는
                # 막히고, 프레임 마커가 incomplete라 redo 한도와 무관하게 계속 이어갈 수 있다(유실·허위완료 동시 차단).
                if flow.log:
                    flow.log("owner_resumable_timeout", to=to, seg=getattr(flow, "leader_segment", 0))
                # [B-17 — 사실통지 축소] 후속 행동지시 삭제: 백스톱 실재 — owner_incomplete=True가 이미
                # 세워져 complete_task가 거부되고(rule/task 미완 게이트), SYS _auto_continue_owner가 같은
                # 담당자에게 기계적으로 이어가기를 보내며, 타인 fresh-Work는 G1(미완 owner 보호)이 막는다.
                return _ok(f"[{flow._info(to)}] 작업 진행 중 일시 무응답으로 끊김 — 진행분은 작업공간에 "
                           f"보존됐고 미완 마커가 세워졌습니다(완료는 차단되며, SYS가 같은 담당자에게 "
                           f"자동 이어가기를 보냅니다).")
            # 구조적 사실: 단일흐름은 한 번에 한 명만 일한다 → 요청자는 그 동료가 끝날 때까지 '블록'된다.
            # 따라서 여기서의 '실패'는 그 동료가 느리거나 불응한 게 아니라 그 동료의 LLM 서브프로세스가
            # '크래시'(SIGTERM/143·연결끊김·과부하)한 것 — 즉 인프라/환경 문제다. 새 사람으로 바꾸거나
            # 충원하면 '같은 환경'에서 똑같이 크래시한다(이게 '백엔드 6명' 루프의 뿌리). 그래서 실패엔
            # '재배정·채용'을 절대 권하지 않는다 — 같은 동료 1회 재시도(블립 회복용) 또는 사용자 보고만.
            flow.consec_fail = getattr(flow, "consec_fail", 0) + 1
            if flow.consec_fail == 1:
                flow._consec_fail_t0 = time.monotonic()   # [G4] 연속 실패 스트릭 시작 시각(시간창 판정용)
            if flow.log:
                flow.log("req_failed", to=to, consec=flow.consec_fail, seg=flow.leader_segment)
            # [G4 — 연속 실패 하드블록(자기치유형, B-03)] 시간창 내 연속 3회 무응답 = 환경 불안정이 지속 중 —
            # 기존 consec_fail>=2는 recruit만 막고(:1040) 흐름은 계속 돌아 밤새 소각됐다(P-031형, 재발점 ④).
            # _hard_blocked를 세워 SYS 이어가기 루프를 정지시키고, sys_core가 백오프 뒤 프로브 wake 1회 성공
            # 시 자동 해제한다(성공 1회 리셋 :아래 consec_fail=0 유지 — 블립 회복 보존).
            if (flow.consec_fail >= 3 and not getattr(flow, "_hard_blocked", None)
                    and time.monotonic() - getattr(flow, "_consec_fail_t0", 0.0) <= _HARD_BLOCK_WINDOW):
                flow._hard_blocked = HARD_BLOCK_TRANSIENT
                if flow.log:
                    flow.log("hard_blocked", consec=flow.consec_fail, seg=flow.leader_segment)
            if flow.consec_fail >= 2:
                return _ok(f"[{to}] 또 실패 — **연속 {flow.consec_fail}회**. 이건 그 동료가 아니라 **환경(인프라) 일시 "
                           f"불안정**입니다(단일흐름이라 한 명만 도는데 그 서브프로세스가 크래시한 것). **새로 뽑거나 "
                           f"다른 사람으로 바꾸지 마세요 — 같은 환경이라 똑같이 실패합니다.** 진행 상황을 사용자에게 "
                           f"'환경 불안정으로 일시 중단'이라 보고하고 멈추세요(무한 재시도·충원 금지).")
            return _ok(f"[{to}] 응답 실패. 단일흐름에선 한 명만 일하므로 이건 그 동료 탓이 아니라 거의 항상 **인프라/일시 "
                       f"오류(서브프로세스 크래시)**입니다 — **다른 사람으로 바꾸거나 새로 뽑지 마세요(같은 환경이라 똑같이 "
                       f"실패).** 같은 동료에게 한 번만 다시 요청해보고(블립이면 회복), 또 실패하면 사용자에게 보고하고 멈추세요.")
        flow.consec_fail = 0   # 정상 응답 → 연속 실패 카운터 리셋(일시 블립 회복)
        if refused:
            # [B-14] 인자 > regex — offdomain_role 인자가 있으면 그 직군명, 없으면 첫줄 regex 캡처(폴백).
            need = (_off_arg or _off_regex).strip() or "해당 전문 직군"
            if flow.log:
                flow.log("work_refused_offdomain", to=to, need=need[:30], seg=flow.leader_segment)
            return _ok(f"[직군밖 반려] {flow._info(to) or to}가 이 일을 **자기 직군 밖**으로 판정했습니다 — "
                       f"필요 직군: {need}.\n**recruit(role='{need}')로 예비를 채용해 그 전문가에게 Work로 "
                       f"맡기세요** — 같은 동료나 관계없는 직군에 다시 떠넘기지 마세요(이 반려는 실패가 아니라 "
                       f"올바른 전문화 신호입니다. 소유는 해제됐고, 채용된 전문가가 새 owner가 됩니다).\n"
                       f"--- 반려 보고 원문 ---\n{_speech_clip(result, 1500)}")
        # owner가 깨어났지만 '실작업 없이'(run/Write/Edit 0회) 곧장 반환 = 아직 착수 전/계획만. 리더가 대신
        # 구현·완료하지 말 것(독점·허위완료의 정확한 진입점). 같은 owner에게 다시 맡겨 '검증된 산출물'을 받게
        # 안내한다. 이 응답은 캐시하지 않는다 → 같은 턴에 재위임해도 합쳐지지 않고 실제로 다시 깨운다.
        if premature:
            _dbg(f"{tag} ⚠owner 미착수(실작업 0)")
            if flow.log:
                flow.log("owner_no_work", to=to, seg=flow.leader_segment)
            # [B-17 — 사실통지 축소] 후속 행동지시 삭제: 백스톱 실재 — owner_incomplete=True가 세워져
            # complete_task가 거부되고(rule/task), 대리구현은 permissions #4(owner 도메인 대리 Write 거부)가
            # 막으며, SYS _auto_continue_owner가 같은 owner에게 기계적으로 다시 맡긴다.
            return _ok(f"[{to} 응답] {_speech_clip(result, 1500)}\n\n[사실] {flow._info(to) or to}의 실작업"
                       f"(run/파일작성)이 0회입니다(착수 전/계획만) — 미완 마커가 세워졌습니다(완료·대리구현은 "
                       f"게이트가 막고, SYS가 같은 owner에게 자동 이어가기를 보냅니다).")
        # 위임 응답엔 owner가 '직접 돌린 실행 증거(시스템 캡처)'를 붙여 돌려준다 — 위임자가 말이 아니라
        # 증거로 '검증 후 수락'할 수 있게(반사적 재요청 대신). owner가 이번에 run을 돌렸을 때만.
        receipt = ""
        if (kind == Kind.WORK and not was_clarify and flow.current
                and flow.current.run_count > runs_before and flow.current.evidence):
            receipt = f"\n[owner 실행 증거(시스템 캡처)] {_speech_clip(flow.current.evidence, 1000)}"
        # [발견1 교정 2026-06-13] 검증 대상 산출물이 '존재'하면(owner 위임 인도 OR 리더가 직접
        # 구현=leader_writes>0) 그 후 타 멤버 응답을 교차 검증 참여로 센다 — 리더 독식 Task(owner==0)도
        # 제3자 검증 대상('누가 만들었든 제3자 검증'은 보편 이치). 종전엔 owner_delivered만 봐서 리더
        # 독식이 검증 면제되던 구멍.
        product_ready = (flow.current.owner_delivered
                         or (not flow.current.owner and getattr(flow.current, "leader_writes", 0) > 0))
        if flow.current and product_ready and to != flow.current.owner:
            flow.current.cross_checks += 1
            # [독립 검증 = 다른 도메인 — 동질 모델] 같은 Claude·같은 직군 검증자는 에코(같은 관점→같은
            # 맹점). owner와 도메인이 다른 검증자만 '독립'으로 따로 센다(owner 미상이면 리더 기준).
            _own = flow.current.owner or flow.leader
            _od = {_norm_job(j) for j in _jobs_of(flow._info(_own) or "")} - {""}
            _vd = {_norm_job(j) for j in _jobs_of(flow._info(to) or "")} - {""}
            if _od and _vd and not (_od & _vd):
                flow.current.cross_check_offdomain += 1
                # [검증 종료상태 — 리뷰F2 교정] *독립(off-domain)* 검증 시점의 저작수만 기록한다(same-domain
                # 검증엔 갱신 안 함 — 종전엔 same-domain 검증이 마커를 올려 *변경된* 코드의 정당한 off-domain
                # 재검증을 막던 staleness). + 이 검증자를 기록(리뷰F1: 재검증 dedup이 '이미 검증한 그 검증자'에게
                # 만 적용되게 — 검증자에게 새 작업 시키는 것까지 막던 회귀 차단).
                flow.current.last_verify_writes = sum(int(v) for v in (flow.writes_by_role or {}).values())
                flow.current.cross_checkers.add(int(to))
            # [원리 기반 루프 신호(2026-07-09)] 이 검증의 '결과'를 본다 — 실패로 끝났으면 연속실패 +1,
            # 통과가 하나라도 나오면 0으로. 루프의 본질 = 결과 없는 반복이지 횟수가 아니다(고정 12회 폐지).
            _head9 = (result or "")[:300]
            _failish = any(t in _head9 for t in ("FAIL", "실패", "미달", "반대", "안 닫", "재현 불가", "BLOCKED"))
            _passish = any(t in _head9 for t in ("PASS", "통과", "이상 없", "확인 완료", "충족"))
            if _passish and not _failish:
                flow.current.verify_fail_streak = 0
            elif _failish:
                flow.current.verify_fail_streak = int(getattr(flow.current, "verify_fail_streak", 0) or 0) + 1
            _ckpt(flow)              # [교차검증 사실 영속] 복구가 교차검증을 다시 요구하지 않게(마감 닫힘)
            # [회로차단기 — 수렴 경보] 검증이 연속으로 실패로만 끝난다(사이에 통과 0) = 접근을 바꿔도 결과가
            # 안 바뀌는 반복 — 흔히 코드로 못 고치는 한계(플랫폼 제약 등). 봇은 '해결 불가'를 스스로 판정 못
            # 하므로 시스템이 메타인지를 대신해 사람에게 *1회* 넘긴다. (종전 고정 12회 — 라이브에서 12사이클
            # 낭비 후에야 발동하는 늦은 임의 수치로 판명, 2026-07-09 결과 기반으로 교체)
            from .task import _LOOP_FAIL_STREAK
            if (int(getattr(flow.current, "verify_fail_streak", 0) or 0) >= _LOOP_FAIL_STREAK
                    and not getattr(flow.current, "loop_escalated", False)):
                flow.current.loop_escalated = True
                if flow.log:
                    flow.log("loop_circuit_breaker", task=flow.current.task_id,
                             streak=flow.current.verify_fail_streak, cross=flow.current.cross_checks)
                try:
                    _goal9 = ((flow.current.status.goal or flow.current.purpose or "") if flow.current else "")[:60]
                    await flow.guide.post(
                        flow.user_channel, 0,
                        f"[수렴 경보 — 사람 판정 필요] (대상 Task: {_goal9}…) 검증이 {flow.current.verify_fail_streak}회 연속 실패로만 "
                        f"끝나고 그 사이 통과가 없습니다 — 접근을 바꿔도 결과가 안 바뀌는 반복이라, 흔히 코드로 "
                        f"못 고치는 한계(플랫폼 제약 등)입니다. 결정해주세요: **① 현 상태 수용·마감** / **② 다른 방향 제시**.")
                except Exception:
                    pass
                _ckpt(flow)
        flow.req_results[dupkey] = result   # 같은 턴 병렬 중복요청이 재사용할 응답 캐시(동료 재호출 방지)
        return _ok(f"[{to} 응답] {_speech_clip(result, 4000)}{receipt}")


    async def _deliver_tracked():
        payload = await _deliver()
        if detached["on"]:
            try:
                txt = payload["content"][0]["text"]
            except Exception:
                txt = str(payload)[:400]
            flow.detached_results.append(f"{flow._info(to) or to} → {_speech_clip(txt, 4000)}")
        return payload

    inner = asyncio.ensure_future(_deliver_tracked())
    flow.inflight_tasks.add(inner)
    inner.add_done_callback(flow.inflight_tasks.discard)
    if getattr(flow, "_handoff", False):
        # [논블로킹 핸드오프 — 단일흐름 안정성(2026-06-22 사용자 설계)] 동료의 *턴 전체*를 도구호출 안에서
        # 기다리지 않는다. 기다리면 75초 넘을 때 CLI가 도구호출을 포기→CancelledError→detach→백그라운드
        # 비동기 churn(P-029: 6위임 전부 detach·'처리 중 턴종료' 누수·빈 산출물). 대신 위임을 인플라이트로
        # 등록하고 *즉시* 반환 — 동료 작업은 SYS 이어가기 루프(_drain_inflight)와 _deliver 중첩 루프가 호출
        # *밖*에서 완주시켜 결과로 요청자를 잇는다. 베턴은 이미 to로 넘어가 요청자는 비활성 → 재위임 불가
        # (규약이 막음). 도구호출이 1초라 75초가 닿지 않고, 베턴 1개라 비동기 다중실행이 구조적으로 불가 = 단일흐름.
        detached["on"] = True
        flow.handoff_inflight[me_id] = inner
        return _ok("[위임됨 — SYS가 동료를 끝까지 완주시켜 *결과로 당신을 이어줍니다*(비동기 아님 · 한 번에 "
                   "한 위임). **'처리 중' 같은 말이나 재위임·추가 행동 없이 이 턴을 여기서 마치세요** — "
                   "결과가 도착하면 SYS가 자동으로 당신을 재개합니다.]")
    try:
        return await asyncio.shield(inner)
    except asyncio.CancelledError:
        if not inner.done():
            detached["on"] = True       # 도구 호출만 죽고 위임은 계속 — 결과는 detached로 전달
            if flow.log:
                flow.log("delegation_detached", to=to, seg=flow.leader_segment)
        raise

