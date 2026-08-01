"""계속 안 나서는 사람은 덜 묻는다(U-442 실측: 82번 질문에 73번 연속 거절)."""
import inspect

from system import sys_core


def _src():
    return inspect.getsource(sys_core.Sys._floor_segment_open)


def test_연속_거절이_쌓이면_간격이_벌어진다():
    src = _src()
    assert "_seg_decline" in src and "2 ** (st // 3)" in src
    assert "min(8," in src              # 상한 8세그먼트 — 완전히 빼지 않는다


def test_한_번이라도_나서면_즉시_원복한다():
    src = _src()
    assert "0 if s > 0 else" in src


def test_전원이_대기_중이면_그래도_묻는다():
    """모두가 backoff에 걸리면 아무에게도 안 묻는 정지가 생긴다 — 그 경우 종전 후보를 쓴다."""
    src = _src()
    assert "if _kept:" in src and "cands = _kept" in src
