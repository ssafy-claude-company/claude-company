"""동시 발언 계약 — 응찰이 선 사람들이 같은 라운드에서 함께 말한다(낙찰자만 편집)."""
import asyncio

from system.rule.floor import (OPEN, SELF, Allocation, FloorState, Turn, parallel_floor_width,
                               run_conversation)


class _Pol:
    """OPEN → 응찰 수집 → SELF(최고 응찰) 한 번, 그 다음은 종결."""
    name = "turn-taking"

    def __init__(self):
        self.n = 0

    def next_after(self, state, turn):
        self.n += 1
        return Allocation(OPEN, candidates=[11, 12, 13]) if self.n == 1 else Allocation("close")

    def resolve_open(self, state, turn, bids):
        return Allocation(SELF, next=max(bids, key=lambda b: b[1])[0], reason="응찰")


def _run(width, monkeypatch):
    monkeypatch.setenv("ORGANT_FLOOR_PARALLEL", str(width))
    spoken, companions = [], []

    async def speak(who, alloc):
        spoken.append(who)
        return Turn(speaker=who, body="발언")

    async def speak_many(winner, extra, alloc):
        spoken.append(winner)
        companions.extend(extra)
        return [Turn(speaker=w, body="발언") for w in [winner] + list(extra)]

    st = FloorState([11, 12, 13])
    asyncio.run(run_conversation(_Pol(), st, Turn(speaker=11, body="개시"), speak,
                                 bid=lambda c, p: _bids(), max_turns=6,
                                 speak_many=speak_many))
    return spoken, companions


async def _bids():
    return [(11, 9), (12, 7), (13, 3)]


def test_응찰이_선_사람들이_함께_말한다(monkeypatch):
    spoken, companions = _run(3, monkeypatch)
    assert spoken == [11], "낙찰자는 한 명"
    assert sorted(companions) == [12, 13], "응찰한 나머지가 동행 발언"


def test_폭이_1이면_종전대로_한_명만(monkeypatch):
    spoken, companions = _run(1, monkeypatch)
    assert spoken == [11] and companions == []


def test_기본폭은_3(monkeypatch):
    monkeypatch.delenv("ORGANT_FLOOR_PARALLEL", raising=False)
    assert parallel_floor_width() == 3
