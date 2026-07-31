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


def test_세그먼트마다_사실을_다시_본다():
    """복구 한 번만으로는 경로에 따라 놓친다 — 이어가기 세그먼트마다(캐시) 본다."""
    from system import sys_core
    src = inspect.getsource(sys_core.Sys)
    assert "_sync_pool_deploy" in src
    body = inspect.getsource(sys_core.Sys._sync_pool_deploy)
    assert "pool_live_url" in body and "_pool_sync_at" in body
