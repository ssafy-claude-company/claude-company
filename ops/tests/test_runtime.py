"""실행 방법 판별과 제3자 엔드포인트 검증(2026-07-30, 현준-4).

엔드포인트 검증은 제3자 LLM 등록을 열기 전에 반드시 서 있어야 하는 문이다. 그 주소로
접속하는 것은 봇이 아니라 **러너**이고, 러너는 오늘 만든 egress 관문의 대상이 아니다
(러너 자신이 모델 API로 나가야 하므로). 그래서 내부로 돌리는 주소를 여기서 막는다.
"""
import pytest

from organt.runtime import (CLAUDE, CODEX, EndpointError, is_codex,
                            runtime_kind, validate_endpoint)


# ── 실행 방법 판별 ────────────────────────────────────────────────────────
def test_gpt는_codex_경로다():
    assert runtime_kind("gpt-5.6") == CODEX
    assert is_codex("gpt-5.6") is True


def test_그_외에는_claude_경로다():
    for m in ("opus", "sonnet", "haiku", "claude-fable-5", "", None):
        assert runtime_kind(m) == CLAUDE
        assert is_codex(m) is False


def test_대소문자와_공백에_흔들리지_않는다():
    assert runtime_kind("  GPT-5.6  ") == CODEX


# ── 엔드포인트 검증: 거부해야 하는 것 ───────────────────────────────────
def test_평문은_거부한다():
    """프롬프트가 그대로 흐른다."""
    with pytest.raises(EndpointError):
        validate_endpoint("http://example.com/v1")


def test_루프백은_거부한다():
    """러너는 murmur 내부에 닿는다(실측 127.0.0.1:8000 → 200)."""
    for u in ("https://127.0.0.1/v1", "https://[::1]/v1"):
        with pytest.raises(EndpointError):
            validate_endpoint(u)


def test_사설대역은_거부한다():
    for u in ("https://10.0.0.5/v1", "https://192.168.0.5/v1", "https://172.16.0.1/v1"):
        with pytest.raises(EndpointError):
            validate_endpoint(u)


def test_링크로컬_메타데이터는_거부한다():
    """클라우드 메타데이터 주소 — SSRF의 단골 목적지."""
    with pytest.raises(EndpointError):
        validate_endpoint("https://169.254.169.254/latest/meta-data/")


def test_주소에_자격증명을_담지_못한다():
    """키는 금고에 둔다 — 주소에 박히면 로그·기록에 그대로 남는다."""
    with pytest.raises(EndpointError):
        validate_endpoint("https://user:pw@example.com/v1")


def test_호스트가_없으면_거부한다():
    with pytest.raises(EndpointError):
        validate_endpoint("https:///v1")


def test_이름이_내부로_풀리면_거부한다(monkeypatch):
    """DNS rebinding — 저장 때 공인, 호출 때 사설로 바꾸는 우회를 호출 시점 재검증이 막는다."""
    import socket as _s
    monkeypatch.setattr(_s, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(EndpointError):
        validate_endpoint("https://looks-public.example.com/v1")


# ── 통과해야 하는 것 ─────────────────────────────────────────────────────
def test_공인주소는_통과한다():
    host, addrs = validate_endpoint("https://8.8.8.8/v1")
    assert host == "8.8.8.8" and addrs == ["8.8.8.8"]


def test_공인으로_풀리는_이름은_통과한다(monkeypatch):
    import socket as _s
    monkeypatch.setattr(_s, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    host, addrs = validate_endpoint("https://example.com/v1")
    assert host == "example.com" and addrs == ["93.184.216.34"]


def test_해석을_끄면_이름만_확인한다():
    """저장 시점 1차 검사용 — 호출 시점에는 resolve=True로 다시 부른다."""
    host, addrs = validate_endpoint("https://example.com/v1", resolve=False)
    assert host == "example.com" and addrs == []
