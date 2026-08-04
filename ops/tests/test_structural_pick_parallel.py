"""[등재자=담당, 불변 + 구조 픽은 배분권 밖(2026-08-04, 사용자)]

정본: '백로그는 백로그 등록자가 담당하고 바뀔 수 없거든'(2026-08-04). 개인이 등재한 일감의 담당은
등재자 자신뿐이고, 무주(수렴안 서기 0)만 집는 사람이 담당이 된다. 병렬은 담당을 바꿔서가 아니라
등재자들이 각자 자기 일감을 동시에 굴려서 온다 — 구조 픽(등재 순서대로 다음 등재자를 세우는 기계)은
배분권 검사만 건너뛴다(실측 U-496·U-504: 개인 등재 판에서 배분권 게이트에 막혀 24h 차선 추가 0회)."""
import os, sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
import pytest
from system.rule.backlog import BacklogError, BacklogRelay


def _relay():
    r = BacklogRelay("ST-1")
    r.submit(11, "첫 일감 — 데이터 계약을 고정한다", force=True)      # 등재자 11
    r.submit(13, "둘째 일감 — 화면 렌더링을 붙인다", force=True)      # 등재자 13
    return r


def test_구조_픽은_등재자를_세운다_배분권_밖():
    r = _relay()
    b1 = r.backlogs[0]
    r.pick(11, b1.backlog_id, 11)
    r.done(11, b1.backlog_id, "완료")          # 11이 배분권자
    b2 = r.backlogs[1]
    # 구조 픽 — 배분권(11)이 아니어도 등재자(13)를 세운다
    b = r.pick(13, b2.backlog_id, 13, structural=True)
    assert b.status == "in_progress" and b.assignee == 13


def test_등재자가_아닌_담당은_구조라도_불가():
    """[정본] 담당은 등재자뿐 — 구조 픽이라도 남에게 배정하지 못한다."""
    r = _relay()
    b2 = r.backlogs[1]                          # 등재자 13
    with pytest.raises(BacklogError):
        r.pick(12, b2.backlog_id, 12, structural=True)


def test_배분권자도_남의_일감을_남에게_지명_못한다():
    r = _relay()
    b1 = r.backlogs[0]
    r.pick(11, b1.backlog_id, 11)
    r.done(11, b1.backlog_id, "완료")           # 11 = 배분권자
    b2 = r.backlogs[1]                          # 등재자 13
    with pytest.raises(BacklogError):
        r.pick(11, b2.backlog_id, 12)           # 지명이어도 담당은 등재자만


def test_무주_일감은_집는_사람이_담당():
    r = BacklogRelay("ST-2")
    r.submit(0, "수렴안 서기 등재 — 팀 산물", force=True)
    b = r.backlogs[0]
    got = r.pick(12, b.backlog_id, 12)          # 자기선택
    assert got.assignee == 12


def test_구조_픽도_동시_상한은_그대로():
    os.environ["ORGANT_BACKLOG_PARALLEL"] = "1"
    try:
        r = _relay()
        b1, b2 = r.backlogs[0], r.backlogs[1]
        r.pick(11, b1.backlog_id, 11)
        with pytest.raises(BacklogError):
            r.pick(13, b2.backlog_id, 13, structural=True)
    finally:
        os.environ.pop("ORGANT_BACKLOG_PARALLEL", None)


def test_구조_픽도_상태_검사는_그대로():
    r = _relay()
    b1 = r.backlogs[0]
    r.pick(11, b1.backlog_id, 11)
    r.done(11, b1.backlog_id, "완료")
    with pytest.raises(BacklogError):
        r.pick(11, b1.backlog_id, 11, structural=True)
