"""[관측 — 실황 미러] Organt·Project 단위 '지금' 상태를 파일(entity_status.json)로 미러.

ms_status.json 패턴의 일반화 — 설계: murmur/docs/MONITORING_REDESIGN_2026-07-10.md §5.
쓰는 쪽 = Flow의 log 봉투(flow.py): 이벤트가 흐를 때마다(1초 스로틀, 상태변화 이벤트는 즉시)
그 시점의 흐름 상태(베턴 보유자·위임 콜스택·태스크)를 스냅샷한다. 러너 메모리가 진실원 —
로그 재구성 없음. 읽는 쪽 = murmur /api/monitor/{agent,project}(읽기 전용).
실패는 흐름에 무해(전부 삼킴). ORGANT_PJT 미설정(순수 단위 테스트)이면 무동작.
"""
import json
import os
import time

_MIN_INTERVAL = 1.0     # 일반 이벤트 미러 스로틀(초) — LLM 페이스라 실질 부하 없음
_last_write = 0.0

# 이 이벤트들은 스로틀 무시하고 즉시 미러(상태 전이의 경계)
FORCE_EVENTS = {"flow_done", "flow_user_stopped", "req_sent", "req_rejected",
                "turn_done", "runner_boot"}


def _path():
    pjt = os.environ.get("ORGANT_PJT")
    if not pjt:
        return None
    d = os.path.join(pjt, "ops", "var", "organt_sns_state")
    return os.path.join(d, "entity_status.json") if os.path.isdir(d) else None


def snapshot(flow):
    """flow의 지금 상태 → {"pid", "project", "organts"} (mirror가 파일에 병합 저장).

    organt 상태 규칙(단일 활성 구조에서 도출):
    - comm.alive인 봇 = working (베턴 보유)
    - 콜스택의 요청자(frm) = waiting (누구를 기다리는지 = 그 프레임의 to)
    - 그 외는 이 미러에 없음 = idle (소비측이 로스터와 대조해 판단)
    """
    comm = getattr(flow, "comm", None)
    ch = getattr(flow, "user_channel", None)
    if comm is None or ch is None:
        return None
    try:
        ch = int(ch)
    except (TypeError, ValueError):
        return None
    stack = []
    for fr in list(getattr(comm, "open_requests", []) or []):
        try:
            k = getattr(fr, "kind", "work")
            k = str(getattr(k, "value", None) or k).replace("Kind.", "").lower()
            stack.append({"frm": int(fr.from_id), "to": int(fr.to_id), "kind": k,
                          "since": float(getattr(fr, "ts", 0.0) or 0.0)})
        except (TypeError, ValueError):
            continue
    cur = getattr(flow, "current", None)
    task_id = str(getattr(cur, "task_id", "") or "") if cur is not None else ""
    alive = getattr(comm, "alive", None)
    done = bool(getattr(comm, "done", False) or getattr(flow, "done", False))
    origin = getattr(comm, "origin", None)

    def _is_bot(b):
        return isinstance(b, int) and b != origin

    # [💭 실황 생각] flow.activity_log 꼬리 — 발화자 라벨이 텍스트에 박혀 있다(진행 가시성).
    # mono_ts(monotonic)를 같은 프로세스 안에서 epoch로 근사 변환해 싣는다.
    acts = []
    try:
        mono_now = time.monotonic()
        for row in list(getattr(flow, "activity_log", []) or [])[-5:]:
            t, m = row[0], row[1]
            wall = row[2] if len(row) > 2 else None      # 복원된 옛 행은 근사 변환으로 폴백
            acts.append({"t": str(t)[:140],
                         "ts": round(float(wall) if wall
                                     else time.time() - max(0.0, mono_now - float(m)), 1)})
    except Exception:
        acts = []
    # [📍 회의 국면 핀(2026-07-17, 사용자: '회의가 하나로 묶여 구분 안 감 — 가시성')] 현재 단계 회의
    # 국면을 실황 첫 줄에 상주 — 롤링 로그에 쓸려가지 않는 상태 표면(프론트 무변경).
    _pn = getattr(flow, "_meet_stage_note", None)
    if _pn:
        acts.insert(0, {"t": f"📍 {str(_pn)[:120]}", "ts": time.time()})
    project = {"active": int(alive) if _is_bot(alive) else None,
               "stack": stack, "task": task_id, "done": done,
               "activity": acts, "updated": time.time()}
    organts = {}
    if not done:
        if _is_bot(alive):
            organts[str(alive)] = {
                "state": "working", "pid": ch, "task": task_id, "waiting_on": None,
                "activity": acts,
                "since": (stack[-1]["since"] if stack else None)}
        for fr in stack:
            if _is_bot(fr["frm"]) and fr["frm"] != alive:
                organts[str(fr["frm"])] = {
                    "state": "waiting", "pid": ch, "task": task_id,
                    "waiting_on": fr["to"], "since": fr["since"]}
    return {"pid": ch, "project": project, "organts": organts}


def mirror(flow, force=False):
    """flow 상태를 entity_status.json에 병합 기록. 같은 pid의 옛 organt 항목은 재계산."""
    global _last_write
    now = time.time()
    if not force and now - _last_write < _MIN_INTERVAL:
        return
    p = _path()
    if not p:
        return
    snap = snapshot(flow)
    if snap is None:
        return
    try:
        try:
            with open(p, encoding="utf-8") as f:
                cur = json.load(f) or {}
        except Exception:
            cur = {}
        projects = dict(cur.get("projects") or {})
        organts = {k: v for k, v in dict(cur.get("organts") or {}).items()
                   if (v or {}).get("pid") != snap["pid"]}
        key = str(snap["pid"])
        if snap["project"]["done"]:
            projects.pop(key, None)
        else:
            projects[key] = snap["project"]
            organts.update(snap["organts"])
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"projects": projects, "organts": organts, "updated": now},
                      f, ensure_ascii=False)
        os.replace(tmp, p)
        _last_write = now
    except Exception:
        pass
