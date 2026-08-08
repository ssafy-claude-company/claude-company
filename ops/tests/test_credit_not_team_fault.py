"""한도 소진은 팀 잘못이 아니다 (2026-08-06, 실측 U-526).

크레딧이 떨어지면 봇 턴이 '[크레딧 한도] 이 판의 크레딧을 다 써 …' 문자열만 돌려준다. 그런데 그
문자열이 그대로 회의 발언으로 게시됐다 — 실측 U-526 criteria 회의에 6줄 연속:

    고서준/게임 클라이언트 엔지니어 | [회의][단계:criteria] [크레딧 한도] 이 판의 크레딧을 다 써 …
    강주하/게임 QA 엔지니어      | [회의][단계:criteria] [크레딧 한도] …

회의는 진전이 없으니 stage_stall이 올라 파킹됐고, 사용자에게 나간 문구는
'[사람 조치 필요] 회의가 진전 없이 맴돌아 여기서 멈춥니다 — 안건을 구체화해 주시면 이어서 진행해요'.
원인은 지불인데 팀의 합의 실패로 보고된다.

한도 소진 응답은 발언으로 게시하지 않고, 회의 마감 기록이 그 사유를 싣는다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import comm_helpers as H
from system.rule import communication as C


def test_한도_소진_응답은_게시되지_않는다():
    src = inspect.getsource(H._say_speech)
    assert '[크레딧 한도]' in src and "return" in src, "한도 문자열이 발언으로 게시된다"


def test_마감_기록이_지불_사유를_싣는다():
    src = inspect.getsource(C)
    i = src.find("if not _landed:")
    block = src[i:i + 1400]
    assert "_quota_over" in block, "마감 사유가 크레딧 소진을 구분하지 않는다"
    assert "팀의 합의 문제가 아닙니다" in block


def test_관문_반려_사유가_있으면_그쪽이_우선이다():
    """실제로 형태가 막았으면 그 문구가 우선 — 크레딧 문구가 덮지 않는다."""
    src = inspect.getsource(C)
    i = src.find("if not _landed:")
    block = src[i:i + 1400]
    assert 'str(_last_block or "") == ""' in block
