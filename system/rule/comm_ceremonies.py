"""[M9 분할 — communication.py에서 추출] 협업 함수: vote·parallel_work·recruit.
파사드 보존 — communication.py가 이 모듈을 재수출해 기존 import 경로 유지.
meet은 request 결합이라 communication.py에 남김."""
import asyncio
import json
import os
import re
from ..protocol import Kind
from .comm_helpers import (
    _JOB_SEP, _SPARE_LABEL, _add_members, _clarify_hold, _find_variant_job,
    _fork_collect, _group_of, _is_spare, _job_tokens, _jobs_of, _norm_job,
    _resolve_members, _say,
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
    """[Communication Rule 로직] recruit — guide_tools에서 이관(평문 반환, @tool이 _ok 래핑)."""
    from .._util import _speech_clip, _react, _dbg
    from .task import _ckpt
    g = flow.guide
    role_name = (args.get("role") or "").strip()
    spec = (args.get("member") or "").strip()
    # [전문화 정책 — 범용 직군 금지(사용자 결정)] 범용(풀스택 등)은 모든 일을 흡수해 전문 채용을
    # 억제하고(라이브: AI·서버·데이터가 한 봇에 22건 집중) 병렬의 병목이 된다. 전문 직군으로 나눠 뽑는다.
    if role_name and any(g in _norm_job(role_name)
                         for g in ("풀스택", "풀 스택", "fullstack", "full stack", "full-stack",
                                   "제너럴", "generalist", "만능", "올라운드")):
        return (f"채용 거부(전문화 정책): '{role_name}' 같은 범용 직군은 두지 않습니다 — 범용은 모든 "
                   f"일을 흡수해 전문 채용을 막고 병렬의 병목이 됩니다(1봇 1직업 전문화가 회사 원칙). "
                   f"필요한 전문 직군으로 나눠 뽑으세요(예: 백엔드 / 프론트엔드 / AI 엔지니어 / 데이터 엔지니어).")
    # [직군 중복 생성 게이트 — 근본] recruit가 자유 텍스트 직군명을 받다 보니 흐름마다 변형 이름
    # ('VFX 전문가' 있는데 'VFX 아티스트')으로 '같은 도메인 직군'이 새 Discord 역할로 계속 불어났다.
    # 비교 풀은 현재 팀 라벨 + '서버의 커스텀 역할 전체'(직군 역할은 서버 영속이라, 토큰 유실/오프라인
    # 봇의 직군도 보인다). 변형이 감지되면 생성하지 않고 멈춰 세운다 — 재사용(기존 이름 그대로)이나
    # 명시적 신설(new_role='yes')은 에이전트가 정한다(시스템이 정답 이름을 정하는 하드코딩 아님).
    if role_name:
        existing_jobs = {j for v in flow.bot_info.values()
                         if v and not str(v).startswith(_SPARE_LABEL)
                         for j in _jobs_of(v)}   # 겸직 라벨은 구성 직군으로 풀어 비교
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
                       f"role='{dup}' 그대로 다시 호출해 기존 직군으로 채용하세요. 정말 '{dup}'과(와) 다른 "
                       f"일을 하는 새 직군이 필요하면 new_role='yes'를 함께 줘 명시적으로 신설하세요.")
    if flow.current is None:
        # [예비 담당자 '자기 직군 우선'] Task 열기 전에 담당자가 자기 직군부터 정하는 건 허용한다 — 자기
        # 자신 + role 지정일 때만. 이래야 '예비'인 채로 create_project/create_task를 열어 화면(상태블록·동료
        # 프롬프트)에 '예비'로 박히는 걸 막는다(사용자가 본 '담당자가 예비로 들어옴'의 직접 원인). 다른 사람
        # 채용 등은 종전대로 Task가 먼저 있어야 한다.
        self_pick = _resolve_members(spec, flow, flow.pool) if spec else []
        if role_name and ((not spec) or (self_pick and self_pick[0] == me_id)):
            # 1봇 1직업: 이 분기는 '예비(무직)' 담당자용이다 — 이미 직군이 있는 봇이 자기 직군을
            # 덮어쓰면(디자이너→게임 기획자) 전문화 기억이 영속 오염된다(라이브 관측). 같은 직군
            # 재확인만 통과시키고, 다른 직군은 거부한다(필요하면 예비를 그 직군으로 뽑는 것).
            cur = (flow._info(me_id) or "").strip()
            new_label = role_name
            if cur and not _is_spare(flow, me_id):
                cur_jobs = _jobs_of(cur)
                if any(_norm_job(j) == _norm_job(role_name) for j in cur_jobs):
                    return (f"이미 '{role_name}' 직군을 보유하고 있습니다 — 그대로 진행하세요(변경 없음).")
                # 겸직 예외(사용자 정책): ① 풀에 예비가 한 명도 없거나 ② 새 직군이 기존 직군과
                # '비슷한 일'(도메인 토큰 공유)일 때만, **기존 직군을 유지한 채** 새 직군을 더한다
                # (교체 아님 — 전문화 기억 보존). 봇당 최대 2개(직군 스택 누적 재발 방지). 그 외에는
                # 1봇 1직업 원칙 — 예비를 그 직군으로 새로 뽑는 게 정도.
                spares_left = [s for s in flow.pool if _is_spare(flow, s)]
                similar = any(_job_tokens(j) & _job_tokens(role_name) for j in cur_jobs)
                if spares_left and not similar:
                    return (f"자기 직군 추가 거부: 당신은 이미 '{cur}' 직군입니다 — **1봇 1직업** 원칙이라 "
                               f"무관한 직군('{role_name}') 겸직은 예비가 없거나 비슷한 일일 때만 허용됩니다"
                               f"(전문화 보호). '{role_name}'이 필요하면 Task를 연 뒤 recruit(role='{role_name}')로 "
                               f"'예비'를 그 직군으로 채용하세요(예비 {len(spares_left)}명).")
                if len(cur_jobs) >= 2:
                    return (f"겸직 한도 초과: 당신은 이미 직군 2개('{cur}')를 보유하고 있습니다 — 봇당 "
                               f"겸직은 최대 2개입니다. '{role_name}'은 예비나 다른 동료에게 맡기세요.")
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
    # 충원 루프 하드 차단: 최근 요청이 연속 2회+ 실패(시스템 일시불안정)면 채용을 막는다 — 지금 새로
    # 뽑아도 같은 불안정으로 똑같이 실패한다('백엔드 6명' 사태의 구조적 차단; 안내가 아니라 거부).
    # 기존 동료에게 다시 요청해 한 명이라도 응답이 오면 consec_fail이 리셋돼 다시 채용 가능.
    if getattr(flow, "consec_fail", 0) >= 2:
        return (f"채용 보류: 최근 요청이 연속 {flow.consec_fail}회 무응답/실패 — 시스템 일시 불안정입니다. "
                   f"지금 새로 뽑아도 같이 실패하니 채용을 막습니다(무한 충원 루프 방지). 기존 동료에게 잠시 뒤 "
                   f"다시 요청해 한 명이라도 응답이 오면 그때 충원하거나, 계속 안 되면 사용자에게 보고하고 멈추세요.")
    cand = _resolve_members(spec, flow, flow.pool) if spec else []
    if not cand:
        # member 미지정(또는 못 찾음): 직군 채용이면 '예비' 인력에서 자동 선발(아직 프로젝트팀에 없는 예비)
        spares = [m for m in flow.pool if _is_spare(flow, m) and m not in flow.project_team]
        if role_name and spares:
            cand = [spares[0]]
        else:
            return (f"채용할 인력을 못 찾음 — member로 기존 동료(id/역할)를 지정하거나, role로 새 직군을 "
                       f"적어 '예비'를 채용하세요. 남은 예비: {len(spares)}명 / 현재 풀: {flow._names(flow.pool)}")
    mid = cand[0]
    # 예비(직군 미배정)는 'role=직군'을 줘야만 채용된다 — 말로만 배정 차단(직군은 구조적으로 부여).
    if _is_spare(flow, mid) and not role_name:
        return (f"채용 거부: {flow._info(mid) or mid}는 '예비'(직군 미배정)입니다 — role='직군명'을 함께 "
                   f"지정해 어떤 직군으로 채용할지 정하세요(예: recruit(member='{mid}', role='게임 기획자')). "
                   f"직군 없이는 합류·위임 불가(말로만 배정 금지 — 직군이 실제로 부여돼야 일을 맡길 수 있음).")
    # [같은 직군 채용도 자유] role 중복/실패상태로 채용을 거부하지 않는다 — 반복 채용('백엔드 6명')의 진짜
    # 원인은 '동료 무응답(서브프로세스 행)'이었고 그건 워커 턴 타임아웃으로 끊었다(8분 내 인프라실패 처리).
    # 따라서 필요하면 같은 직군을 더 뽑아도 된다. '무응답=인프라'라는 판단·안내는 요청 실패 메시지로만 한다.
    hired = ""
    if role_name:
        cur = flow._info(mid)
        if _is_spare(flow, mid) or not cur:
            flow.bot_info[mid] = role_name                    # 예비/무직 → 그 직군으로 (런타임만, 이 흐름)
            hired = f" — '{role_name}' 직군으로 채용(잠정 — 첫 실작업 시 영속)"
            # [일로 직업 획득 — 영속 이연] 예비를 직군으로 뽑아도 *지금은 영속하지 않는다*(jobs.json·Discord
            # 보류). 그 봇이 *첫 실작업(Write/Edit/run)*을 하는 순간에만 영속한다(권한 훅이 승격) — '직업=기억'을
            # 문자 그대로. 끝까지 일 안 하면 영속 안 돼 다음 흐름에 예비로 사라진다(0-기억 직군 양산의 근본 차단).
            # 충돌(같은 봇 이중채용)도 무해 — 둘 다 일 안 하면 둘 다 예비로 남는다.
            flow.tentative_roles[mid] = role_name
        elif not any(_norm_job(j) == _norm_job(role_name) for j in _jobs_of(cur)):
            # 이미 다른 직군 보유 — 원칙은 **1봇 1직업**(새 직군은 예비를 뽑는 게 정도). 겸직은 사용자
            # 정책의 예외 둘 중 하나일 때만: ① 풀에 예비가 한 명도 없음(어쩔 수 없음) ② 새 직군이
            # 기존 직군과 '비슷한 일'(도메인 토큰 공유). 허용 시 교체가 아니라 **추가**다 — 기존 전문화
            # 기억(주직군)을 유지한 채 부직군을 더하고, 봇당 최대 2개(직군 5~6개 스택 재발 방지).
            cur_jobs = _jobs_of(cur)
            spares_left = [s for s in flow.pool if _is_spare(flow, s)]
            similar = any(_job_tokens(j) & _job_tokens(role_name) for j in cur_jobs)
            if spares_left and not similar:
                return (f"채용 거부: {cur}(id {mid})는 이미 '{cur}' 직군입니다 — **1봇 1직업** 원칙이라 "
                           f"무관한 직군('{role_name}') 겸직은 예비가 없거나 비슷한 일일 때만 허용됩니다"
                           f"(전문화 기억 보호). '{role_name}'이 필요하면 recruit(role='{role_name}')로 "
                           f"'예비'를 그 직군으로 새로 뽑으세요(예비 {len(spares_left)}명).")
            if len(cur_jobs) >= 2:
                return (f"겸직 한도 초과: {flow._info(mid) or mid}(id {mid})는 이미 직군 2개('{cur}')를 "
                           f"보유 — 봇당 겸직은 최대 2개입니다. '{role_name}'은 예비나 다른 동료에게 맡기세요.")
            new_label = f"{cur}{_JOB_SEP}{role_name}"
            flow.bot_info[mid] = new_label
            hired = f" — '{role_name}' 겸직 추가(보유: {new_label})"
            if getattr(flow, "persist_role", None):
                try:
                    flow.persist_role(mid, new_label)
                except Exception:
                    pass
        # 이미 그 직군을 보유하고 있으면 라벨 변경 없이 그대로 합류.
        flow.current.status.group = _group_of(flow, flow.current.team)
        # 이름은 그대로 두고 '직군 라벨 전체'를 Discord 역할(권한)로 동기화 — best-effort. 단 *잠정 채용*
        # (예비→직군, 첫 실작업 전)은 보류한다 — 일로 획득하는 순간 SYS가 부여(영속 이연, 양산 차단).
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
    return (f"{flow._info(mid) or mid} 합류{hired}(사유: {args.get('reason', '')}). "
               f"현재 팀: {flow._names(flow.current.team)}")
