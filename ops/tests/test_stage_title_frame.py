"""첫 프레임이 회의의 정체를 정한다 (2026-08-06, 사용자: '회의의 첫 시작자는 뭔가 이상하게 말하는
느낌이 있는데 문제가 없는지 확인해봐').

[실측] U-514 criteria 회의 — 파이프라인 단계는 criteria('무엇이 되면 끝인가')인데 봇이 자기
topic으로 열어 제목이 '이번에 보여줄 하나 — 《달빛 정거장》의 첫 플레이 가능한 수직 슬라이스'였고,
여는 의견도 그 질문(범위)에 답했다: "이번 주기는 … 흐름 하나를 보여주는 데 집중하자고 제안합니다".
U-506도 같은 모양이다. 회의 topic은 개시 행·회의록·모든 라운드 프롬프트의 '주제'로 재사용되므로,
첫 문장이 어긋나면 회의 전체가 엉뚱한 질문을 돈다(U-514는 그 뒤 표결 1회 부결).

[수리] 단계가 정해져 있으면 제목은 그 단계의 정본 이름으로 세우고, 봇이 쓴 문장은 범위로 뒤에
붙인다 — 맥락(무엇에 대한 회의인지)은 보존하면서 질문은 단계의 것으로 고정한다.
"""
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import stage_title


def test_단계_제목이_정본이다():
    assert stage_title("criteria") == "완수 기준 정의"
    assert stage_title("milestone") == "이번 주기 정의"
    assert stage_title("goal") == "Task 목표 정의"


def test_봇_topic은_제목에_붙지_않는다():
    """[2026-08-07 개정] 07-30엔 봇 문장을 `<단계 제목> — <봇 문장>`으로 뒤에 붙였다. 그 결과 같은
    단계 회의가 제목만 다른 여러 건으로 보였다 — 실측 U-527 subtask 4건이 전부 다른 제목이었고
    그중 셋은 한 봇(게임 비주얼)이 자기 관심사로 연 것이다:

        '작업 영역 분해 — 이번에 만들 것을 어떤 작업 영역들로 나눌지…'   (SYS 개시)
        '작업 영역 분해 — 플레이어가 1초 안에 읽어야 할 시각 계층과…'
        '작업 영역 분해 — 실제로 열어 플레이·검증할 수 있도록…'
        '작업 영역 분해 — 프론트엔드 합류 후 구현·검증을 담당할…'

    안건은 단계가 정하는 하나다. 봇의 관심사는 발언으로 말한다(본문 안건은 그대로 실린다)."""
    import inspect
    from system.rule import communication as C
    src = inspect.getsource(C)
    assert "topic = _ms_title(_stage) or topic.strip()" in src, "단계 제목 정본화가 사라졌다"
    assert 'topic = f"{_canon} — {_own[:80]}"' not in src, "봇 문장이 다시 제목에 붙는다"


def test_topic_재바인딩이_클로저를_깨지_않는다():
    """meet의 내부 코루틴이 topic을 재바인딩하므로 nonlocal 선언이 있어야 한다(회귀: 38건 실패)."""
    import inspect
    from system.rule import communication as C
    src = inspect.getsource(C)
    i = src.find("async def _run_meet():")
    assert i > 0
    head = src[i:i + 300]
    assert "nonlocal topic" in head, "nonlocal topic이 빠지면 회의가 통째로 죽는다"
