"""실행할 수 없는 실증에 묶인 GOAL은 마지막 주기가 푼다(U-442: e2e가 영영 안 열림)."""
import types

from system.rule.milestone import _goal_verifier_unrunnable, meeting_stage


class _C:
    def __init__(self, verify):
        self.desc, self.verify, self.status, self.passed = "핵심 경로 완주", verify, "open", False
        self.release_lock = True


def _flow(tmp_path, verify):
    ms = types.SimpleNamespace(status="done", ms_id="MS-1", goal="주기", subtasks=[],
                               criteria=[], locked_criteria=[_C(verify)])
    return types.SimpleNamespace(
        workspace=str(tmp_path), milestones=[ms],
        current=types.SimpleNamespace(
            status=types.SimpleNamespace(goal="게임을 만든다"),
            acceptance="- 핵심 경로 완주 | 실증: " + verify, task_id="T-1"))


def test_없는_파일을_가리키면_실행_불가로_본다(tmp_path):
    f = _flow(tmp_path, "node scripts/verify-recruitment-game.mjs --check=core")
    assert _goal_verifier_unrunnable(f) is True


def test_있는_파일이면_정상이다(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify.py").write_text("print(1)", encoding="utf-8")
    f = _flow(tmp_path, "python3 scripts/verify.py")
    assert _goal_verifier_unrunnable(f) is False


def test_그런_경우_마지막_주기_회의를_연다(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow(tmp_path, "node scripts/verify-recruitment-game.mjs --check=core")
    assert meeting_stage(f) == "milestone"
    assert meeting_stage(f) is None          # 같은 사유로 두 번은 안 연다


def test_실행_가능하면_주기를_더_열지_않는다(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify.py").write_text("print(1)", encoding="utf-8")
    f = _flow(tmp_path, "python3 scripts/verify.py")
    assert meeting_stage(f) is None
