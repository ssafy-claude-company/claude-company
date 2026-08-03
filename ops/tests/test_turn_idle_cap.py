"""[응답이 끊긴 턴이 영원히 매달린다(2026-08-02 실측)] 자원 폭주 직후 시작된 두 턴이 120분간 세션에
한 줄도 쓰지 않은 채 프로세스만 살아 있었고 두 판이 통째로 멈췄다.

시간이 아니라 **무응답 시간**으로 잰다 — 정상 작업 턴은 최장 67분까지 가지만 그동안 계속 기록을
쓴다(정상 기록 간격 중앙 1초·95분위 116초, 표본 10.8만). 그리고 읽기 루프 자체는 건드리지 않는다:
처음엔 readline을 wait_for로 감쌌다가 `test_Codex턴취소는_프로세스그룹까지_회수`가 멈췄다 —
취소 의미가 바뀌어 회수가 안 됐다. 매달림을 고치려다 취소를 깨뜨리면 안 된다."""
import inspect

from organt import codex_mcp_bridge as br


def _src():
    return inspect.getsource(br)


def test_무응답_감시자가_있다():
    s = _src()
    assert "ORGANT_TURN_IDLE_CAP" in s and "_idle_watch" in s


def test_읽기_루프는_원래대로다():
    """취소 의미를 바꾸지 않는다 — 회수 테스트가 이걸로 깨졌었다."""
    s = _src()
    assert "async for raw in proc.stdout" in s
    assert "wait_for(proc.stdout.readline()" not in s


def test_상한을_0으로_두면_감시하지_않는다():
    s = _src()
    i = s.index("_idle_watch")
    seg = s[i:i + 700]
    assert "cap <= 0" in seg and "return" in seg


def test_매달리면_프로세스를_회수한다():
    s = _src()
    i = s.index("_idle_watch")
    seg = s[i:i + 900]
    assert "proc.kill()" in seg


def test_감시자는_턴이_끝나면_정리된다():
    assert "_idle_task.cancel()" in _src()
