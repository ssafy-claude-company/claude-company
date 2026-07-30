"""실행 격리 계약 — 매 run에 빈 포트·고유 산출물 폴더가 주어진다(동시 실행 안전)."""
import re

from system.guide_tools import _free_port, _scrubbed_run_env


def test_실행마다_빈_포트가_주어진다():
    a, b = _free_port(), _free_port()
    assert 1024 < a < 65536 and 1024 < b < 65536


def test_환경에_PORT_ARTIFACT_DIR_RUN_ID가_실린다():
    env = _scrubbed_run_env()
    assert env["PORT"].isdigit()
    assert re.fullmatch(r"artifacts/run-\d{9}", env["ARTIFACT_DIR"])
    assert env["RUN_ID"].isdigit()


def test_두_실행의_포트가_서로_다르다():
    """같은 시각에 도는 두 검증이 같은 포트를 잡으면 하나가 죽는다 — 그래서 매번 새로 고른다."""
    ports = {_scrubbed_run_env()["PORT"] for _ in range(5)}
    assert len(ports) > 1


def test_비밀은_여전히_지워진다():
    import os
    os.environ["ORGANT_GUIDE_TOKEN"] = "secret-should-not-leak"
    try:
        assert "ORGANT_GUIDE_TOKEN" not in _scrubbed_run_env()
    finally:
        os.environ.pop("ORGANT_GUIDE_TOKEN", None)
