"""[S3 리허설 — 플래그 ON 관통 대본] 소형 Task의 e2e 경로 전체를 실 Flow·실 도구 표면으로 관통.

경로(계약 §6): 마일스톤 개설(결정권자, S1 표면) → iter 실증(조건 충족·done) → Task 경계
→ e2e_open(분모 자동 조립: 조건 회귀+원문) → e2e_scope(QA의 표면 제출) → e2e_result(증거 제출,
1건 fail) → e2e_finish(**e2e_fail** → ms_replan 복기 마일스톤 자동 개설) → 결함 해소 →
재관통 → **e2e_pass**. §11 이벤트 사슬과 §8 오버헤드 동승(관문 집계)까지 한 대본에서 검증.

라이브와의 차이 = 봇의 검사 실행(run·브라우저)이 대본 값으로 대체된 것뿐 — 도구 표면·규칙 경로는
실물 그대로(make_guide_tools 등록분을 handler로 구동). 실 QA 봇 관통은 라이브 스모크(§13)의 몫.
"""
import asyncio

import pytest

from system.rule.milestone import iter_verify, next_milestone, wrapup_done
from system.rule.wrapup import E2E_FAIL, E2E_PASS, tallying_logger
from test_sys import FakeGuide, _flow, _tools


@pytest.fixture
def onflag(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")


def _txt(res) -> str:
    """도구 반환(_ok 봉투)에서 텍스트만."""
    return str(res)


def _drive(tools, name, args=None):
    return _txt(asyncio.run(tools[name].handler(args or {})))


def test_e2e_full_rehearsal_fail_replan_then_pass(onflag):
    g = FakeGuide()
    f = _flow(g)
    f.origin_request = "버튼 누르면 카운트가 1씩 증가. 새로고침해도 값이 유지"
    events = []
    f.log = tallying_logger(f, lambda ev, **kw: events.append((ev, kw)))   # sys_core 배선과 동형

    # ① 결정권자(리더 자리 승계)가 마일스톤 2개 확정 — S1 표면 그대로
    lead = _tools(f, 11, "leader")
    assert "개설" in _drive(lead, "set_milestone", {
        "goal": "카운터 API",
        "criteria": "POST /count가 값을 증가시킨다 | curl POST 후 GET으로 확인\n"
                    "값이 파일에 저장된다 | 재기동 후 GET 값 유지 확인"})
    assert "개설" in _drive(lead, "set_milestone", {
        # [#4 게이트 강화 여파] '브라우저 클릭 확인'(빈 서술)은 이제 등록 거부 — 실행 가능형으로.
        "goal": "프론트", "criteria": "버튼 클릭이 화면 숫자를 올린다 | playwright로 클릭 후 숫자 1 증가 확인"})
    assert len(f.milestones) == 2

    # ② iter 실증(증거 있는 충족) → 잔여 정리 → done — Task 경계 도달
    for ms in list(f.milestones):
        passed, _ = iter_verify(f, ms, [{"desc": c.desc, "passed": True, "evidence": "run 출력 OK"}
                                        for c in ms.criteria])
        assert passed and wrapup_done(f, ms) == "done"

    # ③ QA(멤버) 표면으로 e2e 관통 — 개시(분모: 조건 3 + 원문 2)
    qa = _tools(f, 12, "member")
    out = _drive(qa, "e2e_open")
    assert "e2e 개시" in out and "condition:1" in out and "origin:1" in out
    assert "surface:1" in _drive(qa, "e2e_scope",
                                 {"surfaces": "GET /\nPOST /count", "arcs": "기동→클릭 3회→새로고침 값 유지"})

    # ④ 전 항목 제출 — surface:2 하나만 실패(관측·증거 동봉)
    for it in f.e2e_checklist:
        fail = it["id"] == "surface:2"
        _drive(qa, "e2e_result", {"item": it["id"], "ok": "fail" if fail else "pass",
                                  "observed": "POST가 500 응답" if fail else "OK",
                                  "evidence": "curl -X POST 출력 로그"})

    # ⑤ 판정 → e2e_fail + 복기 마일스톤 자동 개설(§6 복기 재진입)
    fin = _drive(qa, "e2e_finish")
    assert "e2e_fail" in fin and "복기 마일스톤" in fin and "POST가 500" in fin
    assert len(f.milestones) == 3 and f.milestones[-1].origin.startswith("e2e:")
    names = [e for e, _ in events]
    assert names.count("ms_open") == 3 and "ms_replan" in names and E2E_FAIL in names
    # §8 오버헤드가 e2e_fail payload에 동승(관문 집계: iter 2회·wall-clock — 이벤트 이름 추가 없음)
    fail_kw = next(kw for ev, kw in events if ev == E2E_FAIL)
    assert fail_kw["overhead"]["iters"] == 2 and fail_kw["overhead"]["wallclock_s"] >= 0

    # ⑥ 복기 라운드 — 결함 해소 실증 → done → 재관통 → e2e_pass
    rm = next_milestone(f)
    passed, _ = iter_verify(f, rm, [{"desc": c.desc, "passed": True, "evidence": "재실행 — 미재현 확인"}
                                    for c in rm.criteria])
    assert passed and wrapup_done(f, rm) == "done"
    assert "e2e 개시" in _drive(qa, "e2e_open")                      # 분모 재조립(복기 조건 포함)
    _drive(qa, "e2e_scope", {"surfaces": "GET /\nPOST /count", "arcs": "기동→클릭 3회→새로고침 값 유지"})
    for it in f.e2e_checklist:
        _drive(qa, "e2e_result", {"item": it["id"], "ok": "pass",
                                  "observed": "OK", "evidence": "재검사 출력"})
    assert "e2e_pass" in _drive(qa, "e2e_finish")
    assert [e for e, _ in events].count(E2E_PASS) == 1


def test_claim_kick_target_작업단계_선점킥_규칙(onflag):
    """[ch79/P-032 라이브 실측 수리 — 2026-07-19] 회의가 백로그를 등록하고 작업 단계로 전이했는데
    아무도 선점하지 않던 교착: SYS가 깨울 대상을 규칙으로 고른다 — 제출자에게 백로그당 1회,
    in_progress가 생기면 침묵(순차 1활성), 킥을 씹으면 다음 open으로 한 번씩만 확대."""
    from system.rule.backlog import BacklogRelay, backlog_scope_key
    from system.rule.milestone import SubTask, claim_kick_target
    g = FakeGuide()
    f = _flow(g)
    lead = _tools(f, 11, "leader")
    _drive(lead, "set_milestone", {"goal": "게임", "criteria": "판정 정확 | pytest 50회 확인"})
    ms = f.milestones[0]
    st = SubTask(st_id=f"{ms.ms_id}/ST-1", goal="메커닉", criteria=[])
    ms.subtasks.append(st)
    r = BacklogRelay(st.st_id)
    f.backlog_relays = {st.st_id: r}
    b1 = r.submit(12, "메커닉 정의서 작성", force=True)
    b2 = r.submit(13, "저장 스키마 확정", force=True)

    who, b, st_id = claim_kick_target(f)
    assert (who, b.backlog_id, st_id) == (12, b1.backlog_id, st.st_id)   # 첫 open의 제출자
    f._claim_kicked = {backlog_scope_key(st.st_id, b1.backlog_id)}        # 킥했는데 씹힘 → 다음 open 1회
    who2, b_2, _ = claim_kick_target(f)
    assert (who2, b_2.backlog_id) == (13, b2.backlog_id)
    b1.status = "in_progress"                                            # 누가 집으면 침묵
    assert claim_kick_target(f) is None
    b1.status = "done"
    f._claim_kicked = {backlog_scope_key(st.st_id, b1.backlog_id),
                       backlog_scope_key(st.st_id, b2.backlog_id)}        # 전부 킥 소진 → 침묵
    assert claim_kick_target(f) is None


def test_claim_kick_key는_SubTask범위라_다른단계_B1을_막지않음(onflag):
    """B1은 단계마다 반복된다. 앞 단계 B1 킥 기록이 다음 단계 B1 진입을 막지 않는다."""
    from system.rule.backlog import BacklogRelay, backlog_scope_key
    from system.rule.milestone import SubTask, claim_kick_target
    g = FakeGuide()
    f = _flow(g)
    lead = _tools(f, 11, "leader")
    _drive(lead, "set_milestone", {"goal": "게임", "criteria": "판정 정확 | pytest 50회 확인"})
    ms = f.milestones[0]
    st1 = SubTask(st_id=f"{ms.ms_id}/ST-1", goal="화면", criteria=[])
    st2 = SubTask(st_id=f"{ms.ms_id}/ST-2", goal="API", criteria=[])
    ms.subtasks.extend([st1, st2])
    r1, r2 = BacklogRelay(st1.st_id), BacklogRelay(st2.st_id)
    b11 = r1.submit(12, "화면 구현", force=True)
    b21 = r2.submit(13, "API 구현", force=True)                 # 이 단계에서도 지역 ID는 B1
    b11.status = "done"
    f.backlog_relays = {st1.st_id: r1, st2.st_id: r2}
    f._claim_kicked = {backlog_scope_key(st1.st_id, b11.backlog_id)}

    who, b, st_id = claim_kick_target(f)
    assert (who, b.backlog_id, st_id) == (13, "B1", st2.st_id)


def test_ledger_signature_장부전진만_센다(onflag):
    """[진전 기반 재픽(2026-07-20)] 재픽 판정 = 장부 서명 변화. 발언·도구 소음은 서명을 안 바꾸고,
    목표 확정·단위 등록·백로그 종결 같은 장부 전진만 바꾼다 — '상한 N회' 수치 판정의 대체."""
    from system.rule.backlog import BacklogRelay
    from system.rule.milestone import SubTask, ledger_signature
    g = FakeGuide()
    f = _flow(g)
    s0 = ledger_signature(f)
    assert ledger_signature(f) == s0                        # 아무 일 없음 = 동일(발언은 장부 아님)
    lead = _tools(f, 11, "leader")
    _drive(lead, "set_milestone", {"goal": "게임", "criteria": "판정 정확 | pytest 50회 확인"})
    s1 = ledger_signature(f)
    assert s1 != s0                                         # 주기 등록 = 전진
    ms = f.milestones[0]
    st = SubTask(st_id=f"{ms.ms_id}/ST-1", goal="메커닉", criteria=[])
    ms.subtasks.append(st)
    r = BacklogRelay(st.st_id)
    f.backlog_relays = {st.st_id: r}
    b = r.submit(12, "정의서", force=True)
    s2 = ledger_signature(f)
    assert s2 != s1                                         # 단위·백로그 등록 = 전진
    b.status = "done"
    assert ledger_signature(f) != s2                        # 백로그 종결 = 전진


def test_rehearsal_boundary_gate_blocks_early_open(onflag):
    """미완 마일스톤이 있으면 e2e_open이 거부 — Task 경계 규약이 도구 표면에서도 산다."""
    g = FakeGuide()
    f = _flow(g)
    lead = _tools(f, 11, "leader")
    _drive(lead, "set_milestone", {"goal": "m", "criteria": "c | curl 확인"})
    assert "아직 Task 경계가 아닙니다" in _drive(_tools(f, 12, "member"), "e2e_open")


def test_rehearsal_flag_off_tools_absent():
    """플래그 OFF면 e2e 도구가 등록 자체가 안 된다 — 라이브 불변의 도구면 증명."""
    import os
    assert os.environ.get("ORGANT_PIPELINE") != "milestone"
    g = FakeGuide()
    f = _flow(g)
    assert "e2e_open" not in _tools(f, 12, "member")
