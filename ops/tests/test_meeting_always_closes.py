"""모든 회의는 마무리로 닫힌다 (2026-08-06, 사용자: '회의에 종료라는건 존재하면 안되는거 아니야?'
· '데이터 계속 모두 보면서 정확한 실측으로 고쳐서 깔끔한 e2e 되도록 해봐').

[전수 계측 — 판 26개]

    회의 개시 213  ·  회의 마무리 93  →  결론 없이 남은 회의 120건 (56%)
    같은 단계 재개설 129회

결론 없이 남은 120건을 사유별로 갈랐더니 **71건이 흔적조차 없었다**. 회의는 실제로 돌았다 —
개시 직후 3건의 유형을 세면 [회의] 82 · [심의단] 20 · [독립 의견] 16. 발언도 심의도 있었는데
**닫는 기록만 없다.**

원인은 마무리 게시 조건이었다: `if _landed and _conclusion`. 단계 결론이 등록에 착지한 회의만
마무리를 남기고, 다른 모든 출구(등록 관문 반려·합의 부결·발언 소진·예산 소진·파킹)는 아무 기록도
없이 회의를 열어 둔 채 빠져나갔다. 화면은 결론 없는 블록을 '종료'로 그리므로, 사용자가 본 '종료'는
전부 이 구멍이다.

결과와 무관하게 닫는다 — 결론이 있으면 결론을, 없으면 무엇이 막았는지를 기록으로 남긴다.
회의를 조용히 버리는 출구가 없어야 같은 단계가 왜 다시 열리는지 읽힌다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import communication as C


def _src():
    return inspect.getsource(C)


def test_착지하지_못한_회의도_마무리를_남긴다():
    src = _src()
    i = src.find("if not _landed:")
    assert i > 0, "미착지 출구가 마무리 없이 빠져나간다"
    block = src[i:i + 5000]
    assert '"[회의 마무리]", _txt0' in block, "미결 마무리를 게시하지 않는다"


def test_막은_것을_기록에_싣는다():
    src = _src()
    i = src.find("if not _landed:")
    block = src[i:i + 5000]
    assert "막은 것:" in block, "왜 못 닫았는지가 기록에 없다"
    assert "_last_block" in block, "마지막 관문 반려 사유를 쓰지 않는다"


def test_관문_반려_사유가_보존된다():
    src = _src()
    assert '_last_block = str(_note or "")[:300]' in src, "등록 반려 사유가 유실된다"


def test_미결_마감도_로그로_관측된다():
    """수리 뒤에도 미결 비율은 계속 측정돼야 한다 — 기록만 남기고 원인을 안 보면 같은 자리를 돈다."""
    src = _src()
    assert 'flow.log("meet_closed_without_conclusion"' in src


def test_결론이_있으면_결론을_싣는다():
    """미결 기록이 정상 결론 게시를 밀어내지 않는다 — 두 경로가 함께 산다."""
    src = _src()
    i = src.find("if not _landed:")
    j = src.find("if _landed and _conclusion:", i)
    assert j > i, "결론 게시 경로가 사라졌다"


def test_끊긴_회의도_다음_개시_때_닫힌다():
    """[2026-08-07 실측 U-528] 08-06의 미결 마감은 _run_meet의 **정상 반환 경로**에만 있었다.
    회의 도중 상위 처리가 끼면 코루틴이 그 자리에서 끝나 마무리가 안 나간다:

        13495 개시 → 직전 [채용 확정] … 열린 채
        13509 개시 → 직전 [채용 확정] … 열린 채
        13537 개시 → 직전 [마감 대기] 완주 중인 작업 1건의 결과를 회수한 뒤 마감합니다 … 열린 채

    개시 6건 중 4건이 결론 없이 남았다. 개시 순간 표시를 세우고, 다음 회의가 열릴 때 앞것을 닫는다.
    """
    src = _src()
    assert "_meet_unclosed" in src, "열린 회의 표시가 없다"
    i = src.find("_unc = getattr(flow, \"_meet_unclosed\", None)")
    assert i > 0, "새 회의를 열기 전에 앞 회의를 닫지 않는다"
    block = src[i:i + 2400]
    assert "meet_closed_interrupted" in block, "끊긴 마감이 관측되지 않는다"
    assert "상위 처리(채용 성사·위임 회수 등)" in block, "무엇이 끊었는지 기록하지 않는다"
    # [끊긴 회의도 결론을 남긴다(2026-08-07, 사용자: '어떤 회의든 결론을 뭐라도 내야하지 않음?')]
    # 실측 U-536 subtask: 발언 10건이 오갔는데 마무리에 아무 내용도 없었다.
    assert "여기까지 정한 것:" in block, "그때까지의 합의가 마무리에 안 실린다"


def test_정상_마감은_표시를_지운다():
    """정상적으로 닫힌 회의가 다음 개시 때 또 닫히면 안 된다."""
    src = _src()
    assert "flow._meet_unclosed = None      # 이 회의는 기록으로 닫혔다" in src
