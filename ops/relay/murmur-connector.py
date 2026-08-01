#!/usr/bin/env python3
"""murmur 커넥터 — 집에서 돌리는 LLM을 murmur에 붙인다 (2026-07-31, 현준-4).

쓰는 법:

    MURMUR_TOKEN=... python3 murmur-connector.py

  선택:
    MURMUR_URL   기본 https://murmur.dojin-mini.shop
    LLM_URL      기본 http://127.0.0.1:11434/v1/chat/completions  (Ollama)
    LLM_KEY      로컬 LLM이 키를 요구하면
    MURMUR_HUMAN 1이면 사람이 답한다 — 프롬프트를 보여 주고 타이핑을 기다린다
    MURMUR_CMD   OpenAI 모양이 아닌 LLM을 붙일 때. 이 명령을 실행하고 stdin으로 프롬프트를
                 넣어 stdout을 답으로 읽는다. 예: MURMUR_CMD="python3 my_llm.py"

응답자가 무엇이든 상관없다. 이 커넥터가 하는 일은 요청을 가져와 답을 돌려주는 것뿐이고,
그 사이에 무엇이 있는지는 murmur가 알지 못한다 — Ollama든, 다른 API든, 사람이든.
사람이 답하려면 설정에서 마감(reply_deadline_sec)을 넉넉히 잡아 두면 된다.

왜 이렇게 하나:
  공인 주소도 인증서도 없는 사람이 대부분이다. 이 커넥터는 **밖에서 안으로** 들어오지
  않고 **안에서 밖으로** 붙는다. 방화벽을 열 필요도, 인증서를 받을 필요도 없다.
  murmur가 남의 주소를 부르지 않으므로 SSRF 표면도 생기지 않는다.

무엇을 지키나:
  · 토큰은 환경변수로만 받는다. 명령줄에 두면 ps로 옆 사람에게 보인다.
  · LLM 주소는 로컬만 기본값이다. 바깥 주소를 넣는 것은 사용자 선택이지 기본이 아니다.
  · 실패해도 계속 돈다. 한 번의 오류로 커넥터가 죽으면 사람이 다시 켜야 한다.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

MURMUR = os.environ.get("MURMUR_URL", "https://murmur.dojin-mini.shop").rstrip("/")
TOKEN = os.environ.get("MURMUR_TOKEN", "").strip()
LLM_URL = os.environ.get("LLM_URL", "http://127.0.0.1:11434/v1/chat/completions")
LLM_KEY = os.environ.get("LLM_KEY", "").strip()
HUMAN = os.environ.get("MURMUR_HUMAN", "").strip() in ("1", "true", "yes")
CMD = os.environ.get("MURMUR_CMD", "").strip()
CMD_TIMEOUT = int(os.environ.get("MURMUR_CMD_TIMEOUT", "600"))
_spec_qs = ""
IDLE_SLEEP = 2.0


def _post(url, body, headers=None, timeout=120):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _get(url, headers=None, timeout=30):
    req = urllib.request.Request(url)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _prompt_text(req):
    """요청에서 사람이 읽을 프롬프트를 뽑는다.

    OpenAI 모양(messages)이 표준이지만, 그것을 모르는 응답자에게는 그냥 글로 준다 -
    모양을 아는 것은 커넥터의 일이고, 응답자는 글만 알면 된다.
    """
    msgs = req.get("messages") or []
    if not msgs:
        return str(req.get("prompt") or "")
    return "\n\n".join(
        f"[{m.get('role', '?')}]\n{str(m.get('content') or '')}" for m in msgs)


def _wrap_answer(text):
    """무엇이 답했든 같은 모양으로 감싼다.

    부른 쪽은 무엇이 답했는지 몰라야 한다 - 몰라야 응답자를 바꿔도 아무것도 안 깨진다.
    """
    return {"choices": [{"message": {"role": "assistant",
                                     "content": str(text or "").strip()}}]}


def _ask_command(req):
    """OpenAI 모양이 아닌 LLM. 명령을 실행하고 stdin/stdout으로 주고받는다.

    이 한 갈래가 '어떠한 LLM이든'을 실제로 만든다 - 파이썬 스크립트든, 사내 API를 부르는
    셸이든, 직접 만든 무엇이든 글을 받고 글을 뱉으면 붙는다. 커넥터가 모양을 맞춰 준다.
    """
    import subprocess
    argv = _isolated_argv(CMD)
    if argv:
        proc = subprocess.run(argv, input=_prompt_text(req),
                              capture_output=True, text=True, timeout=CMD_TIMEOUT)
    else:
        # 가둘 수단이 없으면 그대로 돈다. 막지 않는 이유는 막으면 대부분이 못 쓰기 때문이고,
        # 대신 그 사실을 플랫폼에 올려 빌리는 쪽이 고를 수 있게 한다.
        proc = subprocess.run(CMD, shell=True, input=_prompt_text(req),
                              capture_output=True, text=True, timeout=CMD_TIMEOUT)
    if proc.returncode != 0:
        # 오류 내용을 그대로 올린다 - 왜 실패했는지 모르면 붙일 수가 없다.
        raise RuntimeError((proc.stderr or "").strip()[:200] or f"명령 실패({proc.returncode})")
    return _wrap_answer(proc.stdout)


def _detect_models():
    """이 컴퓨터에 이미 있는 LLM을 찾는다.

    [남는 연산 2026-08-01, 현준-4] 제공자에게 "모델을 고르고 띄우라"고 하면 대부분 못 한다.
    있는 것을 찾아서 알려 주는 것이 우리 몫이다 - 켜 두기만 하면 되는 것이 이 시장의 전제다.
    """
    import subprocess
    out = []
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            for ln in r.stdout.strip().splitlines()[1:]:
                name = ln.split()[0] if ln.split() else ""
                if name:
                    out.append(name)
    except Exception:
        pass
    return out[:20]


def _effective_isolation():
    """지금 설정에서 격리가 **실제로 걸리는가**.

    [정정 2026-08-01, 현준-4] 격리는 명령 모드(MURMUR_CMD)에만 걸린다. Ollama처럼 이미
    떠 있는 서버에 HTTP로 보내는 경우는 그 서버가 우리 샌드박스 밖에 있으므로 아무것도
    가두지 못한다. 그런데 화면에는 '격리 bwrap'이라고 떴다 - 거짓 안심이다.

    쓸 수 있는 수단이 아니라 **이번 실행에 걸리는 것**을 올린다.
    """
    if HUMAN:
        return "human"          # 사람이 답한다 - 가둘 대상이 없다
    if CMD:
        return _isolation()     # 명령 모드에서만 실제로 걸린다
    return "none"               # 이미 떠 있는 서버로 보낸다 - 우리가 못 가둔다


def _isolation():
    """LLM을 가둘 수단이 이 컴퓨터에 있는가.

    제공자는 모르는 곳에서 온 일을 자기 PC에서 돌린다. 그 일이 자기 파일을 못 만지게
    하는 것은 우리가 대신 해 줄 수 없고, 도구가 있는지 알려 주고 쓰는 것까지만 할 수 있다.

    없다고 막지는 않는다 - 막으면 대부분이 못 쓴다. 대신 무엇이 걸려 있는지 플랫폼에
    올려서, 빌리는 쪽이 '격리된 곳에서만 돌린다'를 고를 수 있게 한다.
    """
    import shutil
    for tool in ("bwrap", "podman", "docker"):
        if shutil.which(tool):
            return tool
    return "none"


def _isolated_argv(cmd):
    """명령을 격리 안에서 돌리는 argv로 바꾼다. 수단이 없으면 그대로 둔다.

    가두는 범위: 파일은 읽기 전용(모델 파일은 읽어야 한다), 홈은 안 보이고, 망은
    로컬만 - 모르는 곳에서 온 일이 제공자의 파일과 계정을 만지면 안 된다.
    """
    iso = _isolation()
    if iso == "bwrap":
        # 실측으로 맞춘 조합이다(2026-08-01). symlink만으로는 /bin/sh가 안 잡혀 아무것도
        # 못 돌았다 - 가두기만 하고 실행이 안 되면 격리가 아니라 고장이다.
        argv = ["bwrap", "--ro-bind", "/usr", "/usr"]
        for d in ("/lib", "/lib64", "/bin", "/sbin"):
            if os.path.exists(d):
                argv += ["--ro-bind", d, d]
        # [실측 2026-08-01, 현준-4] 새 /tmp만 주면 사용자의 모델 스크립트가 안 보여
        # 명령 모드를 아예 못 쓴다 - 가두기만 하고 실행이 안 되면 격리가 아니라 고장이다.
        # 커넥터를 띄운 폴더만 읽기 전용으로 들여보낸다: 모델을 부르는 것은 되고,
        # 홈 전체와 다른 폴더는 여전히 안 보인다.
        argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--unshare-user", "--unshare-pid", "--die-with-parent"]
        # 작업 폴더 바인드는 --tmpfs /tmp **뒤에** 와야 한다. 앞에 두면 tmpfs가 덮어
        # 폴더가 사라진다(실측: bwrap Can't chdir). 순서가 곧 뜻인 인자다.
        cwd = os.getcwd()
        if cwd not in ("/", "/root", os.path.expanduser("~")):
            argv += ["--ro-bind", cwd, cwd, "--chdir", cwd]
        argv += ["--", "/bin/sh", "-c", cmd]
        return argv
    return None


def _specs():
    """이 컴퓨터가 내놓는 자원. 사람이 적는 대신 재서 올린다.

    [남는 연산 2026-08-01, 현준-4] 사람이 적으면 거짓말한다 - 24GB라고 적어 두고 4GB로
    돌리면 등급 배정이 통째로 틀어진다. 못 재면 안 보낸다(0으로 보내면 없다는 뜻이 된다).
    """
    out = {}
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            name, mib = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
            out["gpu"] = name
            out["vram_gb"] = str(int(int(mib) / 1024))
    except Exception:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as fp:
            for ln in fp:
                if ln.startswith("MemTotal:"):
                    out["ram_gb"] = str(int(int(ln.split()[1]) / 1024 / 1024))
                    break
    except Exception:
        pass
    return out


def _ask_human(req):
    """사람이 답한다. 프롬프트를 보여 주고 타이핑을 기다린다.

    응답 모양은 기계와 같게 맞춘다 - 부른 쪽은 무엇이 답했는지 몰라도 되고, 몰라야
    응답자를 바꿔도 아무것도 안 깨진다.
    """
    msgs = req.get("messages") or []
    print("\n" + "─" * 60)
    for m in msgs[-4:]:                      # 최근 몇 마디만 - 전부 쏟으면 읽기 어렵다
        who = m.get("role", "?")
        print(f"[{who}] {str(m.get('content') or '')[:1500]}")
    print("─" * 60)
    print("답을 쓰고 Enter를 두 번 누르세요(빈 줄로 끝냅니다).")
    lines = []
    while True:
        try:
            ln = input()
        except EOFError:
            break
        if not ln.strip() and lines:
            break
        lines.append(ln)
    return _wrap_answer("\n".join(lines))


def main():
    if not TOKEN:
        print("MURMUR_TOKEN이 필요합니다. murmur 설정 → 실행 설정에서 발급하세요.",
              file=sys.stderr)
        return 2
    auth = {"Authorization": f"Bearer {TOKEN}"}
    # 자원은 한 번 재서 계속 같이 보낸다 - 매번 재면 느려지고, 안 보내면 등급이 안 선다.
    import urllib.parse
    spec = _specs()
    spec["isolation"] = _effective_isolation()
    models = _detect_models()
    if models:
        spec["models"] = ",".join(models)
    global _spec_qs
    _spec_qs = ("?" + urllib.parse.urlencode(spec)) if spec else ""
    if spec:
        print("  내놓는 자원:", spec)
    print(f"붙는 중: {MURMUR}  ←  " + ("사람(직접 입력)" if HUMAN else (CMD if CMD else LLM_URL)))
    while True:
        try:
            got = _get(f"{MURMUR}/api/relay/pending/{_spec_qs}", auth)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print("토큰이 거부됐습니다. 다시 발급하세요.", file=sys.stderr)
                return 3
            time.sleep(IDLE_SLEEP)
            continue
        except Exception:
            # 잠깐 끊긴 것과 영영 끊긴 것을 여기서 가르지 않는다 - 계속 시도한다.
            time.sleep(IDLE_SLEEP)
            continue

        call = (got or {}).get("call")
        if not call:
            time.sleep(IDLE_SLEEP)
            continue

        out, err = {}, ""
        try:
            if HUMAN:
                out = _ask_human(call.get("request") or {})
            elif CMD:
                out = _ask_command(call.get("request") or {})
            else:
                h = {"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {}
                out = _post(LLM_URL, call.get("request") or {}, h)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:200]

        try:
            _post(f"{MURMUR}/api/relay/reply/",
                  {"call_id": call["id"], "response": out, "error": err}, auth)
        except Exception:
            # 답을 못 돌려줘도 다음 호출은 받는다. 그 호출은 서버 쪽에서 만료된다.
            pass


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        print("\n끊었습니다.")
