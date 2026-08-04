"""[구조 픽은 배분권 밖(2026-08-04, 사용자: '시스템적으로 직렬적인 부분을 병렬로 바꾸기로 했잖아')]

병렬 차선 추가(2026-07-31)는 구조가 등재 순서대로 다음 (일감, 수행자)를 세우는 기계인데, 회의가
등재한 일감에 개인 등재자가 박힌 판에서는 자기 등재 조건이 안 서고 배분권 게이트에 걸려 —
실측 U-496·U-504: 24h 차선 추가 0회·거부 7회("배분권은 마지막 작업자에게"), 판이 영원히 한 줄.
수렴안 서기(0) 등재 판만 차선 4개까지 병렬 — 같은 기계가 등재자 표기 하나로 갈렸다.

배분권이 지키는 것은 '남을 마음대로 지명하지 못하게'다. 등재 순서 그 자체인 구조 픽은 배분권
검사만 건너뛴다 — 동시 상한·상태 검사는 그대로."""
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
import pytest
from system.rule.backlog import BacklogError, BacklogRelay


def _relay():
    r = BacklogRelay("ST-1")
    r.submit(11, "첫 일감 — 데이터 계약을 고정한다", force=True)     # 등재자 11
    r.submit(13, "둘째 일감 — 화면 렌더링을 붙인다", force=True)     # 등재자 13(제3자)
    return r


def test_개인등재_판에서도_구조_픽은_차선을_세운다():
    r = _relay()
    b1 = r.backlogs[0]
    r.pick(11, b1.backlog_id, 11)          # 자기 등재 자기 착수 — 종전 규칙 그대로
    r.done(11, b1.backlog_id, "완료")       # 11이 턴 홀더(배분권자)가 된다
    b2 = r.backlogs[1]
    # 종전: 12의 자기선택은 배분권(11)에 막힘 — 그대로다(남 지명 방지 질서 유지)
    with pytest.raises(BacklogError):
        r.pick(12, b2.backlog_id, 12)
    # 구조 픽(등재 순서대로 다음 차례를 세우는 기계)은 통과한다
    b = r.pick(12, b2.backlog_id, 12, structural=True)
    assert b.status == "in_progress" and b.assignee == 12


def test_구조_픽도_동시_상한은_그대로():
    import os
    os.environ["ORGANT_BACKLOG_PARALLEL"] = "1"
    try:
        r = _relay()
        b1, b2 = r.backlogs[0], r.backlogs[1]
        r.pick(11, b1.backlog_id, 11)
        with pytest.raises(BacklogError):
            r.pick(12, b2.backlog_id, 12, structural=True)   # 상한 1 — 구조라도 못 넘는다
    finally:
        os.environ.pop("ORGANT_BACKLOG_PARALLEL", None)


def test_구조_픽도_상태_검사는_그대로():
    r = _relay()
    b1 = r.backlogs[0]
    r.pick(11, b1.backlog_id, 11)
    r.done(11, b1.backlog_id, "완료")
    with pytest.raises(BacklogError):
        r.pick(12, b1.backlog_id, 12, structural=True)       # done은 못 집는다
