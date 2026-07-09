"""[Rule — S3 마무리층] Task 경계 e2e 전수 검증 + 복기(ms_replan) 진입 + 오버헤드 관측.

계약: murmur/docs/PIPELINE_REWORK_2026-07-09.md §6(e2e)·§8(관측)·§11(이벤트)·§12(플래그).

소유(S3): '전수'의 분모(체크리스트) · 결과 수집·판정 · 결함 전달 형식 · ms_replan 진입점 호출 ·
§8 오버헤드 스냅샷. **실행 주체는 QA 봇(판단)** — 이 모듈은 구조만 소유한다(봇을 계산기로 만들지 않는다).

접점:
- ms_replan 재수립 자체는 S1 소유(회의·생성) — 시그니처 확정 전까지 entry 콜러블 주입(mock).
- 이벤트 이름은 §11 그대로(e2e_pass·e2e_fail·ms_replan). 오버헤드는 payload 동승(이름 추가 없음).
- 상태는 flow.wrapup_state에 두고 체크포인트 동승은 flow(_ckpt) 쪽 접점에서 잇는다(§9).
- ORGANT_PIPELINE 플래그 이중수용: 플래그 OFF면 이 모듈의 어떤 경로도 라이브 동작을 바꾸지 않는다.
"""
import os
import re
import time

# §11 이벤트 어휘 — 계약 그대로(변형 금지)
E2E_PASS = "e2e_pass"
E2E_FAIL = "e2e_fail"
MS_REPLAN = "ms_replan"

# '전수' 4축(§6 S3 코멘트): 조건 회귀 · 관통 · 표면 전수 · 원문 대조
KIND_CONDITION = "condition"   # 마일스톤 등록 완수조건의 최종 버전 재실행
KIND_ARC = "arc"               # 실기동 주 사용 경로 관통
KIND_SURFACE = "surface"       # 노출 표면(라우트·API·명령)당 최소 1검사
KIND_ORIGIN = "origin"         # ORIGIN 요구 문장별 실증
_KINDS = (KIND_CONDITION, KIND_ARC, KIND_SURFACE, KIND_ORIGIN)


class WrapupError(Exception):
    """e2e 마무리 규약 위반(빈 분모·미지 항목 결과 등)."""


def pipeline_on() -> bool:
    """§12 플래그 이중수용 — ORGANT_PIPELINE=milestone 일 때만 새 파이프라인 경로."""
    return os.environ.get("ORGANT_PIPELINE", "").strip() == "milestone"


def build_checklist(conditions=None, arcs=None, surfaces=None, origin_items=None) -> list:
    """'전수'의 분모를 만든다 — 4축 입력을 항목 목록으로 정규화.

    각 항목: {"id": "축:번호", "kind": 축, "spec": 검사 내용(원문)}.
    빈 축은 허용(예: CLI 산출물이면 브라우저 arc가 없을 수 있다) — 단 전 축이 비면 분모가 없는
    것이므로 WrapupError(전수 검증이 성립 안 함).
    """
    checklist = []
    for kind, items in ((KIND_CONDITION, conditions), (KIND_ARC, arcs),
                        (KIND_SURFACE, surfaces), (KIND_ORIGIN, origin_items)):
        for i, spec in enumerate(items or [], 1):
            s = str(spec or "").strip()
            if s:
                checklist.append({"id": f"{kind}:{i}", "kind": kind, "spec": s})
    if not checklist:
        raise WrapupError("전수 분모가 비었습니다 — 조건·관통·표면·원문 중 최소 한 축은 있어야 합니다.")
    return checklist


def judge(checklist, results) -> tuple:
    """QA 봇의 항목별 결과를 판정으로 접는다 → (verdict, defects).

    results: {item_id: {"ok": bool, "evidence": str, "observed": str}}.
    - 전 항목이 **증거 있는 pass**여야 e2e_pass. 증거 없는 pass는 pass가 아니다(결함으로 잡는다).
    - 결과가 누락된 항목도 결함(검사 안 됨 — '전수'가 깨졌으므로).
    - checklist 밖 id의 결과는 규약 위반(WrapupError) — 분모 밖 검사로 전수를 참칭하지 못하게.
    """
    known = {it["id"]: it for it in checklist}
    for rid in results or {}:
        if rid not in known:
            raise WrapupError(f"분모에 없는 항목 결과: {rid}")
    defects = []
    for it in checklist:
        r = (results or {}).get(it["id"])
        if r is None:
            defects.append(_defect(it, observed="(검사 안 됨)", evidence=""))
        elif not r.get("ok"):
            defects.append(_defect(it, observed=str(r.get("observed") or "(관측 미기재)"),
                                   evidence=str(r.get("evidence") or "")))
        elif not str(r.get("evidence") or "").strip():
            defects.append(_defect(it, observed="pass 주장에 증거 없음", evidence=""))
    return (E2E_PASS if not defects else E2E_FAIL), defects


def _defect(item, observed: str, evidence: str) -> dict:
    """결함 한 건 — ms_replan 입력의 원자. suspect_ms는 조건 축이면 그 조건의 출처 마일스톤."""
    return {"id": item["id"], "kind": item["kind"], "spec": item["spec"],
            "observed": observed, "evidence": evidence,
            "suspect_ms": item.get("ms") or ""}


def format_defects(defects) -> str:
    """결함 목록 → 재수립 회의 입력 텍스트(S3 소유 형식 — 구조 보존, 사람이 읽는 문장).

    형식: 항목당 3줄(무엇을/관측/증거). 재수립 회의(S1)가 이 원문을 그대로 받는다.
    """
    if not defects:
        return "(결함 없음)"
    lines = [f"[e2e 결함 {len(defects)}건 — 마일스톤 재수립 입력]"]
    for i, d in enumerate(defects, 1):
        lines.append(f"{i}. ({d['kind']}) {d['spec']}")
        lines.append(f"   관측: {d['observed']}")
        if d.get("evidence"):
            lines.append(f"   증거: {d['evidence']}")
        if d.get("suspect_ms"):
            lines.append(f"   의심 마일스톤: {d['suspect_ms']}")
    return "\n".join(lines)


def overhead_snapshot(flow) -> dict:
    """§8 관측 — 소형 Task의 계층 오버헤드. 설계 개입 없이 데이터만 쌓는다(확정: '민감하게 접근').

    flow에 카운터가 없으면 0 (S1/S2가 심는 카운터와 느슨 결합 — 없어도 죽지 않는다).
    """
    started = float(getattr(flow, "task_started_ts", 0) or 0)
    return {
        "meetings": int(getattr(flow, "meet_count", 0) or 0),
        "iters": int(getattr(flow, "iter_count", 0) or 0),
        "wallclock_s": round(time.time() - started, 1) if started else 0,
        "tokens_out": int(getattr(flow, "tokens_out_sum", 0) or 0),
        "cost_usd": round(float(getattr(flow, "cost_usd_sum", 0) or 0), 4),
    }


def emit_verdict(flow, verdict: str, defects, snapshot=None) -> None:
    """판정을 §11 이벤트로 적재(§9 최대 저장) + flow.wrapup_state에 동승 준비.

    payload: 분모 크기·결함 수·결함 목록·오버헤드 스냅샷(§8 — 이벤트 이름 추가 없이 동승).
    """
    if verdict not in (E2E_PASS, E2E_FAIL):
        raise WrapupError(f"미지의 판정: {verdict}")
    snap = snapshot or {}
    state = {"verdict": verdict, "defects": list(defects or []), "overhead": snap,
             "ts": time.time()}
    try:
        flow.wrapup_state = state          # §9 — 체크포인트 동승 접점(flow._ckpt는 S1 쪽에서 잇는다)
    except Exception:
        pass
    log = getattr(flow, "log", None)
    if log:
        log(verdict, defects=len(state["defects"]), overhead=snap)


async def enter_replan(flow, defects, entry=None) -> str:
    """e2e_fail → 마일스톤 재수립 진입(§6 복기). 재수립 자체는 S1의 회의·생성 체계가 수행한다.

    entry 기본값 = S1의 실 진입점 `milestone.ms_replan`(통합주기 1 착지 — ms_replan 이벤트도
    거기서 적재된다). 커스텀 entry(flow, brief, defects)는 자기 이벤트를 스스로 책임진다.
    async·sync 콜러블 모두 수용. 반환 = 재수립 회의 입력 텍스트(brief).
    """
    if not defects:
        raise WrapupError("결함 없이 재수립 진입은 없습니다 — e2e_pass면 Task가 닫힌다.")
    brief = format_defects(defects)
    if entry is None:
        from . import milestone as _ms
        _ms.ms_replan(flow, [_defect_line(d) for d in defects])
    else:
        out = entry(flow, brief, defects)
        if hasattr(out, "__await__"):
            await out
    return brief


def _defect_line(d) -> str:
    """결함 dict → 한 줄(S1 ms_replan의 defects 입력 단위 — 새 마일스톤 조건 초안의 원문이 된다)."""
    return f"({d.get('kind')}) {str(d.get('spec') or '')[:60]} — 관측: {str(d.get('observed') or '')[:60]}"


# ── 아크: 분모 조립 → (QA가 판단·제출) → 판정 → 복기 (§6 — 도구 표면은 guide_tools) ──────

def assemble_base_checklist(flow) -> list:
    """Task 경계 e2e 개시 — 조건·원문 축을 flow에서 자동 조립(§6 ①·④).

    조건 축 = 전 마일스톤의 완수조건 전부(통과 여부 무관 — **최종 버전**에서 재실증해 뒤 작업이
    앞 것을 깼는지 잡는다). 항목에 출처 마일스톤(ms)을 달아 결함의 suspect_ms로 쓴다.
    원문 축 = flow.task_origin(사용자 원 요구)의 문장들. 표면·관통 축은 QA의 e2e_scope 제출로
    확장된다(분모는 구조가 들되, 표면의 발견은 봇의 판단).
    """
    conds, tags = [], []
    for m in (getattr(flow, "milestones", None) or []):
        for c in m.criteria:
            conds.append(f"{c.desc} | 재실증: {c.verify}")
            tags.append(m.ms_id)
    origin = [s.strip() for s in re.split(r"[.\n]", str(getattr(flow, "task_origin", "") or ""))
              if s.strip()]
    cl = build_checklist(conditions=conds, origin_items=origin)
    for it in cl:
        if it["kind"] == KIND_CONDITION:
            it["ms"] = tags[int(it["id"].split(":")[1]) - 1]
    flow.e2e_checklist = cl
    flow.e2e_results = {}
    return cl


def register_scope(flow, surfaces=None, arcs=None) -> list:
    """QA가 발견한 표면·관통 경로를 분모에 추가(§6 ②·③). 반환 = 추가된 항목(id 포함 — 봇에게 회신).
    중복 spec은 조용히 무시(재제출 무해). 개시 전 호출은 규약 위반."""
    cl = getattr(flow, "e2e_checklist", None)
    if cl is None:
        raise WrapupError("e2e가 개시되지 않았습니다 — e2e_open(분모 조립)이 먼저입니다.")
    added = []
    for kind, items in ((KIND_SURFACE, surfaces), (KIND_ARC, arcs)):
        n = sum(1 for it in cl if it["kind"] == kind)
        for spec in (items or []):
            s = str(spec or "").strip()
            if not s or any(it["kind"] == kind and it["spec"] == s for it in cl):
                continue
            n += 1
            it = {"id": f"{kind}:{n}", "kind": kind, "spec": s}
            cl.append(it)
            added.append(it)
    return added


def submit_result(flow, item_id: str, ok, observed: str = "", evidence: str = "") -> str:
    """항목 결과 제출(QA의 판단을 구조로 수집). 반환 = 진행 현황 한 줄(남은 항목 안내)."""
    cl = getattr(flow, "e2e_checklist", None)
    if cl is None:
        raise WrapupError("e2e가 개시되지 않았습니다 — e2e_open이 먼저입니다.")
    iid = str(item_id or "").strip()
    known = {it["id"] for it in cl}
    if iid not in known:
        raise WrapupError(f"분모에 없는 항목: {iid} — 유효 항목: {', '.join(sorted(known))}")
    if getattr(flow, "e2e_results", None) is None:
        flow.e2e_results = {}
    flow.e2e_results[iid] = {"ok": bool(ok), "observed": str(observed or "").strip(),
                             "evidence": str(evidence or "").strip()}
    remain = [it["id"] for it in cl if it["id"] not in flow.e2e_results]
    return (f"{len(flow.e2e_results)}/{len(cl)} 제출됨"
            + (f" — 남은 항목: {', '.join(remain)}" if remain else " — 전 항목 제출 완료, e2e_finish로 판정하세요."))


def render_checklist(flow) -> str:
    """분모 현황을 봇에게 서빙 — 항목 id·내용·제출 상태."""
    cl = getattr(flow, "e2e_checklist", None) or []
    rs = getattr(flow, "e2e_results", None) or {}
    lines = [f"[e2e 분모 {len(cl)}항목 — 각 항목을 실증(run·브라우저)하고 e2e_result로 제출하세요]"]
    for it in cl:
        mark = "제출됨" if it["id"] in rs else "미제출"
        lines.append(f"- {it['id']} ({mark}): {it['spec']}")
    return "\n".join(lines)


def finish_e2e(flow):
    """판정 + 이벤트(§11) + 실패 시 복기(§6 — S1 ms_replan 실 진입점). 반환 (verdict, defects, new_ms)."""
    cl = getattr(flow, "e2e_checklist", None)
    if not cl:
        raise WrapupError("e2e가 개시되지 않았습니다 — e2e_open이 먼저입니다.")
    verdict, defects = judge(cl, getattr(flow, "e2e_results", None) or {})
    emit_verdict(flow, verdict, defects, overhead_snapshot(flow))
    new_ms = None
    if verdict == E2E_FAIL:
        from . import milestone as _ms
        new_ms = _ms.ms_replan(flow, [_defect_line(d) for d in defects])
    return verdict, defects, new_ms


# ── 도구 표면용 래퍼 (guide_tools의 얇은 래퍼가 부른다 — 문자열 반환, 봇 대면 문구) ──────

def _boundary_gap(flow) -> str:
    """Task 경계 판정 — 미완 마일스톤 목록(비면 경계 도달). §6: e2e는 Task 경계에서."""
    ms = getattr(flow, "milestones", None) or []
    if not ms:
        return "마일스톤이 없습니다(마일스톤 파이프라인 Task가 아님)"
    open_ms = [m.ms_id for m in ms if m.status != "done"]
    return f"미완 마일스톤: {', '.join(open_ms)}" if open_ms else ""


def rule_e2e_open(flow) -> str:
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    gap = _boundary_gap(flow)
    if gap:
        return f"e2e 개시 불가 — 아직 Task 경계가 아닙니다({gap}). 모든 마일스톤이 닫힌 뒤 개시하세요."
    try:
        cl = assemble_base_checklist(flow)
    except WrapupError as e:
        return f"e2e 개시 불가: {e}"
    return (f"e2e 개시 — 분모 {len(cl)}항목(조건 회귀·원문 대조). **당신이 할 일**: ① 산출물의 노출 "
            f"표면(페이지·라우트·API·명령)과 주 사용 경로를 파악해 e2e_scope로 제출(분모 확장) "
            f"② 각 항목을 실제 실행(run·브라우저)으로 검사해 e2e_result로 제출(증거 필수) "
            f"③ 전 항목 제출 후 e2e_finish.\n" + render_checklist(flow))


def rule_e2e_scope(flow, args) -> str:
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    try:
        added = register_scope(flow,
                               surfaces=str(args.get("surfaces") or "").splitlines(),
                               arcs=str(args.get("arcs") or "").splitlines())
    except WrapupError as e:
        return str(e)
    if not added:
        return "추가된 항목 없음(빈 입력 또는 전부 중복)."
    return ("분모 확장 — 추가 항목: "
            + " · ".join(f"{it['id']}({it['spec'][:40]})" for it in added)
            + "\n각 항목을 검사해 e2e_result로 제출하세요.")


def rule_e2e_result(flow, args) -> str:
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    ok = str(args.get("ok") or "").strip().lower() in ("true", "pass", "ok", "yes", "1", "충족")
    try:
        return submit_result(flow, str(args.get("item") or ""), ok,
                             observed=str(args.get("observed") or ""),
                             evidence=str(args.get("evidence") or ""))
    except WrapupError as e:
        return str(e)


def rule_e2e_finish(flow) -> str:
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    try:
        verdict, defects, new_ms = finish_e2e(flow)
    except WrapupError as e:
        return str(e)
    if verdict == E2E_PASS:
        return "e2e_pass — 전 항목 증거 있는 충족. Task 마무리 가능."
    head = f"e2e_fail — 결함 {len(defects)}건."
    if new_ms is not None:
        head += (f" 복기 마일스톤 {new_ms.ms_id} 개설됨(결함 해소가 완수조건 초안 — 확정은 회의에서). "
                 f"결함이 해소되면 다시 Task 경계에서 e2e_open 하세요.")
    return head + "\n" + format_defects(defects)
