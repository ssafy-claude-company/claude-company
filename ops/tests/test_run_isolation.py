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


# ── [비특권 러너도 격리한다(2026-07-30, 현준-4 보안 감사)] ────────────────────────
# 종전엔 geteuid()!=0이면 격리를 통째로 건너뛰고 /bin/sh -c로 직행했다. 여러 봇이 한 계정을
# 공유하는 배치에서 그 계정이 읽는 모든 것에 `cd ..` 한 번으로 닿는다 — 러너를 비특권으로
# 내리려면 이 구멍을 먼저 막아야 하고, 안 막으면 강등이 격리를 오히려 없앤다.
def _argv_as(euid, monkeypatch, tmp_path):
    import os as _os
    from system import guide_tools as gt
    monkeypatch.setattr(_os, "geteuid", lambda: euid)
    ws = tmp_path / "ws"
    ws.mkdir()
    return gt._prepare_run_exec(str(ws), "echo hi")


def test_비특권도_bwrap으로_격리된다(monkeypatch, tmp_path):
    argv, _env, err = _argv_as(1000, monkeypatch, tmp_path)
    assert err == ""
    assert argv and argv[0].endswith("bwrap")          # /bin/sh 직행이 아니다
    assert "--unshare-user" in argv                     # 비특권은 userns를 열어야 bind가 된다
    assert "--tmpfs" in argv and "/root" in argv        # host /root는 가린다


def test_비특권은_권한강등_도구를_쓰지_않는다(monkeypatch, tmp_path):
    """userns 안에서 host nobody uid는 매핑되지 않아 setpriv가 실패한다(실측).
    이미 비특권이라 강등할 특권도 없다."""
    argv, _env, _err = _argv_as(1000, monkeypatch, tmp_path)
    i = argv.index("--")
    assert argv[i + 1] == "/bin/sh"          # 강등 도구를 거치지 않고 바로 셸


def test_root는_종전대로_nobody로_강등한다(monkeypatch, tmp_path):
    """라이브 경로 무회귀 — root는 bwrap 안에서 nobody로 내려간다."""
    argv, _env, err = _argv_as(0, monkeypatch, tmp_path)
    assert err == ""
    i = argv.index("--")
    assert argv[i - 1] == "--bounding-set=-all"   # setpriv 인자 뒤에 실행부가 온다
    assert any(a.endswith("setpriv") for a in argv)
    assert "--unshare-user" not in argv                 # root는 userns가 필요 없다


def test_bwrap이_없으면_비특권은_종전대로_통과한다(monkeypatch, tmp_path):
    """개발 머신(bwrap 미설치)에서 run 자체를 못 쓰게 만들지 않는다."""
    import os as _os
    from system import guide_tools as gt
    monkeypatch.setattr(_os, "geteuid", lambda: 1000)
    monkeypatch.setattr(gt.shutil, "which", lambda n: None)
    ws = tmp_path / "ws"
    ws.mkdir()
    argv, _env, err = gt._prepare_run_exec(str(ws), "echo hi")
    assert err == "" and argv[0] == "/bin/sh"


# ── [스크러빙 규칙 구멍 봉합(2026-07-30, 현준-4 실측)] ─────────────────────────
# 종전 목록은 `_API_KEY`처럼 좁아서 ORGANT_VAULT_KEY·DATABASE_URL·OPENAI_KEY가 전부 통과했다.
# 그 값들이 러너 env에 없어 무해했을 뿐이라 — 무해한 이유가 목록이 아니라 우연이면 안 된다.
def test_금고키와_DB자격증명이_지워진다():
    from system.guide_tools import _is_secret_env
    for name in ("ORGANT_VAULT_KEY", "DATABASE_URL", "OPENAI_KEY", "ANTHROPIC_API_KEY",
                 "REDIS_URI", "SENTRY_DSN", "AWS_CREDENTIALS", "GH_PAT"):
        assert _is_secret_env(name), f"{name} 이 비밀로 안 잡힌다"


def test_빌드에_필요한_env는_남는다():
    """전부 막으면 npm·빌드가 죽는다 — PATH·HOME 같은 일반 env는 보존한다."""
    from system.guide_tools import _is_secret_env, _scrubbed_run_env
    for name in ("PATH", "HOME", "PORT", "LANG", "npm_config_cache", "KEYBOARD"):
        assert not _is_secret_env(name), f"{name} 이 비밀로 잘못 잡힌다"
    env = _scrubbed_run_env()
    assert "PATH" in env and "HOME" in env


def test_스크러빙된_env에_시크릿성_이름이_없다():
    from system.guide_tools import _scrubbed_run_env
    env = _scrubbed_run_env()
    leaked = [k for k in env
              if any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD", "DATABASE_URL"))]
    assert leaked == [], f"봇 셸 env에 남은 시크릿성 이름: {leaked}"


# ── [판별 격리 도우미 경유(2026-07-30, 설계문서 7절 (나))] ─────────────────────
# 비특권 러너는 봇 셸을 판별 uid로 내릴 수 없다(실측: bwrap 비setuid → userns uid 1개).
# 특권이 필요한 그 한 조각만 도우미가 맡는다. 기본 off — 설치 전에는 지금 동작 그대로.
def _prep(euid, monkeypatch, tmp_path, **env):
    import os as _os
    from system import guide_tools as gt
    monkeypatch.setattr(_os, "geteuid", lambda: euid)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    ws = tmp_path / "ws"
    ws.mkdir()
    return gt._prepare_run_exec(str(ws), "echo hi"), str(ws)


def test_플래그가_꺼져_있으면_도우미를_안_거친다(monkeypatch, tmp_path):
    (argv, _e, err), _ws = _prep(1000, monkeypatch, tmp_path)
    assert err == ""
    assert "organt-sandbox" not in " ".join(argv)
    assert argv[0].endswith("bwrap")          # 종전 동작 그대로


def test_플래그가_켜지면_도우미_경유_argv가_나온다(monkeypatch, tmp_path):
    helper = tmp_path / "organt-sandbox"
    helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (argv, _e, err), ws = _prep(1000, monkeypatch, tmp_path,
                                ORGANT_SANDBOX_HELPER="1",
                                ORGANT_SANDBOX_HELPER_PATH=str(helper))
    assert err == ""
    assert argv[0].endswith("sudo") and str(helper) in argv
    # uid는 넘기지 않는다 — 호출자가 고르면 판 A의 트리를 판 B uid로 넘길 수 있다
    assert "--uid" not in argv
    assert argv[argv.index("--workspace") + 1] == ws
    assert argv[argv.index("--command") + 1] == "echo hi"   # 셸을 거치지 않고 argv 원소로


def test_도우미가_없으면_통과시키지_않는다(monkeypatch, tmp_path):
    """fail-closed — 격리를 켰다고 해 놓고 조용히 맨몸으로 돌면 안 된다."""
    (argv, env, err), _ws = _prep(1000, monkeypatch, tmp_path,
                                  ORGANT_SANDBOX_HELPER="1",
                                  ORGANT_SANDBOX_HELPER_PATH=str(tmp_path / "없는파일"))
    assert argv is None and env is None and "도우미를 찾을 수 없습니다" in err


def test_root는_플래그와_무관하게_종전_경로다(monkeypatch, tmp_path):
    """root는 이미 setpriv로 내려갈 수 있다 — 도우미를 거칠 이유가 없다."""
    (argv, _e, err), _ws = _prep(0, monkeypatch, tmp_path, ORGANT_SANDBOX_HELPER="1")
    assert err == "" and "organt-sandbox" not in " ".join(argv)
    assert any(a.endswith("setpriv") for a in argv)
