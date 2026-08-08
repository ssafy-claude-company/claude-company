"""막은 것을 사람에게 그대로 넘긴다 (2026-08-07 실측 U-535).

criteria 단계가 파킹으로 끝났다. 화면에 남은 두 문장은 이랬다:

    [회의 마무리] 미결 (**무엇이 되면 이 Task가 끝인가**를 정한다) — 막은 것: 완수조건에
      **존재이유 테스트**가 없습니다 …
    [막혀서 멈췄어요] … — 막힌 지점 — **무엇이 되면 이 Task가 끝인가**를 정한다

둘 다 사람이 조치할 수 없는 문장이다.

  ① 마무리의 '막은 것'은 **이미 해소된** 관문 반려였다. 13994에서 존재이유 테스트가 없다고
     반려됐고 13995에서 팀이 `[존재이유]` 조건을 넣었다. 그 뒤 실제로 회의를 끝낸 것은 표결
     3연속 부결(13996·13997·13998)이다. `_last_block`이 등록 관문 반려에서만 쓰여, 나중에
     일어난 부결이 앞의 문구를 덮지 못했다 — 마감 문구가 사실과 달랐다.

  ② 파킹 안내의 '막힌 지점'은 `flow._stage_stuck`인데 거기엔 **안건**만 들어갔다. 사람은
     '무엇이 되면 끝인가를 정한다'는 문장만 보고 한 줄을 답해야 했다. 실제로 막은 것
     ('위험 유형별 움직임 차이를 검증하는 인수 조건이 빠졌다')은 어디에도 실리지 않았다.

뒤에 일어난 것이 마감 문구를 이기고, 파킹 안내는 안건과 막은 것을 함께 싣는다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import communication as C


def _src():
    return inspect.getsource(C)


def test_부결도_막은_것으로_기록된다():
    src = _src()
    i = src.find("elif not _passed:")
    assert i > 0, "부결 분기가 사라졌다"
    block = src[i:i + 1600]
    assert "_last_block = (" in block, "부결이 막은 것으로 기록되지 않는다"
    assert "마지막 반대:" in block, "무엇을 반대했는지가 실리지 않는다"


def test_관문_반려도_계속_기록된다():
    """부결 기록을 넣으면서 관문 반려 기록을 지우면 안 된다 — 둘 다 회의를 막은 사건이고,
    같은 변수를 시간순으로 덮어쓴다(마지막에 일어난 것이 마감 문구가 된다)."""
    src = _src()
    assert '_last_block = str(_note or "")[:300]' in src, "등록 관문 반려 사유가 유실된다"


def test_반대가_없으면_기존_사유를_지우지_않는다():
    """예산 소진 등으로 _diss가 비면 앞서 기록한 관문 반려가 유일한 사실이다."""
    src = _src()
    i = src.find("elif not _passed:")
    block = src[i:i + 1600]
    assert "if _diss else _last_block" in block, "반대가 없을 때 사유가 지워진다"


def test_파킹_안내가_막은_것을_싣는다():
    src = _src()
    i = src.find("if flow._consec_stuck >= 2:")
    assert i > 0
    block = src[i:i + 900]
    assert "막은 것:" in block, "파킹 안내에 막은 것이 없다"
    assert "_last_block" in block, "안건만 싣고 실제 사유를 안 싣는다"


def test_안건도_함께_남는다():
    """막은 것만 남기면 어느 단계에서 막혔는지가 사라진다 — 둘 다 싣는다."""
    src = _src()
    i = src.find("if flow._consec_stuck >= 2:")
    block = src[i:i + 900]
    assert "_agenda or topic" in block
