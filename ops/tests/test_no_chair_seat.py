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


def test_불참_사유는_발언이_아니다():
    """[U-525 실측] fork에서 제외된 사람(_res=None)의 시스템 사유가 '(이 흐름에서 진행 중인 위임
    보유 — 이번 수집에서 제외)'라는 독립 의견 한 건으로 피드에 섰고, 말한 적 없는 사람이 유권자로
    잡혔다. 기록에는 남기되 발언·표결에서는 뺀다."""
    src = _src()
    i = src.find("_txt = _res\n")
    assert i > 0, "제외 사유가 여전히 발언 본문으로 쓰인다(_res or _note)"
    block = src[i:i + 500]
    assert "[독립 — 불참]" in block, "불참이 회의록에도 안 남는다"
    assert "continue" in block, "불참자가 발언·표결로 흘러간다"


def test_백지_단계_개시는_SYS_명의다():
    """[2026-08-07 실측 U-528 12:28] 화면에 이렇게 섰다:

        송도경#2418 · 게임개발 · 오후 12:28
        Task 목표 정의

    개시 행은 안건인데 사람 명의로 나간다. 백지 단계에서 여는 의견을 뗀 뒤로는 본문이 안건 한 줄뿐
    이라, 그 사람이 안건을 읽은 것처럼 보인다. 회의를 여는 것은 시스템이므로 SYS 명의로 남긴다 —
    그 사람의 의견은 아래 독립 라운드에서 자기 이름으로 따로 선다."""
    src = _src()
    i = src.find("_open_label = ")
    assert i > 0, "개시 라벨 조립이 사라졌다"
    block = src[i:i + 600]
    assert "if _blank_stage:" in block, "백지 단계 구분이 없다"
    assert "flow.guide.post(int(flow.current.thread_id), 0," in block, "SYS 명의로 안 나간다"


def test_뒤_단계_개시는_종전대로_사람_명의다():
    """딛을 결론이 있는 단계의 개시 행에는 여는 의견이 함께 실린다 — 그건 그 사람의 말이다."""
    src = _src()
    i = src.find("_open_label = ")
    block = src[i:i + 1100]
    assert "await _say_speech(flow, me_id, _open_label, _preface, meta=_meta_open)" in block


def test_게시_실패에도_회의가_끊기지_않는다():
    src = _src()
    i = src.find("_open_label = ")
    block = src[i:i + 1100]
    assert "except Exception:" in block, "SYS 게시 실패가 회의를 죽인다"


def test_제목과_결론이_데이터로_실린다():
    """[2026-08-07, 사용자: '첫 글을 제목으로 마지막 글을 결론으로가 아니라 정확히 데이터 적으로
    관리해서 결론 따로 받고 제목 따로 받아야지'] 화면이 본문에서 안건·결론을 되짚지 않게, 게시
    지점에서 구조 필드로 함께 보낸다."""
    src = _src()
    assert '"meet": {"role": "open"' in src, "제목이 데이터로 안 실린다"
    assert '"role": "close"' in src and '"resolved"' in src, "결론이 데이터로 안 실린다"
    assert '"conclusion": str(_conclusion or "")[:400]' in src, "결론 원문이 안 실린다"
