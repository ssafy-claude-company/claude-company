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
    dup = ok + [{"desc": "버튼 클릭 시 카운트 1 증가", "verify": "다른 절차"}]
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
