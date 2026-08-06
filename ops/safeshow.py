#!/usr/bin/env python3
"""설정 파일을 **안전하게** 읽어 본다 — 값의 모양으로 가린다 (2026-08-06, 현준-4).

[왜 필요했나] 감사 중에 설정 파일을 읽으려고 "키 이름에 secret/key가 들어간 줄을 가린다"는
정규식을 썼다. 그런데 LiveKit 설정의 열쇠는 `<키이름>: <값>` 꼴이라 이름 쪽에 secret도 key도
없었다 — 마스킹을 그대로 지나쳐 **API 시크릿이 대화 기록에 찍혔다**(실측, 그 열쇠는 교체함).
같은 회차에 비슷한 실수를 두 번 했다.

배운 것: **이름으로 가리면 새 이름을 만날 때마다 뚫린다.** 가릴 것은 값이다 — 사람이 읽는
글이 아닌, 길고 섞인 글자 뭉치는 그 자체가 비밀의 모양이다.

이 도구는 넉넉하게 가린다. 설정을 눈으로 훑는 것이 목적이지 값을 확인하는 것이 아니므로,
덜 가리는 쪽보다 더 가리는 쪽으로 틀린다.

    python3 ops/safeshow.py /etc/livekit.yaml
"""
import re
import sys

MASK = "••••[가려진 값]"
MIN_LEN = 16

# 사람이 읽는 것들 — 이건 비밀이 아니다.
_KEEP = re.compile(
    r"^(?:"
    r"https?://[^\s]*$"          # 주소
    r"|/[\w./-]*$"               # 경로
    r"|[\w.-]+\.(?:com|net|org|io|dev|shop|kr|local)$"   # 도메인
    r"|[\d.]+$"                  # 숫자·버전·IP
    r"|[A-Za-z_][A-Za-z_]*$"     # 숫자 없는 낱말(영문)
    r")",
    re.I)

# 길고 섞인 글자 뭉치 — 비밀의 모양.
# = 와 : 는 일부러 뺀다. 그 둘까지 삼키면 "이름=값" 통째로 가려져 **무엇이 설정돼 있는지**도
# 안 보인다 — 구조는 남기고 값만 가리는 것이 이 도구의 쓸모다.
_TOKEN = re.compile(r"[A-Za-z0-9+/_.-]{%d,}" % MIN_LEN)

_PEM = re.compile(r"-----BEGIN[^-]*-----.*?-----END[^-]*-----", re.S)


def _looks_secret(tok):
    if _KEEP.match(tok):
        return False
    has_digit = any(c.isdigit() for c in tok)
    has_alpha = any(c.isalpha() for c in tok)
    if has_digit and has_alpha:
        return True
    # 순수 16진수/숫자 뭉치도 열쇠다(예: 48자리 hex).
    return len(tok) >= 24 and re.fullmatch(r"[0-9a-fA-F]+", tok) is not None


def scrub(text):
    text = _PEM.sub(MASK, text)
    return _TOKEN.sub(lambda m: MASK if _looks_secret(m.group(0)) else m.group(0), text)


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    for path in argv[1:]:
        if len(argv) > 2:
            print(f"===== {path} =====")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                print(scrub(f.read()), end="")
        except OSError as e:
            print(f"(못 읽음: {e})", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
