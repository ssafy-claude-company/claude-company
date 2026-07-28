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


def test_산출물stamp는_큰파일의_동일크기_중간변경도_탐지(tmp_path):
    """release/e2e 버전은 큰 파일의 앞·뒤 표본이 아니라 전체 authoring 내용에 귀속된다."""
    from system.rule.milestone import workspace_artifact_stamp

    f = _flow()
    f.workspace = str(tmp_path)
    artifact = tmp_path / "large-artifact.bin"
    artifact.write_bytes(b"A" * (5 * 1024 * 1024))
    before = workspace_artifact_stamp(f)

    with artifact.open("r+b") as fh:
        fh.seek(2 * 1024 * 1024 + 17)  # 종전 앞/뒤 1MiB 표본 밖, 파일 크기는 그대로
        fh.write(b"B")

    after = workspace_artifact_stamp(f)
    assert before and after and before != after


def test_산출물stamp는_lockfile_next배포물_symlink_target을묶고_dependency_cache는제외(
    tmp_path,
):
    from system.rule.milestone import workspace_artifact_stamp

    f = _flow()
    f.workspace = str(tmp_path)
    lock = tmp_path / "package-lock.json"
    built = tmp_path / ".next" / "server" / "app.js"
    cached = tmp_path / "node_modules" / "pkg" / "index.js"
    external = tmp_path.parent / f"{tmp_path.name}-runtime.js"
    lock.write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    built.parent.mkdir(parents=True)
    built.write_text("runtime-v1\n", encoding="utf-8")
    cached.parent.mkdir(parents=True)
    cached.write_text("cache-v1\n", encoding="utf-8")
    external.write_text("linked-v1\n", encoding="utf-8")
    (tmp_path / "runtime-link.js").symlink_to(external)

    base = workspace_artifact_stamp(f)
    cached.write_text("cache-v2\n", encoding="utf-8")
    assert workspace_artifact_stamp(f) == base                 # 설치 cache는 비용상 제외
    lock.write_text('{"lockfileVersion":3,"packages":{}}\n', encoding="utf-8")
    lock_changed = workspace_artifact_stamp(f)
    assert lock_changed != base                                # 의존성 해석판은 결속
    built.write_text("runtime-v2\n", encoding="utf-8")
    built_changed = workspace_artifact_stamp(f)
    assert built_changed != lock_changed                       # 실제 Next 배포물도 결속
    external.write_text("linked-v2\n", encoding="utf-8")
    assert workspace_artifact_stamp(f) != built_changed        # 실행되는 symlink target도 결속


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
    # [서브태스크 조건 게이트 폐지(2026-07-22, GPT e2e 실측)] 불량 조건은 거부 말고 폐기 — 서브태스크
    # 조건은 완수 판정에 안 쓰니(완수=백로그 소진), 순수 작업 영역으로 개설한다(등록 루프 봉합).
    opened = open_subtask(f, ms, "x", [{"desc": "완벽해야 함", "verify": ""}])
    assert not isinstance(opened, str) and opened.goal == "x"


def test_마일스톤_완수는_서브태스크_전부_완료_요구():
    """[최대 구현 선완료(2026-07-14, 사용자: '조건 다 만족해도 서브테스크는 다 하고 끝내는걸로')] 마일스톤
    조건이 4/4여도 그 주기가 낳은 SubTask가 open이면 완수 보류 — 하위 단위 전부 done이라야 주기가 닫힌다
    (ch61: 조건 4/4인데 ST 5개 open으로 닫혀 빈 백로그 유령)."""
    f = _flow()
    ms = open_milestone(f, "M1", [{"desc": "빌드 통과", "verify": "npm run build 0"}])
    st = open_subtask(f, ms, "프론트 뼈대", [{"desc": "index 로드", "verify": "curl -s localhost/"}])
    ok, _ = iter_verify(f, ms, [{"desc": "빌드 통과", "passed": True, "evidence": "exit 0"}])
    assert ok and ms.status == "wrapup"
    # SubTask(st) 미완 → 마일스톤 완수 보류
    r = wrapup_done(f, ms)
    assert "보류" in r and st.st_id in r and ms.status == "wrapup"     # 아직 안 닫힘
    # SubTask 닫고 나면 통과
    iter_verify(f, st, [{"desc": "index 로드", "passed": True, "evidence": "HTTP 200"}])
    assert st.status == "wrapup" and wrapup_done(f, st) == "done"
    assert wrapup_done(f, ms) == "done" and ms.status == "done"        # 하위 완료 후 주기 닫힘


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


def test_등록은_누구나_서기_파싱과_게이트_경유(monkeypatch):
    """[결정권자 폐지] 마일스톤 등록은 누구나(서기) — 확정의 실체는 회의 종결 표결이고 품질은
    등록 게이트가 방어. '조건 | 실증절차' 줄 파싱·소망형 거부는 그대로."""
    from system.rule.milestone import rule_set_milestone, rule_set_subtask
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    out = rule_set_milestone(f, 12, {"goal": "M1", "criteria": "- 카운트 API | curl 확인\n버튼 UI | playwright 확인"})
    assert "개설" in out and len(f.milestones[0].criteria) == 2   # 비리더도 등록 가능(서기)
    out = rule_set_milestone(f, 11, {"goal": "M2", "criteria": "잘 동작해야 함"})
    assert "거부" in out and len(f.milestones) == 1          # 소망형 — 등록 게이트가 막음(품질 방어)
    out = rule_set_subtask(f, 12, {"goal": "프론트 뼈대", "criteria": "index 로드 | curl -s /"})
    assert "추가" in out and f.milestones[0].subtasks        # SubTask 추가는 현장 누구나(자발 참여)


def test_팀판_개인등록_차단_확정은_표결_분해는_수렴안(monkeypatch):
    """[확정 권위 게이트(2026-07-14, 사용자: '개인이 마일스톤 만들고 대체되고 난리 — 닫아야지. 개인
    권한 서브태스크·백로그 다 제한하고 회의 흐름을 이용하도록')] 동료가 있는 판에서 set_milestone·
    set_subtask 개인 등록은 거부 — 주기 확정은 회의 종결 표결([수렴안] 가결 자동 등록), 단위 분해는
    수렴안 '단위:' 줄. 솔로 판(동료 없음)은 종전대로 도구 허용. U-019 라이브: 표결 0건, 개인 직접
    등록·대체 파기가 원인."""
    import types
    from system.rule.milestone import rule_set_milestone, rule_set_subtask
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    f.current = types.SimpleNamespace(team=[11, 12], status=types.SimpleNamespace(goal="목표 확정됨"))
    out = rule_set_milestone(f, 12, {"goal": "M1", "criteria": "API | curl 확인"})
    assert "거부" in out and "표결" in out and not f.milestones     # 팀 판 개인 등록 차단
    out = rule_set_subtask(f, 12, {"goal": "단위", "criteria": "로드 | curl -s /"})
    assert "거부" in out and "수렴안" in out                        # 단위 분해도 수렴안 경로만
    f.current.team = [12]                                           # 솔로(자기뿐) — 도구 허용
    out = rule_set_milestone(f, 12, {"goal": "M1", "criteria": "API | curl 확인"})
    assert "개설" in out


def test_가결_수렴안_단위줄_마일스톤과_동반등록(monkeypatch):
    """[단위 동반 등록(2026-07-14)] 수렴안의 '단위:' 줄 = 팀 합의 SubTask 분해 — 가결 등록
    (register_consensus)이 마일스톤과 함께 등록하고, '|'를 포함해도 마일스톤 조건으로 오파싱하지
    않는다. 팀 판에서 단위가 생기는 유일 경로."""
    from system.rule.milestone import register_consensus
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    prop = ("목표: 방명록 1주기\n등록 API 동작 | curl POST 후 GET 확인\n"
            "단위: 백엔드 저장 API | curl POST 200 확인\n단위: 프론트 목록 UI | playwright 로드 확인")
    ms, n = register_consensus(f, prop, "방명록")
    assert not isinstance(ms, str) and n == 2
    assert len(ms.criteria) == 1                                    # '단위:' 줄이 조건에 안 섞임
    assert [st.goal for st in ms.subtasks] == ["백엔드 저장 API", "프론트 목록 UI"]


def test_수렴안_로드맵과_단계인식_분해회의(monkeypatch):
    """[전체 플로우(2026-07-14 사용자 설계)] ①첫 수렴안: '단계:' 줄 = 로드맵(달구지→자동차) 보관 +
    M1 등록 ②열린 주기 존재 시 수렴안 = 그 주기의 분해 회의(단위 추가, 주기 신설 아님 = 순차 1주기)
    ③기존 백로그가 처리 중이면 단위 추가 보류(경계 생성 — '종료될 때만 생성')."""
    from system.rule.milestone import register_consensus
    from system.rule.backlog import relay_for
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    prop1 = ("단계: 달구지 — ToDo MVP\n단계: 자동차 — 계정·동기화\n목표: ToDo MVP\n"
             "CRUD 동작 | curl POST 후 GET 확인")
    ms, n = register_consensus(f, prop1, "ToDo")
    assert not isinstance(ms, str) and f.roadmap == ["달구지 — ToDo MVP", "자동차 — 계정·동기화"]
    assert len(ms.criteria) == 1 and n == 0                       # '단계:' 줄이 조건에 안 섞임
    # ② 열린 주기 존재 — 분해 회의(단위 추가)
    ms2, n2 = register_consensus(f, "단위: 백엔드 API | curl 200 확인", "분해")
    assert not isinstance(ms2, str) and ms2.ms_id == ms.ms_id and n2 == 1   # 신설 아님 — 같은 주기
    # ③ 백로그 처리 중 — 경계 생성 보류
    r = relay_for(f, ms.subtasks[0])
    b = r.submit(12, "저장 API 구현")
    r.pick(12, b.backlog_id, 12)
    err, _ = register_consensus(f, "단위: 프론트 UI | playwright 확인", "분해2")
    assert isinstance(err, str) and "보류" in err and "처리 중" in err
    b2 = r.done(12, b.backlog_id)                                 # 종료 후엔 추가 가능
    ms3, n3 = register_consensus(f, "단위: 프론트 UI | playwright 확인", "분해2")
    assert not isinstance(ms3, str) and n3 == 1


def test_완수_조건충족이어도_백로그_처리중이면_보류_중단은제외(monkeypatch):
    """[완수 정의(2026-07-14, 사용자: '백로그를 모두 완수하면 끝 — 중단으로 처리된 것은 제외')]
    완수조건이 전부 충족돼도 미종결(open/in_progress) 백로그가 남으면 wrapup 보류. dropped(중단)는
    완수 집계에서 제외돼 종결로 취급 — 남은 게 dropped뿐이면 통과."""
    from system.rule.milestone import iter_verify, Milestone, Criterion
    from system.rule.backlog import relay_for
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    ms = Milestone(ms_id="MS-1", goal="g", criteria=[Criterion("조건A", "run a")])
    f.milestones = [ms]
    from system.rule.milestone import SubTask
    st = SubTask(st_id="MS-1/ST-1", goal="단위", criteria=[])
    ms.subtasks.append(st)
    r = relay_for(f, st)
    r.submit(11, "작업1"); r.pick(11, "B1", 11)                   # B1 in_progress
    ok, note = iter_verify(f, ms, [{"desc": "조건A", "passed": True, "evidence": "run OK"}])
    assert ok is False and "백로그" in note and "처리 중" in note   # 조건 충족해도 백로그 남아 보류
    r.drop(11, "B1", "역량 밖")                                    # 중단(dropped)으로 처리
    ok2, note2 = iter_verify(f, ms, [{"desc": "조건A", "passed": True, "evidence": "run OK"}])
    assert ok2 is True and ms.status == "wrapup"                   # dropped는 제외 → 통과


def test_수렴안_미동봉시_재회의_코칭이_붙는다():
    """[회의 미수렴 재시도(2026-07-14, 안정성 감사 위험#1)] 파이프라인 회의가 종결됐는데 [수렴안]이
    하나도 없고 열린 마일스톤도 없으면, 종전엔 침묵(코칭 0)이라 판이 겉돌았다 — 이제 '확정 실패 —
    수렴안 미동봉' + 형식 재안내가 회의록에 붙어 재meet를 유도한다. register_consensus 경로의 방어."""
    # extract_consensus가 빈 응답에서 아무것도 못 뽑는지(코칭 트리거 조건) 단위 확인.
    from system.rule.milestone import extract_consensus
    assert extract_consensus("이 회의 마칩니다. [종료]") is None      # 수렴안 미동봉 → None → conv_props 빔
    assert extract_consensus("[수렴안]\n목표: x\n조건 | run\n[/수렴안]") is not None


def test_중지투표_도구가_리더셋에_등록되고_import된다():
    """[중지 투표(2026-07-14)] vote_stop 세리머니가 재수출되고 리더 도구 셋에 등록됐는지(계약 정합)."""
    from system.rule.communication import vote_stop            # 재수출 경로
    from system.tool_names import LEADER_TOOLS
    assert callable(vote_stop)
    assert "mcp__guide__vote_stop" in LEADER_TOOLS


def test_수렴안_파싱_콜론없는줄_크래시안함_단계조건_안뺏김(monkeypatch):
    """[파싱 견고화(2026-07-14, 정합 감사)] ①콜론 없는 '목표..'/'단계..'가 split(':',1)[1] IndexError로
    meet를 크래시시키던 것 봉합 ②'단계'가 아니라 '단계:'로만 로드맵 인식 — '단계별 배포 | 확인'류
    완수조건이 roadmap으로 오분류돼 조건에서 소실되던 비대칭 제거."""
    from system.rule.milestone import register_consensus
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    # 콜론 없는 '목표 없이' 줄 + '단계별' 완수조건 — 크래시 없이 조건으로 보존돼야
    prop = ("목표: 배포\n단계별 배포 성공 | curl -s localhost:8000 확인\n기능 동작 | playwright로 로드 확인\n단계: MVP\n단계: 확장")
    ms, n = register_consensus(f, prop, "t")
    assert not isinstance(ms, str)                              # 크래시 없음
    assert f.roadmap == ["MVP", "확장"]                         # '단계:'만 로드맵
    _descs = " ".join(c.desc for c in ms.criteria)
    assert "단계별 배포 성공" in _descs                         # '단계별' 조건이 소실 안 됨(2개 조건)
    assert len(ms.criteria) == 2


def test_GOAL_구조화_회의수렴안이_Task목표를_채움(monkeypatch):
    """[GOAL 구조화(2026-07-14, 사용자: 'set_goal도 봇 지능 의지보다 구조적으로 제한')] 개인이
    set_goal을 부를지가 아니라, 첫 주기 수렴안의 '목표:' 줄이 미확정 Task GOAL을 구조적으로 채운다 —
    같은 회의 산물로 목표 선행 게이트가 충족돼 마일스톤이 등록된다."""
    import types
    from system.rule.milestone import register_consensus
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    f.current = types.SimpleNamespace(team=[11, 12], status=types.SimpleNamespace(goal=""))
    assert not f.current.status.goal                             # GOAL 미확정
    ms, _ = register_consensus(f, "목표: ToDo MVP 배포\n동작 | curl 확인", "ToDo")
    assert not isinstance(ms, str)                               # 목표 미확정이어도 수렴안이 채워 등록됨
    assert f.current.status.goal == "ToDo MVP 배포"              # Task GOAL이 회의 산물로 세팅됨


def test_마일스톤_완수_보고와_로드맵_다음단계_코칭(monkeypatch):
    """[주기 보고 체계(2026-07-14, 사용자: '각 주기마다 사용자가 체감하도록 적용하고 보고')] 마일스톤
    wrapup_done → [마일스톤 보고](조건+증거) 게시 + 로드맵 다음 단계 회의 코칭."""
    from system.rule.milestone import register_consensus, iter_verify, wrapup_done
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    ms, _ = register_consensus(f, "단계: MVP\n단계: 확장판\n목표: MVP\n동작 | curl 확인", "t")
    ms.criteria[0].passed = True
    ms.criteria[0].evidence = "curl 200 OK"
    ms.status = "wrapup"
    assert wrapup_done(f, ms) == "done"
    notes = "\n".join(getattr(f, "_pipeline_notes", []) or [])
    assert "[마일스톤 보고]" in notes and "curl 200 OK" in notes
    assert "[다음 단계]" in notes and "확장판" in notes            # 로드맵 2단계 회의 코칭


def test_종결표결_수렴안_추출과_결정권자_부재(monkeypatch):
    """[결정권자 폐지] [수렴안] 블록 파서 — 종결 표결 동봉분을 시스템이 서기로 등록하는 원료.
    재협상도 누구나(사람 승인이 진짜 게이트)."""
    from system.rule.milestone import extract_consensus, open_milestone, rule_renegotiate
    body = ("[종료]\n[수렴안]\n목표: 방명록 1주기\n등록 API 동작 | curl POST 후 GET 확인\n"
            "목록 표시 | playwright 로드 확인\n[/수렴안]")
    c = extract_consensus(body)
    assert c and "등록 API" in c and "playwright" in c
    assert extract_consensus("[종료]") is None               # 미동봉 — 등록 없음
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    open_milestone(f, "M1", [{"desc": "a", "verify": "run a"}])
    out = rule_renegotiate(f, 12, {"target": "a", "reason": "인프라 제약"})
    assert "승인 대기" in out                                # 비리더도 재협상 가능(누구나)


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
    # 공통 표면: member 역할에서도 파이프라인 도구 전부가 보인다 — set_milestone도 서기(누구나)
    from system.guide_tools import make_guide_tools
    names = {t.name for t in make_guide_tools(f, 12, "member")}
    assert {"set_subtask", "report_iter", "set_milestone", "renegotiate_criterion"} <= names


def test_첫턴_프레임_중앙집권_해제_프롬프트(monkeypatch):
    """[중앙집권 해제(2026-07-14, 사용자: '발제자같은 중앙집권은 해제')] 플래그 ON에서 To 수신자는
    '발제자/우두머리'가 아니라 권한 없는 '첫 턴' 프레임 — GOAL·마일스톤·단위는 회의 수렴안이, 배분은
    순차 릴레이가 맡는다는 안내를 받는다. '발제자' 라벨과 개인 set_subtask 안내는 사라진다."""
    from system.sys_prompt import prompt as _prompt
    from types import SimpleNamespace
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    fake_sys = SimpleNamespace(bot_info={11: "백엔드", 12: "QA"}, bot_profiles={}, bot_experience={},
                               capability_ledger={}, _craft_note=lambda me, fw=True: "",
                               _portfolio_note=lambda: "", _origin_request="")
    p = _prompt(fake_sys, "카운터 만들어줘", "Work", "leader", 11, leader_id=11, flow=f)
    assert "발제자" not in p                                   # 중앙집권 라벨 제거
    assert "첫 턴" in p and "우두머리가 아니라" in p           # 권력 아닌 첫 턴 역할
    assert "수렴안" in p and "순차 릴레이" in p                # 확정=회의 수렴안 / 배분=순차 릴레이
    assert "pick_backlog" in p and "drop_backlog" in p         # 새 플로우 도구 안내
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)
    p_off = _prompt(fake_sys, "카운터 만들어줘", "Work", "leader", 11, leader_id=11, flow=f)
    assert "첫 턴" not in p_off and "발제자" not in p_off       # OFF — 종전 담당자 프레임 불변


def test_subtask는_백로그소진으로_닫힌다_게이트없음(monkeypatch):
    """[서브태스크 게이트 제거(2026-07-22, 사용자: '서브태스크·백로그 검증은 비용만 크다 — 검증은
    마일스톤')] report_iter(target=SubTask)는 조건 검증(iter_verify)이 아니라 백로그 소진으로 완수 —
    든 백로그를 완료해 전부 소진되면 종료, 잔여 있으면 '다음 수행자 선정' 코칭. 허용목록 공통 불변."""
    from system.rule.backlog import relay_for
    from system.rule.milestone import rule_report_iter, rule_set_milestone, rule_set_subtask
    from system.tool_names import FLOW_TOOLS, LEADER_TOOLS
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    rule_set_milestone(f, 11, {"goal": "M1", "criteria": "전체 빌드 | npm run build"})
    rule_set_subtask(f, 12, {"goal": "프론트 뼈대"})           # 조건 없이(게이트 제거) 개설
    st = f.milestones[0].subtasks[0]
    assert st.criteria == []                                    # 서브태스크는 조건 없음(작업 영역)
    r = relay_for(f, st)
    b1 = r.submit(12, "index 페이지 뼈대 구현"); b2 = r.submit(12, "라우팅 구현")
    r.pick(12, b1.backlog_id, 12)
    out = rule_report_iter(f, 12, {"target": st.st_id, "results": "index 페이지 뼈대 구현 | pass | done"})
    assert "잔여 1건" in out and st.status != "done"           # B2 남음 → 미완, 다음 수행자 코칭
    r.pick(12, b2.backlog_id, 12)
    out = rule_report_iter(f, 12, {"target": st.st_id, "results": "라우팅 구현 | pass | done"})
    assert "소진, 완수" in out and st.status == "done"          # 백로그 전부 소진 → 완수(게이트 없음)
    out = rule_report_iter(f, 12, {"target": "없는것", "results": "x | pass | e"})
    assert "못 찾았습니다" in out
    # [S3 발견 결함의 회귀 가드] 등록(guide_tools)과 허용(tool_names)은 한 세트다.
    # [결정권자 폐지] 파이프라인 도구 4종 전부 공통(FLOW) — 리더 전용 배치 금지.
    assert {"mcp__guide__set_subtask", "mcp__guide__report_iter",
            "mcp__guide__set_milestone", "mcp__guide__renegotiate_criterion"} <= set(FLOW_TOOLS)
    assert "mcp__guide__set_milestone" not in LEADER_TOOLS
    assert "mcp__guide__set_subtask" not in LEADER_TOOLS       # 공통 이동 후 이중 배치 금지


def test_보고는_미착수_등록백로그를_일괄완료_못한다(monkeypatch):
    """[U-041 실측(2026-07-22, 사용자: '서브태스크 하나 다 처리해서 백로그 한번에 다 검증 성공 박아')]
    게임 기획자가 B1만 작업하고 한 report_iter로 B2·B3(미착수 등록 백로그)까지 desc-매칭 자동
    pick+done → 무작업 거짓 일괄 완수. 수리: 보고는 '내가 든(in_progress·me)' 백로그나 방금 만든
    솔로 작업만 완료 — 선점 안 한 open 등록 백로그는 보고로 못 닫는다(각자 릴레이로)."""
    from system.rule.backlog import relay_for
    from system.rule.milestone import rule_report_iter, rule_set_milestone, rule_set_subtask
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    rule_set_milestone(f, 11, {"goal": "게임", "criteria": "30턴 완주 | run 재현"})
    rule_set_subtask(f, 12, {"goal": "게임 로직", "criteria": "로직 검증 | pytest 통과"})
    ms = f.milestones[0]; st = ms.subtasks[0]
    r = relay_for(f, st)
    b1 = r.submit(12, "카드 비교 규칙 구현")
    b2 = r.submit(12, "점수 공식 구현")
    b3 = r.submit(12, "라운드 전환 구현")
    r.pick(12, b1.backlog_id, 12)                              # 12가 B1만 선점(작업 중)
    # 12가 한 보고로 B1·B2·B3 전부 pass 보고
    rule_report_iter(f, 12, {"target": st.st_id,
                             "results": "카드 비교 규칙 구현 | pass | done\n"
                                        "점수 공식 구현 | pass | done\n"
                                        "라운드 전환 구현 | pass | done"})
    byid = {b.backlog_id: b for b in r.backlogs}
    assert byid[b1.backlog_id].status == "done"               # 내가 든 B1만 완료
    assert byid[b2.backlog_id].status != "done"               # 미착수 B2는 보고로 완료 불가
    assert byid[b3.backlog_id].status != "done"               # B3도


def test_무지정_보고는_자기백로그_SubTask로_귀속_릴레이_이음(monkeypatch):
    """[U-036 실측(2026-07-21): 디자이너가 target 없이 report_iter → 마일스톤 조건(V0/V1 게이트)에
    0/5 미착지, 자기 백로그는 영원히 in_progress → 릴레이 정지·[다음 선정] 불발 → 54분 메타 표결
    공회전] ①target 미지정 + 보고자가 in_progress 백로그를 쥔 SubTask가 있으면 그 SubTask로 자동
    귀속 ②자기선점 백로그의 완료 보고도 위임 완료와 같은 핸드오프 공고([다음 선정])를 띄운다."""
    from system.rule.backlog import relay_for
    from system.rule.milestone import rule_report_iter, rule_set_milestone, rule_set_subtask
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    evs = []
    f.log = lambda ev, **kw: evs.append((ev, kw))
    rule_set_milestone(f, 11, {"goal": "게임 최소버전", "criteria": "브라우저 30턴 완주 | run 재현"})
    rule_set_subtask(f, 12, {"goal": "메커닉 규격", "criteria": "전 규격 리뷰 통과 | grep -c 승인 review.md ≥ 1"})
    st = f.milestones[0].subtasks[0]
    r = relay_for(f, st)
    b = r.submit(12, "메커닉 규격 정의")
    r.pick(12, b.backlog_id, 12)                       # 12가 자기선점(in_progress)
    r.submit(13, "선택지 데이터 구조")                   # 남은 백로그(다음 선정 후보)
    f._pipeline_notes = []
    rule_report_iter(f, 12, {"results": "메커닉 규격 정의 | pass | mechanic_spec.md 완성"})
    assert b.status == "done"                          # 자기 백로그가 장부에 착지
    assert any(ev == "iter_target_inferred" and kw.get("st") == st.st_id for ev, kw in evs)
    assert any("[다음 선정]" in n for n in f._pipeline_notes)   # 릴레이가 이어진다(정지 없음)
    # 백로그를 안 쥔 보고는 종전대로 마일스톤 검증(무회귀) — 미착지 안내에 오귀속 코칭 동봉
    out = rule_report_iter(f, 13, {"results": "엉뚱한 산출물 | pass | 파일"})
    assert "미착지" in out and "당신이 집은 백로그" in out


def test_완료된_서브태스크는_재검증_차단_다음단위로(monkeypatch):
    """[U-039 재개 실측(2026-07-21, 사용자: '완료로 뜨는데 재검증 루프')] 재개 시 앵커의 스테일
    컨텍스트가 이미 done인 ST-1에 계속 report_iter를 날려 iter_n++·ms_iter_pass가 반복(크레딧
    공회전)되고 릴레이는 다음 단위로 안 넘어갔다. 완료된 SubTask 재검증은 차단하고 다음 미완 단위로
    코칭한다."""
    from system.rule.backlog import relay_for
    from system.rule.milestone import rule_report_iter, rule_set_milestone, rule_set_subtask
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    rule_set_milestone(f, 11, {"goal": "게임", "criteria": "브라우저 30턴 | run 재현"})
    rule_set_subtask(f, 12, {"goal": "메커닉", "criteria": "규격 리뷰 | grep -c 승인 review.md ≥ 1"})
    rule_set_subtask(f, 12, {"goal": "로직", "criteria": "로직 검증 | pytest 통과"})
    ms = f.milestones[0]
    st1, st2 = ms.subtasks[0], ms.subtasks[1]
    st1.status = "done"                                    # ST-1 완료 상태(재개 후)
    _n0 = st1.iter_n
    out = rule_report_iter(f, 11, {"target": st1.st_id, "results": "규격 리뷰 | pass | ok"})
    assert "이미 완료" in out and st2.st_id in out          # 재검증 거부 + 다음 미완 단위 코칭
    assert st1.iter_n == _n0                                # iter_n 안 늘어남(재검증 루프 차단)


def test_등록_허용목록_전수_대조_회귀가드(monkeypatch):
    """[ch79 실측 회귀(2026-07-19) — 손 고른 부분집합 가드의 구멍] pick_backlog가 등록만 되고
    허용목록에 빠져 봇 선점이 전원 '권한 밖 도구' 거부 → 작업 전이 교착의 숨은 뿌리.
    가드를 등록 기반 전수 대조로: 멤버/리더 세션에 실제 등록되는 guide 도구 전부가
    그 역할의 허용 집합(FLOW / FLOW∪LEADER)에 있어야 한다."""
    from system.tool_names import FLOW_TOOLS, LEADER_TOOLS
    from test_sys import FakeGuide, _flow as _sys_flow, _tools
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _sys_flow(FakeGuide())
    flow_set, lead_set = set(FLOW_TOOLS), set(FLOW_TOOLS) | set(LEADER_TOOLS)
    member_names = {f"mcp__guide__{n}" for n in _tools(f, 12, "member")}
    leader_names = {f"mcp__guide__{n}" for n in _tools(f, 11, "leader")}
    assert member_names <= flow_set, f"멤버 등록-허용 불일치: {sorted(member_names - flow_set)}"
    assert leader_names <= lead_set, f"리더 등록-허용 불일치: {sorted(leader_names - lead_set)}"


def test_조건_불가능_출구_정체경보와_재협상_포기(monkeypatch):
    """[설계검토 #1 · 결정권자 폐지] 진전 없는 반복 미충족이 임계 도달 시 정체 경보 → **누구나**
    재협상 상신 → 사람 승인 포기(waive) → 나머지 조건으로 주기 진행(무한 iter 차단)."""
    from system.rule.milestone import (approve_waiver, iter_verify, open_milestone,
                                        rule_renegotiate)
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    monkeypatch.setenv("ORGANT_ITER_STUCK_LIMIT", "3")
    f = _flow()
    ms = open_milestone(f, "M1", [{"desc": "카운트 API", "verify": "curl localhost:3000/count"},
                                  {"desc": "영속 저장", "verify": "재시작 후 curl로 카운트 유지 확인"}])
    # 진전 없이 3회 미충족 → 정체 경보
    for i in range(3):
        ok, note = iter_verify(f, ms, [{"desc": "카운트 API", "passed": False, "evidence": ""}])
    assert ms.iter_stuck >= 3 and "정체" in note and "renegotiate" in note
    # 재협상은 누구나(결정권자 폐지) — 진짜 게이트는 사람 승인. 봇 혼자 포기 못 함(blocked_pending).
    out = rule_renegotiate(f, 12, {"target": "영속 저장", "reason": "이 환경은 파일 영속이 재시작에 안 남음"})
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


def test_회의단계_체인_goal_마일스톤_서브태스크_백로그_순차(monkeypatch):
    """[회의 하나당 결론 하나(2026-07-14, 사용자)] meeting_stage가 상태에서 단계를 유도하고
    register_stage가 그 단계 결론 하나만 등록 — GOAL→마일스톤→서브태스크→백로그 순차. 각 단계는
    이전 결론 위에서만 열려 겹치지 않는다(종전 '수렴안 하나가 다 만들기' = 너무 큰 회의를 대체)."""
    import types
    from system.rule.milestone import meeting_stage, register_stage
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    f.current = types.SimpleNamespace(task_id="T1", team=[11, 12],
                                      status=types.SimpleNamespace(goal="", purpose=""),
                                      acceptance="", standard="", interfaces="")
    # ① GOAL 단계 — [수렴안]을 가공해 목표만 정함(통일 수렴안, 가공은 단계 몫)
    assert meeting_stage(f) == "goal"
    ok, _ = register_stage(f, "goal", "목표: 방명록 앱\n등록 동작 | curl POST 확인")
    assert ok and f.current.status.goal == "방명록 앱"
    assert not getattr(f, "milestones", None)                # GOAL 회의는 마일스톤을 안 연다

    # ② 마일스톤 단계 — GOAL 섰으니 로드맵/주기
    assert meeting_stage(f) == "milestone"
    ok, _ = register_stage(
        f, "milestone",
        "단계: MVP\n단계: 확장\n이번 주기: 방명록 MVP\n"
        "로드 | 실증: python3 verify_guestbook.py",
    )
    assert ok and f.roadmap == ["MVP", "확장"]
    _ms = [m for m in f.milestones if m.status not in ("done", "superseded")][0]
    assert not _ms.subtasks                                  # 마일스톤 회의는 단위를 안 만든다

    # ③ 서브태스크 단계 — 마일스톤 섰고 단위 없음
    assert meeting_stage(f) == "subtask"
    ok, _ = register_stage(f, "subtask", "단위: 백엔드 API | curl 확인\n단위: 프론트 폼 | playwright 확인")
    assert ok and len([s for s in _ms.subtasks if s.status != "superseded"]) == 2

    # ④ 백로그 단계 — 첫 미충원 단위부터 (단위마다 별도 백로그 회의)
    assert meeting_stage(f) == "backlog"
    ok, _ = register_stage(f, "backlog", "백로그: POST /guestbook 구현\n백로그: GET 목록 구현")
    assert ok
    # [게으른 백로그 회의(2026-07-17, ch78 실측: 단위 9개면 회의 9개를 다 끝내야 실작업 — ~1h×9 선행
    # 낭비)] 집을 일이 남았으면 작업이 먼저 — 둘째 단위 회의는 첫 단위 백로그 '소진'이 연다(릴레이).
    assert meeting_stage(f) is None                          # 첫 단위에 집을 일 있음 → 작업 단계 우선
    for b in f.backlog_relays[_ms.subtasks[0].st_id].backlogs:
        b.status = "done"                                    # 첫 단위 백로그 소진 시뮬레이션
    assert meeting_stage(f) == "backlog"                     # 소진 → 이제 둘째 단위 백로그 회의
    ok, _ = register_stage(f, "backlog", "백로그: 폼 UI\n백로그: 제출 검증")
    assert ok
    assert meeting_stage(f) is None                          # 둘째 단위에 집을 일 → 작업 단계


def test_GOAL_비준전문_공개계약_완수조건_체크포인트와_하위잠금계보(monkeypatch, tmp_path):
    """U-052: GOAL 표결 원문이 TaskRef/문서/체크포인트에 남고 하위 주기가 이를 대체하지 못한다.

    상위 조건은 중간 마일스톤의 active 분모에 합치지 않는다. 마지막 로드맵 주기에서만 실제
    release_lock 조건으로 승격되어 기존 실증 경로를 타고, 별도 locked_criteria에도 계보가 남는다.
    """
    import types
    from system._util import dossier_read
    from system.rule.milestone import (
        canonical_parent_contract, ms_status_snapshot, register_stage,
    )

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    f.workspace = str(tmp_path)
    f.current = types.SimpleNamespace(
        task_id="T-U052", team=[11, 12],
        status=types.SimpleNamespace(goal="", purpose="상태 머신 구현"),
        acceptance="", standard="", interfaces="",
    )
    checkpoints = []
    f.checkpoint_task = lambda: checkpoints.append("saved")
    parent = (
        "목표: 호출자가 상태 전이를 통제하는 StateMachine\n"
        "공개 계약: transition(nextState)는 금지 전이에 Error를 던지고 현재 상태를 보존한다.\n"
        "- 금지 전이는 Error와 상태 불변을 보장 | 실증: node test_state_machine.js"
    )
    ok, note = register_stage(f, "goal", parent, "U-052")
    assert ok, note
    assert checkpoints == ["saved"]                         # 마일스톤 전에도 GOAL 자체가 즉시 체크포인트
    assert f.current.acceptance == (
        "- 금지 전이는 Error와 상태 불변을 보장 | 실증: node test_state_machine.js")
    assert "transition(nextState)" in f.current.interfaces
    goal_doc = dossier_read(f, "GOAL.md") or ""
    assert "## Ratified Decision" in goal_doc and parent in goal_doc
    assert canonical_parent_contract(f) == parent            # 80/160자 절단 없는 비준 전문

    child = (
        "단계: MVP\n"
        "단계: 확장\n"
        "이번 주기: Node18 상태 머신 최소 구현\n"
        "- 금지 전이는 false를 반환 | 실증: node test_child_contract.js"
    )
    ok2, note2 = register_stage(f, "milestone", child, "U-052")
    assert ok2, note2
    ms = f.milestones[-1]
    assert [(c.desc, c.verify) for c in ms.criteria] == [
        ("금지 전이는 false를 반환", "node test_child_contract.js")
    ]                                                        # 중간 주기 분모는 회의가 정한 1건 그대로
    assert [(c.desc, c.verify) for c in ms.locked_criteria] == [
        ("금지 전이는 Error와 상태 불변을 보장", "node test_state_machine.js")
    ]                                                        # 상위 계약은 대체되지 않고 별도 실상태에 남음
    snap = ms_status_snapshot(f)
    assert snap["total"] == 1 and len(snap["locked_cr"]) == 1
    restored = ms_from_dict(ms_to_dict(ms))
    assert [(c.desc, c.verify) for c in restored.locked_criteria] == [
        ("금지 전이는 Error와 상태 불변을 보장", "node test_state_machine.js")
    ]

    ms.status = "done"
    ok3, note3 = register_stage(
        f, "milestone",
        "이번 주기: 확장판 최종 인수\n"
        "- 확장 API 로드 | 실증: node test_expansion.js",
        "U-052 final",
    )
    assert ok3, note3
    final_ms = f.milestones[-1]
    assert [(c.desc, c.verify, c.release_lock) for c in final_ms.criteria] == [
        ("확장 API 로드", "node test_expansion.js", False),
        ("금지 전이는 Error와 상태 불변을 보장", "node test_state_machine.js", True),
    ]                                                        # 최종 주기에서만 상위 계약이 실제 분모
    assert ms_status_snapshot(f)["total"] == 2


def test_GOAL_정본과_부분저장잠금은_합집합이고_같은desc는_정본verify우선(tmp_path):
    """부분 복원 locked refs가 하나 있다는 이유로 최신 GOAL의 나머지 조건을 가리지 않는다."""
    import types
    from system.rule.milestone import Criterion

    f = _flow()
    f.workspace = str(tmp_path)
    f.current = types.SimpleNamespace(
        acceptance=(
            "- 계약 A | 실증: python3 verify_a.py\n"
            "- 계약 B | 실증: python3 verify_b.py"
        ),
        status=types.SimpleNamespace(goal="g"),
    )
    f.roadmap = ["중간", "최종"]
    first = open_milestone(f, "중간", [{"desc": "중간 산출물", "verify": "python3 mid.py"}])
    first.locked_criteria = [
        Criterion("계약 A", "python3 stale_a.py"),       # 같은 desc의 낡은 verify
        Criterion("구 저장 전용 계약", "python3 legacy.py"),
    ]
    first.status = "done"
    final = open_milestone(f, "최종", [{"desc": "최종 산출물", "verify": "python3 final.py"}])

    expected = [
        ("계약 A", "python3 verify_a.py"),
        ("계약 B", "python3 verify_b.py"),
        ("구 저장 전용 계약", "python3 legacy.py"),
    ]
    assert [(c.desc, c.verify) for c in final.locked_criteria] == expected
    assert [(c.desc, c.verify) for c in first.locked_criteria] == expected
    assert [(c.desc, c.verify) for c in final.criteria if c.release_lock] == expected


def test_회의산물_백로그도_주인을_갖고_태어난다(monkeypatch):
    """[무주 자기선택(2026-07-16, 사용자 추궁: '전문가가 올려둔 걸 채가나?')] 배분은 수행자=제출자
    고정이라 자기 등재분은 못 채간다 — 그런데 백로그 회의 수렴안이 만든 팀 산물(submitter=0)은 그
    규칙으로 수행자=SYS(0)가 되어 배분이 깨졌다(회의→릴레이 접합 결함). 무주 항목은 '집는 사람이
    한다'(자기선택)로 전담이 붙고, 남의 제출분 채가기는 여전히 구조적으로 불가."""
    import types
    from system.rule.milestone import register_stage
    from system.rule.backlog import relay_for, BacklogError
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    f.current = types.SimpleNamespace(task_id="T1", team=[11, 12],
                                      status=types.SimpleNamespace(goal="g", purpose=""),
                                      acceptance="", standard="", interfaces="")
    register_stage(
        f, "milestone",
        "이번 주기: MVP\n동작 | 실증: python3 verify_mvp.py",
    )
    ms = [m for m in f.milestones if m.status not in ("done", "superseded")][0]
    register_stage(f, "subtask", "단위: 백엔드 | curl 확인")
    st = ms.subtasks[0]
    ok, _ = register_stage(f, "backlog", "백로그: API 구현\n백로그: 테스트 작성")
    assert ok
    r = relay_for(f, st)
    b = r.backlogs[0]
    # [2026-07-20 재결정(사용자: '선점 대기는 불가능 — 애초에 주인이 있어야')] 무주 출생 금지:
    # 귀속 실패분은 적임(role_fit) 지정 주인으로 태어난다. 자기선택·채가기 불가 불변은 그대로.
    assert int(b.submitter) in (11, 12)                  # 무주(0) 출생 금지 — 지정 주인
    r.pick(12, b.backlog_id, 12)                         # 집는 자기선택은 여전히 열려 있음
    assert b.assignee == 12 and b.status == "in_progress"  # 전담 = 집는 사람
    # 채가기 불가 불변: 봇 11이 12의 자기등재분을 못 가져감
    mine = r.submit(12, "내 도메인 정리", force=True)
    r.done(12, b.backlog_id, "완료")                     # 순차 잠금 해제
    try:
        r.pick(11, mine.backlog_id, 11)                  # 남의 제출분을 자기에게 — 거부돼야
        assert False, "남의 제출분 채가기가 허용됨"
    except BacklogError:
        pass


def test_백로그회의_발제자_귀속과_무주출생금지(monkeypatch):
    """[발제자=주인(2026-07-16, 사용자)] 회의 DRAFT에 그 줄을 쓴 봇(SYS가 턴별 diff로 귀속)이 등록 시
    제출자가 된다 — 수행자=제출자 원칙이 회의 경로에도 이어짐. 귀속 없는 줄은 무주(자기선택 폴백)."""
    import types
    from system.rule.milestone import register_stage
    from system.rule.backlog import relay_for
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    f.current = types.SimpleNamespace(task_id="T1", team=[11, 12],
                                      status=types.SimpleNamespace(goal="g", purpose=""),
                                      acceptance="", standard="", interfaces="")
    register_stage(
        f, "milestone",
        "이번 주기: MVP\n동작 | 실증: python3 verify_mvp.py",
    )
    register_stage(f, "subtask", "단위: 백엔드 | curl 확인")
    st = [m for m in f.milestones if m.status not in ("done", "superseded")][0].subtasks[0]
    f._draft_attr = {"백로그: API 구현": 12}          # 봇 12가 초안에 쓴 줄(diff 귀속)
    ok, _ = register_stage(f, "backlog", "백로그: API 구현\n백로그: 문서 정리")
    assert ok
    bl = relay_for(f, st).backlogs
    assert bl[0].submitter == 12                      # 발제자=주인으로 남음
    # [2026-07-20 재결정] 귀속 없는 줄도 무주(0)가 아니라 적임 지정 주인으로 — 무주 출생 금지
    assert int(bl[1].submitter) in (11, 12)


def test_백로그_소진되고_주기_미완이면_추가분해회의가_체인에_잡힌다(monkeypatch):
    """[백로그 소진=회의 트리거의 체인 편입(2026-07-16, 잔재 감사)] 전 단위 백로그가 소진(전부 종결)
    됐는데 주기가 미완이면 meeting_stage가 'subtask'(추가 분해 회의) — 종전엔 handoff 코칭만 있고
    stage=None이라 봇이 meet를 불러도 결론 경로가 없었다."""
    import types
    from system.rule.milestone import meeting_stage, register_stage
    from system.rule.backlog import relay_for
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    f.current = types.SimpleNamespace(task_id="T1", team=[11, 12],
                                      status=types.SimpleNamespace(goal="g", purpose=""),
                                      acceptance="", standard="", interfaces="")
    register_stage(
        f, "milestone",
        "이번 주기: MVP\n동작 | 실증: python3 verify_mvp.py",
    )
    register_stage(f, "subtask", "단위: 백엔드 | curl 확인")
    st = [m for m in f.milestones if m.status not in ("done", "superseded")][0].subtasks[0]
    register_stage(f, "backlog", "백로그: API 구현")
    assert meeting_stage(f) is None                      # 미처리 백로그 존재 → 작업 단계
    r = relay_for(f, st)
    b = r.backlogs[0]
    r.pick(12, b.backlog_id, 12); r.done(12, b.backlog_id, "완료")
    assert meeting_stage(f) == "subtask"                 # 소진+주기 미완 → 추가 분해 회의(체인 자동)


# ── [2026-07-17 ch78 실측 회귀 — 조건 파서·게이트 계약] ─────────────────────────
# 라이브 5h·$45 소진 원인 3종: ①본문 파이프를 구분자로 오인 ②게이트 fail-fast(사이클당 1건 수리)
# ③볼드 키 매칭 실패(폴백 제목 오등록). 오프라인 예행(rehearse3)으로 확정한 계약을 고정한다.

def test_조건구분자는_라벨_본문파이프는_조건아님():
    from system.rule.milestone import draft_norm_line, parse_criteria_lines
    # 라벨 변형('검증:'·'로그 검증:')은 제거가 아니라 '| 실증:'으로 정본화 — 선별·분리·귀속 단일 토큰
    assert "| 실증: " in draft_norm_line("- 조건 A | 검증: pytest tests/a")
    assert "| 실증: " in draft_norm_line("- 조건 B | 로그 검증: python cmp.py")
    # 조건 본문의 붙은 파이프(JSON enum)는 구분자가 아니다 — desc에 그대로 남고, 분리는 라벨/띄어쓴 파이프에서
    c = parse_criteria_lines("스키마 enum(buy|pass|claim) 유지 | 실증: python check.py --strict")[0]
    assert c["desc"] == "스키마 enum(buy|pass|claim) 유지" and "python check.py" in c["verify"]
    wrapped = parse_criteria_lines(
        "- **홈 화면이 실제로 열린다** | 실증: python3 browser_check.py"
    )[0]
    assert wrapped["desc"] == "홈 화면이 실제로 열린다"
    internal = parse_criteria_lines(
        "- API는 **transition(nextState)**만 노출한다 | 실증: node test.js"
    )[0]
    assert internal["desc"] == "API는 **transition(nextState)**만 노출한다"


def test_마크다운_inline_exact_verifier는_프리플라이트통과_내부backtick은_거부():
    """U-056: 템플릿대로 exact command를 inline-code로 써도 통과하되 명령 내부 backtick은 못 숨긴다."""
    from system.rule.evidence import direct_verifier_command
    from system.rule.milestone import parse_criteria_lines, stage_preflight

    line = "- 상태 전이 계약 통과 | 실증: `node test_state_machine.js`"
    criterion = parse_criteria_lines(line)[0]
    assert criterion["verify"] == "`node test_state_machine.js`"
    assert direct_verifier_command(
        criterion["verify"], require_existing=False
    ) == "node test_state_machine.js"

    draft = (
        "# DRAFT [stage:milestone] — 상태 머신\n"
        "## 결정\n"
        "이번 주기: 상태 전이 구현\n"
        f"{line}\n\n"
        "## 참고 (자유 — 판정 대상 아님)\n"
    )
    assert stage_preflight("milestone", draft) == []

    assert direct_verifier_command(
        "node test_state_machine.js `whoami`", require_existing=False
    ) == ""
    assert direct_verifier_command(
        "`node test_state_machine.js `whoami``", require_existing=False
    ) == ""


def test_U057_장문_GOAL_3건은_정본marker로_최종비준되고_등록후_identity가복원된다(
    monkeypatch, tmp_path,
):
    """LLM이 장문 desc를 복사하지 않아도 final DRAFT의 1:1 marker가 canonical 잠금/receipt key가 된다."""
    import types
    from system.rule.evidence import verifier_command_hash, verifier_spec_hash
    from system.rule.milestone import (
        draft_to_proposal, ensure_goal_ratification_scaffold, register_stage,
        stage_preflight,
    )

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    command = "node test_state_machine.js"
    descs = [
        (
            "프로젝트 루트의 사용자 산출물은 CommonJS state_machine.js와 "
            "test_state_machine.js 정확히 두 파일이며 시스템 내부 기록 외 추가 파일은 없어야 한다"
        ),
        (
            "createStateMachine이 반환한 객체의 공개 API는 인자 없는 getState()와 "
            "**transition(nextState)**뿐이고 초기 상태 idle 및 세 허용 전이 계약을 보존한다"
        ),
        (
            "네 상태의 16개 순서쌍 중 허용 전이 세 개 외 나머지 13개는 모두 Error를 throw하고 "
            "현재 상태를 바꾸지 않으며 성공 시 PASS와 exit 0을 남긴다"
        ),
    ]
    specs = [
        f"프로젝트 루트에서 `{command}`를 실행하고 파일 수와 프로세스 종료코드를 함께 확인한다",
        f"외부 의존성 없는 `{command}`를 실행해 공개 API와 초기·허용 전이 assertion을 확인한다",
        f"각 순서쌍을 독립 실행하는 `{command}`의 PASS 출력과 exit 0으로 금지 전이 불변을 확인한다",
    ]
    f = _flow()
    f.workspace = str(tmp_path)
    f.current = types.SimpleNamespace(
        task_id="T-U057", team=[11, 12],
        status=types.SimpleNamespace(goal="CommonJS 상태 머신", purpose="최종 인수"),
        acceptance="\n".join(
            f"- {desc} | 실증: {spec}" for desc, spec in zip(descs, specs)
        ),
        standard="", interfaces="",
    )
    draft = (
        "# DRAFT [stage:milestone] — 상태 머신 최종 인수\n"
        "## 결정\n"
        "단계: 완성\n"
        "이번 주기: 네 상태의 공개 API와 16개 전이 계약 완성\n\n"
        "## 참고 (자유 — 판정 대상 아님)\n"
    )

    scaffolded = ensure_goal_ratification_scaffold(f, draft)
    markers = [
        "GOAL@" + verifier_spec_hash(desc, spec)
        for desc, spec in zip(descs, specs)
    ]
    for marker, desc in zip(markers, descs):
        assert f"- {marker} | 실증: {command}" in scaffolded
        assert f"  - {marker} — {desc}" in scaffolded
    assert scaffolded.count("| 실증: " + command) == 3       # 동일 명령이어도 조건별 1:1 명시 결속
    assert "⟦exact command⟧" not in scaffolded
    assert ensure_goal_ratification_scaffold(f, scaffolded) == scaffolded
    assert stage_preflight("milestone", scaffolded, f) == []

    proposal = draft_to_proposal("milestone", scaffolded)
    ok, note = register_stage(f, "milestone", proposal, "U-057")
    assert ok, note
    ms = f.milestones[-1]
    assert [(c.desc, c.verify) for c in ms.locked_criteria] == list(zip(descs, specs))
    assert [(c.desc, c.verify) for c in ms.criteria] == list(zip(descs, specs))
    assert all(c.release_lock for c in ms.criteria)
    assert all(not c.desc.startswith("GOAL@") for c in ms.criteria + ms.locked_criteria)
    for criterion, locked, desc, spec in zip(
        ms.criteria, ms.locked_criteria, descs, specs
    ):
        expected_spec_hash = verifier_spec_hash(desc, spec)
        assert criterion.ratified_verifier_command == command
        assert criterion.ratified_verifier_command_hash == verifier_command_hash(command)
        assert criterion.ratified_verifier_spec_hash == expected_spec_hash
        assert locked.ratified_verifier_command == command
        assert locked.ratified_verifier_spec_hash == expected_spec_hash
        assert criterion.receipt_id == locked.receipt_id == ""  # 실행 전 receipt 참칭 없음
        assert criterion.verified_spec_hash == locked.verified_spec_hash == ""

    from system.rule.milestone import ms_from_dict, ms_to_dict
    restored = ms_from_dict(ms_to_dict(ms))
    assert [
        (
            c.desc, c.verify, c.release_lock,
            c.ratified_verifier_command,
            c.ratified_verifier_command_hash,
            c.ratified_verifier_spec_hash,
        )
        for c in restored.criteria
    ] == [
        (
            c.desc, c.verify, c.release_lock,
            c.ratified_verifier_command,
            c.ratified_verifier_command_hash,
            c.ratified_verifier_spec_hash,
        )
        for c in ms.criteria
    ]


def test_GOAL_marker_변조와_모호한_inline명령은_fail_closed(monkeypatch, tmp_path):
    import types
    from system.rule.evidence import verifier_spec_hash
    from system.rule.milestone import (
        draft_decision_region, ensure_goal_ratification_scaffold, stage_preflight,
    )

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    desc = "정본 조건은 유사하거나 합성한 설명으로 대체되지 않고 정확한 상태 전이 계약을 보존한다"
    natural = "완성 뒤 실제 상태 전이를 독립적으로 실행해 결과와 종료코드를 확인한다"
    command = "node test_state_machine.js"
    f = _flow()
    f.workspace = str(tmp_path)
    f.current = types.SimpleNamespace(
        task_id="T-marker-tamper", team=[11, 12],
        status=types.SimpleNamespace(goal="상태 머신", purpose=""),
        acceptance=f"- {desc} | 실증: {natural}",
        standard="", interfaces="",
    )
    draft = (
        "# DRAFT [stage:milestone] — 최종 인수\n"
        "## 결정\n"
        "단계: 완성\n"
        "이번 주기: 상태 머신 완성\n\n"
        "## 참고 (자유 — 판정 대상 아님)\n"
    )
    scaffolded = ensure_goal_ratification_scaffold(f, draft)
    marker = "GOAL@" + verifier_spec_hash(desc, natural)
    valid = scaffolded.replace("⟦exact command⟧", command)
    assert stage_preflight("milestone", valid, f) == []

    uppercase = ensure_goal_ratification_scaffold(
        f, valid.replace(marker, marker.upper(), 1)
    )
    assert uppercase.count("| 실증: " + command) == 1
    assert stage_preflight("milestone", uppercase, f) == []

    invalid = valid.replace(marker, "GOAL@ALL", 1)
    # 실제 meet 순서가 ensure→preflight이므로 변조를 자동치유해 숨기면 안 된다.
    invalid = ensure_goal_ratification_scaffold(f, invalid)
    assert "유효하지 않은 GOAL marker" in "\n".join(
        stage_preflight("milestone", invalid, f)
    )

    truncated = valid.replace(marker, "GOAL@" + marker[5:21], 1)
    truncated = ensure_goal_ratification_scaffold(f, truncated)
    assert "유효하지 않은 GOAL marker" in "\n".join(
        stage_preflight("milestone", truncated, f)
    )

    unknown = valid.replace(marker, "GOAL@" + "0" * 64, 1)
    assert "알 수 없거나 낡은" in "\n".join(
        stage_preflight("milestone", unknown, f)
    )

    marker_line = f"- {marker} | 실증: {command}"
    moved_to_reference = valid.replace(marker_line + "\n", "", 1).replace(
        "## 참고 (자유 — 판정 대상 아님)\n",
        "## 참고 (자유 — 판정 대상 아님)\n" + marker_line + "\n",
        1,
    )
    moved_to_reference = ensure_goal_ratification_scaffold(f, moved_to_reference)
    assert (
        f"- {marker} | 실증: ⟦exact command⟧"
        in draft_decision_region(moved_to_reference)
    )
    moved_filled = moved_to_reference.replace("⟦exact command⟧", command, 1)
    assert stage_preflight("milestone", moved_filled, f) == []

    duplicate = valid.replace(marker_line, marker_line + "\n" + marker_line)
    assert "중복됐습니다" in "\n".join(
        stage_preflight("milestone", duplicate, f)
    )
    block_start = valid.index("<!-- SYS:GOAL-RATIFICATION:START -->")
    block_end = (
        valid.index("<!-- SYS:GOAL-RATIFICATION:END -->", block_start)
        + len("<!-- SYS:GOAL-RATIFICATION:END -->")
    )
    block = valid[block_start:block_end]
    duplicate_blocks = ensure_goal_ratification_scaffold(
        f, valid.replace(block, block + "\n" + block)
    )
    assert "중복됐습니다" in "\n".join(
        stage_preflight("milestone", duplicate_blocks, f)
    )
    malformed = valid.replace(
        "## 결정\n",
        "## 결정\n<!-- SYS:GOAL-RATIFICATION:START -->\n"
        "- 일반 완수조건 보존 | 실증: node test_state_machine.js\n",
        1,
    )
    assert ensure_goal_ratification_scaffold(f, malformed) == malformed
    assert "- 일반 완수조건 보존 | 실증: node test_state_machine.js" in malformed
    assert "START가 닫히기 전에 중복" in "\n".join(
        stage_preflight("milestone", malformed, f)
    )

    injected = valid.replace(command, command + " `whoami`", 1)
    injected_errors = "\n".join(stage_preflight("milestone", injected, f))
    assert "exact executable verifier command" in injected_errors

    f.current.acceptance = (
        f"- {desc} 변경 | 실증: {natural}"
    )
    assert "알 수 없거나 낡은" in "\n".join(
        stage_preflight("milestone", valid, f)
    )

    f.current.acceptance = (
        f"- {desc} | 실증: `{command}`와 `npm test`를 각각 실행해 비교한다"
    )
    ambiguous = ensure_goal_ratification_scaffold(f, draft)
    assert "⟦exact command⟧" in ambiguous

    f.current.acceptance = ""
    alternate = valid.replace(command, "npm test", 1)
    assert ensure_goal_ratification_scaffold(f, alternate) == alternate


def test_GOAL_marker는_결정구획의_첫_후속heading보다_앞에_들어간다(monkeypatch, tmp_path):
    import types
    from system.rule.milestone import (
        draft_decision_region, ensure_goal_ratification_scaffold, stage_preflight,
    )

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    f.workspace = str(tmp_path)
    f.current = types.SimpleNamespace(
        task_id="T-heading", team=[11, 12],
        status=types.SimpleNamespace(goal="상태 머신", purpose=""),
        acceptance=(
            "- 공개 API 계약을 보존한다 | 실증: "
            "루트에서 `node test_state_machine.js`를 실행한다"
        ),
        standard="", interfaces="",
    )
    draft = (
        "# DRAFT [stage:milestone] — 최종 인수\n"
        "## 결정\n"
        "단계: 완성\n"
        "이번 주기: 상태 머신 완성\n\n"
        "## 검토\n"
        "참여자 검토 근거\n\n"
        "## 참고 (자유 — 판정 대상 아님)\n"
    )
    scaffolded = ensure_goal_ratification_scaffold(f, draft)
    assert "GOAL@" in draft_decision_region(scaffolded)
    assert scaffolded.index("GOAL@") < scaffolded.index("## 검토")
    assert stage_preflight("milestone", scaffolded, f) == []


def test_GOAL_marker는_final확정전에_주입되지않고_해소된_구시스템이의만정리한다(
    monkeypatch, tmp_path,
):
    import types
    from system.rule.milestone import (
        clear_resolved_goal_ratification_objection,
        ensure_goal_ratification_scaffold, stage_preflight,
    )

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    command = "node test_state_machine.js"
    desc = "네 상태의 공개 API와 16개 전이 계약을 장문 정본 그대로 보존하고 금지 전이 상태 불변을 보장한다"
    f = _flow()
    f.workspace = str(tmp_path)
    f.current = types.SimpleNamespace(
        task_id="T-U057-resume", team=[11, 12],
        status=types.SimpleNamespace(goal="상태 머신", purpose=""),
        acceptance=(
            f"- {desc} | 실증: 루트에서 `{command}`를 실행해 모든 전이를 확인한다"
        ),
        standard="", interfaces="",
    )
    first_cycle = (
        "# DRAFT [stage:milestone] — 로드맵\n"
        "## 결정\n"
        "단계: 구현 → 완성\n"
        "이번 주기: 상태 머신 구현\n"
        "- 최소 구현 로드 | 실증: node test_state_machine.js\n\n"
        "## 참고 (자유 — 판정 대상 아님)\n"
    )
    assert ensure_goal_ratification_scaffold(f, first_cycle) == first_cycle
    assert stage_preflight("milestone", first_cycle, f) == []

    unresolved = first_cycle.replace("단계: 구현 → 완성", "단계: ⟦최대 3단계⟧")
    assert ensure_goal_ratification_scaffold(f, unresolved) == unresolved

    final_cycle = first_cycle.replace(
        "단계: 구현 → 완성\n이번 주기: 상태 머신 구현\n"
        "- 최소 구현 로드 | 실증: node test_state_machine.js",
        "단계: 완성\n이번 주기: 상태 머신 최종 인수",
    )
    resolved = ensure_goal_ratification_scaffold(f, final_cycle)
    expanded_nonfinal = resolved.replace(
        "단계: 완성\n이번 주기: 상태 머신 최종 인수",
        "단계: 구현 → 완성\n이번 주기: 상태 머신 구현\n"
        "- 최소 구현 로드 | 실증: node test_state_machine.js",
    )
    expanded_nonfinal = ensure_goal_ratification_scaffold(f, expanded_nonfinal)
    assert "SYS:GOAL-RATIFICATION" not in expanded_nonfinal
    assert stage_preflight("milestone", expanded_nonfinal, f) == []

    old_system = (
        "> [이의 @형식] 최종 마일스톤은 자연어 GOAL 조건과 같은 desc의 exact command 비준이 필요합니다.\n"
    )
    resumed = resolved.replace(
        "\n## 참고",
        "\n" + old_system + "> [이의 @사람] 공개 API 설명을 다시 확인하세요.\n\n## 참고",
    )
    cleaned, changed = clear_resolved_goal_ratification_objection(
        f, resumed, "milestone"
    )
    assert changed is True
    assert old_system.strip() not in cleaned
    assert "> [이의 @사람] 공개 API 설명을 다시 확인하세요." in cleaned

    no_inline = types.SimpleNamespace(**vars(f.current))
    no_inline.acceptance = f"- {desc} | 실증: 완성 뒤 수동으로 모든 전이를 확인한다"
    f.current = no_inline
    placeholder = ensure_goal_ratification_scaffold(f, final_cycle)
    assert "⟦exact command⟧" in placeholder
    still_open = placeholder.replace("\n## 참고", "\n" + old_system + "\n## 참고")
    unchanged, changed = clear_resolved_goal_ratification_objection(
        f, still_open, "milestone"
    )
    assert changed is False and unchanged == still_open


def test_GOAL_marker는_legacy_bold_desc를_중복조건으로_만들지않는다(monkeypatch, tmp_path):
    import types
    from system.rule.milestone import (
        draft_to_proposal, ensure_goal_ratification_scaffold, register_stage,
        stage_preflight,
    )

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    desc = "공개 API는 **transition(nextState)**만 노출하고 호출 전후 상태를 정확히 보존한다"
    natural = "공개 API와 상태 보존을 실행 결과로 확인한다"
    command = "node test_state_machine.js"
    f = _flow()
    f.workspace = str(tmp_path)
    (tmp_path / "test_state_machine.js").write_text(
        "console.log('PASS')\n", encoding="utf-8")
    f.current = types.SimpleNamespace(
        task_id="T-bold-legacy", team=[11, 12],
        status=types.SimpleNamespace(goal="상태 머신", purpose=""),
        acceptance=f"- {desc} | 실증: {natural}",
        standard="", interfaces="",
    )
    legacy = (
        "# DRAFT [stage:milestone] — 재개\n"
        "## 결정\n"
        "**단계:** 완성\n"
        "**이번 주기:** 상태 머신 완성\n"
        f"- {desc} | 실증: {command}\n\n"
        "## 참고 (자유 — 판정 대상 아님)\n"
    )
    scaffolded = ensure_goal_ratification_scaffold(f, legacy)
    assert stage_preflight("milestone", scaffolded, f) == []
    ok, note = register_stage(
        f, "milestone", draft_to_proposal("milestone", scaffolded), "bold-legacy"
    )
    assert ok, note
    ms = f.milestones[-1]
    assert [(c.desc, c.verify) for c in ms.criteria] == [(desc, natural)]
    assert ms.criteria[0].ratified_verifier_command == command

    from system.rule.evidence import verifier_command_hash, verifier_spec_hash
    from system.rule.milestone import (
        goal_locked_release_error, workspace_artifact_stamp, write_revision,
    )
    c = ms.criteria[0]
    c.passed, c.evidence = True, "PASS"
    c.evidence_source, c.receipt_id = "sys_run", "run-marker"
    c.verified_write_epoch = write_revision(f)
    c.verified_artifact_stamp = workspace_artifact_stamp(f)
    c.verified_command = command
    c.verified_command_hash = verifier_command_hash(command)
    c.verified_spec_hash = verifier_spec_hash(c.desc, c.verify)
    assert goal_locked_release_error(f) is None

    c.ratified_verifier_command_hash = "tampered"
    assert "GOAL 잠금 조건 실증 미완" in goal_locked_release_error(f)
    assert not c.passed and c.receipt_id == ""


def test_GOAL_MD_표결원문의_whole_bold_desc도_canonical_marker를_오염시키지않는다(
    monkeypatch, tmp_path,
):
    import types
    from system.rule.evidence import verifier_spec_hash
    from system.rule.milestone import (
        _goal_acceptance_entries, ensure_goal_ratification_scaffold, register_stage,
    )

    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    desc = "홈 화면이 실제로 열린다"
    spec = "브라우저에서 `python3 browser_check.py`로 제목과 오류 0건을 확인한다"
    f = _flow()
    f.workspace = str(tmp_path)
    f.current = types.SimpleNamespace(
        task_id="T-bold-goal-doc", team=[11, 12],
        status=types.SimpleNamespace(goal="", purpose=""),
        acceptance="", standard="", interfaces="",
    )
    ok, note = register_stage(
        f, "goal",
        f"목표: 홈 완성\n- **{desc}** | 실증: {spec}",
        "whole-bold-goal",
    )
    assert ok, note
    assert _goal_acceptance_entries(f) == [{"desc": desc, "verify": spec}]

    draft = (
        "# DRAFT [stage:milestone] — 홈 최종 인수\n"
        "## 결정\n"
        "단계: 완성\n"
        "이번 주기: 홈 완성\n\n"
        "## 참고 (자유 — 판정 대상 아님)\n"
    )
    scaffolded = ensure_goal_ratification_scaffold(f, draft)
    marker = "GOAL@" + verifier_spec_hash(desc, spec)
    assert f"- {marker} | 실증: python3 browser_check.py" in scaffolded
    assert desc + "**" not in scaffolded


def test_게이트는_불량을_일괄보고():
    bad = [{"desc": "카운터 API", "verify": "확인한다"},
           {"desc": "버튼 UI", "verify": "잘 살펴본다"}]
    e = gate_criteria(bad)
    assert e and "2건" in e and "카운터 API" in e and "버튼 UI" in e   # fail-fast 아님


def test_등록_볼드키_파싱과_프리플라이트_동일계약():
    from system.rule.milestone import register_stage, stage_preflight
    f = _flow()
    ok, _ = register_stage(f, "goal", "목표: 카운터 앱\n- 웹 로드 | 실증: curl -s localhost:3000", "t")
    assert ok
    prop = ("**이번 주기:** 카운터 1주기\n"
            "- 스키마 enum(a|b) 유지 | 검증: pytest tests/schema\n"
            "산문 설명에 붙은 파이프 a|b 있어도 조건 아님\n")
    assert stage_preflight("milestone", "## 결정\n\n" + prop + "\n## 참고 (자유 — 판정 대상 아님)\n") == []
    ok2, note = register_stage(f, "milestone", prop.replace("**", ""), "t")
    assert ok2, note
    ms = f.milestones[-1]
    assert ms.goal == "카운터 1주기"            # 볼드 키·폴백 오등록 아님
    assert len(ms.criteria) == 1                # 산문(붙은 파이프) 줄은 조건으로 미등록


def test_들여쓴_하위설명줄은_조건이_아니다():
    from system.rule.milestone import draft_to_proposal
    d = ("## 결정\n\n이번 주기: X\n"
         "- 조건 A | 실증: pytest tests/a\n"
         "  ⑥하위 설명: 명도 대비 5.5:1 | 【각주】\n"      # 들여쓴 연속 줄 — 조건 오인 금지
         "  + 협의사항: ease curve 3자 협의 | 검증: 협의 기록\n"
         "\n## 참고 (자유 — 판정 대상 아님)\n")
    prop = draft_to_proposal("milestone", d)
    assert "조건 A" in prop and "하위 설명" not in prop and "협의사항" not in prop


def test_작업단계에는_meet가_거부되고_릴레이로_코칭(monkeypatch):
    """[ch78 실측(2026-07-18)] 백로그가 서 있는 작업 단계의 회의는 단계 None 자유 회의 — 등록 경로
    없이 예산만 소모(재시작 복원·봇 습관 양쪽 반복). '회의 하나=결론 하나': 정할 게 없으면 릴레이."""
    import asyncio as _aio
    import types
    from system.rule.communication import meet
    from system.rule.milestone import open_milestone, open_subtask
    from system.rule.backlog import BacklogRelay
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    f.current = types.SimpleNamespace(team=[11, 12], task_id="T1",
                                      status=types.SimpleNamespace(goal="목표"))
    ms = open_milestone(f, "M1", [{"desc": "빌드", "verify": "npm run build 0"}])
    st = open_subtask(f, ms, "로직", [{"desc": "로드", "verify": "curl /"}])
    r = BacklogRelay(subtask_id=st.st_id)
    r.submit(12, "카드 상수 정의")                      # 집을 백로그 존재 → 작업 단계
    f.backlog_relays = {st.st_id: r}
    out = _aio.run(meet(f, 11, {"topic": "그냥 회의"}))
    assert "작업 단계" in out and "pick_backlog" in out   # 회의 거부 + 릴레이 코칭


def test_표결_파서_엄격화():
    """[감사 2026-07-18] '반대 없습니다'는 찬성, 비확답은 기권(찬성 오집계 아님)."""
    from system.rule.comm_helpers import _classify_vote
    assert _classify_vote("[찬성]") == "for"
    assert _classify_vote("반대 없습니다") == "for"          # 부정 = 찬성
    assert _classify_vote("반대할 이유 없이 동의합니다") == "for"
    assert _classify_vote("[반대: 스키마 빠짐]") == "against"
    assert _classify_vote("반대합니다") == "against"
    assert _classify_vote("자료 검토했습니다 좋아보여요") == "abstain"   # 비확답 → 기권(찬성 아님)
    assert _classify_vote("") == "abstain"


def test_조건재협상_교착출구_왕복():
    """[감사 2026-07-18] renegotiate→escalate(사람 통지)→blocked→approve_waiver→waived.
    종전 escalate_to_human=None이라 무통지 교착이던 것 배선 회귀."""
    from system.rule.milestone import open_milestone, renegotiate_criterion, approve_waiver
    f = _flow()
    ms = open_milestone(f, "M1", [{"desc": "배포 200", "verify": "curl -s localhost/"},
                                  {"desc": "결제 연동", "verify": "grep paid cfg.json"}])
    esc = []
    f.escalate_to_human = lambda m: (setattr(f, "awaiting_human", m), esc.append(m))
    renegotiate_criterion(f, ms, "결제 연동", "유료 계정 없음")
    assert esc and f.awaiting_human                          # 사람에게 통지됨
    c = next(x for x in ms.criteria if "결제" in x.desc)
    assert c.status == "blocked_pending"
    approve_waiver(f, ms, "결제 연동", approve=True)
    assert c.status == "waived" and f.awaiting_human is None  # 승인 반영 + 대기 해제


def test_사람대기_파생_재시작에도_승인경로_유지():
    """[검수 2026-07-18] awaiting_human(휘발 플래그)은 재시작에 비지만, blocked_pending 조건은
    체크포인트를 넘는다 — 대기 판정·HUD 노출을 조건에서 파생해 재시작 후 승인도 반영되게."""
    from system.rule.milestone import (open_milestone, renegotiate_criterion, approve_waiver,
                                       ms_to_dict, ms_from_dict, pending_waivers, ms_status_snapshot)
    f = _flow()
    ms = open_milestone(f, "M1", [{"desc": "결제 연동", "verify": "grep paid cfg.json"},
                                  {"desc": "배포 200", "verify": "curl -s localhost/"}])
    renegotiate_criterion(f, ms, "결제 연동", "유료 계정 없음")
    # 러너 재시작 시뮬레이션: 조건은 ckpt 왕복 복원, 플래그는 증발
    f2 = _flow()
    f2.milestones = [ms_from_dict(ms_to_dict(m)) for m in f.milestones]
    f2.awaiting_human = None
    pw = pending_waivers(f2)
    assert pw and "결제" in pw[0].desc                          # 파생 대기 감지
    snap = ms_status_snapshot(f2)
    assert snap and snap.get("awaiting_human") and "결제" in snap["awaiting_human"]   # HUD 파생 노출
    approve_waiver(f2, f2.milestones[0], "결제 연동", approve=True)
    assert not pending_waivers(f2)                              # 승인 반영 — 대기 자연 소멸
    assert ms_status_snapshot(f2).get("awaiting_human") is None


def test_사람답변_파서_부정문은_승인아님():
    """[검수 2026-07-18] 광역 `'승인' in text`가 '승인 안 할게요'까지 전 조건 waive하던 것 —
    부정문은 무동작, 반려·거부는 deny."""
    from system.rule.milestone import parse_waiver_reply
    assert parse_waiver_reply("조건 승인") == "approve"
    assert parse_waiver_reply("포기 승인할게요") == "approve"
    assert parse_waiver_reply("승인합니다") == "approve"
    assert parse_waiver_reply("승인 안 할게요") is None
    assert parse_waiver_reply("아직 승인하지 마") is None
    assert parse_waiver_reply("승인 보류할게") is None
    assert parse_waiver_reply("조건 반려") == "deny"
    assert parse_waiver_reply("거부할게요") == "deny"
    assert parse_waiver_reply("승인 말고 반려로 해줘") == "deny"
    assert parse_waiver_reply("이 조건이 왜 안 되는지 설명해줘") is None


def test_표결_파서_찬성부정은_찬성아님():
    """[검수 2026-07-18] '찬성하지 않/할 수 없'이 선두 '찬성' 토큰으로 for 오집계되던 것 — 기권.
    유보 찬성('찬성하지만 …')은 그대로 찬성."""
    from system.rule.comm_helpers import _classify_vote
    assert _classify_vote("찬성하지 않습니다") == "abstain"
    assert _classify_vote("찬성할 수 없습니다") == "abstain"
    assert _classify_vote("찬성하기 어렵습니다") == "abstain"
    assert _classify_vote("찬성하지만 일정 우려 있습니다") == "for"
    assert _classify_vote("[찬성] 스키마 확인했습니다") == "for"


def test_e2e통과는_실행사실을_장부에_남긴다(monkeypatch):
    """[2026-07-27 U-065 실측] 마감 관문은 '이 흐름에서 산출물을 run으로 실제 실행했는가'를 요구하고,
    재개 때 그 표식을 0으로 되돌린다(옳은 원칙). 그런데 Task 경계 e2e가 바로 그 재실행을 이미 한다 —
    전 항목을 exact command로 돌려 SYS receipt를 받아야만 e2e_pass가 선다. 그 사실을 안 남겨,
    다 만들고 검증까지 끝낸 판이 '한 번도 실행하지 않았습니다'로 여섯 번 연속 못 닫혔다."""
    import inspect
    from system.rule import wrapup
    src = inspect.getsource(wrapup.rule_e2e_finish)
    assert "verified = True" in src, "e2e_pass가 실행 사실을 기록하지 않는다"
    # 허위 완료 경로가 열리지 않는지: 증거 없는 pass는 애초에 e2e_pass가 아니다(문서화된 불변식).
    assert "증거 없는 pass는 pass가 아니다" in inspect.getsource(wrapup)


def test_GOAL잠금_재개방이_반복되면_사람에게_넘긴다(monkeypatch):
    """[2026-07-27 U-067 실측] 잠금 조건의 영수증은 '비준 명령으로 검증됐을 때만' 유효한데, 검증이
    다른 명령으로 돌면 통과해도 무효라 주기가 다시 열린다 — 재개방↔재검증이 끝없이 돈다(실측:
    e2e 개시 3회·통과 0, 파일 쓰기 0인 구간에도 지속). 매번 통과하는데 매번 되돌리면 그건 봇이
    풀 문제가 아니라 계약 불일치이므로, 연속 3회면 파킹 신호를 세워 사람에게 넘긴다."""
    import inspect
    from system.rule import milestone

    src = inspect.getsource(milestone.promote_final_locked_criteria)
    assert "_goal_lock_reopens" in src, "재개방 횟수를 세지 않는다"
    assert "flow._goal_lock_reopens = 0" not in src, \
        "통과 1회로 리셋하면 이 순환(매 바퀴 통과 포함)은 영원히 안 걸린다"
    from system.rule import wrapup
    assert "_goal_lock_reopens = 0" in inspect.getsource(wrapup.rule_e2e_finish), \
        "판정에 닿아도 카운터가 안 풀리면 정상 판이 파킹된다"
    assert "goal_lock_reopen_parked" in src, "반복 재개방에 파킹 신호가 없다"
    assert "_stage_stuck" in src, "파킹 신호를 세우지 않는다"


def test_검증기가_남긴_리포트는_산출물_스탬프를_바꾸지_않는다():
    """[2026-07-27 U-067 실측] 봇이 만든 검증기는 실행마다 리포트를 덮어쓴다
    (artifacts/verify_ui/latest.json). 그 파일이 authoring manifest에 섞여 **검증할 때마다 스탬프가
    바뀌었다** → 방금 선 영수증이 스스로 무효 → 주기 재개방 → 또 검증… 구조적 무한(실측: e2e 개시
    3회·통과 0, 그 사이 Write/Edit 0건). 검증 실행이 건드린 파일은 저작 입력이 아니라 스탬프 밖이다."""
    import os, tempfile
    from types import SimpleNamespace
    from system.rule.milestone import (
        record_run_outputs, workspace_artifact_stamp, workspace_file_digests)

    with tempfile.TemporaryDirectory() as ws:
        with open(os.path.join(ws, "index.html"), "w") as fp:
            fp.write("<h1>game</h1>")
        out = os.path.join(ws, "artifacts", "verify_ui")
        os.makedirs(out)
        with open(os.path.join(out, "latest.json"), "w") as fp:
            fp.write('{"ts": 0}')                    # 이전 실행이 이미 남긴 리포트
        flow = SimpleNamespace(workspace=ws, log=None, run_outputs=None)

        def _verify_run(payload):
            """검증기 흉내 — 실행할 때마다 같은 리포트 파일을 새 내용으로 덮어쓴다."""
            pre = workspace_file_digests(flow)
            with open(os.path.join(out, "latest.json"), "w") as fp:
                fp.write(payload)
            record_run_outputs(flow, pre)

        _verify_run('{"ts": 1}')
        sealed = workspace_artifact_stamp(flow)      # 영수증이 봉인되는 시점의 스탬프
        _verify_run('{"ts": 2}')
        assert workspace_artifact_stamp(flow) == sealed, \
            "검증을 다시 돌렸다고 영수증이 무효가 된다(무한 재개방의 원인)"

        # 제품이 실제로 바뀌면 여전히 무효 — 보호 성질은 유지된다
        with open(os.path.join(ws, "index.html"), "w") as fp:
            fp.write("<h1>game v2</h1>")
        assert workspace_artifact_stamp(flow) != sealed, "제품 변경이 스탬프에 안 잡힌다"


def test_봇의_임의_실행은_등재_대상이_아니다():
    """등재는 **회의가 검증 수단으로 결속한 명령**의 실행에만 건다. 봇이 임의 run으로
    (`python -c`처럼) 제품을 몰래 고치는 경로는 종전대로 스탬프가 잡아야 한다 — 원 위협 모델."""
    import inspect
    from system import guide_tools
    from system.rule.milestone import known_verifier_commands

    src = inspect.getsource(guide_tools)
    i = src.index("검증 산출 등재")
    assert "known_verifier_commands" in src[i:i + 900], \
        "임의 run까지 등재하면 간접 쓰기 우회가 열린다"

    class _F:
        e2e_checklist = [{"verifier_command": "python3 verify_ui.py --desktop-width 1280"}]
        milestones = []
        workspace = "/nonexistent"
    assert any("verify_ui.py" in c for c in known_verifier_commands(_F())), \
        "분모의 verifier를 검증 명령으로 못 읽는다"


def test_잠금조건_비준은_새_주기로_승계된다(tmp_path):
    """[2026-07-27 U-067 실측] e2e 실패로 새 주기가 열리면 GOAL 잠금 조건이 그 주기로 복사되는데,
    비준(회의가 정한 exact 검증 명령)이 따라가지 않아 새 조건이 '미비준'이 됐다. 그러면 SYS는 매
    바퀴 '비준하라'만 안내하고 자동 검증은 한 번도 돌지 않는다 — 같은 세 갈래가 12번 반복되며
    단계가 36개까지 불어난 폭주의 뿌리다. 비준은 (desc, verify)에 해시로 결속되므로 같은 조건이면
    같은 비준이다. **영수증(통과·증거)은 승계하지 않는다** — 증명은 새 산출물에서 다시 번다."""
    from types import SimpleNamespace
    from system.rule.evidence import verifier_command_hash, verifier_spec_hash
    from system.rule.milestone import (Criterion, Milestone,
                                       promote_final_locked_criteria,
                                       ratified_goal_verifier_command)

    (tmp_path / "verify_ui.py").write_text("print('ok')\n")
    cmd = "python3 verify_ui.py --desktop-width 1280"
    desc, verify = "조건: 브라우저에서 한 판이 끝까지 돈다", "브라우저로 시작→플레이→결과를 1회 확인"

    def _lock(**kw):
        c = Criterion(desc=desc, verify=verify, release_lock=True)
        for k, v in kw.items():
            setattr(c, k, v)
        return c

    # 1주기: 회의가 비준을 세워 통과까지 갔다. 2주기(e2e 실패 후 보충): 조건만 복사돼 비준이 없다.
    done = Milestone(ms_id="MS-1", goal="1차", criteria=[], status="done")
    done.criteria = [_lock(
        passed=True, evidence="exit=0", evidence_source="sys_run", receipt_id="r-1",
        ratified_verifier_command=cmd,
        ratified_verifier_command_hash=verifier_command_hash(cmd),
        ratified_verifier_spec_hash=verifier_spec_hash(desc, verify))]
    done.locked_criteria = [Criterion(desc=desc, verify=verify)]
    fresh = Milestone(ms_id="MS-2", goal="결함 해소", criteria=[], status="open")
    fresh.criteria = []
    fresh.locked_criteria = [Criterion(desc=desc, verify=verify)]

    flow = SimpleNamespace(milestones=[done, fresh], workspace=str(tmp_path),
                           writes_by_role={}, run_outputs=set(), current=None, log=None)
    promote_final_locked_criteria(flow, checkpoint=False)

    got = next((c for c in fresh.criteria if c.desc == desc), None)
    assert got is not None, "잠금 조건이 새 주기로 승격되지 않았다"
    assert ratified_goal_verifier_command(got, str(tmp_path), require_existing=True) == cmd, \
        "비준이 새 주기로 안 따라가 자동 검증이 영영 안 돈다(폭주의 뿌리)"
    assert not got.passed and not str(got.evidence or "").strip(), \
        "영수증까지 승계하면 새 산출물에서 증명 없이 통과한다"


def test_조건이_바뀌면_옛_비준은_승계되지_않는다(tmp_path):
    """비준은 그 조건(desc+verify)에 대한 결정이다 — 조건이 달라지면 무엇으로 증명할지도 다시
    정해야 한다. spec 해시가 다르면 물려주지 않는다."""
    from types import SimpleNamespace
    from system.rule.evidence import verifier_command_hash, verifier_spec_hash
    from system.rule.milestone import (Criterion, Milestone,
                                       promote_final_locked_criteria,
                                       ratified_goal_verifier_command)

    (tmp_path / "verify_ui.py").write_text("print('ok')\n")
    cmd = "python3 verify_ui.py --desktop-width 1280"
    old_desc, old_verify = "옛 조건", "옛 절차"
    new_desc, new_verify = "새 조건: 다른 것을 본다", "새 절차로 확인"

    done = Milestone(ms_id="MS-1", goal="1차", criteria=[], status="done")
    c_old = Criterion(desc=old_desc, verify=old_verify, release_lock=True)
    c_old.ratified_verifier_command = cmd
    c_old.ratified_verifier_command_hash = verifier_command_hash(cmd)
    c_old.ratified_verifier_spec_hash = verifier_spec_hash(old_desc, old_verify)
    done.criteria = [c_old]
    done.locked_criteria = [Criterion(desc=new_desc, verify=new_verify)]

    flow = SimpleNamespace(milestones=[done], workspace=str(tmp_path),
                           writes_by_role={}, run_outputs=set(), current=None, log=None)
    promote_final_locked_criteria(flow, checkpoint=False)

    got = next((c for c in done.criteria if c.desc == new_desc), None)
    assert got is not None
    assert not ratified_goal_verifier_command(got, str(tmp_path), require_existing=True), \
        "다른 조건의 비준을 물려받으면 팀이 정하지 않은 방법으로 통과한다"


def test_잠금조건이_반복_정체하면_사람에게_올라간다():
    """[2026-07-27 U-067 실측] 탈출 사다리는 GOAL 잠금 조건에서 그냥 되돌아 나와 정체 카운터도 안
    풀고 사람에게도 안 올렸다 — 같은 자리를 12바퀴 돌았다. 포기는 여전히 불가하되, 반복 정체는
    사람에게 넘기고 경보 반복을 멈춘다."""
    import inspect
    from system.rule import milestone

    src = inspect.getsource(milestone.renegotiate_criterion)
    i = src.index('if getattr(c, "release_lock", False):')
    seg = src[i:i + 2400]
    assert "goal_lock_stuck_parked" in seg, "반복 정체가 사람에게 안 올라간다"
    assert "iter_stuck = 0" in seg, "정체 카운터를 안 풀어 경보가 매 바퀴 반복된다"
    assert "이월·포기할 수 없습니다" in seg, "잠금 조건 포기 금지가 풀렸다"
    # [단, 정본에서 사라진 잠금은 예외(2026-07-27, 전수감사)] 지금 팀이 합의한 계약에 없는 조건이
    # 출구 없이 판을 영원히 막던 것 — 그건 잠금 취급을 풀고 일반 사다리로 보낸다.
    assert "goal_lock_stale_released" in seg, "정본에서 사라진 낡은 잠금에 출구가 없다"


def _ms_open_with(goals, criteria_passed=False, iter_stuck=0):
    from system.rule.milestone import Criterion, Milestone, SubTask
    ms = Milestone(ms_id="MS-9", goal="주기", criteria=[
        Criterion(desc="조건 A", verify="확인")], status="open")
    ms.criteria[0].passed = criteria_passed
    ms.iter_stuck = iter_stuck
    ms.subtasks = [SubTask(st_id=f"MS-9/ST-{i+1}", goal=g, criteria=[], status="done")
                   for i, g in enumerate(goals)]
    return ms


def test_재분해는_정체_임계를_넘으면_열리지_않는다():
    """[2026-07-27 U-067 실측] '조건 미충족 + 백로그 소진'만으로 재분해가 무한 재점화돼 같은 세
    영역이 12세대 반복, 단계가 36개까지 불었다. 앞 세대가 결과를 못 바꿨으면(iter_stuck) 또 쪼개지
    말고 재협상 사다리로 흘러야 한다. **첫 재분해는 반드시 허용**(정상 보충 경로)."""
    from types import SimpleNamespace
    from system.rule.milestone import meeting_stage, stuck_limit

    def _stage(stuck):
        ms = _ms_open_with(["실브라우저 루프 재현"], iter_stuck=stuck)
        cur = SimpleNamespace(status=SimpleNamespace(goal="게임 한 종"), team=[11, 12])
        flow = SimpleNamespace(milestones=[ms], backlog_relays={}, current=cur,
                              workspace="", log=None)
        return meeting_stage(flow)

    assert _stage(0) == "subtask", "첫 보충 분해까지 막으면 정상 주기가 못 닫힌다"
    assert _stage(stuck_limit()) != "subtask", "정체가 임계를 넘어도 계속 쪼갠다(폭주)"


def test_이미_열린_단위와_같은_영역은_반려된다():
    """[2026-07-27 U-067 실측] 중복 검사가 **한 수렴안 안**만 봐서, 앞 세대 단위가 done이 되면
    같은 영역 3개를 12세대 반복해도 중복 0으로 통과했다. 이미 있는 단위와 겹치면 새로 쪼개지 말고
    그 단위를 이어가야 한다."""
    from types import SimpleNamespace
    from system.rule.milestone import stage_preflight

    ms = _ms_open_with(["실브라우저 루프 재현"])
    flow = SimpleNamespace(milestones=[ms], backlog_relays={}, current=None,
                          workspace="", log=None)
    text = ("## 결정\n단위: 실브라우저 루프 재현\n"
            "백로그: [실브라우저 루프 재현] 브라우저로 한 판을 끝까지 돌린다\n")
    errs = stage_preflight("subtask", text, flow=flow)
    assert any("이미 열린 단위" in e for e in errs), "같은 영역 재분해가 그대로 통과한다"

    errs2 = stage_preflight("subtask", text.replace("실브라우저 루프 재현", "결제 연동"), flow=flow)
    assert not any("이미 열린 단위" in e for e in errs2), "정말 새로운 영역까지 막으면 보충이 불가능"


def test_착지한_회의_초안은_다음_회의가_물려받지_않는다():
    """[2026-07-27 U-067 실측] 등록까지 끝난 초안이 파일에 남아, 다음 같은-단계 회의가 그 완성본을
    물려받아 자리표시 0·이의 0으로 즉시 재가결했다(같은 단위 3개 × 12세대). 중단된 초안(표지 없음)은
    재시작-안전을 위해 계속 보존해야 하므로, 표지 한 줄로 둘을 가른다."""
    from system.rule.milestone import DRAFT_LANDED_MARK, draft_should_reset

    interrupted = "[stage:subtask]\n## 결정\n단위: 쓰다 만 것\n"
    assert draft_should_reset("subtask", interrupted) is False, \
        "중단된 초안을 지우면 재시작마다 회의가 처음부터다"
    assert draft_should_reset("subtask", interrupted + DRAFT_LANDED_MARK + "\n") is True, \
        "착지한 초안을 물려받아 즉시 재가결한다(세대 증식)"


def test_비준_반려는_거부된_명령을_보여준다():
    """[2026-07-27 U-068 실측] 반려문이 미달 '조건'만 나열해, 봇들이 자기가 써넣은 명령이 문제였다는
    걸 못 알아채고 같은 명령을 다시 냈다(`npm run verify …` — 이 판엔 npm 프로젝트가 없다). 회의는
    4패스를 태우고 무진전 컷. 반려는 **본 것을 말해야** 한다 — 어느 줄의 어떤 명령이 거부됐는지."""
    from types import SimpleNamespace
    from system.rule.milestone import _milestone_verifier_errors

    flow = SimpleNamespace(workspace="/nonexistent", milestones=[], current=None, log=None)
    errs = _milestone_verifier_errors(flow, [
        {"desc": "초행 사용자가 첫 루프를 완료한다", "verify": "npm run verify -- --goal GOAL@abc"},
    ])
    joined = "\n".join(errs)
    assert "npm run verify" in joined, "봇이 써넣은 명령을 안 보여준다(같은 명령을 다시 낸다)"
    assert "초행 사용자가" in joined, "어느 조건이 걸렸는지 안 보여준다"


def test_목표회의는_요청을_좁히지_말라고_말한다():
    """[2026-07-27 사용자: 'Task 자체는 할 수 있는 최대한으로 잡고'] 종전 안내는 '구체적으로'만
    요구해 열린 요청("게임 만들어줘")이 최소 산출물로 수렴했다(실측: "1인용 단일 화면 미니게임 1종").
    Task는 목적지이고, 작게 가는 것은 마일스톤 주기가 맡는다 — 목표를 줄이는 게 아니라 순서를 나눈다."""
    from system.rule.milestone import stage_agenda

    _, agenda = stage_agenda("goal")
    assert "갈 수 있는 곳까지" in agenda, "Task를 최대로 잡으라는 자리가 없다(요청이 좁혀진다)"
    assert "도달 순서를 나눕니다" in agenda, "작게 가는 책임이 마일스톤이라는 설명이 없다"


def test_목표회의는_완수조건이_명령_부채가_됨을_예고한다():
    """[2026-07-27 U-068 실측] 여기 적은 완수조건은 그대로 GOAL 잠금이 되어 마지막 주기에서
    **실행 가능한 명령 한 줄**로 실증해야 한다. 그 예고가 없어 회의는 자연어 절차로 통과시키고,
    계획 단계에서 비준 부채로 두 번 파킹했다."""
    from system.rule.milestone import stage_agenda

    _, agenda = stage_agenda("goal")
    assert "명령 한 줄" in agenda, "나중에 돌아올 명령 부채를 예고하지 않는다"
    assert "다시 쓰세요" in agenda, "지금 고칠 길을 알려주지 않는다"


def test_로드맵_진척은_복기주기를_빼고_같은_눈으로_센다():
    """[2026-07-27 전수감사] ①복기(e2e 실패로 열린) 주기가 done이 되면 로드맵 완주 수에 합산돼,
    **계획 단계가 남았는데도 '최종 주기 도달'로 판정**되고 다음 계획 회의가 안 열렸다(사다리가 한
    칸씩 잘림). ②사용자 안내만 정규화 안 한 roadmap을 써서 한 줄 화살표 로드맵이면 안내가 아예
    안 뜨고 숫자도 한 칸 앞섰다."""
    from types import SimpleNamespace
    from system.rule.milestone import Milestone, roadmap_done_count, roadmap_phases

    def _ms(mid, status, origin=""):
        m = Milestone(ms_id=mid, goal="g", criteria=[], status=status)
        m.origin = origin
        return m

    flow = SimpleNamespace(
        roadmap=["최소버전 → 확장 → 완성"],
        milestones=[_ms("MS-1", "done"), _ms("MS-R", "done", "e2e:결함1"), _ms("MS-2", "open")])
    assert roadmap_phases(flow) == ["최소버전", "확장", "완성"], "한 줄 화살표 로드맵이 안 쪼개진다"
    assert roadmap_done_count(flow) == 1, "복기 주기가 로드맵 완주로 잡힌다(사다리가 잘린다)"
