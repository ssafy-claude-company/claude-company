"""의장 자리는 없다 (2026-08-06, 사용자: '의장이라는 개념 자체가 없어졌을텐데 더 깊게 봐봐').

[규명] 2026-07-14에 어휘만 중립화했다 — '소집자 의견'을 '여는 의견'으로 바꾸며 "회의 연 사람도 한
참여자일 뿐"이라고 적었다. 그런데 **자리**는 그대로였다:

  ① 회의를 연 사람의 의견이 개시 메시지 본문에 붙어 회의 맨 앞에 선다(모두가 그 아래 붙는다).
  ② 그 사람은 확정 표결에서 통째로 빠진다(`_voters = [v for v in _voters if v != me_id]`).

말은 제일 먼저 하고 표는 안 던지는 자리 — 그게 의장이다. 08-06에 백지 단계 독립 라운드를 복원한
이유가 '먼저 쓴 한 줄이 곧 결론이 된다'였는데, 정작 그 한 줄의 특권 자리는 손대지 않았다.
실측 U-520 goal 회의: 개시 메시지의 여는 의견이 '60초짜리 2D 아케이드'였고, 그 회의를 연 것은
**채용 봇**이었다(채용은 팀도 아니다 — [test_recruiter_not_anchor] 참조).

[수리] 백지 단계(goal·criteria)에서 개시 메시지는 주제만 싣는다. 연 사람의 의견은 이미 받아 둔
것을 남들 뒤에 같은 자격의 [독립 의견] 한 건으로 세우고, 말했으므로 유권자가 된다. 딛을 결론이
있는 뒤 단계는 종전대로 — 거기서는 회의를 여는 일이 문맥 제시이지 안을 먼저 쓰는 일이 아니다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import communication as C


def _src():
    return inspect.getsource(C)


def test_백지_단계_개시는_주제만_싣는다():
    src = _src()
    assert "_blank_stage = bool(_stage in _R1_STAGES)" in src, "백지 단계 판정이 사라졌다"
    i = src.find("_preface = topic if _blank_stage else (")
    assert i > 0, "백지 단계에서도 여는 의견이 개시 메시지에 붙는다"


def test_뒤_단계는_여는_의견을_유지한다():
    """딛을 결론이 있는 단계까지 없애지 않는다 — 거기서 여는 의견은 문맥 제시다."""
    src = _src()
    i = src.find("_preface = topic if _blank_stage else (")
    assert "[여는 의견] {my_view}" in src[i:i + 300], "뒤 단계의 여는 의견까지 사라졌다"


def test_연_사람의_의견도_독립_의견_한_건이다():
    src = _src()
    i = src.find("if str(my_view or \"\").strip():")
    assert i > 0, "연 사람의 의견이 독립 라운드에 등재되지 않는다"
    block = src[i:i + 600]
    assert '"[독립 의견][단계:{_stage}]", my_view' in block, "같은 자격으로 서지 않는다"
    assert "_indep_spoke.add(int(me_id))" in block, "말했는데 발언자로 세지 않는다"


def test_말했으면_표를_던진다():
    src = _src()
    assert "_voters = [v for v in _voters if v != me_id or int(me_id) in _indep_spoke]" in src, \
        "연 사람이 독립 의견을 내고도 표결에서 빠진다"


def test_다시_깨우지_않는다():
    """의견은 SYS가 회의 전에 이미 받아 뒀다(my_view) — 같은 말을 위해 턴을 한 번 더 태우지 않는다."""
    src = _src()
    i = src.find("if str(my_view or \"\").strip():")
    block = src[i:i + 600]
    assert "flow.wake" not in block and "_fork_collect" not in block, "연 사람을 다시 깨운다"
