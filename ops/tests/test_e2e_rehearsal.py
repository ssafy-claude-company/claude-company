"""[S3 리허설 — 플래그 ON 관통 대본] 소형 Task의 e2e 경로 전체를 실 Flow·실 도구 표면으로 관통.

경로(계약 §6): 마일스톤 개설(결정권자, S1 표면) → iter 실증(조건 충족·done) → Task 경계
→ e2e_open(분모 자동 조립: 조건 회귀+원문) → e2e_scope(QA의 표면 제출) → e2e_result(증거 제출,
1건 fail) → e2e_finish(**e2e_fail** → ms_replan 복기 마일스톤 자동 개설) → 결함 해소 →
재관통 → **e2e_pass**. §11 이벤트 사슬과 §8 오버헤드 동승(관문 집계)까지 한 대본에서 검증.

라이브와의 차이 = 봇의 검사 실행(run·브라우저)이 대본 값으로 대체된 것뿐 — 도구 표면·규칙 경로는
실물 그대로(make_guide_tools 등록분을 handler로 구동). 실 QA 봇 관통은 라이브 스모크(§13)의 몫.
"""
import asyncio
import re

import pytest

from system.rule.milestone import (
    iter_verify, next_milestone, open_subtask, wrapup_done,
)
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


def _run_receipt(tools, item) -> str:
    out = _drive(
        tools, "run",
        {"command": item["verifier_command"],
         "evidence_for": item["id"]},
    )
    match = re.search(r"\[SYS run receipt\]\s+(run-[0-9a-f]+)", out)
    assert match, out
    return match.group(1)


def test_e2e_full_rehearsal_fail_replan_then_pass(onflag, tmp_path):
    g = FakeGuide()
    f = _flow(g)
    f.workspace = str(tmp_path)
    f.origin_request = "버튼 누르면 카운트가 1씩 증가. 새로고침해도 값이 유지"
    for name in (
        "api_increment_check.py", "persist_check.py", "browser_check.py",
        "http_get_check.py", "api_post_check.py", "browser_arc_check.py",
    ):
        (tmp_path / name).write_text("print('verified')\n", encoding="utf-8")
    # [주기는 배달까지다(2026-07-31)] 웹 산출물 주기는 사람이 열 진입이 있어야 닫힌다.
    (tmp_path / "index.html").write_text("<h1>rehearsal</h1>\n", encoding="utf-8")
    events = []
    f.log = tallying_logger(f, lambda ev, **kw: events.append((ev, kw)))   # sys_core 배선과 동형

    # ① 결정권자(리더 자리 승계)가 마일스톤 2개 확정 — S1 표면 그대로
    lead = _tools(f, 11, "leader")
    assert "개설" in _drive(lead, "set_milestone", {
        "goal": "카운터 API",
        "criteria": "POST /count가 값을 증가시킨다 | python3 api_increment_check.py\n"
                    "값이 파일에 저장된다 | python3 persist_check.py"})
    assert "개설" in _drive(lead, "set_milestone", {
        "goal": "프론트",
        "criteria": "버튼 클릭이 화면 숫자를 올린다 | python3 browser_check.py"})
    assert len(f.milestones) == 2

    # ② iter 실증(증거 있는 충족) → 잔여 정리 → done — Task 경계 도달
    from system.rule.backlog import Backlog, relay_for
    for ms in list(f.milestones):
        st = open_subtask(f, ms, f"{ms.goal} 구현", [])
        relay_for(f, st)._pool["B1"] = Backlog(
            "B1", f"{ms.goal} 구현", 12, status="done", assignee=12)
        st.status = "done"
        passed, _ = iter_verify(f, ms, [{"desc": c.desc, "passed": True, "evidence": "run 출력 OK"}
                                        for c in ms.criteria])
        assert passed and wrapup_done(f, ms) == "done"

    # ③ QA(멤버) 표면으로 e2e 관통 — 개시(분모: 조건 3 + 원문 2)
    qa = _tools(f, 12, "member")
    out = _drive(qa, "e2e_open")
    assert "e2e 개시" in out and "condition:1" in out
    assert "origin:1" not in out and "사용자 원문 컨텍스트" in out
    nonce = f._e2e_receipt_nonce
    checklist = list(f.e2e_checklist)
    reopened = _drive(qa, "e2e_open")
    assert "e2e 진행 중" in reopened
    assert f._e2e_receipt_nonce == nonce and f.e2e_checklist == checklist
    assert "surface:1" in _drive(qa, "e2e_scope",
                                 {"surfaces":
                                      "GET / || python3 http_get_check.py\n"
                                      "POST /count || python3 api_post_check.py",
                                  "arcs":
                                      "기동→클릭 3회→새로고침 값 유지 || "
                                      "python3 browser_arc_check.py"})

    # ④ 전 항목 제출 — surface:2 하나만 실패(관측·증거 동봉)
    for index, it in enumerate(f.e2e_checklist):
        fail = it["id"] == "surface:2"
        args = {"item": it["id"], "ok": "fail" if fail else "pass",
                "observed": "POST가 500 응답" if fail else "OK",
                "evidence": "curl -X POST 출력 로그"}
        if not fail:
            args["receipt"] = _run_receipt(qa, it)
        _drive(qa, "e2e_result", args)
        if index == 0:
            partial_results = dict(f.e2e_results)
            assert "e2e 진행 중" in _drive(qa, "e2e_open")
            assert f._e2e_receipt_nonce == nonce
            assert f.e2e_results == partial_results

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
    repair_st = open_subtask(f, rm, "POST 결함 수정", [])
    relay_for(f, repair_st)._pool["B1"] = Backlog(
        "B1", "POST 결함 수정", 12, status="done", assignee=12)
    repair_st.status = "done"
    passed, _ = iter_verify(f, rm, [{"desc": c.desc, "passed": True, "evidence": "재실행 — 미재현 확인"}
                                    for c in rm.criteria])
    assert passed and wrapup_done(f, rm) == "done"
    assert "e2e 개시" in _drive(qa, "e2e_open")                      # 분모 재조립(복기 조건 포함)
    _drive(qa, "e2e_scope", {
        "surfaces": "GET / || python3 http_get_check.py\n"
                    "POST /count || python3 api_post_check.py",
        "arcs": "기동→클릭 3회→새로고침 값 유지 || python3 browser_arc_check.py",
    })
    for it in f.e2e_checklist:
        _drive(qa, "e2e_result", {"item": it["id"], "ok": "pass",
                                  "observed": "OK", "evidence": "재검사 출력",
                                  "receipt": _run_receipt(qa, it)})
    assert "e2e_pass" in _drive(qa, "e2e_finish")
    assert [e for e, _ in events].count(E2E_PASS) == 1
    final_checklist = list(f.e2e_checklist)
    final_results = dict(f.e2e_results)
    final_nonce = f._e2e_receipt_nonce
    assert "e2e 이미 판정됨 — e2e_pass" in _drive(qa, "e2e_open")
    assert f.e2e_checklist == final_checklist
    assert f.e2e_results == final_results
    assert f._e2e_receipt_nonce == final_nonce
    assert f.wrapup_state["verdict"] == E2E_PASS


def test_claim_kick_target_작업단계_선점킥_규칙(onflag):
    """[ch79/P-032 라이브 실측 수리 — 2026-07-19] 회의가 백로그를 등록하고 작업 단계로 전이했는데
    아무도 선점하지 않던 교착: SYS가 깨울 대상을 규칙으로 고른다 — 제출자에게 백로그당 1회,
    킥을 씹으면 다음 open으로 한 번씩만 확대.
    (2026-07-31: 'in_progress가 생기면 침묵'은 폐기 — 여력이 남으면 다음 사람도 세운다.)"""
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
    b1.status = "in_progress"          # 누가 집어도 여력이 있으면 다음 사람을 세운다(2026-07-31)
    assert claim_kick_target(f)[1].backlog_id == b2.backlog_id
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


def test_e2e_통과_뒤_마감까지_관통한다(onflag, tmp_path):
    """[깨끗한 판 예행(2026-07-27)] 라이브 한 번을 낭비할 수 없어, e2e 통과 **다음**을 대본으로 먼저
    관통한다 — 마감 관문 전체를 실 도구 표면으로 통과시켜 Task가 실제로 닫히는지 본다.
    U-067에서는 여기서 19번 멎었다. 픽스처의 관문 우회를 **끄고** 돌린다(우회한 채 통과는 예행이 아님).
    """
    from system.rule.backlog import Backlog, relay_for
    from system.rule.milestone import iter_verify, open_subtask, wrapup_done

    g = FakeGuide()
    f = _flow(g)
    f.workspace = str(tmp_path)
    f.origin_request = "버튼 누르면 카운트가 1씩 증가하는 웹페이지"
    # 마감 관문을 실제로 통과시키는 게 이 예행의 목적 — 기본 우회를 끈다.
    for _bypass in ("acceptance_checked", "percept_checked", "existence_checked", "gap_checked"):
        setattr(f, _bypass, False)
    (tmp_path / "browser_check.py").write_text("print('verified')\n", encoding="utf-8")
    (tmp_path / "index.html").write_text("<h1>counter</h1>\n", encoding="utf-8")
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")     # 실재 자산(지각·시각 축)
    events = []
    f.log = tallying_logger(f, lambda ev, **kw: events.append((ev, kw)))

    lead = _tools(f, 11, "leader")
    _drive(lead, "create_task", {"title": "카운터 웹페이지", "members": "12"})
    assert f.current is not None, "Task가 열리지 않았다(예행 전제 실패)"
    # 팀 판의 GOAL·수용 계약도 회의 표결 몫이다(개인 set_goal 거부) — 회의 등록기가 쓰는 값을
    # 그대로 세운다. 이 예행이 보려는 것은 회의 기계가 아니라 마감 관문이다.
    f.current.status.goal = "버튼을 누르면 화면 숫자가 1씩 오르는 웹페이지 1종"
    f.current.acceptance = "- 조건: 버튼 클릭이 화면 숫자를 올린다 | 실증: python3 browser_check.py"

    # 팀 판의 마일스톤 확정은 회의 표결 몫이라(개인 등록 거부) 규칙 API로 같은 결과를 만든다 —
    # 이 예행이 보려는 것은 회의 기계가 아니라 **마감 관문**이다.
    from system.rule.milestone import open_milestone
    _ms0 = open_milestone(
        f, "카운터 웹페이지",
        [{"desc": "버튼 클릭이 화면 숫자를 올린다", "verify": "python3 browser_check.py"}])
    assert not isinstance(_ms0, str), f"마일스톤 개설 실패: {_ms0}"
    ms = f.milestones[0]
    st = open_subtask(f, ms, "화면 구현", [])
    relay_for(f, st)._pool["B1"] = Backlog("B1", "화면 구현", 12, status="done", assignee=12)
    st.status = "done"
    # GOAL 잠금 조건(수용 계약)이 최종 주기로 승격되므로 그것까지 함께 실증한다 — 라이브와 같은 모양.
    from system.rule.milestone import promote_final_locked_criteria
    promote_final_locked_criteria(f, checkpoint=False)
    # 잠금 조건은 SYS 영수증만 인정한다(라이브 설계) — 자동 검증이 봉인하는 필드를 그대로 싣는다.
    from system.rule.evidence import verifier_command_hash, verifier_spec_hash
    from system.rule.milestone import workspace_artifact_stamp, write_revision
    _ep, _st = write_revision(f), workspace_artifact_stamp(f)
    _rows = []
    for c in ms.criteria:
        r = {"desc": c.desc, "passed": True, "evidence": "exit=0 `python3 browser_check.py`"}
        if getattr(c, "release_lock", False):
            r.update({"_sys_run_receipt": r["evidence"], "_sys_run_receipt_id": "auto-lock-test",
                      "_verified_command": "python3 browser_check.py",
                      "_verified_command_hash": verifier_command_hash("python3 browser_check.py"),
                      "_verified_spec_hash": verifier_spec_hash(c.desc, c.verify),
                      "_verified_write_epoch": _ep, "_verified_artifact_stamp": _st})
        _rows.append(r)
    passed, _note = iter_verify(f, ms, _rows)
    assert passed, f"주기 충족 실패 — {str(_note)[:300]} / 조건={[(c.desc[:20], c.passed) for c in ms.criteria]}"
    assert wrapup_done(f, ms) == "done"

    qa = _tools(f, 12, "member")
    assert "e2e 개시" in _drive(qa, "e2e_open")
    _drive(qa, "e2e_scope", {"surfaces": "GET / || python3 browser_check.py",
                             "arcs": "기동→클릭→숫자 증가 || python3 browser_check.py"})
    for it in f.e2e_checklist:
        _drive(qa, "e2e_result", {"item": it["id"], "ok": "pass", "observed": "OK",
                                  "evidence": "검사 출력", "receipt": _run_receipt(qa, it)})
    assert "e2e_pass" in _drive(qa, "e2e_finish")

    # ── 여기부터가 이번 예행의 목적: 마감이 실제로 닫히는가 ──
    assert f.current.verified is True, "전수 검증이 실행 사실로 안 남는다"
    assert f.current.owner_incomplete is False, "옛 미완 표식이 안 풀린다"

    out = ""
    for _ in range(6):                       # 보류형 관문은 '보고 다시 호출'이 정상 경로
        # [최악 조건] 봇이 특별한 회계를 **안 쓴다**고 보고 민맹 result만 보낸다 — 실판에서 관측된
        # 모습이 정확히 이것이다(관문이 요구하는 헤더를 아무도 안 적었다). 구조가 남긴 증거
        # (e2e 영수증·백로그 장부)만으로 닫혀야 이번 라이브가 한 번에 끝난다.
        out = _txt(_drive(qa, "complete_task", {
            "result": "완료: 카운터 웹페이지 — 전수 검증 0결함",
            # 시각 축은 마감 구동부가 사실대로 채워 넣는 인자다(스크린샷 영수증이 없으면 미검증으로
            # 정직히 명시) — 라이브 마감도 이 경로로 닫혔다. 나머지는 최악 그대로 둔다.
            "visual_evidence": "[시각 미검증: 자동 검증(헤드리스)만 수행 — 사람 시각 확인 필요]",
        }))
        if f.current is None:
            break
    assert f.current is None, f"마감이 끝내 안 닫혔다 — 마지막 사유: {out[:500]}"


def test_다단계_사다리_2주기_관통후_마감(onflag, tmp_path):
    """[사다리 예행(2026-07-27)] 오늘 되살린 다단계 로드맵은 **한 번도 끝까지 안 돌아본 경로**다.
    2주기 로드맵을 실제로 관통시켜 확인한다: ①1주기 완주 뒤 계획 단계가 다시 열리는가 ②중간
    주기엔 GOAL 잠금이 안 붙고 최종 주기에만 붙는가 ③로드맵이 남았으면 e2e가 안 열리는가
    ④소진 뒤 e2e가 열려 마감까지 가는가 ⑤단계 재개설 상한이 정상 사다리를 죽이지 않는가."""
    from system.rule.backlog import Backlog, relay_for
    from system.rule.evidence import verifier_command_hash, verifier_spec_hash
    from system.rule.milestone import (
        iter_verify, meeting_stage, open_milestone, open_subtask,
        promote_final_locked_criteria, roadmap_done_count, workspace_artifact_stamp,
        write_revision, wrapup_done,
    )
    from system.rule.wrapup import rule_e2e_open

    g = FakeGuide()
    f = _flow(g)
    f.workspace = str(tmp_path)
    f.origin_request = "카운터 웹앱"
    for _b in ("acceptance_checked", "percept_checked", "existence_checked", "gap_checked"):
        setattr(f, _b, False)
    (tmp_path / "verify_ui.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "index.html").write_text("<h1>c</h1>\n", encoding="utf-8")
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    f.log = tallying_logger(f, lambda ev, **kw: None)

    lead = _tools(f, 11, "leader")
    _drive(lead, "create_task", {"title": "카운터", "members": "12"})
    f.current.status.goal = "버튼으로 숫자를 올리는 웹앱"
    f.current.acceptance = "- 조건: 버튼이 숫자를 올린다 | 실증: python3 verify_ui.py"
    f.roadmap = ["최소버전", "확장"]                     # 사다리 2칸

    def _run_cycle(goal, final):
        ms = open_milestone(f, goal, [{"desc": f"{goal} 동작", "verify": "python3 verify_ui.py"}])
        assert not isinstance(ms, str), ms
        st = open_subtask(f, ms, f"{goal} 구현", [])
        relay_for(f, st)._pool["B1"] = Backlog("B1", "구현", 12, status="done", assignee=12)
        st.status = "done"
        promote_final_locked_criteria(f, checkpoint=False)
        locked = [c for c in ms.criteria if getattr(c, "release_lock", False)]
        assert bool(locked) is final, \
            f"{goal}: 잠금 조건이 {'붙어야' if final else '안 붙어야'} 하는데 {len(locked)}건"
        ep, stmp = write_revision(f), workspace_artifact_stamp(f)
        rows = []
        for c in ms.criteria:
            r = {"desc": c.desc, "passed": True, "evidence": "exit=0 `python3 verify_ui.py`"}
            if getattr(c, "release_lock", False):
                r.update({"_sys_run_receipt": r["evidence"], "_sys_run_receipt_id": "auto-lock-t",
                          "_verified_command": "python3 verify_ui.py",
                          "_verified_command_hash": verifier_command_hash("python3 verify_ui.py"),
                          "_verified_spec_hash": verifier_spec_hash(c.desc, c.verify),
                          "_verified_write_epoch": ep, "_verified_artifact_stamp": stmp})
            rows.append(r)
        ok, note = iter_verify(f, ms, rows)
        assert ok, f"{goal} 주기 미충족 — {note}"
        assert wrapup_done(f, ms) == "done"
        return ms

    # ① 1주기(중간) — 잠금 없음, 완주 뒤 계획 단계가 다시 열린다
    _run_cycle("최소버전", final=False)
    assert roadmap_done_count(f) == 1, "로드맵 완주 수가 안 는다"
    assert meeting_stage(f) == "milestone", "1주기 완주 뒤 다음 계획 회의가 안 열린다(사다리 끊김)"
    # ③ 로드맵이 남았으면 e2e는 아직
    assert "로드맵" in _txt(rule_e2e_open(f)), "로드맵이 남았는데 e2e가 열린다(판정 낭비)"

    # ② 2주기(최종) — 잠금 조건이 여기서만 붙는다
    _run_cycle("확장", final=True)
    assert roadmap_done_count(f) == 2
    assert meeting_stage(f) is None, "로드맵 소진 뒤에도 계획 회의를 연다"

    # ④ 소진 → e2e → 마감
    qa = _tools(f, 12, "member")
    assert "e2e 개시" in _drive(qa, "e2e_open")
    _drive(qa, "e2e_scope", {"surfaces": "GET / || python3 verify_ui.py",
                             "arcs": "기동→클릭 || python3 verify_ui.py"})
    for it in f.e2e_checklist:
        _drive(qa, "e2e_result", {"item": it["id"], "ok": "pass", "observed": "OK",
                                  "evidence": "검사 출력", "receipt": _run_receipt(qa, it)})
    assert "e2e_pass" in _drive(qa, "e2e_finish")

    out = ""
    for _ in range(6):
        out = _txt(_drive(qa, "complete_task", {
            "result": "완료: 카운터 웹앱 — 전수 검증 0결함",
            "visual_evidence": "[시각 미검증: 자동 검증만 수행]"}))
        if f.current is None:
            break
    assert f.current is None, f"사다리 판이 마감까지 못 갔다 — {out[:400]}"
