"""마일스톤 DB 미러는 같은 채널/종류 안에서 오래된 응답이 최신 상태를 덮지 않는다."""
import asyncio
from types import SimpleNamespace

from system.rule import milestone


def _flow(put_state):
    return SimpleNamespace(
        user_channel=42,
        guide=SimpleNamespace(put_state=put_state),
    )


def test_느린_open_응답이_done_최신상태를_덮지_않는다(monkeypatch):
    monkeypatch.setenv("ORGANT_STATE_DB", "1")

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []
        stored = {}

        async def put_state(ch, kind, data):
            calls.append(data["status"])
            if data["status"] == "open":
                entered.set()
                await release.wait()
            stored[(ch, kind)] = data

        flow = _flow(put_state)
        milestone._push_state_db(flow, 42, "ms", {"status": "open"})
        await entered.wait()
        milestone._push_state_db(flow, 42, "ms", {"status": "done"})
        # done을 별도 병렬 task로 보내지 않는다. open 응답 뒤 같은 writer가 순서대로 보낸다.
        await asyncio.sleep(0)
        assert calls == ["open"]
        release.set()
        await milestone.flush_state_db(flow, kind="ms")

        assert calls == ["open", "done"]
        assert stored[(42, "ms")] == {"status": "done"}

    asyncio.run(scenario())


def test_전송중_빠른_활동갱신은_누적된_최신장으로_합쳐진다(monkeypatch):
    monkeypatch.setenv("ORGANT_STATE_DB", "1")

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def put_state(_ch, _kind, data):
            calls.append(data)
            if data["n"] == 0:
                entered.set()
                await release.wait()

        flow = _flow(put_state)
        milestone._push_state_db(flow, 42, "ms", {"n": 0, "act": []})
        await entered.wait()
        for n in range(1, 51):
            milestone._push_state_db(
                flow, 42, "ms", {"n": n, "act": [f"생각-{i}" for i in range(1, n + 1)]})
        release.set()
        await milestone.flush_state_db(flow, kind="ms")

        # DB가 보관할 수 없는 중간 장 49개는 합치되, 최신 누적 활동과 번호는 하나도 잃지 않는다.
        assert [c["n"] for c in calls] == [0, 50]
        assert calls[-1]["act"] == [f"생각-{i}" for i in range(1, 51)]

    asyncio.run(scenario())


def test_옛장_전송실패중_들어온_최신장은_계속_전송한다(monkeypatch):
    monkeypatch.setenv("ORGANT_STATE_DB", "1")

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def put_state(_ch, _kind, data):
            calls.append(data["status"])
            if data["status"] == "open":
                entered.set()
                await release.wait()
                raise RuntimeError("temporary mirror outage")

        flow = _flow(put_state)
        milestone._push_state_db(flow, 42, "ms", {"status": "open"})
        await entered.wait()
        milestone._push_state_db(flow, 42, "ms", {"status": "done"})
        release.set()
        await milestone.flush_state_db(flow, kind="ms")

        assert calls == ["open", "done"]

    asyncio.run(scenario())
