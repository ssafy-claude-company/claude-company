"""접은 것은 보이게 접는다 (2026-08-07 실측 U-536).

주기 1의 완수조건이 실증되자(`[iter 검증] 1차 — 충족 1/1 · 전 조건 충족`) 화면의 다섯 영역이
전부 '완료'로 섰다. 그런데 그중 '배포·인프라'는 백로그 0/1이었다. 로그를 따라가면 이렇다:

    backlog_turn_incomplete  ST-11 B1 drives=4
    backlog_turn_incomplete  ST-11 B1 drives=5
    backlog_drive_cap_blocked ST-11 B1 drives=6
    blocked_folded_on_proven_cycle  n=1  backlogs=["MS-120642122-1/ST-11::B1"]

배포 일감이 여섯 번 막힌 뒤(원인은 npm 캐시 권한 — 같은 날 따로 수리) 조건 실증과 함께 접혔다.
접는 것 자체는 2026-08-03 정본이다('근본 문제가 해결됐는데 잔여 문제여도 끝나도록 구조적으로
유도'). 문제는 **그 사실이 로그에만 있었다**는 것이다 — 판에는 '완료'만 보이고, 공개 URL은
만들어지지 않았는데 사람은 그것을 알 방법이 없었다.

끝나도록 유도하는 규칙은 그대로 두고, 무엇을 두고 끝냈는지는 판에 남긴다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import backlog as B


def test_접힌_일감이_판에_남는다():
    src = inspect.getsource(B)
    i = src.find('flow.log("blocked_folded_on_proven_cycle"')
    assert i > 0, "이월 보존 경로가 사라졌다"
    block = src[i:i + 1400]
    assert "_pnote(flow" in block, "접은 사실이 로그에만 남는다"
    assert "[이월]" in block, "판에 설 라벨이 없다"


def test_몇_건인지와_왜인지가_함께_간다():
    src = inspect.getsource(B)
    i = src.find('flow.log("blocked_folded_on_proven_cycle"')
    block = src[i:i + 1400]
    assert "len(folded)" in block, "몇 건을 접었는지 안 적는다"
    assert "_why0" in block, "무엇 때문에 막혔는지 안 적는다"


def test_끝나도록_유도하는_규칙은_그대로다():
    """보이게 하는 것과 막는 것은 다르다 — 접기 자체는 종전대로 진행된다(08-03 정본)."""
    src = inspect.getsource(B)
    i = src.find("def fold_blocked_on_proven_cycle")
    if i < 0:
        i = src.find("blocked_folded_on_proven_cycle")
    block = src[max(0, i - 200):i + 1600]
    assert "b.status = DROPPED" in block, "접기가 사라졌다"


def test_새_종류는_정본에_등록돼_있다():
    from guide.msgkind import MSG_KINDS, kind_of
    assert "[이월]" in MSG_KINDS["milestone"]
    assert kind_of("[이월] 이번 주기 조건이 실증돼 막힌 일감 1건을 다음으로 넘깁니다") == "milestone"
