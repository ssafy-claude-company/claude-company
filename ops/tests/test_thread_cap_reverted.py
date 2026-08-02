"""[스레드 누계 상한은 폐기(2026-08-01, 같은 날 실측으로 반증)] 같은 길이 구간에서 첫 턴과 이어가는
턴을 나란히 재니 이득이 뒤집혔다 — 30~120초 턴은 새 스레드가 7.0배 쌌지만 15분 넘는 작업 턴은
0.8배로 역전했다. 누계 상한은 바로 그 긴 작업 턴까지 끊으므로 다시 들어오면 안 된다."""
import inspect

from organt import organt as og


def test_누계만으로_스레드를_끊지_않는다():
    src = inspect.getsource(og.Organt._resume_sid)
    assert "ORGANT_THREAD_INPUT_CAP" not in src
    assert "_thread_cum_input" not in src


def test_일감_경계_리셋은_남아_있다():
    src = inspect.getsource(og.Organt._resume_sid)
    assert "_scope_changed" in src
