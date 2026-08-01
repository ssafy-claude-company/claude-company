"""실행 종류 판정의 계약(2026-08-01, 현준-4).

종전엔 모델 문자열만 봤다. 그러면 내가 등록한 LLM 이름이 'llama-3.3-70b'일 때 gpt-
접두사가 아니라서 Claude 경로로 가고, 애써 등록한 주소는 영영 안 쓰인다 - 이름이
실행 방식을 정하는 구조라 이름을 바꾸면 경로가 바뀐다.
"""
from organt.runtime import CLAUDE, CODEX, kind_of, runtime_kind


def test_명시가_없으면_옛_규칙을_그대로_쓴다():
    """설정을 안 붙인 봇의 실행은 한 글자도 바뀌면 안 된다."""
    assert kind_of("gpt-5.5") == CODEX
    assert kind_of("opus") == CLAUDE
    assert kind_of("") == CLAUDE
    assert kind_of(None) == CLAUDE


def test_명시가_이름을_이긴다():
    """내 LLM 이름이 무엇이든 등록한 종류대로 돈다."""
    assert kind_of("llama-3.3-70b", "openai_compat") == CODEX
    assert kind_of("우리집-3090", "relay") == CODEX
    assert kind_of("gpt-5.5", "claude") == CLAUDE


def test_우리_것_말고는_전부_codex_프로토콜이다():
    """openai_compat도 relay도 OpenAI 호환 얼굴을 쓴다 - 프로토콜이 같다."""
    for d in ("openai_compat", "relay", "무엇이든"):
        assert kind_of("아무이름", d) == CODEX


def test_옛_함수는_그대로다():
    """호출부가 아직 쓰는 곳이 있다 - 뜻을 바꾸면 그쪽이 조용히 달라진다."""
    assert runtime_kind("gpt-4") == CODEX
    assert runtime_kind("llama") == CLAUDE
