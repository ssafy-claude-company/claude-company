"""Organt가 쓰는 Guide 도구셋 (P2P Communication + 다중 Task + 팀 배정 모델).

회사식 인력 구조: **채용 풀(전체 로스터) → 프로젝트 팀(규모 산정해 배정) → Task 팀(필요 인원)**.
- 깨어난 Organt는 `request`로 *현재 Task 팀의 동료*에게 요청한다(Info=질문/Work=작업).
- 인원이 부족하면 `recruit`로 풀에서 현재 Task에 합류시킨다("더 필요하면 더 가져온다").
SYS가 대상 동료를 중첩 베턴으로 깨워(flow.wake) 응답을 돌려준다 → 항상 1명만 활성(단일흐름).

리더(첫 Organt)는 추가로:
- create_project(name, team): 규모를 산정해 프로젝트 팀 배정 + 전용 채널 생성
- create_task(purpose, goal, members): Task에 필요한 인원 배정 + 상태블록/Thread 생성(반복 가능)
- complete_task(result): 현재 Task를 완료로 마감
대화는 '현재 Task' 스레드에서. 보고는 별도 툴이 아니라 반환값(=Response)이 origin까지 unwind.
"""
import asyncio
import os
import secrets
import shutil
import signal
import subprocess
import tempfile
import time

import anyio

from claude_agent_sdk import create_sdk_mcp_server, tool

from ._util import _dbg, _looks_transient, _ok, _speech_clip  # noqa: F401  [_speech_clip: sys_core + PJT/tests(test_sys)가 파사드에서 직접 import — 유지, _looks_transient: system/tests(test_misc)가 파사드에서 import — 유지]
from ._util import clip as _clip
from .rule.evidence import (
    command_matches_spec, looks_like_verification_command as _evidence_command,
    direct_verifier_command, normalize_verifier_command,
    verifier_command_hash, verifier_spec_hash,
)

from .tool_names import ORIGIN, FLOW_TOOLS, COORD_TOOLS, LEADER_TOOLS  # noqa: F401  [FLOW/COORD/LEADER_TOOLS: guide/discord_main·PJT(organt_discord·scripts·tests) 소비 — 유지, ORIGIN: 소비 불확실 — 호환 유지]

# run 툴 안전 차단: 파괴/탈출/저장소·시스템 경로/네트워크 외 명령은 막는다(npm·node·curl·python은 허용).
_RUN_DENY = ("rm -rf", "rm -r ", "sudo", "shutdown", "reboot", "mkfs", "dd if=", ":(){",
             "git ", "/home/user/pjt", "/etc/", "/usr/", "/root", "> /", "chmod ", "chown ",
             "pkill", "kill -9 1 ", "wget ", "ssh ", "scp ", "npm publish", "history",
             # 비밀 읽기 차단(심층방어) — 권한강등이 1차 방어, 이건 비루트 폴백·명시 차단.
             ".guide_env", "/environ", "/tmp/claude-0",
             # [보안 감사(2026-07-18)] SSRF·비밀 경로 명시 차단(강등 실패·`..`/심볼릭 우회 방어).
             # 클라우드 메타데이터(자격증명 유출) — AWS/GCP/Azure/Alibaba 공통 링크로컬.
             "169.254.169.254", "metadata.google", "100.100.100.200",
             # SSH 키·자격 파일(워크스페이스 마스킹 뒤 `/../.ssh` 우회까지 차단).
             ".ssh", "id_rsa", "id_ed25519", "authorized_keys", "/proc/",
             # 내부 API 직격(guide 토큰 탈취 시 금고 자격증명 복호 엔드포인트) — 러너 전용 경로.
             "/api/guide", "/api/atelier", "deploy_creds",
             # [B-08 — Task Dossier 쓰기 보호 2중] permissions 훅은 Write/Edit만 잡는다 —
             # bash `cp/mv/sed -i/rm` 우회(_RUN_AUTHOR는 heredoc·cat>·tee만 차단)를 여기서 막는다.
             # 슬래시 포함형만 deny-tuple에(x.collaboration.js 오탐 방지) — 무슬래시는 아래 전용
             # regex(_COLLAB_RE — 단어 경계)가 처방 메시지와 함께 잡는다.
             ".collab/")

# [B-08] run 명령의 .collab 참조 판정 — '.collab' 뒤에 영숫자가 이어지면(.collaboration 등) 오탐이라
# 제외하는 단어 경계 regex. deny-tuple의 generic 메시지 대신 '어디로 기록하나' 처방을 붙이기 위한 전용 검사.
import re as _re
_COLLAB_RE = _re.compile(r"\.collab(?![0-9a-z])")
# [C9 보강 — 순수 additive] 위 '/home/user/pjt'는 표준 설치 경로 하드코딩 — 임의 경로에 설치하면 두뇌
# 소스(ORGANT_PJT)가 셸 차단에서 빠진다. 실제 설치 경로를 env에서 파생해 추가한다(비면 제외, 기존 항목 전부 유지).
_ORGANT_PJT_DENY = os.environ.get("ORGANT_PJT", "").rstrip("/").lower()
if _ORGANT_PJT_DENY:
    _RUN_DENY += (_ORGANT_PJT_DENY,)
# run으로 '파일 작성'(heredoc·cat>·tee)을 막는다 — 산출물 작성/수정은 Write/Edit로 해야 권한·협의
# 게이트(협의 중 선구현 금지)가 적용되고 '누가 무엇을 만들었나'가 기록된다. run은 실행·빌드·검증 전용.
# (이 백도어로 리더가 위임 없이 전부 혼자 작성해 독점하거나, 협의 단계 동료가 선구현하는 걸 차단.)
_RUN_AUTHOR = ("<<", "cat >", "cat>", "tee ", "tee\t")

# [run 셸 비밀 차단 — 봇 키 유출 방지] run은 작업공간 검증용 셸이지만 부모(러너) 환경을 그대로 물려받아,
# RENDER_KEY·GH_PAT 같은 배포 자격증명이 env에 있으면 `echo $RENDER_KEY`/`env`/`curl -X DELETE`로 읽혀
# 악용될 수 있다(deny-list는 rm/git/sudo만 막지 env 노출은 못 막음). deploy 도구는 *인프로세스*로 키를 쓰므로
# (os.environ 직접 읽음·서브프로세스 아님) 배포 능력은 그대로 두고, run 서브프로세스 env에서만 비밀을 지운다
# → 봇은 배포는 할 수 있어도(deploy 도구) 키를 읽을 수는 없다. PATH 등 빌드에 필요한 일반 env는 보존.
_SECRET_ENV_EXACT = {
    "RENDER_KEY", "RENDER_API_KEY", "RENDER_OWNER", "GH_PAT", "GH_USER",
    "GITHUB_TOKEN", "GITHUB_PAT", "ORGANT_GUIDE_TOKEN", "ORGANT_GUIDE_TOKENS",
}
# [규칙 구멍 봉합(2026-07-30, 현준-4 실측)] 종전 목록은 `_API_KEY`처럼 좁아서 이름에 KEY가 있어도
# 새는 것이 있었다: ORGANT_VAULT_KEY(전 테넌트 시크릿 복호 키)·DATABASE_URL(DB 자격증명)·OPENAI_KEY가
# 전부 통과했다. 지금 러너 env엔 그 값들이 없어 무해하지만, 누가 하나만 넣으면 그대로 봇 셸에 실린다
# — '무해한 이유'가 목록이 아니라 우연이면 안 된다. KEY로 끝나거나 KEY를 품은 이름을 통째로 막고,
# 접속 문자열(URL·DSN·URI)도 자격증명을 품는 형태라 함께 막는다.
# 일반 빌드 env를 잡지 않도록 화이트리스트를 둔다 — 이름에 KEY가 들어가도 비밀이 아닌 것들.
_SECRET_ENV_SUBSTR = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "APIKEY", "KEY",
                      "CREDENTIAL", "DATABASE_URL", "_DSN", "_URI")
# KEY를 품지만 비밀이 아닌 이름(빌드·터미널 환경). 여기 없는 KEY* 는 전부 비밀로 취급한다.
_SECRET_ENV_ALLOW = {
    "KEYBOARD", "TERM_KEYS", "SSH_AUTH_SOCK",      # 환경·터미널
    "npm_config_keyfile".upper(),                  # npm 설정 이름(값은 경로)
}


PW_CACHE = "/root/.cache/ms-playwright"


def browser_build_gap(workspace, cache=None):
    """[봇이 쓰는 playwright가 기대하는 브라우저 빌드가 공유 캐시에 있는가(2026-08-02, U-478 실측)]

    playwright는 판올림마다 브라우저 빌드 번호가 바뀐다. 공유 캐시에 chromium·firefox·webkit이
    '있다'는 사실만으로 거절하면, 작업공간의 playwright가 **다른 번호**를 기대할 때 그 거절은 거짓이
    된다. 실측: 작업공간 node playwright 1.62.1은 firefox-1538·webkit-2336을 찾는데 캐시에는
    1532·2311만 있었다. 봇은 "이미 있음" 거절을 믿고 /tmp에 apt 상태 디렉터리를 만들어 시스템
    패키지를 손으로 풀었고(exec 163회 중 apt·ldd 15회), 결국 백로그가 세 번 차단돼 판이 파킹됐다.

    작업공간의 playwright-core가 선언한 브라우저 개정판을 읽어 캐시에 없는 것만 돌려준다.
    읽을 수 없으면 빈 목록 — 모르면 거절하지 않는다(거짓 거절이 이 사고의 원인이었다).
    """
    import glob as _glob
    import json as _json
    cache = str(cache or PW_CACHE)   # 기본값은 호출 시점에 읽는다(테스트·재배치 가능)
    ws = str(workspace or "").strip()
    if not ws:
        return []
    reg = os.path.join(ws, "node_modules", "playwright-core", "browsers.json")
    try:
        with open(reg, encoding="utf-8") as fp:
            data = _json.load(fp)
    except Exception:
        return []
    want, missing = {}, []
    for b in (data.get("browsers") or []):
        name, rev = str(b.get("name") or ""), str(b.get("revision") or "")
        if name in ("chromium", "firefox", "webkit") and rev:
            want[name] = rev
    for name, rev in want.items():
        if not _glob.glob(os.path.join(cache, f"{name}-{rev}")):
            missing.append(f"{name}-{rev}")
    return missing


def run_repeat_note(flow, cmd, stamp):
    """[같은 검증을 고친 것 없이 되돌린다(2026-08-02, 감사로그 실측)] 최근 24시간 run 203회 중 **51%가
    같은 명령의 반복**이었다(`npm run verify:milestone1` 34회, `npm run test:all` 18회). 고치고 다시
    재는 것은 정상이지만, **작업공간이 한 글자도 안 바뀐 채** 같은 명령을 또 돌리는 것은 같은 결과를
    다시 사는 일이다. 막지는 않는다 — 라이브 URL·배포처럼 바깥 상태가 바뀌어 결과가 달라지는 검증이
    있기 때문이다. 세 번째부터 사실만 붙여 준다(무엇을 고칠지 정하고 다시 오라는 신호).
    """
    if not stamp:
        return ""
    book = getattr(flow, "_run_repeat", None)
    if book is None:
        book = flow._run_repeat = {}
    key = (str(cmd or "").strip()[:200], str(stamp))
    n = book[key] = int(book.get(key, 0)) + 1
    if n < 3:
        return ""
    try:
        if flow.log:
            flow.log("run_repeat_unchanged", n=n, cmd=str(cmd or "")[:60])
    except Exception:
        pass
    return (f"\n\n⚠ 이 명령을 **작업공간이 그대로인 채 {n}번째** 실행했습니다 — 바깥 상태(라이브 URL·배포)가 "
            f"바뀌지 않았다면 결과도 같습니다. 무엇을 고칠지 정해 파일을 바꾼 뒤 다시 재세요. "
            f"환경이 막고 있어 못 고치는 것이면 block_backlog의 사유로 그 사실을 적으세요.")


def prefer_shared_browsers(command, workspace, cache=None):
    """[같은 브라우저를 판마다 한 벌씩 더 올린다(2026-08-02, 실측)] 구판 작업공간은 `.qa-browsers`(646MB)와
    `.qa-deps.broken`(141MB)을 자기 명령에 경로째 박아 쓰고 있었다 — 공유 캐시(2.4GB)에 **같은 개정판이
    이미 다 있는데도**. 4코어·14G 머신에 같은 chromium이 두 벌 올라가 페이지 캐시가 갈리고, 브라우저
    검증 하나가 코어 절반을 먹는 상황에서 두 판이 서로를 느리게 만들었다. 어제 이름만 `.broken`으로
    바꿔둔 디렉터리조차 명령에 박혀 있어 그대로 쓰였다 — 이름 바꾸기는 불완전한 조치였다.

    공유 캐시에 필요한 개정판이 **빠짐없이** 있을 때만 판별 경로를 공유 캐시로 바꾼다. 하나라도 없으면
    손대지 않는다 — 없는 것을 있다고 우기면 봇이 또 몇 시간을 태운다(browser_build_gap 참조).
    """
    import re as _re
    cmd = str(command or "")
    ws = str(workspace or "").strip()
    if not cmd or not ws or "PLAYWRIGHT_BROWSERS_PATH=" not in cmd:
        return cmd, ""
    shared = str(cache or PW_CACHE)
    if browser_build_gap(ws, shared):
        return cmd, ""
    changed = []

    def _sub(m):
        val = m.group(1).strip("\"'")
        if val.rstrip("/") == shared.rstrip("/"):
            return m.group(0)
        changed.append(val)
        return "PLAYWRIGHT_BROWSERS_PATH=" + shared

    out = _re.sub(r"PLAYWRIGHT_BROWSERS_PATH=([^\s;|&]+)", _sub, cmd)
    if not changed:
        return cmd, ""
    return out, ("[공유 브라우저로 바꿔 실행] " + ", ".join(changed[:2])
                 + " 대신 공유 캐시를 씁니다 — 같은 개정판이 이미 있고, 판별 사본은 같은 브라우저를 "
                   "한 벌 더 메모리에 올려 서로를 느리게 만듭니다.")


def _preinstalled_refusal(cmd, workspace="") -> str:
    """이미 갖춰진 것을 다시 설치하려는 명령이면 거절 사유 + 바로 쓰는 법. 아니면 빈 문자열.

    **없는 것을 '있다'고 거절하지 않는다** — browser_build_gap이 빈 자리를 찾으면 거절 대신
    작업공간에 받는 정확한 명령을 돌려준다(그 몇백 MB가 세 시간짜리 삽질보다 싸다)."""
    c = " ".join(str(cmd or "").split()).lower()
    if "playwright install" in c or "playwright/driver" in c:
        _gap = browser_build_gap(workspace)
        if _gap:
            return ("설치 필요(공유 캐시에 없음): 이 작업공간의 playwright가 기대하는 "
                    f"**{', '.join(_gap)}**이(가) 공유 캐시에 없습니다. 시스템 패키지를 손으로 풀지 "
                    "말고 작업공간에 받으세요:\n"
                    "`PLAYWRIGHT_BROWSERS_PATH=$PWD/.pw npx playwright install "
                    f"{' '.join(g.split('-')[0] for g in _gap)}`\n"
                    "그 뒤 검증 명령도 같은 `PLAYWRIGHT_BROWSERS_PATH=$PWD/.pw`로 실행하세요.")
        # [없는 엔진을 봇이 손으로 만들고 있었다(2026-08-02, U-478 세션 전문 실측)] 4브라우저 완수조건을
        # 받은 배포/인프라 봇이 webkit이 없자 /tmp에 apt 상태 디렉터리를 따로 만들어 시스템 패키지를 풀고
        # ldd로 의존성을 좇았다 — 한 턴 1시간 48분 중 상당 부분이 그 삽질이었다(exec 163회 중 apt·ldd 계열
        # 반복 15회). 셋을 정식 설치했으니(chromium·firefox·webkit) 무엇이 있는지 이름으로 알려준다.
        return ("실행 거부(이미 있음): 브라우저는 이 판의 공유 캐시에 이미 설치돼 있고 샌드박스가 "
                "`PLAYWRIGHT_BROWSERS_PATH`로 물려줍니다 — **chromium·firefox·webkit(Safari 엔진) 셋 다** "
                "바로 씁니다. 내려받지 말고 그대로 쓰세요"
                "(`python3 -c \"from playwright.sync_api import sync_playwright\"`로 확인). "
                "재설치는 작업공간에 수백 MB를 복사하고 십수 분을 태웁니다.")
    if ("pip install" in c or "pip3 install" in c) and "playwright" in c:
        return ("실행 거부(이미 있음): python playwright 패키지는 공유 venv에 이미 있습니다 — "
                "`python3 -c \"import playwright\"`가 바로 통합니다. --target으로 작업공간에 "
                "따로 깔지 마세요(용량·시간 낭비이고 검증 스크립트가 그 경로에 묶입니다).")
    return ""


def _is_secret_env(name: str) -> bool:
    u = (name or "").upper()
    if u in _SECRET_ENV_ALLOW:
        return False
    return u in _SECRET_ENV_EXACT or any(s in u for s in _SECRET_ENV_SUBSTR)


def _free_port() -> int:
    """지금 비어 있는 TCP 포트 하나 — 커널이 고르게 하고(0번 바인딩) 바로 돌려준다."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _scrubbed_run_env() -> dict:
    """봇 run 셸용 환경 — 부모 env 복사본에서 배포·인증 비밀만 제거(PATH·HOME 등 빌드 필수 env는 유지).

    [실행 격리(2026-07-30, 사용자: '충돌을 안정적으로 설계해서 병렬로')] 검증 실행이 판 시간의
    22%다(U-079 실측 5.3h/24.1h). 동시에 돌리려면 두 가지가 겹치지 않아야 한다: **포트**와
    **산출물 경로**. 실행마다 빈 포트와 고유 산출물 폴더를 환경으로 준다 —
    명령이 `$PORT`·`$ARTIFACT_DIR`를 쓰면 서로를 밟지 않고, 안 쓰면 종전 그대로 동작한다
    (라이브 명령을 깨지 않는 점진 도입). `$RUN_ID`는 영수증·증거 추적용.
    """
    # [sudo 잔재 제거 2026-07-31, 현준-4] 도우미를 sudo로 부르면 SUDO_COMMAND·SUDO_USER가
    # 봇 셸까지 따라 들어간다. 비밀은 아니지만 호출 전문(작업공간 경로·명령 원문)이
    # 그대로 실려, 봇이 자기가 어떻게 감싸여 실행되는지 읽는다. 격리는 안이 밖을
    # 모르게 하는 것이라 지운다.
    env = {k: v for k, v in os.environ.items()
           if not _is_secret_env(k) and not k.startswith("SUDO_")}
    try:
        rid = f"{int(time.time() * 1000) % 10**9:09d}"
        env.setdefault("RUN_ID", rid)
        env["PORT"] = str(_free_port())
        env["ARTIFACT_DIR"] = f"artifacts/run-{rid}"
    except Exception:
        pass
    return env


def _run_drop_creds():
    """[권한강등 — 비밀 파일 읽기 근본차단] env-scrub는 봇 *자기 env*만 지운다 — 러너가 root면 봇 셸도
    root라 `cat .guide_env`·`cat /proc/<러너>/environ`으로 비밀(RENDER_KEY·GH_PAT·AI_API_KEY·
    ORGANT_GUIDE_TOKEN)을 우회로 읽을 수 있다(라이브 확인됨). run 셸을 비특권 사용자로 떨어뜨리면
    600 root 파일·root 프로세스 environ을 *권한 자체로* 못 읽는다(node·npm 빌드는 HOME·캐시를
    작업공간으로 잡아주면 정상). 루트가 아니면(로컬 개발) None — 이미 비특권. 사용자명은
    ORGANT_RUN_USER로 교체 가능(기본 nobody). root에서 사용자/강등 도구를 못 찾으면 호출자가
    fail-closed한다.

    [판별 uid 지원(2026-07-30)] ORGANT_RUN_USER가 숫자면 계정 없이 그 uid/gid로 내린다.
    판이 늘어날 때마다 /etc/passwd에 계정을 만들지 않고 판별 경계를 세우려는 것 — setpriv·chown은
    숫자 uid를 그대로 받는다. 판별 uid 할당은 특권 도우미(organt-sandbox)가 정하고, 이 함수는
    받은 값을 해석만 한다."""
    try:
        if os.geteuid() != 0:
            return None
        want = (os.environ.get("ORGANT_RUN_USER") or "nobody").strip()
        if want.isdigit():
            n = int(want)
            if n <= 0:                      # 0(root)으로는 내려가지 않는다 — 강등의 의미가 사라진다
                return None
            return (n, n)
        import pwd
        r = pwd.getpwnam(want)
        return (r.pw_uid, r.pw_gid)
    except (KeyError, AttributeError, OSError, ValueError):
        return None


_NO_CHOWN = {"/", "/tmp", "/var", "/var/tmp", "/home", "/usr", "/etc", "/root", "/opt", "/srv"}


def _chown_tree(path, uid, gid):
    """작업공간을 강등 사용자 소유로 — 산출물·node_modules·빌드 출력 기록 가능하게. 실패는 무시(최선).
    공유/시스템 루트(/tmp 등)는 통째 chown 금지 — 격리된 흐름별 작업공간만 대상(오용·테스트 방어)."""
    try:
        rp = os.path.realpath(path)
        if rp in _NO_CHOWN or rp.count(os.sep) < 2:
            return                                          # 공유 루트 → 강등은 하되 chown은 건너뜀
        os.chown(rp, uid, gid)
        for root, dirs, files in os.walk(rp):
            for n in dirs + files:
                try:
                    os.chown(os.path.join(root, n), uid, gid, follow_symlinks=False)
                except OSError:
                    pass
    except OSError:
        pass


def _rewrite_workspace_paths(command, workspace, replacement=".") -> str:
    """0700 부모를 다시 순회하지 않도록 *정확한 workspace 경계*만 cwd 상대경로로 바꾼다.

    단순 ``str.replace('/x/ws', '.')``는 ``/x/ws2``까지 ``.2``로 오염시킨다. 앞은 셸 토큰
    경계이고 뒤는 경로 구분자/토큰 끝인 경우만 치환해 sibling 경로와 일반 문자열을 보존한다.
    """
    cmd, ws = str(command or ""), str(workspace or "").strip()
    roots = set()
    if ws:
        roots.add(ws.rstrip("/"))
        try:
            roots.add(os.path.realpath(ws).rstrip("/"))
        except OSError:
            pass
    for root in sorted((p for p in roots if p and p != "/"), key=len, reverse=True):
        pattern = _re.compile(
            rf"(?<![A-Za-z0-9_./-]){_re.escape(root)}"
            rf"(?=(?:/|[\s;&|<>()\"'`]|$))"
        )
        cmd = pattern.sub(str(replacement), cmd)
    return cmd


def _prepare_run_exec(workspace, command):
    """root가 cwd에 먼저 들어간 뒤 bubblewrap 격리+uid 강등하도록 공통 argv/env를 만든다.

    ``Popen(user=...)``은 child가 비특권이 된 뒤 0700 ``/root`` 아래 cwd로 chdir해 live 작업공간에
    진입하지 못한다. bwrap은 root인 채 host workspace를 ``/tmp/workspace``에 bind하고 host
    ``/root``를 빈 tmpfs로 가린 뒤, 최종 command만 setpriv로 host nobody uid/gid·capability 0으로
    내린다. 따라서 npm/getcwd/하위 cd는 정상인 반면 ``cd ..``나 절대 sibling 경로로 host 비밀에
    닿을 수 없다.
    """
    ws = str(workspace or "").strip()
    cmd = normalize_verifier_command(command)
    env = _scrubbed_run_env()
    bwrap = shutil.which("bwrap")
    # [비특권 러너도 격리한다(2026-07-30, 현준-4 보안 감사)] 종전엔 geteuid()!=0이면 격리를 통째로
    # 건너뛰고 `/bin/sh -c`로 직행했다. "root가 아니면 이미 안전하다"는 전제인데, 여러 봇이 한 계정을
    # 공유하는 배치에서는 틀리다 — 그 계정이 읽을 수 있는 모든 것(다른 채널 작업공간·world-readable
    # 설정)에 `cd ..` 한 번으로 닿는다. 러너를 비특권으로 내리려면 이 구멍을 먼저 막아야 하고,
    # 안 막으면 강등이 격리를 오히려 없앤다.
    # 비특권에서는 --unshare-user로 userns를 열어 같은 파일시스템 경계를 세운다. setpriv 강등은
    # 하지 않는다 — userns 안에서 host nobody uid가 매핑되지 않아 실패하고(실측), 이미 비특권이라
    # 강등할 특권도 없다. bwrap이 없는 환경(개발 머신)은 종전대로 통과시킨다.
    unpriv = os.geteuid() != 0
    # [판별 격리 도우미 경유(2026-07-30, 설계문서 7절 (나))] 비특권 러너는 봇 셸을 판별 uid로
    # 내릴 수 없다(bwrap 비setuid → userns uid 1개, setpriv엔 특권 필요 — 실측). 특권이 필요한
    # 그 한 조각만 도우미가 맡고, 여기서는 도우미를 부르는 argv로 바꿔 준다. 나머지 실행·타임아웃·
    # 로그 경로는 종전과 같다.
    # 기본 off — 도우미가 설치되고 플래그가 켜진 때만 경유한다(설치 전 무영향).
    if unpriv and os.environ.get("ORGANT_SANDBOX_HELPER", "0") not in ("0", "", "false", "False"):
        helper = os.environ.get("ORGANT_SANDBOX_HELPER_PATH") or "/usr/local/sbin/organt-sandbox"
        sudo = shutil.which("sudo")
        if not sudo or not os.path.exists(helper):
            return None, None, ("판별 격리 도우미를 찾을 수 없습니다 "
                                f"({helper}) — ops/sandbox/INSTALL.md 참조.")
        # 명령은 셸을 거치지 않고 argv 원소로 넘긴다(도우미가 샌드박스 *안*에서만 sh -c로 해석).
        # uid는 넘기지 않는다 — 도우미가 작업공간에서 도출한다(호출자가 고르면 교차 오염).
        return ([sudo, "-n", helper, "--workspace", ws, "--command", cmd],
                _scrubbed_run_env(), "")
    if unpriv and not bwrap:
        exec_cmd = _rewrite_workspace_paths(cmd, ws)
        return ["/bin/sh", "-c", exec_cmd], env, ""
    if not bwrap:
        return None, None, "root run 격리·권한강등 도구 bwrap을 찾을 수 없습니다."
    setpriv = ""
    uid = gid = None
    if not unpriv:
        drop = _run_drop_creds()
        if not drop:
            return None, None, "root run의 비특권 사용자(ORGANT_RUN_USER)를 찾을 수 없습니다."
        setpriv = shutil.which("setpriv")
        if not setpriv:
            return None, None, "root run 최종 권한강등 도구 setpriv를 찾을 수 없습니다."
        uid, gid = drop
    real_ws = os.path.realpath(ws)
    if not unpriv:
        _chown_tree(real_ws, uid, gid)
    sandbox_ws = "/tmp/workspace"
    exec_cmd = _rewrite_workspace_paths(cmd, ws, sandbox_ws)
    env["HOME"] = sandbox_ws
    env["npm_config_cache"] = "/tmp/npm-cache"
    # [egress 관문(2026-07-30, 현준-4)] 봇의 직접 외부 연결은 방화벽이 막는다(봇 uid 범위).
    # 나가는 길은 관문 하나이고 목적지는 허용 목록으로만 통과한다. 도구가 알아서 쓰도록 관례
    # 변수를 심는다 — 안 쓰는 도구는 방화벽에 막혀 실패하고, 그것이 기본 거부의 의도다.
    # 관문 주소가 없으면(설정 안 된 배치) 아무것도 심지 않는다 — 종전 동작 불변.
    _gw = os.environ.get("ORGANT_EGRESS_PROXY", "").strip()
    if _gw:
        for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            env[_k] = _gw
        env["npm_config_proxy"] = _gw
        env["npm_config_https_proxy"] = _gw
        # 관문을 거치면 안 되는 곳 — 자기 판이 띄운 개발 서버 검증(실측: curl 목적지 최다).
        for _k in ("no_proxy", "NO_PROXY"):
            env[_k] = "127.0.0.1,localhost,::1,0.0.0.0"
    env["XDG_CACHE_HOME"] = "/tmp/npm-cache"
    argv = [
        bwrap,
        "--ro-bind", "/", "/",
        "--tmpfs", "/root",
        # [봇 임시공간 봉합(2026-07-26, U-063 실측)] 종전 tmpfs는 root 소유 0755로 생겨, 권한강등된
        # 봇(ORGANT_RUN_USER)이 **임시 디렉터리를 하나도 못 만들었다**. 브라우저 검증뿐 아니라 임시
        # 파일을 쓰는 보통 도구가 전부 EACCES로 죽었고(실측: playwright `mkdtemp '/tmp/...'` 거부),
        # 봇은 원인을 못 보니 검증을 포기하고 약한 명령으로 우회했다(U-063 계획 교착의 실질 뿌리).
        # 이건 샌드박스 전용 tmpfs라 호스트 /tmp와 무관 — 표준 1777로 되돌린다.
        "--perms", "01777", "--tmpfs", "/tmp",
        "--perms", "01777", "--tmpfs", "/var/tmp",
        "--tmpfs", "/run",
        "--dev", "/dev",
        "--proc", "/proc",
        "--dir", sandbox_ws,
        "--bind", real_ws, sandbox_ws,
        "--perms", "0777", "--dir", "/tmp/npm-cache",
    ]
    # 라이브 venv의 shebang/interpreter는 /root/ClaudeCompany/.venv를 가리킨다. host /root는
    # 숨기되 이 비밀 없는 실행환경만 읽기 전용으로 다시 노출한다.
    venv = os.environ.get("VIRTUAL_ENV") or "/root/ClaudeCompany/.venv"
    sandbox_path = ":".join(
        part for part in str(env.get("PATH") or "/usr/local/bin:/usr/bin:/bin").split(":")
        if part and not os.path.realpath(part).startswith("/root/")
    )
    if os.path.isdir(venv) and os.path.realpath(venv).startswith("/root/"):
        real_venv = os.path.realpath(venv)
        argv.extend(["--dir", os.path.dirname(real_venv),
                     "--dir", real_venv,
                     "--ro-bind", real_venv, real_venv])
        legacy_root = "/root/murmur-stack"
        if (os.path.islink(legacy_root)
                and os.path.realpath(legacy_root) == os.path.dirname(real_venv)):
            # 기존 venv console-script shebang이 이 역사적 symlink 경로를 품고 있다.
            argv.extend(["--symlink", os.path.dirname(real_venv), legacy_root])
        sandbox_path = os.path.join(real_venv, "bin") + ":" + sandbox_path
    env["PATH"] = sandbox_path
    # [협의 기록 봉인(2026-07-31, 현준-4 실측)] 정책은 이미 '.collab/은 시스템 소유'인데
    # 강제는 run 명령 문자열 검사뿐이었다. 파일시스템은 봇에게 그 폴더의 소유권을 줬다
    # (_chown_tree가 작업공간을 통째로 넘긴다) - 실측으로 봇 uid가 TEAM.md를 지울 수 있었다.
    # 문자열은 우회된다(스크립트·빌드도구·경로 조립). 샌드박스 안에서 읽기 전용으로 덮어
    # 파일시스템이 정책을 강제하게 한다. 러너는 샌드박스 밖이라 종전대로 쓴다.
    _collab = os.path.join(real_ws, ".collab")
    if os.path.isdir(_collab):
        argv.extend(["--ro-bind", _collab, sandbox_ws + "/.collab"])
    playwright_cache = "/root/.cache/ms-playwright"
    if os.path.isdir(playwright_cache):
        argv.extend([
            "--dir", "/root/.cache",
            "--dir", playwright_cache,
            "--ro-bind", playwright_cache, playwright_cache,
        ])
        env["PLAYWRIGHT_BROWSERS_PATH"] = playwright_cache
    argv.extend([
        "--chdir", sandbox_ws,
        "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        # 비특권에서는 userns를 함께 열어야 bind/tmpfs 자체가 허용된다(root면 불필요).
        *(["--unshare-user"] if unpriv else []),
        "--die-with-parent",
        "--setenv", "HOME", sandbox_ws,
        "--setenv", "PATH", sandbox_path,
        "--setenv", "npm_config_cache", "/tmp/npm-cache",
        "--setenv", "XDG_CACHE_HOME", "/tmp/npm-cache",
        *(["--setenv", "PLAYWRIGHT_BROWSERS_PATH", playwright_cache]
          if os.path.isdir(playwright_cache) else []),
    ])
    if unpriv:
        # 강등할 특권이 없다 — 파일시스템 경계만 세우고 바로 실행한다.
        argv.extend(["--", "/bin/sh", "-c", exec_cmd])
    else:
        argv.extend([
            setpriv,
            "--reuid", str(uid), "--regid", str(gid), "--clear-groups",
            "--no-new-privs", "--inh-caps=-all", "--ambient-caps=-all",
            "--bounding-set=-all",
            "--", "/bin/sh", "-c", exec_cmd,
        ])
    return argv, env, ""


async def run_workspace_command(workspace, command, timeout=60):
    """run 도구와 SYS 자동검증이 함께 쓰는 안전한 작업공간 셸 프리미티브.

    ``(ok, rc, stdout, stderr, reason)``을 반환한다. 별도 SYS subprocess 경로가 기존 run보다
    약해지지 않도록 cwd·비밀 제거·비특권 강등·프로세스그룹 회수를 한곳에서 보장한다.
    """
    cmd, ws = str(command or "").strip(), str(workspace or "").strip()
    if not ws:
        return False, None, "", "", "작업공간이 설정되지 않았습니다."
    scan = cmd
    try:
        scan = scan.replace(os.path.realpath(ws), " ").replace(ws, " ")
    except Exception:
        pass
    if _COLLAB_RE.search(cmd.lower()):
        return False, None, "", "", "협의 기록(.collab/) 접근은 허용되지 않습니다."
    if any(d in scan.lower() for d in _RUN_DENY):
        return False, None, "", "", f"파괴/저장소/시스템 패턴 포함 — {cmd[:80]}"
    if any(p in cmd for p in _RUN_AUTHOR):
        return False, None, "", "", "run은 실행·빌드·검증 전용이며 파일 작성 명령은 허용되지 않습니다."

    of, ef = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    argv, env, prep_error = _prepare_run_exec(ws, cmd)
    if prep_error:
        of.close(); ef.close()
        return False, None, "", "", prep_error
    try:
        p = await asyncio.create_subprocess_exec(
            *argv, cwd=ws, stdout=of, stderr=ef, start_new_session=True, env=env)
        timed_out = False
        try:
            rc = await asyncio.wait_for(p.wait(), timeout=max(1, int(timeout)))
        except asyncio.TimeoutError:
            timed_out, rc = True, None
        finally:
            _reap_pgroup(p.pid)
            try:
                await asyncio.wait_for(p.wait(), timeout=2)
            except Exception:
                pass
        of.seek(0); ef.seek(0)
        out, err = of.read().decode("utf-8", "replace"), ef.read().decode("utf-8", "replace")
    except Exception as e:
        return False, None, "", "", f"실행 오류: {e}"
    finally:
        of.close(); ef.close()
    if timed_out:
        return False, rc, out, err, f"실행 시간초과({int(timeout)}s)"
    return rc == 0, rc, out, err, ""


def _looks_like_verification_command(command: str) -> bool:
    """호환용 파사드 — 실제 판정은 rule.evidence의 단일 계약을 쓴다."""
    return _evidence_command(command)


def _receipt_evidence_target(flow, evidence_for) -> str:
    """현재 SYS 경계가 요구하는 정확한 증거 대상. 빈 값은 영수증 발급 불가."""
    supplied = str(evidence_for or "").strip()
    challenge = getattr(flow, "_release_verify_challenge", None) or {}
    if challenge:
        expected = str(challenge.get("desc") or "").strip()
        return expected if supplied == expected else ""
    if getattr(flow, "_e2e_receipt_nonce", None):
        known = {
            str(item.get("id") or "").strip()
            for item in (getattr(flow, "e2e_checklist", None) or [])
            if isinstance(item, dict)
        }
        return supplied if supplied and supplied in known else ""
    return ""


def _verification_record(flow, evidence_for):
    """현재 target의 SYS 소유 verifier challenge 레코드와 명세를 찾는다."""
    target = str(evidence_for or "").strip()
    challenge = getattr(flow, "_release_verify_challenge", None) or {}
    if challenge and target == str(challenge.get("desc") or "").strip():
        return "release", challenge, str(challenge.get("verify") or "").strip()
    if getattr(flow, "_e2e_receipt_nonce", None):
        item = next(
            (row for row in (getattr(flow, "e2e_checklist", None) or [])
             if isinstance(row, dict) and str(row.get("id") or "").strip() == target),
            None,
        )
        if item is not None:
            return "e2e", item, str(item.get("verifier_spec") or item.get("spec") or "").strip()
    return None, None, ""


def _target_dead_end_hint(flow, target) -> str:
    """[막힘이 안 풀리는 이유를 말해준다(2026-07-29, U-079 4세대 실측)] 종전 문구는 '정확한 target id가
    아닙니다'로 끝나, e2e 항목(`condition:N`)을 주기 안에서 재실증하려던 QA가 무엇이 잘못인지도,
    지금 무엇을 해야 하는지도 알 수 없었다 — 같은 시도를 반복하다 백로그를 blocked로 두고, 그
    막힘은 주기 내내 풀리지 않았다(MS-298888112-4/ST-2 B1). e2e 장부는 **Task 경계**에서만 열린다.
    지금 주기 안이라면 이 target으로는 영영 봉인할 수 없다는 사실과 두 출구를 함께 준다."""
    t = str(target or "").strip()
    if not t.lower().startswith(("condition:", "surface:", "flow:", "origin:")):
        # [봉인할 대상이 아예 없을 때(2026-07-31, U-442 실측)] release/e2e challenge가 하나도 열려
        # 있지 않으면 어떤 target도 봉인되지 않는다. 그런데 안내는 "정확한 target id가 아닙니다"로
        # 끝나서, 팀은 '주기를 닫아야 target이 생긴다 → 닫으려면 영수증이 필요하다'는 순환에 갇혀
        # 판이 멈췄다(실측: 같은 취지의 [의견] 수십 건 → 중지 표결 → 사용자 대기).
        if not (getattr(flow, "_release_verify_challenge", None)
                or getattr(flow, "_e2e_receipt_nonce", None)):
            return ("\n지금은 **봉인할 대상이 없습니다** — 영수증(receipt)은 주기 잠금(release)이나 "
                    "Task 경계(e2e)가 열렸을 때만 발급됩니다. 이번 주기의 완수조건은 봉인 없이 그냥 "
                    "`run`으로 실행하고 그 결과(exit code·stdout 요지)를 `report_iter`에 제출하면 "
                    "닫힙니다 — evidence_for·seal_verifier 없이 진행하세요.")
        return ""
    from .rule.wrapup import _boundary_gap
    try:
        gap = _boundary_gap(flow)
    except Exception:
        gap = ""
    if not gap:
        return ("\n이 id는 e2e 장부 항목입니다 — 장부가 열려 있지 않거나(e2e_open 미개시) "
                "항목 id가 장부와 다릅니다. e2e_open 응답의 체크리스트에 있는 id를 그대로 쓰세요.")
    return ("\ne2e 항목(`" + t + "`)은 **Task 경계에서만** 실증합니다 — 지금은 주기가 열려 있어"
            "(" + str(gap)[:60] + ") 이 target으로는 봉인·receipt 발급이 불가능합니다. "
            "이번 주기에 할 일은 **결함의 원인을 고치고 이 마일스톤의 완수조건을 그 조건의 "
            "verifier로 실증**하는 것까지입니다. 주기가 닫히면 e2e 장부가 자동으로 다시 열리고 "
            "그때 이 항목을 재실증합니다. 지금 이 백로그가 e2e 재실증만을 요구한다면 그 백로그는 "
            "이번 주기에 완료할 수 없습니다 — 원인 수정 백로그로 바꿔 쓰거나 drop_backlog 하세요.")


def _seal_verifier_command(flow, actor, evidence_for, command) -> str:
    """검증 명령을 현재 target/spec/artifact에 봉인한다. 실행은 다음 exact run 한 번만 허용."""
    from .rule.milestone import workspace_artifact_stamp, write_revision

    kind, record, spec = _verification_record(flow, evidence_for)
    target = str(evidence_for or "").strip()
    cmd = normalize_verifier_command(command)
    if record is None:
        return ("verifier 봉인 불가 — 현재 release/e2e challenge의 정확한 target id가 아닙니다."
                + _target_dead_end_hint(flow, target))
    fixed = bool(record.get("verifier_fixed"))
    existing = normalize_verifier_command(record.get("verifier_command"))
    if (kind == "release"
            and not direct_verifier_command(spec, getattr(flow, "workspace", ""))
            and not record.get("verifier_structurally_ratified")):
        return (
            "verifier 봉인 불가 — 자연어 GOAL은 실행 시점의 임의 command 제안으로 비준할 수 "
            "없습니다. 최종 마일스톤 회의에서 SYS가 붙인 해당 GOAL@ 정본 marker의 exact "
            "executable verifier를 먼저 비준해야 합니다.")
    if fixed and existing and cmd != existing:
        return (f"verifier 봉인 불가 — {target}은 SYS가 정한 exact 명령만 허용합니다: "
                f"`{existing[:180]}`")
    structurally_ratified = bool(record.get("verifier_structurally_ratified"))
    admissible = (
        _evidence_command(cmd, getattr(flow, "workspace", ""))
        if structurally_ratified and fixed and existing == cmd
        else command_matches_spec(cmd, spec, getattr(flow, "workspace", ""))
    )
    if not admissible:
        return ("verifier 봉인 불가 — true/echo/inline -c·-e/작업공간 밖 test가 아닌 실제 "
                "테스트·빌드·HTTP·브라우저 검사 명령이어야 하며, 실행형 verify가 이미 있으면 "
                "그 원문과 정확히 같아야 합니다.")
    # [가리키는 파일이 없는 명령은 봉인하지 않는다(2026-08-03, 실측 U-478·U-496)] 봉인은 명령의
    # **형태**만 봤다(command_matches_spec). 그래서 이 작업공간에 없는 절대경로가 그대로 봉인되고,
    # 다음 run이 exit 2로 죽고, receipt는 rc=0에서만 발급되므로 그 항목은 결과 없이 실패로 남는다.
    # e2e judge는 "검증기가 못 돌았다"와 "제품이 틀렸다"를 구분하지 않으므로(wrapup.judge), 그것이
    # **제품 결함**으로 집계돼 수리 주기가 열린다 — 실측 U-496 13:04 e2e 결함 2건의 실제 원인이
    # "봉인된 verifier의 잘못된 절대경로로 guide에서 exit 2/receipt 미발급"이었다.
    # 판정자는 이미 있다(_goal_verifier_unrunnable과 같은 술어): 형태는 실행 명령인데 가리키는
    # 파일이 없으면 지금 말해 준다 — 봉인 전에 고치면 결함 기록도, 수리 주기도 생기지 않는다.
    _ws = getattr(flow, "workspace", "")
    if (_ws and direct_verifier_command(cmd, _ws, require_existing=False)
            and not direct_verifier_command(cmd, _ws, require_existing=True)):
        return ("verifier 봉인 불가 — 이 명령이 가리키는 파일이 작업공간에 없습니다: "
                f"`{cmd[:160]}`. 경로는 작업공간 기준이어야 하며(cwd={_ws}), 파일을 먼저 만들거나 "
                "경로를 고친 뒤 다시 봉인하세요. 없는 파일을 봉인하면 실행이 실패하고 그 실패가 "
                "제품 결함으로 기록됩니다.")
    stamp = workspace_artifact_stamp(flow)
    if not stamp:
        return "verifier 봉인 불가 — 작업공간 artifact stamp를 만들 수 없습니다."
    record.update({
        "verifier_command": cmd,
        "verifier_command_hash": verifier_command_hash(cmd),
        "verifier_spec_hash": verifier_spec_hash(target, spec),
        "verifier_seal": secrets.token_hex(16),
        "verifier_actor": int(actor),
        "verifier_epoch": write_revision(flow),
        "verifier_stamp": stamp,
        "verifier_used": False,
    })
    return (f"verifier 봉인 완료 — target={target}. 다음 run에서 evidence_for를 그대로 두고 "
            f"아래 명령을 한 글자도 바꾸지 말고 1회 실행하세요:\n`{cmd}`")


def _authorize_sealed_verifier_run(flow, actor, evidence_for, command):
    """실행 직전 exact seal을 단일사용으로 소비한다. 반환=(receipt seal metadata, 오류문구)."""
    from .rule.milestone import workspace_artifact_stamp, write_revision

    kind, record, spec = _verification_record(flow, evidence_for)
    target = str(evidence_for or "").strip()
    cmd = normalize_verifier_command(command)
    if record is None:
        return None, ("SYS receipt 실행 불가 — 현재 challenge의 정확한 evidence_for가 아닙니다."
                      + _target_dead_end_hint(flow, target))
    expected = normalize_verifier_command(record.get("verifier_command"))
    command_hash = verifier_command_hash(cmd)
    spec_hash = verifier_spec_hash(target, spec)
    if (not expected or not record.get("verifier_seal")
            or record.get("verifier_used")
            or cmd != expected
            or command_hash != str(record.get("verifier_command_hash") or "")
            or spec_hash != str(record.get("verifier_spec_hash") or "")
            or int(record.get("verifier_actor") or 0) not in (0, int(actor))
            or not (
                _evidence_command(cmd, getattr(flow, "workspace", ""))
                if record.get("verifier_structurally_ratified")
                and record.get("verifier_fixed") and expected == cmd
                else command_matches_spec(
                    cmd, spec, getattr(flow, "workspace", ""))
            )):
        return None, ("SYS receipt 실행 불가 — 먼저 run(seal='yes')로 이 target의 verifier를 봉인하고, "
                      "봉인된 exact command를 같은 실행자가 한 번만 실행해야 합니다.")
    if (int(record.get("verifier_epoch", -2)) != write_revision(flow)
            or not record.get("verifier_stamp")
            or str(record.get("verifier_stamp")) != workspace_artifact_stamp(flow)):
        return None, ("SYS receipt 실행 불가 — verifier 봉인 뒤 산출물 버전이 달라졌습니다. 현재 버전에서 "
                      "같은 target/command를 다시 seal한 뒤 실행하세요.")
    if int(record.get("verifier_actor") or 0) == 0:
        record["verifier_actor"] = int(actor)
    record["verifier_used"] = True
    return {
        "kind": kind,
        "target": target,
        "seal": str(record["verifier_seal"]),
        "command_hash": command_hash,
        "spec_hash": spec_hash,
    }, ""


def _issue_run_receipt(
    flow, actor, command, rc, stdout="", stderr="", evidence_for="", seal_meta=None,
) -> str:
    """실제 run subprocess 종료 뒤에만 생성되는 process-local 영수증.

    release verifier가 연 exact challenge token을 함께 봉인한다. 장부는 같은 봇 턴 안의 report_iter가
    단일사용으로 소비하며 재시작 시 사라져도 잠금은 fail-closed(조건 자체의 최종 receipt는 별도 직렬화).
    """
    from .rule.milestone import workspace_artifact_stamp, write_revision
    challenge = getattr(flow, "_release_verify_challenge", None) or {}
    e2e_nonce = str(getattr(flow, "_e2e_receipt_nonce", "") or "")
    if not challenge and not e2e_nonce:
        return ""                              # 일반 작업 run은 manifest hashing/ledger 비용 불필요
    target = _receipt_evidence_target(flow, evidence_for)
    _kind, record, spec = _verification_record(flow, target)
    command_hash = verifier_command_hash(command)
    spec_hash = verifier_spec_hash(target, spec)
    if (rc is None or int(rc) != 0
            or not target or record is None or not seal_meta
            or str(seal_meta.get("target") or "") != target
            or str(seal_meta.get("seal") or "") != str(record.get("verifier_seal") or "")
            or str(seal_meta.get("command_hash") or "") != command_hash
            or str(seal_meta.get("spec_hash") or "") != spec_hash
            or not record.get("verifier_used")
            or int(record.get("verifier_actor") or 0) != int(actor)
            or normalize_verifier_command(record.get("verifier_command"))
            != normalize_verifier_command(command)
            or not _evidence_command(command, getattr(flow, "workspace", ""))):
        return ""
    rid = "run-" + secrets.token_hex(10)
    ledger = getattr(flow, "_run_receipts", None)
    if ledger is None:
        ledger = flow._run_receipts = {}
    ledger[rid] = {
        "actor": int(actor),
        "command": str(command or "")[:500],
        "rc": rc,
        "stdout": str(stdout or "")[-500:],
        "stderr": str(stderr or "")[-300:],
        "challenge": str(challenge.get("token") or ""),
        "e2e_nonce": e2e_nonce,
        "evidence_for": target,
        "verifier_seal": str(seal_meta["seal"]),
        "command_hash": command_hash,
        "spec_hash": spec_hash,
        "write_epoch": write_revision(flow),
        "artifact_stamp": workspace_artifact_stamp(flow),
    }
    while len(ledger) > 48:
        ledger.pop(next(iter(ledger)))
    return rid


# [Task Rule → rule/task.py] 완료·인수 검증 게이트는 원래 §7 설계대로 rule/task로 분리(guide_tools 병합 해체)
from .rule.task import (_perceptual_essential, _wants_real_data,  # noqa: F401  [PJT/tests(test_sys)가 파사드에서 직접 import — 유지]
                        _has_real_dataset, _synthesizes_data, _is_verifier)
# [마일스톤 파이프라인 — S1(PIPELINE_REWORK_2026-07-09)] 도구 표면·회의 설명 분기가 소비.
from .rule.milestone import (pipeline_on as _pipe_on, rule_renegotiate, rule_report_iter,
                             rule_set_milestone, rule_set_subtask)
from .rule.wrapup import (rule_e2e_finish, rule_e2e_open, rule_e2e_result,
                          rule_e2e_scope)


# [스태핑 커버리지 — 리더 흡수 차단(2026-06-19, 사용자: '전문가 분배 무조건, 리더는 자기 직군만')]
# 기존 게이트(#4 owner도메인 대리구현 금지 / #6 리더독식)는 '전문가가 *있으면*' 리더 흡수를 막지만,
# 리더가 그 도메인 전문가를 *안 뽑으면*(언더스태핑) 보호할 owner가 없어 리더가 흡수한다(라이브 P-022:
# 'AI를 학습' 요청에 AI엔지니어 미투입 → 백엔드 리더가 AI·data 53건 흡수). 그래서 set_goal에서 '목표가
# *명시적으로* 부른 전문 능력을 팀이 보유했나'를 본다 — 없으면 recruit 강제(그러면 owner가 박혀 기존
# #4가 자동으로 리더를 자기 직군에 가둠). 기능 식별(능력 needs↔팀 라벨)이라 직군 타이틀 하드코딩이 아니다.
# 고신호 능력만(오발 최소). 새 능력은 (이름, needs(text)→bool, providers(label keywords)) 한 줄로 확장.
# [팀·역량 라우팅 Rule → rule/communication] guide_tools 병합 해체(re-export로 도구·tests 호환)
from .rule.communication import _say as _rule_say, vote as _rule_vote  # noqa: F401  [발언·표결 → rule/communication]
from .rule.communication import vote_stop as _rule_vote_stop  # noqa: F401  [중지 투표 → comm_ceremonies]
from .rule.communication import request as _rule_request  # noqa: F401
from .rule.communication import recruit as _rule_recruit  # noqa: F401
from .rule.communication import parallel_work as _rule_parallel_work  # noqa: F401
from .rule.communication import meet as _rule_meet  # noqa: F401
from .rule.communication import (_capability_gaps, _needed_caps_coverage, _offdomain_capability_hit,  # noqa: F401  [스택(permissions·sys_core·system/tests) + PJT/tests(test_sys) 소비 — 유지]
                                 _norm_job, _jobs_of, _JOB_SEP)  # _JOB_SEP: guide/discord_guide.py:324가 참조 표기 — 호환 유지
from .rule.communication import _clarify_hold  # [G2 — clarify 행동 잠금(B-02)] run 경로도 동일 조건




def _reap_pgroup(pgid: int):
    """프로세스그룹 pgid에 남은 프로세스를 모두 종료한다(백그라운드 서버 누수 차단).
    셸을 self-session으로 띄우면 모든 자손이 pgid==셸pid를 공유한다. 다만 리더(셸)가
    먼저 끝나 reap되면 '고아 프로세스그룹'이 돼 killpg가 안 먹으므로, /proc를 훑어
    pgid가 같은 잔여 프로세스를 PID로 직접 SIGKILL한다(이게 run 간 포트충돌의 구조적 해결)."""
    try:
        os.killpg(pgid, signal.SIGKILL)   # 리더 생존 시 빠른 경로
    except (ProcessLookupError, PermissionError, OSError):
        pass
    me = os.getpid()
    try:
        entries = [d for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return
    for d in entries:
        pid = int(d)
        if pid == me:
            continue
        try:
            with open(f"/proc/{pid}/stat", "rb") as f:
                data = f.read()
            # stat: 'pid (comm) state ppid pgrp ...' → comm의 마지막 ')' 뒤 3번째가 pgrp
            if int(data[data.rindex(b")") + 1:].split()[2]) == pgid:
                os.kill(pid, signal.SIGKILL)
        except (OSError, ValueError, IndexError):
            continue


from .rule.task import TaskRef, create_task as _rule_create_task  # noqa: F401  [Task 상태·도구로직 → rule/task; TaskRef: sys_core + PJT/tests(test_sys) 소비 — 유지]
from .rule.task import complete_task as _rule_complete_task  # noqa: F401
from .rule.task import set_goal as _rule_set_goal  # noqa: F401


from .flow import Flow  # noqa: F401  [Flow 상태 → flow.py]
# [배포 타겟 호환 — Render Node 전용(2026-06-22 P-028 규명)] deploy_sync는 Node만 빌드한다(runtime:node
# 하드코딩, package.json 필수). 흔한 사고: Node 서버가 *런타임*에 Python을 spawn/exec → Render Node 환경엔
# Python이 없어 백엔드가 안 떠 502(P-028: ECONNREFUSED:8001, 28모델 고아). 런어웨이 5회 상한은 *사후* 차단
# [Project Rule → rule/project.py] 배포 신원·적합성은 원래 §7 설계대로 분리(guide_tools 병합 해체). re-export로 호환.
from .rule.project import deploy_service_name, _deploy_infeasibility, create_project as _rule_create_project  # noqa: F401  [deploy_service_name: sys_core + PJT/tests, _deploy_infeasibility: PJT/tests(test_sys) 소비 — 유지]
from .rule.project import deploy as _rule_deploy  # noqa: F401
from .rule.project import send_file as _rule_send_file  # noqa: F401












def _holds_completion(flow, me_id, role) -> bool:
    """[리더 폐지(2026-07-27, 사용자: '리더라는 존재 자체를 없애버리고')] 마감 권한을 '리더'라는
    자리에 묶어두면, 그 자리에 있는 봇이 못/안 부를 때 판 전체가 닫히지 못한다(U-065: 구현·검증이
    모두 끝난 판이 마감 호출자 부재로 여섯 겹의 교착을 겪음). 마감의 옳음은 **자리**가 아니라
    **관문**이 지킨다 — 주기 완료·e2e 통과·교차검증·증거는 complete_task 게이트가 전부 검사하며,
    자격 없는 호출은 그 자리에서 거절된다. 그러므로 팀의 누구든 마감을 시도할 수 있게 한다
    (허위 완료 방지는 게이트가, 권한 분산은 여기서)."""
    return True

def _resolve_scoped_backlog(flow, subtasks, backlog_id, me_id, st_hint=""):
    """지역 ID(B1...)를 실제 ``(SubTask, Backlog)`` 한 쌍으로 해석한다.

    도구 호출은 오래전부터 id만 받았지만 B번호는 SubTask마다 다시 시작한다. 같은 B1이 여러
    열린 단위에 있으면 완료된 앞 단계가 뒤 단계 조작을 가로채지 않게 비종결 후보만 보고,
    명시 st → 현재 수행자 → 배분권자 → 제출자 순으로 좁힌다. 그래도 둘 이상이면 추측하지 않는다.
    """
    from .rule.backlog import DONE, DROPPED, relay_for

    bid = str(backlog_id or "").strip()
    hint = str(st_hint or "").strip()
    # [시스템이 쓴 표기를 시스템이 되받는다(2026-08-03, 실측 U-478)] 이 판은 봇에게 범위를
    # `<SubTask-ID>::<Bn>`으로 보여준다 — 후보 목록도(`ST::B1 · ST::B2`), 오류문도, 보충 링크
    # 문법(`[해결: ST::Bn]`)도 그 꼴이다. 그런데 봇이 그 문자열을 st로 되돌려주면 st_id와 같지
    # 않아 '단위를 찾지 못했습니다'로 거절됐고, 팀은 같은 확인을 네 턴 반복했다(10:32~10:45,
    # 그 사이 wrapup 재실증도 못 했다). 가르친 표기는 받아야 한다 — `::` 뒤는 일감 이름이므로
    # 범위 힌트에서 떼어 낸다(모호할 여지가 없는 자기 표기다).
    if "::" in hint:
        hint = hint.split("::", 1)[0].strip()
    hint = hint.lower()
    candidates = []
    for st in subtasks:
        relay = relay_for(flow, st)
        for backlog in relay.backlogs:
            if backlog.backlog_id == bid and backlog.status not in (DONE, DROPPED):
                candidates.append((st, relay, backlog))
    if hint:
        hinted = [x for x in candidates
                  if hint == str(x[0].st_id).lower() or hint in str(x[0].goal).lower()]
        if not hinted:
            return None, (f"백로그 {bid}의 단위 '{st_hint}'를 찾지 못했습니다. 열린 후보: "
                          + " · ".join(str(x[0].st_id) for x in candidates[:8]))
        candidates = hinted
    if len(candidates) == 1:
        return candidates[0], None

    mine_active = [x for x in candidates
                   if x[2].status == "in_progress" and int(x[2].assignee or 0) == int(me_id)]
    if len(mine_active) == 1:
        return mine_active[0], None
    holder = [x for x in candidates if int(x[1].turn_holder or 0) == int(me_id)]
    if len(holder) == 1:
        return holder[0], None
    mine = [x for x in candidates if int(x[2].submitter or 0) == int(me_id)]
    if len(mine) == 1:
        return mine[0], None
    if not candidates:
        return None, f"백로그 {bid}가 열린 단위 어디에도 없습니다."
    return None, (f"백로그 {bid}가 여러 단위에 있어 하나로 정할 수 없습니다 — st에 단위 ID/목표를 "
                  f"함께 주세요: " + " · ".join(str(x[0].st_id) for x in candidates[:8]))


def _active_scoped_backlog(flow, subtasks):
    """열린 마일스톤 전체의 단일 활성 백로그를 찾는다(B ID는 지역 ID라 scope도 함께 반환)."""
    from .rule.backlog import relay_for
    for st in subtasks:
        relay = relay_for(flow, st)
        for backlog in relay.backlogs:
            if backlog.status == "in_progress":
                return st, relay, backlog
    return None


def _audited(tools, me_id, role):
    """도구 실행을 공용 감사에 남기도록 감싼다.

    [경로별 감사 갈림 봉합(2026-07-30, 현준-4)] 도구를 실행하는 길이 셋(Claude 훅·codex 브리지·
    러너 직접 호출)이라 호출부마다 붙이면 반드시 하나가 빠진다. 도구가 스스로 남기면
    길이 몇 개든 상관없다 - 여기가 유일한 길목이다.
    """
    from .audit import record_tool_use
    out = []
    for t in tools:
        name = getattr(t, "name", None)
        inner = getattr(t, "handler", None)
        if inner is None:
            out.append(t)
            continue

        async def _wrapped(args, _inner=inner, _name=name):
            record_tool_use(actor=me_id, role=role, tool=_name, tool_input=args)
            return await _inner(args)

        try:
            t.handler = _wrapped        # 도구 객체는 그대로 두고 손잡이만 바꾼다
        except Exception:
            pass                        # 못 바꾸면 원본 그대로 - 기록이 도구를 막지 않는다
        out.append(t)
    return out


def make_guide_tools(flow: Flow, me_id: int, role: str, mode: str = "collab"):
    # [G3 — 캐주얼 도구 미장착(B-06)] mode="casual"이면 협업·제작 도구(request·recruit·리더도구)를 아예
    # 장착하지 않고 run만 준다(일상 대화 턴의 오발 프로젝트를 프롬프트가 아니라 구조로 차단 — 스키마 토큰도
    # 절약). mode="e2e"는 Task 경계 전용 표면(run·scope·result·finish)만 준다. 기본값 "collab"은
    # 현행과 동일(하위호환 — 기존 호출부 무변경).
    g = flow.guide
    tools = []

    async def _say(who, text):
        return await _rule_say(flow, who, text)   # [→ rule/communication._say] 발언을 봇 본인 명의로(가시성=실체)

    @tool("request", "현재 Task 팀의 동료 한 명에게 요청(kind: Info=질문 / Work=작업, to_id 문자열). "
          # [이 일에도 이름이 있다(2026-07-31, 사용자: '그냥 첫 글 첫 문장 가져오는건 잘못된 대처')]
          # 회의·단계는 제목을 강제하는데 요청만 없어서, 화면이 첫 문장을 잘라 제목처럼 썼다.
          "**title=이 요청 한 줄 제목**(필수 — 예: '결과 리포트 UI 브라우저 검증'). 화면은 이 제목으로 "
          "그 대화를 부릅니다(없으면 첫 문장을 잘라 쓰던 옛 방식으로 떨어집니다). "
          "미완 owner가 있는 일을 타인에게 새로 맡길 땐 takeover_reason(담당 교체 사유) 또는 "
          "different_deliverable(별개 산출물임을 명시) 인자를 함께(선택 — 없으면 이어가기 안내로 보류될 수 있음). "
          "직군밖 차단의 의식적 예외는 override_reason(왜 그 동료가 맡아야 하는지)로(종전 body '[직군초과: 사유]'와 동등).",
          {"to_id": str, "kind": str, "title": str, "body": str, "takeover_reason": str,
           "different_deliverable": str, "override_reason": str})
    async def request(args):
        return await _rule_request(flow, me_id, role, args)
    tools.append(request)

    @tool("recruit",
          "동료가 필요하면 **지목하지 말고 '필요'를 공고**한다(진짜 채용): recruit(need='어떤 문제/"
          "일손이 필요한지') → 한가한 동료 전원이 공고를 받고 스스로 지원([지원]+지원서)하거나 "
          "패스한다 → 지원서가 돌아오면 recruit(member=지원자, reason=선발 사유)로 확정. "
          "직군을 미리 정할 필요 없다 — 문제에 집중하라(원하면 role=로 참고 표기; 직군 없는 지원자는 "
          "지원서에 [직군: 이름]을 선언). 지원하지 않은 동료의 지명은 거부된다(독단 영입 금지). "
          "지원자가 없고 role이 있으면 신규 채용(genesis) 자동. **1봇 1직업**(겸직은 예비 없음/유사 "
          "일일 때만, 최대 2) · 직군명은 기존 것 재사용 우선(변형 금지, 신설은 new_role='yes').",
          {"member": str, "need": str, "role": str, "reason": str, "new_role": str})
    async def recruit(args):
        return _ok(await _rule_recruit(flow, me_id, role, args))
    tools.append(recruit)

    # [마일스톤 파이프라인 — 공통 표면(전 참여자)] SubTask 추가(자발 참여의 문)와 iter 검증 제출은
    # 결정권자 전용이 아니다 — 현장 누구나. 플래그 OFF면 미등록(표면 불변).
    if _pipe_on():
        @tool("set_subtask",
              "진행 중 마일스톤에 분해 단위(SubTask)를 추가한다 — 팀 판에선 회의 수렴안에 '단위: 목표 | "
              "실증절차' 줄로 동봉해 **가결과 함께 등록**되는 게 정석(개인 등록은 솔로 판만). 단위는 팀 "
              "공유 컨테이너고, **전담은 백로그 단위** — 자기 몫은 pick_backlog(desc)로 등재해 집는다.",
              {"goal": str, "criteria": str})
        async def set_subtask(args):
            from .rule.milestone import flush_pipeline_notes as _flush
            _r = _ok(rule_set_subtask(flow, me_id, args))
            await _flush(flow)
            return _r
        tools.append(set_subtask)

        @tool("pick_backlog",
              "**각자 자기 일감을 동시에 진행한다.** desc='내가 할 일'로 내 백로그를 풀에 등재한다(st=단위 "
              "id/목표 일부로 소속 지정). **등재 순서가 곧 실행 순서다** — 선행되어야 하는 일부터 "
              "등재하라(B1→B2→… 순으로 넘어간다). 아무도 작업 중이 아니고 내 차례면 즉시 착수, 아니면 "
              "대기(앞 사람이 끝나면 등재 순서상 다음 백로그의 제출자에게 자동으로 넘어온다). "
              "**동시 진행**: 본문에 `[쓰기: 경로1, 경로2]`로 이 일이 고칠 영역을 선언하면, 영역이 "
              "겹치지 않는 다른 일감과 **같은 시각에** 진행됩니다(선언이 없으면 종전대로 하나씩). "
              "예: `[쓰기: public/game.js] 충돌 판정 고치기`. 겹치는 일감은 자동으로 순서를 기다립니다. "
              "id='B3'는 **마무리자(직전 완료·중단자)만** — 순서를 벗어나 다른 백로그를 다음으로 지정할 "
              "때 쓴다(수행자=그 제출자, 회의 산물처럼 제출자가 없으면 집는 사람). **작업(run/Write)은 착수된 뒤에만.**",
              {"id": str, "desc": str, "st": str})
        async def pick_backlog(args):
            from .rule.backlog import (
                relay_for, BacklogError, DuplicateBacklog, IN_PROGRESS, BLOCKED,
                backlog_rows, backlog_scope_key, blocked_ready_for_revisit,
            )
            from .rule.milestone import _set_pipeline_ctx, _ckpt as _ck  # [갭#1 — 릴레이 변이 즉시 영속]
            ms = next((m for m in (getattr(flow, "milestones", None) or []) if m.status not in ("done", "superseded")), None)
            _sts = [x for x in ms.subtasks if x.status not in ("done", "superseded")] if ms else []
            if not _sts:
                return _ok("활성 SubTask가 없습니다 — 단위 분해는 회의 수렴안('단위:' 줄)으로 가결과 함께 등록됩니다.")
            bid = str(args.get("id") or "").strip()
            desc = str(args.get("desc") or "").strip()
            _stq = str(args.get("st") or "").strip()
            try:
                if bid:
                    # [선정(2026-07-14)] 마무리자가 남은 백로그를 골라 그 제출자를 다음 수행자로 — 순차.
                    _hit, _err = _resolve_scoped_backlog(flow, _sts, bid, me_id, _stq)
                    if _err:
                        return _ok(f"선점 불가: {_err}")
                    _tgt, r, b = _hit
                    if b.status == IN_PROGRESS and int(b.assignee or 0) == int(me_id):
                        _set_pipeline_ctx(flow, me_id)
                        return _ok(f"백로그 {b.backlog_id}는 이미 당신이 작업 중입니다 — 이어서 하세요.")
                    # [전원 병렬(2026-07-31, 사용자 지시)] 다른 일감이 돈다고 선정을 막지 않는다.
                    # 단 **한 사람은 한 번에 하나** — 내가 이미 들고 있으면 그것부터 끝낸다.
                    from .rule.backlog import worker_busy_with as _busy_with
                    _mine_now = _busy_with(flow, me_id)
                    if _mine_now and _mine_now != b.backlog_id:
                        return _ok(f"선점 불가: 당신은 이미 백로그 {_mine_now}를 작업 중입니다 — "
                                   f"그것을 끝내거나(report_iter) 중단(drop_backlog)한 뒤 집으세요. "
                                   f"동시에 두 개를 들면 어느 쪽도 끝나지 않습니다.")
                    if b.status == BLOCKED:
                        _rows = backlog_rows(flow)
                        if not blocked_ready_for_revisit(
                                b, [row[2] for row in _rows],
                                backlog_scope_key(_tgt.st_id, b.backlog_id)):
                            return _ok(
                                f"선점 불가: {_tgt.st_id}/{b.backlog_id}는 선행 작업으로 차단됐습니다 — "
                                "연결된 보충 백로그가 모두 종결되고 실제 완료 증거가 생긴 뒤 재개하세요.")
                    # [무주=자기선택(2026-07-16)] 회의 산물(submitter=0)은 '집는 사람이 한다' — 수행자=나.
                    # 제출자 있는 항목은 종전대로 수행자=제출자(전담 불변 — 남이 채갈 수 없음).
                    _assn = int(b.submitter) or int(me_id)
                    # [무주 자기착수엔 착수 근거를 세운다(2026-07-29, U-079 실측)] 무주 항목(회의
                    # 수렴안, submitter=0)은 '집는 사람이 한다'로 열려 있어, 아무나 목록의 다음 것을
                    # 그대로 집으면 응찰·선정이 통째로 건너뛰어진다(실측: relay_bid 0 · 자기착수 다수 →
                    # 판이 위에서 아래로 흐르고 '선행이 안 끝나 못 한다'고 말할 자리가 사라진다).
                    # 막으면 교착 위험이 있으므로(응찰 수집이 아직 도구로 배선되지 않았다) 막지 않고,
                    # **왜 지금 이것이 착수 가능한지**를 적게 한다 — 선행 충족 판단이 기록에 남고,
                    # 못 적으면 그 자체가 '아직 아니다'라는 신호다.
                    if int(b.submitter or 0) == 0 and _assn == int(me_id):
                        _why = str(args.get("why") or args.get("reason") or "").strip()
                        if len(_why) < 10:
                            return _ok(
                                f"선점 보류: {b.backlog_id}는 무주 항목입니다 — 집기 전에 **지금 착수 "
                                "가능한 근거**를 why 인자로 한 줄 적으세요(필요한 선행 산출물이 이미 "
                                "있는지, 무엇을 근거로 지금인지). 선행이 안 끝났으면 집지 말고 그 선행 "
                                "백로그를 먼저 진행하세요.")
                        try:
                            if getattr(flow, "log", None):
                                flow.log("relay_self_claim", backlog=b.backlog_id,
                                         st=str(_tgt.st_id), by=int(me_id), why=_why[:160])
                        except Exception:
                            pass
                        # 근거는 **그 백로그 안에** 남긴다 — 채널 마커로 띄우면 공통 흐름을 어지럽히고,
                        # 정작 '이 일감을 왜 지금 집었나'는 그 일감 옆에 있어야 읽힌다(2026-07-29 사용자 지적).
                        try:
                            _ln = f"[자기착수] {flow._info(me_id) or me_id} — 근거: {_clip(_why, 240)}"
                            if not getattr(b, "activity", None) or b.activity[-1] != _ln:
                                b.activity.append(_ln)
                        except Exception:
                            pass
                    r.pick(int(me_id), b.backlog_id, _assn)      # relay가 배분권(마무리자)·순차 잠금 검증
                    _ck(flow)                                     # [갭#1] 선정 즉시 영속(크래시 내구)
                    _who = flow._info(_assn) if hasattr(flow, "_info") else _assn
                    if _assn == int(me_id):
                        _set_pipeline_ctx(flow, me_id)
                        return _ok(f"백로그 {b.backlog_id} 착수 — 작업하세요.")
                    return _ok(f"[다음 선정] {b.backlog_id} → {_who}를 다음 수행자로 선정 — 곧 깨어나 "
                               f"착수합니다. 선정 사유는 채널에 남기세요.")
                elif desc:
                    # [일감은 문장이어야 한다(2026-07-30, U-442 실측)] 본문 'x' 한 글자짜리 백로그가
                    # 그대로 등재돼 작업 목록에 남았다 — 집는 사람도 무엇을 하라는 것인지 알 수 없고,
                    # 완수 판정도 불가능하다. 무엇을 하는 일인지 읽히는 최소 길이를 요구한다.
                    if len(desc) < 10 or len(desc.split()) < 2:
                        return _ok(f"등재 거부: 백로그 본문이 너무 짧습니다('{desc}') — 집는 사람이 "
                                   f"무엇을 해야 하는지 읽히도록 **한 문장**으로 쓰세요"
                                   f"(예: 'JD 입력값 검증 규칙을 고정하고 화면에 연결한다').")
                    # [순차 1명 1개(2026-07-14, 사용자: '한명씩 여러개 등록이 아닌 순차적으로 1명씩
                    # 1개씩 돌아가며 — 균등 분배')] 내가 이미 미종결(open/in_progress/blocked) 백로그를
                    # 갖고 있으면 새 등재 거부 — 한 사람이 여러 개 선점해 몰아쥐는 것 차단.
                    _mine_open = next((b.backlog_id for x in _sts if (getattr(flow, "backlog_relays", None) or {}).get(x.st_id)
                                       for b in flow.backlog_relays[x.st_id].backlogs
                                       if int(b.submitter) == int(me_id) and b.status not in ("done", "dropped")), None)
                    if _mine_open is not None:
                        return _ok(f"등재 거부: 당신은 이미 백로그 {_mine_open}(미종결)를 갖고 있습니다 — "
                                   f"백로그는 1명 1개씩 순차 등재(균등 분배)입니다. 그것을 완료(report_iter)/"
                                   f"중단(drop_backlog)한 뒤 다음을 등재하세요.")
                    _tgt = None
                    if _stq:
                        _tgt = next((x for x in _sts if _stq in x.st_id or _stq.lower() in x.goal.lower()), None)
                        if _tgt is None:
                            return _ok(f"등재 불가: '{_stq}'와 맞는 열린 단위가 없습니다. 열린 단위: "
                                       + " · ".join(f"{x.st_id}({x.goal[:20]})" for x in _sts[:8]))
                    if _tgt is None:
                        _tgt = next((x for x in _sts if int(me_id) in (getattr(x, "participants", None) or set())), _sts[0])
                    r = relay_for(flow, _tgt)
                    try:
                        b = r.submit(int(me_id), _clip(desc, 240))   # 풀에 등재(OPEN)
                    except DuplicateBacklog as e:
                        return _ok(str(e))
                    _tgt.participants.add(int(me_id))
                    _tgt.backlog_ids = [x.backlog_id for x in r.backlogs]
                    # [순차 착수 정책(2026-07-14)] 첫 착수(turn_holder None) 또는 내가 마무리자일 때만 즉시
                    # 착수 — 아니면 등재만(대기). 내 차례는 마무리자의 pick_backlog(id) 선정으로 온다.
                    # [자기 일감은 곧바로 착수(2026-07-31, 사용자: '전체 직원이 계속 자기꺼')]
                    # 종전엔 배분권(마무리자)과 전체 1활성 둘 다가 등재자를 대기로 돌려세웠다 —
                    # 자기가 낸 일감을 자기가 하는 데 남의 차례를 기다릴 이유가 없다.
                    try:
                        r.pick(int(me_id), b.backlog_id, int(me_id))
                    except BacklogError as e:
                        _ck(flow)
                        return _ok(f"백로그 {b.backlog_id} 등재 완료(대기) — {e} 당신 차례가 오면 착수합니다.")
                else:
                    return _ok("id(마무리자 선정) 또는 desc(내 백로그 등재) 중 하나가 필요합니다.")
            except BacklogError as e:
                return _ok(f"선점 불가: {e}")
            _ck(flow)                             # [갭#1] 착수(mutation) 즉시 영속 — 크래시 내구
            _set_pipeline_ctx(flow, me_id)        # 이 턴의 이후 게시부터 이 백로그로 귀속
            return _ok(f"백로그 {b.backlog_id} 착수 — 작업하세요. 완료는 report_iter(조건 검증) 또는 위임 마무리가 장부에 반영합니다.")
        tools.append(pick_backlog)

        @tool("drop_backlog",
              "**중단**: 내 백로그(내가 제출/수행 중)를 완수 불가로 판단해 장부에서 제외한다 — 백로그는 "
              "개인 역량 안이어야 하며, 불가 판단도 본인 몫. blocked(선행 대기·재방문)와 다르게 중단은 "
              "종결이다. 중단하면 당신이 다음 선정의 담당자가 된다. id=백로그, st=단위 ID/목표, "
              "reason=왜 불가한가(필수).",
              {"id": str, "st": str, "reason": str})
        async def drop_backlog(args):
            from .rule.backlog import BacklogError, handoff_note
            from .rule.milestone import flush_pipeline_notes as _flush, _ckpt as _ck
            ms = next((m for m in (getattr(flow, "milestones", None) or []) if m.status not in ("done", "superseded")), None)
            _sts = [x for x in ms.subtasks if x.status not in ("done", "superseded")] if ms else []
            bid = str(args.get("id") or "").strip()
            _stq = str(args.get("st") or "").strip()
            reason = str(args.get("reason") or "").strip()
            if not bid or not reason:
                return _ok("id와 reason(왜 완수 불가인가)이 모두 필요합니다 — 중단은 기록이 남는 종결입니다.")
            _hit, _err = _resolve_scoped_backlog(flow, _sts, bid, me_id, _stq)
            if _err:
                return _ok(f"중단 불가: {_err}")
            _tgt, r, b = _hit
            try:
                r.drop(int(me_id), bid, reason)
            except BacklogError as e:
                return _ok(f"중단 불가: {e}")
            handoff_note(flow, r, me_id, "중단됐습니다")
            _ck(flow)                             # [갭#1] 중단 즉시 영속(크래시 내구)
            _res = _ok(f"백로그 {bid} 중단(처리 제외) — 사유가 장부에 남았습니다. 당신이 다음 선정의 "
                       f"담당자입니다: 남은 백로그 보유자들의 사유를 듣고 pick_backlog(id)로 선정하세요.")
            await _flush(flow)
            return _res
        tools.append(drop_backlog)

        @tool("block_backlog",
              "[차단 — 선행 필요] 내 백로그가 **다른 일(선행)이 먼저 돼야** 진행 가능할 때: 이 백로그를 "
              "잠시 보류(blocked, 버리지 않고 보존·나중 재개)하고 순차 릴레이의 자리를 넘긴다 — 정지한 채 "
              "릴레이를 막지 않는 출구다. id=백로그, st=단위 ID/목표, reason=무슨 선행이 필요한가. 중단(drop=완수 불가)과 "
              "다르다: 차단은 선행이 풀리면 재방문한다. waits_for=기다리는 백로그 id(예: B3) — 적어 두면 "
              "그것이 끝나는 순간 이 일감이 자동으로 다시 열린다(비워 두면 이유 문장에서 B번호를 읽는다).",
              {"id": str, "st": str, "reason": str, "waits_for": str})
        async def block_backlog(args):
            from .rule.backlog import BacklogError, handoff_note
            from .rule.milestone import flush_pipeline_notes as _flush, _ckpt as _ck
            ms = next((m for m in (getattr(flow, "milestones", None) or []) if m.status not in ("done", "superseded")), None)
            _sts = [x for x in ms.subtasks if x.status not in ("done", "superseded")] if ms else []
            bid = str(args.get("id") or "").strip()
            _stq = str(args.get("st") or "").strip()
            reason = str(args.get("reason") or "").strip()
            if not bid or not reason:
                return _ok("id와 reason(무슨 선행이 필요한가)이 모두 필요합니다.")
            _hit, _err = _resolve_scoped_backlog(flow, _sts, bid, me_id, _stq)
            if _err:
                return _ok(f"차단 불가: {_err}")
            _tgt, r, b = _hit
            # [무엇을 기다리는지 적는다(2026-07-31, 사용자 지시)] 적어 두면 그것이 끝나는 순간
            # 이 일감이 자동으로 다시 열린다. 안 적었으면 이유 문장에서 B번호를 읽고, 그마저 없으면
            # '지금 일하는 사람들이 손을 비울 때'로 둔다(종전처럼 소진까지 기다리지 않게).
            _wf = str(args.get("waits_for") or "").strip().upper()
            if not _wf:
                _m = _re.search(r"\bB(\d+)\b", reason.upper())
                _wf = f"B{_m.group(1)}" if _m else ""
            _ww = 0
            if not _wf:
                from .rule.backlog import active_backlog_rows as _act
                _others = [int(getattr(x[2], "assignee", 0) or 0) for x in _act(flow)
                           if int(getattr(x[2], "assignee", 0) or 0) != int(me_id)]
                _ww = _others[0] if _others else 0
            try:
                _bl, _deadlock = r.block(int(me_id), bid, int(me_id), reason,
                                         waits_for=_wf, waits_who=_ww)
            except BacklogError as e:
                return _ok(f"차단 불가: {e}")
            handoff_note(flow, r, me_id, "차단됐습니다(선행 대기)")
            _ck(flow)
            _msg = (f"백로그 {bid} 중지(선행 대기 — 보존). "
                    + (f"{_wf}가 끝나면 자동으로 다시 열립니다. " if _wf
                       else "기다리는 작업이 끝나면 자동으로 다시 열립니다. ")
                    + "당신은 그동안 다음 일감을 집으면 됩니다(pick_backlog).")
            if _deadlock:
                _msg += (f" ⚠ 같은 백로그가 {_bl.block_count}회 차단됐습니다 — 접근이 결과를 못 바꾸는 "
                         f"신호입니다. renegotiate_criterion(조건 재협상) 또는 vote_stop(판 접기)을 고려하세요.")
                # [교착신호 구조화(2026-07-26, 대기지점 전수 조사)] 종전엔 이 신호가 **권고 문구뿐**이라
                # 봇이 안 밟으면 같은 차단이 계속 쌓였다(차단→재방문→또 차단). 같은 접근이 두 번 결과를
                # 못 바꿨으면 더 굴리는 건 토큰만 태우는 일 — 파킹 신호를 세워 사람에게 넘긴다
                # (집행은 sys_core 이어가기 루프. 거짓 완료가 아니라 정직한 멈춤이다).
                try:
                    flow._stage_stuck = f"백로그 교착 — {bid}({str(reason or '')[:40]})"[:80]
                    if flow.log:
                        flow.log("backlog_deadlock_parked", backlog=str(bid),
                                 blocks=int(_bl.block_count))
                except Exception:
                    pass
            _res = _ok(_msg)
            await _flush(flow)
            return _res
        tools.append(block_backlog)

        @tool("report_iter",
              "진행 중 주기의 완수조건 실증 결과를 제출한다(검증 참여자 누구나). results=한 줄에 "
              "'조건 | pass/fail | 증거(run 출력 요지)' — **증거 없는 pass는 인정되지 않는다**. "
              "GOAL 최종 잠금 조건은 SYS 검증 challenge 중 run이 발급한 receipt id도 함께 제출해야 하며 "
              "임의 evidence 문자열로는 통과하지 않는다. "
              "target=SubTask id(또는 goal 일부)를 주면 그 SubTask의 검증 — 통과 시 잔여 백로그가 "
              "자동 정리되고 SubTask가 닫힌다. 비우면 마일스톤 검증: 전부 실증되면 wrapup(잔여 정리)로 "
              "넘어가고, 정리가 끝나면 wrapup='done'으로 닫는다. 마감은 사람이 아니라 조건이다.",
              {"results": str, "target": str, "wrapup": str, "receipt": str})
        async def report_iter(args):
            from .rule.milestone import flush_pipeline_notes as _flush
            _r = _ok(rule_report_iter(flow, me_id, args))
            await _flush(flow)
            return _r
        tools.append(report_iter)

        # [결정권자 폐지(2026-07-09, 사용자)] 확정=회의 종결 표결(가결 시 수렴안 자동 등록).
        # set_milestone은 '서기' 표면(누구나 — 표결 없이 열 때·복기 때), 재협상도 누구나(게이트=사람 승인).
        @tool("set_milestone",
              "**마일스톤**(Task의 큰 주기: 목표+완수조건)을 등록한다 — 누구나(서기 역할). 정석은 "
              "meet 회의의 공동 결론 파일(DRAFT.md) 완성·가결로 자동 등록되는 것이고, 이 도구는 그 외 "
              "경로(단독 소형 주기·복기)용. goal=목표 한 줄, criteria='조건 | 실증절차' 줄들. "
              "소망형·실행 불가 조건은 등록이 거부된다. 조건 충족이 주기를 닫는다 — 사람이 아니라.",
              {"goal": str, "criteria": str})
        async def set_milestone(args):
            from .rule.milestone import flush_pipeline_notes as _flush
            _r = _ok(rule_set_milestone(flow, me_id, args))
            await _flush(flow)
            return _r
        tools.append(set_milestone)

        @tool("renegotiate_criterion",
              "[조건 재협상 — 누구나] 완수조건이 환경상 달성 불가일 때의 정식 출구. 정체 경보(진전 "
              "없는 반복)가 뜨면 무한 반복하지 말고 이걸로 올린다. target=조건(desc 일부), reason=왜 "
              "불가능한가. **사람 승인**이 오면 그 조건은 포기(waive)되고 나머지로 주기가 진행된다.",
              {"target": str, "reason": str})
        async def renegotiate_criterion(args):
            from .rule.milestone import flush_pipeline_notes as _flush
            _r = _ok(rule_renegotiate(flow, me_id, args))
            await _flush(flow)
            return _r
        tools.append(renegotiate_criterion)

    @tool("run",
          f"작업공간에서 명령을 실행해 산출물을 직접 검증(빌드/구동/테스트). cwd={flow.workspace or '작업공간 루트'} "
          f"(이 절대경로가 작업공간 — `/workspace` 아님). 60s 제한, "
          "웹 작품은 **실제 브라우저 검증 가능**: playwright의 **chromium·firefox·webkit(Safari 엔진) 셋 다** "
          "설치돼 있습니다(2026-08-02 확인 — 셋 모두 라이브 URL 로드 성공). 다중 브라우저 완수조건은 "
          "이 셋으로 실증하세요 — 시스템 패키지를 손으로 풀 필요가 없습니다. 예: PJT venv의 python -c로 "
          "sync_playwright 페이지 로드→로드시간·콘솔에러·스크린샷 확인('실행됨'과 '사용할 만함'은 다르다). "
          "출력 반환. 서버 구동은 'node server.js & sleep 1; curl -s localhost:3000/'처럼 백그라운드+점검으로 "
          "묶으면 됨 — run이 끝나면 백그라운드 프로세스까지 자동 정리하므로 kill 불필요(다음 run의 포트 충돌 없음). "
          # [동시 실행 격리(2026-07-30)] 검증이 판 시간의 22%다. 여럿이 동시에 돌 수 있으려면
          # 포트와 산출물 경로가 겹치지 않아야 한다 — SYS가 실행마다 빈 포트·고유 폴더를 준다.
          "**동시 실행 안전**: 매 실행에 `$PORT`(빈 포트)·`$ARTIFACT_DIR`(이 실행 전용 폴더)·`$RUN_ID`가 "
          "환경변수로 주어진다. 서버는 `PORT=$PORT node server.js`처럼 그 포트로 띄우고 점검도 "
          "`curl -s localhost:$PORT/`로, 증거는 `$ARTIFACT_DIR/…`에 남겨라 — 그러면 다른 검증과 "
          "같은 시각에 돌아도 서로를 밟지 않는다(고정 포트·고정 경로를 쓰면 동시 실행 시 충돌한다). "
          "파괴·git·시스템경로 명령은 차단. GOAL 잠금 검증 중에는 evidence_for=조건 desc 원문, e2e 중에는 "
          "evidence_for=검사 항목 id를 함께 쓴다. 먼저 seal='yes'로 SYS가 target+spec+현재 artifact에 "
          "exact command를 봉인하고, 다음 호출에서 seal을 비운 채 **동일 명령을 1회** 실행해야 receipt가 "
          "발급된다. true/echo/inline -c·-e/작업공간 밖 test 및 봉인과 다른 명령은 불가.",
          {"command": str, "evidence_for": str, "seal": str})
    async def run(args):
        cmd = str(args.get("command", ""))
        _hold = _clarify_hold(flow, me_id)   # [G2 — clarify 행동 잠금(B-02)] 되묻기 답 오기 전 추측 실행 금지
        if _hold:
            return _ok(_hold)
        if not getattr(flow, "workspace", None):
            return _ok("실행 불가: 작업공간이 설정되지 않았습니다.")
        # [단일활성 구조화 — 논블로킹 핸드오프] 내가 위임을 보내 그 동료가 지금 활성(베턴=동료)인데 내가
        # solo run을 돌리면 '리더+동료 동시 실행'(이중 활성)이 된다. 핸드오프는 request를 즉시 반환하므로
        # 프롬프트가 아니라 구조로 막는다: 내 인플라이트 위임이 살아 있고 내가 비활성이면 run을 거부하고
        # 턴을 마치게 한다 — SYS가 위임을 완주시켜 결과로 나를 재개한다(활성은 언제나 한 명). 동료 자신은
        # 활성(alive==me_id)이라 이 게이트에 안 걸려 자기 작업을 정상 실행한다.
        if (any(not t.done() for t in getattr(flow, "inflight_tasks", ()))
                and flow.comm.alive != me_id and not flow.comm.done):
            return _ok("[대기] 직전 위임이 아직 진행 중입니다 — 지금 직접 실행(run)하면 동료와 동시 작업(이중 "
                       "활성)이 됩니다. 추가 행동 없이 이 턴을 마치세요. 위임이 완료되면 SYS가 그 결과와 함께 "
                       "당신을 다시 깨웁니다(그때 검증·통합하세요).")
        # [백로그 문맥 전수 보장(2026-07-13, 사용자: '백로그 단위로 일하게 설계돼 있는데')] 판(활성 ST의
        # 장부)이 열려 있으면 작업 실행은 백로그 선점 후에만 — 집지 않은 작업이 장부 밖에서 벌어져
        # 검증 시점 소급 등재(빈 완수)를 만드는 구조 구멍을 도구층에서 막는다. 장부가 아직 없는
        # 초기 탐색(협의·GOAL 확정 전)은 자유.
        try:
            from .rule.milestone import pipeline_on as _po
            if _po():
                _ms = next((m for m in (getattr(flow, "milestones", None) or []) if m.status not in ("done", "superseded")), None)
                # [열린 단계 전체 스캔(2026-07-14)] permissions 2.7과 동형 — 첫 열린 ST만 보면 내 백로그가
                # 다른 열린 단계에 있을 때 오거부, 첫 단계 슬롯만 쥐면 도메인 무관 통과. 전 열린 단계를 본다.
                # [빈 장부 구멍 봉쇄(2026-07-13, 라이브 U-013: ST 열림·백로그 0·자유 실행)] ST가 열려
                # 있으면 장부가 비어 있어도 선점 없인 실행 불가 — 안 만들면 영영 안 걸리던 구멍.
                _sts = [x for x in _ms.subtasks if x.status not in ("done", "superseded")] if _ms else []
                _rls = getattr(flow, "backlog_relays", None) or {}
                _mine = any(b.status == "in_progress" and int(b.assignee or 0) == int(me_id)
                            for x in _sts if _rls.get(x.st_id) is not None
                            for b in _rls[x.st_id].backlogs)
                if _sts and not _mine:
                    _r0 = _rls.get(_sts[0].st_id)
                    _cand = " · ".join(f"{b.backlog_id}({b.status[:4]})" for b in (_r0.backlogs[:8] if _r0 else [])) or "(비어 있음)"
                    return _ok(f"[백로그 선점 필요] 열린 단계({len(_sts)}개)가 있으면 작업은 백로그 단위입니다 — "
                               f"pick_backlog(기존 id 또는 desc='이번에 내가 할 일')로 **내 몫을 직접 등재해 집은 뒤** "
                               f"실행하세요. 한 번의 호출로 등재+착수됩니다. 집지 않은 작업은 장부·대화에 남지 "
                               f"않습니다. 현재 장부: {_cand}")
        except Exception:
            pass
        if _COLLAB_RE.search(cmd.lower()):
            # [B-08] 거부에 '어디로 기록하나' 처방 동봉(결정 지점 공급 — permissions 훅과 같은 문구).
            return _ok("실행 거부: 협의 기록(.collab/)은 시스템 소유 — 회의 결론 초안 DRAFT.md만 Edit로 직접 편집 가능하고, 나머지는 meet/vote/보고로만  "
                       "기록됩니다(열람은 Read 도구로).")
        # [작업공간 절대경로 오차단 해소(2026-07-16, ch76: 'ls -la /root/…작업공간'이 /root 패턴에
        # 걸림)] cwd 앵커가 절대경로 사용을 가르치므로, 자기 작업공간 경로는 마스킹 후 위험 패턴 검사.
        _scan = cmd
        try:
            _ws = str(getattr(flow, "workspace", "") or "")
            if _ws:
                _scan = _scan.replace(os.path.realpath(_ws), " ").replace(_ws, " ")
        except Exception:
            pass
        if any(d in _scan.lower() for d in _RUN_DENY):
            return _ok(f"실행 거부(안전): 파괴/저장소/시스템 패턴 포함 — {cmd[:80]}")
        # [이미 있는 것을 다시 받지 않는다(2026-07-31, U-442 실측)] QA가 작업공간에 playwright 브라우저
        # (646MB)와 pip 패키지(140MB)를 새로 내려받아 12분을 태웠다 — 이 판엔 둘 다 이미 있고 샌드박스가
        # 읽기전용으로 물려준다(PLAYWRIGHT_BROWSERS_PATH=공유 캐시, PATH=공유 venv).
        _already = _preinstalled_refusal(cmd, _ws)
        if _already:
            return _ok(_already)
        if any(p in cmd for p in _RUN_AUTHOR):
            return _ok("실행 거부: run은 '실행·빌드·검증' 전용입니다 — 파일 작성/수정은 Write/Edit 도구로 "
                       "하세요(그래야 권한·협의 게이트가 적용되고 누가 무엇을 만들었는지 기록됩니다). 예: "
                       "server.js 작성은 Write, 패키지 설치·서버 구동·curl 점검은 run. 남의 도메인 산출물을 "
                       "run으로 대신 찍어내지 말고 그 owner에게 Work로 위임하세요.")

        _seal_requested = str(args.get("seal") or "").strip().lower() in (
            "yes", "true", "1", "seal", "봉인",
        )
        if _seal_requested:
            return _ok(_seal_verifier_command(
                flow, me_id, args.get("evidence_for"), cmd))
        _seal_meta = None
        if str(args.get("evidence_for") or "").strip():
            _seal_meta, _seal_error = _authorize_sealed_verifier_run(
                flow, me_id, args.get("evidence_for"), cmd)
            if _seal_error:
                return _ok(_seal_error)

        def _exec():
            # 자체 세션(프로세스그룹)으로 실행 → 직속 셸 종료 후 그룹째 정리한다.
            of, ef = tempfile.TemporaryFile(), tempfile.TemporaryFile()
            argv, env, prep_error = _prepare_run_exec(flow.workspace, cmd)
            if prep_error:
                of.close(); ef.close()
                raise RuntimeError(prep_error)
            p = subprocess.Popen(argv, shell=False, cwd=str(flow.workspace),
                                 stdout=of, stderr=ef, start_new_session=True,
                                 env=env)
            timed_out = False
            try:
                rc = p.wait(timeout=60)
            except subprocess.TimeoutExpired:
                timed_out, rc = True, None
            finally:
                _reap_pgroup(p.pid)
                try:
                    p.wait(timeout=2)
                except Exception:
                    pass
            of.seek(0); ef.seek(0)
            out = of.read().decode("utf-8", "replace")
            err = ef.read().decode("utf-8", "replace")
            of.close(); ef.close()
            return timed_out, rc, out, err

        # [검증 산출 등재(2026-07-27, U-067)] 검증기가 실행마다 덮어쓰는 리포트가 authoring
        # manifest에 섞이면 검증이 자기 영수증을 무효화한다(rule/milestone.record_run_outputs).
        # **회의가 검증 수단으로 결속한 명령일 때만** 등재한다 — 임의 run은 종전대로 스탬프에 잡힌다.
        _rro = None
        _pre_files = None
        try:
            from .rule.milestone import (known_verifier_commands as _kvc,
                                         record_run_outputs as _rro0,
                                         workspace_file_digests as _wfd)
            from .rule.evidence import normalize_verifier_command as _nvc
            if _nvc(cmd) in _kvc(flow):
                _rro, _pre_files = _rro0, _wfd(flow)
        except Exception:
            _rro = None
            _pre_files = None
        _shared_note = ""
        try:
            cmd, _shared_note = prefer_shared_browsers(cmd, _ws)
        except Exception:
            _shared_note = ""
        _repeat_note = ""
        try:
            from .rule.milestone import workspace_artifact_stamp as _was
            _repeat_note = run_repeat_note(flow, cmd, _was(flow))
        except Exception:
            _repeat_note = ""
        try:
            timed_out, rc, out, err = await anyio.to_thread.run_sync(_exec)
        except Exception as e:
            return _ok(f"실행 오류: {e}")
        if _rro is not None and _pre_files is not None:
            try:
                _rro(flow, _pre_files)
            except Exception:
                pass
        if timed_out:
            _dbg(f"[RUN] {me_id} `{cmd[:60]}` TIMEOUT")
            return _ok("실행 시간초과(60s) — 그룹째 정리함. 서버는 'node server.js & sleep 1; curl ...'처럼 "
                       "백그라운드로 띄우세요(포그라운드로 서버를 실행하면 멈춥니다). **큰 단일 다운로드/빌드"
                       "(수백MB+ 도구·모델)는 60초에 안 끝납니다 — 작은 패키지·에셋으로, 또는 닿는 경량 대안으로 "
                       "갈아타세요(이 환경엔 GPU 없음·Render는 Node-웹 전용).\n"
                       f"[부분 stdout]\n{out[-800:]}\n[부분 stderr]\n{err[-400:]}")
        _dbg(f"[RUN] {me_id} `{cmd[:60]}` exit={rc}")
        if flow.current is not None:
            flow.current.verified = True          # 실행 0회 완료 차단(layer1)
            flow.current.run_count += 1
            # 시스템이 직접 캡처한 영수증(에이전트 말이 아니라 실제 출력). 완료 보고에 떼어낼 수 없게 묶인다.
            errtail = ("\n[stderr] " + err[-200:]) if (err or "").strip() else ""
            flow.current.evidence = f"exit={rc} `{cmd[:50]}`\n{(out or '')[-400:]}{errtail}"
        _receipt_required = bool(getattr(flow, "_release_verify_challenge", None)
                                 or getattr(flow, "_e2e_receipt_nonce", None))
        try:
            _receipt_id = _issue_run_receipt(
                flow, me_id, cmd, rc, out, err,
                evidence_for=args.get("evidence_for"),
                seal_meta=_seal_meta,
            )
        except Exception:
            _receipt_id = ""
        # [기동증명 코칭 — 백그라운드 시작만 하고 끝내는 실수 감지(라이브 P-005: 백엔드가 server.js를 다음 run에서
        # curl하려고 별도 run에 `node server.js … &`로 띄웠다 reap돼 죽은 서버에 curl→무한 헤맴)] 명령이 *끝의
        # 단일 `&`로 백그라운드 시작*이면(뒤에 점검 없음), 이 프로세스는 run 종료 시 그룹째 정리돼 다음 run엔
        # 없다 → 그 자리에서 올바른 '한 run 묶기' 패턴을 처방한다(추측·재시도 루프 차단).
        _c = cmd.strip()
        _bg_only = _c.endswith("&") and not _c.endswith("&&")
        _hint = _repeat_note + (("\n\n" + _shared_note) if _shared_note else "")
        if _bg_only:
            _hint += ("\n\n⚠ 끝의 `&`로 띄운 백그라운드 프로세스(서버 등)는 **이 run이 끝나며 그룹째 정리**됐습니다 "
                     "— 다음 run엔 살아있지 않습니다(run 간 포트충돌 방지 설계). 서버 **기동증명은 반드시 한 run "
                     "안에** start→대기→점검을 묶으세요: `node server.js & sleep 1; curl -s 127.0.0.1:$PORT/헬스경로 "
                     "&& curl -s -X POST 127.0.0.1:$PORT/api/…` (별도 run으로 나누면 서버가 죽어 curl이 붙지 못합니다).")
        _receipt_note = (
            f"\n[SYS run receipt] {_receipt_id}" if _receipt_id
            else ("\n[SYS run receipt 발급 실패 — target에 봉인된 exact verifier command를 "
                  "현재 산출물에서 단일사용 실행해야 함]"
                  if _receipt_required else "")
        )
        return _ok(f"[exit {rc}] (작업공간)\n[stdout]\n{out[-1500:]}\n[stderr]\n{err[-600:]}"
                   f"{_receipt_note}{_hint}")

    tools.append(run)

    if mode == "casual":
        # [G3 — B-06] 일상 대화 턴: run(사실 확인·간단 실행)만 장착 — request·recruit·리더도구 없음.
        # 캐주얼 오분류로 프로젝트·회의가 열리는 것을 도구 부재로 기계 차단(프롬프트 지시 의존 제거).
        return [run]

    if role != "leader":
        # [B-14 — report 도구(스태시형·이중 수용, BOT_ARCH_REDESIGN 2026-07-03)] 멤버 세션 장착. 구조화
        # 보고 필드를 시스템에 *스태시*할 뿐 — **Response는 여전히 턴 반환값**(모듈 docstring '보고=반환값'
        # 원칙 유지). 소비: offdomain_role은 [직군밖] 첫줄 regex보다 우선(_deliver, regex 폴백 존치),
        # experience/craft_standard는 [경험]/[직무기준] 블록과 같은 흡수 경로(run_turn, regex 폴백 존치),
        # result/changes/verify/risks는 REPORTS.md에 구조화 동봉. 미사용 봇은 종전 동작(이중 수용 — 무중단).
        @tool("report",
              "작업 보고의 구조화 필드를 시스템에 기록(선택) — 보고 본문은 여전히 턴 반환값(Response)으로 "
              "합니다. result=한 줄 결론(완료/부분/실패), changes=파일·핵심 변경, verify=검증 방법→결과, "
              "risks=남은 것·주의점. offdomain_role=이 일이 당신 직군 밖이면 필요 직군명(반려 신호), "
              "experience=이번 작업의 직군 차원 교훈 1~2줄, craft_standard=직무 기준 갱신(있을 때만).",
              {"result": str, "changes": str, "verify": str, "risks": str,
               "offdomain_role": str, "experience": str, "craft_standard": str})
        async def report(args):
            if getattr(flow, "report_stash", None) is None:
                flow.report_stash = {}
            flow.report_stash[me_id] = {
                k: str(args.get(k) or "").strip()
                for k in ("result", "changes", "verify", "risks",
                          "offdomain_role", "experience", "craft_standard")}
            return _ok("보고 필드가 기록되었습니다 — 이어서 같은 결론을 Response(턴 반환값)로 간결히 "
                       "보고하며 턴을 마치세요(이 도구가 보고를 대신하지 않습니다).")
        tools.append(report)

        if me_id in (getattr(flow, "fork_kind", None) or {}):
            # [B-15 — cast_vote 도구(fork 가지 세션 전용)] flow.fork_kind[m]은 wake 전에 세팅되므로 서버
            # 빌드 시점에 가지 식별 가능. 표는 인자로 정확히 — [표] regex 수합은 폴백 존치(이중 수용).
            @tool("cast_vote",
                  "표결 가지에서 투표를 기록 — option=선택지명(안건의 선택지 문구 그대로), reason=근거 "
                  "1~2줄. 호출 후 같은 근거를 Response로 간결히 반환하며 턴을 마치세요.",
                  {"option": str, "reason": str})
            async def cast_vote(args):
                opt = str(args.get("option") or "").strip()
                if not opt:
                    return _ok("오류: option(선택지명)이 비었습니다 — 안건의 선택지 문구를 그대로 적어주세요.")
                if getattr(flow, "vote_stash", None) is None:
                    flow.vote_stash = {}
                flow.vote_stash[me_id] = {"option": opt, "reason": str(args.get("reason") or "").strip()}
                return _ok(f"투표 기록됨: {opt} — 근거를 Response로 간결히 반환하며 턴을 마치세요.")
            tools.append(cast_vote)

    # [배포 탈중앙화(2026-07-08, 사용자: '리더만 배포권은 말도 안 되는 중앙집권')] deploy는 리더 전용이
    # 아니라 **모든 협업 멤버**에게 준다 — 검증을 끝낸 사람(대개 산출물 owner)이 직접 공개한다. 리더 전용이던
    # 탓에 워커가 수정을 끝내고도 '배포 권한 아무도 없어?'로 빙빙 돌던 교착의 근본. 보안(키 인프로세스라
    # 봇이 키를 못 읽음)·런어웨이(배포 캡·anti-thrash)는 *누가* 부르든 그대로 작동하므로 리더 독점 이유 없음.
    @tool("deploy",
          "검증을 마친 산출물을 실제로 공개 배포한다(GitHub push + Render 웹서비스 생성/갱신). "
          "name=영문 소문자·하이픈 서비스명(예: slither-multiplayer). 라이브 URL을 반환. "
          "Node 앱이어야 하고 서버는 process.env.PORT를 사용해야 함. run 검증을 끝낸 뒤 마지막에 호출. "
          "검증을 끝낸 누구나(대개 owner) 직접 배포한다 — 남에게 넘기려 멈추지 말 것. "
          "note=이번 배포의 계기·변경 한 줄(필수에 준함 — 피드에 '누가 왜 배포했나'로 남습니다).",
          {"name": str, "note": str})
    async def deploy(args):
        return await _rule_deploy(flow, args, me_id=me_id)
    tools.append(deploy)

    # [atelier(P0 B-2, 2026-07-13)] 공유 판(atelier)에 남기는 문 — deploy의 Render처럼 외부 독립
    # 서비스 클라이언트(매체 아님 — 매체중립 무관). 사용은 Organt의 선택(강제·자동 없음): 산출물
    # 설명·검증 증거를 남기거나, 판에서 승격돼 온 요청([atelier 핀 #N])을 끝냈을 때 마감 회신.
    # env(ATELIER_URL/ATELIER_TOKEN) 미설정이면 호출해도 안내만 — 협업 흐름은 막지 않는다.
    @tool("atelier",
          "사람과 같이 쓰는 공유 캔버스(atelier) — 필요하다고 판단될 때만. "
          "op=read: 판 읽기(project,canvas) — 무엇이 있고 어떻게 연결됐고 어떤 핀·검증 판정이 있는지 "
          "요약문으로 돌아온다(작업 전 맥락 파악·사람 피드백 확인용). "
          "op=note: 스티키 한 장(project,canvas,text — 산출물 설명·검증 증거·설계 메모). "
          "op=shot: 실화면 라이브 조각(project,canvas,url,text=제목,sel=CSS선택자(선택) — 배포/구현한 "
          "화면을 판에 품는다, 캡쳐 아님). "
          "op=done: 'atelier 핀'이 붙은 사람 요청을 끝낸 뒤 마감 회신(pin=요청문 [atelier 핀 #N]의 N, "
          "text=처리 한 줄). project=판 이름(요청문의 '판에서 보기' 주소 /p/<이름>/ 참조, 예: murmur), "
          "canvas=시트 이름(없으면 생성, 예: 검증-증거).",
          {"type": "object",
           "properties": {"op": {"type": "string", "enum": ["read", "note", "shot", "done"]},
                          "project": {"type": "string"}, "canvas": {"type": "string"},
                          "text": {"type": "string"}, "url": {"type": "string"},
                          "sel": {"type": "string"}, "pin": {"type": "string"}},
           "required": ["op"]})
    async def atelier(args):
        from . import atelier_client as _atl

        def _go():
            op = str(args.get("op") or "")
            pj = str(args.get("project") or "").strip()
            cv = str(args.get("canvas") or "메모").strip()
            tx = str(args.get("text") or "").strip()
            if op == "done":
                return _atl.done(str(args.get("pin") or ""), tx)
            if not pj:
                raise RuntimeError("project(판 이름)가 필요합니다 — 예: murmur")
            if op == "read":
                return _atl.read(pj, cv)
            if op == "shot":
                u = str(args.get("url") or "").strip()
                if not u:
                    raise RuntimeError("shot은 url(품을 실화면 주소)이 필요합니다")
                return _atl.shot(pj, cv, u, str(args.get("sel") or ""), tx)
            if not tx:
                raise RuntimeError("note는 text(남길 내용)가 필요합니다")
            return _atl.note(pj, cv, tx)

        try:
            return _ok(await anyio.to_thread.run_sync(_go))
        except Exception as e:   # 판 장애가 협업을 막으면 안 됨 — 실패는 안내로만
            return _ok(f"atelier 실패: {e}")
    tools.append(atelier)

    if role == "leader":
        @tool("create_project",
              "Project로 판단되면 전용 채널 생성 + 규모를 산정해 팀 배정"
              "(team=쉼표구분 동료 id/역할명, 본인 제외분). 비우면 풀 전체.",
              {"name": str, "team": str})
        async def create_project(args):
            # [도구=얇은 래퍼] 로직은 rule/project.py(Project Rule)에 — @tool은 계약·표현만, 규칙은 rule/가 소유(§7 복원)
            return _ok(await _rule_create_project(flow, args))
        tools.append(create_project)

        @tool("create_task",
              "Task '빈 껍데기'를 연다 — **Purpose도 비운 채 팀만 확정**한다(개인이 할 일을 미리 못 박음 = 중앙집권 "
              "방지). 이후 **배정된 팀이 모여(request Info) Purpose(풀 문제)·Goal(성공기준)을 함께 정해 set_goal로 "
              "확정**한다 — 이때 **각 직군 전문가가 *자기 도메인*의 Task·소유를 직접 제안**하게 하라(남의 "
              "도메인을 정하지 말 것 — 전문가가 자기 분야를 정의). Owner는 그 일을 Work로 받은 동료가 된다(선배정 "
              "금지). **members=이 일에 필요한 직군 동료를 당신이 직접 고른다**(자동 전원 소집 아님 — 직군 고정 방지) — "
              "고를 때 **각 동료의 누적 경험·강점(직무 기준)을 살려** 적임자에게 맡겨라. 비우면 프로젝트팀 "
              "기본, 모자란 직군은 recruit(role=)로 채운다(그 직군 전문가가 즉석 생성돼 합류).",
              {"members": str})
        async def create_task(args):
            # [도구=얇은 래퍼] 로직은 rule/task.py(Task Rule)
            return _ok(await _rule_create_task(flow, args))
        tools.append(create_task)

        @tool("set_goal",
              "팀 회의로 정한 이번 Task의 **Purpose(풀 문제)와 Goal(측정가능한 성공기준)**을 확정·기록한다. 개인 "
              "단독/선지정 금지 — **이 Task의 멤버 전원**과 meet(회의)로 'Purpose·각 도메인의 목표·성공기준'을 "
              "수렴한 결과를 적는다(1:1 request(Info)보다 meet 권장 — 앵커링↓·회의록 자동 기록). Goal엔 '무엇이 "
              "되면 성공인가'(결과·시나리오)만 쓰고 '어떤 파일·엔드포인트·스택으로 만들지'(구현 방법)는 쓰지 말 것 — "
              "그건 owner가 정한다(단, **각 산출물·파일은 정확히 한 도메인이 소유하도록 계획** — 이중 배정 금지; "
              "통합 파일(엔트리 HTML 등)도 단일 owner를 정하고 타 도메인은 그 owner에게 통합 요청한다. *먼저 만든 "
              "자가 가지는* 게 아니라 *도메인 책임자가* 소유한다). Work 위임은 확정 뒤에만 가능. acceptance(수용 "
              "계약)엔 회의에서 각 전문가가 제안한 '좋음의 구체·검증가능 조건'(훌륭한 예 대비)을 항목으로 적되, "
              "**반드시 '존재이유 테스트' 1개 이상**(이 산출물이 *진짜 그것*임을 증명하는 전체·부정형 검증 — 실패하면 "
              "핵심 목적이 깨지는 것)을 포함한다. 예: 2인 협동게임='솔로 플레이어로는 클리어 불가', 추천='무관 질의엔 "
              "상위가 달라짐', 인증='틀린 토큰은 거부'. 부품 체크(버튼 있나·이벤트 발화하나)만 적으면 *부품은 통과인데 "
              "전체는 목적 미달*인 산출물이 마감된다 — 마감이 이 항목들(특히 존재이유 테스트)의 실현을 검증한다. "
              "게이트 면제 인자(종전 마커와 동등): maximal_na(최대화 N/A 사유)·staffing_waiver(스태핑 면제 "
              "이유)·depth_solo(심도 단독 — 능력·사유). team_check=구성 점검 합의 결론(필수 게이트 — "
              "'추가 직군 불필요 — <사유>' 또는 '<직군> 부족 → recruit 예정').",
              {"purpose": str, "goal": str, "acceptance": str, "standard": str, "interfaces": str,
               "existence_test": str, "maximal_na": str, "staffing_waiver": str, "depth_solo": str,
               "team_check": str})
        async def set_goal(args):
            return await _rule_set_goal(flow, me_id, role, args)
        tools.append(set_goal)

        # [결정권자 폐지(2026-07-09)] set_milestone·renegotiate_criterion은 공통 구역(위 recruit 옆)으로
        # 이동 — 확정=종결 표결, 등록=서기(누구나), 재협상=누구나(게이트=사람 승인).

        @tool("vote",
              "팀 표결(구조적 합의): 선택지를 두고 멤버 전원의 선택+근거를 **동시에**(독립·앵커링 방지) "
              "수집·집계한다. question=안건, options='선택지1;선택지2;...', members=쉼표구분(비우면 현재 "
              "Task 팀 전원). 1:1 Info를 여러 번 도는 대신 합의를 구조화 — 결과(집계+근거)를 보고 소집자가 정리한다.",
              {"question": str, "options": str, "members": str})
        async def vote(args):
            return _ok(await _rule_vote(flow, me_id, args))
        tools.append(vote)

        @tool("vote_stop",
              "[중지 투표] 해결 불가한 판을 봇 혼자가 아니라 팀 표결로 접는다 — 백로그를 다 돌아도 "
              "마일스톤을 충족 못 하고 접근이 결과를 못 바꿀 때의 구조적 출구. target='milestone'(진행 중 "
              "마일스톤만 종결) 또는 'task'(Task 통째 — 사람 승인 상신). reason=왜 해결 불가한가. "
              "과반(도메인 관점) 찬성 시 실행. (조건 1개만 불가면 renegotiate_criterion을 쓰세요.)",
              {"target": str, "reason": str})
        async def vote_stop(args):
            return _ok(await _rule_vote_stop(flow, me_id, args))
        tools.append(vote_stop)

        @tool("meet",
              ("완전 turn-taking 회의(§4): 소집자가 주제+자기 의견을 발제하면 매 발언권이 응찰"
               "([응찰: N])로 돌아간다 — 강제 라운드 없음. topic=주제, members=쉼표구분(비우면 현재 Task "
               "팀 전원), rounds=발언 예산 배수(기본 2). my_opinion=당신(소집자)의 독립 의견(필수) — 당신도 "
               "중재자가 아니라 한 참여자다. **회의는 공동 결론 파일(DRAFT.md)이 완성·가결돼야 끝난다** — 각자 "
               "자기 몫을 직접 편집·이의·해소하고, 빈 곳·이의 0이면 전원 표결로 그 파일이 결론이 된다(결정권자·개인 "
               "set_goal/set_milestone 없음 — 폐지). 단계(GOAL/마일스톤/서브태스크/백로그)는 시스템이 상태에서 "
               "정해 다음 회의를 자동으로 연다.") if _pipe_on() else
              ("라운드로빈 회의: 1라운드는 전원의 '독립 의견'을 동시에 수집하고(앵커링 방지), 2라운드부터 "
               "서로의 발언을 보며 직렬로 토론한다(회의록 반환). topic=주제, members=쉼표구분(비우면 현재 "
               "Task 팀 전원), rounds=라운드 수(기본 2). **my_opinion=당신(소집자)의 독립 의견(필수) — "
               "당신도 중재자가 아니라 한 참여자로 자기 도메인 관점을 낸다**. 1:1 중계 없이 실제 다자 토론을 "
               "구조화 — 회의록을 보고 수렴·확정한다."),
              {"topic": str, "members": str, "rounds": str, "my_opinion": str})
        async def meet(args):
            return _ok(await _rule_meet(flow, me_id, args))
        tools.append(meet)

        @tool("parallel_work",
              "파일 영역이 겹치지 않는 **독립 Work 여러 건을 동시에** 위임(병렬 실행+직렬 통합, RFC-006). "
              "assignments=JSON 배열 '[{\"to\":\"봇id\",\"files\":\"상대경로,상대경로\",\"body\":\"지시\"}]'. "
              "각자 배정된 files에만 쓸 수 있다(쓰기 리스 — 영역 겹침은 거부). 영역이 겹치거나 순서 의존이면 "
              "request(Work) 직렬로. 조인 후 통합·검증·마감은 직렬로 진행.",
              {"assignments": str})
        async def parallel_work(args):
            return _ok(await _rule_parallel_work(flow, me_id, args))
        tools.append(parallel_work)



        @tool("list_projects",
              "회사가 진행/배포해 온 프로젝트 전체 목록(P-번호·이름·요약)을 조회 — 신규성 판단·중복 회피·"
              "기존 작품 이어가기 판단에 사실 근거가 더 필요할 때(프롬프트의 회사 이력은 최근 일부만).",
              {})
        async def list_projects(args):
            # [B-18③ — _portfolio_note push(16건 캡) 유지 + pull '보강'(BOT_ARCH_REDESIGN 2026-07-03).
            # pull '전환'은 기각(A-8: 결함 원인이 '몰라서 못 물음') — 이 도구는 캡 밖 전체 조회용 보강.
            fn = getattr(flow, "projects_provider", None)   # SYS 주입(없으면 빈 목록 — 테스트·비등록 무해)
            try:
                rows = list(fn() or []) if callable(fn) else []
            except Exception:
                rows = []
            if not rows:
                return _ok("(등록된 프로젝트가 없습니다)")
            rows.sort(key=lambda p: str(p.get("id") or ""))
            lines = []
            for p in rows:
                gist = (str(p.get("summary") or p.get("purpose") or "").strip().replace("\n", " "))[:100]
                name = (p.get("name") or "").strip()
                label = f"{name} ({p.get('id')})" if name else str(p.get("id") or "?")
                lines.append(f"- {label}" + (f" — {gist}" if gist else ""))
            return _ok(f"[회사 프로젝트 이력 — 전체 {len(lines)}건]\n" + "\n".join(lines))
        tools.append(list_projects)

        @tool("send_file",
              "산출물 파일을 사용자에게 Discord 첨부로 보낸다 — 사용자가 '파일로 받고 싶다'고 했거나 산출물이 "
              "파일 형태(이미지·문서·데이터·코드 번들 등)일 때만(항시 보내지 말 것). path=작업공간 기준 상대경로, "
              "caption=한 줄 설명(선택). 25MB 이하만 — 큰 건 deploy(배포 URL)로.",
              {"path": str, "caption": str})
        async def send_file(args):
            return await _rule_send_file(flow, me_id, args)
        tools.append(send_file)

    # [e2e 마무리 — S3 도구 표면(PIPELINE_REWORK §6)] 전 멤버 장착 — Task 경계 개시는 현장의 몫
    # (마지막 작업자/QA, §3 관례와 동형). 플래그 ON에서만 등록(OFF 라이브는 도구 자체가 없다 — 동작
    # 불변). 로직은 rule/wrapup.py(매체중립): 분모(체크리스트)·판정·복기는 구조가, 검사는 봇이.
    if _pipe_on():
        @tool("e2e_open",
              "Task 경계(모든 마일스톤 done)에서 **전수 e2e 검증을 개시**한다 — 전 마일스톤의 완수조건"
              "(최종 버전 재실증)은 회의 비준/이전 SYS 실증으로 고정된 exact verifier만 재사용한다. "
              "사용자 원문은 무관한 suite로 별도 pass 처리하지 않고 검증 scope 해석 컨텍스트로 보존한다. "
              "개시 후: 산출물의 노출 표면을 e2e_scope로 추가하고, 각 항목을 실제 실행으로 검사해 "
              "e2e_result로 제출하라.",
              {})
        async def e2e_open(args):
            return _ok(rule_e2e_open(flow))
        tools.append(e2e_open)

        @tool("e2e_scope",
              "e2e 분모 확장 — 산출물을 열어 파악한 **노출 표면**(surfaces: 페이지·라우트·API·명령, "
              "한 줄에 `검사 설명 || exact verifier command`)과 **주 사용 경로**(arcs: 같은 형식의 "
              "실기동 관통 시나리오)를 제출한다. inline assert/print나 무관한 성공 명령은 봉인되지 않는다. "
              "추가된 항목 id가 반환된다 — 이 목록이 '전수'의 분모가 되므로 아는 표면을 빠뜨리지 마라.",
              {"surfaces": str, "arcs": str})
        async def e2e_scope(args):
            return _ok(rule_e2e_scope(flow, args))
        tools.append(e2e_scope)

        @tool("e2e_result",
              "e2e 항목 하나의 검사 결과 제출. item=항목 id(예: condition:1), ok=pass/fail, "
              "observed=관측한 것 한 줄, evidence=**실행 증거**(run 출력·브라우저 확인 요지 — 증거 없는 "
              "pass는 결함으로 판정된다). pass면 e2e_open 뒤 해당 검사를 실행한 run 응답의 "
              "`[SYS run receipt]` id를 receipt에 반드시 붙인다(항목마다 새 영수증, 재사용 불가).",
              {"item": str, "ok": str, "observed": str, "evidence": str, "receipt": str})
        async def e2e_result(args):
            return _ok(rule_e2e_result(flow, args, me_id=me_id))
        tools.append(e2e_result)

        @tool("e2e_finish",
              "전 항목 제출 후 판정 — 전부 '증거 있는 pass'면 e2e_pass(Task 마무리 가능), 아니면 "
              "e2e_fail: 결함 목록으로 복기 마일스톤이 자동 개설된다(결함 해소가 완수조건 초안, "
              "확정은 회의). 미제출 항목은 '검사 안 됨' 결함이 된다.",
              {})
        async def e2e_finish(args):
            return _ok(rule_e2e_finish(flow))
        tools.append(e2e_finish)

    if mode == "e2e":
        # [Task 경계 전용 표면] 저비용 모델이 완료된 백로그를 다시 읽고 수정하거나 협업 도구로
        # 새 일을 벌이지 못하게, 이미 SYS가 연 e2e 장부를 판정하는 네 도구만 장착한다. Codex
        # 브리지도 e2e_result 표식 때문에 receipt용 run을 보존한다.
        allowed = {"run", "e2e_scope", "e2e_result", "e2e_finish"}
        return [candidate for candidate in tools if candidate.name in allowed]

    if mode == "close":
        # [마감 전용 표면(2026-07-27, U-067 실측)] 마감 관문 앞에서 봇들이 **할 일을 정확히 말하고
        # 아무도 부르지 않았다** — 6턴 연속 "마감하면 됩니다"만 남고 호출 0회(도구는 26개 노출돼
        # 있었다). 같은 판의 e2e 단계는 표면을 그 일에 필요한 것만 남겨 실제로 도구를 쓰게 했다.
        # 같은 방식: 산출물을 확인하는 run과 마감만 남긴다 — 논의가 선택지로 남아 있지 않게.
        # (관문이 요구하는 회계·의식적 드롭은 complete_task의 result에 담긴다.)
        allowed = {"run", "complete_task"}
        return [candidate for candidate in tools if candidate.name in allowed]

    # [완료 권한 = 검수 역할(사용자 2026-07)] acceptance/'done' 판정은 QA의 일 — 종전엔 리더가 독점(complete_task
    # 리더 전용)했다. 리더의 역할은 기획·위임·조율이지 검수가 아니라, QA/PM이 '인수 PASS'로 판정해도 닫을 권한이
    # 없어 계속 검사만 하고 리더는 닫을 권한이 있는데 검증자가 아니라 계속 위임만 하는 무한 루프였다(라이브
    # P-005: QA 오은우·PM 유찬영이 인수 PASS 선언했는데 complete_task 0회, 162턴 무한). 완료 권한을 검수 역할
    # (QA)로 이관 — 검수자가 자기 검수 결과로 직접 마감(리더 독점도 SYS 강제도 아닌 탈중앙). 팀에 QA 없으면
    # 리더 폴백(옵션2, 마감 불능 방지). role별 세션이라, 이 봇이 마감권 보유자면 도구 장착.
    if _holds_completion(flow, me_id, role):
        @tool("complete_task",
              "현재 Task의 목표가 충족되면 상태블록을 완료로 마감(result 기록). 마감 전 acceptance의 **'존재이유 "
              "테스트'를 최종 사용자처럼 end-to-end로 실제 실행**해 통과 증거를 result에 남겨라 — 부품이 *있는지*가 "
              "아니라 *전체가 목적을 달성하는지*(부정형 테스트가 실제로 실패를 막는지)를 본다. 다음 Task는 create_task로. "
              "게이트 회계/면제 인자(종전 result 마커와 동등): percept_na(지각차원 없음 사유)·visual_evidence(시각 "
              "검증 — 무엇이 보였나)·data_source(데이터 출처/불가 사유)·acceptance_check(수용기준 항목별 회계)·"
              "standard_check(최대성 항목별 회계)·contrib_waiver(기여 불필요 이유).",
              {"result": str, "percept_na": str, "visual_evidence": str, "data_source": str,
               "acceptance_check": str, "standard_check": str, "contrib_waiver": str})
        async def complete_task(args):
            _res = await _rule_complete_task(flow, role, args)
            # [거절 사유 관측(2026-07-27)] 마감 관문의 거절문은 봇에게만 돌아가 로그에 안 남아,
            # 왜 안 닫히는지 알려면 매번 판을 태워야 했다(U-065: 여섯 겹을 판마다 하나씩 벗김).
            # 마감이 안 난 경우의 사유 첫 줄만 남긴다 — 진단이 판 비용에 묶이지 않게.
            try:
                if flow.current is not None and flow.log:
                    _txt = _res
                    if isinstance(_txt, dict):
                        _txt = ((_txt.get("content") or [{}])[0] or {}).get("text", "")
                    _txt = str(_txt or "").strip().splitlines()[0] if str(_txt or "").strip() else ""
                    if _txt:
                        flow.log("complete_task_refused", why=_txt[:180])
            except Exception:
                pass
            return _res
        tools.append(complete_task)

    return _audited(tools, me_id, role)


def build_guide_server(flow: Flow, me_id: int, role: str, mode: str = "collab"):
    return create_sdk_mcp_server("guide", "1.0.0", make_guide_tools(flow, me_id, role, mode=mode))
