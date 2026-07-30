"""[P0 봇풀 바운딩(2026-07-18, HA 설계)] 봇 턴 = `claude` CLI 서브프로세스. 회의 _fork_collect가
심의단 N명 턴을 asyncio.gather로 병렬 스폰하고, 그런 흐름이 cap개 동시에 돌 수 있어 — 동시 CLI
서브프로세스 수가 (흐름 × 심의단)로 무바운드하게 자란다(메모리·CPU 스파이크·OOM 위험). 이 모듈은
전역 세마포어 + 메모리 입장 제어로 '동시에 사는 서브프로세스'를 하드 상한 지운다(초과분은 대기 =
자연 백프레셔). 순수 프로세스 로컬 — 다중 머신과 무관하게 단일 머신 안정성을 올린다.

[서버 단위 상한(2026-07-29)] 전역 상한만으론 '시끄러운 이웃'을 못 막는다. 채널(서버) 하나가
슬롯을 전부 채우면 다른 채널의 봇은 그 판이 끝날 때까지 한 턴도 못 돈다. 테넌트별 상한을 한 겹
더 두어, 한 서버가 쓸 수 있는 동시 슬롯을 제한한다(전역 상한은 호스트 보호로 그대로 유지).
기본 0 = 비활성이라 켜기 전까지 동작은 종전과 같다.

튜닝(env):
  ORGANT_MAX_SUBPROCS   동시 CLI 서브프로세스 상한(기본 8). 0 이하면 비활성(무제한 — 종전 동작).
  ORGANT_MAX_SUBPROCS_PER_TENANT
                        채널(서버) 하나가 동시에 쓸 수 있는 슬롯 상한(기본 0 = 비활성).
                        전역 상한과 함께 걸리며, 테넌트 슬롯을 먼저 잡고 전역 슬롯을 잡는다.
  ORGANT_MEM_FLOOR_MB   이 여유메모리(MemAvailable) 미만이면 새 스폰을 지연(기본 500). 0이면 비활성.
  ORGANT_SPAWN_WAIT_S   메모리 부족 시 최대 대기(기본 45s). 넘으면 그냥 진행(교착 방지 — 상한은 세마포어).
"""
import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager

_sem = None            # 지연 생성(이벤트루프 바인딩) — 프로세스당 1개
_sem_size = None


def _limit():
    try:
        return int(os.environ.get("ORGANT_MAX_SUBPROCS", "8"))
    except ValueError:
        return 8


def _get_sem():
    global _sem, _sem_size
    n = _limit()
    if n <= 0:
        return None                       # 비활성 — 종전 무제한 동작
    if _sem is None or _sem_size != n:
        _sem = asyncio.Semaphore(n)       # 첫 사용 시 현재 루프에 바인딩
        _sem_size = n
    return _sem


# 테넌트별 세마포어. 상한값·이벤트루프가 바뀌면 통째로 버리고 다시 만든다(죽은 루프에 묶인
# 세마포어를 재사용하면 'attached to a different loop'로 터진다).
_tenant_state = {"size": None, "loop": None, "sems": {}}


def _tenant_limit():
    try:
        return int(os.environ.get("ORGANT_MAX_SUBPROCS_PER_TENANT", "0"))
    except ValueError:
        return 0


def _get_tenant_sem(tenant):
    """이 테넌트(채널)의 세마포어. 상한이 0 이하이거나 tenant가 없으면 None(=제한 없음).

    tenant가 None인 호출(테넌트를 모르는 경로)은 전역 상한만 받는다. 서버 단위 상한을 켠 뒤에도
    분류 불가한 턴이 막히지 않게 하려는 의도적 선택이다."""
    n = _tenant_limit()
    if n <= 0 or not tenant:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _tenant_state["size"] != n or _tenant_state["loop"] is not loop:
        _tenant_state.update(size=n, loop=loop, sems={})
    sems = _tenant_state["sems"]
    sem = sems.get(tenant)
    if sem is None:
        sem = sems[tenant] = asyncio.Semaphore(n)
    return sem


def _mem_available_mb():
    """리눅스 여유 메모리(MemAvailable, MB). 읽기 실패 시 None(입장 제어 스킵)."""
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024   # kB → MB
    except (OSError, ValueError, IndexError):
        return None
    return None


def _mem_floor_mb():
    try:
        return int(os.environ.get("ORGANT_MEM_FLOOR_MB", "500"))
    except ValueError:
        return 500


def _spawn_wait_s():
    try:
        return float(os.environ.get("ORGANT_SPAWN_WAIT_S", "45"))
    except ValueError:
        return 45.0


async def _await_memory(on_wait=None):
    """여유메모리가 바닥(floor) 아래면 회복될 때까지(상한 내) 대기 — OOM 전에 스폰을 미룬다."""
    floor = _mem_floor_mb()
    if floor <= 0:
        return
    waited = 0.0
    cap = _spawn_wait_s()
    while waited < cap:
        avail = _mem_available_mb()
        if avail is None or avail >= floor:
            return
        if on_wait and waited == 0.0:
            try:
                on_wait(avail, floor)
            except Exception:
                pass
        await asyncio.sleep(1.5)
        waited += 1.5
    # 상한 초과 — 그냥 진행(세마포어가 동시성 상한을 이미 지키므로 교착보다 진행이 안전)


@asynccontextmanager
async def slot(on_wait=None, tenant=None):
    """봇 턴 1개의 서브프로세스 슬롯 — 메모리 입장 제어 통과 후 테넌트·전역 세마포어 획득.

    사용: `async with botpool.slot(tenant=pid): async with ClaudeSDKClient(...) as c: ...`
    두 세마포어 모두 비활성이면 no-op로 통과(종전 동작 불변).

    획득 순서는 테넌트 → 전역으로 고정한다. 반대로 잡으면 전역 슬롯을 쥔 채 테넌트 슬롯을
    기다리게 되어, 대기자가 호스트 전체 슬롯을 갉아먹고 남의 채널까지 굶긴다. 순서가 한 방향
    이라 교착도 생기지 않는다."""
    await _await_memory(on_wait)
    tsem = _get_tenant_sem(tenant)
    sem = _get_sem()
    async with AsyncExitStack() as stack:
        if tsem is not None:
            await stack.enter_async_context(tsem)
        if sem is not None:
            await stack.enter_async_context(sem)
        yield
