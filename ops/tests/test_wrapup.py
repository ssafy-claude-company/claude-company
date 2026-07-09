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
        self.milestones = []
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
    assert flow.events == []                                 # 커스텀 entry는 이벤트를 스스로 책임진다


def test_enter_replan_default_entry_is_s1_ms_replan():
    """entry 미지정 → S1의 실 진입점(milestone.ms_replan)이 복기 마일스톤을 연다."""
    defects = [{"id": "condition:1", "kind": KIND_CONDITION, "spec": "카운트 저장",
                "observed": "값 유실", "evidence": "curl", "suspect_ms": "MS-x"}]
    flow = FakeFlow()
    asyncio.run(enter_replan(flow, defects))
    assert len(flow.milestones) == 1                          # 복기 마일스톤 개설
    assert flow.milestones[0].origin.startswith("e2e:")
    assert [e for e, _ in flow.events] == ["ms_open", "ms_replan"]   # 이벤트는 S1 쪽에서 적재


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


# ── ⑧ 아크: 개시(분모 자동 조립) → scope 확장 → 제출 → 판정 → 복기 ──────

from system.rule.milestone import Criterion, Milestone  # noqa: E402 — S1 접점(같은 rule 층)
from system.rule.wrapup import (assemble_base_checklist, finish_e2e,  # noqa: E402
                                register_scope, rule_e2e_finish, rule_e2e_open,
                                rule_e2e_result, rule_e2e_scope, submit_result)


def _flow_at_boundary():
    """마일스톤 2개가 전부 done인(=Task 경계) flow — 조건 3개, 원문 2문장."""
    flow = FakeFlow()
    flow.task_origin = "버튼 누르면 카운트가 1씩 증가. 새로고침해도 값이 유지"
    flow.milestones = [
        Milestone(ms_id="MS-1", goal="카운터 API", status="done",
                  criteria=[Criterion("POST /count가 값을 증가시킨다", "curl -X POST 후 GET으로 확인"),
                            Criterion("값이 파일에 저장된다", "재기동 후 GET 값 유지 확인")]),
        Milestone(ms_id="MS-2", goal="프론트", status="done",
                  criteria=[Criterion("버튼 클릭이 화면 숫자를 올린다", "브라우저에서 클릭 후 표시 확인")]),
    ]
    return flow


def test_assemble_pulls_all_criteria_with_suspect_ms_and_origin():
    flow = _flow_at_boundary()
    cl = assemble_base_checklist(flow)
    conds = [it for it in cl if it["kind"] == KIND_CONDITION]
    assert len(conds) == 3 and [c["ms"] for c in conds] == ["MS-1", "MS-1", "MS-2"]
    assert len([it for it in cl if it["kind"] == KIND_ORIGIN]) == 2
    assert flow.e2e_checklist is cl and flow.e2e_results == {}


def test_scope_extends_denominator_and_dedups():
    flow = _flow_at_boundary()
    assemble_base_checklist(flow)
    added = register_scope(flow, surfaces=["GET /", "POST /count"], arcs=["기동→클릭3회→새로고침"])
    assert [it["id"] for it in added] == ["surface:1", "surface:2", "arc:1"]
    assert register_scope(flow, surfaces=["GET /"]) == []     # 중복 무시
    with pytest.raises(WrapupError):
        register_scope(FakeFlow(), surfaces=["x"])            # 개시 전 호출 = 위반


def test_submit_validates_and_tracks_remaining():
    flow = _flow_at_boundary()
    cl = assemble_base_checklist(flow)
    with pytest.raises(WrapupError):
        submit_result(flow, "ghost:9", True, evidence="x")
    note = submit_result(flow, cl[0]["id"], True, evidence="curl 200")
    assert f"1/{len(cl)} 제출됨" in note and "남은 항목" in note


def test_finish_pass_emits_event_no_replan():
    flow = _flow_at_boundary()
    cl = assemble_base_checklist(flow)
    for it in cl:
        submit_result(flow, it["id"], True, observed="OK", evidence=f"실행: {it['id']}")
    verdict, defects, new_ms = finish_e2e(flow)
    assert verdict == E2E_PASS and defects == [] and new_ms is None
    assert [e for e, _ in flow.events] == [E2E_PASS]
    assert flow.events[0][1]["overhead"]["meetings"] == 3     # §8 동승


def test_finish_fail_opens_replan_milestone_with_suspect():
    flow = _flow_at_boundary()
    cl = assemble_base_checklist(flow)
    for it in cl:
        submit_result(flow, it["id"], True, observed="OK", evidence="e")
    submit_result(flow, cl[0]["id"], False, observed="POST가 500", evidence="curl 로그")
    verdict, defects, new_ms = finish_e2e(flow)
    assert verdict == E2E_FAIL and len(defects) == 1
    assert defects[0]["suspect_ms"] == "MS-1"                 # 의심 마일스톤이 결함에 실린다
    assert new_ms is not None and new_ms.origin.startswith("e2e:")
    assert len(flow.milestones) == 3                          # 복기 마일스톤 추가
    names = [e for e, _ in flow.events]
    assert names[:1] == [E2E_FAIL] and "ms_replan" in names   # §11 사슬


def test_tool_wrappers_full_round(monkeypatch):
    """도구 표면 왕복 대본 — 개시(경계 게이트)→scope→result→finish까지 봇 대면 문구로."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    flow = _flow_at_boundary()
    flow.milestones[1].status = "open"
    assert "아직 Task 경계가 아닙니다" in rule_e2e_open(flow)  # 경계 게이트
    flow.milestones[1].status = "done"
    out = rule_e2e_open(flow)
    assert "e2e 개시" in out and "condition:1" in out
    assert "surface:1" in rule_e2e_scope(flow, {"surfaces": "GET /", "arcs": ""})
    for it in flow.e2e_checklist:
        rule_e2e_result(flow, {"item": it["id"], "ok": "pass", "observed": "OK", "evidence": "run 출력"})
    assert rule_e2e_finish(flow).startswith("e2e_pass")


def test_tool_wrappers_flag_off(monkeypatch):
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)
    assert "ORGANT_PIPELINE" in rule_e2e_open(FakeFlow())     # OFF면 안내만, 동작 없음


# ── ⑦ 플래그 ────────────────────────────────────────────────────────────

def test_pipeline_flag_default_off(monkeypatch):
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)
    assert pipeline_on() is False
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    assert pipeline_on() is True
    monkeypatch.setenv("ORGANT_PIPELINE", "other")
    assert pipeline_on() is False
