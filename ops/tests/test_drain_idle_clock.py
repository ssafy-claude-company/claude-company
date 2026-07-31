"""위임 회수는 같은 시계로 잰다(2026-07-31 실측: idle=1784555047 → 30초마다 잘림)."""
import inspect

from system import sys_core


def test_무활동_판정에_단조시계를_쓴다():
    src = inspect.getsource(sys_core.Sys._drain_inflight)
    seg = src[src.index("inflight_drain_timeout") - 1500:src.index("inflight_drain_timeout")]
    assert "time.monotonic()" in seg
    assert "time.time() - float(getattr(flow, \"last_activity\"" not in seg


def test_상한은_무활동이지_벽시계가_아니다():
    src = inspect.getsource(sys_core.Sys._drain_inflight)
    assert "_idle_cap" in src and "ORGANT_DRAIN_HARD_CAP" in src
    assert "turn_timeout\", 600) or 600) * 3 // 2" not in src
