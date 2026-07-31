#!/usr/bin/env python3
"""murmur 커넥터 — 집에서 돌리는 LLM을 murmur에 붙인다 (2026-07-31, 현준-4).

쓰는 법:

    MURMUR_TOKEN=... python3 murmur-connector.py

  선택:
    MURMUR_URL   기본 https://murmur.dojin-mini.shop
    LLM_URL      기본 http://127.0.0.1:11434/v1/chat/completions  (Ollama)
    LLM_KEY      로컬 LLM이 키를 요구하면
    MURMUR_HUMAN 1이면 사람이 답한다 — 프롬프트를 보여 주고 타이핑을 기다린다

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
    text = "\n".join(lines).strip()
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def main():
    if not TOKEN:
        print("MURMUR_TOKEN이 필요합니다. murmur 설정 → 실행 설정에서 발급하세요.",
              file=sys.stderr)
        return 2
    auth = {"Authorization": f"Bearer {TOKEN}"}
    print(f"붙는 중: {MURMUR}  ←  " + ("사람(직접 입력)" if HUMAN else LLM_URL))
    while True:
        try:
            got = _get(f"{MURMUR}/api/relay/pending/", auth)
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
