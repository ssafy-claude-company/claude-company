"""채용은 일감을 받지 않고, 못 뽑으면 그 사실이 보인다 (2026-08-06, 사용자: '심지어 채용이 백로그
잡음' / '무슨 채용 과정도 안보이고').

[실측 ①] 백로그 주인 폴백(_owner_fb)은 '무주 출생 금지'를 위해 적임자를 주인으로 지정한다. 그
후보 풀은 이 판의 팀이지만, 팀이 비어 있으면 **전사 로스터**로 떨어진다 — 거기엔 채용이 있다.
U-504에서 채용 봇이 백로그 75건 전부를 혼자 등재·수행한 경로가 이것이다. 08-04 계약(채용은 팀이
아니다)이 이 자리에는 닿지 않았다.

[실측 ②] U-521: 채용 공고 8건 · 유찰 8건 · 실제 채용 0건. genesis 생성이 실패했는데 그 실패가
도구 반환문 한 줄로만 갔다 — 판에는 '신규 채용으로 전환합니다'만 남고 아무도 오지 않았고, 로그에도
recruit_posted만 쌓였다. 채용 봇은 같은 공고를 20분간 반복했다. 막다른 길은 보여야 멈춘다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import comm_ceremonies as CC
from system.rule import milestone as M


def test_폴백_주인_후보에서_채용이_빠진다():
    src = inspect.getsource(M)
    i = src.find("_fb_pool = {int(x): _bots_o[int(x)]")
    assert i > 0, "폴백 풀 구성이 사라졌다"
    head = src[max(0, i - 700):i]
    assert '_nj_fb(v) != "채용"' in head, "채용이 백로그 주인 후보에 남아 있다"


def test_후보가_비면_무주로_반려한다():
    """채용을 빼서 후보가 하나도 없으면 0(무주)을 돌려준다 — 시스템 봇에게 밀지 않는다."""
    src = inspect.getsource(M)
    i = src.find("def _owner_fb(")
    assert "if not _fb_pool:" in src[i:i + 200] and "return 0" in src[i:i + 260]


def test_genesis_실패가_로그와_피드에_남는다():
    src = inspect.getsource(CC)
    i = src.find("if not _new:")
    assert i > 0
    block = src[i:i + 1200]
    assert 'flow.log("recruit_genesis_failed"' in block, "실패가 로그에 안 남는다"
    assert "[채용 실패]" in block, "실패가 판에 안 보인다"


def test_같은_공고_반복을_막는_지시가_있다():
    src = inspect.getsource(CC)
    i = src.find("if not _new:")
    block = src[i:i + 1200]
    assert "같은 공고를" in block and "다시 내지 마세요" in block, "반복 공고를 막지 않는다"


def test_정족수를_채우면_공고_자리에서_거절한다():
    """[실측 U-528] 세 도메인(게임개발·게임기획·게임검증)을 채운 직후 또 공고가 나갔다:

        [채용 공고] 무엇을 만들지에 대한 첫 회의를 소집하고 게임 방향을 정리할 담당자가 필요합니다
        [채용] 유찰 — 이 판에 이미 담당(게임기획)이 있어 새로 뽑지 않습니다

    08-06에 넣은 '채용 그만' 안내는 meet 거절문에 붙어 있어서, 회의를 시도하지 않고 바로 공고를
    내면 그 말을 못 본다. 안내가 닿는 자리가 잘못됐다 — 공고를 내려는 그 자리에서 말한다."""
    src = inspect.getsource(CC)
    assert "recruit_declined_quorum_met" in src, "공고 자리에서 거절하지 않는다"
    i = src.find("recruit_declined_quorum_met")
    block = src[max(0, i - 900):i + 700]
    assert "회의는 **시스템이 자동으로 엽니다**" in block
    assert "_norm_job(str(flow._info(me_id) or \"\")) == \"채용\"" in block, "실무자 증원까지 막는다"


def test_실무자의_증원은_막지_않는다():
    """이 거절은 채용 역할에만 건다 — 팀이 정말 사람이 더 필요하다고 판단하면 그건 팀의 결정이다."""
    src = inspect.getsource(CC)
    i = src.find("recruit_declined_quorum_met")
    block = src[max(0, i - 900):i + 200]
    assert '== "채용"' in block


def test_이미_팀인_사람을_다시_뽑지_않는다():
    """[실측 U-528] 후보 풀은 이 판의 팀을 빼는데(cands), 선발 경로는 팀 여부를 다시 보지 않았다.
    이미 합류한 동료가 자기 턴에 '[지원]'을 적으면 '[채용 확정]'이 한 번 더 나갔다:

        13504 송도경/게임개발 | [지원] 기존 DRAFT의 30초 결정론적 장애물 회피 루프를 기준으로…
        13505 온승우/게임기획 | [채용 확정] 게임개발 — 지원서 선발(게임개발)
        13507 장지안/게임검증 | [지원] 게임검증으로 참여하겠습니다…
        13508 온승우/게임기획 | [채용 확정] 게임검증 — 지원서 선발(게임검증)

    사람은 안 늘고 공고·지원·확정 6줄과 그만큼의 턴만 늘었다."""
    src = inspect.getsource(CC)
    assert "recruit_declined_already_member" in src, "이미 팀인 사람도 다시 확정한다"
    i = src.find("recruit_declined_already_member")
    block = src[max(0, i - 700):i + 600]
    assert "바로 일을 맡길 사람" in block, "다음 행동(위임)을 지시하지 않는다"


def test_다른_도메인_겸직은_막지_않는다():
    """같은 일을 다시 맡기는 것만 막는다 — 정말 다른 도메인이면 겸직 게이트가 따로 판단한다."""
    src = inspect.getsource(CC)
    i = src.find("recruit_declined_already_member")
    block = src[max(0, i - 700):i + 200]
    assert "_same_job(j, role_for)" in block
