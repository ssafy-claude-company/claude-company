#!/usr/bin/env bash
# obs.sh — organt 스택 터미널 빠른 관측 (관측계약 §회전·알림·ops. 순수 bash+python3, 외부 의존 없음)
#
#   ops/obs.sh          요약: 러너 생존(systemd+stats API heartbeat)·flow/audit 줄수/크기·
#                       최근 10개 flow 이벤트·denied율(audit 최근)·최근 알림(수렴경보+발송기록)
#   ops/obs.sh tail     flow.jsonl 실시간 tail(사람이 읽게 ts·event·요점 필드만)
#
#   env: ORGANT_PJT(기본 /root/ClaudeCompany) · STATE_DIR · ORGANT_STATS_URL(기본 로컬 웹 stats)
set -euo pipefail

PJT="${ORGANT_PJT:-/root/ClaudeCompany}"
STATE_DIR="${STATE_DIR:-$PJT/ops/var/organt_sns_state}"
FLOW="$STATE_DIR/flow.jsonl"
AUDIT="$STATE_DIR/audit.jsonl"
ALERT_STATE="$PJT/ops/var/.alert_state.json"
STATS_URL="${ORGANT_STATS_URL:-http://127.0.0.1:8000/api/stats/}"

# ── python 조각(quoting 안전하게 heredoc으로 정의; 데이터는 argv로 전달) ──
_py_engine=$(cat <<'PY'
# argv: stats API 응답 JSON — engine 생존 표시
import json, sys, time
try:
    e = (json.loads(sys.argv[1]).get("engine") or {})
except ValueError:
    print("  stats 파싱 실패"); raise SystemExit
last = e.get("last") or 0
ago = f"{time.time() - last:.0f}s 전" if last else "기록 없음"
print(f"  heartbeat(stats API): live={e.get('live')}  last_beat={ago}")
PY
)

_py_pretty=$(cat <<'PY'
# stdin의 JSONL을 사람이 읽게: "HH:MM:SS event  요점필드" 한 줄씩 (tail -F 파이프용)
import json, sys, time
for ln in sys.stdin:
    ln = ln.strip()
    if not ln:
        continue
    try:
        d = json.loads(ln)
    except ValueError:
        continue
    ts = d.pop("ts", None)
    ev = d.pop("event", "?")
    hh = time.strftime("%H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else "--:--:--"
    rest = " ".join(f"{k}={v}" for k, v in d.items() if k not in ("seq", "trace_id"))
    print(f"{hh}  {ev:<28} {rest[:110]}", flush=True)
PY
)

_py_recent=$(cat <<'PY'
# argv: 파일, N — 꼬리 N개 이벤트를 pretty 출력(파일 없으면 조용히 종료)
import json, os, sys, time
path, n = sys.argv[1], int(sys.argv[2])
try:
    size = os.path.getsize(path)
except OSError:
    print("  (파일 없음)"); sys.exit(0)
with open(path, "rb") as f:
    if size > 262144:
        f.seek(size - 262144); f.readline()
    lines = f.read().decode("utf-8", "replace").splitlines()
for ln in lines[-n:]:
    try:
        d = json.loads(ln)
    except ValueError:
        continue
    ts, ev = d.pop("ts", None), d.pop("event", "?")
    hh = time.strftime("%m-%d %H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else "?"
    rest = " ".join(f"{k}={v}" for k, v in d.items() if k not in ("seq", "trace_id"))
    print(f"  {hh}  {ev:<26} {rest[:96]}")
PY
)

_py_denied=$(cat <<'PY'
# argv: audit 파일 — 꼬리 512KB의 denied율(전체/최근 1h)
import json, os, sys, time
path = sys.argv[1]
try:
    size = os.path.getsize(path)
except OSError:
    print("  (audit 없음)"); sys.exit(0)
with open(path, "rb") as f:
    if size > 524288:
        f.seek(size - 524288); f.readline()
    lines = f.read().decode("utf-8", "replace").splitlines()
now = time.time(); tot = den = tot1h = den1h = 0
for ln in lines:
    try:
        d = json.loads(ln)
    except ValueError:
        continue
    ev = d.get("event")
    if ev not in ("tool_use", "tool_denied"):
        continue
    tot += 1; den += (ev == "tool_denied")
    if isinstance(d.get("ts"), (int, float)) and now - d["ts"] <= 3600:
        tot1h += 1; den1h += (ev == "tool_denied")
pct = lambda a, b: f"{a / b * 100:.1f}%" if b else "-"
print(f"  꼬리 표본 {tot}건: denied {den} ({pct(den, tot)})   |   최근 1h {tot1h}건: denied {den1h} ({pct(den1h, tot1h)})")
PY
)

_py_alerts=$(cat <<'PY'
# argv: flow 파일, alert_state 파일 — 최근 수렴경보/회로차단 + 마지막 발송 기록
import json, os, sys, time
flow, statef = sys.argv[1], sys.argv[2]
now = time.time(); found = []
try:
    size = os.path.getsize(flow)
    with open(flow, "rb") as f:
        if size > 524288:
            f.seek(size - 524288); f.readline()
        for ln in f.read().decode("utf-8", "replace").splitlines():
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if d.get("event") in ("convergence_alert", "loop_circuit_breaker"):
                found.append(d)
except OSError:
    pass
if found:
    for d in found[-5:]:
        ago = (now - d["ts"]) / 60 if isinstance(d.get("ts"), (int, float)) else -1
        print(f"  ⚠ {d['event']} task={d.get('task', '-')} ({ago:.0f}분 전)")
else:
    print("  수렴경보/회로차단 없음(flow 꼬리 기준)")
try:
    sent = json.load(open(statef)).get("sent", {})
    for k, v in sorted(sent.items(), key=lambda kv: -kv[1])[:5]:
        print(f"  발송기록 {k} — {(now - v) / 3600:.1f}h 전")
except (OSError, ValueError):
    print("  발송기록 없음(.alert_state.json)")
PY
)

_stat_line() {  # 파일 줄수·크기·회전본 개수 한 줄
  local f="$1" name; name=$(basename "$f")
  if [ -f "$f" ]; then
    local n b r
    n=$(wc -l <"$f"); b=$(stat -c%s "$f")
    # ls는 매치 0개에서 실패해 pipefail+set -e로 스크립트를 죽인다 — find는 0개여도 성공.
    r=$(find "$(dirname "$f")" -maxdepth 1 -name "$(basename "$f").[0-9]*" 2>/dev/null | wc -l)
    printf "  %-12s %7d줄 %9dB  회전본 %d개\n" "$name" "$n" "$b" "$r"
  else
    printf "  %-12s (없음)\n" "$name"
  fi
}

case "${1:-summary}" in
  tail)
    [ -f "$FLOW" ] || { echo "flow.jsonl 없음: $FLOW"; exit 1; }
    echo "── flow.jsonl 실시간 (Ctrl-C 종료) ──"
    exec tail -n 20 -F "$FLOW" | python3 -u -c "$_py_pretty"
    ;;
  summary)
    echo "══ organt 관측 요약 $(date '+%F %T') ══"
    echo "── 러너 ──"
    if command -v systemctl >/dev/null 2>&1; then
      echo "  systemd organt-runner: $(systemctl is-active organt-runner 2>/dev/null || echo unknown)"
    fi
    body=$(curl -sf -m 3 "$STATS_URL" 2>/dev/null) || body=""
    if [ -n "$body" ]; then
      python3 -c "$_py_engine" "$body"
    else
      echo "  stats API 접속 불가($STATS_URL) — 웹 다운이거나 원격에서 실행 중"
    fi
    echo "── 로그 파일 ($STATE_DIR) ──"
    _stat_line "$FLOW"
    _stat_line "$AUDIT"
    echo "── 최근 flow 이벤트 10 ──"
    python3 -c "$_py_recent" "$FLOW" 10
    echo "── denied율 (audit 꼬리) ──"
    python3 -c "$_py_denied" "$AUDIT"
    echo "── 알림 ──"
    python3 -c "$_py_alerts" "$FLOW" "$ALERT_STATE"
    ;;
  *)
    echo "사용: obs.sh [summary|tail]" >&2; exit 2
    ;;
esac
