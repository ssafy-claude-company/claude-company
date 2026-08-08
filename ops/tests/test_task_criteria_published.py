"""Task가 정한 완수조건은 화면이 값으로 받는다 (2026-08-07 실측 U-536).

criteria 회의는 이 Task가 **무엇이 되면 끝인가**를 정한다. U-536에서 그 회의는 두 번 열려
완수조건 17개를 등록했다. 그런데 판 화면의 Task 카드에는 이렇게 떴다:

    완수조건                                   1개

카드가 세던 것은 **마일스톤 조건의 합집합**이었다(주기가 하나뿐이라 1개). Task 자체의 인수조건은
장부(`_cur.acceptance`)와 GOAL.md에만 살고, 채널에는 '완수조건 17개 등록'이라는 **개수 문장**만
나갔다 — 팀이 두 회의를 들여 정한 것이 판에서 읽히지 않았다.

등록하는 그 자리에서 목록을 구조 값(`payload.crit`)으로 함께 싣는다. 화면은 문장을 되짚지 않고
그 값을 읽는다. 종류(`[완수조건]`)는 msgkind 정본에 등록돼 있어야 매체가 받는다 — 안 적으면
게시가 반려된다(그게 이 체계의 규칙이다).
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import milestone as M


def test_pnote가_구조값을_함께_싣는다():
    src = inspect.getsource(M._pnote)
    assert "meta=None" in src, "_pnote가 구조 값을 받지 않는다"
    assert "notes.append((t, meta) if meta else t)" in src


def test_게시가_구조값을_payload로_넘긴다():
    src = inspect.getsource(M.flush_pipeline_notes)
    assert "isinstance(t, tuple)" in src, "누적된 구조 값을 풀지 않는다"
    assert "meta=_meta" in src, "payload로 넘기지 않는다"


def test_조건_등록이_목록을_게시한다():
    src = inspect.getsource(M)
    i = src.find('"[완수조건] 이 Task가 끝나는 조건')
    assert i > 0, "등록 자리에서 완수조건 목록을 게시하지 않는다"
    block = src[i:i + 400]
    assert '"crit"' in block, "구조 키가 없다"
    assert '"d"' in block and '"v"' in block, "조건 본문·실증이 값으로 안 실린다"


def test_개수_문장은_그대로_남는다():
    """목록 게시가 종전 등록 응답을 밀어내지 않는다 — 도구 반환문은 봇이 읽는 자리다."""
    src = inspect.getsource(M)
    assert 'f"완수조건 {len(_crits)}개 등록 — 이제 이번 주기(마일스톤)를 정합니다."' in src


def test_새_종류는_정본에_등록돼_있다():
    """[체계에 맞는 데이터만 받는다] 목록에 없으면 매체가 400으로 반려한다 — 먼저 적는다."""
    from guide.msgkind import MSG_KINDS, kind_of
    assert "[완수조건]" in MSG_KINDS["milestone"]
    assert kind_of("[완수조건] 이 Task가 끝나는 조건 17개 확정") == "milestone"
