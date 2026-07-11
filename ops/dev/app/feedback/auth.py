"""인증 어댑터 — feedback 앱이 호스트 서비스와 접촉하는 유일한 지점(이식 계약).

dev 서비스 구현은 두 겹이다(위 → 아래 순서로 시도):

1) 정적 토큰(자립) — env `DEV_FEEDBACK_TOKENS="토큰:handle,토큰2:handle2"`.
   murmur가 없어도 dev 도구가 자립한다.
2) murmur 위임(선택) — env `DEV_FEEDBACK_MURMUR=http://127.0.0.1:8000` 가 있으면
   같은 Authorization 토큰으로 murmur `/api/me/`를 호출해 is_admin이면 그 handle.
   브라우저 localStorage의 murmur admin 토큰(organt_token)이 그대로 통한다.

반환값은 handle 문자열 또는 None — 모델 FK 없음(원 계약 유지).
"""
import json
import os
import time
import urllib.request

_CACHE = {}          # token -> (handle|None, 만료시각) — 위임 호출 매요청 방지
_TTL = 60


def _static_tokens():
    out = {}
    for pair in os.environ.get("DEV_FEEDBACK_TOKENS", "").split(","):
        if ":" in pair:
            tok, handle = pair.split(":", 1)
            if tok.strip() and handle.strip():
                out[tok.strip()] = handle.strip()
    return out


def _murmur_admin(token):
    base = os.environ.get("DEV_FEEDBACK_MURMUR", "").rstrip("/")
    if not base:
        return None
    hit = _CACHE.get(token)
    if hit and hit[1] > time.time():
        return hit[0]
    handle = None
    try:
        req = urllib.request.Request(f"{base}/api/me/", headers={"Authorization": f"Token {token}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            me = (json.load(r) or {}).get("me") or {}
        if me.get("is_admin"):
            handle = me.get("handle")
    except Exception:
        handle = None
    _CACHE[token] = (handle, time.time() + _TTL)
    return handle


def resolve_admin(request):
    """요청에서 admin 사용자를 식별해 handle을 반환. admin 아니면(미인증 포함) None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Token "):
        return None
    token = auth[6:].strip()
    if not token:
        return None
    static = _static_tokens()
    if token in static:
        return static[token]
    return _murmur_admin(token)
