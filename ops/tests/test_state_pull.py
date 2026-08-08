"""판 상태는 밀지 않고, 필요한 조각만 가져다 쓴다 (2026-08-07, 사용자: '의도적으로 계속 주입하는
거 보다는 직접 필요한 만큼 가져다 쓰는 정보 구조 체계가 중요하겠지?').

[실측] 도구 호출 61,540건 중 Read 20,532 · Glob 7,147 · Grep 2,256이고, 그중 **같은 봇이 같은
경로를 다시 읽은 것이 27,169회(전체 읽기의 91%)**였다. 한 봇이 TURNS.md를 203회, DRAFT.md를
164회 읽었다. 읽은 전문은 그 턴 문맥에 남아 이후 모든 스텝에 다시 실린다 — 한 턴 입력이 26M~42M
까지 부푸는 경로가 이것이다(p50은 88K다. 꼬리만 폭주한다).

[먼저 틀린 길] 판 상태를 프롬프트에 실었다(push). 재독을 없앤 만큼을 주입으로 도로 태운다 —
쓰지도 않는 턴에 실리고, 바뀔 때마다 전문이 다시 간다. 상한(입력 컷)은 더 나쁘다: 복원을 중간에
끊으면 다음 턴이 처음부터 다시 읽는다.

[구조] 문제는 봇이 묻는다는 것이 아니라 **묻는 단위가 문서 한 채**라는 것이었다. 조각 단위로
답하는 창구(state)를 열되, 프롬프트로는 아무것도 알려 주지 않는다. 도구는 세션에 선적재되므로
(ENABLE_TOOL_SEARCH=false) 봇은 도구 목록으로 그것을 알고, 필요할 때 필요한 조각만 부른다.
잊으면 관문이 반려하고 그 반려문이 다시 알려 준다 — 구조가 가르치지 프롬프트가 미리 말하지 않는다.
"""
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.tool_names import FLOW_TOOLS


def test_창구는_전원_허용목록에_있다():
    """등록만 되고 허용에서 빠지면 런타임 권한거부 — 한 세트로 연다."""
    assert "mcp__guide__state" in FLOW_TOOLS


def test_프롬프트는_상태를_주입하지_않는다():
    """세 번 틀린 자리 — 상한 컷·전체 주입·변경 주입. 지금은 비어 있어야 한다."""
    import inspect

    from system import sys_prompt as SP
    src = inspect.getsource(SP)
    assert "_state_access_note" not in src
    assert "_ledger_note" not in src


def test_창구는_조각만_답한다():
    """도구 설명 자체가 발견 경로다 — 무엇을 물을 수 있는지가 거기 있어야 한다."""
    import inspect

    from system import guide_tools as GT
    src = inspect.getsource(GT)
    i = src.find('@tool("state"')
    assert i > 0, "state 창구가 없다"
    desc = src[i:i + 600]
    for k in ("goal", "acceptance", "cycle", "areas", "mine"):
        assert k in desc, k
    assert "통째로 Read하지 말고" in desc, "왜 이걸 써야 하는지가 설명에 없다"


def test_창구는_읽기_전용이다():
    """상태를 바꾸는 길이 아니다 — 결정은 회의가, 등재는 관문이 한다."""
    import inspect

    from system import guide_tools as GT
    src = inspect.getsource(GT)
    i = src.find('@tool("state"')
    j = src.find("tools.append(state)", i)
    block = src[i:j]
    for w in ("= ", "append(", "pop("):
        pass
    assert "flow.current =" not in block and "_ckpt(" not in block


def test_제일_먼저_묻는_것이_있어야_한다():
    """[실측 U-534] 유일한 실무자가 state(what="team")을 불렀는데 목록에 없어 안내문만 돌아갔다.
    그러자 그 봇은 정족수를 스스로 채우려고 **자기 자신과 채용 봇을 recruit**하고, 자기 혼자만
    참가자로 넣은 meet를 두 번 열었다. 누가 이 판에 있는지가 첫 질문이다."""
    import inspect

    from system import guide_tools as GT
    src = inspect.getsource(GT)
    i = src.find('@tool("state"')
    block = src[i:i + 900]
    assert "team(동료와 직군)" in block, "team 조각이 도구 설명에 없다"
    assert '"team": _team' in src, "team 조각이 구현에 없다"


def test_자기_자신은_뽑을_수_없다():
    """같은 사람이 두 번 세어지는 길을 막는다 — 정족수는 다른 도메인 동료로 채운다."""
    import inspect

    from system.rule import comm_ceremonies as CC
    src = inspect.getsource(CC)
    assert "recruit_declined_self" in src
    i = src.find("recruit_declined_self")
    assert "int(mid) == int(me_id)" in src[max(0, i - 400):i]
