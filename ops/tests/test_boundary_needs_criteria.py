"""끝의 기준이 없는 Task는 끝날 수 없다 (2026-08-06, 현준-1 — 사용자: '개입으로 계속 방향 트는게
아니라 구조적으로 인도되어야지').

[규명] 완수조건 회의(criteria)는 meeting_stage에서 **마일스톤이 하나도 없을 때만** 열린다
(`if not acceptance and not _mss0`). 그 창을 놓친 판은 조건이 빈 채로 주기를 돌고, 로드맵을 다
돈 뒤에도 e2e가 열리지 못한다 — e2e 분모(조건 축)가 없기 때문이다. 그러면 그 아래 비준 분기가
**마일스톤 회의**를 열고, 그 회의 골격은 '이번에 완성해 보여줄 하나'를 묻는다. 팀은 새 주기를
정의하고, 그 주기가 끝나면 정확히 같은 자리로 돌아온다.

실측 U-496: GOAL.md의 `## Acceptance`가 빈 채로 3.5일 · 6주기 · $219 · 1,386턴, e2e 이벤트 0건.
6번째 주기의 안건은 '브라우저 실행·배포'였는데 그 산출물은 이미 완성작 경로로 200을 답하고 있었다.

[수리] 로드맵을 다 돌아 열 주기가 없는데 완수조건이 비어 있으면 criteria 회의로 인도한다.
사람이 매번 '새 주기 말고 마감'이라고 방향을 틀어 주는 대신, 구조가 그 회의 하나로 데려간다.
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import meeting_stage


class _MS:
    def __init__(self, status="done", goal="주기", origin=""):
        self.status = status
        self.goal = goal
        self.origin = origin
        self.subtasks = []
        self.criteria = []


class _Cur:
    def __init__(self, acceptance=""):
        self.task_id = "T-1"
        self.status = types.SimpleNamespace(goal="피하기·수집 게임 1종")
        self.acceptance = acceptance
        self.team = [11, 12]


class _Flow:
    def __init__(self, acceptance="", n_done=2, roadmap=None):
        self.current = _Cur(acceptance)
        self.milestones = [_MS() for _ in range(n_done)]
        self.roadmap = roadmap if roadmap is not None else ["완성판"]
        self.backlog_relays = {}
        self.log = None
        self.workspace = ""


def test_로드맵_소진_후_완수조건이_비면_기준_회의로_인도된다():
    """새 주기가 아니라 '무엇이 되면 끝인가' 회의 — 그래야 e2e 분모가 선다."""
    assert meeting_stage(_Flow(acceptance="")) == "criteria"


def test_완수조건이_있으면_경계로_나아간다():
    """조건이 서 있으면 더 열 회의가 없다(=작업/완료 단계 → e2e 경계)."""
    assert meeting_stage(_Flow(acceptance="조건 | 실증: node verify.mjs")) is None


def test_로드맵이_남았으면_종전대로_주기_회의다():
    """사다리가 남은 판은 그대로 다음 주기를 연다 — 이 수리가 진행을 가로채지 않는다."""
    f = _Flow(acceptance="", n_done=1, roadmap=["최소판", "확장판", "완성판"])
    assert meeting_stage(f) == "milestone"


def test_첫_판은_종전_경로_그대로():
    """마일스톤이 아직 없으면 종전 규칙(위쪽 관문)이 criteria를 연다 — 이중 수용."""
    f = _Flow(acceptance="", n_done=0, roadmap=[])
    assert meeting_stage(f) == "criteria"
