"""[턴 예산 회귀(2026-07-28)] GPT 봇의 '말하면 턴이 끝난다' 비대칭 교정.

Claude 봇은 max_turns 안에서 도구를 부르고 또 부른다. codex는 발화 한 번이면 exec가 끝나서,
봇이 '하겠습니다'라고 말하는 것만으로 자기 턴을 소진했다(U-065·U-067에서 complete_task 호출 0).

여기서 고정하는 계약:
  · 도구 호출 0인 실질 턴만 이어 붙인다 — 도구를 부른 턴은 절대 이어 붙이지 않는다(중복 작업 금지)
  · 이어가기는 **반드시 카운터를 올린다** — 상한이 있고 소진되면 끝난다(2026-07-27 폭주 91,402회 교훈)
  · 이어간 패스의 토큰도 원장에 누적된다(공짜 재시도 금지)
"""
import asyncio

import pytest

from organt import codex_mcp_bridge as bridge_mod


class _FakeBridge:
    """도구 호출 수만 흉내내는 브리지 — n번째 패스에서 도구를 부른 것으로 친다."""

    def __init__(self, call_at_pass=None):
        self._calls = 0
        self._at = call_at_pass
        self.passes = 0

    @property
    def tool_calls(self):
        return self._calls

    def ran_pass(self):
        self.passes += 1
        if self._at is not None and self.passes >= self._at:
            self._calls += 1


def _fake_process(bridge, usage=None, text="말만 했습니다"):
    async def _run(**kwargs):
        bridge.ran_pass()
        if kwargs.get("on_usage"):
            kwargs["on_usage"](dict(usage or {"output_tokens": 10}))
        return text, "sid-1"
    return _run


def _args(**over):
    base = {"prompt": "일 해줘", "cwd": "/tmp/ws", "session_id": None, "model": "gpt-5.6-luna",
            "effort": None, "on_activity": None, "on_narrate": None, "stderr": None,
            "read_only": False, "on_usage": None}
    base.update(over)
    return base


def test_침묵턴은_예산만큼_이어간다(monkeypatch):
    monkeypatch.setenv("ORGANT_CODEX_TURN_BUDGET", "2")
    b = _FakeBridge()                       # 끝까지 도구를 안 부르는 봇
    monkeypatch.setattr(bridge_mod, "_run_codex_process", _fake_process(b))
    text, sid = asyncio.run(bridge_mod._run_codex_budgeted(b, _args(), mcp_url="http://x/mcp", budget=bridge_mod._codex_turn_budget()))
    assert b.passes == 3                    # 첫 판 + 이어가기 2회
    assert (text, sid) == ("말만 했습니다", "sid-1")


def test_도구를_부른_턴은_이어가지_않는다(monkeypatch):
    monkeypatch.setenv("ORGANT_CODEX_TURN_BUDGET", "2")
    b = _FakeBridge(call_at_pass=1)         # 첫 판에서 도구를 부른 봇
    monkeypatch.setattr(bridge_mod, "_run_codex_process", _fake_process(b))
    asyncio.run(bridge_mod._run_codex_budgeted(b, _args(), mcp_url="http://x/mcp", budget=bridge_mod._codex_turn_budget()))
    assert b.passes == 1


def test_이어가다_도구를_부르면_즉시_멈춘다(monkeypatch):
    monkeypatch.setenv("ORGANT_CODEX_TURN_BUDGET", "3")
    b = _FakeBridge(call_at_pass=2)         # 이어간 첫 패스에서 도구 호출
    monkeypatch.setattr(bridge_mod, "_run_codex_process", _fake_process(b))
    asyncio.run(bridge_mod._run_codex_budgeted(b, _args(), mcp_url="http://x/mcp", budget=bridge_mod._codex_turn_budget()))
    assert b.passes == 2


def test_예산_0이면_종전과_같다(monkeypatch):
    """회의 발언 턴 등 표식이 없는 턴 = budget 0 — 이어가기 자체가 없다(비용 무회귀)."""
    b = _FakeBridge()
    monkeypatch.setattr(bridge_mod, "_run_codex_process", _fake_process(b))
    asyncio.run(bridge_mod._run_codex_budgeted(b, _args(), mcp_url="http://x/mcp"))
    assert b.passes == 1


def test_예산은_상한이_있다(monkeypatch):
    """운영자가 큰 값을 넣어도 무한 이어가기가 되지 않는다."""
    monkeypatch.setenv("ORGANT_CODEX_TURN_BUDGET", "999")
    assert bridge_mod._codex_turn_budget() == bridge_mod._CODEX_TURN_BUDGET_MAX
    monkeypatch.setenv("ORGANT_CODEX_TURN_BUDGET", "이상한값")
    assert bridge_mod._codex_turn_budget() == bridge_mod._CODEX_TURN_BUDGET_DEFAULT


def test_이어간_패스는_스레드_누계를_그대로_넘긴다(monkeypatch):
    """codex usage는 스레드 누계다 — 패스마다 더하면 이중 청구다(2026-07-28 실측 정정).
    이어가는 패스는 같은 스레드를 resume하므로 **마지막 값이 이미 전 패스를 포함**한다."""
    monkeypatch.setenv("ORGANT_CODEX_TURN_BUDGET", "2")
    b = _FakeBridge()
    seen = []
    totals = iter([{"input_tokens": 100, "output_tokens": 10},      # 1패스 시점 누계
                   {"input_tokens": 260, "output_tokens": 24},      # 2패스 시점 누계
                   {"input_tokens": 430, "output_tokens": 41}])     # 3패스 시점 누계

    async def _run(**kwargs):
        b.ran_pass()
        kwargs["on_usage"](next(totals))
        return "말만 했습니다", "sid-1"

    monkeypatch.setattr(bridge_mod, "_run_codex_process", _run)
    asyncio.run(bridge_mod._run_codex_budgeted(
        b, _args(on_usage=seen.append), mcp_url="http://x/mcp",
        budget=bridge_mod._codex_turn_budget()))
    assert seen[-1] == {"input_tokens": 430, "output_tokens": 41}   # 합(790)이 아니라 마지막 누계


def test_이어가기_프롬프트는_재촉하지_않는다():
    """장려·재촉 문구는 증류돼 다른 판을 망친다(2026-07-27 사용자 교정) — 상태와 규칙만 담는다."""
    p = bridge_mod._CODEX_CONTINUE_PROMPT.format(left=1)
    assert "도구 호출이 없었습니다" in p and "아직 열려 있습니다" in p
    for 재촉 in ("지금 당장", "빨리", "기다리지 말고", "반드시 호출", "두려워"):
        assert 재촉 not in p


def test_관측_훅으로_이어가기가_보인다(monkeypatch):
    monkeypatch.setenv("ORGANT_CODEX_TURN_BUDGET", "1")
    notes = []
    bridge_mod.set_turn_note_sink(lambda ev, kw: notes.append((ev, kw)))
    try:
        b = _FakeBridge()
        monkeypatch.setattr(bridge_mod, "_run_codex_process", _fake_process(b))
        asyncio.run(bridge_mod._run_codex_budgeted(b, _args(), mcp_url="http://x/mcp", budget=bridge_mod._codex_turn_budget()))
    finally:
        bridge_mod.set_turn_note_sink(None)
    kinds = [ev for ev, _ in notes]
    assert "codex_turn_continue" in kinds and "codex_turn_silent" in kinds


def test_SYS가_마감_e2e_턴에_표식을_단다():
    """표식을 다는 쪽(SYS)과 읽는 쪽(organt)이 갈라지면 예산이 영원히 안 켜진다 — 접점을 고정."""
    import inspect

    from system import sys_core
    src = inspect.getsource(sys_core.Sys.run_turn)
    assert "_codex_expect_tool" in src
    assert '("close", "e2e")' in src or "('close', 'e2e')" in src


def test_브리지가_도구호출을_센다():
    """이어갈지 말지의 유일한 근거 — set_tools마다 리셋된다."""
    b = bridge_mod.CodexToolBridge(port=18999)
    assert b.tool_calls == 0
    b._calls = 3
    b.set_tools([])
    assert b.tool_calls == 0


def test_마감_e2e_턴에만_예산이_붙는다(monkeypatch):
    """켜지는 자리 계약 — 회의 발언 턴에까지 걸면 모든 회의가 3배로 돈다."""
    seen = {}

    async def _fake_turn(**kw):
        seen.update(kw)
        return "ok", "sid"

    monkeypatch.setattr("organt.codex_mcp_bridge.run_codex_turn", _fake_turn)
    from organt.organt import Organt
    from system.config import Config
    from pathlib import Path
    o = Organt(Config(system_bot_token="s", channel_id=1, model=None,
                      workspace_dir=Path("/tmp/ws"), audit_log_path=Path("/tmp/a.jsonl")))
    o._codex_model = "gpt-5.6-luna"

    asyncio.run(o._run_codex("발언하세요"))
    assert seen["expect_tool"] is False          # 표식 없음 = 회의 발언 턴
    o._codex_expect_tool = True                  # SYS가 마감·e2e 턴에 다는 표식
    asyncio.run(o._run_codex("마감하세요"))
    assert seen["expect_tool"] is True
    asyncio.run(o._run_codex("표결", micro=True))
    assert seen["expect_tool"] is False          # 마이크로 턴은 제외


@pytest.mark.parametrize("budget", ["1", "2"])
def test_이어가도_같은_세션을_잇는다(monkeypatch, budget):
    """새 세션을 파면 직전 맥락(무엇을 하려던 참이었는지)이 사라진다."""
    monkeypatch.setenv("ORGANT_CODEX_TURN_BUDGET", budget)
    b = _FakeBridge()
    seen_sids = []

    async def _run(**kwargs):
        b.ran_pass()
        seen_sids.append(kwargs.get("session_id"))
        return "말만 했습니다", "sid-1"

    monkeypatch.setattr(bridge_mod, "_run_codex_process", _run)
    asyncio.run(bridge_mod._run_codex_budgeted(
        b, _args(), mcp_url="http://x/mcp", budget=bridge_mod._codex_turn_budget()))
    assert seen_sids[0] is None and all(s == "sid-1" for s in seen_sids[1:])
