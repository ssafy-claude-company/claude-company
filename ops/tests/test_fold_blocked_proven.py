"""[실증된 주기는 끝난다 — blocked까지(2026-08-05, 사용자: '아직도 산출물 결과가 안나오는게 말이 안되지')]

실측: U-504는 조건 1/1 실증인데 blocked 4건(선행 산출물·배포 404)이 주기를 28시간+ 인질로 잡았고,
실험 A·B도 같은 모양(WebKit 전수·배포 후 검증). blocked 사유는 대부분 환경·선행 인프라라 판 안에서
시간이 지나도 풀리지 않는다. 조건이 실증된 주기에서 blocked는 사유째 이월 보존으로 접힌다 —
07-14 계약('손 댄 일감은 남는다')을 08-03/08-05 정본이 대체."""
import sys, types

sys.path.insert(0, __file__.rsplit('/ops/', 1)[0])
from system.rule.backlog import BacklogRelay, fold_blocked_on_proven_cycle


def _flow_ms():
    r = BacklogRelay("ST-1")
    r.submit(0, "선행 배포 URL 검증", force=True)
    b = r.backlogs[0]
    r.pick(11, b.backlog_id, 11)
    r.block(11, b.backlog_id, 0, "배포 URL 404 — 인프라 선행 필요")
    st = types.SimpleNamespace(st_id="ST-1", status="open")
    ms = types.SimpleNamespace(subtasks=[st])
    flow = types.SimpleNamespace(backlog_relays={"ST-1": r}, log=None)
    return flow, ms, b


def test_실증된_주기의_blocked는_사유_보존과_함께_접힌다():
    flow, ms, b = _flow_ms()
    folded = fold_blocked_on_proven_cycle(flow, ms)
    assert folded == ["ST-1::B1"]
    assert b.status == "dropped"
    assert "이월" in b.note and "404" in b.note      # 지우지 않고 사유째 보존


def test_done_단계는_건드리지_않는다():
    flow, ms, b = _flow_ms()
    ms.subtasks[0].status = "done"
    assert fold_blocked_on_proven_cycle(flow, ms) == []
    assert b.status == "blocked"
