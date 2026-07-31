"""장부가 아니라 사실을 본다 — 앱 풀에 살아 있으면 배포다(U-442 실측 교착)."""
import inspect

from system import deploy, sys_recovery


def test_사실_판정은_2xx만():
    src = inspect.getsource(deploy.pool_live_url)
    assert "200 <= int(_st) < 300" in src          # 4xx도 코드로 오므로 2xx만 배달
    assert "장부가 아니라 사실을 본다" in src


def test_복구가_앱_풀_사실을_흐름에_반영한다():
    src = inspect.getsource(sys_recovery)
    assert "pool_live_url" in src and "deploy_state_synced_from_pool" in src
    i = src.index("deploy_state_synced_from_pool")
    head = src[max(0, i - 700):i]
    assert "_deploy_live = True" in head and "_deployed_once = True" in head
