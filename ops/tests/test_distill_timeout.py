"""[증류는 매달릴 수 있다(2026-08-02 실사고)] 증류 세션 36개 중 35개가 1초 안에 끝났는데, 하나가 완료
표식 없이 멈춘 채 프로세스만 1시간 40분 살아 있었다 — 그 봇은 __distill__ 점유로 묶이고, 4코어 머신의
슬롯 3개를 붙들고, '가장 오래된 턴'을 보는 러너 재시작 대기가 영영 성립하지 않았다."""
import inspect

from system import sys_core


def test_증류에_시간_제한이_있다():
    src = inspect.getsource(sys_core.Sys._distill_bot_inner)
    assert "asyncio.wait_for" in src and "ORGANT_DISTILL_TIMEOUT" in src


def test_시간_초과는_실패로_돌려_다음_주기에_다시_한다():
    src = inspect.getsource(sys_core.Sys._distill_bot_inner)
    i = src.index("asyncio.TimeoutError")
    tail = src[i:i + 240]
    assert "bot_distill_timeout" in tail and "return False" in tail


def test_점유는_반드시_풀린다():
    src = inspect.getsource(sys_core.Sys.distill_bot)
    assert "finally" in src and "release" in src
