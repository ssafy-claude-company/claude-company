"""한 번 보내고 끝나면 그 한 사람이 안 하면 영영 못 닫는다(2026-08-03, 실측 U-478).

마감 구동부에는 이미 교차검증 위임 발송기가 있다(task_close_crosscheck_sent). 그런데 흐름당
**1회**(_close_cc_sent)만 나간다. 지목된 동료가 검증 대신 재위임하면 응답은 오지만 교차검증
카운터는 0 그대로이고, 다시 보낼 길이 없어 리더는 마감만 반복 호출한다.

실측 U-478:
  13:49 e2e_pass(결함 0) · 13:50 task_close_crosscheck_sent → QA
  14:52 그 QA가 검증 대신 재위임 → stage_stuck_parked → stalled_stopped
  14:55 complete_thrash holds=4 · 14:57 holds=5
  task_close_state 로그는 내내 cc=0 offdom=0 delivered=true verdict=e2e_pass
  그 한 시간 14턴 $5.57. 제품은 완성돼 있었다(두 뷰포트 3/3 PASS, 터치 포함).

교차검증 카운터는 request(Work) **응답** 경로에서만 오른다(communication.py) — wake로 깨우면
세어지지 않는다. 그래서 새 경로를 만들지 않고, 있는 발송기의 잠금을 풀어 **다른 동료**에게
다시 보낸다. 이미 물어본 사람은 후보에서 뺀다.
"""
import inspect

from system import sys_core
from system.rule import task_gates


def test_게이트가_반복_보류에서_재발송_신호를_세운다():
    src = inspect.getsource(task_gates._gate_cross_check)
    assert "_close_cc_retry" in src, "재발송 신호가 없으면 첫 수신자가 안 하면 영영 못 닫는다"
    assert "cc_held >= 3" in src, "첫 보류는 종전대로 안내로 끝낸다"


def test_마감_구동부가_그_신호로_잠금을_푼다():
    src = inspect.getsource(sys_core)
    i = src.index("_close_cc_retry")
    window = src[i:i + 400]
    assert "flow._close_cc_sent = False" in window, "잠금을 풀지 않으면 발송기가 다시 나가지 않는다"


def test_이미_물어본_사람은_후보에서_뺀다():
    src = inspect.getsource(sys_core)
    assert "_cc_asked" in src, "같은 사람에게 다시 보내면 같은 결과다"
    i = src.index("task_close_crosscheck_sent")
    window = src[max(0, i - 400):i + 200]
    assert "_cc_asked.add" in window, "보낸 사람을 기록해야 다음엔 다른 동료가 걸린다"


def test_위임_경로를_요청_본문이_닫는다():
    """[2026-08-03 실측] 지목된 동료가 이 요청을 받고 한 일이 다시 위임이었다
    ("독립 QA 최종 인수검증 위임을 유지합니다" 14:52·15:14). 위임 응답은 교차검증으로 세지
    않으므로 관문은 그대로 막히고 판이 20분 무진행으로 멎었다."""
    src = inspect.getsource(sys_core)
    i = src.index("SYS — 마감 전 독립 검증")
    window = src[i:i + 900]
    assert "당신이 직접 해야 합니다" in window
    assert "위임했다는 응답은 검증으로 세지" in window
