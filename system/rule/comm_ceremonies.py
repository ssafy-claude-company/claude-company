"""[M9 분할 — communication.py에서 추출] 협업 함수: vote·parallel_work·recruit.
파사드 보존 — communication.py가 이 모듈을 재수출해 기존 import 경로 유지.
meet은 request 결합이라 communication.py에 남김."""
import asyncio
import json
import os
import re
from ..protocol import Kind, Marker
from .comm_engine import BusyInOtherFlow, CommError
from .comm_helpers import (
    _JOB_SEP, _SPARE_LABEL, _add_members, _clarify_hold, _find_variant_job,
    _fork_collect, _group_of, _is_spare, _job_tokens, _jobs_of, _norm_job,
    _resolve_members, _say, _say_speech,
)


async def vote(flow, me_id, args):
    """[Communication Rule 로직] vote — 팀 표결(독립 수집→집계). @tool 래퍼가 _ok로 감쌈(평문 반환)."""
    from .._util import _speech_clip, _react
    from .task import _ckpt
    g = flow.guide
    if flow.current is None:
        return ("오류: 진행 중인 Task가 없습니다. create_task 먼저 여세요.")
    opts = [o.strip() for o in str(args.get("options", "")).split(";") if o.strip()]
    if len(opts) < 2:
        return ("오류: options에 선택지 2개 이상을 ';'로 구분해 주세요.")
    voters = _resolve_members(args.get("members", ""), flow, flow.current.team) or \
             [m for m in flow.current.team if m != me_id]
    voters = [v for v in voters if v != me_id and not _is_spare(flow, v)]
    if not voters:
        return ("오류: 표결할 멤버가 없습니다.")
    _hold = _clarify_hold(flow, me_id)   # [G2 — clarify 행동 잠금(B-02)]
    if _hold:
        return _hold
    if (any(not x.done() for x in getattr(flow, "inflight_tasks", ()))
            and flow.comm.alive != me_id and not flow.comm.done):
        return ("[대기] 직전 위임이 아직 진행 중입니다 — 표결은 그 결과를 받은 뒤 여세요.")
    if getattr(flow, "fork_active", 0) > 0:
        return ("[대기] 다른 의견 수집이 진행 중입니다 — 그 결과를 받은 뒤 여세요(중첩 수집 금지).")
    if flow.comm.done or flow.comm.alive != me_id:
        return (f"지금은 표결을 열 수 없습니다(활성={flow.comm.alive}) — 진행 중인 요청의 "
                   f"응답을 받은 뒤 다시 시도하세요.")
    question = str(args.get("question", "")).strip()

    detached = {"on": False}

    async def _run_vote():
        # [병렬 fork-join] 표는 서로 '독립'(앵커링 방지)이라 동시 수집이 의미를 바꾸지 않고
        # 시간만 줄인다 — 수집이 싸지면 표결을 아껴 쓰지 않게 된다(협동 빈도↑ = 품질).
        def body_of(v):
            return (f"[표결 — 독립 의견] 안건: {question}\n선택지: {' / '.join(opts)}\n"
                    f"동료들의 표는 보이지 않습니다(앵커링 방지). 당신의 전문가 관점에서 "
                    f"하나를 고르고 근거를 2줄 이내로. cast_vote(option, reason) 도구로 투표하거나, "
                    f"형식: [표] 선택지명\n근거")
        tally, reasons = {o: 0 for o in opts}, []
        full_lines = []                        # [B-09] MINUTES.md용 발언 전문(무절단 — 표기·유실 없음)
        dom_picks = {o: set() for o in opts}   # 옵션 → 그 옵션을 고른 '도메인'들(같은 직군 중복 제거)
        for v, res, note in await _fork_collect(flow, me_id, voters, body_of):
            # [B-15 — cast_vote 스태시 소비(이중 수용: 인자 > regex, [표] 폴백 존치)] 가지가 도구로 투표했으면
            # 그 정확한 option을 쓴다(무효표 소멸). 실패 가지의 stale 스태시도 여기서 pop(다음 표결 오염 방지).
            _stv = (getattr(flow, "vote_stash", None) or {}).pop(v, None)
            if res is None:
                reasons.append(f"{flow._info(v) or v}: {note}")
                full_lines.append(f"[표] {flow._info(v) or v}: {note}")
                continue
            pick = str((_stv or {}).get("option") or "").strip()
            if not pick:
                m = re.search(r"\[표\]\s*([^\n]+)", res or "")
                pick = (m.group(1).strip() if m else "")
            if not (res or "").strip() and _stv:
                res = str(_stv.get("reason") or "")   # 도구만 쓰고 빈 반환 — 근거는 스태시에서(판정자 가시성)
            chosen = next((o for o in opts if o in pick or pick in o), None)
            if chosen:
                # [동질 모델 — 표는 도메인(관점) 단위 집계] 같은 Claude·같은 직군 표는 같은 관점이라
                # N표가 아니라 1관점이다. 봇 수가 아니라 '다른 관점 수'로 세야 표결이 다양성을 반영
                # (같은 직군 3명이 같은 선택 = 3표가 아니라 그 직군 1표) — 봇 수 편향 제거. 도메인이
                # 갈리면(동질 모델이라 드묾) 각 옵션에 그 도메인을 1회씩 센다.
                _vd = {_norm_job(j) for j in _jobs_of(flow._info(v) or "")} - {""}
                _vdk = sorted(_vd)[0] if _vd else f"·{v}"
                if _vdk not in dom_picks[chosen]:
                    dom_picks[chosen].add(_vdk)
                    tally[chosen] += 1
            # [판정자 사본도 침묵 절단 금지] 리더는 이 근거로 표결을 '판정'한다 — 채널
            # 발언(400 안전망+잘림 표기)과 같은 내용이어야 한다. 종전 [:150] 하드컷은
            # 판정자가 동강난 근거로 결정하게 만들던 같은 부류의 결함(잘림 사건의 잔재).
            reasons.append(f"{flow._info(v) or v}: {(pick or '무효')} — {_speech_clip(res, 400)}")
            full_lines.append(f"[표] {flow._info(v) or v}: {(pick or '무효')} — {res}")
            await _say(flow, v, f"[표] {(pick or '무효')} — {_speech_clip(res, 400)}")  # 본인 명의 발언
            if v in flow.current.team and v != flow.leader:
                flow.current.participated.add(v)        # 표결 참여 = 실질 협의 인정
        board = " / ".join(f"{o}: {n}관점" for o, n in tally.items())
        if flow.current is not None:
            record = f"[표결] {question}\n{board}\n" + "\n".join(reasons)
            flow.current.collab_notes = _speech_clip(
                (getattr(flow.current, 'collab_notes', '') + '\n\n' + record).strip(), 6000)
            _ckpt(flow)
            # [B-09 Phase A — Task Dossier] 표결 전문을 MINUTES.md에 append(무절단 원본).
            # collab_notes의 6,000자 head-keep 캡이 '표기는 남고 내용은 소멸'시키던 것의 내용 보존.
            from .._util import dossier_append
            dossier_append(flow, "MINUTES.md",
                           f"## 표결 — {question}\n{board}\n" + "\n".join(full_lines))
        return (f"[표결 집계 — 도메인(관점) 단위] {question}\n{board}\n\n[각자의 선택·근거]\n"
                   + "\n".join(reasons)
                   + "\n\n(집계는 **도메인 단위** — 같은 직군 N명의 같은 선택은 동질 모델이라 1관점으로 "
                   + "합산(봇 수가 아니라 다른 관점 수). 참고일 뿐, 최종 판정은 당신(리더).)")

    inner = asyncio.ensure_future(_run_vote())
    flow.inflight_tasks.add(inner)
    inner.add_done_callback(flow.inflight_tasks.discard)
    try:
        return await asyncio.shield(inner)
    except asyncio.CancelledError:
        if not inner.done():
            detached["on"] = True
            if flow.log:
                flow.log("delegation_detached", to="vote", seg=flow.leader_segment)

            def _hand(t):
                try:
                    flow.detached_results.append(f"표결 완료 → {_speech_clip(t.result()['content'][0]['text'], 4000)}")
                except Exception:
                    pass
            inner.add_done_callback(_hand)
        raise


async def parallel_work(flow, me_id, args):
    """[Communication Rule 로직] parallel_work — guide_tools에서 이관(평문 반환, @tool이 _ok 래핑)."""
    from .._util import _speech_clip, _react, _dbg
    from .task import _ckpt
    g = flow.guide
    # [RFC-006 Work-fork v1] 검증된 fork 인프라(_fork_collect: 점유·부분 조인·FAN·detach-safe
    # 코어)에 Work 의미론(쓰기 리스·owner·실작업 판정)을 입힌다 — alive-집합 전면 개편 없이
    # '병렬 실행 + 직렬 통합'(RFC-005 P1)을 연다. 가지는 comm 프레임을 열지 않으므로 재위임
    # 불가(구조 강제) — 실측 근거: P-009·P-010 워커의 중첩 request 0회(막히면 보고→리더 직렬).
    # [병렬 비활성화 — 단일흐름 안정성(2026-06-22 사용자 결정)] 병렬 fork는 가지 에이전트의 작업공간
    # cwd 불일치 + 게이트#9(비-fork 전문가 idle 오발) + 쓰기리스로 Write를 잃어 산출물 0 churn을
    # 유발했다(P-029 규명). 전제가 '단일흐름 안정성'이므로 병렬 Work를 끄고 직렬(request)로 돌린다 —
    # 통합·검증은 어차피 직렬이라 손실 없음. 테스트는 _parallel_enabled로 실경로 검증(경로 수정 후 해제).
    if not getattr(flow, "_parallel_enabled", False):
        return ("[병렬 비활성화] 병렬 Work는 현재 비활성화돼 있습니다 — 작업공간/게이트 정합 문제로 "
                   "가지의 산출물이 유실되는 불안정이 확인됐습니다(P-029). **독립 영역도 request(Work)로 "
                   "한 명씩 직렬 위임**하세요(단일흐름 안정성 우선 — 통합·검증은 어차피 직렬).")
    if flow.current is None:
        return ("오류: 진행 중인 Task가 없습니다. create_task 먼저 여세요.")
    goal = (flow.current.status.goal or "").strip()
    if not goal:
        return ("오류: Goal 확정 전엔 병렬 위임 불가 — set_goal 먼저(분할은 합의된 목표 위에서).")
    if getattr(flow, "fork_active", 0) > 0:
        return ("[대기] 다른 수집/병렬이 진행 중입니다 — 조인 후 시도하세요(중첩 병렬 금지).")
    if (any(not x.done() for x in getattr(flow, "inflight_tasks", ()))
            and flow.comm.alive != me_id and not flow.comm.done):
        return ("[대기] 직전 위임이 아직 진행 중입니다 — 결과를 받은 뒤 병렬을 여세요.")
    try:
        items = json.loads(args.get("assignments") or "")
        assert isinstance(items, list) and items
    except Exception:
        return ('형식 오류: assignments는 JSON 배열 — 예: [{"to":"12","files":"public/app.js","body":"..."}]')
    fan = max(1, int(os.environ.get("ORGANT_FORK_FAN", "3")))
    if len(items) < 2:
        return ("병렬은 2건부터입니다 — 1건은 request(Work)로 위임하세요.")
    if len(items) > fan:
        return (f"병렬 폭 초과({len(items)} > {fan}) — 가장 독립적인 {fan}건만 먼저, 나머지는 조인 후.")
    ws = str(getattr(flow, "workspace", "") or "")
    plan = []
    for it in items:
        try:
            to = int(str(it.get("to")).strip())
        except Exception:
            return (f"형식 오류: to가 봇 id가 아닙니다: {it.get('to')!r}")
        if to == me_id:
            return ("자기 자신에게는 병렬 위임 불가 — 자기 몫은 조인 후 직접.")
        if to not in flow.current.team:
            return (f"요청 거부: {flow._info(to) or to}는 이 Task 팀이 아닙니다 — 팀에 더한 뒤 위임하세요.")
        if _is_spare(flow, to):
            return (f"요청 거부: {flow._info(to) or to}는 직군 미배정('예비') — recruit로 직군 부여 먼저.")
        files = [f.strip() for f in str(it.get("files") or "").split(",") if f.strip()]
        if not files:
            return (f"형식 오류: {flow._info(to) or to}의 files가 비었습니다 — 병렬의 전제는 영역 분리(리스).")
        body = str(it.get("body") or "").strip()
        if not body:
            return (f"형식 오류: {flow._info(to) or to}의 body(지시)가 비었습니다.")
        paths = [os.path.realpath(os.path.join(ws, f)) for f in files]
        plan.append((to, paths, body))
    tos = [p[0] for p in plan]
    if len(set(tos)) != len(tos):
        return ("같은 동료에게 두 영역 동시 배정 — 한 건으로 합치세요.")
    # [토큰 중립 조건 ⓐ — 기계 강제] 영역 상호 배타: 일치/포함이면 거부(겹침은 통합 충돌→Redo→토큰 손실).
    for i in range(len(plan)):
        for j in range(i + 1, len(plan)):
            for a in plan[i][1]:
                for b in plan[j][1]:
                    if a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep):
                        return (f"영역 겹침 거부: {flow._info(plan[i][0])} ↔ {flow._info(plan[j][0])} "
                                   f"({os.path.basename(a)}) — 겹치는 작업은 직렬(request)로.")
    notes = getattr(flow.current, "collab_notes", "")
    m2 = {to: (paths, body) for to, paths, body in plan}

    def body_of(m):
        paths, body = m2[m]
        files_txt = ", ".join(os.path.relpath(p, ws) if ws else p for p in paths)
        t = (f"[병렬 Work — 이 영역의 책임자는 당신] 이 Task의 Goal: {goal}\n"
             f"**당신의 쓰기 영역(리스): {files_txt}** — 이 파일들에만 씁니다. 다른 가지가 다른 "
             f"영역을 동시 작업 중이므로 영역 밖은 Read 참고만 하고, 필요한 변경은 보고의 "
             f"[리스크]에 적으세요. 동료 재위임은 불가(병렬 가지) — 막히면 막힌 지점을 보고하면 "
             f"리더가 직렬로 풉니다. 직군 밖이면 첫 줄 `[직군밖] 필요직군` 반려.\n"
             f"직접 구현하고 run으로 검증한 뒤, 보고 계약([결과]/[변경]/[검증]/[리스크])으로 간결히.\n"
             f"[요청 맥락] {body}")
        if notes:
            t += f"\n[팀 협의 기록(회의·표결) — 준수]\n{_speech_clip(notes, 6000)}"
        return t

    acts0 = {to: flow.act_by.get(to, 0) for to in tos}
    if getattr(flow, "write_lease", None) is None:
        flow.write_lease = {}
    for to, paths, _b in plan:
        flow.write_lease[to] = paths
    if flow.log:
        flow.log("parallel_work", n=len(tos), to=",".join(map(str, tos)), seg=flow.leader_segment)

    async def _run_parallel():
        try:
            results = await _fork_collect(flow, me_id, tos, body_of, kind=Kind.WORK)
        finally:
            for to in tos:
                flow.write_lease.pop(to, None)   # 조인=리스 해제(겹침 게이트는 가지 동안만)
        out = []
        for m, res, note in results:
            acted = flow.act_by.get(m, 0) - acts0.get(m, 0)
            if res is not None and flow.current and m in flow.current.team and m != flow.leader:
                flow.current.participated.add(m)
            if flow.current:
                flow.current.work_delegated += 1
            mark = "" if acted > 0 else " ⚠실작업 0(계획만 — 같은 영역 직렬 재위임 고려)"
            await _say(flow, m, f"[병렬 보고] {_speech_clip(res or note, 1500)}")
            out.append(f"[{flow._info(m) or m}]{mark}\n{_speech_clip(res or note, 4000)}")
        if flow.current and not flow.current.owner:
            flow.current.owner = tos[0]   # 기존 규칙(첫 Work 수신자=owner)과 일관 — 통합 기준점
            if flow.act_by.get(tos[0], 0) > acts0.get(tos[0], 0) and any(
                    m == tos[0] and r is not None for m, r, _n in results):
                flow.current.owner_delivered = True
        if flow.log:
            flow.log("parallel_join", n=len(results), seg=flow.leader_segment)
        _ckpt(flow)
        return (f"[병렬 조인 — {len(results)}건]\n" + "\n\n".join(out)
                   + "\n\n(통합·교차 검증·마감은 직렬로 — 겹치는 후속 작업은 request(Work) 한 명에게.)")

    inner = asyncio.ensure_future(_run_parallel())
    flow.inflight_tasks.add(inner)
    inner.add_done_callback(flow.inflight_tasks.discard)
    try:
        return await asyncio.shield(inner)
    except asyncio.CancelledError:
        if not inner.done():
            if flow.log:
                flow.log("delegation_detached", to="parallel", seg=flow.leader_segment)

            def _hand(t):
                try:
                    flow.detached_results.append(
                        f"병렬 조인 → {_speech_clip(t.result()['content'][0]['text'], 4000)}")
                except Exception:
                    pass
            inner.add_done_callback(_hand)
        raise


async def recruit(flow, me_id, role, args):
    """[Communication Rule 로직] recruit — **진짜 채용**(2026-07-09 사용자 설계).

    지명제 폐지: 리더·팀이 동료를 독단으로 데려오지 않는다.
      ① 공고 — recruit(role=직군, reason=무슨 일인지). 시스템이 후보(그 직군·유사 직군·예비,
         타 흐름 점유 제외, ≤4)를 깨워 지원을 받는다.
      ② 지원 — 후보 Organt가 스스로 정한다: [지원]+지원서(자기 경험 근거) 또는 [패스].
         지원서는 본인 명의로 채널에 게시된다(과정 = 콘텐츠).
      ③ 선발 — 공고자가 지원서를 읽고 recruit(member=지원자, reason=선발 사유)로 확정.
         **지원하지 않은 봇의 지명은 거부**(독단 영입 차단 — 이 함수의 반전 지점).
      유찰(후보 0·지원 0) = genesis 폴백: 그 직군 전문가를 신규 생성해 합류(채용 상속).
    발언권 1층(응찰=자기선택)의 멤버십판 — 합류도 자기선택 + 사회적 선발.
    유지 게이트: 범용 직군 금지·변형 직군 차단·1봇1직업(겸직 예외≤2)·일로 직업 획득(잠정 영속)·
    연속실패 채용중단. 자기 직군 확정(예비 담당자, Task 전)은 채용이 아니라 정체성 확정 — 종전 유지.
    """
    from .._util import _speech_clip, _react, _dbg
    from .task import _ckpt
    g = flow.guide
    role_name = (args.get("role") or "").strip()
    spec = (args.get("member") or "").strip()
    reason = str(args.get("reason") or "").strip()
    # [전문화 정책 — 범용 직군 금지(사용자 결정)] 범용(풀스택 등)은 모든 일을 흡수해 전문 채용을
    # 억제하고(라이브: AI·서버·데이터가 한 봇에 22건 집중) 병렬의 병목이 된다. 전문 직군으로 나눠 뽑는다.
    if role_name and any(gw in _norm_job(role_name)
                         for gw in ("풀스택", "풀 스택", "fullstack", "full stack", "full-stack",
                                    "제너럴", "generalist", "만능", "올라운드")):
        return (f"채용 거부(전문화 정책): '{role_name}' 같은 범용 직군은 두지 않습니다 — 범용은 모든 "
                f"일을 흡수해 전문 채용을 막고 병렬의 병목이 됩니다(1봇 1직업 전문화가 회사 원칙). "
                f"필요한 전문 직군으로 나눠 뽑으세요(예: 백엔드 / 프론트엔드 / AI 엔지니어 / 데이터 엔지니어).")
    # [직군 중복 생성 게이트 — 근본] 변형 이름('VFX 전문가' vs 'VFX 아티스트')로 같은 도메인 직군이
    # 불어나는 것 차단 — 재사용(기존 이름)이나 명시 신설(new_role='yes')은 에이전트가 정한다.
    if role_name:
        existing_jobs = {j for vv in flow.bot_info.values()
                         if vv and not str(vv).startswith(_SPARE_LABEL)
                         for j in _jobs_of(vv)}
        fn_roles = getattr(g, "get_custom_role_names", None)
        if fn_roles and getattr(flow, "guild_id", None):
            try:
                existing_jobs |= set(await fn_roles(flow.guild_id) or [])
            except Exception:
                pass
        dup = _find_variant_job(role_name, existing_jobs)
        if dup and _norm_job(args.get("new_role") or "") not in ("yes", "y", "true", "1"):
            if flow.log:
                flow.log("recruit_variant_blocked", asked=role_name, existing=dup)
            return (f"직군 중복 의심으로 보류: '{role_name}'은(는) 이미 있는 직군 '{dup}'의 변형으로 "
                    f"보입니다(같은 도메인을 다른 이름으로 또 만들면 직군이 계속 불어납니다). 같은 일이면 "
                    f"role='{dup}' 그대로 다시 호출해 기존 직군으로 공고하세요. 정말 '{dup}'과(와) 다른 "
                    f"일을 하는 새 직군이 필요하면 new_role='yes'를 함께 줘 명시적으로 신설하세요.")
    if flow.current is None:
        # [예비 담당자 '자기 직군 우선'] Task 열기 전에 담당자가 자기 직군부터 정하는 건 허용 — 자기
        # 자신 + role 지정일 때만(채용이 아니라 정체성 확정). 그 외는 Task가 먼저 있어야 한다.
        self_pick = _resolve_members(spec, flow, flow.pool) if spec else []
        if role_name and ((not spec) or (self_pick and self_pick[0] == me_id)):
            cur = (flow._info(me_id) or "").strip()
            new_label = role_name
            if cur and not _is_spare(flow, me_id):
                cur_jobs = _jobs_of(cur)
                if any(_norm_job(j) == _norm_job(role_name) for j in cur_jobs):
                    return (f"이미 '{role_name}' 직군을 보유하고 있습니다 — 그대로 진행하세요(변경 없음).")
                spares_left = [s for s in flow.pool if _is_spare(flow, s)]
                similar = any(_job_tokens(j) & _job_tokens(role_name) for j in cur_jobs)
                if spares_left and not similar:
                    return (f"자기 직군 추가 거부: 당신은 이미 '{cur}' 직군입니다 — **1봇 1직업** 원칙이라 "
                            f"무관한 직군('{role_name}') 겸직은 예비가 없거나 비슷한 일일 때만 허용됩니다"
                            f"(전문화 보호). '{role_name}'이 필요하면 Task를 연 뒤 recruit(role='{role_name}')로 "
                            f"공고를 올려 지원을 받으세요(예비 {len(spares_left)}명).")
                if len(cur_jobs) >= 2:
                    return (f"겸직 한도 초과: 당신은 이미 직군 2개('{cur}')를 보유하고 있습니다 — 봇당 "
                            f"겸직은 최대 2개입니다. '{role_name}'은 공고를 올려 다른 동료가 맡게 하세요.")
                new_label = f"{cur}{_JOB_SEP}{role_name}"
            flow.bot_info[me_id] = new_label
            if getattr(flow, "persist_role", None):
                try:
                    flow.persist_role(me_id, new_label)
                except Exception:
                    pass
            fn = getattr(g, "assign_job_role", None)
            if fn and getattr(flow, "guild_id", None):
                try:
                    await fn(flow.guild_id, me_id, new_label)
                except Exception:
                    pass
            what = "겸직 추가" if _JOB_SEP in new_label else "확정"
            return (f"자기 직군 {what}: 당신(id {me_id})의 직군 = '{new_label}' — 한 직원으로 "
                    f"참여합니다. 이어서 create_project → create_task로 팀을 꾸려 시작하세요.")
        return ("오류: 진행 중인 Task가 없습니다. 먼저 create_task로 Task를 여세요. (단 '예비' 담당자가 자기 "
                "직군을 정하는 recruit(member=자신, role=…)는 Task 전에도 됩니다 — 자기 직군부터 정하세요.)")
    # 충원 루프 하드 차단: 최근 요청 연속 2회+ 실패면 채용 중단(무한 충원 루프 방지 — '백엔드 6명' 사태).
    if getattr(flow, "consec_fail", 0) >= 2:
        return (f"채용 보류: 최근 요청이 연속 {flow.consec_fail}회 무응답/실패 — 시스템 일시 불안정입니다. "
                f"지금 새로 뽑아도 같이 실패하니 채용을 막습니다(무한 충원 루프 방지). 기존 동료에게 잠시 뒤 "
                f"다시 요청해 한 명이라도 응답이 오면 그때 충원하거나, 계속 안 되면 사용자에게 보고하고 멈추세요.")

    open_p = getattr(flow, "recruit_open", None)

    # ── ③ 선발 확정(member=) — 지원자 중에서만 ────────────────────────────────
    if spec:
        cand = _resolve_members(spec, flow, flow.pool)
        if not cand:
            return (f"선발 불가: '{spec}'을(를) 풀에서 못 찾았습니다. 현재 풀: {flow._names(flow.pool)}")
        mid = cand[0]
        if mid == me_id:
            return ("자기 자신은 채용 대상이 아닙니다 — 자기 직군 확정은 Task 열기 전에만 가능합니다.")
        if mid in flow.current.team:
            if not role_name:
                return (f"{flow._info(mid) or mid}은(는) 이미 현재 Task 팀입니다 — 채용 불필요.")
            # [팀 내 재배치·겸직 — 영입 아님] 이미 팀인 동료의 직군 추가는 공고 대상이 아니다(합류가
            # 아니라 라벨 변경). 1봇1직업·겸직 예외(예비 0/유사 일)·한도 2는 그대로 강제.
            cur = (flow._info(mid) or "").strip()
            if cur and any(_norm_job(j) == _norm_job(role_name) for j in _jobs_of(cur)):
                return (f"{flow._info(mid) or mid}은(는) 이미 '{role_name}' 직군을 보유 — 변경 없이 "
                        f"그대로 진행하세요.")
            tentative = False
            if cur and not _is_spare(flow, mid):
                cur_jobs = _jobs_of(cur)
                spares_left = [s for s in flow.pool if _is_spare(flow, s)]
                similar = any(_job_tokens(j) & _job_tokens(role_name) for j in cur_jobs)
                if spares_left and not similar:
                    return (f"겸직 거부: {cur}(id {mid})는 이미 '{cur}' 직군입니다 — **1봇 1직업** 원칙이라 "
                            f"무관한 직군('{role_name}') 겸직은 예비가 없거나 비슷한 일일 때만 허용됩니다"
                            f"(전문화 보호). 필요하면 recruit(role='{role_name}')로 공고를 올려 지원을 받으세요.")
                if len(cur_jobs) >= 2:
                    return (f"겸직 한도 초과: {flow._info(mid) or mid}(id {mid})는 이미 직군 2개('{cur}') "
                            f"보유 — 봇당 겸직은 최대 2개입니다.")
                new_label = f"{cur}{_JOB_SEP}{role_name}"
            else:
                new_label = role_name
                flow.tentative_roles[mid] = role_name      # 예비 팀원(리더 등) — 일로 획득 시 영속
                tentative = True
            flow.bot_info[mid] = new_label
            if getattr(flow, "persist_role", None) and not tentative:
                try:
                    flow.persist_role(mid, new_label)
                except Exception:
                    pass
            fn = getattr(g, "assign_job_role", None)
            if fn and getattr(flow, "guild_id", None) and not tentative:
                try:
                    await fn(flow.guild_id, mid, new_label)
                except Exception:
                    pass
            flow.current.status.group = _group_of(flow, flow.current.team)
            what = "겸직 추가" if _JOB_SEP in new_label else "직군 부여"
            return (f"{flow._info(mid) or mid} {what}(팀 내 재배치): '{new_label}'"
                    + (f" (사유: {reason})" if reason else ""))
        if not open_p:
            return ("지명 채용은 폐지됐습니다(독단 영입 차단) — 동료가 필요하면 먼저 "
                    "recruit(role='직군', reason='무슨 일을 하게 되는지')로 **필요를 공고**해 "
                    "지원을 받으세요. 지원서가 돌아오면 그중에서 member=로 선발합니다.")
        if mid not in open_p["applicants"]:
            names = ", ".join(f"{flow._info(a) or a}(id {a})" for a in open_p["applicants"])
            return (f"선발 불가: {flow._info(mid) or mid}은(는) 이 공고에 지원하지 않았습니다 — "
                    f"지원자 중에서만 선발할 수 있습니다(독단 영입 차단). 지원자: {names or '없음'}")
        role_for = open_p["role"]
        joined = await _recruit_join(flow, mid, role_for, via="선발")
        if joined is not None:
            return joined                      # 게이트 거부 문구(겸직 등)
        flow.recruit_open = None
        if flow.log:
            flow.log("recruit_awarded", role=role_for, to=mid, applicants=len(open_p["applicants"]))
        await _say(flow, me_id, f"[채용 확정] {flow._info(mid) or mid} — '{role_for}' 공고 선발"
                                + (f" (사유: {reason})" if reason else ""))
        return (f"{flow._info(mid) or mid} 선발·합류 — '{role_for}' 공고에 대한 지원서 기준"
                f"{('(사유: ' + reason + ')') if reason else ''}. 현재 팀: {flow._names(flow.current.team)}")

    # ── ① 공고(role=) — 후보를 깨워 지원을 받는다 ─────────────────────────────
    if not role_name:
        return ("공고 방법: recruit(role='직군', reason='무슨 일을 하게 되는지')로 팀의 필요를 올리세요 — "
                "시스템이 후보들에게 공고를 돌려 지원서를 모아 돌려줍니다. 동료 지목(member=)은 "
                "지원자 선발 확정에만 씁니다.")
    _hold = _clarify_hold(flow, me_id)
    if _hold:
        return _hold
    if (any(not x.done() for x in getattr(flow, "inflight_tasks", ()))
            and flow.comm.alive != me_id and not flow.comm.done):
        return ("[대기] 직전 위임이 아직 진행 중입니다 — 공고는 그 결과를 받은 뒤 올리세요.")
    if getattr(flow, "fork_active", 0) > 0:
        return ("[대기] 다른 의견 수집이 진행 중입니다 — 그 결과를 받은 뒤 공고하세요(중첩 수집 금지).")
    if flow.comm.done or flow.comm.alive != me_id:
        return (f"지금은 공고할 수 없습니다(활성={flow.comm.alive}) — 진행 중인 요청의 응답을 받은 뒤 다시.")
    if open_p:
        await _say(flow, me_id, f"[채용 공고 교체] 종전 '{open_p['role']}' 공고를 닫고 새 공고를 올립니다.")
        flow.recruit_open = None

    # 후보: 그 직군 보유 > 유사 직군 > 예비(무직). 현재 팀·타 흐름 점유 제외, 상한 4.
    eng, scope = flow.comm.engagement, flow.comm.scope

    def _free(m):
        return not (eng is not None and scope is not None and eng.busy_elsewhere(m, scope))

    exact, similar, spare = [], [], []
    for m in flow.pool:
        if m == me_id or m in flow.current.team or not _free(m):
            continue
        info = (flow._info(m) or "").strip()
        if _is_spare(flow, m) or not info:
            spare.append(m)
        elif any(_norm_job(j) == _norm_job(role_name) for j in _jobs_of(info)):
            exact.append(m)
        elif any(_job_tokens(j) & _job_tokens(role_name) for j in _jobs_of(info)):
            similar.append(m)
    cands = (exact + similar + spare)[:4]

    posting = (f"[채용 공고] 직군: {role_name}" + (f" — {reason}" if reason else "")
               + f"\n프로젝트: {getattr(flow, 'project_name', '') or '(미등록)'}"
               + (f" · 현재 Task: {_speech_clip(flow.current.status.purpose or '', 80)}"
                  if flow.current.status.purpose else ""))
    await _say(flow, me_id, posting)
    if flow.log:
        flow.log("recruit_posted", role=role_name, candidates=len(cands))

    applicants = {}
    if cands:
        flow.fork_active += 1          # 수집 중 중첩 방지(meet·fork와 같은 가드)
        try:
            for m in cands:
                try:
                    flow.comm.request(me_id, m, "recruit", Kind.INFO)
                except BusyInOtherFlow:
                    continue           # 공고 도는 사이 타 흐름이 데려감 — 그 후보만 건너뜀
                except CommError:
                    break              # 베턴 경합 — 수집 중단(지원 0으로 처리)
                me_info = (flow._info(m) or "").strip()
                body = (f"{posting}\n\n[지원 여부를 스스로 정하세요] 당신: "
                        f"{me_info or '예비(직군 미정)'}."
                        + (f" 선발되면 '{role_name}' 직군으로 채용됩니다(첫 실작업 시 확정)."
                           if (_is_spare(flow, m) or not me_info) else "")
                        + "\n맡고 싶으면 첫 줄에 [지원], 이어서 지원서(왜 당신인가 — 당신의 경험·"
                          "기준에서 근거)를 4문장 이내로. 맡지 않겠으면 [패스] 한 줄만. "
                          "지원해도 선발은 공고자가 지원서를 보고 정합니다.")
                try:
                    res = await flow.wake(m, body, Kind.INFO)
                except Exception as e:
                    res = f"(응답 실패: {e})"
                try:
                    flow.comm.respond(m, "accept", res)
                except CommError:
                    pass
                if res and Marker.APPLY_RE.search(res):
                    applicants[m] = res
                    await _say_speech(flow, m, "[지원]", res)   # 지원서 = 본인 명의 공개 발화
                    if flow.log:
                        flow.log("recruit_apply", role=role_name, who=m)
                else:
                    if flow.log:
                        flow.log("recruit_pass", role=role_name, who=m)
        finally:
            flow.fork_active -= 1

    if not applicants:
        # 유찰 → genesis 폴백(신규 채용 — 채용 상속). 신입은 지원 절차 없이 합류(신규 생성이므로).
        await _say(flow, me_id, f"[채용] '{role_name}' 공고 유찰(후보 {len(cands)}·지원 0) — 신규 채용으로 전환합니다.")
        _mk = getattr(g, "create_agent", None)
        _new = None
        if _mk and getattr(flow, "user_channel", None):
            try:
                # [채용 상속(사용자 규칙 2026-07-08)] recruiter=공고 봇 — 신입의 모델·effort를 같은 선으로.
                _new = await _mk(flow.user_channel, role_name, recruiter=me_id)
            except Exception:
                _new = None
        if not _new:
            return (f"채용 실패: '{role_name}' 지원자가 없고 신규 생성도 실패했습니다(일시 오류) — "
                    f"잠시 뒤 다시 공고하세요.")
        nid = int(_new)
        if nid not in flow.pool:
            flow.pool.append(nid)
        flow.bot_info[nid] = role_name
        if flow.log:
            flow.log("recruit_genesis", role=role_name, new=nid)
        joined = await _recruit_join(flow, nid, role_name, via="genesis", fresh=True)
        if joined is not None:
            return joined
        await _say(flow, me_id, f"[채용 확정] {flow._info(nid) or nid} — '{role_name}' 신규 채용(genesis)")
        return (f"'{role_name}' 공고 유찰 → 신규 채용: {flow._info(nid) or nid} 합류. "
                f"현재 팀: {flow._names(flow.current.team)}")

    flow.recruit_open = {"role": role_name, "reason": reason,
                         "applicants": dict(applicants)}
    lines = [f"[채용 공고 '{role_name}'] 지원 {len(applicants)}건 — 지원서를 읽고 선발하세요:"]
    for m, app in applicants.items():
        lines.append(f"\n· {flow._info(m) or m}(id {m}):\n{_speech_clip(app, 600)}")
    lines.append(f"\n선발 = recruit(member='<이름|id>', reason='선발 사유'). "
                 f"모두 부적합하면 다시 공고(role=…)하거나 그대로 진행하세요 — "
                 f"지원 안 한 동료의 지명은 불가합니다.")
    return "\n".join(lines)


async def _recruit_join(flow, mid, role_name, via="선발", fresh=False):
    """합류 마무리(공통) — 직군 부여(겸직 게이트·잠정 영속)·팀 합류·스레드 멤버십.
    거부 사유가 있으면 그 문구를 반환(합류 안 함), 성공이면 None."""
    g = flow.guide
    if not fresh:
        # 예비/무직 → 그 직군으로 잠정 채용(일로 직업 획득 — 첫 실작업 시 영속)
        cur = flow._info(mid)
        if _is_spare(flow, mid) or not cur:
            flow.bot_info[mid] = role_name
            flow.tentative_roles[mid] = role_name
        elif not any(_norm_job(j) == _norm_job(role_name) for j in _jobs_of(cur)):
            # 이미 다른 직군 보유 — 1봇 1직업. 겸직은 예외 둘(예비 없음/유사 일)일 때만, 최대 2개.
            cur_jobs = _jobs_of(cur)
            spares_left = [s for s in flow.pool if _is_spare(flow, s)]
            similar = any(_job_tokens(j) & _job_tokens(role_name) for j in cur_jobs)
            if spares_left and not similar:
                return (f"선발 불가: {cur}(id {mid})는 이미 '{cur}' 직군입니다 — **1봇 1직업** 원칙이라 "
                        f"무관한 직군('{role_name}') 겸직은 예비가 없거나 비슷한 일일 때만 허용됩니다"
                        f"(전문화 기억 보호). 예비 지원자를 선발하거나 재공고하세요.")
            if len(cur_jobs) >= 2:
                return (f"선발 불가(겸직 한도): {flow._info(mid) or mid}(id {mid})는 이미 직군 2개('{cur}') "
                        f"보유 — 봇당 겸직은 최대 2개입니다. 다른 지원자를 선발하거나 재공고하세요.")
            new_label = f"{cur}{_JOB_SEP}{role_name}"
            flow.bot_info[mid] = new_label
            if getattr(flow, "persist_role", None):
                try:
                    flow.persist_role(mid, new_label)
                except Exception:
                    pass
    # 직군 라벨 → 매체 역할 동기(잠정 채용은 보류 — 일로 획득 시 SYS가 부여)
    flow.current.status.group = _group_of(flow, flow.current.team)
    fn = getattr(g, "assign_job_role", None)
    if fn and getattr(flow, "guild_id", None) and mid not in flow.tentative_roles:
        try:
            await fn(flow.guild_id, mid, flow.bot_info.get(mid) or role_name)
        except Exception:
            pass
    if mid not in flow.project_team:
        flow.project_team.append(mid)
    if mid not in flow.current.team:
        flow.current.team.append(mid)
        flow.current.status.group = _group_of(flow, flow.current.team)
        await flow.refresh()
        await _add_members(g, flow.current.thread_id, [mid])   # 스레드에 합류(멤버십=팀)
    return None
