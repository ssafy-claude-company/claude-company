"""스레드는 일감 경계에서 끊는다(U-478 실측: 한 턴이 29M 토큰·상위 5턴이 비용의 41%)."""
import inspect

from organt import organt as og
from system import sys_core


def test_일감이_바뀌면_새_스레드로_시작한다():
    src = inspect.getsource(og.Organt._resume_sid)
    assert "_scope_changed" in src
    assert "일감이 바뀌는 자리" in src


def test_같은_일감을_이어가면_세션을_잇는다():
    src = inspect.getsource(og.Organt._resume_sid)
    i = src.index("_scope_changed")
    assert "return self.session_id" in src[i:]


def test_SYS가_지금_일감을_알려준다():
    src = inspect.getsource(sys_core.Sys.run_turn)
    assert "_work_scope" in src and "active_backlog_rows" in src
