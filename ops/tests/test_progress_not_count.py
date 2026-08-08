"""횟수가 아니라 진전으로 판단한다 (2026-08-07, 사용자: '애초에 그런 횟수 제한은 왜 붙어 있는거야
… 527은 다 부결이면 계속 가야지').

[전수 조사 — 심의를 횟수로 자르는 상한]

    7회 · 4개 판   표결 3회 소진 → 잔여 반대를 이월하고 결론 확정
    2회 · 1개 판   단계 재개설 상한 → 파킹
    2회 · 2개 판   마감 시도 총량(_close_total < 8)

둘 다 같은 결함이었다. 반복을 세면서 **그 반복이 같은 자리인지 고쳐 가는 중인지 보지 않는다**.

실측 U-527 subtask — 부결 2회로 파킹됐는데 사유가 서로 달랐다:
    1회 '실제로 열어 플레이할 프론트엔드·게임플레이 구현 영역과 배포·인프라 영역이 빠져 있다'
    2회 '상위 계약의 사건 결과 6종 이상이 정의·구현·검증 일감으로 명시되지 않았다'
그 사이 팀은 프론트엔드를 채용하고 DRAFT에 구현·QA·배포 영역을 추가했다.

판정 기준을 결정 구획의 변화로 바꾼다 — 자라면 진전(계수 리셋), 그대로면 맴돌이(계수 증가).
자율을 횟수로 자르지 않고, 엇나감(같은 자리 반복)에만 건다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import communication as C
from system.sys_core import Sys


def test_단계_재개설은_진전을_보고_센다():
    src = inspect.getsource(Sys)
    i = src.index('_seen_st = getattr(flow, "_stage_open_n"')
    seg = src[i:i + 3600]
    assert "_progressed" in seg and "draft_decision_region" in seg
    assert '_seen_st[str(_stg)] = 0' in seg, "진전인데 계수를 안 턴다"


def test_확정_표결_소진도_진전을_보고_센다():
    src = inspect.getsource(C)
    i = src.index("elif not _passed:")
    seg = src[i:i + 2600]
    assert "_ready_rejects = 0" in seg, "새 결함을 짚은 부결도 소진으로 센다"
    assert "_ratify_sig" in seg, "진전 판정의 근거가 결정 구획이 아니다"


def test_맴돌이는_여전히_끊는다():
    """제한을 없앤 것이 아니다 — 같은 구획으로 다시 부결하면 그대로 센다."""
    src = inspect.getsource(C)
    i = src.index("elif not _passed:")
    seg = src[i:i + 2600]
    assert "_ready_rejects += 1" in seg


def test_진전은_관측된다():
    """다음 계측에서 '몇 번이 진전이었나'를 셀 수 있어야 한다."""
    assert "ratify_reject_progress" in inspect.getsource(C)
    assert "stage_reopen_progress" in inspect.getsource(Sys)
