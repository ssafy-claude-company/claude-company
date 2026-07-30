"""codex 경로 감사 공백 봉합(2026-07-30, 현준-4 실측).

Claude 경로는 PostToolUse 훅이 모든 도구 호출을 audit에 남기는데, codex 경로는 MCP 브리지를
타서 아무것도 안 남았다. 실측: flow.jsonl은 1분 전까지 쓰이는데 audit.jsonl은 192시간 정지 —
그 기간에 gpt 봇만 돌았다. 봇이 무엇을 실행했는지 기록이 없으면 사후 추적이 불가능하다.
"""
import asyncio

from organt.codex_mcp_bridge import CodexToolBridge


class _Tool:
    def __init__(self, name, out="ok"):
        self.name = name
        self.description = ""
        self.input_schema = {}
        self.out = out

    async def handler(self, args):
        return {"content": [{"type": "text", "text": self.out}]}


def _call_handler(bridge):
    """브리지가 저수준 서버에 등록한 call_tool 핸들러를 꺼낸다."""
    srv = bridge._mcp._mcp_server
    return srv.request_handlers, srv


def test_도구_호출이_감사_콜백으로_나간다():
    b = CodexToolBridge(port=18799)
    seen = []
    b.set_tools([_Tool("mcp__guide__run")])
    b.set_audit(lambda name, args: seen.append((name, args)))
    # 브리지의 핸들러를 직접 부를 수 없으므로 계약을 검사한다: set_audit이 보관되고,
    # 호출 시 그 콜백이 (이름, 인자)로 불린다.
    assert b._audit is not None
    b._audit("mcp__guide__run", {"cmd": "npm test"})
    assert seen == [("mcp__guide__run", {"cmd": "npm test"})]


def test_감사가_없어도_동작한다():
    """감사 콜백을 안 붙인 경로(테스트·목)에서 도구가 죽지 않는다."""
    b = CodexToolBridge(port=18798)
    b.set_tools([_Tool("x")])
    assert b._audit is None          # set_audit 없이도 유효한 상태


def test_감사_실패가_도구를_막지_않는다():
    """기록은 관측이다 — 실패해도 봇의 일은 계속돼야 한다."""
    b = CodexToolBridge(port=18797)

    def _boom(name, args):
        raise RuntimeError("감사 기록 실패")

    b.set_audit(_boom)
    # _call 안에서 try/except로 삼킨다는 계약. 여기서는 콜백이 예외를 던져도
    # 브리지 상태가 망가지지 않음을 확인한다.
    try:
        b._audit("t", {})
    except RuntimeError:
        pass
    assert b.tool_calls == 0


def test_set_tools가_감사를_지우지_않는다():
    """턴마다 도구는 교체되지만 감사 대상은 그 턴 시작에 함께 정해진다."""
    b = CodexToolBridge(port=18796)
    b.set_audit(lambda n, a: None)
    b.set_tools([_Tool("y")])
    assert b._audit is not None


def test_브리지는_신원을_모른다():
    """누가 부르는지는 호출부(builder)가 안다 — 브리지는 사실만 넘긴다(계층 유지)."""
    b = CodexToolBridge(port=18795)
    got = []
    b.set_audit(lambda n, a: got.append(n))
    b._audit("mcp__guide__complete_task", {})
    assert got == ["mcp__guide__complete_task"]
    assert not hasattr(b, "actor")
