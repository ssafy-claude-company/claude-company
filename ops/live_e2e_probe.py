#!/usr/bin/env python3
"""murmur live E2E probe.

Safe modes
----------
* No arguments: print the exact plan and exit without importing Django, making
  HTTP requests, or writing files.
* ``--pid U-NNN``: monitor the latest request in an existing channel using GET
  requests only. Observations are emitted to stdout; no artifact is written.
* ``--execute``: explicitly authorize creating (or reusing ``--pid``) a private
  channel, submitting the fixed state-machine request, and injecting the fixed
  correction once. Only this mode writes JSONL/report artifacts under
  ``ops/var/live_e2e_probe``.

The probe never stops a Task, fixes code, deploys, or restarts a service.
Dependencies are Python's standard library plus the project's Django runtime.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace
from typing import Any, Iterable
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "murmur" / "backend"
OUTPUT_DIR = ROOT / "ops" / "var" / "live_e2e_probe"
ENV_FILE = Path("/etc/murmur-web.env")
BASE_URL = "http://127.0.0.1:8000/api"
LEADER_BOT_ID = "1513819740940927067"
EXACT_VERIFIER = "node test_state_machine.js"
TERMINAL_BACKLOG_STATES = {"done", "dropped"}
BAD_TRACE_EVENTS = {
    "awaiting_human_closed",
    "awaiting_human_parked",
    "cancel_requested",
    "e2e_fail",
    "false_complete_blocked",
    "flow_idle_abort",
    "flow_user_stopped",
    "hard_blocked",
    "ms_replan_stuck",
    "ms_stopped_by_vote",
    "quota_halt",
    "quota_halt_closed",
    "run_loop_error",
    "stalled_stopped",
    "stage_stuck_parked",
    "interject_unacked",
    "request_repick",
    "request_repick_rejected",
    "user_stopped_closed",
}
STOPPED_TRACE_EVENTS = {
    "awaiting_human_closed",
    "false_complete_blocked",
    "quota_halt_closed",
    "request_start_failed_stopped",
    "stalled_stopped",
    "user_stopped_closed",
}
PID_RE = re.compile(r"^[A-Za-z]+-[0-9]+$")


REQUEST_TEXT = """작은 상태 머신을 한 번의 마일스톤으로 완성하세요.

산출물 계약:
- 프로젝트 루트의 사용자 산출물은 CommonJS `state_machine.js`와 `test_state_machine.js` 정확히 두 파일입니다. package.json, 문서, 배포 파일은 만들지 마세요(시스템 내부 `.collab/` 기록은 제외).
- `state_machine.js`는 `module.exports = { createStateMachine }`를 공개합니다.
- `createStateMachine()`이 반환한 객체의 공개 API는 인자 없는 `getState()`와 `transition(nextState)`뿐입니다. 초기 상태는 `idle`입니다.
- 상태 집합은 `idle`, `working`, `done`, `stopped`입니다. 허용 전이는 `idle→working`, `working→done`, `working→stopped` 정확히 세 개이며, 성공 시 새 상태를 반환합니다.
- 네 상태의 16개 순서쌍 중 나머지 13개 전이는 모두 `Error`를 throw하고 현재 상태를 바꾸지 않습니다. `transition(event)`나 `false` 반환 계약으로 바꾸지 마세요.
- `test_state_machine.js`는 외부 의존성 없이 16개 순서쌍을 모두 독립 검증하고, 성공 시 `PASS`를 출력하며 exit 0, 실패 시 non-zero로 끝납니다.
- 네트워크, 외부 의존성, 배포, 문서, 추가 기능, 버전 확장은 하지 마세요.

협업·검증 계약:
- 구현 백로그와 독립 QA 백로그를 서로 다른 담당자에게 각각 등록하고 실제로 수행하세요.
- 등록한 백로그는 dropped/blocked로 마감하지 말고 모두 done으로 끝낸 뒤에만 마일스톤 검증을 시작하세요.
- GOAL과 마일스톤은 위 공개 API·금지 전이의 Error+상태 불변 계약을 그대로 보존하세요.
- 비준된 완수조건은 `네 상태의 16개 전이와 공개 API 계약 충족 | 실증: node test_state_machine.js`입니다.
- 최종 verifier는 정확히 `node test_state_machine.js` 하나이며, 실제 실행 증거와 독립 QA 확인을 남긴 뒤 Task를 완료하세요."""


CORRECTION_TEXT = (
    "[계약 교정] 비준된 상위 계약을 그대로 유지하세요. 공개 함수는 transition(nextState), "
    "금지 전이는 Error throw와 상태 불변이며 verifier는 node test_state_machine.js입니다. "
    "transition(event)나 false 반환으로 바꾸지 마세요. idle→done 금지 전이도 명시 검증하세요."
)


class ProbeError(RuntimeError):
    """Expected, sanitized probe failure."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def stable_fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("0보다 커야 합니다.")
    return number


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("0보다 커야 합니다.")
    return number


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "murmur 상태 머신 live E2E를 관측합니다. 무인자 기본값은 완전 dry-run이며, "
            "외부 쓰기는 명시적인 --execute에서만 일어납니다."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="채널/요청 생성과 1회 교정 주입, ops/var 증거 기록을 명시적으로 허용",
    )
    parser.add_argument(
        "--pid",
        help=(
            "대상 채널 PID. --execute와 함께면 이 채널에 새 요청을 제출하고, "
            "--execute 없이면 최신 요청을 GET으로만 관측"
        ),
    )
    parser.add_argument(
        "--max-seconds",
        type=_positive_float,
        default=10800.0,
        help="종결을 기다릴 최대 시간",
    )
    parser.add_argument(
        "--poll-seconds",
        type=_positive_float,
        default=2.0,
        help="messages/ms_status/activity/trace 폴링 간격",
    )
    parser.add_argument(
        "--settle-polls",
        type=_positive_int,
        default=3,
        help="request done + flow_done 뒤 최종 미러를 기다릴 연속 폴 수",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="한 번만 읽고 중간 assertion 보고서를 출력(장기 관측 점검용)",
    )
    args = parser.parse_args(argv)
    if args.pid and not PID_RE.fullmatch(args.pid.strip()):
        parser.error("--pid는 U-053 같은 형식이어야 합니다.")
    if args.pid:
        args.pid = args.pid.strip().upper()
    if args.execute and args.once:
        parser.error("--execute에서는 --once를 사용할 수 없습니다.")
    return args


def dry_run_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "mode": "dry-run",
        "mutations": False,
        "http": False,
        "django": False,
        "artifacts": False,
        "would_create_channel": not bool(args.pid),
        "would_use_pid": args.pid,
        "leader_bot_id": LEADER_BOT_ID,
        "verifier": EXACT_VERIFIER,
        "request": REQUEST_TEXT,
        "correction": CORRECTION_TEXT,
        "next": (
            "새 실판: python ops/live_e2e_probe.py --execute"
            if not args.pid
            else f"읽기 전용 관측: python ops/live_e2e_probe.py --pid {args.pid}"
        ),
    }


def _load_environment_file(path: Path) -> None:
    """Load a simple systemd EnvironmentFile without ever echoing its values."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ProbeError(f"Django 환경 파일을 읽지 못했습니다: {path} ({exc})") from exc
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def latest_hj_token() -> str:
    """Return the newest @hj PersonSession token. The caller must never log it."""
    _load_environment_file(ENV_FILE)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        import django

        django.setup()
        from sns.models import PersonSession

        session = (
            PersonSession.objects.filter(person__handle="hj")
            .select_related("person")
            .order_by("-last_seen", "-id")
            .first()
        )
    except Exception as exc:
        raise ProbeError(f"@hj 세션을 ORM에서 읽지 못했습니다: {type(exc).__name__}") from exc
    if session is None:
        raise ProbeError("@hj의 PersonSession이 없습니다.")
    if session.person.is_guest:
        raise ProbeError("@hj 세션이 guest 계정을 가리킵니다.")
    if not getattr(session.person, "is_admin", False):
        raise ProbeError("@hj가 admin이 아니어서 trace 관측을 수행할 수 없습니다.")
    token = str(session.token or "")
    if not token:
        raise ProbeError("@hj의 최신 PersonSession 토큰이 비었습니다.")
    return token


class LocalHttp:
    """Small local-only JSON client. Its token has no printable representation."""

    def __init__(self, token: str, timeout: float = 20.0):
        self._token = token
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise ProbeError("HTTP path는 /로 시작해야 합니다.")
        url = BASE_URL + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Token {self._token}",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                code = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            detail = _safe_response_detail(raw)
            raise ProbeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProbeError(f"{method} {path} 로컬 HTTP 실패: {type(exc).__name__}: {exc}") from exc
        if not 200 <= code < 300:
            raise ProbeError(f"{method} {path} -> HTTP {code}")
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProbeError(f"{method} {path} 응답이 JSON이 아닙니다.") from exc
        if not isinstance(parsed, dict):
            raise ProbeError(f"{method} {path} 응답이 JSON object가 아닙니다.")
        return parsed

    def get(self, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, query=query)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, payload=payload)


def _safe_response_detail(raw: bytes) -> str:
    try:
        value = json.loads(raw.decode("utf-8"))
        if isinstance(value, dict):
            return str(value.get("detail") or value.get("error") or "요청 거부")[:300]
    except Exception:
        pass
    return "요청 거부"


class ObservationSink:
    """JSONL sink. A file is opened only in explicit execute mode."""

    def __init__(self, path: Path | None):
        self.path = path
        self._stream = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self._stream = os.fdopen(fd, "w", encoding="utf-8")

    def emit(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if self._stream is None:
            print(line, flush=True)
            return
        self._stream.write(line + "\n")
        self._stream.flush()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Atomically write a mode-0600 report (execute mode only)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def milestones(status: dict[str, Any]) -> list[dict[str, Any]]:
    rows = status.get("list")
    if isinstance(rows, list):
        return [x for x in rows if isinstance(x, dict)]
    return [status] if status.get("ms") else []


def backlog_rows(status: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ms in milestones(status):
        ms_id = str(ms.get("ms") or "")
        for st in ms.get("sts") or []:
            if not isinstance(st, dict):
                continue
            st_id = str(st.get("id") or "")
            for backlog in st.get("bl") or []:
                if not isinstance(backlog, dict):
                    continue
                row = dict(backlog)
                row["ms"] = ms_id
                row["st"] = st_id
                row["scope"] = f"{st_id}::{row.get('id') or ''}"
                out.append(row)
    return out


def request_message(messages: dict[str, Any], request_id: int) -> dict[str, Any] | None:
    for row in messages.get("messages") or []:
        if not isinstance(row, dict):
            continue
        try:
            rid = int(row.get("request_id"))
        except (TypeError, ValueError):
            continue
        if rid == request_id and row.get("kind") == "user_request":
            return row
    return None


def latest_request_id(messages: dict[str, Any]) -> int | None:
    ids = []
    for row in messages.get("messages") or []:
        if not isinstance(row, dict) or row.get("kind") != "user_request":
            continue
        try:
            ids.append(int(row.get("request_id")))
        except (TypeError, ValueError):
            pass
    return max(ids) if ids else None


def trace_fingerprint(event: dict[str, Any]) -> str:
    return stable_fingerprint(
        {
            "src": event.get("src"),
            "seq": event.get("seq"),
            "ts": event.get("ts"),
            "event": event.get("event"),
        }
    )


def milestone_r2_trigger(events: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the first meet_r2_inject while the trace's current stage is milestone."""
    stage = None
    for event in events:
        if not isinstance(event, dict):
            continue
        value = str(event.get("stage") or "").strip()
        if value:
            stage = value
        if event.get("event") == "meet_r2_inject" and stage == "milestone":
            return event
    return None


def _tail_is_monotonic(previous: list[str], current: list[str], total_delta: int) -> bool:
    """Validate append-only tails, including a fixed-size sliding window."""
    if not previous:
        return True
    if total_delta < 0:
        return False
    if total_delta == 0:
        return previous == current
    if len(current) >= len(previous) and current[: len(previous)] == previous:
        return True
    if total_delta >= len(previous):
        return True
    overlap = len(previous) - total_delta
    return previous[-overlap:] == current[:overlap]


class ProbeState:
    def __init__(self, *, execute: bool, pid: str, request_id: int, trace_id: str):
        self.execute = execute
        self.pid = pid
        self.request_id = request_id
        self.trace_id = trace_id
        self.started_at = utc_now()
        self.started_monotonic = time.monotonic()
        self.polls = 0
        self.max_active_backlogs = 0
        self.active_backlog_seen = False
        self.activity_seen = False
        self.scoped_activity_seen = False
        self.correction_attempts = 0
        self.correction_posts = 0
        self.correction_trigger: dict[str, Any] | None = None
        self.seen_messages: set[str] = set()
        self.seen_trace: set[str] = set()
        self.trace_events: list[dict[str, Any]] = []
        self.previous_activity_lines: list[str] = []
        self.previous_activity_total: int | None = None
        self.previous_recorded_total: int | None = None
        self.backlog_history: dict[str, dict[str, Any]] = {}
        self.violations: list[dict[str, Any]] = []
        self.latest_messages: dict[str, Any] = {}
        self.latest_status: dict[str, Any] = {}
        self.latest_activity: dict[str, Any] = {}
        self.runtime_evidence: dict[str, Any] = {}
        self.runtime_evidence_error: str | None = None

    def violate(self, kind: str, detail: str) -> None:
        row = {"kind": kind, "detail": detail}
        if row not in self.violations:
            self.violations.append(row)

    def observe(
        self,
        message_data: dict[str, Any],
        status: dict[str, Any],
        activity: dict[str, Any],
        trace: dict[str, Any],
    ) -> dict[str, Any]:
        self.polls += 1
        self.latest_messages = message_data
        self.latest_status = status
        self.latest_activity = activity

        rows = backlog_rows(status)
        active = [row for row in rows if row.get("s") == "in_progress"]
        self.max_active_backlogs = max(self.max_active_backlogs, len(active))
        if active:
            self.active_backlog_seen = True
        if len(active) > 1:
            self.violate(
                "multiple_active_backlogs",
                "동시에 in_progress인 scope: " + ", ".join(str(x["scope"]) for x in active),
            )

        current_scopes = set()
        for row in rows:
            scope = str(row["scope"])
            current_scopes.add(scope)
            status_now = str(row.get("s") or "")
            activity_now = [str(x) for x in (row.get("act") or [])]
            if activity_now:
                self.scoped_activity_seen = True
            old = self.backlog_history.get(scope)
            if old:
                old_status = old["status"]
                old_activity = old["activity"]
                if old_status in TERMINAL_BACKLOG_STATES and status_now != old_status:
                    self.violate(
                        "terminal_backlog_regressed",
                        f"{scope}: {old_status} -> {status_now}",
                    )
                if len(activity_now) < len(old_activity) and len(old_activity) < 200:
                    self.violate(
                        "backlog_activity_shrank",
                        f"{scope}: {len(old_activity)} -> {len(activity_now)}",
                    )
                elif len(activity_now) >= len(old_activity) and activity_now[: len(old_activity)] != old_activity:
                    if not (
                        len(old_activity) == len(activity_now) == 200
                        and any(
                            old_activity[-n:] == activity_now[:n]
                            for n in range(1, len(old_activity) + 1)
                        )
                    ):
                        self.violate("backlog_activity_rewritten", scope)
            self.backlog_history[scope] = {
                "status": status_now,
                "activity": activity_now,
                "assignee": row.get("aid"),
            }

        lines = [str(x) for x in (activity.get("lines") or [])]
        previous_lines = list(self.previous_activity_lines)
        try:
            total = int(activity.get("total") or 0)
            recorded = int(activity.get("recorded_total") or 0)
        except (TypeError, ValueError):
            total, recorded = 0, 0
            self.violate("activity_count_invalid", "activity count가 정수가 아닙니다.")
        if total or recorded or lines:
            self.activity_seen = True
        if self.previous_activity_total is not None:
            delta = total - self.previous_activity_total
            if total < self.previous_activity_total:
                self.violate(
                    "activity_total_regressed",
                    f"{self.previous_activity_total} -> {total}",
                )
            elif not _tail_is_monotonic(self.previous_activity_lines, lines, delta):
                self.violate("activity_tail_rewritten", f"delta={delta}")
        if (
            self.previous_recorded_total is not None
            and recorded < self.previous_recorded_total
        ):
            self.violate(
                "activity_recorded_total_regressed",
                f"{self.previous_recorded_total} -> {recorded}",
            )
        activity_delta = _activity_delta(previous_lines, lines)
        self.previous_activity_lines = lines
        self.previous_activity_total = total
        self.previous_recorded_total = recorded

        new_trace = []
        for event in trace.get("events") or []:
            if not isinstance(event, dict):
                continue
            fp = trace_fingerprint(event)
            if fp in self.seen_trace:
                continue
            self.seen_trace.add(fp)
            self.trace_events.append(event)
            new_trace.append(event)

        new_messages = []
        for row in message_data.get("messages") or []:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or stable_fingerprint(row))
            if key in self.seen_messages:
                continue
            self.seen_messages.add(key)
            new_messages.append(row)

        req = request_message(message_data, self.request_id)
        live = dict(message_data.get("live_status") or {})
        live.pop("activity", None)
        return {
            "schema": 1,
            "kind": "poll",
            "at": utc_now(),
            "elapsed_s": round(time.monotonic() - self.started_monotonic, 1),
            "pid": self.pid,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "request_state": (req or {}).get("request_state"),
            "request_latest": (req or {}).get("request_latest"),
            "request_actionable": (req or {}).get("request_actionable"),
            "live_status": live or None,
            "pending_count": message_data.get("pending_count"),
            "stuck_count": message_data.get("stuck_count"),
            "context": message_data.get("context"),
            "active_backlogs": [
                {
                    "scope": row["scope"],
                    "assignee": row.get("aid"),
                    "description": row.get("d"),
                }
                for row in active
            ],
            "backlog_states": {
                str(row["scope"]): str(row.get("s") or "") for row in rows
            },
            "ms_status": status,
            "activity": {
                "total": total,
                "recorded_total": recorded,
                "partial": activity.get("partial"),
                "new_tail": activity_delta,
            },
            "new_messages": new_messages,
            "new_trace_events": new_trace,
            "violations": list(self.violations),
        }

    def report(self, outcome: str) -> dict[str, Any]:
        rows = backlog_rows(self.latest_status)
        ms_rows = milestones(self.latest_status)
        req = request_message(self.latest_messages, self.request_id) or {}
        events = self.trace_events
        names = [str(event.get("event") or "") for event in events]
        event_set = set(names)
        bad = sorted(event_set & BAD_TRACE_EVENTS)
        correction_messages = [
            row
            for row in (self.latest_messages.get("messages") or [])
            if isinstance(row, dict)
            and row.get("type") == "human"
            and row.get("interject")
            and str(row.get("body") or "") == CORRECTION_TEXT
        ]
        all_criteria = [
            criterion
            for ms in ms_rows
            for criterion in (ms.get("cr") or [])
            if isinstance(criterion, dict)
        ]
        exact_criteria = [
            criterion
            for criterion in all_criteria
            if str(criterion.get("v") or "").strip() == EXACT_VERIFIER
        ]
        done_events = [event for event in events if event.get("event") == "backlog_done"]
        dropped_events = [
            event for event in events if event.get("event") == "backlog_dropped"
        ]
        ms_done_events = [event for event in events if event.get("event") == "ms_done"]
        human_slots = [
            event
            for event in events
            if event.get("event") == "meet_human_info_slot"
            and event.get("stage") == "milestone"
        ]
        terminal_before_ms = False
        if ms_done_events and rows:
            last_ms_done = max(float(x.get("ts") or 0) for x in ms_done_events)
            terminal_events_before = [
                event
                for event in done_events + dropped_events
                if float(event.get("ts") or 0) <= last_ms_done
            ]
            terminal_before_ms = len(terminal_events_before) >= len(rows)

        live_ms_rows = [
            ms for ms in ms_rows if str(ms.get("status") or "") != "superseded"
        ]
        live_st_rows = [
            st
            for ms in live_ms_rows
            for st in (ms.get("sts") or [])
            if isinstance(st, dict)
        ]
        final_scopes = [
            f"{str(st.get('id') or '')}::{str(backlog.get('id') or '')}"
            for st in live_st_rows
            for backlog in (st.get("bl") or [])
            if isinstance(backlog, dict)
        ]
        structure_ok = (
            bool(live_ms_rows)
            and all(
                bool(
                    [
                        st
                        for st in (ms.get("sts") or [])
                        if isinstance(st, dict)
                    ]
                )
                for ms in live_ms_rows
            )
            and bool(live_st_rows)
            and all(
                st.get("s") == "done"
                and bool(
                    [
                        backlog
                        for backlog in (st.get("bl") or [])
                        if isinstance(backlog, dict)
                    ]
                )
                for st in live_st_rows
            )
            and len(final_scopes) == len(set(final_scopes))
        )
        assignees = {str(row.get("aid")) for row in rows if row.get("aid")}
        assertions = [
            _assertion(
                "request_done",
                req.get("request_state") == "done",
                f"state={req.get('request_state')!r}",
            ),
            _assertion(
                "request_not_stopped",
                req.get("request_state") != "stopped",
                f"state={req.get('request_state')!r}",
            ),
            _assertion("flow_done_event", "flow_done" in event_set, _event_count(names, "flow_done")),
            _assertion("e2e_pass_event", "e2e_pass" in event_set, _event_count(names, "e2e_pass")),
            _assertion(
                "milestones_all_done",
                bool(live_ms_rows)
                and all(ms.get("status") == "done" for ms in live_ms_rows),
                {str(ms.get("ms")): ms.get("status") for ms in live_ms_rows},
            ),
            _assertion(
                "milestone_subtask_backlog_structure",
                structure_ok,
                {
                    "milestones": len(live_ms_rows),
                    "subtasks": len(live_st_rows),
                    "backlog_scopes": len(final_scopes),
                    "duplicate_scopes": len(final_scopes) - len(set(final_scopes)),
                    "by_milestone": {
                        str(ms.get("ms") or ""): {
                            "subtasks": len(
                                [
                                    st
                                    for st in (ms.get("sts") or [])
                                    if isinstance(st, dict)
                                ]
                            ),
                            "status": ms.get("status"),
                        }
                        for ms in live_ms_rows
                    },
                },
            ),
            _assertion(
                "all_backlogs_done",
                len(rows) >= 2 and all(row.get("s") == "done" for row in rows),
                {str(row["scope"]): row.get("s") for row in rows},
            ),
            _assertion(
                "implementation_and_independent_qa",
                len(rows) >= 2 and len(assignees) >= 2,
                {"backlogs": len(rows), "distinct_assignees": len(assignees)},
            ),
            _assertion(
                "max_one_active_backlog",
                self.max_active_backlogs <= 1
                and not any(v["kind"] == "multiple_active_backlogs" for v in self.violations),
                f"max={self.max_active_backlogs}",
            ),
            _assertion(
                "active_backlog_was_visible",
                self.active_backlog_seen,
                "실행 중 in_progress 장부를 최소 한 번 관측",
            ),
            _assertion(
                "activity_monotonic",
                not any(
                    v["kind"]
                    in {
                        "activity_total_regressed",
                        "activity_recorded_total_regressed",
                        "activity_tail_rewritten",
                        "activity_count_invalid",
                    }
                    for v in self.violations
                ),
                [v for v in self.violations if v["kind"].startswith("activity_")],
            ),
            _assertion(
                "backlog_activity_monotonic",
                not any(
                    v["kind"]
                    in {
                        "backlog_activity_shrank",
                        "backlog_activity_rewritten",
                        "terminal_backlog_regressed",
                    }
                    for v in self.violations
                ),
                [
                    v
                    for v in self.violations
                    if v["kind"].startswith("backlog_")
                    or v["kind"] == "terminal_backlog_regressed"
                ],
            ),
            _assertion("task_activity_seen", self.activity_seen, self.latest_activity.get("recorded_total")),
            _assertion(
                "terminal_activity_complete",
                self.latest_activity.get("partial") is False
                and _safe_int(self.latest_activity.get("total"), -1) > 0
                and _safe_int(self.latest_activity.get("total"), -1)
                == _safe_int(self.latest_activity.get("recorded_total"), -2),
                {
                    "partial": self.latest_activity.get("partial"),
                    "total": self.latest_activity.get("total"),
                    "recorded_total": self.latest_activity.get("recorded_total"),
                },
            ),
            _assertion(
                "scoped_backlog_activity_seen",
                self.scoped_activity_seen,
                "백로그 act 원장에 활동이 남음",
            ),
            _assertion(
                "exact_verifier_evidence",
                bool(exact_criteria)
                and any(
                    criterion.get("p")
                    and EXACT_VERIFIER in str(criterion.get("e") or "")
                    and "exit=0" in str(criterion.get("e") or "")
                    for criterion in exact_criteria
                ),
                exact_criteria,
            ),
            _assertion(
                "all_milestone_criteria_have_evidence",
                bool(all_criteria)
                and all(
                    criterion.get("w")
                    or (
                        criterion.get("p")
                        and bool(str(criterion.get("e") or "").strip())
                    )
                    for criterion in all_criteria
                ),
                {"criteria": len(all_criteria)},
            ),
            _assertion(
                "no_waived_milestone_criteria",
                bool(all_criteria) and not any(x.get("w") for x in all_criteria),
                {"waived": sum(1 for x in all_criteria if x.get("w"))},
            ),
            _assertion(
                "backlogs_terminal_before_milestone_done",
                terminal_before_ms,
                {
                    "backlogs": len(rows),
                    "backlog_done_events": len(done_events),
                    "backlog_dropped_events": len(dropped_events),
                    "ms_done_events": len(ms_done_events),
                },
            ),
            _assertion(
                "correction_visible_once",
                len(correction_messages) == 1,
                f"count={len(correction_messages)}",
            ),
            _assertion(
                "correction_delivered",
                "human_info_delivered" in event_set,
                _event_count(names, "human_info_delivered"),
            ),
            _assertion(
                "milestone_reconsidered_after_correction",
                bool(human_slots),
                f"meet_human_info_slot(stage=milestone)={len(human_slots)}",
            ),
            _assertion(
                "execute_injected_at_most_once",
                (not self.execute)
                or (self.correction_attempts == 1 and self.correction_posts == 1),
                {
                    "attempts": self.correction_attempts,
                    "successful_posts": self.correction_posts,
                },
            ),
            _assertion("no_bad_terminal_events", not bad, bad),
            _assertion(
                "no_request_repick",
                "request_repick" not in event_set
                and "request_repick_rejected" not in event_set,
                {
                    "request_repick": names.count("request_repick"),
                    "request_repick_rejected": names.count("request_repick_rejected"),
                },
            ),
            _assertion(
                "db_request_root_terminal",
                bool(self.runtime_evidence.get("db", {}).get("request_root_terminal")),
                self.runtime_evidence.get("db"),
            ),
            _assertion(
                "db_channel_and_request_contract",
                self.runtime_evidence.get("db", {}).get("visibility") == "private"
                and self.runtime_evidence.get("db", {}).get("leader_bot_id")
                == LEADER_BOT_ID
                and bool(
                    self.runtime_evidence.get("db", {}).get("request_body_exact")
                )
                and _safe_int(
                    self.runtime_evidence.get("db", {}).get("root_responses"), 0
                )
                == 1,
                self.runtime_evidence.get("db"),
            ),
            _assertion(
                "db_hj_owner_and_active_membership",
                self.runtime_evidence.get("db", {}).get("owner_handle") == "hj"
                and self.runtime_evidence.get("db", {}).get(
                    "hj_active_memberships"
                )
                == 1,
                {
                    "owner_handle": self.runtime_evidence.get("db", {}).get(
                        "owner_handle"
                    ),
                    "hj_active_memberships": self.runtime_evidence.get(
                        "db", {}
                    ).get("hj_active_memberships"),
                },
            ),
            _assertion(
                "terminal_api_consistent",
                req.get("request_state") == "done"
                and req.get("request_latest") is True
                and req.get("request_actionable") is False
                and _safe_int(self.latest_messages.get("pending_count"), -1) == 0
                and _safe_int(self.latest_messages.get("stuck_count"), -1) == 0
                and (self.latest_messages.get("context") or {}).get("terminal")
                == "done"
                and (self.latest_messages.get("live_status") or {}).get("state")
                not in {"working", "stopped"},
                {
                    "request_state": req.get("request_state"),
                    "request_latest": req.get("request_latest"),
                    "request_actionable": req.get("request_actionable"),
                    "pending_count": self.latest_messages.get("pending_count"),
                    "stuck_count": self.latest_messages.get("stuck_count"),
                    "context_terminal": (
                        self.latest_messages.get("context") or {}
                    ).get("terminal"),
                    "live_state": (
                        self.latest_messages.get("live_status") or {}
                    ).get("state"),
                },
            ),
            _assertion(
                "db_correction_exactly_once",
                self.runtime_evidence.get("db", {}).get("correction_rows") == 1,
                self.runtime_evidence.get("db", {}).get("correction_rows"),
            ),
            _assertion(
                "no_residual_control_signals",
                self.runtime_evidence.get("db", {}).get("interject_signals") == 0
                and self.runtime_evidence.get("db", {}).get("stop_signals") == 0,
                {
                    "interject_signals": self.runtime_evidence.get("db", {}).get(
                        "interject_signals"
                    ),
                    "stop_signals": self.runtime_evidence.get("db", {}).get(
                        "stop_signals"
                    ),
                },
            ),
            _assertion(
                "no_incomplete_db_requests",
                self.runtime_evidence.get("db", {}).get("incomplete_requests") == 0,
                self.runtime_evidence.get("db", {}).get("incomplete_requests"),
            ),
            _assertion(
                "projects_json_root_closed",
                bool(
                    self.runtime_evidence.get("projects_json", {}).get(
                        "root_closed"
                    )
                ),
                self.runtime_evidence.get("projects_json"),
            ),
            _assertion(
                "projects_json_no_pending_interject",
                bool(
                    self.runtime_evidence.get("projects_json", {}).get(
                        "interject_drained"
                    )
                ),
                self.runtime_evidence.get("projects_json"),
            ),
            _assertion(
                "workspace_exactly_two_files",
                self.runtime_evidence.get("workspace", {}).get("files")
                == ["state_machine.js", "test_state_machine.js"],
                self.runtime_evidence.get("workspace"),
            ),
            _assertion(
                "http_file_surface_exactly_two_files",
                self.runtime_evidence.get("workspace", {}).get("http_files")
                == ["state_machine.js", "test_state_machine.js"],
                self.runtime_evidence.get("workspace", {}).get("http_files"),
            ),
            _assertion(
                "local_exact_verifier_pass",
                bool(
                    self.runtime_evidence.get("local_verifier", {}).get("ok")
                    and self.runtime_evidence.get("local_verifier", {}).get(
                        "stdout_pass"
                    )
                    and self.runtime_evidence.get("local_verifier", {}).get("rc")
                    == 0
                    and self.runtime_evidence.get("local_verifier", {}).get(
                        "hashes_stable"
                    )
                    and self.runtime_evidence.get("local_verifier", {}).get(
                        "files_after"
                    )
                    == ["state_machine.js", "test_state_machine.js"]
                ),
                self.runtime_evidence.get("local_verifier"),
            ),
            _assertion(
                "local_verifier_did_not_mutate_artifact",
                bool(
                    self.runtime_evidence.get("local_verifier", {}).get(
                        "artifact_stable"
                    )
                ),
                self.runtime_evidence.get("local_verifier"),
            ),
            _assertion(
                "e2e_receipt_stamp_epoch_integrity",
                bool(
                    self.runtime_evidence.get("e2e_receipts", {}).get(
                        "integrity_ok"
                    )
                ),
                self.runtime_evidence.get("e2e_receipts"),
            ),
            _assertion(
                "e2e_checklist_results_complete",
                bool(
                    self.runtime_evidence.get("e2e_receipts", {}).get(
                        "checklist_results_complete"
                    )
                ),
                self.runtime_evidence.get("e2e_receipts"),
            ),
        ]
        failed = [x["name"] for x in assertions if not x["ok"]]
        return {
            "schema": 1,
            "mode": "execute" if self.execute else "read-only",
            "outcome": outcome,
            "passed": outcome == "terminal" and not failed,
            "started_at": self.started_at,
            "finished_at": utc_now(),
            "elapsed_s": round(time.monotonic() - self.started_monotonic, 1),
            "pid": self.pid,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "verifier": EXACT_VERIFIER,
            "polls": self.polls,
            "assertions": assertions,
            "failed_assertions": failed,
            "violations": self.violations,
            "runtime_evidence_error": self.runtime_evidence_error,
            "runtime_evidence": self.runtime_evidence,
            "metrics": {
                "milestones": len(ms_rows),
                "backlogs": len(rows),
                "distinct_assignees": len(assignees),
                "trace_events": len(events),
                "activity_total": self.latest_activity.get("total"),
                "activity_recorded_total": self.latest_activity.get("recorded_total"),
                "max_active_backlogs": self.max_active_backlogs,
            },
        }


def _activity_delta(previous: list[str], current: list[str]) -> list[str]:
    """Best-effort display delta; assertion logic runs separately."""
    if not previous:
        return current
    if len(current) >= len(previous) and current[: len(previous)] == previous:
        return current[len(previous) :]
    for overlap in range(min(len(previous), len(current)), 0, -1):
        if previous[-overlap:] == current[:overlap]:
            return current[overlap:]
    return current


def _assertion(name: str, ok: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _event_count(names: list[str], name: str) -> str:
    return f"count={names.count(name)}"


def _workspace_file_manifest(workspace: str) -> tuple[list[str], dict[str, str], bool]:
    """List real user files, excluding only SYS process metadata and git internals."""
    root = os.path.realpath(workspace)
    files: list[str] = []
    truncated = False
    for base, dirs, names in os.walk(root, topdown=True, followlinks=False):
        kept_dirs = []
        for name in sorted(dirs):
            rel = os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
            if name in {".git", ".collab"}:
                continue
            full = os.path.join(base, name)
            if os.path.islink(full):
                files.append(rel + "/@symlink")
            elif name == "node_modules":
                files.append(rel + "/@directory")
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(names):
            rel = os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
            files.append(rel)
            if len(files) >= 5000:
                truncated = True
                break
        if truncated:
            break
    files = sorted(set(files))
    hashes: dict[str, str] = {}
    for rel in ("state_machine.js", "test_state_machine.js"):
        source_path = os.path.join(root, rel)
        if os.path.islink(source_path):
            continue
        path = os.path.realpath(source_path)
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            continue
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            continue
        hashes[rel] = digest.hexdigest()
    return files, hashes, truncated


def _runtime_registry_entry(channel_id: int) -> tuple[Path, dict[str, Any]]:
    path = ROOT / "ops" / "var" / "organt_sns_state" / "projects.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"projects.json을 읽지 못했습니다: {type(exc).__name__}") from exc
    projects = data.get("projects") if isinstance(data, dict) else None
    entry = (projects or {}).get(str(channel_id)) if isinstance(projects, dict) else None
    if not isinstance(entry, dict):
        raise ProbeError(f"projects.json에 channel {channel_id} root가 없습니다.")
    return path, entry


def _e2e_receipt_evidence(entry: dict[str, Any], workspace: str) -> dict[str, Any]:
    """Re-run the same structural checks used by recovery, without mutating state."""
    from system.rule.evidence import (
        normalize_verifier_command,
        verifier_command_hash,
        verifier_spec_hash,
    )
    from system.rule.milestone import workspace_artifact_stamp

    checklist = entry.get("e2e_checklist")
    results = entry.get("e2e_results")
    wrapup = entry.get("wrapup_state")
    last_task = entry.get("last_task") or {}
    writes = last_task.get("writes_by_role") or {}
    try:
        epoch = sum(int(value or 0) for value in writes.values())
    except (AttributeError, TypeError, ValueError):
        epoch = -1
    actual_stamp = workspace_artifact_stamp(SimpleNamespace(workspace=workspace))
    problems: list[str] = []
    if not isinstance(checklist, list) or not checklist:
        problems.append("checklist_missing")
        checklist = []
    if not isinstance(results, dict):
        problems.append("results_missing")
        results = {}
    if not isinstance(wrapup, dict):
        problems.append("wrapup_missing")
        wrapup = {}

    items: dict[str, dict[str, Any]] = {}
    item_summary = []
    for item in checklist:
        if not isinstance(item, dict):
            problems.append("item_not_object")
            continue
        iid = str(item.get("id") or "")
        spec = str(item.get("verifier_spec") or item.get("spec") or "")
        command = normalize_verifier_command(item.get("verifier_command"))
        if not iid or iid in items or not spec:
            problems.append(f"item_identity:{iid or '?'}")
            continue
        items[iid] = item
        command_hash_ok = bool(command) and (
            item.get("verifier_command_hash") == verifier_command_hash(command)
        )
        spec_hash_ok = (
            item.get("verifier_spec_hash") == verifier_spec_hash(iid, spec)
        )
        if command != EXACT_VERIFIER:
            problems.append(f"command_not_exact:{iid}")
        if not command_hash_ok:
            problems.append(f"command_hash:{iid}")
        if not spec_hash_ok:
            problems.append(f"spec_hash:{iid}")
        if not item.get("verifier_used"):
            problems.append(f"verifier_unused:{iid}")
        if item.get("verifier_stamp") != actual_stamp:
            problems.append(f"verifier_stamp:{iid}")
        try:
            item_epoch = int(item.get("verifier_epoch", -1))
        except (TypeError, ValueError):
            item_epoch = -1
        if item_epoch != epoch:
            problems.append(f"verifier_epoch:{iid}")
        item_summary.append(
            {
                "id": iid,
                "kind": item.get("kind"),
                "command": command,
                "command_hash_ok": command_hash_ok,
                "spec_hash_ok": spec_hash_ok,
                "verifier_used": bool(item.get("verifier_used")),
                "verifier_stamp_matches": item.get("verifier_stamp") == actual_stamp,
                "verifier_epoch": item_epoch,
            }
        )

    result_summary = []
    receipt_ids: set[str] = set()
    for iid, result in results.items():
        if iid not in items or not isinstance(result, dict):
            problems.append(f"unknown_result:{iid}")
            continue
        item = items[iid]
        receipt_id = str(result.get("receipt_id") or "")
        if not receipt_id or receipt_id in receipt_ids:
            problems.append(f"receipt_id:{iid}")
        receipt_ids.add(receipt_id)
        checks = {
            "ok": bool(result.get("ok")),
            "source": result.get("evidence_source") == "sys_run",
            "stamp": result.get("artifact_stamp") == actual_stamp,
            "epoch": _safe_int(result.get("write_epoch"), -1) == epoch,
            "seal": result.get("verifier_seal") == item.get("verifier_seal"),
            "command": normalize_verifier_command(result.get("verified_command"))
            == normalize_verifier_command(item.get("verifier_command")),
            "command_hash": result.get("command_hash")
            == item.get("verifier_command_hash"),
            "spec_hash": result.get("spec_hash") == item.get("verifier_spec_hash"),
            "evidence": bool(str(result.get("evidence") or "").strip()),
            "pass_output": "PASS" in str(result.get("evidence") or ""),
        }
        for name, ok in checks.items():
            if not ok:
                problems.append(f"result_{name}:{iid}")
        result_summary.append(
            {
                "id": iid,
                "receipt_id": receipt_id,
                **checks,
            }
        )

    result_complete = bool(items) and set(results) == set(items)
    if not result_complete:
        problems.append("checklist_result_set_mismatch")
    wrapup_checks = {
        "verdict": wrapup.get("verdict") == "e2e_pass",
        "stamp": wrapup.get("artifact_stamp") == actual_stamp,
        "epoch": _safe_int(wrapup.get("write_epoch"), -1) == epoch,
        "no_defects": not (wrapup.get("defects") or []),
    }
    for name, ok in wrapup_checks.items():
        if not ok:
            problems.append(f"wrapup_{name}")

    return {
        "integrity_ok": not problems,
        "checklist_results_complete": result_complete
        and bool(result_summary)
        and all(all(v for k, v in row.items() if k not in {"id", "receipt_id"})
                for row in result_summary),
        "actual_artifact_stamp": actual_stamp,
        "write_epoch": epoch,
        "wrapup": {
            "verdict": wrapup.get("verdict"),
            "artifact_stamp_matches": wrapup_checks["stamp"],
            "write_epoch": wrapup.get("write_epoch"),
            "no_defects": wrapup_checks["no_defects"],
        },
        "items": item_summary,
        "results": result_summary,
        "problems": sorted(set(problems)),
    }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _run_local_verifier(workspace: str, before_stamp: str) -> dict[str, Any]:
    """Execute the exact verifier through the same bwrap/capability-0 boundary as SYS."""
    from system.guide_tools import run_workspace_command
    from system.rule.milestone import workspace_artifact_stamp

    ok, rc, stdout, stderr, reason = asyncio.run(
        run_workspace_command(workspace, EXACT_VERIFIER, timeout=60)
    )
    after_stamp = workspace_artifact_stamp(SimpleNamespace(workspace=workspace))
    stdout_lines = [line.strip() for line in str(stdout or "").splitlines()]
    return {
        "command": EXACT_VERIFIER,
        "ok": bool(ok),
        "rc": rc,
        "stdout_pass": "PASS" in stdout_lines,
        "stdout_sha256": hashlib.sha256(
            str(stdout or "").encode("utf-8", "replace")
        ).hexdigest(),
        "stdout_tail": str(stdout or "")[-500:],
        "stderr_tail": str(stderr or "")[-500:],
        "reason": str(reason or ""),
        "artifact_before": before_stamp,
        "artifact_after": after_stamp,
        "artifact_stable": bool(before_stamp) and before_stamp == after_stamp,
    }


def collect_runtime_evidence(
    client: LocalHttp,
    pid: str,
    request_id: int,
    *,
    run_verifier: bool,
) -> dict[str, Any]:
    """Collect safe DB/registry/filesystem evidence; never return a session token."""
    from sns.models import (
        GuideMessage,
        InterjectSignal,
        Membership,
        Project,
        StopSignal,
    )
    from system.rule.milestone import workspace_artifact_stamp

    project = (
        Project.objects.filter(pid=pid)
        .select_related("leader", "owner")
        .first()
    )
    if project is None:
        raise ProbeError(f"{pid} Project DB row가 없습니다.")
    root = GuideMessage.objects.filter(
        msg_id=request_id,
        channel_id=project.id,
        sender_id=0,
        msg_type="request",
    ).first()
    if root is None:
        raise ProbeError(f"request root {request_id} DB row가 없습니다.")
    payload = dict(root.payload or {})
    all_requests = list(
        GuideMessage.objects.filter(
            channel_id=project.id, sender_id=0, msg_type="request"
        ).only("msg_id", "payload")
    )
    incomplete = sum(
        1
        for row in all_requests
        if not (row.payload or {}).get("done_ts")
        and not (row.payload or {}).get("stopped")
        and not (row.payload or {}).get("dismissed")
    )
    correction_rows = GuideMessage.objects.filter(
        channel_id=project.id,
        sender_id=0,
        msg_type="plain",
        body=CORRECTION_TEXT,
        payload__interject=True,
    ).count()
    registry_path, entry = _runtime_registry_entry(project.id)
    workspace = str(entry.get("workspace") or "")
    if not workspace or not os.path.isdir(workspace):
        raise ProbeError("projects.json root의 workspace가 없거나 디렉터리가 아닙니다.")
    workspace = os.path.realpath(workspace)

    file_response = client.get(f"/projects/{pid}/files/")
    http_files = sorted(
        str(row.get("path") or "")
        for row in (file_response.get("files") or [])
        if isinstance(row, dict) and row.get("path")
    )
    files, hashes, truncated = _workspace_file_manifest(workspace)
    before_stamp = workspace_artifact_stamp(SimpleNamespace(workspace=workspace))
    receipt_evidence = _e2e_receipt_evidence(entry, workspace)
    verifier_evidence: dict[str, Any] = {
        "command": EXACT_VERIFIER,
        "executed": False,
        "ok": False,
        "rc": None,
        "stdout_pass": False,
        "artifact_stable": False,
        "reason": "terminal execute mode에서만 독립 실행",
    }
    if run_verifier:
        if files != ["state_machine.js", "test_state_machine.js"]:
            verifier_evidence["reason"] = "파일 집합이 정확히 두 개가 아니어서 실행하지 않음"
        elif set(hashes) != {"state_machine.js", "test_state_machine.js"}:
            verifier_evidence["reason"] = "두 검증 대상이 regular file이 아니어서 실행하지 않음"
        else:
            verifier_evidence = _run_local_verifier(workspace, before_stamp)
            verifier_evidence["executed"] = True
            files_after, hashes_after, truncated_after = _workspace_file_manifest(workspace)
            verifier_evidence["files_after"] = files_after
            verifier_evidence["hashes_stable"] = hashes_after == hashes
            verifier_evidence["manifest_truncated_after"] = truncated_after

    pending_info = entry.get("pending_info") or {}
    interject_retry = entry.get("interject_retry") or {}
    last_task = entry.get("last_task") or {}
    return {
        "db": {
            "channel_id": project.id,
            "project_pid": project.pid,
            "visibility": project.visibility,
            "leader_bot_id": (
                str(project.leader.bot_id) if project.leader is not None else None
            ),
            "owner_handle": (
                str(project.owner.handle) if project.owner is not None else None
            ),
            "hj_active_memberships": Membership.objects.filter(
                project=project,
                person__handle="hj",
                status="active",
            ).count(),
            "request_id": root.msg_id,
            "request_body_exact": root.body == REQUEST_TEXT,
            "request_root_terminal": bool(payload.get("picked"))
            and bool(payload.get("done_ts"))
            and not bool(payload.get("stopped"))
            and not bool(payload.get("dismissed")),
            "request_payload": {
                "picked": bool(payload.get("picked")),
                "done_ts": payload.get("done_ts"),
                "stopped": bool(payload.get("stopped")),
                "dismissed": bool(payload.get("dismissed")),
                "activity_n": _safe_int(payload.get("activity_n"), 0),
            },
            "root_responses": GuideMessage.objects.filter(
                channel_id=project.id,
                msg_type="response",
                reply_to=request_id,
            ).count(),
            "correction_rows": correction_rows,
            "interject_signals": InterjectSignal.objects.filter(
                channel_id=project.id
            ).count(),
            "stop_signals": StopSignal.objects.filter(channel_id=project.id).count(),
            "incomplete_requests": incomplete,
        },
        "projects_json": {
            "path": str(registry_path),
            "project_id": entry.get("id"),
            "channel": entry.get("channel"),
            "origin_msg": str(entry.get("origin_msg") or ""),
            "origin_matches_request": str(entry.get("origin_msg") or "")
            == str(request_id),
            "workspace": workspace,
            "open_task_is_none": entry.get("open_task") is None,
            "last_task_id": last_task.get("task_id"),
            "root_closed": entry.get("open_task") is None
            and bool(last_task.get("task_id"))
            and str(entry.get("origin_msg") or "") == str(request_id),
            "pending_info_count": sum(
                len(value) if isinstance(value, list) else 1
                for value in pending_info.values()
            )
            if isinstance(pending_info, dict)
            else -1,
            "interject_retry_count": len(interject_retry)
            if isinstance(interject_retry, dict)
            else -1,
            "interject_drained": pending_info == {} and interject_retry == {},
        },
        "workspace": {
            "path": workspace,
            "files": files,
            "http_files": http_files,
            "sha256": hashes,
            "manifest_truncated": truncated,
            "artifact_stamp": before_stamp,
        },
        "local_verifier": verifier_evidence,
        "e2e_receipts": receipt_evidence,
    }


def create_channel(client: LocalHttp) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    response = client.post(
        "/channels/",
        {
            "name": f"E2E 상태머신 {stamp}",
            "leader_bot_id": LEADER_BOT_ID,
            "visibility": "private",
        },
    )
    pid = str(response.get("pid") or "").strip().upper()
    if not PID_RE.fullmatch(pid):
        raise ProbeError("채널 생성 응답에 유효한 pid가 없습니다.")
    if response.get("visibility") != "private":
        raise ProbeError(f"새 채널 {pid}가 private이 아닙니다.")
    return pid


def ensure_existing_channel_idle(client: LocalHttp, pid: str) -> None:
    client.get(f"/projects/{pid}/")
    messages = client.get(f"/projects/{pid}/messages/", {"limit": 1000})
    live = messages.get("live_status") or {}
    if live.get("state") == "working" or int(messages.get("pending_count") or 0) > 0:
        raise ProbeError(f"{pid}에 이미 working/pending 요청이 있어 새 요청을 넣지 않습니다.")


def submit_request(client: LocalHttp, pid: str) -> int:
    response = client.post(
        f"/projects/{pid}/request/",
        {"body": REQUEST_TEXT, "kind": "W", "to_id": LEADER_BOT_ID},
    )
    try:
        request_id = int(response.get("msg_id"))
    except (TypeError, ValueError) as exc:
        raise ProbeError("요청 생성 응답에 msg_id가 없습니다.") from exc
    if not response.get("queued"):
        raise ProbeError("요청이 queued 상태로 등록되지 않았습니다.")
    return request_id


def resolve_read_only_request(client: LocalHttp, pid: str) -> int:
    client.get(f"/projects/{pid}/")
    messages = client.get(f"/projects/{pid}/messages/", {"limit": 1000})
    request_id = latest_request_id(messages)
    if request_id is None:
        raise ProbeError(f"{pid}에 관측할 Task 요청이 없습니다.")
    return request_id


def fetch_observation(
    client: LocalHttp, pid: str, request_id: int, trace_id: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    messages = client.get(f"/projects/{pid}/messages/", {"limit": 1000})
    status = client.get("/ms_status/", {"pid": pid})
    activity = client.get(
        f"/projects/{pid}/activity_log/",
        {"request_id": request_id, "offset": 0},
    )
    trace = client.get(f"/monitor/trace/{trace_id}/")
    return messages, status, activity, trace


def should_inject(trace: dict[str, Any], state: ProbeState) -> bool:
    if not state.execute or state.correction_attempts:
        return False
    trigger = milestone_r2_trigger(trace.get("events") or [])
    if trigger is None:
        return False
    state.correction_trigger = {
        "event": trigger.get("event"),
        "ts": trigger.get("ts"),
        "seq": trigger.get("seq"),
    }
    return True


def inject_correction(client: LocalHttp, state: ProbeState) -> dict[str, Any]:
    """At-most-once POST: never retry an ambiguous network response."""
    state.correction_attempts += 1
    response = client.post(
        f"/projects/{state.pid}/interject/",
        {"body": CORRECTION_TEXT},
    )
    if not response.get("ok"):
        raise ProbeError("교정 개입 API가 ok를 반환하지 않았습니다.")
    state.correction_posts += 1
    return {
        "schema": 1,
        "kind": "correction_injected",
        "at": utc_now(),
        "pid": state.pid,
        "request_id": state.request_id,
        "trace_id": state.trace_id,
        "trigger": state.correction_trigger,
        "response": {"ok": bool(response.get("ok")), "key": response.get("key")},
        "text_sha256": hashlib.sha256(CORRECTION_TEXT.encode("utf-8")).hexdigest(),
    }


def is_terminal(state: ProbeState) -> bool:
    req = request_message(state.latest_messages, state.request_id) or {}
    names = {str(x.get("event") or "") for x in state.trace_events}
    request_state = req.get("request_state")
    if request_state == "done":
        return "flow_done" in names
    if request_state == "stopped":
        # fail-closed 흐름은 의도적으로 flow_done을 쓰지 않는다. 거짓 완료를 막은 정상 실패
        # 종결도 probe가 3시간 timeout까지 기다리지 않고 즉시 실패 보고서로 봉인해야 한다.
        return bool(names.intersection(STOPPED_TRACE_EVENTS))
    return False


def artifact_paths(pid: str, request_id: int) -> tuple[Path, Path]:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{stamp}-{pid}-r{request_id}"
    return OUTPUT_DIR / f"{stem}.jsonl", OUTPUT_DIR / f"{stem}.report.json"


def monitor(
    client: LocalHttp,
    args: argparse.Namespace,
    pid: str,
    request_id: int,
) -> tuple[dict[str, Any], Path | None, Path | None]:
    trace_id = "t-" + str(request_id)[-10:]
    observation_path: Path | None = None
    report_path: Path | None = None
    if args.execute:
        observation_path, report_path = artifact_paths(pid, request_id)
    sink = ObservationSink(observation_path)
    state = ProbeState(
        execute=args.execute, pid=pid, request_id=request_id, trace_id=trace_id
    )
    outcome = "timeout"
    settled = 0
    try:
        sink.emit(
            {
                "schema": 1,
                "kind": "probe_started",
                "at": utc_now(),
                "mode": "execute" if args.execute else "read-only",
                "pid": pid,
                "request_id": request_id,
                "trace_id": trace_id,
                "poll_seconds": args.poll_seconds,
                "max_seconds": args.max_seconds,
            }
        )
        while True:
            observation = fetch_observation(client, pid, request_id, trace_id)
            row = state.observe(*observation)
            sink.emit(row)
            trace = observation[3]
            if should_inject(trace, state):
                try:
                    sink.emit(inject_correction(client, state))
                except ProbeError as exc:
                    state.violate("correction_post_failed", str(exc))
                    sink.emit(
                        {
                            "schema": 1,
                            "kind": "correction_failed",
                            "at": utc_now(),
                            "pid": pid,
                            "request_id": request_id,
                            "error": str(exc),
                        }
                    )
            if is_terminal(state):
                settled += 1
                if settled >= args.settle_polls:
                    outcome = "terminal"
                    break
            else:
                settled = 0
            if args.once:
                outcome = "snapshot"
                break
            if time.monotonic() - state.started_monotonic >= args.max_seconds:
                outcome = "timeout"
                break
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        outcome = "interrupted"
    except ProbeError as exc:
        outcome = "probe_error"
        state.violate("probe_error", str(exc))
        sink.emit(
            {
                "schema": 1,
                "kind": "probe_error",
                "at": utc_now(),
                "pid": pid,
                "request_id": request_id,
                "error": str(exc),
            }
        )
    finally:
        try:
            state.runtime_evidence = collect_runtime_evidence(
                client,
                pid,
                request_id,
                run_verifier=bool(args.execute and outcome == "terminal"),
            )
        except ProbeError as exc:
            state.runtime_evidence_error = str(exc)
            state.violate("runtime_evidence_error", str(exc))
        except Exception as exc:
            state.runtime_evidence_error = (
                f"runtime evidence 수집 실패: {type(exc).__name__}"
            )
            state.violate(
                "runtime_evidence_error", state.runtime_evidence_error
            )
        report = state.report(outcome)
        report["artifacts"] = {
            "observations": str(observation_path) if observation_path else None,
            "report": str(report_path) if report_path else None,
        }
        sink.emit({"schema": 1, "kind": "probe_finished", "report": report})
        sink.close()
        if report_path is not None:
            write_report(report_path, report)
    return report, observation_path, report_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.execute and not args.pid:
        print(json.dumps(dry_run_payload(args), ensure_ascii=False, indent=2))
        return 0

    token = latest_hj_token()
    client = LocalHttp(token)
    if args.execute:
        if args.pid:
            ensure_existing_channel_idle(client, args.pid)
            pid = args.pid
        else:
            pid = create_channel(client)
        request_id = submit_request(client, pid)
    else:
        pid = args.pid
        request_id = resolve_read_only_request(client, pid)

    report, observation_path, report_path = monitor(client, args, pid, request_id)
    summary = {
        "passed": report["passed"],
        "outcome": report["outcome"],
        "pid": pid,
        "request_id": request_id,
        "trace_id": report["trace_id"],
        "failed_assertions": report["failed_assertions"],
        "observations": str(observation_path) if observation_path else None,
        "report": str(report_path) if report_path else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if report["outcome"] == "interrupted":
        return 130
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProbeError as exc:
        print(
            json.dumps(
                {"passed": False, "outcome": "preflight_error", "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
