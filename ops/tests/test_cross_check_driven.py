"""교차 검증도 SYS가 구동한다(2026-08-03, 실측 U-478).

마감 게이트는 "만든 사람이 아닌 다른 멤버의 실사용 검증 응답 1건"을 요구하면서, 그 응답을
**받아내는 주체를 두지 않았다** — 리더의 자발성에 맡겼다. SYS는 e2e는 직접 구동하는데
(_drive_task_boundary_e2e) 여기는 하지 않았다.

실측 U-478:
  13:49 e2e_pass (결함 0)
  13:50 complete_task_refused — 교차 검증 참여 0
  14:52 지목된 검증자가 검증 대신 재위임 -> stage_stuck_parked -> stalled_stopped
  14:55 complete_thrash holds=4 · 14:57 holds=5
  그 한 시간 동안 14턴 $5.57. 게이트가 "재호출하지 말라"고 하드 문구로 말해도 반복됐다.

게이트는 동기 함수라 깨울 수 없다 — 누구를 깨울지 신호만 남기고(_cc_drive), SYS 루프가
_stage_stuck과 같은 자리에서 한 번 소비해 그 사람을 직접 깨운다.
"""
import inspect

from system import sys_core
from system.rule import task_gates


def test_게이트가_깨울_대상을_신호로_남긴다():
    src = inspect.getsource(task_gates._gate_cross_check)
    assert "_cc_drive" in src, "누구를 깨울지 신호가 없으면 SYS가 구동할 수 없다"
    assert "cc_held >= 3" in src, "반복 보류에 이르렀을 때만 구동한다(첫 보류는 안내로 충분)"


def test_SYS_루프가_그_신호를_소비해_검증자를_깨운다():
    src = inspect.getsource(sys_core)
    assert "_cc_drive" in src and "cross_check_driven" in src, (
        "신호를 소비하는 자리가 없으면 게이트는 영원히 안내만 반복한다")
    # 소비는 1회성이어야 한다 — 매 바퀴 깨우면 검증자를 굴리기만 한다.
    i = src.index("_ccd = getattr(flow, \"_cc_drive\", None)")
    window = src[i:i + 400]
    assert "flow._cc_drive = None" in window, "신호를 지우지 않으면 매 바퀴 다시 깨운다"


def test_지시가_위임_금지를_명시한다():
    """실측에서 지목된 검증자가 한 일이 '다시 위임'이었다 — 그 경로를 문구로 닫는다."""
    src = inspect.getsource(sys_core)
    i = src.index("cross_check_driven")
    window = src[i:i + 700]
    assert "당신이 직접" in window and "위임" in window
