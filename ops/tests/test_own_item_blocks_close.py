"""남은 게 내 손에 있으면 보고가 아니라 그 일을 끝내야 한다 (2026-08-07 실측 U-536).

주기 2의 완수조건이 1/1 통과했는데도 30분 동안 단계가 닫히지 않았다. 로그는 같은 세 줄을 반복했다:

    iter_backlog_registered  B7  passed=true  st=MS-138398995-2/ST-5
    subtask_left_open        st=ST-5  left=1  ids=B1  states=B1:in_progress
    iter_backlog_registered  B8  passed=true  st=MS-138398995-2/ST-5
    subtask_left_open        st=ST-5  left=1  ids=B1  states=B1:in_progress

30분에 iter_backlog_registered 24회 · 진전 0. 잔여는 언제나 `B1:in_progress` 하나였고, 그 B1은
**보고를 하는 본인이 들고 있던 일감**이었다. 그런데 안내문은 이랬다:

    "백로그 완료 기록 — SubTask ST-5 잔여 1건. 다음 수행자를 pick_backlog(id)로 선정하세요"

그건 주인 없는 일감에게 하는 말이다. 이미 자기가 assignee인 in_progress 일감에는 할 수 있는
행동이 없어서, 봇은 보고만 되풀이했다.

잔여가 전부 '내가 진행 중'이면 그 사실을 그대로 말한다 — 보고로는 닫히지 않으니 그 일을 끝내라고.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import milestone as M


def test_내_진행중_일감이_잔여면_그걸_말한다():
    src = inspect.getsource(M)
    i = src.find('flow.log("subtask_left_open"')
    assert i > 0, "잔여 로그 자리가 사라졌다"
    block = src[i:i + 2000]
    assert "_mine_left" in block, "잔여의 주인이 누구인지 보지 않는다"
    assert "보고를 다시 하는 것으로는 닫히지 않습니다" in block, "되풀이를 끊는 말이 없다"


def test_남의_일감이_섞이면_종전_안내다():
    """다른 사람이 들고 있거나 주인 없는 일감이 하나라도 있으면 '다음 수행자 선정'이 맞는 말이다."""
    src = inspect.getsource(M)
    i = src.find('flow.log("subtask_left_open"')
    block = src[i:i + 2000]
    assert "len(_mine_left) == len(_left)" in block, "일부만 내 것일 때도 같은 말을 한다"
    assert "다음 수행자를 " in block, "종전 안내 경로가 사라졌다"


def test_주인_판정은_상태와_담당을_함께_본다():
    src = inspect.getsource(M)
    i = src.find("_mine_left = [b for b in _left")
    assert i > 0
    block = src[i:i + 260]
    assert 'b.status == "in_progress"' in block and "assignee" in block
