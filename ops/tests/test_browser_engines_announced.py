"""[없는 엔진을 봇이 손으로 만들고 있었다(2026-08-02, U-478 세션 전문 실측)] 4브라우저 완수조건을 받은
봇이 webkit이 없자 /tmp에 apt 상태를 따로 만들어 패키지를 풀고 ldd로 의존성을 좇았다 — 한 턴 1시간
48분. 셋을 설치했으니 도구 설명이 그 사실을 이름으로 알려야 같은 삽질이 다시 생기지 않는다."""
from system.guide_tools import _preinstalled_refusal, make_guide_tools


def test_재설치_거절이_있는_엔진_이름을_알려준다():
    msg = _preinstalled_refusal("npx playwright install webkit")
    assert "webkit" in msg and "firefox" in msg and "chromium" in msg


def test_run_설명이_세_엔진을_명시한다():
    class _F:
        workspace = "/tmp/x"
        guide = None
    import inspect
    src = inspect.getsource(make_guide_tools)
    i = src.index("웹 작품은 **실제 브라우저 검증 가능**")
    seg = src[i:i + 400]
    assert "chromium" in seg and "firefox" in seg and "webkit" in seg
