"""형식 관문 마감 확인 — 각 단계가 형식 미달을 반려하는가(등록은 마지막 방어선)."""
import types

import pytest

from system.rule.milestone import open_milestone, open_subtask, register_stage


class _F:
    log = None
    backlog_relays = {}
    milestones = []


def _flow(tmp_path, goal="2인 턴제 카드 대전", acceptance="- 동작 | 실증: pytest -q"):
    f = _F()
    f.current = types.SimpleNamespace(task_id="T1", team=[11, 12],
                                      status=types.SimpleNamespace(goal=goal),
                                      acceptance=acceptance)
    f.workspace = str(tmp_path)
    f.milestones = []
    return f


def test_목표회의_빈칸_남으면_반려(tmp_path):
    ok, note = register_stage(_flow(tmp_path, goal=""), "goal",
                              "[수렴안]\n목표: ⟦여기에 무엇을 만드는지⟧\n[/수렴안]")
    assert not ok and "빈칸" in note


def test_목표회의_목표줄_없으면_반려(tmp_path):
    ok, note = register_stage(_flow(tmp_path, goal=""), "goal", "[수렴안]\n그냥 산문\n[/수렴안]")
    assert not ok and "목표" in note


def test_목표회의_절차나열은_반려(tmp_path):
    ok, note = register_stage(_flow(tmp_path, goal=""), "goal",
                              "[수렴안]\n목표: ①컨셉 정의 → ②검증 → ③배포\n내용 폭: 기능 3종\n창의 설계: 방패병 — 앞 열이 받는 피해 40% 감소\n최대 표준: 실제 예 대조 · 핵심 기능 3종 · 주 사용 흐름 원탭\n[/수렴안]")
    assert not ok, "목표는 '무엇을 만드는가'지 절차가 아니다"


def test_목표회의_미룸문구는_반려(tmp_path):
    ok, note = register_stage(_flow(tmp_path, goal=""), "goal",
                              "[수렴안]\n목표: (후속: 기획 단계에서 확정)\n내용 폭: 기능 3종\n창의 설계: 방패병 — 앞 열이 받는 피해 40% 감소\n최대 표준: 실제 예 대조 · 핵심 기능 3종 · 주 사용 흐름 원탭\n[/수렴안]")
    assert not ok and "미룸" in note


def test_완수조건회의_조건_없으면_반려(tmp_path):
    ok, note = register_stage(_flow(tmp_path, acceptance=""), "criteria",
                              "[수렴안]\n그냥 잘 만들면 된다\n[/수렴안]")
    assert not ok and "완수조건" in note


def test_완수조건회의_실증_없으면_반려(tmp_path):
    ok, note = register_stage(_flow(tmp_path, acceptance=""), "criteria",
                              "[수렴안]\n조건: 재미있다 | 실증: 느낌으로 판단\n[/수렴안]")
    assert not ok, "실증은 실행 가능한 명령이어야 한다"


def test_완수조건회의_형식_맞으면_통과(tmp_path):
    f = _flow(tmp_path, acceptance="")
    ok, note = register_stage(f, "criteria",
                              "[수렴안]\n조건: 한 판이 끝난다 | 실증: node test/play.js\n[/수렴안]")
    assert ok and "node test/play.js" in f.current.acceptance


def test_주기회의_이번주기_없으면_반려(tmp_path):
    ok, note = register_stage(_flow(tmp_path), "milestone", "[수렴안]\n조건: x | 실증: y\n[/수렴안]")
    assert not ok


def test_단위회의_단위줄_없으면_반려(tmp_path):
    f = _flow(tmp_path)
    open_milestone(f, "최소버전", [{"desc": "동작", "verify": "pytest"}])
    ok, note = register_stage(f, "subtask", "[수렴안]\n그냥 나누자\n[/수렴안]")
    assert not ok


def test_일감회의_백로그줄_없으면_반려(tmp_path):
    f = _flow(tmp_path)
    ms = open_milestone(f, "최소버전", [{"desc": "동작", "verify": "pytest"}])
    open_subtask(f, ms, "게임 규칙", [])
    ok, note = register_stage(f, "backlog", "[수렴안]\n열심히 하자\n[/수렴안]")
    assert not ok


def test_일감회의_참조표기는_백로그가_아니다(tmp_path):
    """'B4'·'#2' 같은 의존 표기가 일감으로 태어나면 즉시 완료로 churn한다 — 제출 관문이 막는다."""
    from system.rule.backlog import BacklogError, relay_for
    f = _flow(tmp_path)
    ms = open_milestone(f, "최소버전", [{"desc": "동작", "verify": "pytest"}])
    st = open_subtask(f, ms, "게임 규칙", [])
    r = relay_for(f, st)
    with pytest.raises(BacklogError):
        r.submit(12, "B4", force=True)


def test_일감_빈본문은_반려(tmp_path):
    from system.rule.backlog import BacklogError, relay_for
    f = _flow(tmp_path)
    ms = open_milestone(f, "최소버전", [{"desc": "동작", "verify": "pytest"}])
    st = open_subtask(f, ms, "게임 규칙", [])
    r = relay_for(f, st)
    with pytest.raises(BacklogError):
        r.submit(12, "   ", force=True)
