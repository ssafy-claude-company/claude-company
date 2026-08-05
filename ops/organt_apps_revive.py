#!/usr/bin/env python3
"""앱 풀 소생 — 죽은 배포 앱을 장부(registry.json)대로 되살린다 (2026-08-05, 현준-1).

[왜] 앱은 transient systemd 유닛(organt-app-*)으로 돈다 — 크래시는 Restart=on-failure가
살리지만 **재부팅·유닛 소멸은 아무도 안 살린다**. 실측: 풀 7앱 중 5앱이 죽은 pid로 방치,
그 404가 U-504 주기를 28시간 인질로 잡았고(B7), 옛 보고 링크들이 전부 죽은 문을 가리켰다.
'실행 정본 = 앱 풀'(07-20 감사)이려면 풀은 스스로 일어서야 한다.

[어떻게] 장부의 비정적 앱마다: 유닛이 살아 있으면 그대로 두고, 죽었으면 러너의
_spawn_app과 같은 방식(systemd-run + Restart=on-failure)으로 재기동 후 장부 pid를 고친다.
포트·디렉터리·cmd는 장부가 정본. systemd 타이머(5분)가 부른다 — 재부팅 직후에도 곧 선다.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

APPS = Path(os.environ.get("ORGANT_APPS_DIR") or "/root/murmur-stack/ops/var/organt_apps")
REG = APPS / "registry.json"


def unit_of(name: str) -> str:
    return "organt-app-" + re.sub(r"[^a-zA-Z0-9-]", "-", name)[:50]


def unit_alive(unit: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", "--quiet", unit])
    return r.returncode == 0


def main_pid(unit: str) -> int:
    """유닛이 실제로 돌리고 있는 pid(없으면 0)."""
    out = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", unit],
                         capture_output=True, text=True).stdout.strip()
    try:
        return int(out or 0)
    except ValueError:
        return 0


def spawn(name: str, entry: dict) -> int:
    appdir = Path(entry.get("dir") or (APPS / name))
    port = int(entry["port"])
    cmd = entry.get("cmd") or "npm start"
    unit = unit_of(name)
    subprocess.run(["systemctl", "reset-failed", unit], capture_output=True)
    subprocess.run(["systemctl", "stop", unit], capture_output=True)
    r = subprocess.run(["systemd-run", "--unit", unit, "--collect",
                        "-p", "Restart=on-failure", "-p", "RestartSec=2",
                        "-p", f"WorkingDirectory={appdir}",
                        "-p", f"Environment=PORT={port}", "-p", "Environment=NODE_ENV=production",
                        "-p", f"StandardOutput=append:{appdir}/app.log",
                        "-p", f"StandardError=append:{appdir}/app.log",
                        "/bin/sh", "-c", cmd], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ✗ {name}: systemd-run 실패 — {r.stderr.strip()[:120]}")
        return 0
    for _ in range(20):
        out = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", unit],
                             capture_output=True, text=True).stdout.strip()
        if out and out != "0":
            return int(out)
        time.sleep(0.2)
    return 0


def main():
    if not REG.exists():
        return
    reg = json.loads(REG.read_text(encoding="utf-8")) or {}
    changed = False
    for name, entry in reg.items():
        if not isinstance(entry, dict) or entry.get("static"):
            continue
        appdir = Path(entry.get("dir") or (APPS / name))
        if not appdir.is_dir():
            print(f"  – {name}: 디렉터리 없음({appdir}) — 건너뜀")
            continue
        if unit_alive(unit_of(name)):
            # [장부의 pid가 사실에서 떠내려간다(2026-08-05 실측)] Restart=on-failure로 유닛이 스스로
            # 다시 뜨면 MainPID가 바뀌는데 장부는 옛 pid를 들고 있는다. pool_app_url은 그 pid의
            # /proc 존재로 '앱이 살아 있나'를 판정하므로, **200을 답하는 앱이 죽은 것으로 읽혀**
            # 봇도 보고도 앱 주소를 못 받았다(실측 4앱 전부 스테일 — U-505·U-504 포함).
            _cur = main_pid(unit_of(name))
            if _cur and str(entry.get("pid")) != str(_cur):
                print(f"  ↻ {name}: 장부 pid {entry.get('pid')} → 실제 {_cur}")
                entry["pid"] = _cur
                changed = True
            continue
        pid = spawn(name, entry)
        if pid:
            entry["pid"] = pid
            entry["revived_ts"] = time.time()
            changed = True
            print(f"  ✓ {name}: 재기동 pid={pid} port={entry['port']}")
    if changed:
        tmp = REG.with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, REG)


if __name__ == "__main__":
    main()
