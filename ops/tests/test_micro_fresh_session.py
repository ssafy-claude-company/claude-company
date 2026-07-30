"""짧은 상호작용은 세션을 물지 않는다 — 응찰·표결 한 줄에 작업 스레드 전체가 실리던 것의 봉합."""
import os

from organt.organt import Organt


class _Stub(Organt):
    def __init__(self):
        self.session_id = "MAIN-THREAD"


def test_micro는_새_스레드로_뜬다():
    o = _Stub()
    assert o._resume_sid(micro=True) is None, "micro가 본 세션을 이어가면 기록 전량이 다시 실린다"


def test_작업_턴은_세션을_잇는다():
    o = _Stub()
    assert o._resume_sid(micro=False) == "MAIN-THREAD"


def test_노브로_종전_동작_복귀():
    o = _Stub()
    os.environ["ORGANT_MICRO_FRESH"] = "0"
    try:
        assert o._resume_sid(micro=True) == "MAIN-THREAD"
    finally:
        os.environ.pop("ORGANT_MICRO_FRESH", None)


def test_동행_발언은_micro로_나간다():
    import inspect

    from system.rule import communication as _c
    src = inspect.getsource(_c)
    assert 'micro=companion' in src
    assert 'wake_micro' in src
