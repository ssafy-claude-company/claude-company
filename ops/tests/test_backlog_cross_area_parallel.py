"""다른 작업 영역이면 동시에 연다(U-442 실측: 96분 동안 동시 진행이 늘 1이었다)."""
import inspect

from system import sys_core


def _handoff_src():
    return inspect.getsource(sys_core.Sys._backlog_handoff)


def test_활성이_있다고_무조건_접지_않는다():
    src = _handoff_src()
    assert "backlog_parallel_open" in src
    # 종전의 무조건 선점 중단(첫 활성 하나만 보고 return)이 남아 있지 않다
    assert "ast, _ar, ab = active_now[0]" not in src


def test_같은_영역과_겹치는_쓰기는_여전히_기다린다():
    src = _handoff_src()
    assert "같은 작업 영역" in src and "선언된 쓰기 영역 겹침" in src
    assert "write_scopes_conflict" in src


def test_동시_상한을_존중한다():
    src = _handoff_src()
    assert "backlog_parallel_width()" in src and "동시 진행 상한" in src


def test_선택_순서는_그대로다():
    """병렬은 '무엇을 먼저 고르는가'를 바꾸지 않는다 — 등록 순서 폴백이 그대로 있어야 한다."""
    src = _handoff_src()
    assert "_in_order" in src
