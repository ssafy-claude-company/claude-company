"""[GPT 봇 계량·강도 회귀(2026-07-28)] — 사용자 지적 두 건의 재발 방지 계약.

① 토큰이 기록되지 않아 크레딧이 안 깎였다: codex 경로는 비용을 안 주고 토큰만 준다. 환산이 없으면
   턴 결산에 cost_usd가 없고, 러너는 `cost_usd`가 있을 때만 report_usage를 보내므로 원장이 0원으로
   남는다(= 한도 무력). 여기서 '토큰 → 결산 레코드'까지를 고정한다.
② 구세대 모델이 매 턴 400으로 죽었다: 강도 미지정 턴은 머신 전역 ~/.codex/config.toml(ultra)을
   물려받는데 gpt-5.4 계열은 그 값을 거부한다. 모델별 상한 클램프를 고정한다.
"""
import asyncio
from pathlib import Path

from organt.codex_mcp_bridge import _clamp_effort, _usage_from_event
from organt.gpt_pricing import estimate_cost_usd, rates_for, usage_record
from organt.organt import Organt
from system.config import Config


def _cfg(model=None) -> Config:
    return Config(
        system_bot_token="s", channel_id=1,
        model=model, workspace_dir=Path("/tmp/ws"),
        audit_log_path=Path("/tmp/audit.jsonl"),
    )


# ── ① 계량 ────────────────────────────────────────────────────────────────
def test_토큰이_비용으로_환산된다():
    """캐시된 입력은 입력의 부분집합이라 캐시 단가로 따로 센다(이중 청구 금지)."""
    usage = {"input_tokens": 1_000_000, "cached_input_tokens": 200_000, "output_tokens": 100_000}
    # luna: 입력 $1.00 / 캐시 $0.10 / 출력 $6.00 per 1M
    expected = 0.8 * 1.00 + 0.2 * 0.10 + 0.1 * 6.00
    assert abs(estimate_cost_usd("gpt-5.6-luna", usage) - expected) < 1e-9


def test_모르는_gpt모델도_0원이_아니다():
    """표에 없는 모델을 0원으로 두면 그 모델을 고른 판이 무제한이 된다(계량 구멍)."""
    assert estimate_cost_usd("gpt-9.9-unknown", {"output_tokens": 1_000_000}) > 0
    assert rates_for("gpt-5.6-luna") == (1.00, 0.10, 6.00)


def test_턴결산에_cost_usd가_실린다():
    """러너(builder.on_turn)는 rec['cost_usd']가 있을 때만 사용량을 웹에 보고한다 — 그 키가 계약."""
    rec = usage_record("gpt-5.6-luna", {"input_tokens": 10_000, "output_tokens": 2_000})
    assert rec["cost_usd"] > 0 and rec["tokens_out"] == 2_000
    assert usage_record("gpt-5.6-luna", {}) == {}      # 사용량 없음 = 결산도 없음(허위 0원 금지)


def test_codex_이벤트에서_사용량을_읽는다():
    assert _usage_from_event({"type": "turn.completed", "usage": {"output_tokens": 5}}) \
        == ("turn", {"output_tokens": 5})
    kind, u = _usage_from_event({"type": "token_count",
                                 "info": {"last_token_usage": {"output_tokens": 3},
                                          "total_token_usage": {"output_tokens": 99}}})
    assert (kind, u) == ("delta", {"output_tokens": 3})   # 턴 단위는 last(누적 total 아님)
    assert _usage_from_event({"type": "item.completed"}) == (None, {})


def test_GPT턴_사용량이_Organt결산에_들어간다(monkeypatch):
    """codex 턴 → on_usage → _last_result. 이 사슬이 끊기면 원장이 0원이 된다."""
    async def fake_turn(**kw):
        kw["on_usage"]({"input_tokens": 1_000, "cached_input_tokens": 0, "output_tokens": 500})
        return "완료했습니다", "sid-1"

    monkeypatch.setattr("organt.codex_mcp_bridge.run_codex_turn", fake_turn)
    o = Organt(_cfg())
    o._codex_model = "gpt-5.6-luna"
    text, sid = asyncio.run(o._run_codex("일 해줘"))
    assert (text, sid) == ("완료했습니다", "sid-1")
    assert o._last_result["cost_usd"] > 0 and o._last_result["tokens_out"] == 500


# ── ② 모델별 추론 강도 ─────────────────────────────────────────────────────
def test_구세대_모델은_지원상한으로_깎인다():
    assert _clamp_effort("gpt-5.4-mini", "max") == "xhigh"
    assert _clamp_effort("gpt-5.4", "max") == "xhigh"
    assert _clamp_effort("gpt-5.5", "high") == "high"


def test_구세대_모델은_강도미지정도_명시된다():
    """빈 값이면 -c를 안 붙여 머신 전역(ultra)을 물려받아 400이 났다 — 명시값으로 끊는다."""
    assert _clamp_effort("gpt-5.4-mini", "") == "high"
    assert _clamp_effort("gpt-5.4-mini", None) == "high"


def test_최신세대와_Claude는_종전_그대로():
    assert _clamp_effort("gpt-5.6-luna", "max") == "max"
    assert _clamp_effort("gpt-5.6-sol", "") == ""         # 전역 상속 유지(무회귀)
    assert _clamp_effort("opus", "max") == "max"
    assert _clamp_effort(None, "") == ""
