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


def test_세는_자리를_관측한다():
    """[2026-08-03 실측] 마감 관문은 cross_check_offdomain>0을 요구하는데, 위임이 여러 번 나가고
    지목된 봇이 턴을 마쳐도(20:58:09, 225초) 카운터는 0 그대로였다(cc=0 offdom=0, holds 11).
    응답이 이 자리에 닿는지, 닿는데 조건에서 걸리는지, 아예 안 닿는지가 구분되지 않으면
    고칠 수가 없다 — 세는 순간과 못 세는 순간을 둘 다 남긴다."""
    from system.rule import communication

    src = inspect.getsource(communication)
    assert "cross_check_seen" in src
    i = src.index("cross_check_seen")
    window = src[i:i + 400]
    for k in ("product_ready", "counted", "owner"):
        assert k in window, k + "가 없으면 어디서 걸렸는지 모른다"


def test_살아있는_위임이_있으면_그_픽은_기다리는_것이다():
    """[기다림은 무진전이 아니다(2026-08-03, 계측으로 확정)]

    진전 판정은 장부 서명 변화다(ledger_signature). 그런데 마감 관문에서 교차검증 응답을 기다리는
    동안에는 장부가 바뀔 수 없다 — 기다림은 정의상 항상 무진전이라 그 픽은 재픽 없이 종결된다.
    위임은 즉시 반환되고(인플라이트) 응답 처리는 나중이므로, 판이 그 전에 파킹되면 응답은 영영
    처리되지 않는다.

    실측 U-478: 21:55:07 위임 → 21:58:14 그 봇 턴 완료 → 21:58:38 stalled_stopped(repicks 7).
    세는 자리에 건 계측(cross_check_seen)이 0건이었다 — 응답이 그 자리에 도달조차 못 했다.
    재개하면 같은 순서를 반복해 cc는 영원히 0이고 마감은 열한 번 거절됐다.
    """
    src = inspect.getsource(sys_core)
    i = src.index("_flow_cycle_progress[_ch1]")
    window = src[max(0, i - 900):i]
    assert "inflight_tasks" in window, "살아 있는 위임을 보지 않으면 기다리는 판이 죽는다"
