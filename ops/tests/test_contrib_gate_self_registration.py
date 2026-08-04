"""[기여 관문 — 흡수의 척도(2026-08-04, 사용자: '위임이 뭐야 위임이란건 존재하지 않아')]

흡수 차단은 'Work를 한 번도 못 받았다'(work_delegated_to 밖)로 흡수를 판정해 왔다. 그건 리더가
전문가에게 일을 맡기던 시절의 척도다. 지금 판의 배분 원리는 **자기 등재·자기선택**이라 아무도
남에게 일을 맡기지 않는다 — 일감은 본인이 등재해 집는다. 그래서 work_delegated_to는 영영 비고,
회의에 참여한 사람은 전원 영구 차단되어 마감이 구조적으로 불가능했다.

실측 U-478(3일·4318 이벤트): 흡수로 잡힌 4명은 발언권에 59회 응찰했고 그중 2명은 자기 일감을
5건 등재했다. 아무도 그들에게 '맡기지' 않았을 뿐 기회는 갔는데, 판은 마감 시도 44회를 거부당한 채
3일을 돌았다.

기회의 척도를 판의 원리에 맞춘다: 발언권 응찰을 받았거나(등재로 가는 유일한 통로가 열렸다) 자기
일감을 등재했으면 흡수가 아니다. 둘 다 없는 사람은 판에 한 번도 불려 나오지 않은 것이고 그때의
흡수는 진짜다."""
import sys, types, pytest

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.task_gates import _had_the_floor, _gate_contrib


class _Relay:
    def __init__(self, backlogs):
        self.backlogs = backlogs


class _B:
    def __init__(self, submitter, assignee=None, status="open"):
        self.submitter = submitter
        self.assignee = assignee
        self.status = status


class _Cur:
    def __init__(self):
        self.contrib_checked = False
        self.participated = {11, 12, 13}
        self.work_delegated_to = set()
        self.collab_notes = ""
        self.task_id = "T-1"


class _Flow:
    def __init__(self, bidders=(), backlogs=()):
        self.floor_bidders = set(bidders)
        self.backlog_relays = {"s": _Relay(list(backlogs))} if backlogs else {}
        self.act_by = {}
        self.current = _Cur()
        self.log = None
        self.team = {11, 12, 13}

    def _info(self, m):
        return {11: "게임 기획자", 12: "QA", 13: "사운드"}.get(int(m), "")

    def _names(self, ms):
        return " · ".join(self._info(m) for m in ms)


def _run(flow, members=(11, 12, 13), result=""):
    return _gate_contrib(flow, {"result": result}, list(members), True, None, None)


def test_응찰만_받아도_기회는_간_것이다():
    """발언권 응찰을 받았으면 등재 통로가 열린 것 — 스스로 패스한 것도 본인 판단이다."""
    assert _had_the_floor(_Flow(bidders=(11, 12))) == {11, 12}


def test_자기_일감을_등재했으면_기회는_간_것이다():
    """통로를 실제로 쓴 사람 — 완료 여부와 무관하게 등재 사실만으로 흡수가 아니다."""
    assert _had_the_floor(_Flow(backlogs=[_B(submitter=13)])) == {13}


def test_한_번도_안_불려나온_사람만_흡수다():
    """응찰도 등재도 없는 사람 — 판에 나온 적이 없으니 진짜 흡수."""
    assert 12 not in _had_the_floor(_Flow(bidders=(11,), backlogs=[_B(submitter=13)]))


def test_응찰_이력이_있으면_기여불필요로_마감된다():
    """[핵심 회귀 — U-478 3일 교착] 종전엔 work_delegated_to가 비어 전원 흡수라 [기여 불필요]가
    막혔다. 응찰을 받은 사람은 기회가 간 것이므로 명시 탈출구가 열린다."""
    flow = _Flow(bidders=(11, 12, 13))
    assert _run(flow, result="[기여 불필요] 이번 범위에 그 도메인 없음") is None


def test_한_번도_안_불려나오면_기여불필요로_못_넘긴다():
    """관문의 원래 취지는 그대로 — 기회조차 못 받은 도메인은 한 줄로 묵살되지 않는다."""
    flow = _Flow(bidders=(11,))
    err = _run(flow, result="[기여 불필요] 필요 없었음")
    assert err and "흡수 차단" in err


def test_관문_문구에_위임이_남아있지_않다():
    """[용어(2026-08-04, 사용자)] 이 판에 '위임'이라는 절차는 없다 — 관문이 없는 행동을 시키면
    봇은 통과할 방법이 없다. 출구는 '자리를 여는 것'으로 서빙된다."""
    flow = _Flow(bidders=(11,))
    err = _run(flow, result="[기여 불필요] 필요 없었음")
    assert "위임" not in err, err
    assert "request(Work)" not in err, err
    assert "회의에서 그 도메인의 단위를 열면" in err


def test_기회가_갔어도_명시_없는_재호출은_통과_안_한다():
    """[증거/명시 통과] 반사적 재호출 차단은 종전 그대로."""
    flow = _Flow(bidders=(11, 12, 13))
    err = _run(flow, result="")
    assert err and "완료 보류" in err
