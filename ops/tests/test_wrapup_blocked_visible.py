"""마지막 한 칸이 침묵 속에 막힌다 (2026-08-06, 현준-1 — U-504 실측).

원했던 e2e 흐름: iter_verify 통과 → 주기 wrapup 전이 → wrapup_done → next_milestone 없음 →
Task 경계 → e2e_open → … → complete_task.

실측 U-504: iter 6차 1/1 통과·SubTask 9개 전부 done까지 왔는데 주기가 닫히지 않고 '무진전'으로
정체 → 재개해도 같은 재생을 반복. 원인: sys_core의 wrapup_done 호출 3곳이 **반환(막힘 사유)을
전부 버렸다**. 배달 게이트 등이 막으면 사유가 로그에도 피드에도 남지 않아, 판은 이유 없이 도는
것처럼 보였고 진전 판정도 False가 됐다.

수리: _wrapup_close가 사유를 장부(wrapup_blocked)와 피드([주기 마감 보류])에 남긴다. 사유 문구는
이미 '지금 할 일 하나'를 지시하는 형태라, 드러나기만 하면 다음 턴이 처리한다.
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])


def _sys():
    from system.sys_core import Sys
    o = object.__new__(Sys)          # 생성자 우회 — _wrapup_close는 self._log만 쓴다
    o._logged = []
    o._log = lambda event, **f: o._logged.append((event, f))
    return o


def _flow_ms(status="wrapup", block=""):
    from system.rule import milestone as M
    ms = M.Milestone(ms_id="MS-1-1", goal="테스트 주기", criteria=[])
    ms.status = status
    flow = types.SimpleNamespace(current=None, milestones=[ms], workspace="", log=None,
                                 backlog_relays={}, posts=[])
    flow.post_system = lambda t: flow.posts.append(t)
    if block:
        # 배달 게이트를 흉내 — cycle_delivery_error가 사유를 돌려주게 workspace를 조작하는 대신
        # wrapup 전 상태로 두면 wrapup_done이 자체 사유를 돌려준다.
        ms.status = "open"
    return flow, ms


def test_막히면_사유가_장부와_피드에_남는다():
    o = _sys()
    flow, ms = _flow_ms(block="pre-wrapup")          # status=open → '정리 완료 선언 불가' 사유
    ok = o._wrapup_close(flow, ms)
    assert ok is False
    assert any(e == "wrapup_blocked" for e, _ in o._logged), o._logged
    assert flow.posts and flow.posts[0].startswith("[주기 마감 보류]"), flow.posts


def test_같은_사유는_한_번만_게시된다():
    o = _sys()
    flow, ms = _flow_ms(block="pre-wrapup")
    o._wrapup_close(flow, ms)
    o._wrapup_close(flow, ms)
    assert len(flow.posts) == 1                       # 로그는 매번, 게시는 사유당 1회
    assert sum(1 for e, _ in o._logged if e == "wrapup_blocked") == 2


def test_닫히면_True_게시_없음():
    o = _sys()
    flow, ms = _flow_ms(status="wrapup")
    ok = o._wrapup_close(flow, ms)
    assert ok is True and ms.status == "done"
    assert not flow.posts


# ── [wrapup은 무인지대였다(2026-08-06)] 구동기 결선 — open만 받던 검증 구동기가 wrapup도 닫는다 ──

def test_wrapup_주기는_구동기가_직접_닫는다():
    import asyncio
    from system.sys_core import Sys
    o = _sys()
    flow, ms = _flow_ms(status="wrapup")
    flow.backlog_relays = {}
    flow.current = types.SimpleNamespace(owner=11)
    flow.anchor = 11
    o._backlog_in_progress = lambda f: None
    ok = asyncio.run(Sys._verify_exhausted_milestone(o, flow))
    assert ok is True and ms.status == "done"


def test_막힌_wrapup은_담당_턴으로_사유를_실어_보낸다():
    import asyncio
    from system.sys_core import Sys
    o = _sys()
    flow, ms = _flow_ms(status="wrapup")
    flow.backlog_relays = {}
    flow.current = types.SimpleNamespace(owner=11)
    flow.anchor = 11
    o._backlog_in_progress = lambda f: None
    # 배달 게이트 흉내 — wrapup_done이 막힘 사유를 돌려주게 미완 ST를 심는다
    from system.rule import milestone as M
    st = M.SubTask(st_id="MS-1-1/ST-1", goal="미완 단위", criteria=[])
    st.status = "open"
    ms.subtasks = [st]
    turns = []
    async def _rt(f, who, prompt, kind, role):
        turns.append((who, prompt))
        st.status = "done"                      # 담당이 처리했다고 가정
    o.run_turn = _rt
    ok = asyncio.run(Sys._verify_exhausted_milestone(o, flow))
    assert turns and "주기 마감 보류" in turns[0][1]
    assert ok is True and ms.status == "done"   # 처리 후 재시도가 닫았다
