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


def test_통과_보고로_등재된_조건은_그_자리에서_닫힌다(monkeypatch):
    """[끝난 일을 열린 일감으로 남기지 않는다(2026-08-03, 실측 U-478/U-496)]

    report_iter는 보고의 조건 줄을 SubTask 장부에 소급 등재한다(장부가 진실이도록). 그 등재분을
    닫는 조건에 '지금 아무도 작업 중이 아닐 것'(단일 활성 잠금)이 걸려 있었는데, 2026-07-31 전원
    병렬 이후 그 순간은 거의 오지 않는다. 그래서 **통과로 보고한 조건까지** 주인 없는 open 일감으로
    장부에 쌓였다 — 실측: 시스템 등재 백로그 89건 미완, 그중 72건은 한 번도 지명되지 않았고 ST-7
    총량이 71에서 108로 늘었다. 단위는 백로그가 다 소진돼야 닫히므로 판이 완주할 수 없다.

    통과 보고는 '시작할 일'이 아니라 '끝난 일'이라 동시 착수 잠금과 무관하다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (st1, relay1, b1), (st2, relay2, b2) = _flow_with_two_subtasks()
    relay1.pick(12, b1.backlog_id, 12)              # 다른 사람이 이미 작업 중(전원 병렬 상태)

    rule_report_iter(flow, 22, {"target": st2.st_id,
                                "results": "새 조건이 실제로 닫혔다 | pass | exit 0"})

    fresh = [b for b in relay2.backlogs if b.backlog_id != b2.backlog_id]
    assert len(fresh) == 1, "통과 조건이 장부에 등재되지 않았다"
    assert fresh[0].status == "done", (
        "통과로 보고한 조건이 주인 없는 open 일감으로 남았다 — 단위가 영영 닫히지 않는다")
    assert b1.status == "in_progress", "남의 진행 중 일감을 건드렸다"


def test_미충족_보고는_남은_일로_장부에_열려_있다(monkeypatch):
    """미충족은 실제로 남은 작업이다 — 닫으면 장부가 거짓이 된다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow, (st1, relay1, b1), (st2, relay2, b2) = _flow_with_two_subtasks()
    relay1.pick(12, b1.backlog_id, 12)

    rule_report_iter(flow, 22, {"target": st2.st_id,
                                "results": "아직 안 닫힌 조건 | fail | exit 1"})

    fresh = [b for b in relay2.backlogs if b.backlog_id != b2.backlog_id]
    assert len(fresh) == 1 and fresh[0].status != "done"
