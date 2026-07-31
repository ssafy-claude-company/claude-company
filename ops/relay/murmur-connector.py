#!/usr/bin/env python3
"""murmur 커넥터 — 집에서 돌리는 LLM을 murmur에 붙인다 (2026-07-31, 현준-4).

쓰는 법:

    MURMUR_TOKEN=... python3 murmur-connector.py

  선택:
    MURMUR_URL   기본 https://murmur.dojin-mini.shop
    LLM_URL      기본 http://127.0.0.1:11434/v1/chat/completions  (Ollama)
    LLM_KEY      로컬 LLM이 키를 요구하면

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


def main():
    if not TOKEN:
        print("MURMUR_TOKEN이 필요합니다. murmur 설정 → 실행 설정에서 발급하세요.",
              file=sys.stderr)
        return 2
    auth = {"Authorization": f"Bearer {TOKEN}"}
    print(f"붙는 중: {MURMUR}  ←  {LLM_URL}")
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
