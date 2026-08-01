"""[스레드 누계 상한(2026-08-01, U-442 실측)] 같은 일감을 오래 붙들면 일감 경계만으로는 안 끊긴다 —
47시간 돈 판의 스레드 누계 입력이 1억 토큰, 최근 20턴이 $17.19(턴당 $0.86)였다."""
import pytest

from organt import organt as og


class _Stub:
    session_id = "S-1"
    _thread_cum_sid = "S-1"
    _thread_cum_input = 0


def _resume(cum, monkeypatch, cap=None):
    monkeypatch.delenv("ORGANT_MICRO_FRESH", raising=False)
    monkeypatch.delenv("ORGANT_SCOPE_FRESH", raising=False)
    if cap is None:
        monkeypatch.delenv("ORGANT_THREAD_INPUT_CAP", raising=False)
    else:
        monkeypatch.setenv("ORGANT_THREAD_INPUT_CAP", str(cap))
    o = _Stub()
    o._thread_cum_input = cum
    return og.Organt._resume_sid(o, micro=False)


def test_누계가_상한을_넘으면_새_스레드로_시작한다(monkeypatch):
    assert _resume(3_000_000, monkeypatch) is None


def test_상한_아래면_세션을_잇는다(monkeypatch):
    assert _resume(2_999_999, monkeypatch) == "S-1"


def test_상한을_0으로_두면_종전대로_계속_잇는다(monkeypatch):
    assert _resume(999_000_000, monkeypatch, cap=0) == "S-1"


def test_다른_세션의_누계는_이_세션을_끊지_않는다(monkeypatch):
    monkeypatch.delenv("ORGANT_MICRO_FRESH", raising=False)
    monkeypatch.delenv("ORGANT_SCOPE_FRESH", raising=False)
    monkeypatch.delenv("ORGANT_THREAD_INPUT_CAP", raising=False)
    o = _Stub()
    o._thread_cum_sid = "S-옛것"
    o._thread_cum_input = 99_000_000
    assert og.Organt._resume_sid(o, micro=False) == "S-1"
