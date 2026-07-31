"""할 일이 없는 사람을 40초마다 다시 깨우던 자리(U-442 실측: owner_no_work 27세그먼트)."""
import inspect

from system.rule import communication as comm


def _src():
    return inspect.getsource(comm)


def test_빈손_연속이_세_번이면_이어가기를_멈춘다():
    src = _src()
    assert "_no_work_streak" in src and "owner_no_work_exhausted" in src
    assert "연속 3회 빈손" in src


def test_멈출_때_미완_표시를_푼다():
    """풀지 않으면 SYS 자동 이어가기가 같은 사람을 또 깨운다 — 루프의 실제 연료."""
    src = _src()
    i = src.index("owner_no_work_exhausted")
    head = src[max(0, i - 900):i]
    assert "owner_incomplete = False" in head


def test_실작업이_있으면_연속이_끊긴다():
    src = _src()
    assert "_no_work_streak" in src and "pop(int(to), None)" in src
