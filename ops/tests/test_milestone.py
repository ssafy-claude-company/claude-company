"""[S1 — 마일스톤 파이프라인 계약 테스트] PIPELINE_REWORK_2026-07-09 §1·§2·§6·§9·§12.
핵심 계약: 조건이 주기를 닫는다 / 소망형 조건은 등록 거부 / 증거 없는 충족 불인정 /
복기(ms_replan)는 결함을 새 주기로 / 상태는 직렬화 왕복 무손실(최대 저장) / 플래그 없으면 OFF."""
import os

from system.flow import Flow
from system.rule.milestone import (
    Milestone, gate_criteria, iter_verify, ms_from_dict, ms_replan, ms_to_dict,
    next_milestone, open_milestone, open_subtask, pipeline_on, wrapup_done,
)


class _G:                      # 최소 가이드 목(Flow 생성용)
    def __getattr__(self, k):
        return None


def _flow():
    f = Flow(_G(), 0, 1, 11, {11: "리더", 12: "백엔드"})
    f.log = None
    return f


def test_플래그_미설정이면_파이프라인_OFF():
    os.environ.pop("ORGANT_PIPELINE", None)
    assert pipeline_on() is False              # 안전값 — 라이브 동작 불변(계약 §12)
    assert pipeline_on("milestone") is True
    assert pipeline_on("MILESTONE ") is True   # 관용 파싱
    assert pipeline_on("on") is False          # 지정 값만


def test_등록게이트_소망형과_실증절차_없는_조건_거부():
    assert gate_criteria([]) is not None                                   # 빈 조건
    assert gate_criteria([{"desc": "잘 동작해야 함", "verify": ""}]) is not None   # 소망형+절차 없음
    assert gate_criteria([{"desc": "카운터 증가", "verify": ""}]) is not None      # 절차 없음
    assert "verify" in gate_criteria([{"desc": "카운터 증가", "verify": ""}])
    ok = [{"desc": "버튼 클릭 시 카운트 1 증가", "verify": "curl -s localhost:3000/count 전후 비교"}]
    assert gate_criteria(ok) is None
    dup = ok + [{"desc": "버튼 클릭 시 카운트 1 증가", "verify": "wget localhost:3000 재확인"}]
    assert "중복" in gate_criteria(dup)


def test_iter검증_증거없는_충족은_불인정_조건충족이_주기를_닫는다():
    f = _flow()
    ms = open_milestone(f, "카운터 앱 1주기", [
        {"desc": "카운트 API 동작", "verify": "curl -s localhost:3000/count"},
        {"desc": "버튼 UI 표시", "verify": "playwright 페이지 로드·버튼 존재 확인"},
    ])
    assert isinstance(ms, Milestone) and ms.status == "open"
    # 증거 없는 passed → 불인정(허위 충족 차단)
    ok, note = iter_verify(f, ms, [{"desc": "카운트 API 동작", "passed": True, "evidence": ""}])
    assert ok is False and "미충족" in note and ms.iter_n == 1
    # 전 조건 실증 → wrapup(잔여 정리 모드) — 사람이 아니라 조건이 닫는다
    ok, note = iter_verify(f, ms, [
        {"desc": "카운트 API 동작", "passed": True, "evidence": "HTTP 200 {count:1}"},
        {"desc": "버튼 UI 표시", "passed": True, "evidence": "playwright: button visible"},
    ])
    assert ok is True and ms.status == "wrapup"
    # wrapup 전 건너뛰기 차단 → 정리 완료 선언으로 done
    st_err = wrapup_done(f, Milestone(ms_id="x", goal="", criteria=[]))
    assert "불가" in st_err
    assert wrapup_done(f, ms) == "done" and ms.status == "done"
    assert next_milestone(f) is None           # 진행 대상 없음 = Task의 마일스톤 소진


def test_서브태스크_주기중_추가와_동일_게이트():
    f = _flow()
    ms = open_milestone(f, "M1", [{"desc": "빌드 통과", "verify": "npm run build 종료코드 0"}])
    st = open_subtask(f, ms, "프론트 뼈대", [{"desc": "index 로드", "verify": "curl -s localhost:3000/"}])
    assert st.st_id.startswith(ms.ms_id) and ms.subtasks == [st]
    bad = open_subtask(f, ms, "x", [{"desc": "완벽해야 함", "verify": ""}])
    assert isinstance(bad, str)                # 같은 등록 게이트가 SubTask에도


def test_복기_ms_replan은_결함을_새_주기로():
    f = _flow()
    ms1 = open_milestone(f, "M1", [{"desc": "a", "verify": "run a"}])
    ms1.status = "done"
    ms2 = ms_replan(f, ["키보드 포커스가 j/k에서 갇힘", "저장 후 404"])
    assert isinstance(ms2, Milestone) and ms2.origin.startswith("e2e:")
    assert len(ms2.criteria) == 2 and all(c.verify for c in ms2.criteria)   # 조건 초안도 실증 형태
    assert next_milestone(f) is ms2            # 복기 주기가 다음 진행 대상
    assert ms_replan(f, []) is None            # 결함 없으면 무동작


def test_직렬화_왕복_무손실_최대저장():
    f = _flow()
    ms = open_milestone(f, "M1", [{"desc": "a", "verify": "run a"}], origin="사용자 원문")
    st = open_subtask(f, ms, "s", [{"desc": "b", "verify": "run b"}])
    st.participants.add(12)
    st.backlog_ids.append("BL-1")
    iter_verify(f, ms, [{"desc": "a", "passed": True, "evidence": "ok"}])
    d = ms_to_dict(ms)
    ms2 = ms_from_dict(d)
    assert ms_to_dict(ms2) == d                # 왕복 무손실(계약 §9 — 재시작 후 중간 재개의 토대)
    assert ms2.status == "wrapup" and ms2.subtasks[0].participants == {12}


def test_flow에_milestones_필드가_기본_빈값():
    f = _flow()
    assert f.milestones == []                  # 플래그 OFF 라이브에서 항상 빈 리스트(불변 보증)


def test_도구는_플래그_ON에서만_등록(monkeypatch):
    """[§12 이중수용] OFF 라이브엔 set_milestone/set_subtask 도구 자체가 없다 — 표면 불변."""
    from system.guide_tools import make_guide_tools
    f = _flow()
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)
    names_off = {t.name for t in make_guide_tools(f, 11, "leader")}
    assert "set_milestone" not in names_off and "set_subtask" not in names_off
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    names_on = {t.name for t in make_guide_tools(f, 11, "leader")}
    assert {"set_milestone", "set_subtask"} <= names_on


def test_확정은_결정권자만_파싱과_게이트_경유(monkeypatch):
    """[§1·§4] 마일스톤 확정 도구 — 결정권자 아닌 봇은 거부, '조건 | 실증절차' 줄 파싱, 게이트 경유."""
    from system.rule.milestone import rule_set_milestone, rule_set_subtask
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()          # leader=11 (_flow의 Flow 생성 인자)
    out = rule_set_milestone(f, 12, {"goal": "M1", "criteria": "a | run a"})
    assert "결정권자" in out and not f.milestones            # 비결정권자 거부
    out = rule_set_milestone(f, 11, {"goal": "M1", "criteria": "- 카운트 API | curl 확인\n버튼 UI | playwright 확인"})
    assert "개설" in out and len(f.milestones[0].criteria) == 2   # 줄 파싱(불릿 관용)
    out = rule_set_milestone(f, 11, {"goal": "M2", "criteria": "잘 동작해야 함"})
    assert "거부" in out and len(f.milestones) == 1          # 소망형 — 등록 게이트가 막음
    out = rule_set_subtask(f, 12, {"goal": "프론트 뼈대", "criteria": "index 로드 | curl -s /"})
    assert "추가" in out and f.milestones[0].subtasks        # SubTask 추가는 현장 누구나(자발 참여)


def test_iter_제출_도구_전_사이클(monkeypatch):
    """[배치4 — 드라이브] report_iter: 결과 파싱 → 검증 → wrapup 전이 → done 마감 안내까지
    봇 주도 전 사이클이 도구만으로 돈다(마감은 사람이 아니라 조건)."""
    from system.rule.milestone import parse_iter_results, rule_report_iter
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    assert "주기가 없습니다" in rule_report_iter(f, 12, {"results": "a | pass | ok"})
    rule_set_milestone_ok = __import__("system.rule.milestone", fromlist=["rule_set_milestone"]).rule_set_milestone
    rule_set_milestone_ok(f, 11, {"goal": "M1", "criteria": "카운트 API | curl 확인\n버튼 UI | playwright 확인"})
    # 파싱: pass 표기 관용·증거 분리
    rs = parse_iter_results("카운트 API | pass | HTTP 200\n- 버튼 UI | fail | 안 보임")
    assert rs[0]["passed"] and not rs[1]["passed"] and rs[0]["evidence"] == "HTTP 200"
    out = rule_report_iter(f, 12, {"results": "카운트 API | pass | HTTP 200\n버튼 UI | fail | 안 보임"})
    assert "미충족" in out                                     # 일부만 실증 — 주기 유지
    out = rule_report_iter(f, 12, {"results": "버튼 UI | pass | playwright visible"})
    assert "wrapup" in out                                     # 전 조건 실증 — 스스로 전이
    out = rule_report_iter(f, 12, {"wrapup": "done"})
    assert "종료" in out and f.milestones[0].status == "done"  # 정리 선언으로 닫힘
    # 공통 표면: member 역할에서도 set_subtask·report_iter가 보인다(자발 참여의 문)
    from system.guide_tools import make_guide_tools
    names = {t.name for t in make_guide_tools(f, 12, "member")}
    assert {"set_subtask", "report_iter"} <= names and "set_milestone" not in names


def test_결정권자_프레임_프롬프트(monkeypatch):
    """[배치4] 플래그 ON에서 흐름을 여는 To 수신자는 리더가 아니라 결정권자 프레임을 받는다."""
    from system.sys_prompt import prompt as _prompt
    from types import SimpleNamespace
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    fake_sys = SimpleNamespace(bot_info={11: "백엔드", 12: "QA"}, bot_profiles={}, bot_experience={},
                               capability_ledger={}, _craft_note=lambda me, fw=True: "",
                               _portfolio_note=lambda: "", _origin_request="")
    p = _prompt(fake_sys, "카운터 만들어줘", "Work", "leader", 11, leader_id=11, flow=f)
    assert "결정권자" in p and "set_milestone" in p and "report_iter" in p
    assert "배정하지 마세요" in p                              # 배분은 릴레이 몫
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)
    p_off = _prompt(fake_sys, "카운터 만들어줘", "Work", "leader", 11, leader_id=11, flow=f)
    assert "결정권자" not in p_off                             # OFF — 종전 담당자 프레임 불변


def test_subtask_iter_통과가_백로그_정리훅을_부르고_닫는다(monkeypatch):
    """[통합주기 3 — §12-1 접점] report_iter(target=SubTask): 조건 실증 → S2 on_subtask_wrapup
    (잔여 백로그 정리) 호출 → 자동 종료. 허용목록도 공통(FLOW_TOOLS)에 있어 훅이 거부하지 않는다."""
    from system.rule.milestone import rule_report_iter, rule_set_milestone, rule_set_subtask
    from system.tool_names import FLOW_TOOLS, LEADER_TOOLS
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    rule_set_milestone(f, 11, {"goal": "M1", "criteria": "전체 빌드 | npm run build"})
    rule_set_subtask(f, 12, {"goal": "프론트 뼈대", "criteria": "index 로드 | curl -s /"})
    st = f.milestones[0].subtasks[0]
    out = rule_report_iter(f, 12, {"target": st.st_id, "results": "index 로드 | pass | HTTP 200"})
    assert "통과 — 종료" in out and st.status == "done"        # 훅 경유 자동 종료(정리 요지 동봉)
    out = rule_report_iter(f, 12, {"target": "없는것", "results": "x | pass | e"})
    assert "못 찾았습니다" in out
    # [S3 발견 결함의 회귀 가드] 등록(guide_tools)과 허용(tool_names)은 한 세트다.
    assert "mcp__guide__set_subtask" in FLOW_TOOLS and "mcp__guide__report_iter" in FLOW_TOOLS
    assert "mcp__guide__set_milestone" in LEADER_TOOLS
    assert "mcp__guide__set_subtask" not in LEADER_TOOLS       # 공통 이동 후 이중 배치 금지


def test_조건_불가능_출구_정체경보와_재협상_포기(monkeypatch):
    """[설계검토 #1] 진전 없는 반복 미충족이 임계 도달 시 정체 경보 → 결정권자 재협상 → 사람 승인
    포기(waive) → 나머지 조건으로 주기 진행(무한 iter 차단). 포기는 봇 혼자 못 하고 사람 승인 필요."""
    from system.rule.milestone import (approve_waiver, iter_verify, open_milestone,
                                        renegotiate_criterion, rule_renegotiate)
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    monkeypatch.setenv("ORGANT_ITER_STUCK_LIMIT", "3")
    f = _flow()
    ms = open_milestone(f, "M1", [{"desc": "카운트 API", "verify": "curl localhost:3000/count"},
                                  {"desc": "영속 저장", "verify": "재시작 후 curl로 카운트 유지 확인"}])
    # 진전 없이 3회 미충족 → 정체 경보
    for i in range(3):
        ok, note = iter_verify(f, ms, [{"desc": "카운트 API", "passed": False, "evidence": ""}])
    assert ms.iter_stuck >= 3 and "정체" in note and "renegotiate" in note
    # 비결정권자는 재협상 못 함
    assert "결정권자" in rule_renegotiate(f, 12, {"target": "영속 저장", "reason": "환경 제약"})
    # 결정권자 재협상 → blocked_pending(봇 혼자 포기 못 함, 사람 승인 대기)
    out = renegotiate_criterion(f, ms, "영속 저장", "이 환경은 파일 영속이 재시작에 안 남음")
    assert "승인 대기" in out and ms.criteria[1].status == "blocked_pending" and ms.iter_stuck == 0
    # 사람 승인 → waived → 그 조건은 미충족 목록에서 빠진다
    approve_waiver(f, ms, "영속 저장", approve=True)
    assert ms.criteria[1].status == "waived"
    ok, note = iter_verify(f, ms, [{"desc": "카운트 API", "passed": True, "evidence": "HTTP 200 count:1"}])
    assert ok is True and ms.status == "wrapup"                # 나머지 조건만 충족돼도 주기 닫힘


def test_등록게이트_실행불가_verify_거부(monkeypatch):
    """[설계검토 #4] verify가 '확인함' 같은 빈 서술이면 거부 — 실행 명령이나 측정 기준을 강제."""
    from system.rule.milestone import gate_criteria
    assert gate_criteria([{"desc": "카운트 증가", "verify": "확인한다"}]) is not None    # 빈 서술 거부
    assert "실행 가능한 형태" in gate_criteria([{"desc": "카운트 증가", "verify": "잘 되는지 본다"}])
    assert gate_criteria([{"desc": "카운트 증가", "verify": "curl localhost:3000/count 로 확인"}]) is None  # 명령
    assert gate_criteria([{"desc": "응답 시간", "verify": "3초 이하"}]) is None            # 측정
    assert gate_criteria([{"desc": "상태코드", "verify": "200 반환"}]) is None             # 수치


def test_흐름루프_주기_인식_종료조건(monkeypatch):
    """[§5 흐름 축] 플래그 ON에서 '미완 주기 존재'가 continue 루프의 계속 조건이 된다 —
    결정권자가 주기를 안 닫으면 SYS가 계속 깨워 주기를 닫게 한다(진행을 주기가 관할). OFF면 무영향."""
    from system.rule.milestone import next_milestone, open_milestone, pipeline_on
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    # _run_leader 내부 _ms_pending과 동일 술어를 재구성(단위 검증 — 루프 전체는 대본/관통이 검증)
    _pending = lambda: pipeline_on() and next_milestone(f) is not None
    assert _pending() is False                                  # 주기 없음 — 계속 안 함
    open_milestone(f, "M1", [{"desc": "a", "verify": "run a"}])
    assert _pending() is True                                   # 미완 주기 — 계속(결정권자를 깨움)
    f.milestones[0].status = "done"
    assert _pending() is False                                  # 주기 소진 — 종료 허용
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)
    open_milestone(f, "M2", [{"desc": "b", "verify": "run b"}])  # 데이터 있어도
    assert (pipeline_on() and next_milestone(f) is not None) is False   # OFF면 무영향


def test_체크포인트_동승과_복원_왕복(tmp_path):
    """[§9 최대 저장] checkpoint_open_task가 주기 상태를 프로젝트 레지스트리에 싣고,
    restore_open_task가 open_task 유무와 무관하게 되살린다(재시작 후 중간 재개의 실배선)."""
    import asyncio
    from types import SimpleNamespace
    from system import sys_recovery

    f = _flow()
    f.project_channel = 500
    ms = open_milestone(f, "M1", [{"desc": "a", "verify": "run a"}])
    iter_verify(f, ms, [{"desc": "a", "passed": True, "evidence": "ok"}])
    fake_sys = SimpleNamespace(projects={500: {}}, _save_projects=lambda: None,
                               _task_snapshot=lambda flow, t: {})
    sys_recovery.checkpoint_open_task(fake_sys, f)
    saved = fake_sys.projects[500]["milestones"]
    assert saved and saved[0]["status"] == "wrapup"            # 상태 그대로 동승
    f2 = _flow()                                               # 재시작 후 새 흐름
    out = asyncio.run(sys_recovery.restore_open_task(fake_sys, f2, fake_sys.projects[500]))
    assert out is None                                         # open_task 없어도
    assert len(f2.milestones) == 1 and f2.milestones[0].status == "wrapup"   # 주기 복원
    assert f2.milestones[0].criteria[0].evidence == "ok"       # 증거까지 무손실
