"""회의 하나에 결론 하나 — 목표 회의와 완수조건 회의가 갈린다."""
import types

from system.rule.milestone import _STAGE_FRAME, meeting_stage, stage_agenda


class _F:
    milestones = []
    log = None
    backlog_relays = {}


def _flow(goal="", acceptance=""):
    f = _F()
    f.current = types.SimpleNamespace(task_id="T1", team=[11, 12],
                                      status=types.SimpleNamespace(goal=goal),
                                      acceptance=acceptance)
    return f


def test_목표가_없으면_목표회의():
    assert meeting_stage(_flow()) == "goal"


def test_목표만_있으면_완수조건회의():
    """[사용자 지시(2026-07-30)] 종전엔 한 회의가 '무엇을 만들지'와 '무엇이 되면 끝인지'를 함께
    정했다 — 두 결정이 한 표결에 묶이면 먼저 쓴 사람의 골격이 통째로 통과한다."""
    assert meeting_stage(_flow(goal="2인 턴제 카드 대전")) == "criteria"


def test_둘_다_있으면_주기회의로():
    assert meeting_stage(_flow(goal="G", acceptance="- 동작 | 실증: pytest")) == "milestone"


def test_목표회의_틀에는_완수조건_요구가_없다():
    _t, tmpl = stage_agenda("goal")[0], stage_agenda("goal")[1]
    assert "목표:" in tmpl and "완수조건 |" not in tmpl


def test_완수조건회의_프레임은_하나만_묻는다():
    fr = _STAGE_FRAME["criteria"]
    assert "무엇이 되면" in fr and "만들 것은 이미" in fr


def test_완수조건_회의가_장부에_등록한다(tmp_path):
    """이 회의의 결론 하나 = Task 완수조건. 등록되면 다음 회의(주기)로 넘어간다."""
    import types

    from system.rule.milestone import register_stage

    f = _flow(goal="2인 턴제 카드 대전")
    f.workspace = str(tmp_path)
    ok, note = register_stage(f, "criteria",
                              "[수렴안]\n조건: 두 명이 한 판을 끝낸다 | 실증: node test/play.js\n[존재이유] 솔로로는 클리어 불가 | 실증: node test/play.js --solo\n[/수렴안]")
    assert ok, note
    assert "실증: node test/play.js" in f.current.acceptance
    assert meeting_stage(f) == "milestone", "완수조건이 서면 다음은 주기 회의"


def test_목표회의는_완수조건_없이도_등록된다(tmp_path):
    """종전엔 목표 회의가 완수조건까지 요구했다 — 두 결정이 한 표결에 묶이던 자리."""
    from system.rule.milestone import register_stage

    f = _flow()
    f.workspace = str(tmp_path)
    ok, note = register_stage(f, "goal", "[수렴안]\n목표: 2인 턴제 카드 대전\n내용 폭: 기능 3종 · 깊이 축 — 강화 선택 3택1이 5웨이브에 걸쳐 누적\n창의 설계: 방패병 — 앞 열이 받는 피해 40% 감소\n최대 표준: 실제 예 대조 · 핵심 기능 3종 · 주 사용 흐름 원탭\n[/수렴안]")
    assert ok, note
    assert f.current.status.goal.startswith("2인 턴제")
