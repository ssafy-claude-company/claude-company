"""라이브가 없으면 재배포 보류가 걸리지 않는다(U-442 실측: 배포 보류 → 마감 거부 → 정지)."""
import inspect

from system.rule import project as pj


def test_보류는_라이브가_선_뒤부터():
    src = inspect.getsource(pj)
    i = src.index("deploy_blind_held")
    head = src[max(0, i - 1200):i]
    assert '_deploy_live' in head          # 라이브가 있을 때만 맹목 판정
    assert "검증할 라이브가 없으면 맹목이 아니다" in head


def test_보류_문구는_출구를_제시한다():
    src = inspect.getsource(pj)
    i = src.index("재배포 보류")
    seg = src[i:i + 700]
    assert "request(Work)" in seg and "complete_task" in seg
