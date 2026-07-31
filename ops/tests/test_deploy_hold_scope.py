"""라이브가 없으면 재배포 보류가 걸리지 않는다(U-442 실측: 배포 보류 → 마감 거부 → 정지)."""
import inspect

from system.rule import project as pj


def test_보류는_라이브가_선_뒤부터():
    src = inspect.getsource(pj)
    assert "검증할 라이브가 없으면 맹목이 아니다" in src
    cond = src[src.index("검증할 라이브가 없으면"):]
    cond = cond[:cond.index('flow.log("deploy_blind_held"')]
    assert 'getattr(flow, "_deploy_live", False)' in cond   # 라이브가 있을 때만 맹목 판정


def test_보류_문구는_출구를_제시한다():
    src = inspect.getsource(pj)
    i = src.index("재배포 보류")
    seg = src[i:i + 700]
    assert "request(Work)" in seg and "complete_task" in seg
