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
import secrets
import time

from .evidence import (
    command_matches_spec, direct_verifier_command, normalize_verifier_command,
    verifier_command_hash, verifier_spec_hash,
)

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


def _checkpoint(flow) -> None:
    """e2e 개시·분모·결과·판정을 마일스톤과 같은 Task 체크포인트에 즉시 동승."""
    try:
        from .milestone import _ckpt
        _ckpt(flow)
    except Exception:
        pass


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
            "suspect_ms": item.get("ms") or "",
            "verifier_spec": item.get("verifier_spec") or item.get("spec") or "",
            "verifier_command": item.get("verifier_command") or ""}


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


def tallying_logger(flow, log_fn):
    """[§8 관측 — 단일 관문 집계] flow.log 배선 지점(sys_core)에서 감싸, 흐름의 이벤트 수·토큰·
    비용·시작시각을 flow.event_counts에 쌓는다. S2 제안("relay 이벤트는 이미 전부 방출 — 집계로
    충분")의 구현: 이벤트 지점마다 카운터를 심는 대신 관문 하나에서 센다.

    - 정본은 여전히 flow.jsonl(§9 — 상태는 이벤트로 재구축 가능). 이 집계는 e2e_* payload에
      동승할 요약본이다.
    - 원 log_fn의 계약(반환·예외)을 보존하고, 집계 실패는 로깅을 막지 않는다.
    """
    def _tally_log(event, **f):
        try:
            ec = getattr(flow, "event_counts", None)
            if ec is None:
                ec = {}
                flow.event_counts = ec
            ec.setdefault("_first_ts", time.time())
            ec[event] = int(ec.get(event, 0)) + 1
            if f.get("tokens_out"):
                ec["_tokens_out_sum"] = int(ec.get("_tokens_out_sum", 0)) + int(f["tokens_out"] or 0)
            if f.get("cost_usd"):
                ec["_cost_usd_sum"] = float(ec.get("_cost_usd_sum", 0.0)) + float(f["cost_usd"] or 0)
        except Exception:
            pass
        return log_fn(event, **f)
    return _tally_log


def overhead_snapshot(flow) -> dict:
    """§8 관측 — 소형 Task의 계층 오버헤드. 설계 개입 없이 데이터만 쌓는다(확정: '민감하게 접근').

    1순위 = flow.event_counts(tallying_logger 집계): iters=ms_iter_verify 수, 토큰·비용=turn_done
    동승분 합, wall-clock=첫 이벤트부터. meetings=meet_open(S1이 회의 개시 이벤트를 내면 자동
    집계 — 없으면 0). 2순위 = 레거시 flow 카운터(getattr). 둘 다 없으면 0 — 죽지 않는다.
    """
    ec = getattr(flow, "event_counts", None) or {}
    started = float(ec.get("_first_ts") or getattr(flow, "task_started_ts", 0) or 0)
    return {
        "meetings": int(ec.get("meet_open", 0) or getattr(flow, "meet_count", 0) or 0),
        "iters": int(ec.get("ms_iter_verify", 0) or getattr(flow, "iter_count", 0) or 0),
        "wallclock_s": round(time.time() - started, 1) if started else 0,
        "tokens_out": int(ec.get("_tokens_out_sum", 0) or getattr(flow, "tokens_out_sum", 0) or 0),
        "cost_usd": round(float(ec.get("_cost_usd_sum", 0) or getattr(flow, "cost_usd_sum", 0) or 0), 4),
    }


def emit_verdict(flow, verdict: str, defects, snapshot=None) -> None:
    """판정을 §11 이벤트로 적재(§9 최대 저장) + flow.wrapup_state에 동승 준비.

    payload: 분모 크기·결함 수·결함 목록·오버헤드 스냅샷(§8 — 이벤트 이름 추가 없이 동승).
    """
    if verdict not in (E2E_PASS, E2E_FAIL):
        raise WrapupError(f"미지의 판정: {verdict}")
    snap = snapshot or {}
    from .milestone import workspace_artifact_stamp, write_revision
    state = {"verdict": verdict, "defects": list(defects or []), "overhead": snap,
             "artifact_stamp": workspace_artifact_stamp(flow),
             "write_epoch": write_revision(flow), "ts": time.time()}
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
        _ms.ms_replan(flow, defects)
    else:
        out = entry(flow, brief, defects)
        if hasattr(out, "__await__"):
            await out
    return brief


def _defect_line(d) -> str:
    """결함 dict → 한 줄(S1 ms_replan의 defects 입력 단위 — 새 마일스톤 조건 초안의 원문이 된다)."""
    return f"({d.get('kind')}) {str(d.get('spec') or '')[:60]} — 관측: {str(d.get('observed') or '')[:60]}"


# ── 아크: 분모 조립 → (QA가 판단·제출) → 판정 → 복기 (§6 — 도구 표면은 guide_tools) ──────


def _prime_e2e_verifier(
    flow, item, command, fixed=True, structurally_ratified=False,
) -> None:
    """한 e2e item의 exact command를 현재 artifact에 SYS challenge로 봉인한다."""
    from .milestone import workspace_artifact_stamp, write_revision

    target = str(item.get("id") or "").strip()
    spec = str(item.get("verifier_spec") or item.get("spec") or "").strip()
    cmd = normalize_verifier_command(command)
    admissible = (
        bool(direct_verifier_command(cmd, getattr(flow, "workspace", "")))
        if structurally_ratified
        else command_matches_spec(cmd, spec, getattr(flow, "workspace", ""))
    )
    if not admissible:
        raise WrapupError(
            f"{target} verifier가 실제 검사형 명령이 아닙니다 — exact 테스트/빌드/HTTP/브라우저 "
            "명령을 등록하세요.")
    stamp = workspace_artifact_stamp(flow)
    if not stamp:
        raise WrapupError(f"{target} verifier 봉인용 artifact stamp를 만들 수 없습니다.")
    item.update({
        "verifier_spec": spec,
        "verifier_command": cmd,
        "verifier_command_hash": verifier_command_hash(cmd),
        "verifier_spec_hash": verifier_spec_hash(target, spec),
        "verifier_seal": secrets.token_hex(16),
        "verifier_actor": 0,
        "verifier_epoch": write_revision(flow),
        "verifier_stamp": stamp,
        "verifier_used": False,
        "verifier_fixed": bool(fixed),
        "verifier_structurally_ratified": bool(structurally_ratified),
    })


def assemble_base_checklist(flow) -> list:
    """Task 경계 e2e 개시 — 조건·원문 축을 flow에서 자동 조립(§6 ①·④).

    조건 축 = 전 마일스톤의 완수조건 전부(통과 여부 무관 — **최종 버전**에서 재실증해 뒤 작업이
    앞 것을 깼는지 잡는다). 항목에 출처 마일스톤(ms)을 달아 결함의 suspect_ms로 쓴다.
    사용자 원문은 조건/표면/관통 검사를 해석하는 컨텍스트로 보존한다. 자연어 원문 문장마다 서로
    무관한 criterion suite를 붙여 별도 pass로 가장하지 않는다. 표면·관통 축은 QA의 e2e_scope
    제출로 확장된다(분모는 구조가 들되, 표면의 발견은 봇의 판단).
    """
    from .milestone import (
        ratified_goal_verifier_command, workspace_artifact_stamp, write_revision,
    )

    conds, tags, commands, verifier_specs, structural = [], [], [], [], []
    production = hasattr(flow, "_run_receipts")
    workspace = getattr(flow, "workspace", "")
    current_stamp = workspace_artifact_stamp(flow) if production else ""
    current_epoch = write_revision(flow)
    candidates = {}

    def _offer(c, source, order):
        key = (str(c.desc or "").strip(), str(c.verify or "").strip())
        if not all(key):
            return
        verified = normalize_verifier_command(getattr(c, "verified_command", ""))
        trusted = bool(
            getattr(c, "passed", False)
            and getattr(c, "evidence_source", "") == "sys_run"
            and verified
            and getattr(c, "verified_command_hash", "") == verifier_command_hash(verified)
            and getattr(c, "verified_spec_hash", "") == verifier_spec_hash(c.desc, c.verify)
            and int(getattr(c, "verified_write_epoch", -2)) == current_epoch
            and bool(current_stamp)
            and getattr(c, "verified_artifact_stamp", "") == current_stamp
        )
        direct = direct_verifier_command(c.verify, workspace)
        natural_lock = bool(
            getattr(c, "release_lock", False)
            and not direct_verifier_command(
                c.verify, workspace, require_existing=False)
        )
        ratified = (
            ratified_goal_verifier_command(c, workspace, require_existing=True)
            if natural_lock else ""
        )
        is_structural = bool(
            trusted and ratified == verified
        )
        command = (
            verified if is_structural else ""
        ) if natural_lock else (verified if trusted else direct)
        score = (2 if trusted else 1 if command else 0, order)
        old = candidates.get(key)
        if old is None or score >= old["score"]:
            candidates[key] = {
                "criterion": c, "source": source, "command": command,
                "structural": is_structural, "score": score,
            }

    order = 0
    for m in (getattr(flow, "milestones", None) or []):
        if getattr(m, "status", "") == "superseded":
            continue
        for c in m.criteria:
            if getattr(c, "status", "active") == "waived":
                continue
            order += 1
            _offer(c, m.ms_id, order)
    # 구 체크포인트도 GOAL 잠금 조건을 분모에서 잃지 않는다. 정상 경로에선 최종 active 조건과
    # 중복되므로 신뢰 가능한 current receipt/command가 우선하고 동률이면 최신 사본이 이긴다.
    try:
        from .milestone import _goal_locked_refs
        for c in _goal_locked_refs(flow):
            order += 1
            _offer(c, "GOAL", order)
    except Exception:
        pass
    for (desc, verify), row in sorted(
            candidates.items(), key=lambda pair: pair[1]["score"][1]):
        conds.append(f"{desc} | 재실증: {verify}")
        tags.append(row["source"])
        commands.append(row["command"])
        verifier_specs.append(verify)
        structural.append(bool(row["structural"]))

    origin_text = str(getattr(flow, "origin_request", "") or getattr(flow, "task_origin", "") or "")
    origin = [s.strip() for s in re.split(r"[.\n]", origin_text) if s.strip()]
    flow.e2e_origin_context = origin
    cl = build_checklist(conditions=conds)
    for it in cl:
        if it["kind"] == KIND_CONDITION:
            index = int(it["id"].split(":")[1]) - 1
            it["ms"] = tags[index]
            it["verifier_spec"] = verifier_specs[index]
            if production and not commands[index]:
                raise WrapupError(
                    f"{it['id']} 조건에 회의 비준 또는 이전 SYS 실증으로 고정된 exact verifier가 "
                    "없습니다. e2e 시점 QA의 임의 명령 제안으로 대체할 수 없습니다.")
            if production:
                _prime_e2e_verifier(
                    flow, it, commands[index], fixed=True,
                    structurally_ratified=structural[index],
                )
    flow.e2e_checklist = cl
    flow.e2e_results = {}
    # 경계 이전 run receipt는 e2e 증거로 재사용할 수 없다. nonce 이후 실제 run만 항목 pass에 결부된다.
    if hasattr(flow, "_run_receipts"):          # production Flow; 느슨결합 pure rule fixture는 judge만 시험
        flow._e2e_receipt_nonce = secrets.token_hex(16)
        flow._run_receipts = {}
    else:
        flow._e2e_receipt_nonce = None
    _checkpoint(flow)
    return cl


def register_scope(flow, surfaces=None, arcs=None, item_verifiers=None) -> list:
    """QA가 발견한 표면·관통 경로를 분모에 추가(§6 ②·③). 반환 = 추가된 항목(id 포함 — 봇에게 회신).
    기존 condition verifier는 회의/실증 시점에 이미 고정돼야 하며 e2e 시점 재결속은 허용하지
    않는다. 중복 spec은 조용히 무시. 개시 전 호출은 위반."""
    cl = getattr(flow, "e2e_checklist", None)
    if cl is None:
        raise WrapupError("e2e가 개시되지 않았습니다 — e2e_open(분모 조립)이 먼저입니다.")
    production = hasattr(flow, "_run_receipts")

    def _entry(raw):
        if isinstance(raw, dict):
            return (str(raw.get("spec") or "").strip(),
                    normalize_verifier_command(raw.get("verifier_command")))
        text = str(raw or "").strip()
        if "||" in text:
            spec, command = text.rsplit("||", 1)
            return spec.strip(), normalize_verifier_command(command)
        return text, ""

    added = []
    if any(str(raw or "").strip() for raw in (item_verifiers or [])):
        raise WrapupError(
            "condition/origin verifier의 e2e 시점 재결속은 허용되지 않습니다 — 완수조건을 "
            "비준한 마일스톤 회의에서 exact command를 고정하세요.")

    for kind, items in ((KIND_SURFACE, surfaces), (KIND_ARC, arcs)):
        n = sum(1 for it in cl if it["kind"] == kind)
        for raw in (items or []):
            s, command = _entry(raw)
            if not s or any(it["kind"] == kind and it["spec"] == s for it in cl):
                continue
            if production and not command:
                raise WrapupError(
                    f"{kind} scope '{s[:60]}'에 verifier command가 없습니다 — "
                    "'검사 설명 || exact 테스트/HTTP/브라우저 명령' 형식으로 등록하세요.")
            if production and not command_matches_spec(
                    command, s, getattr(flow, "workspace", "")):
                raise WrapupError(
                    f"{kind} scope '{s[:60]}'의 verifier가 무검사/inline/작업공간 밖 명령입니다.")
            n += 1
            it = {"id": f"{kind}:{n}", "kind": kind, "spec": s,
                  "verifier_spec": s}
            if production:
                _prime_e2e_verifier(flow, it, command, fixed=True)
            cl.append(it)
            added.append(it)
    if added:
        _checkpoint(flow)
    return added


def submit_result(flow, item_id: str, ok, observed: str = "", evidence: str = "",
                  receipt_meta=None) -> str:
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
    row = {"ok": bool(ok), "observed": str(observed or "").strip(),
           "evidence": str(evidence or "").strip()}
    if receipt_meta:
        row.update(receipt_meta)
    flow.e2e_results[iid] = row
    _checkpoint(flow)
    remain = [it["id"] for it in cl if it["id"] not in flow.e2e_results]
    return (f"{len(flow.e2e_results)}/{len(cl)} 제출됨"
            + (f" — 남은 항목: {', '.join(remain)}" if remain else " — 전 항목 제출 완료, e2e_finish로 판정하세요."))


def render_checklist(flow) -> str:
    """분모 현황을 봇에게 서빙 — 항목 id·내용·제출 상태."""
    cl = getattr(flow, "e2e_checklist", None) or []
    rs = getattr(flow, "e2e_results", None) or {}
    lines = [f"[e2e 분모 {len(cl)}항목 — 각 항목을 실증(run·브라우저)하고 e2e_result로 제출하세요]"]
    origin = getattr(flow, "e2e_origin_context", None) or []
    if origin:
        lines.append("[사용자 원문 컨텍스트 — 별도 pass로 가장하지 않고 비준 조건·scope 해석에 대조]")
    for it in cl:
        mark = "제출됨" if it["id"] in rs else "미제출"
        command = str(it.get("verifier_command") or "").strip()
        lines.append(f"- {it['id']} ({mark}): {it['spec']}"
                     + (f"\n  exact verifier: `{command}`" if command else
                        "\n  verifier 미결속(판정 불가): 마일스톤 회의에서 exact command 재비준 필요"))
    return "\n".join(lines)


def finish_e2e(flow):
    """판정 + 이벤트(§11) + 실패 시 복기(§6 — S1 ms_replan 실 진입점). 반환 (verdict, defects, new_ms)."""
    from .milestone import goal_locked_release_error
    lock_error = goal_locked_release_error(flow)
    if lock_error:
        raise WrapupError(f"e2e 판정 불가 — {lock_error}")
    nonce = str(getattr(flow, "_e2e_receipt_nonce", "") or "")
    if nonce:
        from .milestone import workspace_artifact_stamp, write_revision
        stamp, epoch = workspace_artifact_stamp(flow), write_revision(flow)
        items = {
            str(item.get("id") or ""): item
            for item in (getattr(flow, "e2e_checklist", None) or [])
            if isinstance(item, dict)
        }
        stale = [
            iid for iid, row in (getattr(flow, "e2e_results", None) or {}).items()
            if bool(row.get("ok")) and (
                iid not in items
                or
                row.get("evidence_source") != "sys_run"
                or not row.get("receipt_id")
                or row.get("artifact_stamp") != stamp
                or int(row.get("write_epoch", -1)) != epoch
                or not items[iid].get("verifier_used")
                or row.get("verifier_seal") != items[iid].get("verifier_seal")
                or row.get("command_hash") != items[iid].get("verifier_command_hash")
                or row.get("spec_hash") != items[iid].get("verifier_spec_hash")
                or row.get("verified_command") != items[iid].get("verifier_command")
                or row.get("command_hash")
                != verifier_command_hash(row.get("verified_command"))
                or row.get("spec_hash")
                != verifier_spec_hash(
                    iid, items[iid].get("verifier_spec") or items[iid].get("spec"))
            )
        ]
        if stale:
            raise WrapupError(
                "e2e 판정 불가 — SYS run receipt가 없거나 뒤 실행으로 stale된 pass 항목: "
                + ", ".join(stale[:8]) + ". 현재 산출물 버전에서 해당 항목을 다시 run하세요.")
    cl = getattr(flow, "e2e_checklist", None)
    if not cl:
        raise WrapupError("e2e가 개시되지 않았습니다 — e2e_open이 먼저입니다.")
    verdict, defects = judge(cl, getattr(flow, "e2e_results", None) or {})
    emit_verdict(flow, verdict, defects, overhead_snapshot(flow))
    new_ms = None
    if verdict == E2E_FAIL:
        from . import milestone as _ms
        new_ms = _ms.ms_replan(flow, defects)
    _checkpoint(flow)
    return verdict, defects, new_ms


# ── 도구 표면용 래퍼 (guide_tools의 얇은 래퍼가 부른다 — 문자열 반환, 봇 대면 문구) ──────

def _boundary_gap(flow) -> str:
    """Task 경계 판정 — 미완 마일스톤 목록(비면 경계 도달). §6: e2e는 Task 경계에서."""
    from .milestone import (
        goal_locked_release_error, promote_final_locked_criteria,
        work_ledger_release_error,
    )
    promote_final_locked_criteria(flow)
    ms = getattr(flow, "milestones", None) or []
    if not ms:
        return "마일스톤이 없습니다(마일스톤 파이프라인 Task가 아님)"
    open_ms = [m.ms_id for m in ms if m.status != "done"]
    if open_ms:
        return f"미완 마일스톤: {', '.join(open_ms)}"
    ledger_error = work_ledger_release_error(flow, repair=True)
    if ledger_error:
        return ledger_error
    lock_error = goal_locked_release_error(flow)
    return lock_error or ""


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
            f"표면(페이지·라우트·API·명령)과 주 사용 경로를 파악해 e2e_scope에 "
            f"'검사 설명 || exact verifier command'로 제출 "
            f"② 각 항목 아래 SYS가 봉인한 exact verifier를 run하되 evidence_for에 그 항목 id를 "
            f"정확히 넣고, 발급된 receipt로 e2e_result를 제출(다른 명령은 실행·영수증 모두 거부) "
            f"③ 전 항목 제출 후 e2e_finish.\n" + render_checklist(flow))


def rule_e2e_scope(flow, args) -> str:
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    try:
        added = register_scope(flow,
                               surfaces=str(args.get("surfaces") or "").splitlines(),
                               arcs=str(args.get("arcs") or "").splitlines(),
                               item_verifiers=str(
                                   args.get("item_verifiers") or "").splitlines())
    except WrapupError as e:
        return f"scope 등록 거부: {e}"
    if not added:
        return "추가된 항목 없음(빈 입력 또는 전부 중복)."
    return ("분모 확장/검증기 결속 — 항목: "
            + " · ".join(f"{it['id']}({it['spec'][:40]})" for it in added)
            + "\n각 항목을 검사해 e2e_result로 제출하세요.")


def _consume_e2e_receipt(flow, actor, item_id, receipt_id):
    """e2e_open 이후 실제 rc=0 run row를 한 항목의 pass 증거로 단일사용한다."""
    from .milestone import workspace_artifact_stamp, write_revision
    rid = str(receipt_id or "").strip()
    nonce = str(getattr(flow, "_e2e_receipt_nonce", "") or "")
    ledger = getattr(flow, "_run_receipts", None) or {}
    row = ledger.get(rid)
    item = next(
        (candidate for candidate in (getattr(flow, "e2e_checklist", None) or [])
         if isinstance(candidate, dict)
         and str(candidate.get("id") or "").strip() == str(item_id or "").strip()),
        None,
    )
    if (not rid or not nonce or not row or item is None
            or str(row.get("e2e_nonce") or "") != nonce
            or str(row.get("evidence_for") or "") != str(item_id or "").strip()
            or not item.get("verifier_used")
            or str(row.get("verifier_seal") or "") != str(item.get("verifier_seal") or "")
            or str(row.get("command_hash") or "")
            != str(item.get("verifier_command_hash") or "")
            or str(row.get("spec_hash") or "") != str(item.get("verifier_spec_hash") or "")
            or str(row.get("command_hash") or "")
            != verifier_command_hash(row.get("command"))
            or str(row.get("spec_hash") or "")
            != verifier_spec_hash(
                item_id, item.get("verifier_spec") or item.get("spec"))
            or normalize_verifier_command(row.get("command"))
            != normalize_verifier_command(item.get("verifier_command"))
            or (actor is not None and int(row.get("actor") or 0) != int(actor))
            or int(row.get("rc") if row.get("rc") is not None else -1) != 0
            or int(row.get("write_epoch", -2)) != write_revision(flow)
            or not row.get("artifact_stamp")
            or row.get("artifact_stamp") != workspace_artifact_stamp(flow)):
        return None
    stderr = str(row.get("stderr") or "").strip()
    stdout = str(row.get("stdout") or "").strip()
    evidence = f"SYS-RUN {rid} exit=0 `{str(row.get('command') or '')[:100]}`"
    if stdout:
        evidence += "\n" + stdout[-220:]
    if stderr:
        evidence += "\n[stderr] " + stderr[-120:]
    flow._run_receipts.pop(rid, None)
    return evidence, {
        "evidence_source": "sys_run",
        "receipt_id": rid,
        "write_epoch": int(row["write_epoch"]),
        "artifact_stamp": str(row["artifact_stamp"]),
        "verifier_seal": str(row["verifier_seal"]),
        "verified_command": str(row["command"]),
        "command_hash": str(row["command_hash"]),
        "spec_hash": str(row["spec_hash"]),
    }


def rule_e2e_result(flow, args, me_id=None) -> str:
    if not pipeline_on():
        return "이 도구는 마일스톤 파이프라인(ORGANT_PIPELINE=milestone)에서만 동작합니다."
    ok = str(args.get("ok") or "").strip().lower() in ("true", "pass", "ok", "yes", "1", "충족")
    try:
        evidence = str(args.get("evidence") or "")
        receipt_meta = None
        # guide 도구 경계는 me_id를 넘긴다. 순수 rule 단위테스트의 me_id=None 경로만 judge 계약을
        # 독립 시험하도록 두고, 실제 봇 pass는 반드시 e2e_open 이후 SYS receipt를 소비한다.
        if ok and me_id is not None:
            bound = _consume_e2e_receipt(
                flow, me_id, args.get("item"), args.get("receipt"))
            if bound is None:
                return ("e2e pass 접수 거부 — e2e_open 이후 현재 산출물에서 당신이 실행한 rc=0 "
                        "SYS run receipt id가 필요합니다(이전/재사용/임의 evidence 불가).")
            evidence, receipt_meta = bound
        return submit_result(flow, str(args.get("item") or ""), ok,
                             observed=str(args.get("observed") or ""),
                             evidence=evidence, receipt_meta=receipt_meta)
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
    else:
        # [복기 진전 게이트] 결함이 반복 라운드에도 줄지 않아 새 복기를 열지 않음 — 같은 접근의
        # 재시도는 낭비다. 정직하게 마무리하고 사람 판단을 기다린다(재개·요청 구체화가 출구).
        head += (" 복기 정체 — 결함이 반복 라운드에도 줄지 않아 새 복기 주기를 열지 않습니다. "
                 "같은 접근을 반복하지 말고, 결함 목록과 원인 가설을 [완료 보고]에 정직하게 남기고 "
                 "마무리하세요(사람 확인 대기).")
    return head + "\n" + format_defects(defects)
