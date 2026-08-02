"""[증류한 지식이 새 스레드에 안 실렸다(2026-08-02)] will_resume이 True면 SYS는 개인 기준(수면 증류
산출)을 주입하지 않는다 — '대화 기억이 담보한다'는 전제다. 일감 경계 리셋이 붙은 뒤로는 세션 파일이
있어도 새 스레드로 시작할 수 있어, 그 봇은 기준도 기억도 없이 일했다. 두 판정이 같은 사실을 봐야 한다."""
from organt import organt as og


class _O:
    session_id = "S-1"
    _work_scope = ""
    _work_scope_seen = ""

    def _session_in_store(self):
        return True

    _scope_changed = og.Organt._scope_changed


def _mk(scope, seen):
    o = _O()
    o._work_scope, o._work_scope_seen = scope, seen
    return o


def test_일감이_바뀌면_새_스레드이자_첫_wake다(monkeypatch):
    monkeypatch.delenv("ORGANT_SCOPE_FRESH", raising=False)
    o = _mk("ST-1::B2", "ST-1::B1")
    assert og.Organt.will_resume(o) is False          # → SYS가 개인 기준을 다시 실어 준다
    assert og.Organt._resume_sid(o, micro=False) is None


def test_같은_일감이면_이어붙고_재주입하지_않는다(monkeypatch):
    monkeypatch.delenv("ORGANT_SCOPE_FRESH", raising=False)
    o = _mk("ST-1::B1", "ST-1::B1")
    assert og.Organt.will_resume(o) is True
    assert og.Organt._resume_sid(o, micro=False) == "S-1"


def test_두_판정은_언제나_같은_사실을_본다(monkeypatch):
    monkeypatch.delenv("ORGANT_SCOPE_FRESH", raising=False)
    for scope, seen in (("A", "B"), ("A", "A"), ("", ""), ("A", "")):
        o = _mk(scope, seen)
        fresh = og.Organt._resume_sid(_mk(scope, seen), micro=False) is None
        assert og.Organt.will_resume(o) is (not fresh)


def test_스위치를_끄면_종전대로_이어붙는다(monkeypatch):
    monkeypatch.setenv("ORGANT_SCOPE_FRESH", "0")
    o = _mk("ST-1::B2", "ST-1::B1")
    assert og.Organt.will_resume(o) is True
