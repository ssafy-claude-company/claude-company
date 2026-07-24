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
    _capability_gaps, _clarify_hold, _cooldown_probe, _find_variant_job, _fork_collect,
    _classify_vote, _free_alternatives, _group_of, _is_spare, _is_substantive, _job_tokens,
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
    from .comm_helpers import _resolve_nominee
    m = _NOMINATE_RE.search(t)
    if m:
        # [지명 정본 해석(2026-07-21, 사용자: '동명이인 — id로 최대한')] id 우선·이름은 유일 일치만.
        addressee = _resolve_nominee(m.group(1), flow, allowed)
    if addressee is None:
        # [스탠스 @대상 흡수(2026-07-21, U-039 실측: '[질문 @게임 기획자(id)]' 자연 표기)] 타입 괄호
        # 안의 @대상 — **괄호 속 id 우선**, 이름은 유일 일치만(첫 실질 줄의 자기 선언 위치만).
        for _ln in t.splitlines():
            _ls = _ln.strip()
            if not _ls:
                continue
            m2 = re.match(r"^\[\s*(?:주장|질문|반박|지지)\b[^@\]]*@\s*([^\](]{1,40}?)\s*(?:\(([^)]*)\))?\s*\]", _ls)
            if m2:
                for _cand in (m2.group(2), m2.group(1)):   # id(괄호) 먼저, 이름은 폴백
                    if _cand:
                        addressee = _resolve_nominee(_cand.strip(), flow, allowed)
                        if addressee is not None:
                            break
            break
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
    _sys_open = bool(args.get("_sys_open"))
    members = _resolve_members(args.get("members", ""), flow, flow.current.team) or \
              [m for m in flow.current.team if m != me_id]
    members = [m for m in members if m != me_id and not _is_spare(flow, m)]
    # [평등 회의 — 소집자 잔재 청산(2026-07-21, U-037 실측·사용자: '소집자 개념조차 없는 평등한
    # 상태여야 하는데, 지명해도 걔가 말 안 하던데')] 어휘 중립화(07-14)가 구조엔 못 미쳤다 — 회의를
    # 연 봇이 참여자 목록에서 빠져 여는 의견 1회 뒤 응찰·지명 수신·DRAFT 편집이 전부 불가했고,
    # 안건의 주인(앵커=게임 기획자)을 지명한 8건이 조용히 증발해 '기획자 제시 대기'가 목표로 박제된
    # 채 가결됐다. SYS가 여는 단계 회의(현행 파이프라인 전 경로)는 흐름 태스크에서 돌아 개설자
    # 세션이 유휴이므로 개설자도 평참여자다(발언권 루프·심의 응찰·지명 대상·편집 전부). 봇이 툴로
    # 직접 연 회의만 종전 배제 유지 — 그 세션은 툴 결과를 기다리는 중이라 동시 wake가 세션 경합.
    if _sys_open and me_id in (flow.current.team or []) and not _is_spare(flow, me_id) \
            and me_id not in members:
        members = [me_id] + members
    if not [m for m in members if m != me_id]:
        return ("오류: 회의할 멤버가 없습니다.")
    _hold = _clarify_hold(flow, me_id)   # [G2 — clarify 행동 잠금(B-02)]
    if _hold:
        return _hold
    # [작업 단계 회의 가드(2026-07-17, ch78 실측)] 백로그가 서 있는 작업 단계의 회의는 등록 경로가
    # 없는(단계 None) 자유 회의 — 결론 없이 발언 예산만 태운다(재시작 복원·봇 습관 양쪽에서 반복 관측,
    # 회당 ~$5). '회의 하나=결론 하나' 계약: 지금 정할 것이 없으면 회의가 아니라 릴레이가 맞다.
    # (조건 갈등은 renegotiate_criterion, 도메인 질문은 request(Info), 다음 회의는 백로그 소진이 연다.)
    try:
        from .milestone import meeting_stage as _mg0, pipeline_on as _po0
        if _po0() and _mg0(flow) is None:
            _rls0 = getattr(flow, "backlog_relays", None) or {}
            if any(b.status in ("open", "in_progress", "blocked") for r in _rls0.values() for b in r.backlogs):
                if flow.log:
                    flow.log("meet_deferred_workstage", who=int(me_id))
                return ("[작업 단계] 지금은 정할 단계가 없고 집을 백로그가 남아 있습니다 — 회의 대신 "
                        "**pick_backlog로 하나 집어 실작업**을 진행하세요. 조건 문제는 renegotiate_criterion, "
                        "동료 질문은 request(Info)로. 다음 단계 회의는 백로그가 소진되면 시스템이 엽니다.")
    except Exception:
        pass
    if (any(not x.done() for x in getattr(flow, "inflight_tasks", ()))
            and flow.comm.alive != me_id and not flow.comm.done):
        return ("[대기] 직전 위임이 아직 진행 중입니다 — 회의는 그 결과를 받은 뒤 여세요.")
    if getattr(flow, "fork_active", 0) > 0:
        return ("[대기] 다른 의견 수집이 진행 중입니다 — 그 결과를 받은 뒤 여세요(중첩 수집 금지).")
    # [SYS 자동 개시(2026-07-14, 사용자: '기계적 단계는 SYS가 돌려')] SYS가 첫 회의를 자동으로 열 때는
    # (봇이 도구를 부른 게 아니라) comm alive 가드를 우회한다 — 개시자를 alive로 세워 정상 진행.
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
        nonlocal members   # [심의단 자기선택] 응찰 선발로 재바인딩 — 클로저 지역화 방지
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
        from .milestone import extract_stage_proposal as _stage_extract
        from .milestone import pipeline_on as _ms_on
        from .milestone import meeting_stage as _ms_stage, stage_agenda as _ms_agenda
        from .milestone import register_stage as _ms_regstage, stage_frame as _ms_frame
        _no_r1 = _ms_on()
        # [회의 하나당 결론 하나(2026-07-14, 사용자)] 이 회의가 정할 단 하나를 상태에서 유도 —
        # GOAL/마일스톤/서브태스크/백로그. 안건·수렴안 템플릿이 그 단계로 좁혀지고, 채택 시 그 단계만 등록.
        _stage = _ms_stage(flow) if _no_r1 else None
        if _stage:
            # [전역 회의 소속 태깅] 이 회의가 도는 동안 run_turn의 파이프라인 태깅이 SubTask를 생략
            # (주기까지만) — 전역 회의가 특정 단계 폴더로 접히는 오배치 차단. 해제는 meet() 완료 콜백.
            flow._stage_meeting = _stage
        _agenda, _stage_tmpl = _ms_agenda(_stage)
        if _agenda:
            from .milestone import stage_context as _ms_sctx
            _agenda = _agenda + _ms_sctx(flow, _stage)   # [정합 A] 어느 단위/주기 회의인지 안건에 명시
        _stage_frame = _ms_frame(_stage) if _stage else ""   # 매 발언 턴에 주입할 '이 회의의 정체' 프레임
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
            # [단계는 필드로(2026-07-17, 사용자: '공용처리·안정적 관리')] 개시 메시지에 런타임 단계를
            # 기계 마커로 스탬프 — 봇이 자기 topic으로 재개설해도 피드가 같은 단계 회의로 병합·라벨링.
            # (표시층 guide_format이 마커를 벗기고, feed_assembly가 필드로 승격 — 본문 스크래핑 아님.)
            await _say_speech(flow, me_id,
                              "[회의 시작]" + (f"[단계:{_stage}]" if _stage else ""), _preface)
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
        from .floor import (CLOSE_VOTE, SELF, FloorState, Turn, bid_threshold, floor_mode,
                            make_floor, round_robin, run_conversation)
        mode = floor_mode(getattr(flow, "floor_mode", None), default="orchestrated")
        tt = (mode == "turn-taking")
        # ══ [2층 stance seam(2026-07-18) — 발화 타입·인접쌍 기대(CA-Lab RFC-003 2층)] 기본 OFF:
        # ORGANT_FLOOR_L2=stance(또는 flow.floor_l2)이고 TT일 때만. 켜면 ①발언 첫 줄 [주장]/[질문]/
        # [반박]/[지지] 자기 선언을 Turn.stype으로 운반 ②[질문]/[반박]+[지명]이 인접쌍(답 의무)을 열고
        # ③합의 종결 직전 미해소 의무자에게 발언권 1회(StanceFloor 래퍼 — 배선은 정책 교체 한 줄).
        from .stance import StanceFloor, StanceLedger, floor_l2_mode, parse_stance
        _l2 = bool(tt and floor_l2_mode(getattr(flow, "floor_l2", None)) == "stance")
        _ledger = StanceLedger() if _l2 else None

        def _l2_obs(name, p):
            if flow.log:   # 관측: 인접쌍 수명주기 — CA-Lab 2층 실험(기대·해소·종결블록)의 원자료
                flow.log(name, opener=p.opener, target=p.target, ptype=p.ptype,
                         how=(p.resolved or None))

        def _l2_note(t):
            # [2층 장부 기입 = 턴 생성 지점(stance.py 계약)] next_after 기입은 예산 컷의 마지막 턴을
            # 유실한다(§6-2 동기 사례가 그 자리) — 여기서 전 턴을 정확히 1회 기입한다.
            if _ledger is not None:
                for _ev, _p in _ledger.note_turn(st.turn_no, t.speaker, getattr(t, "stype", None),
                                                 t.addressee, t.passed, participants=list(members)):
                    _l2_obs(_ev, _p)
            return t
        # [심의단 자기선택(2026-07-16, 사용자: '근본적으로')] 전원(8~12명) 심의·만장일치는 비용이 N²로
        # 자라는 근본 병목 — 회의도 선거·백로그와 같은 자기선택 원칙으로. 안건에 자기 도메인이 걸리는
        # 봇만 응찰해 심의단(상한 5)이 되고, 표결도 심의단 만장일치. 불참자는 피드로 보고 다음 라운드에
        # 응찰해 언제든 합류 가능(문 열림 — 중앙 배제 아님). 소수 팀(<6)은 종전대로 전원.
        # [심의단 = 비율(2026-07-20, 사용자: '고정 수치 박지 말고 비율로 — 합리적 트레이드오프')]
        # 종전 고정 상한 5는 팀 8이든 30이든 같은 폭 — 심의 폭↔비용은 팀 규모에 비례 배분이 맞다.
        # 상한 = ceil(팀 × ORGANT_PANEL_RATIO(기본 1/3)), 하한 2(표결 성립 최소). 트레이드오프:
        # 비율↑ = 도메인 커버리지↑·비용↑ / 비율↓ = 그 반대 — env 한 줄로 조정(수치는 정책이지 코드가 아님).
        try:
            _pratio = float(os.environ.get("ORGANT_PANEL_RATIO", "") or (1 / 3))
        except ValueError:
            _pratio = 1 / 3
        _panel_cap = max(2, int(len(members) * _pratio + 0.999))   # ceil
        _team_full = list(members)      # 심의단 선발 전 전체 로스터 — 무진전 시 확대 응찰의 후보 풀
        _refilled = {"on": False}       # [무진전 1차 대응 = 심의단 확대] 회의당 1회
        # [백로그 단계 = 심의단 축소 예외(2026-07-21, U-037/ch82 실측 — 사용자: 'PM이 남의 것까지 막
        # 발제')] 목표·주기·단위 회의는 '토론해서 하나를 정하는' 자리라 소수 심의가 맞지만, 백로그
        # 회의의 본질은 **각자 자기 도메인 몫을 등재**(자기 등재 원칙, 2026-07-14 사용자 확정 존재론:
        # 전담의 실체=백로그)다 — 심의단 3명으로 줄이면 판 밖 도메인 몫을 누군가 대필하게 되고, 발제
        # 귀속(작성자=주인)·릴레이(제출자=수행자)를 타고 소유가 독식된다(실측: PM이 30건 중 26건 발제
        # → 게임기획·백엔드 일까지 PM에게 순차 배정 궤도). 백로그 단계는 전원 참여로 각자 등재한다.
        # [병합 회의 교정(2026-07-23, ch94)] 지금은 subtask 회의가 영역+백로그를 함께 만든다. 예외를
        # 옛 stage 이름(backlog)에만 두면 병합 뒤 다시 1/3 심의단만 발제 기회를 얻는다.
        if _no_r1 and tt and len(members) > _panel_cap and _stage not in ("backlog", "subtask"):
            def _sb(c):
                # [도메인 가시화(2026-07-20, U-035)] 단계 안건은 일반문 — 응찰 판단엔 주제(원문 도메인)가
                # 먼저 보여야 한다('웹게임'이 안 보이면 게임 기획자가 패스한다).
                return (f"[회의 소집 — 심의 응찰] 주제: {str(topic)[:80]} / 안건: {(_agenda or topic)[:120]}\n"
                        f"이 결정에 당신({flow._info(c)}) 도메인이 직접 걸립니까? 걸리면 `[응찰: N]`(1~9)과 "
                        f"한 줄 이유, 아니면 `[패스]`. 불참해도 결과는 피드로 보이고, 회의가 이어지면 다시 "
                        f"응찰해 합류할 수 있습니다 — 심의는 소수 정예가 빠르게.")
            _sc = []
            for _m0, _r0, _n0 in await _fork_collect(flow, me_id, list(members), _sb, micro=True):
                _s0 = 0 if _r0 is None else _bid_score(_r0)
                if flow.log:   # [관측(2026-07-20, U-035 실측 갭)] 심의 응찰이 무기록이라 '왜 그 봇이
                    flow.log("meet_panel_bid", who=int(_m0), score=int(_s0))   # 빠졌나'를 진단 못 했다
                if _s0 > 0:
                    _sc.append((_s0, _m0))
            _sc.sort(reverse=True)
            _sel = [m0 for _s0, m0 in _sc[:_panel_cap]]
            # [도메인 커버리지 1석(2026-07-20, U-035 실측: 게임 판 목표 회의에 게임 기획자 무발언)]
            # 자기선택은 유지하되 — 안건 최고 적합 직군(role_fit)이 **응찰했는데** 점수순에서 밀렸으면
            # 1석 구제(cap+1). 패스했으면 자발 존중, 단 로그로 가시화(질 감사가 보게).
            try:
                from ..role_fit import role_fit as _prf
                # 적합 질의 = 주제+원문(도메인 어휘가 실린 곳) — 단계 안건은 일반문이라 부적합.
                _fitq = f"{topic} {str(getattr(flow, 'origin_request', '') or '')[:200]}"
                _fit = lambda m0: _prf(_fitq, str(flow.bot_info.get(int(m0)) or ""))
                _top = max(members, key=_fit)
                if _fit(_top) > 0 and _top not in _sel:
                    _bidded = next((s for s, m0 in _sc if m0 == _top), 0)
                    if _bidded > 0:
                        _sel.append(_top)
                        if flow.log:
                            flow.log("panel_topfit_added", who=int(_top), score=int(_bidded))
                    elif flow.log:
                        flow.log("panel_topfit_passed", who=int(_top))
            except Exception:
                pass
            if len(_sel) >= 2:
                members = _sel
                try:
                    _chp = (flow.current.thread_id if flow.current else None) or flow.user_channel
                    await flow.guide.post(int(_chp), 0,
                        "[심의단] 자기선택 " + str(len(members)) + "명 — "
                        + " · ".join(str(flow._info(m0) or m0) for m0 in members)
                        + " (안건에 도메인이 걸린 응찰자, 이후 라운드 재응찰로 합류 가능)")
                except Exception:
                    pass
                if flow.log:
                    flow.log("meet_panel_selected", n=len(members), stage=str(_stage))
        schedule = [(r, m) for r in range(2, rounds + 1) for m in members]
        budget = len(schedule)                # 토론 발언 예산 — 정책 불문 종전 라운드 비용과 동형
        wakes = {"n": 0}
        # [응찰 쿨다운(2026-07-18, wake 축소)] OPEN 수집에서 직전 패스 봇 제외 횟수 — 기본 0(현행).
        try:
            _cool_n = max(0, int(os.environ.get("ORGANT_BID_COOLDOWN", "0") or 0))
        except ValueError:
            _cool_n = 0
        # [발언 누진 임계 기울기] floor 정책(TurnTakingFloor._speak_step)과 같은 env·기본값 — 프롬프트
        # 고지용(판정은 정책이 함). 값이 갈리면 고지와 판정이 어긋나므로 한 env를 양쪽이 읽는다.
        try:
            _sstep = max(0, int(os.environ.get("ORGANT_SPEAK_BID_STEP", "1") or 1))
        except ValueError:
            _sstep = 1
        _cool = {}                            # 봇 → 남은 스킵 수집 수(_cooldown_probe가 감쇠)
        # 총 wake 상한(발언+응찰) — TT 비용·폭주 백스톱. 응찰은 open마다 후보 전원(≤인원-1)이라
        # 상한을 인원 배수로 잡는다(회의는 소수 인원 표면 — 응찰이 곧 '전원이 눈치보는' 비용).
        # [게이트 회의는 재응찰 여지 확보(2026-07-14, 사용자: '상한 두지 마라')] 수렴안 채택이 유일
        # 출구라 여러 패스가 필요할 수 있어 파이프라인 회의는 비용 천장을 4배로 — 인위적 라운드 상한은
        # 없고, 이 천장은 무의미 무한스핀(응찰 소진 후 no-op 반복) 방지용 비용 바닥일 뿐이다.
        wake_cap = budget * (len(members) + 1) * (4 if _no_r1 else 1) + 2
        # [수렴안 = 공동 편집 파일(2026-07-16, 사용자)] 회의 개시 때 SYS가 DRAFT.md 골격을 깔고,
        # 참여자들이 직접 편집·이의·해소로 파일에서 결론을 통합한다(단일 봇 통짜 생성·병합자 폐지).
        # 워크스페이스 없는 판(테스트·솔로)은 _draft_path=None → 종전 [수렴안] 채팅블록 경로 폴백.
        _draft_path = None
        _dstate = {"h": None, "stable": 0}
        _honor = {"nom": None, "used": False}   # [결론 직전 지명 존중(2026-07-21)] 회의당 1회
        if _no_r1 and tt and _stage and flow.current is not None:
            from .milestone import stage_draft_template as _ms_dtmpl, draft_status as _ms_dstat
            from .milestone import draft_to_proposal as _ms_dprop
            from .._util import dossier_read as _dread, dossier_write as _dwrite
            _tmpl = _ms_dtmpl(_stage, (_agenda or topic)[:120])
            if _tmpl:
                from .milestone import draft_should_reset as _dsr
                _ex = _dread(flow, "DRAFT.md")
                if _dsr(_stage, _ex):                       # [재시작-안전 불변식] 같은 단계 재회의면 진행분 보존
                    _dwrite(flow, "DRAFT.md", _tmpl)        # 새 단계·초안 부재만 새 골격
                if _dread(flow, "DRAFT.md") is not None:    # 쓰기 실패(워크스페이스 없음)면 폴백 유지
                    _draft_path = f"{dossier_rel(flow.current.task_id)}/DRAFT.md"
                    _stlbl = {"goal": "① GOAL", "milestone": "② 마일스톤", "subtask": "③ 서브태스크",
                              "backlog": "④ 백로그"}.get(str(_stage), str(_stage))
                    flow._meet_stage_note = f"{_stlbl} 회의 — DRAFT 함께 채우는 중"
                    try:
                        flow.note_activity(0, "🗳 회의 — 공동 결론 DRAFT 개설(함께 채우는 중)", force=True)
                    except Exception:
                        pass
        sched_i = {"i": 0}                    # orchestrated 라벨(r)용 — allocator 소비 순서와 1:1
        block = {"label": None, "items": []}  # [B-09] MINUTES.md 블록 버퍼(라운드/토론 단위 flush)

        def _flush_minutes():
            # [B-09] 블록(라운드) 단위 전문 append — 크래시해도 끝난 블록까지는 원본 보존(종전 동일).
            if block["items"]:
                head = "[토론]" if tt else f"[{block['label']} 토론]"
                dossier_append(flow, "MINUTES.md", f"## 회의 — {topic} {head}\n"
                               + "\n".join(f"[{block['label']}] {w}: {t}" for w, t in block["items"]))
                block["items"] = []

        _seen = {}   # 봇 -> 이미 본 minutes 개수(append-only 인덱스 = 사용자 안 보이는 유니크 값)
        def _ctx_for(bot):
            # [못 본 발언만 주입(2026-07-15, 사용자: '유니크 값 매기고 못 받은 메시지 찾아')] 매 턴 누적
            # 전체를 재주입하지 않는다 — 봇은 자기 세션 기억이 있고(resume), 전체 회의록은 MINUTES.md
            # 파일로 남는다. 프롬프트엔 그 봇이 아직 못 본 발언(마지막 본 인덱스 이후)만 담고, 앞은
            # 기억/파일에 맡긴다. (LLM 반복 주입 최소화·파일 활용이라는 설계 목표 그대로.)
            i = _seen.get(bot, 0)
            fresh = minutes[i:]
            _seen[bot] = len(minutes)
            _ref = (f"\n(전체 회의록은 작업공간 {dossier_rel(flow.current.task_id)}/MINUTES.md 를 필요할 때만 Read)"
                    if flow.current is not None else "")
            if not fresh:
                return "(당신이 아직 못 본 새 발언 없음 — 앞 발언은 당신 기억·MINUTES.md에)" + _ref
            return "\n".join(fresh) + _ref

        def _draft_lint():
            """[린터식 기계 집계(2026-07-17, ch78 실측: 꺾쇠 12→18 증식·동결)] 결정 구획을 막는 실제
            꺾쇠 목록+이의 수를 그대로 보고 — 내용 판단 없음. 발언 턴(도구 가능)과 종결 응찰 양쪽에 서빙."""
            if _draft_path is None:
                return ""
            try:
                from .milestone import draft_decision_region as _dr
                import re as _re
                _t = _dr(str(_dread(flow, "DRAFT.md") or ""))
                _phs = _re.findall(r"⟦[^⟧\n]{1,150}⟧", _t)
                _obj_ls = _re.findall(r"^\s*>\s*(.{0,60})", _t, _re.M)
                if not _phs and not _obj_ls:
                    return ""
                _ls = " · ".join(p[:40] for p in _phs[:8]) + (" …" if len(_phs) > 8 else "")
                # [이의도 원문으로(ch78 실측)] '[의견]'·'[검증 대기]' 같은 인용 메모도 전부 미해소로
                # 집계된다 — 어떤 줄이 막는지 원문을 서빙해야 봇이 해소(반영 후 삭제)하거나 참고로 옮긴다.
                _os = " · ".join(f"「{o.strip()}…」" for o in _obj_ls[:4])
                return (f"\n[기계 집계] 결정 구획의 빈 곳 {len(_phs)}개: {_ls} / 미해소 인용(>) {len(_obj_ls)}건"
                        + (f": {_os}" if _os else "") +
                        f"\n**지금 못 정하는 세부면 빈칸(⟦…⟧)을 지우고 '(후속: …)'로 바꾸거나 참고 구획으로 "
                        f"옮기세요. 인용(>) 줄은 의견·메모라도 미해소로 집계됩니다 — 반영했으면 그 줄을 "
                        f"삭제하고, 결정이 아니면 참고 구획으로.** 빈칸(⟦…⟧)·인용이 남는 한 회의는 안 닫힙니다.")
            except Exception:
                return ""

        def _mk_body(m, r, won=False, answer=False):
            """토론 발언 프롬프트 — r(int)=종전 라운드 문구(바이트 동일), r=None=TT(발언권 규약 동봉,
            won=응찰 낙찰 발언)."""
            log_txt = _ctx_for(m)
            if flow.log:
                # [B-09 Phase A 관측 지표] meet 재방송 자수 — R2+ 축소(B-11)의 절감 검산 베이스라인.
                flow.log("meet_r2_inject", chars=len(log_txt), r=(r or 0),
                         compressed=bool(doc_collab_on() and r1_full))
            _frm = (f"\n[이 회의의 자리] {_stage_frame}\n" if _stage_frame else "")
            if r is not None:
                return (f"[회의 {r}라운드] 주제: {topic}{_frm}\n못 본 발언:\n{log_txt}\n\n"
                        f"당신({flow._info(m)})의 차례입니다 — 위 안건에 당신 도메인 관점으로 답하세요"
                        f"(근거 필수, 맹목적 동의 금지). 3~5줄(최대 1000자). 이미 기록된 실측은 재실행하지 말고 원문(파일:줄·수치) 인용으로 갈음하세요.")
            # [수렴 쪽으로(2026-07-15, 사용자)] '동의/반박/보완'(=덧붙여라) 틀 제거 — 그게 무한 누적의
            # 원천이었다. 대신 안건에 답하되, 충분히 다뤄졌으면 새로 보태지 말고 [수렴안]을 제출하도록
            # 유도(제출 시 전원 찬반 표결 → 전원 찬성이면 확정·종료). 응찰 소진을 기다릴 필요 없음.
            head = ("[회의 토론 — 답 슬롯] 동료의 [질문]/[반박]이 당신의 답을 기다립니다 — 그에 답하세요"
                    "(짧아도 됩니다. 답할 수 없으면 그 이유가 답입니다)."
                    if answer else
                    "[회의 토론 — 발언권 획득(당신의 응찰이 선정됨)] 방금 응찰한 그 관점을 지금 발언하세요."
                    if won else "[회의 토론]")
            if _draft_path is not None:
                _sub = (f"\n\n**이 회의의 결론은 공동 파일 `{_draft_path}` 에서 만듭니다.** 지금 그 파일을 "
                        f"Read하고 셋 중 하나를 하세요: ①빈칸(⟦…⟧)·모호한 부분 중 **당신 도메인 몫을 Edit로 "
                        f"직접 채우기** ②이견은 해당 줄 아래 `> [이의 @{flow._info(m) or '직군'}] 한 줄` 추가 "
                        f"③남의 이의 해소(내용 고치고 그 이의 줄 삭제). 당신이 추가한 백로그 줄은 **당신이 발제자(그 일의 주인)**로 등록됩니다. 그 뒤 채널엔 **무엇을 바꿨는지 한 줄만** "
                        f"발언하세요(장문 금지). 바꿀 것이 없으면 `[패스]`만 — 자리표시·이의가 0이 되고 변경이 "
                        f"멎으면 전원 최종 표결로 확정됩니다.{_draft_lint()}")
            else:
                _sub = (f"\n\n**이 안건이 충분히 다뤄졌다고 보면, 새 의견을 보태지 말고 아래 [수렴안]을 발언에 "
                        f"담아 제출하세요 — 전원 찬반 표결에 부쳐지고 전원 찬성이면 확정·종료됩니다"
                        f"(미완이면 부결되니 다듬어 낼 것):**\n{_stage_tmpl}" if (_no_r1 and _stage_tmpl) else "")
            _l2_rule = (" 발언 성격은 첫 줄 `[주장]`/`[질문]`/`[반박]`/`[지지]`로 밝힐 수 있습니다 — "
                        "`[질문]`/`[반박]`을 `[지명]`과 함께 쓰면 그 동료의 답 전엔 회의가 닫히지 않습니다."
                        if _l2 else "")
            # [지명 정본 문법 서빙(2026-07-21)] 이름 지명은 동명이인 모호 — 봇 id 로스터를 프레임에
            # 실어 `[지명: <봇id>]`가 기본이 되게 한다(해석 불가 시 재전송 요구가 백스톱).
            _ros0 = " · ".join(f"{flow._info(x) or x}(id {x})" for x in members if x != m)
            return (f"{head} 주제: {topic}{_frm}\n못 본 발언:\n{log_txt}\n\n"
                    f"당신({flow._info(m)})의 차례입니다 — 위 안건에 당신 도메인 관점으로 답하세요"
                    f"(근거 필수, 맹목적 동의·이미 나온 것 반복 금지). 3~5줄(최대 1000자). 이미 기록된 실측은 재실행하지 말고 원문(파일:줄·수치) 인용으로 갈음하세요.{_sub}\n"
                    f"[발언권 규약] 특정 동료의 답이 꼭 필요하면 발언 마지막 줄에 `[지명: <봇id>]` "
                    f"(참여자: {_ros0}) — 더 보탤 것이 없으면 `[패스]`만.{_l2_rule}")

        async def _speech(m, body, label):
            """발언 1회 — 정책 불문 단일 실행 경로. 반환 Turn(지명·패스 신호) / None=회의 중단."""
            nonlocal last_full
            if flow.comm.done or flow.comm.alive != me_id or wakes["n"] >= wake_cap:
                return None
            _self_turn = (m == me_id)   # [평등 회의] 개설자 발언 — 베턴이 이미 그의 것이라 프레임 불요
            if not _self_turn:
                try:
                    flow.comm.request(me_id, m, "meet", Kind.INFO)
                except BusyInOtherFlow as e:
                    # 멤버 단위 사유(라운드 사이에 타 흐름이 데려감) — 회의를 끊지 않고 그
                    # 멤버만 건너뛴다(부분 진행). 베턴 경합(아래)과 달리 시스템 문제가 아니다.
                    minutes.append(f"[{label}] {flow._info(m) or m}: (타 흐름({e.holder_scope}) "
                                   f"참여 중 — 이 라운드 불참)")
                    return _l2_note(Turn(speaker=m, passed=True, body="(타 흐름 참여 중 — 불참)"))
                except CommError as e:
                    minutes.append(f"(회의 중단 — 베턴 경합: {str(e)[:60]})")
                    return None
            wakes["n"] += 1
            try:
                res = await flow.wake(m, body, Kind.INFO)
            except Exception as e:
                res = f"(발언 실패: {e})"
            if not _self_turn:
                try:
                    flow.comm.respond(m, "accept", res)
                except CommError:
                    pass
            addressee, passed = _turn_signals(flow, res, members) if tt else (None, False)
            # [지명 구조 강제 — 재전송(2026-07-21, 사용자: '평문에 이름 쓰지 말고 구조화, 안 맞으면
            # 다시 보내라고 SYS가 강하게 제한')] 지명 의도가 보이는데 해석 불가(형식·동명이인·참여자
            # 밖)면 무효로 삼키지 않고, 그 봇에게 정본 문법([지명: <봇id>]) 한 줄 재전송을 요구한다
            # (마이크로·발언당 1회 — 취소는 [패스]).
            if (tt and addressee is None and not passed
                    and re.search(r"\[\s*지명|\[\s*(?:주장|질문|반박|지지)\b[^\]]*@", res or "")):
                _ros = " · ".join(f"{flow._info(x) or x}(id {x})" for x in members if x != m)
                try:
                    _wk2 = getattr(flow, "wake_micro", None) or flow.wake
                    _fix = await _wk2(m, "[지명 형식 재전송] 방금 발언의 지명을 해석하지 못했습니다 — "
                                         "이름은 동명이인이 있을 수 있어 **봇 id로** 지명합니다. 딱 한 줄만 "
                                         f"다시 보내세요: `[지명: <봇id>]` (참여자: {_ros}). "
                                         "지명을 접으면 `[패스]`.", Kind.INFO)
                    if _fix and not str(_fix).strip().startswith("[패스]"):
                        addressee, _ = _turn_signals(flow, _fix, members)
                except Exception:
                    pass
                if flow.log:
                    flow.log("nominee_resent", who=int(m), resolved=bool(addressee))
                if addressee is None:
                    minutes.append("[안내] 방금 발언의 지명을 해석하지 못해 무효 처리됐습니다 — 지명은 "
                                   "`[지명: <봇id>]` 형식입니다. 팀 밖 직군이 필요하면 recruit로 충원하세요.")
            _who = flow._info(m) or m
            if passed:
                minutes.append(f"[{label}] {_who}: (패스)")
                return _l2_note(Turn(speaker=m, passed=True, body=res or ""))
            minutes.append(f"[{label}] {_who}: {_speech_clip(res)}")
            if block["label"] != label:
                _flush_minutes()
                block["label"] = label
            block["items"].append((_who, res))
            last_full = (_who, res)
            await _say_speech(flow, m, "[회의]" if tt else f"[회의 {label}]", res)  # 본인 명의([B-12] 매체 조건부)
            if m in flow.current.team and m != flow.leader:
                flow.current.participated.add(m)    # 회의 발언 = 실질 협의 인정
            # [수렴안 조기 제출(2026-07-15, 사용자: '발언 다 안 끝나도 수렴안 제출 → 거기서 찬반')] 응찰
            # 소진(종결표결)을 기다리지 않고, 토론 발언 중 누구든 [수렴안]을 내면 그걸 게이트 루프로 넘겨
            # 즉시 비준(전원 찬성)에 부친다 — 지명 릴레이가 응찰을 막아 종결표결이 안 열리던 교착 우회.
            if _no_r1 and _draft_path is not None:
                # [초안 종결 감지] 매 발언 후 DRAFT 상태 확인 — 자리표시 0·이의 0·직전 턴 무변경(안정)이면
                # 조기 종료 → 게이트 루프가 전원 최종 표결(한 명의 편집 직후 바로 닫히는 것 방지 = 안정 1턴).
                import hashlib as _hl
                from .milestone import draft_decision_region as _dregion
                _dtxt = str(_dread(flow, "DRAFT.md") or "")
                _h = _hl.md5(_dregion(_dtxt).encode()).hexdigest()   # 안정 판정 = 결정 구획만
                # [발제 귀속 — 턴별 diff(2026-07-16, 사용자: '발제한 애가 주인, 누가 발제했는지 남아야')]
                # 이 턴에 새로 나타난(또는 고쳐 쓴) '백로그:' 줄은 이 화자의 발제 — 봇 규약(태그) 없이
                # SYS가 구조적으로 추적한다. 마지막 실질 저자가 주인(고쳐 쓴 사람이 새 주인).
                from .milestone import draft_norm_line as _dnorm
                _cur_lines = {n for n in (_dnorm(l) for l in _dtxt.splitlines())
                              if n and n.startswith("백로그:")}
                _seen_lines = _dstate.setdefault("lines", set())
                _attr = _dstate.setdefault("attr", {})
                for _ln in _cur_lines - _seen_lines:
                    _attr[_ln] = int(m)
                _dstate["lines"] = _cur_lines
                if _h == _dstate["h"]:
                    _dstate["stable"] += 1
                else:
                    _dstate.update(h=_h, stable=0)
                _ph, _obj = _ms_dstat(_dtxt)
                from .milestone import draft_missing_key as _dmk
                _mkey = _dmk(_stage, _dtxt)
                if _mkey and _ph == 0 and _obj == 0 and "@형식" not in _dtxt:
                    # 등록 필수 키 부재 — SYS가 형식 이의를 기계 기록(가결-등록거부 루프 선차단)
                    _ref0 = _dtxt.find("\n## 참고")
                    _line0 = (f"> [이의 @형식] 결정 구획에 '{_mkey}' 줄이 **실제 결정으로** 필요합니다 — "
                              f"없거나 '(후속: …)' 미룸뿐이면 등록되지 않습니다(예: '{_mkey} 실제 한 줄 결정').")
                    _nd = (_dtxt[:_ref0].rstrip("\n") + "\n" + _line0 + "\n" + _dtxt[_ref0:]) if _ref0 > 0 else (_dtxt.rstrip("\n") + "\n" + _line0 + "\n")
                    _dwrite(flow, "DRAFT.md", _nd)
                    _dtxt = _nd; _ph, _obj = _ms_dstat(_dtxt)
                try:
                    flow.note_activity(0, f"🗳 결론 DRAFT — 빈칸 {_ph} · 이의 {_obj}", force=True)
                except Exception:
                    pass
                if _dtxt.strip() and _ph == 0 and _obj == 0 and _dstate["stable"] >= 1:
                    if flow.log:
                        flow.log("draft_ready", stage=str(_stage), stable=_dstate["stable"])
                    # [결론 직전 지명 존중(2026-07-21, U-038 재작업 — 사용자: '의견 부탁합니다 했는데
                    # 그냥 결론 짓고 종료')] 완성 컷이 표결로 직행하며 이 발언의 유효 지명이 증발하던
                    # 것 — 게이트 루프가 표결 전에 그 지명자에게 딱 1턴을 준다(회의당 1회 상한).
                    if tt and addressee and addressee != m and not _honor["used"]:
                        _honor["nom"] = addressee
                    return None                     # 조기 종료 → 게이트 루프가 최종 표결·등록
            elif _no_r1 and res:
                _cprop = _stage_extract(_stage, res)
                # [자리표시 가드(정합 C)] 템플릿 에코('⟦…⟧' 잔존)는 제출로 안 침 — 껍데기 조기종료 방지.
                import re as _re2
                if _cprop and not _re2.search(r"⟦[^⟧\n]{1,150}⟧", _cprop):
                    conv_props.append(_cprop)
                    if flow.log:
                        flow.log("consensus_in_discussion", who=int(m), stage=str(_stage))
                    return None                     # 조기 종료 → 게이트 루프가 비준·등록
            _sty = parse_stance(res) if _l2 else None
            if _sty and flow.log:
                flow.log("stance_turn", who=int(m), stype=_sty)
            return _l2_note(Turn(speaker=m, addressee=addressee, body=res or "", stype=_sty))

        async def _speak(speaker, alloc):
            if tt:
                _ans = (alloc.reason or "").startswith("2층")   # 종결 블록의 답 슬롯 배분
                return await _speech(speaker, _mk_body(speaker, None, won=(alloc.kind == SELF),
                                                       answer=_ans), "토론")
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
            # [응찰 쿨다운(2026-07-18, wake 축소)] OPEN 수집에서 직전 패스 봇을 _cool_n회 제외 —
            # 스킵 = 강도 0과 동형(비용 0). 종결 표결은 전원(회의 닫힘 판정이라 표본 축소 금지).
            probe = list(cands) if purpose == CLOSE_VOTE else _cooldown_probe(cands, _cool, _cool_n)
            def body_of(c):
                if purpose == CLOSE_VOTE:
                    # [결정권자 폐지 — 종결 표결이 곧 확정(2026-07-09, 사용자)] 파이프라인 회의에서
                    # [종료] 투표는 수렴안(완수조건 초안)을 동봉한다 — 가결되면 그 안이 그대로 등록된다
                    # (사람이 아니라 표결+등록 게이트가 확정). 확정 발화 권력의 비인격 대체.
                    # [단계별 수렴안(2026-07-14, 사용자: '회의 하나당 하나')] 이 회의의 안건에 맞는
                    # 좁은 수렴안만 요청한다 — 목표+마일스톤+단위를 한꺼번에가 아니라 이 단계 결론 하나.
                    if _draft_path is not None:
                        _conv = (f"\n마쳐도 되면 `[종료]`만 — 이 회의의 결론은 공동 파일 `{_draft_path}` "
                                 f"완성(자리표시 0·이의 0)으로 판정됩니다. 아직 빈 곳·이의가 있으면 "
                                 f"`[계속: N]`으로 발언권을 받아 파일을 다듬으세요.")
                    else:
                        _conv = (f"\n마쳐도 된다면 `[종료]` 다음 줄에 이 회의의 결론을 아래 형식으로 동봉하세요 "
                             f"(이 회의 안건 = **{_agenda}**):\n{_stage_tmpl}\n"
                             "(동료가 이미 낸 결론에 동의하면 그대로 복사·수정해 제출 — 전원 찬성 표결로 "
                             "채택됩니다. 이 회의 안건 밖의 것은 넣지 마세요 — 다음 단계 회의에서 정합니다.)"
                             if (_no_r1 and _stage_tmpl) else " 마쳐도 되면 `[종료]`만.")
                    # [린터식 게이트 보고(2026-07-17)] 기계 집계는 _draft_lint(발언 턴과 공유) — 어디가
                    # 막는지 모른 채 '<후속 협의>' 꺾쇠를 증식하던 것(12→18 실측)의 위치 서빙.
                    _gate = ((f"\n\n**이 회의는 결론 파일이 완성·가결돼야만 끝납니다 — 발언권 소진·지명 "
                              f"릴레이로는 안 끝납니다.** `{_draft_path}` 에 아직 빈 곳/이의가 남았거나 표결이 "
                              f"부결됐습니다 — 발언권을 받아 파일을 채우고 이의를 해소하세요.{_draft_lint()}")
                             if _draft_path is not None else
                             ("\n\n**이 회의는 위 형식의 결론이 채택돼야만 끝납니다 — 발언권 소진·지명 릴레이"
                              "로는 안 끝납니다.** 아직 채택된 결론이 없습니다. 마치려면 반드시 위 코드블록/형식"
                              "대로 결론을 동봉하세요(누구든). 없으면 회의는 닫히지 않고 다시 열립니다.")
                             ) if (_no_r1 and _gate_unmet["on"]) else ""
                    _l2_pend = ""
                    if _ledger is not None:
                        _pp = _ledger.pending_targeted(members)
                        if _pp:   # [2층] 표결자에게 왜 닫힘이 보류될 수 있는지 서빙(기계 집계, 판단 없음)
                            _l2_pend = ("\n[미해소 인접쌍] " + " · ".join(
                                f"{flow._info(p.target) or p.target}에게 "
                                f"{'질문' if p.ptype == 'question' else '반박'}"
                                f"({flow._info(p.opener) or p.opener})" for p in _pp[:3])
                                + " — 의무자의 답 전엔 합의 종결이 보류됩니다.")
                    return (f"[회의 — 종결 확인] 주제: {topic}\n못 본 발언:\n{_ctx_for(c)}\n\n"
                            f"발언이 소진됐습니다. 이 회의를 마쳐도 됩니까? 당신({flow._info(c)})이 "
                            f"판단하세요. 더 다뤄야 할 것이 있으면 `[계속: N]`(N=1~9)과 무엇인지 한 줄만 "
                            f"— 발언권을 받아 직접 발언하게 됩니다.{_l2_pend}{_conv}{_gate}")
                # [발언 누진 임계 고지(2026-07-21, U-036 재작업 #2)] 문턱은 floor 정책(resolve_open)이
                # 기계로 거르지만, 응찰자가 바를 모르면 미달 응찰만 반복한다 — 현재 임계를 그대로 서빙.
                _req = bid_threshold(len(members), _sstep)
                _bar = (f" 지금 참여 {len(members)}명 — 이번 판은 `[응찰: {_req}]` 이상만 발언권을 "
                        f"받습니다(그 미만은 패스와 동일 — 꼭 보태야 할 것만)." if _req > 1 else "")
                return (f"[회의 — 발언권 응찰] 주제: {topic}\n못 본 발언:\n{_ctx_for(c)}\n\n"
                        f"지금 발언권이 비어 있습니다. 당신({flow._info(c)})이 **지금** 발언할 필요가 "
                        f"있는지 스스로 판단하세요. 있으면 `[응찰: N]`(N=1~9, 필요 강도)과 한 줄 이유만 "
                        f"답하세요 — 발언 내용은 발언권을 받은 뒤에 말합니다. 없으면 `[패스]`만.{_bar}")
            out = []
            for m, res, note in await _fork_collect(flow, me_id, list(probe), body_of, micro=True):
                wakes["n"] += 1
                s = 0 if res is None else _bid_score(res)
                out.append((m, s))
                if purpose != CLOSE_VOTE and _cool_n > 0 and res is not None and s == 0:
                    _cool[m] = _cool_n           # [응찰 쿨다운] 명시 패스 → 다음 _cool_n회 수집 제외
                if purpose == CLOSE_VOTE and _no_r1 and res:
                    # [종결 표결 동봉 결론 수집] 단계별 형식으로 추출 — goal은 GOAL.md 파일블록, 그 외
                    # [수렴안]. 가결 시 register_stage로 자동 등록의 원료(제출 순서 보존).
                    _c = _stage_extract(_stage, res)
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
            """[수렴안 확정 표결(2026-07-14, 사용자: '찬성을 모두 받아야만')] 전원 찬성이어야 채택.
            반환 (passed: bool|None, dissents) — None=예산 소진으로 표결 자체가 못 돎(부결 아님 —
            소진 카운터에 세지 않는다. 2026-07-21, U-040 실측: 실표결 2회인데 유령 부결 1이 끼어
            '표결 3회 소진'으로 조기 확정, 사용자: '표결 2회인데 왜 3회라며 끊겼지')."""
            if wakes["n"] >= wake_cap:
                return None, [], 0
            def _rbody(c):
                # [단계 기준 심사(2026-07-16, ch75 실측)] 비준에 단계 프레임이 없어 봇들이 '완전한 기획
                # 문서' 기준으로 심사 — 다음 단계 몫(세부 메커닉·분해)이 없다고 반대를 쏟아 만장일치 불가.
                # 이 회의가 정하는 딱 하나 기준으로만 판단시키고, 표결은 즉답(도구 금지)으로 강제.
                _fr = (f"\n[이 회의가 정하는 것] {_stage_frame}" if _stage_frame else "")
                return (f"[회의 — 결론 확정 표결] 주제: {topic}{_fr}\n제출된 결론:\n{prop}\n\n"
                        f"**이 회의 안건 기준으로만** 확정 여부를 판단하세요 — 다음 단계 몫(세부 설계·작업 "
                        f"분해·구현 스펙)이 없다는 이유로 반대하지 마세요(그건 다음 회의들이 정합니다). "
                        f"이 안건의 결론으로 충분하면 `[찬성: 왜 충분한지 한 줄]`, 이 안건 범위에서 빠진 게 "
                        f"있으면 `[반대: 무엇이 빠졌는지 한 줄]`, 판단을 보류하면 `[기권: 왜 보류인지 한 줄]` — "
                        f"**찬성·반대·기권 어느 쪽이든 대괄호 안에 사유 한 줄이 반드시** 있어야 합니다"
                        f"(사유 없는 빈 표는 무효 — 반려되어 다시 요구합니다). 마커를 별표(*)로 감싸거나, "
                        f"대괄호 밖에 '근거:'·'판단:' 라벨·이 지시문 인용을 붙이지 마세요. 도구 호출·파일 확인 "
                        f"금지 — 지금 이 텍스트만 보고 즉답. 전원 찬성이어야 확정, 반대는 병합 후 재표결. "
                        f"찬성이어도 마지막 줄에 `[실패한다면: 한 줄]`을 덧붙이세요(사전부검 — 이 결론이 "
                        f"실패한다면 가장 그럴듯한 이유. 표에는 영향 없고 위험 기록으로만 남습니다).")
            # [확정 표결 = 전원(2026-07-21, U-039 실측 — 사용자: '의견은 못 했어도 찬반은 전체가
            # 참여해야지')] 종전엔 심의단(축소 후 members — 2~3명)만 표결해 '찬성 2 → 확정'으로 모호한
            # 결론이 쉽게 가결됐다. 발언(비용 큰 턴)은 심의단이 맡되, 찬반(마이크로 즉답)은 팀 전원 —
            # 비참여 도메인이 결론의 구멍(장르 미정 등)을 막을 표면을 갖는다. 반대=병합 원료(종전 동일).
            _voters = list(dict.fromkeys(list(_team_full) + list(members)))
            _yes, _dissents, _premortems, _against_ids = 0, [], [], []
            try:
                flow._meet_stage_note = f"표결 진행 중 — 전원 {len(_voters)}명"
                flow.note_activity(0, f"🗳 결론 확정 표결 진행 — 전원 {len(_voters)}명 응답 수집", force=True)
            except Exception:
                pass
            _ballots = []   # [표결 서사(2026-07-21, 사용자: '찬성·반대 이유 서사가 잘 보이게')] 전 투표자 (이름·표·사유)

            def _read_ballot(res):
                """[한 응답 → (vote, reason, premortems)] 표결 파서·사유 정규화·사전부검 분리를 한 곳에.
                - 파서 엄격화(2026-07-18): 명시 마커([찬성]/[반대] 또는 선두 토큰)만 확답, 그 외 기권.
                - 사전부검(2026-07-22): '[실패한다면: …]'은 표 사유 아닌 위험 기록 — 분리 수집.
                - 사유 정규화(2026-07-22 U-044): 마커 별표(**[찬성]**)·역할라벨·'근거:'·프롬프트 에코를
                  벗겨 실질 사유 한 줄만. 봇이 잡소리 서두 뒤에 [마커: 진짜사유]를 달면 그걸 위치 무관 채택.
                reason=='' 이면 사유 미기재(= 빈 표, 반려 대상)."""
                t = str(res or "")
                _v = _classify_vote(t)
                _pm = [p.strip() for p in
                       re.findall(r"\[\s*실패한다면\s*[::]\s*([^\]\n]{2,150})\]?", t)]
                _rn = t.replace("*", "").strip()                          # 마크다운 굵게/이탤릭 제거
                # [마커: 사유] 우선 — 찬성/반대/기권 어느 쪽이든 대괄호 안 사유를 위치 무관하게 집는다
                _mk = re.search(r"\[\s*(?:찬성|반대|기권)\s*[:：]\s*([^\]\n]{2,})", _rn)
                if _mk:
                    _r = _mk.group(1).strip("[]").strip()
                else:                                                     # 선두 마커·역할라벨·라벨·에코 제거
                    _l0 = _rn.split("\n", 1)[0].strip()
                    _cl = re.sub(r"^\[[^\]]{0,50}\]\s*", "", _l0)         # 선두 대괄호(마커/역할) 통째
                    _cl = re.sub(r"^(찬성|반대|기권)[\s:：\]]*", "", _cl)
                    _cl = re.sub(r"^(근거|판단|사유|이유)\s*[:：]\s*", "", _cl)
                    _cl = re.sub(r"^당신\s*\([^)]*\)\s*의?\s*판단[:：]?\s*", "", _cl).strip("[]").strip()
                    _r = _cl or next((x.strip().strip("[]").strip()       # 첫 줄이 마커뿐이면 다음 실질 줄
                                      for x in _rn.splitlines()[1:]
                                      if x.strip() and "실패한다면" not in x[:14]), "")
                return _v, _r[:150], _pm

            # [빈 사유 반려(2026-07-22, 사용자: '이유 없으면 기권이겠지 — 데이터가 빈다는 건 그 봇이
            # 사용법을 몰랐던 것. 반려해서 받아내야지')] 찬성이든 반대든 사유 없는 표는 무효로 보고, 그 봇에게
            # 한 번 더 사유를 요구한다(반려). 재요청이 사유를 담아오면 그걸로 교체, 그래도 비면 원표 유지.
            # [앵커 표 직접 수집(2026-07-22, GPT e2e 실측: 앵커가 fork에서 busy로 제외돼 '사유 없는 빈 기권'으로
            # 통과 — 반려도 안 걸린다)] 앵커(me_id)는 회의를 돌리는 당사자라 fork(busy 스킵)에 안 잡힌다. fork는
            # 나머지 표결자만 걷고, 앵커는 직접 깨워 사유와 함께 표를 받는다(전원 표결 + 사유 강제 완결).
            _resp = {m: res for m, res, _note in
                     await _fork_collect(flow, me_id, [v for v in _voters if v != me_id], _rbody, micro=True)}
            wakes["n"] += len(_resp)
            if me_id in _voters and me_id not in _resp and wakes["n"] < wake_cap:
                try:
                    _wm = getattr(flow, "wake_micro", None) or flow.wake
                    _resp[me_id] = await _wm(me_id, _rbody(me_id), Kind.INFO)
                    wakes["n"] += 1
                except Exception:
                    pass
            _redo = [m for m, res in _resp.items()
                     if res is not None and not _read_ballot(res)[1]]
            if _redo and wakes["n"] < wake_cap:
                try:
                    flow.note_activity(0, f"🗳 사유 빠진 표 {len(_redo)}건 반려 — 사유 재요청", force=True)
                except Exception:
                    pass
                def _redo_body(c):
                    _fr = (f"\n[이 회의가 정하는 것] {_stage_frame}" if _stage_frame else "")
                    return (f"[회의 — 표결 반려] 방금 낸 표에 **사유가 비어** 있었습니다. 표는 찬성·반대·기권 "
                            f"어느 쪽이든 **반드시 한 줄 사유**가 있어야 유효합니다(빈 표는 무효).{_fr}\n"
                            f"제출된 결론:\n{prop}\n\n다시 — `[찬성: 왜 이 결론으로 충분한지]`·`[반대: 무엇이 "
                            f"빠졌는지]`·`[기권: 왜 판단을 보류하는지]` 중 하나로, **대괄호 안에 사유를 담아** "
                            f"한 줄만 답하세요.")
                for m, res, _note in await _fork_collect(flow, me_id, _redo, _redo_body, micro=True):
                    wakes["n"] += 1
                    if res is not None and _read_ballot(res)[1]:          # 사유 담겨 온 것만 교체
                        _resp[m] = res

            for m in _voters:
                _vote, _reason, _pms = _read_ballot(_resp.get(m))
                _premortems.extend(_pms)                                  # 찬성자도 위험(사전부검)을 내놓는다
                if _vote == "against":
                    _dissents.append(_reason or "(사유 미기재)")
                    _against_ids.append((int(m), _reason or "(사유 미기재)"))   # 반대자=발언권 넘길 사람
                elif _vote == "for":
                    _yes += 1
                # abstain = 집계 안 함(찬성 오집계 방지) — 만장일치 게이트는 yes>=1이라 전원 기권이면 미통과
                # [투표자 id 동봉(2026-07-21, 사용자: '아바타 hover 프로필 안 뜬다')] 이름@봇id — 표시가
                # 공용 아바타(프로필·색)를 쓰게. feed_assembly가 who@id를 who+whoId로 분해.
                _ballots.append((f"{flow._info(m) or m}@{int(m)}", _vote, _reason))
                if flow.log:
                    flow.log("consensus_ratify_vote", who=m, vote=_vote)
            flow._ratify_against = list(_against_ids)   # [반대자 발언권(2026-07-22, 사용자)] 부결이면 이 사람들이
            #   발언권을 얻어 직접 고치거나 적임 직군을 지명 — 시스템이 담당을 추측하지 않는다
            # [표결 가시화(2026-07-16, 사용자: '회의 중 표결이 안 보여서 문제')] 결과를 채널 회의
            # 블록에 [표] 한 줄로 게시 — 종전엔 표결이 침묵이라 사용자 눈엔 판이 멈춘 것처럼 보였다
            # (ch74~75: 15분+ 무행). 개별 찬반이 아니라 집계+반대 요지만(이벤트 결과 1줄, 스팸 아님).
            # [사전부검 기계 병합] 수집된 위험을 초안 '## 참고'에 append(중복 억제) — 다음 라운드·
            # 다음 주기 회의가 위험 목록을 입력으로 갖는다(판정 대상 아님).
            if _premortems and _draft_path is not None:
                try:
                    _dpm = str(_dread(flow, "DRAFT.md") or "")
                    _new_pm = [p for p in dict.fromkeys(_premortems) if p[:40] not in _dpm]
                    if _dpm and _new_pm:
                        _dwrite(flow, "DRAFT.md", _dpm.rstrip("\n") + "\n[사전부검 — 실패한다면]\n"
                                + "\n".join(f"· {p}" for p in _new_pm) + "\n")
                except Exception:
                    pass
            _passed0 = (len(_dissents) == 0 and _yes >= 1)
            try:
                _vsum = (f"결론 확정 표결 — 찬성 {_yes} · 반대 {len(_dissents)}"
                         + (" → 확정" if _passed0 else
                            " / 반대 요지: " + " · ".join(d[:60] for d in _dissents[:3])))
                # [표결 서사 동봉(2026-07-21)] 집계 줄 아래 전 투표자의 표·사유를 구조 표기로 붙인다 —
                # feed_assembly가 '투표: 이름 | 표 | 사유' 줄을 파싱해 펼침 렌더의 원료로 승격(본문
                # 스크래핑 아님 — 정본 표기). 사유 없는 표는 표만.
                _vlabel = {"for": "찬성", "against": "반대", "abstain": "기권"}
                _vsum += "".join(f"\n투표: {nm} | {_vlabel.get(vt, vt)}"
                                 + (f" | {rs}" if rs else "") for nm, vt, rs in _ballots)
                _ch = (flow.current.thread_id if flow.current else None) or flow.user_channel
                await flow.guide.post(int(_ch), 0, f"[표] {_vsum}")   # SYS 명의(앵커 발언 착시 방지)
            except Exception:
                pass
            return _passed0, _dissents, _yes

        def _file_reg_objection(note):
            """[등록 거부 사유를 DRAFT 이의로 기록(2026-07-23, 사용자: '봇들이 반려됐을 때 반려된 이유를
            아는지')] 표결은 통과했는데 등록 게이트가 결론을 보류하면, 종전엔 사유가 채널 발언으로만 게시돼
            다음 라운드 wake 본문엔 안 실렸다 — 봇은 DRAFT가 '완성'으로 보여 같은 결론을 재표결(무한 루프).
            반대 부결의 '> [이의 @표결]'과 동형으로 '> [이의 @등록]'을 결정 구획(참고 직전)에 걸어, 완성
            게이트가 미해소로 잡고 다음 발언들이 형식을 실제로 고치게 한다(같은 사유 중복은 스킵)."""
            if _draft_path is None or not note:
                return
            try:
                _dt = str(_dread(flow, "DRAFT.md") or "")
                _ol = f"> [이의 @등록] {str(note)[:150]}"
                if _ol[:48] in _dt:
                    return
                _rf = _dt.find("\n## 참고")
                _dwrite(flow, "DRAFT.md",
                        (_dt[:_rf].rstrip("\n") + "\n" + _ol + "\n" + _dt[_rf:]) if _rf > 0
                        else (_dt.rstrip("\n") + "\n" + _ol + "\n"))
            except Exception:
                pass

        async def _merge_dissents(prop, dissents):
            """[반대 사유 병합(2026-07-15, 사용자: '자기거 없어서 부결난거면 그걸 합쳐야지')] 부결된
            수렴안에 동료들의 '빠졌다'는 지적을 다 합쳐 갱신 — 모두의 것이 들어갈 때까지 자라 만장일치가
            되게. 단일 봇의 저작이 아니라 '동료 지적을 기계적으로 병합'하는 서기 역할(재비준이 품질 담보)."""
            if wakes["n"] >= wake_cap:
                return prop
            _dlist = "\n".join(f"- {d}" for d in dissents[:12])
            _fr = (f"\n[이 회의가 정하는 것] {_stage_frame}" if _stage_frame else "")
            # [빈 슬롯 금지(2026-07-16, ch75 실측)] 병합자가 다음 단계 몫 지적을 '[待 …규정]' 빈칸으로
            # 표시하니 재비준이 빈칸=미완으로 또 부결(만장일치 불가 루프). 이 단계 범위 지적만 실값으로
            # 채우고, 다음 단계 몫은 수렴안에 넣지 않는다(다음 회의가 정함).
            _body = (f"[수렴안 병합 — 반대 사유 반영(서기 역할)]{_fr}\n현재 수렴안:\n{prop}\n\n"
                     f"동료들이 아래가 빠졌다고 반대했습니다:\n{_dlist}\n\n"
                     f"**이 회의 안건 범위의 지적만** 실제 내용으로 채워 수렴안을 갱신하세요 — 기존 것을 "
                     f"지우지 말고(당신 새 의견 추가 금지, 병합만). 다음 단계 몫(세부 설계·작업 분해·구현 "
                     f"스펙) 지적은 **수렴안에 넣지 마세요**(빈 자리표시·'[待 …]'·'추후 규정' 같은 미결 슬롯 "
                     f"금지 — 그건 다음 회의가 정합니다). 갱신된 [수렴안] 전문만 출력:\n{_stage_tmpl}")
            try:
                _wm = getattr(flow, "wake_micro", None) or flow.wake
                _res = await _wm(me_id, _body, Kind.INFO)
                wakes["n"] += 1
                return _stage_extract(_stage, _res) or prop
            except Exception:
                return prop

        st = FloorState(members)
        if not _no_r1:
            for m in members[:-1]:
                st.record(Turn(speaker=m, body="(1R)"))     # R1 발언을 침묵 장부에 반영(오퍼 공정성)
        policy = (make_floor("turn-taking") if tt
                  else make_floor("orchestrated", allocator=round_robin([m for _, m in schedule])))
        if _l2:
            # [2층] 래퍼 한 겹 — 배분 위임, 개입은 합의 종결 직전 미해소 인접쌍 1지점(stance.py §4).
            policy = StanceFloor(policy, _ledger, observer=_l2_obs)
        # [§4] 완전 TT의 시작 턴 = 소집자 발제(내용 발화가 아니라 주제 제시) — 이후 전 발언이 응찰.
        _t0 = (Turn(speaker=me_id, body="(발제)") if _no_r1
               else Turn(speaker=members[-1], body="(1R 마지막 발언)"))
        if _ledger is not None:
            _l2_note(_t0)   # 전 턴 기입 불변식 — 개시 턴도 1회(게이트 루프가 _t0를 재사용해도 여기뿐)
        # [게이트 = 채택된 수렴안(2026-07-14, 사용자: '회의에 상한 두지 말고 — 수렴안 채택돼야만 끝난다')]
        # 종료 조건을 '발언권 소진'이 아니라 '수렴안이 표결로 채택됨'으로 바꾼다. 파이프라인 TT 회의는
        # 한 패스 돌린다 → 이번에 [수렴안]이 제출됐나? 없으면 전원 발언권 되살려 재응찰(인위적 상한 없음).
        # 있으면 그 안에 전원 찬성 표결(_ratify_vote) → 통과하면 register_consensus로 채택·등록(GOAL.md
        # 생성) → 종료. 부결·등록거부면 회의 계속. 앵커 특권·거짓 완료·봇 파일작성 떠넘기기 없이 게이트가
        # 유일 출구. 비용 천장(wake_cap, 4배)에 닿으면 무의미 스핀 대신 정직히 상신(거짓 완료 아님).
        from collections import Counter
        _confirm_note = ""
        _landed, _conclusion = False, ""        # 이 단계 결론이 착지했나 + 결론 요지(회의 마무리 게시용)
        _pipe = bool(_no_r1 and tt)
        # [R1 브레인라이팅(2026-07-22, 사용자: 집단지능 문헌 반영 — NGT/브레인라이팅: 침묵 독립 기고가
        # 앵커링·발언 편중·생산 차단을 줄인다(Diehl&Stroebe·NGT 실증), Woolley 2010: 기회 균등이 c와
        # 상관, 2025-26 MAD 연구: 독립 생성+적당한 이견이 최적)] 토론 전에 전원이 병렬로 독립 기고
        # (무기억 마이크로 — 회당 ~0.1cr)하고 초안 '## 참고'에 **익명 병합**(지위 편향 축소, 판정
        # 대상 아님·발제 귀속은 결정 구획 편집이 정본이라 무간섭). 첫 발언 앵커 완화 + 전원 기회 보장.
        _r1_fresh = False
        if _pipe and _draft_path is not None:
            try:
                _ph0, _ = _ms_dstat(str(_dread(flow, "DRAFT.md") or ""))
                _r1_fresh = _ph0 > 0          # 자리표시 남음 = 첫 개회(재개설·속행이면 스킵 — 중복 기고 낭비 방지)
            except Exception:
                _r1_fresh = False
        if _r1_fresh:
            def _r1b(c):
                return (f"[독립 기고 — 토론 전 병렬 수집] 안건: {(_agenda or topic)[:160]}\n"
                        f"당신({flow._info(c)}) 도메인 몫으로 **결정 구획에 들어가야 할 구체 값·조건·"
                        f"이견 후보를 3~5줄**로 적어주세요(동료 발언은 아직 없습니다 — 독립 판단). "
                        f"**도구·파일 금지, 이 텍스트만 보고 즉답.** 자기 직군에 맡을 일이 정말 없으면 "
                        f"`[패스: 이유]`로 판단 근거를 남기세요(이유 없는 패스는 미응답으로 봅니다).")
            try:
                _r1n = 0
                _r1_lines = []
                _r1_attr = []      # [(bot_id, 기고 텍스트)] — 발제 귀속의 원저자(전사자 아님)
                _r1_passes = {}    # {bot_id: 이유} — '일 없음'의 개인 판단을 사후 추측하지 않게
                _r1_responded = set()  # 실질 기고자 — 소유 여부와 별개로 자기 판단 기회를 행사함
                _r1_targets = [m for m in members if m != me_id]
                _my_pass = re.match(r"^\[패스\s*:\s*(.+?)\]", my_view, re.S)
                if me_id in members and _my_pass and _my_pass.group(1).strip():
                    _r1_passes[int(me_id)] = _my_pass.group(1).strip()[:200]
                elif me_id in members and _is_substantive(my_view):
                    # SYS 개설자는 동시 세션 경합을 피해 R1 wake 대신 여는 의견으로 독립 판단을 낸다.
                    _r1_responded.add(int(me_id))
                for _m1, _r1, _n1 in await _fork_collect(flow, me_id, _r1_targets,
                                                         _r1b, micro=True):
                    _t1 = str(_r1 or "").strip()
                    _pm1 = re.match(r"^\[패스\s*:\s*(.+?)\]", _t1, re.S)
                    if _pm1 and _pm1.group(1).strip():
                        _r1_passes[int(_m1)] = _pm1.group(1).strip()[:200]
                    elif _t1 and not _t1.startswith("[패스") and "API Error" not in _t1[:20]:
                        _r1_responded.add(int(_m1))
                        _r1_lines.append(_t1[:700])
                        for _cl in _t1.splitlines():           # 줄 단위로 저자 보존(백로그 매칭용)
                            _cl = _cl.strip("·-* \t")
                            if len(_cl) >= 6:
                                _r1_attr.append((int(_m1), _cl[:200]))
                        _r1n += 1
                if _r1_lines:
                    _d1 = str(_dread(flow, "DRAFT.md") or "")
                    if _d1:
                        _blk1 = ("\n[R1 독립 기고 — 익명 병합(토론 전 병렬 수집·판정 대상 아님)]\n"
                                 + "\n".join(f"· {x}" for x in _r1_lines) + "\n")
                        _dwrite(flow, "DRAFT.md", _d1.rstrip("\n") + "\n" + _blk1)
                # [발제 귀속 = 원저자(2026-07-22, U-041 실측: 병합 회의에서 앵커가 결정 구획을 독점
                # 편집해 백로그 발제가 90% 앵커로 쏠림 — 남의 도메인까지 대필 귀속)] R1 기고를 낸 봇을
                # 백로그 등록기가 참조해, 전사자(앵커)가 아니라 실제 기고자에게 귀속시킨다(강제 배분
                # 아님 — 각자 자기 도메인을 R1에 냈으면 그 크레딧이 그에게 간다).
                flow._r1_attr = _r1_attr
                # SYS 단계 회의의 개설자도 평참여자다. 그는 동시 세션 경합 때문에 R1 wake만 생략하고,
                # 여는 의견/DRAFT로 자기 일감을 소유하거나 여는 의견에서 이유 있는 패스를 남겨야 한다.
                flow._r1_targets = {int(x) for x in members}
                flow._r1_passes = _r1_passes
                flow._r1_responded = _r1_responded
                if flow.log:
                    flow.log("meet_r1_brainwrite", n=_r1n, of=len(_r1_targets),
                             passes=len(_r1_passes), responded=len(_r1_responded))
            except Exception:
                pass
        _pass = 0
        _ready_rejects = 0   # [무한 반대의 차기 라우팅] 완성 파일 상태에서의 부결 횟수 — 3회 소진 후 이월 확정
        _prevote_seen = set()   # [표결 전 기여 관문] 도메인 점검 1턴을 이미 준 미발언 표결자 — 1회 상한(지각 합류는 새로 잡힘)
        _skip_discuss = False   # [이의 해소 fastpath(2026-07-20)] 해소 위임 직후엔 재토론 없이 재검·재표결
        _last_pass_hash = None  # [무진전 패스 감지(2026-07-20)] 초안 결정구획 해시 — 무변화 패스=즉시 중단
        while True:
            _pass += 1
            _before = len(conv_props)
            # 재응찰 = 전원 발언권 되살려 다시 토론(사용자 '발언권 다 살려 선택 응찰'). 회의가 단계별로
            # 작아져(회의 하나당 하나) 재토론 비용이 크지 않다 — 종전의 '효율 재응찰(수렴안만 요청)'은
            # 큰 회의 대응이었고 단계 분리로 불필요해져 폐지(2026-07-14).
            _ran_discuss = not _skip_discuss   # 이번 패스가 실제 토론을 돌았는가(무진전 판정의 전제)
            if not _skip_discuss:
                await run_conversation(policy, st, _t0,
                                       _speak, bid=(_bid if tt else None),
                                       max_turns=(budget if tt else budget + 1), on_alloc=_on_alloc)
            _skip_discuss = False
            _flush_minutes()
            # [결론 직전 지명 존중(2026-07-21)] 완성 컷에 실려온 지명자에게 답 슬롯 1턴 — 그 편집이
            # 초안을 되열면 아래 평가가 자연히 회의를 계속한다(상한 1 = 지명 릴레이 부활 아님).
            if _pipe and _honor["nom"] and not _honor["used"]:
                _honor["used"] = True
                _nm, _honor["nom"] = _honor["nom"], None
                if flow.log:
                    flow.log("meet_final_nominee_slot", who=int(_nm))
                await _speech(_nm, _mk_body(_nm, None, won=False, answer=True), "토론")
                _flush_minutes()
            if flow.current is None or not _pipe:
                break                                       # 솔로/orchestrated = 단일 패스(종전 동작)
            if _draft_path is not None:
                # [초안 모드 종결] 파일 상태가 유일한 진실 — 완성(자리표시 0·이의 0)이면 전원 최종 표결,
                # 가결 시 그 파일이 그대로 결론으로 등록된다. 부결·미완성이면 회의 계속(revive) — 반대자는
                # 표결문 요구대로 DRAFT에 이의를 남기므로 다음 패스가 그 이의를 해소하며 수렴한다.
                _dtxt = str(_dread(flow, "DRAFT.md") or "")
                _ph, _obj = _ms_dstat(_dtxt)
                # [등록 프리플라이트(2026-07-17, ch78 실측: 가결→등록거부 사이클에 $3~5×N)] 파일이
                # 완성돼도 등록 파서가 거부할 형식이면 표결이 낭비 — register_stage와 같은 검사를 표결
                # 전에 돌려 불량 전부를 '> [이의 @형식]'으로 파일에 기록(기계 검사 보고, 내용 판단 없음).
                # 봇 비용 0으로 발견하고, 다음 발언 턴들이 일괄 수리한다.
                _pre_errs = []
                if _dtxt.strip() and _ph == 0 and _obj == 0:
                    try:
                        from .milestone import stage_preflight as _ms_pre
                        _pre_errs = [e for e in _ms_pre(_stage, _dtxt) if e]
                    except Exception:
                        _pre_errs = []
                    if _pre_errs:
                        # [차단≠기록(2026-07-17, ch78 실측)] 봇이 이의 줄을 참고로 옮기면 '파일에 이미
                        # 있음' 중복 억제가 차단까지 꺼버려 표결→등록거부 루프 재발 — 차단은 검사 결과로,
                        # 기록만 중복 억제한다.
                        _pre_new = [e for e in _pre_errs if e[:40] not in _dtxt]
                        if _pre_new:
                            _blk0 = "\n".join(f"> [이의 @형식] {e[:200]}" for e in _pre_new[:6])
                            _ref0 = _dtxt.find("\n## 참고")
                            if _ref0 > 0:
                                _dwrite(flow, "DRAFT.md", _dtxt[:_ref0].rstrip("\n") + "\n" + _blk0 + "\n" + _dtxt[_ref0:])
                            else:
                                _dwrite(flow, "DRAFT.md", _dtxt.rstrip("\n") + "\n" + _blk0 + "\n")
                        try:
                            flow._meet_stage_note = f"등록 형식 미달 {len(_pre_errs)}건 — 수리 회의 계속"
                            flow.note_activity(0, f"🗳 등록 사전 검사 — 형식 미달 {len(_pre_errs)}건 DRAFT 기록, 수리 후 표결", force=True)
                        except Exception:
                            pass
                        if flow.log:
                            flow.log("meet_preflight_failed", passes=_pass, n=len(_pre_errs))
                if _dtxt.strip() and _ph == 0 and _obj == 0 and not _pre_errs:
                    # [표결 전 전원 기여 관문(2026-07-22, 사용자 U-041: '심의단 2명만 발언하고 안 말한
                    # 사람이 반대로 판 깨는 게 맞는 구조냐 — 표결권 있으면 발언 기회도')] 심의단(members)만
                    # 토론하고 전원(_team_full)이 표결하던 것 — 초안을 못 만진 표결자의 우려가 토론이 아니라
                    # 반대/기권표로 튀어 부결·재루프(U-041 실측: 브랜드 스토리텔러가 발언 없이 반대→4:1 부결→
                    # 사람 조치). 표결 직전, 아직 이 회의에 발언 못 한 표결자(비심의단·지각 합류)에게 초안을
                    # 보이고 병렬 micro 1턴: 도메인 몫이 빠졌으면 한 줄 이의, 없으면 [패스]. 이의는 결정
                    # 구획에 [이의]로 기록돼 표결이 안 열리고 다음 패스가 해소한다(우려가 표가 아니라 초안으로).
                    _absent = [m for m in _team_full
                               if m not in set(members) and m != me_id and m not in _prevote_seen]
                    if _absent and wakes["n"] < wake_cap:
                        _prevote_seen.update(_absent)
                        from .milestone import draft_decision_region as _dregionc

                        def _cvb(c):
                            return (f"[표결 전 도메인 점검] 곧 이 결론을 확정 표결합니다. 당신"
                                    f"({flow._info(c)}) 도메인에서 **빠졌거나 확정 전 꼭 짚을 게 있으면 "
                                    f"한 줄**로, 없으면 `[패스]`. 도구·파일 금지, 이 텍스트만 보고 즉답.\n"
                                    f"{_dregionc(_dtxt)[:1200]}")
                        _concerns = []
                        for _cm, _cr, _cn in await _fork_collect(flow, me_id, _absent, _cvb, micro=True):
                            wakes["n"] += 1
                            _ct = str(_cr or "").strip()
                            if _ct and not _ct.startswith("[패스]") and "Error" not in _ct[:16]:
                                _concerns.append((_cm, _ct.splitlines()[0][:150]))
                        if _concerns:
                            _dtxt = str(_dread(flow, "DRAFT.md") or "")
                            _refc = _dtxt.find("\n## 참고")
                            _blkc = "\n".join(f"> [이의 @{flow._info(cm) or cm}] {ct}"
                                              for cm, ct in _concerns[:6])
                            _dtxt = ((_dtxt[:_refc].rstrip("\n") + "\n" + _blkc + "\n" + _dtxt[_refc:])
                                     if _refc > 0 else (_dtxt.rstrip("\n") + "\n" + _blkc + "\n"))
                            _dwrite(flow, "DRAFT.md", _dtxt)
                            _ph, _obj = _ms_dstat(_dtxt)
                            if flow.log:
                                flow.log("meet_prevote_concern", raised=len(_concerns), of=len(_absent))
                            try:
                                flow.note_activity(0, f"🗳 표결 전 도메인 점검 — 미발언 표결자 이의 "
                                                   f"{len(_concerns)}건 반영, 해소 후 표결", force=True)
                            except Exception:
                                pass
                            continue    # 표결 안 열고 다음 패스 — 토론이 이의 해소(우려가 표가 아닌 초안으로)
                    from .milestone import draft_decision_region as _dregion2
                    _passed, _diss, _yes = await _ratify_vote(
                        f"(공동 결론 파일 {_draft_path} — **'## 결정' 구획만이 표결 대상**, 참고 구획은 "
                        f"근거 자료)\n{_dregion2(_dtxt)}\n\n"
                        f"※ 반대 요지는 시스템이 DRAFT.md에 [이의]로 기록해 다음 라운드가 해소합니다 — "
                        f"무엇이 빠졌는지 한 줄로 구체히 쓰세요. (완성 파일 기준 표결 3회가 소진되면 잔여 "
                        f"반대는 차기 주기로 이월 기록되고 결론이 확정됩니다.)")
                    # [무한 반대의 차기 라우팅(2026-07-17, ch78 실측: 완성 파일에 표결 5연속 부결 — 라운드마다
                    # 새 다듬기 반대 1건씩)] 반대는 무한 허용하되 종결을 못 막게 — 완성 상태 부결 3회 소진 후
                    # 잔여 반대는 참고 구획에 '[차기 이월]'로 기록하고 결론을 확정한다(억압 아님: 기록·가시·
                    # 다음 주기 회의의 입력). 재개설 예산 리셋으로 광택 루프가 무한해지던 것의 구조적 종결 보장.
                    if _passed is None:
                        # [유령 부결 차단(2026-07-21)] 예산 소진 = 표결 불능 — 부결로 세지 않고
                        # 루프의 소진 경로(wake_cap break)가 정직하게 마감한다.
                        if flow.log:
                            flow.log("meet_ratify_skipped_budget", passes=_pass)
                    elif not _passed:
                        _ready_rejects += 1
                    # [소진-확정에 다수결 바닥(2026-07-22, 사용자 U-044 실측: '찬성2·반대3인데 3회
                    # 소진으로 통과')] 3회 소진 이월-확정은 무한 교착 방지 장치지만, 마지막 라운드가
                    # 반대 우세(찬성<반대)면 확정하면 안 된다 — 소수 반대는 다수결로 넘기되(교착 방지
                    # 유지), 다수가 반대하는 안은 확정 못 하게. 반대 우세면 회의 계속(예산 천장이 마감).
                    _carry = ((_passed is False) and _ready_rejects >= 3
                              and (_yes or 0) >= len(_diss or []))
                    if _passed or _carry:
                        if _carry:
                            if _diss:
                                _dtxt3 = str(_dread(flow, "DRAFT.md") or "")
                                _blk3 = "\n".join(f"[차기 이월 @표결] {d[:150]}" for d in _diss[:5])
                                _dwrite(flow, "DRAFT.md", _dtxt3.rstrip("\n") + "\n\n" + _blk3 + "\n")
                            try:
                                _ch3 = (flow.current.thread_id if flow.current else None) or flow.user_channel
                                await flow.guide.post(int(_ch3), 0,
                                                      f"[표] 표결 3회 소진 — 잔여 반대 {len(_diss or [])}건 차기 주기 이월, 결론 확정")
                            except Exception:
                                pass
                            if flow.log:
                                flow.log("meet_dissent_carryover", n=len(_diss or []), passes=_pass)
                        flow._draft_attr = dict(_dstate.get("attr") or {})   # 발제 귀속 → 등록기
                        _ok, _note = _ms_regstage(flow, _stage, _ms_dprop(_stage, _dtxt), topic)
                        if _ok:
                            _landed, _conclusion = True, _note
                            _confirm_note = "\n\n" + _note
                            if flow.log:
                                flow.log("stage_confirmed", stage=str(_stage), passes=_pass, via="draft")
                            flow._meet_stage_note = None
                            # [마커 즉시 게시(2026-07-18, ch78 실측)] _pnote는 누적만 하고 도구 래퍼만
                            # flush — 회의-경유 등록의 [마일스톤 시작]·[SubTask 개설] 마커가 재시작에서
                            # 증발해 피드에 마일스톤 블록이 안 떴다. 등록 성공 즉시 여기서 flush.
                            try:
                                from .milestone import flush_pipeline_notes as _fpn
                                await _fpn(flow)
                            except Exception:
                                pass
                            break
                        if flow.log:
                            flow.log("stage_register_rejected", stage=str(_stage), reason=str(_note)[:80])
                        await _say_speech(flow, me_id, "[회의]",
                                          f"결론 파일이 등록 게이트에 보류됐습니다 — {_note} (DRAFT를 다듬어 재수렴)")
                        _file_reg_objection(_note)   # 채널 게시만으론 다음 라운드 wake에 안 실려 봇이 모른다 → 이의로 걸어 해소 강제
                    else:
                        # [부결 이의의 파일 반영 — SYS 서기(2026-07-16, ch76 실측)] 표결은 마이크로 즉답
                        # (도구 금지)이라 반대자가 이의를 파일에 못 남긴다 → 이의 0 유지 → ready→부결 무한.
                        # 반대 요지를 SYS가 '> [이의 @표결]'로 DRAFT에 자동 기록(중복 요지는 스킵) — 종결이
                        # 구조적으로 막히고, 다음 발언 턴들이 그 이의를 해소(내용 채움)하며 수렴한다.
                        _dtxt2 = str(_dread(flow, "DRAFT.md") or "")
                        _new = [d for d in (_diss or [])[:5] if d and d[:40] not in _dtxt2]
                        if _new:
                            _blk = "\n".join(f"> [이의 @표결] {d[:150]}" for d in _new)
                            _ref = _dtxt2.find("\n## 참고")
                            if _ref > 0:   # 결정 구획 끝(참고 구획 직전)에 삽입 — 판정 대상 유지
                                _dwrite(flow, "DRAFT.md", _dtxt2[:_ref].rstrip("\n") + "\n" + _blk + "\n" + _dtxt2[_ref:])
                            else:
                                _dwrite(flow, "DRAFT.md", _dtxt2.rstrip("\n") + "\n" + _blk + "\n")
                        try:
                            flow._meet_stage_note = f"표결 부결 — 이의 {len(_new)}건 해소 회의 계속"
                            flow.note_activity(0, f"🗳 표결 부결 — 이의 {len(_new)}건 DRAFT 기록, 해소 회의 계속", force=True)
                        except Exception:
                            pass
                        if flow.log:
                            flow.log("meet_consensus_rejected", passes=_pass, via="draft", filed=len(_new))
                        # [이의별 해소 위임(2026-07-20, 사용자: '부결의 근간')] 부결마다 전원 재토론 패스가
                        # 돌던 것(실측: 표결 12라운드·부결 7·$수십) — 이의는 그 도메인 적임 1명이 파일에서
                        # 해소하면 된다. role_fit으로 이의별 적임을 골라 편집 위임(실작업 wake) 후, 다음
                        # 루프는 재토론 없이(fastpath) 완성 검사→재표결로 직행. 해소가 안 됐으면(해시
                        # 무변화) 아래 무진전 감지가 전원 패스 폴백/중단을 판정 — 제한이 아니라 낭비 제거.
                        # [반대자 발언권 + 지명(2026-07-22, 사용자: 'a b가 반대했으니 a b가 발언권을 얻는
                        # 게 맞고, 다른 전문가 몫이면 그 직군에게 직접 발언권을 주도록 — 그 일이 누구 일인지
                        # 시스템은 모른다, 프론트 앵커 오배정처럼')] 종전엔 role_fit으로 시스템이 담당을 골라
                        # 위임(누구 일인지 추측)했다 — 폐지. 반대한 사람이 발언권을 얻어 직접 고치거나, 자기
                        # 몫이 아니면 [지명: <봇id>]로 적임에게 넘긴다(그 사람이 이어받아 고침).
                        try:
                            _nom_re = re.compile(r"\[지명[:：]\s*([0-9]+)\s*\]")
                            _mset = {int(x) for x in _team_full}   # 패널 밖 팀원도 지명 가능(그 일 적임)
                            _handled = set()
                            for _oid, _oreason in (getattr(flow, "_ratify_against", None) or [])[:5]:
                                if _oid in _handled:
                                    continue
                                _handled.add(_oid)
                                wakes["n"] += 1
                                _r = await flow.wake(int(_oid),
                                    f"[이의 해소] 당신이 반대했습니다: «{_oreason[:160]}». 공동 결론 파일 "
                                    f"`{_draft_path}` 를 **직접 고쳐** 이 이의를 해소하세요(Edit + 그 이의(>) 줄 "
                                    f"삭제) 후 한 줄 보고. 이게 **다른 직군의 몫**이면 고치지 말고 "
                                    f"`[지명: <봇id>]`로 그 사람에게 발언권을 넘기세요(그 사람이 이어 고칩니다).",
                                    Kind.INFO)
                                _nm = _nom_re.search(str(_r or ""))
                                if _nm and int(_nm.group(1)) in _mset and int(_nm.group(1)) not in _handled:
                                    _nid = int(_nm.group(1))
                                    _handled.add(_nid)
                                    wakes["n"] += 1
                                    await flow.wake(_nid,
                                        f"[이의 해소 — 지명받음] {flow._info(_oid) or _oid} 님이 이 이의를 당신 "
                                        f"몫으로 지명했습니다: «{_oreason[:160]}». 공동 결론 파일 `{_draft_path}` 를 "
                                        f"당신 도메인으로 고치고(Edit + 그 이의 줄 삭제) 한 줄 보고하세요.", Kind.INFO)
                            _skip_discuss = True
                            if flow.log:
                                flow.log("dissent_resolution_by_objector", n=len(_handled))
                        except Exception:
                            pass
                elif flow.log:
                    flow.log("meet_gate_unmet", passes=_pass, via="draft",
                             placeholders=_ph, objections=_obj)
                _gate_unmet["on"] = True
                # [무진전 패스 = 즉시 중단(2026-07-20, 사용자: '수치가 아니라 근간')] 이 패스가 초안
                # 결정구획을 한 글자도 못 바꿨으면 같은 반복은 같은 결과다 — 재픽과 같은 진전 판정.
                # 사람을 부르고 정직하게 닫는다(억지 반복 대신 상신 — 중단이지 완료 아님).
                try:
                    import hashlib as _hl2
                    from .milestone import draft_decision_region as _dr3
                    _hh = (_hl2.md5(_dr3(str(_dread(flow, "DRAFT.md") or "")).encode()).hexdigest()
                           if _draft_path is not None else None)
                except Exception:
                    _hh = None
                # [조합 수리(2026-07-20, U-035 라이브 실측)] 해소 위임(fastpath) 패스가 파일을 못
                # 바꿨을 땐 전원 재토론 폴백이 먼저다 — 브레이크는 '토론까지 돈 패스'의 무변화에만.
                # (종전엔 위임 실패 43초 만에 조기 중단+사람 호출 — 폴백 없이 브레이크가 선점했다.)
                if _hh is not None and _hh == _last_pass_hash and _ran_discuss:
                    # [무진전 1차 대응 = 심의단 확대(2026-07-21, 사용자: '대화가 더 필요한 상황에 결론
                    # 강제·회의 파괴가 답이냐 — 대화할 사람이 적어서 선 것')] ch82 실측: 2명 심의단이
                    # 발언만 돌다 섰고, 재개설(컷+킥오프)이 풀린 실이유는 '새 참여자'였다. 회의를 부수는
                    # 대신 같은 회의에서 아직 밖에 있는 팀원에게 재응찰을 돌려 합류시킨다 — 패널 안내문이
                    # 약속하던 '이어지면 재응찰로 합류'의 기계 이행(결론을 강제하지 않고 대화 상대를
                    # 늘린다). 회의당 1회 — 확대 후에도 무진전이면 종전대로 정직 중단(사람 상신).
                    _rest = [m for m in _team_full if m not in members]
                    if not _refilled["on"] and _rest:
                        _refilled["on"] = True

                        def _rfb(c):
                            return (f"[회의 소집 — 심의 응찰] 주제: {str(topic)[:80]}\n"
                                    f"이 회의가 진전 없이 서 있습니다 — 막힌 곳:{_draft_lint() or ' 결정 구획 미완'}\n"
                                    f"당신({flow._info(c)}) 도메인이 보탬이 되면 `[응찰: N]`(1~9)과 한 줄 "
                                    f"이유, 아니면 `[패스]`.")
                        _newly = []
                        for _m2, _r2, _n2 in await _fork_collect(flow, me_id, _rest, _rfb, micro=True):
                            wakes["n"] += 1
                            if _r2 is not None and _bid_score(_r2) > 0:
                                _newly.append(_m2)
                        if _newly:
                            members.extend(_newly)
                            st.participants.extend(int(x) for x in _newly)   # 침묵 장부 신입 = 첫 발언권 우선
                            if flow.log:
                                flow.log("meet_panel_refilled", n=len(_newly), stage=str(_stage))
                            try:
                                _chp2 = (flow.current.thread_id if flow.current else None) or flow.user_channel
                                await flow.guide.post(int(_chp2), 0,
                                    "[심의단] 합류 " + " · ".join(str(flow._info(x) or x) for x in _newly)
                                    + " — 회의가 막혀 재응찰로 확대")
                            except Exception:
                                pass
                            _last_pass_hash = _hh
                            _t0 = Turn(speaker=me_id, body="(심의단 확대 — 회의 계속)")
                            if wakes["n"] >= wake_cap:
                                if flow.log:
                                    flow.log("meet_gate_exhausted", passes=_pass)
                                break
                            continue
                    try:
                        await flow.guide.post(int(flow.user_channel), 0,
                                              "[사람 조치 필요] 회의가 진전 없이 맴돌아 여기서 멈춥니다 — "
                                              "안건을 구체화해 주시면 이어서 진행해요.")
                    except Exception:
                        pass
                    if flow.log:
                        flow.log("meet_no_progress_break", passes=_pass)
                    break
                _last_pass_hash = _hh
                _t0 = Turn(speaker=me_id, body="(결론 파일 미완/부결 — 회의 계속)")
                if wakes["n"] >= wake_cap:
                    if flow.log:
                        flow.log("meet_gate_exhausted", passes=_pass)
                    break
                continue
            _fresh = conv_props[_before:]                   # 이번 패스에 제출된 수렴안 후보
            if _fresh:
                _top = Counter(_fresh).most_common(1)[0][0]
                # [반대 사유 병합→재비준(2026-07-15, 사용자: '자기거 없어서 부결이면 합쳐야지')] 제출된
                # 수렴안이 '내 도메인 게 빠졌다'로 부결되면, 그 반대 사유들을 수렴안에 병합해 갱신하고
                # 재비준한다 — 모두의 것이 들어갈 때까지 자라 만장일치가 됨(완성된 수렴안). 상한까지
                # 병합해도 안 되면(무한 반대) 회의 계속(revive).
                _passed, _dissents, _ = await _ratify_vote(_top)
                _mrg = 0
                while (not _passed and _dissents and _mrg < 3 and wakes["n"] < wake_cap):
                    _mrg += 1
                    if flow.log:
                        flow.log("consensus_merge", round=_mrg, dissents=len(_dissents), stage=str(_stage))
                    _top = await _merge_dissents(_top, _dissents)   # 반대 사유 병합
                    _passed, _dissents, _ = await _ratify_vote(_top)   # 재비준
                if _passed:
                    _ok, _note = _ms_regstage(flow, _stage, _top, topic)   # 이 단계 결론 '하나'만 등록
                    if _ok:
                        _landed, _conclusion = True, _note
                        _confirm_note = "\n\n" + _note
                        if flow.log:
                            flow.log("stage_confirmed", stage=str(_stage), passes=_pass, merges=_mrg)
                        try:
                            from .milestone import flush_pipeline_notes as _fpn
                            await _fpn(flow)   # [마커 즉시 게시] 도구 래퍼 밖 등록도 피드 마커 유실 없음
                        except Exception:
                            pass
                        break                               # 채택 완료 — 회의 종료
                    if flow.log:
                        flow.log("stage_register_rejected", stage=str(_stage), reason=str(_note)[:80])
                    await _say_speech(flow, me_id, "[회의]",
                                      f"수렴안이 채택됐으나 등록이 보류됐습니다 — {_note} (다듬어 재수렴)")
                    _file_reg_objection(_note)   # 등록 거부 사유를 DRAFT 이의로 걸어 다음 라운드가 해소하게(채널 게시만으론 봇이 모름)
                elif flow.log:
                    flow.log("meet_consensus_rejected", passes=_pass, merges=_mrg)
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
        if _landed:
            flow._consec_stuck = 0            # [착지=진전] 막힘 카운터 리셋
        if _pipe and not _landed and not _confirm_note:
            # 게이트 미충족으로 비용 소진 종료 — 거짓 완료로 넘기지 않고 정직히 상신(사용자 확인 필요)
            if flow.log:
                flow.log("ms_consensus_empty", topic=str(topic)[:60], members=len(members))
            _confirm_note = ("\n\n[확정 실패 — 수렴 소진] 회의가 이 단계의 수렴안을 채택하지 못한 채 발언 "
                             "예산을 소진했습니다. **거짓 완료로 넘기지 않습니다** — 요구 명확화나 팀 재구성 "
                             "등 사람 확인이 필요합니다.")
            # [막히면 멈춤(2026-07-23, 사용자: '막히면 중지로 일단 멈춰두는 게 맞아보여')] 같은 단계가
            # 연속 2회 예산을 소진하면(1회 재시도 버퍼) 봇을 계속 굴려 토큰을 태우지 않고 판을 파킹한다 —
            # 여기선 신호만 세우고(_stage_stuck), 실제 정지(mark_stopped+안내)는 sys_core 이어가기 루프가
            # 집행(파킹 권한은 오케스트레이터 소관). 무한 재루프(관측: 소진→재회의→소진 4회+)를 끊는다.
            flow._consec_stuck = getattr(flow, "_consec_stuck", 0) + 1
            if flow._consec_stuck >= 2:
                flow._stage_stuck = str(_agenda or topic or "이 단계")[:80]
        # [회의 마무리 결론 게시(2026-07-14, 사용자: '회의를 접었을 때 발제된 이유와 결론이 보이면 좋겠다')]
        # 단계 결론이 착지하면 그 결론을 [회의 마무리] 발언으로 회의 블록 안에 넣어, 접힌 회의가 '왜
        # 열렸나(안건)+무엇으로 맺었나(결론)'로 읽히게 한다(collab_kind가 [회의 마무리]=meeting이라 같은 블록).
        if _landed and _conclusion:
            try:
                _concl = f"결론 ({_agenda}) — " + str(_conclusion).replace("[표결 확정] ", "").strip()
                await _say_speech(flow, me_id, "[회의 마무리]", _concl)
                minutes.append(f"[회의 마무리] {flow._info(me_id) or me_id}: {_concl}")
            except Exception as _e:
                if flow.log:
                    flow.log("meet_conclusion_post_failed", err=str(_e)[:100])
        # [발언 분포 관측(2026-07-22, 사용자: Woolley 2010 — c 요인은 턴 분포 균등성과 상관)] 회의별
        # 실발언 분포의 지니계수를 로그로 — 균등성↔결론 품질 상관을 실측으로 본 뒤에 조율한다
        # (측정 먼저, 행동 변경 없음).
        try:
            _cnt = {}
            for _t in st.history:
                if not _t.passed and _t.speaker in st.participants:
                    _cnt[_t.speaker] = _cnt.get(_t.speaker, 0) + 1
            _xs = sorted((_cnt.get(m, 0) for m in st.participants))
            _n2, _tot = len(_xs), sum(_xs)
            if _n2 > 1 and _tot > 0:
                _gini = sum((2 * (i + 1) - _n2 - 1) * x for i, x in enumerate(_xs)) / (_n2 * _tot)
                if flow.log:
                    flow.log("meet_turn_gini", g=round(_gini, 3), n=_n2, turns=_tot,
                             stage=str(_stage or ""))
        except Exception:
            pass
        return (f"[회의록] 주제: {topic} ({rounds}라운드, {len(members)}명)\n"
                   + "\n".join(minutes)
                   + (_confirm_note if _no_r1 else
                      "\n\n(수렴·확정은 당신(리더)의 몫 — 합의점을 정리해 set_goal/결정에 반영하세요.)"))

    inner = asyncio.ensure_future(_run_meet())
    flow.inflight_tasks.add(inner)
    inner.add_done_callback(flow.inflight_tasks.discard)
    # [전역 회의 소속 태깅 해제] 완료·취소·detach 어느 경로든 회의가 끝나면 SubTask 태깅 복원.
    inner.add_done_callback(lambda _t: setattr(flow, "_stage_meeting", None))
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
            # [직군 계열 중복 차단(2026-07-14, 사용자: '채널엔 10명인데 Task는 11명, 기획+게임기획자')]
            # 선거는 계열 dedup(기획⊂게임기획자)으로 직군당 1명을 뽑는데, 위임 자동합류가 이를 우회해
            # 같은 계열을 되불러들였다(ch69: 팀의 게임기획자 옆에 일반 기획이 위임으로 합류 → 11명).
            # 자동합류 전에 현 Task 팀에 같은 계열이 이미 있으면 그 동료로 리다이렉트(중복 합류 차단) —
            # 판정은 선거(_same_group)와 동일한 정규화 부분문자열.
            def _fam(mid):
                return "".join((flow._info(mid) or "").lower().split())
            _tn = _fam(to)
            # 리더 자신도 팀원 — 리더가 같은 계열이면 직접 하면 되므로 포함(m != me_id 제외 시 리더 계열이 새 구멍).
            _same_fam = [m for m in flow.current.team if not _is_spare(flow, m)
                         and _fam(m) and _tn and (_fam(m) in _tn or _tn in _fam(m))]
            if _same_fam:
                _dbg(f"{tag} ✗거부:계열중복(위임 자동합류 차단)")
                _who = ", ".join(f"{flow._info(m)}(id {m})" + ("(당신)" if m == me_id else "")
                                 for m in _same_fam)
                return (f"요청 거부: {flow._info(to)}(id {to})는 팀의 {_who}와 같은 직군 계열입니다 — 이 "
                        f"Task엔 직군당 1명입니다(선거 규칙과 동일). 그 동료가(당신이면 직접) 처리하세요"
                        f"(중복 합류·재시도 금지).")
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
