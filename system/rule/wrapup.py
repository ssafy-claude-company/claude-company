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

    entry: S1이 확정할 진입점 콜러블(시그니처 미확정 — mock 주입 지점). 관례상
    entry(flow, brief, defects)를 기대하고, 없으면 이벤트만 적재하고 brief를 돌려준다
    (호출부가 회의 입력으로 쓴다). async·sync 콜러블 모두 수용.
    """
    if not defects:
        raise WrapupError("결함 없이 재수립 진입은 없습니다 — e2e_pass면 Task가 닫힌다.")
    brief = format_defects(defects)
    log = getattr(flow, "log", None)
    if log:
        log(MS_REPLAN, defects=len(defects))
    if entry is not None:
        out = entry(flow, brief, defects)
        if hasattr(out, "__await__"):
            await out
    return brief
