"""제3자 엔진으로 나가는 실행 환경의 계약(2026-07-31, 현준-4).

주소가 있으면 그쪽으로 나가고, 없거나 못 믿을 주소면 우리 엔진으로 돈다. 조용히 남의
주소로 보내는 것보다 우리 엔진으로 도는 편이 안전하다 - 그 판단을 여기서 못박는다.
"""
from organt.codex_mcp_bridge import _codex_env, _engine_env


def test_우리_엔진이면_아무것도_안_싣는다():
    """주소가 없으면 우리 엔진이다 - 실을 값이 없다."""
    assert _engine_env(None, None) == {}
    assert _engine_env("", "키") == {}


def test_공인_주소는_실린다():
    env = _engine_env("https://api.openai.com/v1", "sk-어떤키")
    assert env["OPENAI_BASE_URL"] == "https://api.openai.com/v1"
    assert env["OPENAI_API_KEY"] == "sk-어떤키"


def test_내부_주소는_호출_직전에_걸러진다():
    """웹이 내줄 때 이미 봤지만 그 사이 이름이 내부로 바뀌었을 수 있다.

    두 곳에서 같은 판정을 하는 것이 DNS rebinding 방어의 전부다.
    """
    for bad in ("https://127.0.0.1/v1", "https://192.168.0.5/v1",
                "https://169.254.169.254/v1"):
        assert _engine_env(bad, "키") == {}, bad


def test_평문_주소도_걸러진다():
    assert _engine_env("http://api.openai.com/v1", "키") == {}


def test_주소가_걸리면_키도_안_나간다():
    """주소를 못 믿으면 그 주소로 갈 키도 실으면 안 된다."""
    env = _engine_env("https://10.0.0.1/v1", "sk-비밀")
    assert "OPENAI_API_KEY" not in env


def test_실행_환경은_부모를_물려받지_않는다():
    """러너의 키가 봇 프로세스로 따라 들어가면 격리가 무너진다."""
    env = _codex_env(None)
    assert set(env) == {"PATH", "HOME"}


def test_빈_값은_환경에_들어가지_않는다():
    """빈 OPENAI_API_KEY가 실리면 codex가 '키 있음'으로 착각한다."""
    env = _codex_env({"OPENAI_API_KEY": "", "OPENAI_BASE_URL": None})
    assert "OPENAI_API_KEY" not in env and "OPENAI_BASE_URL" not in env
