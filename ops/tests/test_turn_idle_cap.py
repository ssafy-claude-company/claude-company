"""[응답이 끊긴 턴이 영원히 매달린다(2026-08-02 실측)] 19:24 자원 폭주 직후 시작된 두 턴이 120분간
한 줄도 쓰지 않은 채 프로세스만 살아 있었고 두 판이 통째로 멈췄다. 시간으로 자르면 정상 작업 턴
(최장 67분, 성과 있음)을 죽인다 — 무응답 시간으로 잰다(정상 기록 간격 중앙 1초·95분위 116초)."""
import inspect

from organt import codex_mcp_bridge as br


def _src():
    return inspect.getsource(br)


def test_무응답_상한이_있다():
    s = _src()
    assert "ORGANT_TURN_IDLE_CAP" in s and "asyncio.wait_for(proc.stdout.readline()" in s


def test_무기한_대기하던_루프는_사라졌다():
    assert "async for raw in proc.stdout" not in _src()


def test_상한을_0으로_두면_종전대로_기다린다():
    s = _src()
    i = s.index("ORGANT_TURN_IDLE_CAP")
    assert "_idle_cap > 0" in s[i:i + 700]


def test_회수는_실패로_돌려보낸다():
    s = _src()
    i = s.index("무응답 {int(_idle_cap)}s")
    seg = s[max(0, i - 200):i + 320]
    assert "api_error" in seg and "proc.kill()" in seg
