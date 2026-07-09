"""[§5 재배치 — S3 계약 테스트] SYS 자동 개입 장치의 파이프라인 ON 우회 (BRIEF-phys-S3).

고정하는 계약:
  ① _auto_coordinate: ON이면 강제 배정 안 함 + **큐 보존**(릴레이 ②응찰의 몫 — 비우면 유실).
     OFF면 종전 그대로 큐를 소비(리더 명의 직접 위임 시도).
  ② _auto_continue_owner: ON이면 재발사 대신 **주기 이어가기 접점**(flow.iter_continue — S1
     시그니처 확정 전 mock)으로 위임. 미주입이면 아무것도 안 함. OFF면 종전 경로(배포 실증=완료
     인정 블록 포함) 그대로.
  ③ 부팅 복구 존중: §9로 복원된 '주기 중간' 상태(milestones)를 자동 장치가 건드리지 않고
     접점에 그대로 넘긴다.
"""
import asyncio

import pytest

from system.rule.milestone import ms_from_dict, next_milestone
from system.sys_core import Sys
from test_sys import FakeGuide, _flow


@pytest.fixture
def onflag(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")


@pytest.fixture
def offflag(monkeypatch):
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)


def _sys_flow():
    g = FakeGuide()
    s = Sys(g, guild_id=1, organt_builder=None, bot_info={11: "L", 12: "백엔드", 13: "프론트"})
    f = _flow(g)
    f.log = s._log
    return s, f


def _ref(owner=12, incomplete=True):
    return type("Ref", (), {"task_id": "t-1", "owner": owner,
                            "owner_incomplete": incomplete, "owner_delivered": False,
                            "last_work_body": "원문 위임"})()


_COORD = [{"to": 13, "req_role": "백엔드", "to_role": "프론트", "body": "프론트 연결", "requester": 12}]


# ── ① _auto_coordinate ──────────────────────────────────────────────────

def test_coordinate_on_skips_and_preserves_queue(onflag):
    s, f = _sys_flow()
    f.pending_coordination = list(_COORD)
    out = asyncio.run(s._auto_coordinate(f, 11))
    assert out == ""
    assert f.pending_coordination == _COORD            # 큐 보존 — 릴레이가 소비할 몫
    assert not any(r["event"] == "auto_coordinate" for r in s.flow_log)


def test_coordinate_off_consumes_queue_legacy(offflag):
    s, f = _sys_flow()
    f.pending_coordination = list(_COORD)
    out = asyncio.run(s._auto_coordinate(f, 11))
    assert f.pending_coordination == []                # 종전: SYS가 소비(직접 위임 시도)
    assert any(r["event"] == "auto_coordinate" for r in s.flow_log)
    assert "[SYS 조율" in out                          # 위임 시도 결과(성공/오류 무관)가 리더에게 회신


# ── ② _auto_continue_owner ─────────────────────────────────────────────

def test_continue_on_delegates_to_iter_hook(onflag):
    s, f = _sys_flow()
    f.current = _ref()
    f._deploy_live = True                              # OFF였다면 '배포 실증=완료' 블록이 잡았을 상황
    got = {}

    async def hook(flow, lead):
        got.update(flow=flow, lead=lead)
        return "[주기 이어가기 — iter가 재개]"

    f.iter_continue = hook
    out = asyncio.run(s._auto_continue_owner(f, 11))
    assert out == "[주기 이어가기 — iter가 재개]" and got["lead"] == 11
    assert f.current.owner_incomplete is True          # 완료 판정도 주기 몫 — SYS가 안 건드림
    assert not any(r["event"] in ("sys_auto_continue", "deploy_goal_met_owner_done")
                   for r in s.flow_log)


def test_continue_on_without_hook_does_nothing(onflag):
    s, f = _sys_flow()
    f.current = _ref()
    assert asyncio.run(s._auto_continue_owner(f, 11)) == ""
    assert f.current.owner_incomplete is True          # 재발사도 해제도 없음


def test_continue_off_legacy_deploy_block_intact(offflag):
    s, f = _sys_flow()
    f.current = _ref()
    f._deploy_live = True
    called = []
    f.iter_continue = lambda flow, lead: called.append(1)   # OFF에선 접점을 타지 않는다
    out = asyncio.run(s._auto_continue_owner(f, 11))
    assert "배포 목표 달성" in out and called == []
    assert f.current.owner_incomplete is False         # 종전 경로: 실증=완료 인정 그대로
    assert any(r["event"] == "deploy_goal_met_owner_done" for r in s.flow_log)


# ── ③ 부팅 복구 존중 ────────────────────────────────────────────────────

def test_recovered_midcycle_state_respected(onflag):
    """§9 복원된 '주기 중간'(ms1 done·ms2 iter 1회 진행) 상태가 자동 장치를 지나도 그대로다."""
    s, f = _sys_flow()
    f.milestones = [
        ms_from_dict({"ms_id": "MS-1", "goal": "api", "status": "done", "iter_n": 2,
                      "criteria": [{"desc": "c1", "verify": "v1", "passed": True, "evidence": "e"}]}),
        ms_from_dict({"ms_id": "MS-2", "goal": "front", "status": "open", "iter_n": 1,
                      "criteria": [{"desc": "c2", "verify": "v2"}]}),
    ]
    f.current = _ref()
    f.pending_coordination = list(_COORD)
    seen = {}
    f.iter_continue = lambda flow, lead: seen.update(
        ms=[m.ms_id for m in flow.milestones], nxt=next_milestone(flow).ms_id) or "재개"

    assert asyncio.run(s._auto_continue_owner(f, 11)) == "재개"
    assert asyncio.run(s._auto_coordinate(f, 11)) == ""
    assert seen == {"ms": ["MS-1", "MS-2"], "nxt": "MS-2"}   # 복원 상태가 접점에 그대로 전달
    assert f.milestones[1].iter_n == 1 and f.milestones[1].status == "open"
    assert f.pending_coordination == _COORD