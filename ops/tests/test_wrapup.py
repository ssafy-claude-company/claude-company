"""[S3 마무리층 — 계약 테스트] Task 경계 e2e 전수 검증·복기 진입·관측(PIPELINE_REWORK §6·§8·§11·§12).

여기서 고정하는 계약:
  ① '전수'의 분모 = 4축(조건 회귀·관통·표면·원문) 합집합 — 전 축이 비면 성립 안 함
  ② 판정 = 전 항목이 '증거 있는 pass'일 때만 e2e_pass (증거 없는 pass·누락 항목 = 결함)
  ③ 분모 밖 결과로 전수를 참칭하지 못한다(WrapupError)
  ④ e2e_fail → 결함 목록이 형식 보존된 채 ms_replan 진입점(entry — S1 확정 전 mock)에 전달
  ⑤ 결함 없이 재수립 진입 없음
  ⑥ §11 이벤트 이름 그대로 적재 + §8 오버헤드가 payload 동승(이벤트 이름 추가 없음)
  ⑦ §12 플래그: ORGANT_PIPELINE=milestone 일 때만 on (없으면 off — 라이브 불변의 전제)
(실 QA 봇이 항목을 검사하는 라이브 검증은 별도 — 여기는 구조의 대본 검증.)
"""
import asyncio
import os

import pytest

from system.rule.wrapup import (E2E_FAIL, E2E_PASS, KIND_ARC, KIND_CONDITION,
                                KIND_ORIGIN, KIND_SURFACE, MS_REPLAN, WrapupError,
                                build_checklist, emit_verdict, enter_replan,
                                format_defects, judge, overhead_snapshot, pipeline_on)


class FakeFlow:
    """log 수집만 하는 최소 flow — 카운터는 일부만 심어 느슨 결합을 검증."""

    def __init__(self):
        self.events = []
        self.meet_count = 3
        self.iter_count = 5

    def log(self, event, **kw):
        self.events.append((event, kw))


def _cl():
    return build_checklist(conditions=["counter API가 증가값을 저장한다"],
                           arcs=["기동→버튼 3회→새로고침→값 유지"],
                           surfaces=["GET /", "POST /count"],
                           origin_items=["버튼 누르면 카운트가 1씩 증가"])


def _all_pass(cl):
    return {it["id"]: {"ok": True, "evidence": f"run 출력: {it['id']} OK"} for it in cl}


# ── ① 분모 ──────────────────────────────────────────────────────────────

def test_checklist_four_kinds():
    cl = _cl()
    assert {it["kind"] for it in cl} == {KIND_CONDITION, KIND_ARC, KIND_SURFACE, KIND_ORIGIN}
    assert len(cl) == 5  # 표면 2 + 나머지 1씩


def test_checklist_empty_axis_ok_but_all_empty_fails():
    cl = build_checklist(conditions=["c1"])          # 한 축만 있어도 성립
    assert len(cl) == 1
    with pytest.raises(WrapupError):
        build_checklist()                            # 전 축 공백 = 분모 없음


def test_checklist_blank_items_dropped():
    cl = build_checklist(surfaces=["GET /", "  ", None])
    assert [it["spec"] for it in cl] == ["GET /"]


# ── ② 판정 ──────────────────────────────────────────────────────────────

def test_judge_all_evidenced_pass():
    cl = _cl()
    verdict, defects = judge(cl, _all_pass(cl))
    assert verdict == E2E_PASS and defects == []


def test_judge_fail_item_becomes_defect():
    cl = _cl()
    rs = _all_pass(cl)
    rs[cl[0]["id"]] = {"ok": False, "observed": "500 응답", "evidence": "curl 로그"}
    verdict, defects = judge(cl, rs)
    assert verdict == E2E_FAIL
    assert [d["id"] for d in defects] == [cl[0]["id"]]
    assert defects[0]["observed"] == "500 응답"


def test_judge_pass_without_evidence_is_defect():
    cl = _cl()
    rs = _all_pass(cl)
    rs[cl[1]["id"]] = {"ok": True, "evidence": "  "}
    verdict, defects = judge(cl, rs)
    assert verdict == E2E_FAIL
    assert defects[0]["observed"] == "pass 주장에 증거 없음"


def test_judge_missing_item_is_defect():
    cl = _cl()
    rs = _all_pass(cl)
    rs.pop(cl[-1]["id"])
    verdict, defects = judge(cl, rs)
    assert verdict == E2E_FAIL and defects[0]["observed"] == "(검사 안 됨)"


# ── ③ 분모 밖 참칭 차단 ─────────────────────────────────────────────────

def test_judge_unknown_result_id_rejected():
    cl = _cl()
    rs = _all_pass(cl)
    rs["ghost:1"] = {"ok": True, "evidence": "x"}
    with pytest.raises(WrapupError):
        judge(cl, rs)


# ── ④·⑤ 복기 진입 ──────────────────────────────────────────────────────

def test_enter_replan_passes_brief_and_defects_to_entry():
    cl = _cl()
    rs = _all_pass(cl)
    rs[cl[0]["id"]] = {"ok": False, "observed": "회귀", "evidence": "e"}
    _, defects = judge(cl, rs)
    flow, got = FakeFlow(), {}

    def entry(f, brief, ds):
        got.update(flow=f, brief=brief, defects=ds)

    brief = asyncio.run(enter_replan(flow, defects, entry=entry))
    assert got["flow"] is flow and got["defects"] == defects
    assert got["brief"] == brief and "재수립 입력" in brief
    assert defects[0]["spec"] in brief                       # 형식 보존 — 원문이 회의 입력에 남는다
    assert [e for e, _ in flow.events] == [MS_REPLAN]


def test_enter_replan_accepts_async_entry():
    called = []

    async def entry(f, brief, ds):
        called.append(len(ds))

    defects = [{"id": "condition:1", "kind": KIND_CONDITION, "spec": "s",
                "observed": "o", "evidence": "", "suspect_ms": ""}]
    asyncio.run(enter_replan(FakeFlow(), defects, entry=entry))
    assert called == [1]


def test_enter_replan_without_defects_rejected():
    with pytest.raises(WrapupError):
        asyncio.run(enter_replan(FakeFlow(), []))


# ── ⑥ 이벤트·관측 동승 ──────────────────────────────────────────────────

def test_emit_verdict_logs_contract_event_with_overhead():
    flow = FakeFlow()
    snap = overhead_snapshot(flow)
    emit_verdict(flow, E2E_PASS, [], snapshot=snap)
    assert flow.events == [(E2E_PASS, {"defects": 0, "overhead": snap})]
    assert flow.wrapup_state["verdict"] == E2E_PASS          # §9 체크포인트 동승 접점


def test_emit_verdict_rejects_unknown_verdict():
    with pytest.raises(WrapupError):
        emit_verdict(FakeFlow(), "e2e_maybe", [])


def test_overhead_snapshot_loose_coupling_defaults():
    snap = overhead_snapshot(FakeFlow())
    assert snap["meetings"] == 3 and snap["iters"] == 5      # 있는 카운터는 읽고
    assert snap["wallclock_s"] == 0 and snap["tokens_out"] == 0  # 없는 건 0 — 죽지 않는다


# ── ⑦ 플래그 ────────────────────────────────────────────────────────────

def test_pipeline_flag_default_off(monkeypatch):
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)
    assert pipeline_on() is False
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    assert pipeline_on() is True
    monkeypatch.setenv("ORGANT_PIPELINE", "other")
    assert pipeline_on() is False
