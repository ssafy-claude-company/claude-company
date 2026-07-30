"""[P0 봇풀 바운딩·로그 로테이션(2026-07-18, HA 설계)] 동시 서브프로세스 상한·메모리 입장·무한로그 방지."""
import asyncio
import os

from organt import botpool
from system.logrotate import rotate_if_needed, RotatingCounter


def test_세마포어가_동시성을_상한한다(monkeypatch):
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS", "3")
    monkeypatch.setenv("ORGANT_MEM_FLOOR_MB", "0")   # 메모리 게이트 끄고 세마포어만 측정
    botpool._sem = None                              # 새 상한으로 재바인딩

    async def run():
        active, peak = [0], [0]
        async def w():
            async with botpool.slot():
                active[0] += 1; peak[0] = max(peak[0], active[0])
                await asyncio.sleep(0.02); active[0] -= 1
        await asyncio.gather(*(w() for _ in range(12)))
        return peak[0]
    assert asyncio.run(run()) <= 3


def test_상한_0이면_무제한_통과(monkeypatch):
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS", "0")
    monkeypatch.setenv("ORGANT_MEM_FLOOR_MB", "0")
    botpool._sem = None

    async def run():
        n = [0]
        async def w():
            async with botpool.slot():
                n[0] += 1
        await asyncio.gather(*(w() for _ in range(20)))
        return n[0]
    assert asyncio.run(run()) == 20         # 비활성 = 종전 무제한 동작


def _reset_pool():
    botpool._sem = None
    botpool._tenant_state.update(size=None, loop=None, sems={})


def test_테넌트별_상한이_한_채널의_독식을_막는다(monkeypatch):
    """[서버 단위 상한(2026-07-29)] 전역 8이어도 한 채널은 제 상한까지만 동시에 돈다."""
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS", "8")
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS_PER_TENANT", "2")
    monkeypatch.setenv("ORGANT_MEM_FLOOR_MB", "0")
    _reset_pool()

    async def run():
        active, peak = {}, {}
        async def w(t):
            async with botpool.slot(tenant=t):
                active[t] = active.get(t, 0) + 1
                peak[t] = max(peak.get(t, 0), active[t])
                await asyncio.sleep(0.02)
                active[t] -= 1
        await asyncio.gather(*(w("P-1") for _ in range(8)),
                             *(w("P-2") for _ in range(8)))
        return peak
    peak = asyncio.run(run())
    assert peak["P-1"] <= 2 and peak["P-2"] <= 2      # 채널마다 따로 상한
    assert peak["P-2"] >= 1                           # 다른 채널이 굶지 않는다


def test_테넌트_상한_0이면_종전동작(monkeypatch):
    """기본값 0 = 비활성. 켜기 전까지 전역 상한만 걸린다(라이브 무변경 보장)."""
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS", "5")
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS_PER_TENANT", "0")
    monkeypatch.setenv("ORGANT_MEM_FLOOR_MB", "0")
    _reset_pool()

    async def run():
        active, peak = [0], [0]
        async def w():
            async with botpool.slot(tenant="P-1"):
                active[0] += 1; peak[0] = max(peak[0], active[0])
                await asyncio.sleep(0.02); active[0] -= 1
        await asyncio.gather(*(w() for _ in range(12)))
        return peak[0]
    assert asyncio.run(run()) == 5        # 한 채널이어도 전역 상한까지 쓴다


def test_테넌트_미지정은_전역상한만_받는다(monkeypatch):
    """분류 불가한 턴이 서버 상한을 켠 뒤에도 막히지 않는다."""
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS", "4")
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS_PER_TENANT", "1")
    monkeypatch.setenv("ORGANT_MEM_FLOOR_MB", "0")
    _reset_pool()

    async def run():
        active, peak = [0], [0]
        async def w():
            async with botpool.slot():          # tenant 없음
                active[0] += 1; peak[0] = max(peak[0], active[0])
                await asyncio.sleep(0.02); active[0] -= 1
        await asyncio.gather(*(w() for _ in range(10)))
        return peak[0]
    assert asyncio.run(run()) == 4          # 테넌트 상한 1에 걸리지 않는다


def test_메모리_바닥이면_지연되다_상한후_진행(monkeypatch):
    monkeypatch.setenv("ORGANT_MAX_SUBPROCS", "4")
    monkeypatch.setenv("ORGANT_MEM_FLOOR_MB", "999999999")   # 항상 바닥 취급
    monkeypatch.setenv("ORGANT_SPAWN_WAIT_S", "0")           # 즉시 상한 → 교착 없이 진행
    botpool._sem = None
    hit = []

    async def run():
        async with botpool.slot(on_wait=lambda a, f: hit.append((a, f))):
            return "entered"
    assert asyncio.run(run()) == "entered"   # 상한 대기 후에도 진행(세마포어가 진짜 상한)


def test_로그_로테이션_상한_넘으면_밀어낸다(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANT_LOG_MAX_MB", "0")   # 0 = 비활성
    p = tmp_path / "audit.jsonl"
    p.write_text("x" * 5000, encoding="utf-8")
    rotate_if_needed(str(p))
    assert not (tmp_path / "audit.jsonl.1").exists()          # 비활성이면 그대로

    # 아주 작은 상한으로 로테이션 발동(1바이트 상한을 흉내 — env가 MB라 대신 직접 호출 경로 확인)
    monkeypatch.setenv("ORGANT_LOG_MAX_MB", "1")
    big = tmp_path / "flow.jsonl"
    big.write_bytes(b"y" * (2 * 1024 * 1024))                 # 2MB > 1MB 상한
    rotate_if_needed(str(big))
    assert (tmp_path / "flow.jsonl.1").exists()               # 밀려남
    assert not big.exists() or big.stat().st_size == 0        # 원본 비워짐/이동


def test_로테이팅카운터_주기검사(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANT_LOG_MAX_MB", "1")
    p = tmp_path / "a.jsonl"
    p.write_bytes(b"z" * (2 * 1024 * 1024))
    rc = RotatingCounter(str(p), every=3)
    rc.tick(); rc.tick()
    assert not (tmp_path / "a.jsonl.1").exists()              # 아직 주기 안 참
    rc.tick()
    assert (tmp_path / "a.jsonl.1").exists()                  # 3회째 검사·로테이션


# ── [레지스트리 DB화(2026-07-18, HA 설계 — 러너 페일오버 준비)] 플래그 off 무변경·on 왕복 ──
def test_레지스트리_writethrough_플래그(monkeypatch):
    import asyncio
    from system import sys_store
    sys_store._reg_last_push[0] = 0.0

    class _G:
        def __init__(self): self.pushed = []
        async def put_state(self, ch, kind, data): self.pushed.append((ch, kind, data))

    class _S:
        def __init__(self): self.guide = _G()

    async def run(on):
        s = _S()
        if on:
            monkeypatch.setenv("ORGANT_REGISTRY_DB", "1")
        else:
            monkeypatch.delenv("ORGANT_REGISTRY_DB", raising=False)
        sys_store._reg_last_push[0] = 0.0
        sys_store._push_registry_db(s, {"n": 1, "projects": {"9": {"id": "P-9"}}, "queue": []})
        await asyncio.sleep(0.01)
        return s.guide.pushed

    assert asyncio.run(run(False)) == []                      # 플래그 off = 무동작(라이브 무변경)
    pushed = asyncio.run(run(True))
    assert len(pushed) == 1 and pushed[0][0] == 0 and pushed[0][1] == "registry"


def test_레지스트리_DB부팅복원_플래그(monkeypatch):
    from system import sys_store

    class _G:
        def get_state_sync(self, ch, kind):
            return {"n": 3, "projects": {"7": {"id": "P-7"}}, "queue": []}

    class _S:
        def __init__(self):
            self.guide = _G(); self.projects = {}; self._proj_n = 0; self.queue = []
            self.projects_path = None; self.seed_path = None; self.logs = []
        def _log(self, ev, **k): self.logs.append(ev)
        def _save_projects(self): pass

    # 플래그 off → DB 무시(파일 경로, projects_path None이라 무복원)
    monkeypatch.delenv("ORGANT_REGISTRY_FROM_DB", raising=False)
    s = _S(); sys_store.load_projects(s)
    assert s.projects == {}                                   # DB 안 봄

    # 플래그 on → DB에서 복원
    monkeypatch.setenv("ORGANT_REGISTRY_FROM_DB", "1")
    s2 = _S(); sys_store.load_projects(s2)
    assert s2.projects == {7: {"id": "P-7"}} and s2._proj_n == 3
    assert "projects_db_restored" in s2.logs


def test_레지스트리_디바운스_트레일링_push(monkeypatch):
    """[검수 2026-07-18] 디바운스 창 안 저장은 버려지지 않고 최신본으로 창 끝 1회 push —
    '마지막 저장'이 DB에 영영 안 실리던 유실(페일오버 부팅이 낡은 레지스트리) 봉합."""
    import asyncio
    from system import sys_store

    class _G:
        def __init__(self): self.pushed = []
        async def put_state(self, ch, kind, data): self.pushed.append(data)

    class _S:
        def __init__(self): self.guide = _G()

    monkeypatch.setenv("ORGANT_REGISTRY_DB", "1")
    monkeypatch.setattr(sys_store, "_REG_DEBOUNCE_S", 0.1)

    async def run():
        s = _S()
        sys_store._reg_last_push[0] = 0.0
        sys_store._reg_pending[0] = None
        sys_store._push_registry_db(s, {"n": 1})     # 창 밖 — 즉시 push
        sys_store._push_registry_db(s, {"n": 2})     # 창 안 — 트레일링 예약
        sys_store._push_registry_db(s, {"n": 3})     # 창 안 — 최신본으로 교체
        await asyncio.sleep(0.3)                     # 창(0.1s) 경과 — 트레일링 발화
        return s.guide.pushed

    pushed = asyncio.run(run())
    assert pushed and pushed[0]["n"] == 1                       # 즉시 push
    assert pushed[-1]["n"] == 3 and len(pushed) == 2            # 최신본만 1회(중간분 병합)
