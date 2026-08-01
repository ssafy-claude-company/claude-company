"""[백로그 이름으로도 보고가 들어간다(2026-08-01, U-442 실측)] 화면과 서로의 발언은 백로그를 `B1`로
부르는데 report_iter는 SubTask id만 받아, 팀이 정본 id를 되묻다 주기가 5시간 넘게 멈췄다."""
import pytest

from system.rule.milestone import rule_report_iter
from test_backlog_scope_regressions import _flow_with_two_subtasks


def test_백로그_이름이_유일하면_그_단위로_보고된다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (st1, relay1, b1), (st2, relay2, _b2) = _flow_with_two_subtasks()
    relay2.submit(22, "두 번째 단위의 두 번째 일감", force=True)   # ST-2에만 B2가 있다

    out = rule_report_iter(flow, 22, {"target": "B2", "results": "검증 | pass | exit 0"})

    assert "대상 SubTask를 못 찾았습니다" not in out


def test_같은_이름이_여러_단위에_있으면_정본_id를_되돌려준다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (st1, _r1, _b1), (st2, _r2, _b2) = _flow_with_two_subtasks()

    out = rule_report_iter(flow, 99, {"target": "B1", "results": "검증 | pass | exit 0"})

    assert "여러 단위" in out and f"{st1.st_id}/B1" in out and f"{st2.st_id}/B1" in out


def test_없는_이름은_종전대로_단위_목록을_안내한다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (st1, _r1, _b1), (st2, _r2, _b2) = _flow_with_two_subtasks()

    out = rule_report_iter(flow, 22, {"target": "B9", "results": "검증 | pass | exit 0"})

    assert "대상 SubTask를 못 찾았습니다" in out and st1.st_id in out
