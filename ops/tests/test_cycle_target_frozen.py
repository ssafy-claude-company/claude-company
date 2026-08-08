"""닫히는 주기의 과녁은 고정된다 (2026-08-06, 실측 U-478·U-496).

[실측] backlog 단계까지 간 판의 꼬리를 세면 주기 마감 보류가 U-478 21회 · U-496 8회다. 일은 실제로
됐다 — 백로그 완료 124·129건, 마일스톤 완수 6·6건. 그런데 주기가 닫히지 않는다.

U-478 채널에는 사람이 직접 넣은 지시가 남아 있다:

    [운영자 지시 — 주기를 닫으세요] ST-10·ST-11 같은 추가 분해는 **과녁을 계속 옮겨
    28시간째 주기가 닫히지 않고 있습니다**

주기가 정리(wrapup)에 들어간 뒤에도 새 작업 영역을 계속 열 수 있었다. 닫으려는 순간 새 일이 생기니
완수 조건이 영원히 미충족이다. 사용자 계약 '실증된 주기는 끝난다'는 과녁 고정을 전제로 한다.

새 일을 막는 것이 아니라 **이번 주기에 넣는 것**을 막는다 — 다음 주기의 몫이다.
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import Milestone, open_subtask


class _Flow:
    def __init__(self):
        self.current = types.SimpleNamespace(task_id="T-1")
        self.log = None
        self.workspace = ""
        self.backlog_relays = {}
        self.milestones = []


def _ms(status):
    m = Milestone(ms_id="MS-1", goal="이번 주기", criteria=[])
    m.status = status
    return m


def test_열린_주기에는_영역을_추가할_수_있다():
    """주기 중 추가는 계약 §2 그대로 — 이 관문은 닫히는 주기에만 선다."""
    st = open_subtask(_Flow(), _ms("open"), "구현", [])
    assert not isinstance(st, str), st


def test_정리_단계_주기에는_새_영역을_못_연다():
    st = open_subtask(_Flow(), _ms("wrapup"), "추가 분해", [])
    assert isinstance(st, str) and "과녁이 고정" in st


def test_끝난_주기에도_못_연다():
    st = open_subtask(_Flow(), _ms("done"), "추가 분해", [])
    assert isinstance(st, str) and "다음 주기" in st


def test_거절은_다음_행동을_지시한다():
    """막기만 하면 같은 도구를 다시 부른다 — 어디에 넣어야 하는지 말한다."""
    st = open_subtask(_Flow(), _ms("wrapup"), "추가 분해", [])
    assert "마일스톤 회의" in st and "다음 주기" in st
