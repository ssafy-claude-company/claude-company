#!/usr/bin/env python3
"""세션 귀속 기록 — 어느 세션이 무슨 명령을 돌렸는가.

[세션 귀속(2026-07-31, 현준-4)] sudo 로그는 행위자를 전부 'dojin'으로 남긴다. TTY 칸도 비어
있는데, Claude Code의 명령이 제어 터미널 없이 돌기 때문이다. 그래서 서버에서 벌어진 일을
세션 단위로 되짚을 방법이 없었다 — 봇 쪽 감사를 살려도 사람 쪽은 여전히 깜깜했다.

잡을 지점은 명령이 실행되기 직전이다. 세션 이름은 자기 조상 프로세스(claude --name)가 안다.
기록은 append-only(chattr +a)라 세션 자신도 지난 줄을 고치지 못한다.

무슨 일이 있어도 0으로 끝난다 — 기록은 관측이지 관문이 아니다. 여기서 막히면 사람의 작업이
멈춘다.
"""
import json
import os
import re
import sys

LOG = "/var/log/claude-session-audit.jsonl"
_NAME = re.compile(r"--name\s+(.+?)(?:\s+--|\s*$)")


def _session_name():
    """조상 프로세스에서 '[dojin-mini] 현준-4 : …' 같은 세션 이름을 찾는다."""
    pid = os.getppid()
    for _ in range(12):                      # 조상 사슬을 얕게만 거슬러 오른다
        if pid <= 1:
            break
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fp:
                args = fp.read().decode("utf-8", "replace").split("\0")
            line = " ".join(a for a in args if a)
            if "claude" in line and "--name" in line:
                m = _NAME.search(line)
                if m:
                    return m.group(1).strip().strip("'\"")
            with open(f"/proc/{pid}/stat", "rb") as fp:
                pid = int(fp.read().decode("utf-8", "replace").rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return None


def main():
    try:
        raw = sys.stdin.read()
    except Exception:
        return
    try:
        ev = json.loads(raw) if raw.strip() else {}
    except ValueError:
        ev = {}
    ti = ev.get("tool_input") or {}
    entry = {
        "ts": __import__("time").time(),
        "session": _session_name(),
        "session_id": ev.get("session_id"),
        "tool": ev.get("tool_name"),
        "cwd": ev.get("cwd"),
        # 명령 원문은 그대로 남긴다 — 무엇을 했는지가 기록의 전부다. 다만 한 줄이
        # 로그를 삼키지 않도록 길이만 자른다.
        "command": str(ti.get("command") or ti.get("file_path") or "")[:2000],
    }
    try:
        with open(LOG, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
