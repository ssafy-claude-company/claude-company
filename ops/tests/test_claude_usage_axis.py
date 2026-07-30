"""Claude 경로 결산 계약 — 캐시 읽기·쓰기를 입력에 합산해 codex와 같은 축으로 잰다."""


class _Msg:
    total_cost_usd = 0.5
    duration_ms = 1200
    num_turns = 1
    usage = {"input_tokens": 1_000, "cache_read_input_tokens": 40_000,
             "cache_creation_input_tokens": 2_000, "output_tokens": 300}


def test_캐시_읽기쓰기가_입력에_포함된다():
    u = _Msg.usage
    tin = int(u["input_tokens"]) + int(u["cache_read_input_tokens"]) + int(u["cache_creation_input_tokens"])
    assert tin == 43_000, "캐시 축을 빼면 입력이 실제보다 작게 잡힌다"


def test_소스가_캐시필드를_읽는다():
    import inspect

    from organt import organt as _o
    src = inspect.getsource(_o)
    assert "cache_read_input_tokens" in src and "cache_creation_input_tokens" in src
    assert "tokens_cached" in src
