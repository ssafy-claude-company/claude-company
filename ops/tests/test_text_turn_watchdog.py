"""글만 쓰는 턴에는 심박이 없다 (2026-08-06, 실측 5일 316건).

[계측] agent_timeout 316건을 tool_ran으로 갈랐더니 **316건 전부 tool_ran=false ·
first_tool_s=null**이었다. 하나도 예외가 없다. 08-03에 붙여 둔 계측('잘린 턴이 느린 것인지 멈춘
것인지')이 답을 줬다 — 느린 게 아니라 **도구를 쓸 일이 없는 턴**이었다.

이 워치독의 유일한 생존 신호는 도구 활동이다(audit PostToolUse 훅이 flow.last_activity를 갱신).
그런데 회의 발언·발언권 응찰·표결·인수 보고처럼 "도구 호출 금지, 텍스트로만"이라고 지시받은 턴은
도구를 부르지 않는다. 심박이 영영 안 뛰니 own_idle이 '턴 시작부터의 벽시계'가 되고, 진행 중이든
아니든 8분에 잘린다. 코드 주석은 '완전히 멈춘 것만 끊는다'인데 실제로는 고정 벽시계였다.

이것이 U-478의 '독립 QA 인수검증이 계속 timeout'(위임 5회 반복 → Task 마감 보류)의 기계적
원인이다. 타임아웃이 몰린 봇 상위 6개가 316건 중 118건을 차지한다.

도구 없는 턴은 별도 상한으로 본다(ORGANT_TEXT_TURN_TIMEOUT, 기본 15분). 없애지는 않는다 —
진짜 행도 끊어야 하므로. 잘릴 때 상한과 도구 여부를 로그에 남겨 다음 계측으로 검증한다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.sys_core import Sys


def test_도구_없는_턴은_별도_상한을_갖는다():
    src = inspect.getsource(Sys.run_turn)
    assert "_text_turn_timeout" in src, "micro 턴이 도구 턴과 같은 상한을 쓴다"
    assert "cap=_tcap" in src, "상한이 워치독에 전달되지 않는다"


def test_상한은_환경변수로_조정된다():
    src = inspect.getsource(Sys.__init__)
    assert "ORGANT_TEXT_TURN_TIMEOUT" in src


def test_기본값은_도구_턴보다_길다():
    import os
    a = int(os.environ.get("ORGANT_TURN_TIMEOUT", "480"))
    b = int(os.environ.get("ORGANT_TEXT_TURN_TIMEOUT", "900"))
    assert b > a, (a, b)


def test_상한은_정수로_뭉개지_않는다():
    """turn_timeout은 소수도 된다(테스트가 0.5초를 쓴다) — int()로 자르면 0이 돼 전부 잘린다."""
    src = inspect.getsource(Sys._run_until_silent)
    assert "float(cap or self.turn_timeout)" in src


def test_잘린_사실이_관측된다():
    src = inspect.getsource(Sys._run_until_silent)
    assert 'turn_watchdog_cut' in src and "tool_ran" in src
