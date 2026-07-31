"""[S2 백로그 릴레이 — 계약 테스트] PIPELINE_REWORK_2026-07-09 §3·§9·§11 고정.

여기서 고정하는 계약:
  ① 지명 — 마무리자(턴 홀더)만 다음 (백로그, 수행자)를 정한다. 첫 배분은 홀더 제약 없음.
  ② 응찰 — 유일 최고점 즉시 배정 / 동률 = 배정 없음(해소는 결정권자 권한 ②) / 무응찰 = 규칙 ③으로.
  ③ 무응찰 — 마지막에 일한 작업자만 지정(pass_to). 응찰이 재무산돼도 같은 사람.
  차단 — blocked 보존 + 차단자가 다음 시작자 지정(턴 이동). 같은 백로그 2회 차단 = deadlock_signal.
  중복 게이트 — 어휘 겹침 제출 차단(직군 변형 게이트 동형), force 명시로만 우회.
  §9 — to_ckpt/from_ckpt(JSON 왕복) 후 릴레이 **중간부터** 재개.
  §11 — 이벤트 이름 그대로 방출(어휘 밖 이름 0개)을 대본으로 관측.

플래그 OFF 불변(완수조건 ③): backlog.py는 이 테스트 밖 어디서도 import되지 않는다 — 배선은
ORGANT_PIPELINE 뒤 S1 접점에서만 일어난다. 따라서 기존 스위트 그린 = 라이브 동작 불변의 증명.
"""
import json

import pytest

from system.rule.backlog import (BLOCKED, DONE, IN_PROGRESS, OPEN, BacklogError,
                                 BacklogRelay, DuplicateBacklog)

A, B, C, D = 1, 2, 3, 4

# §11 이벤트 어휘 중 S2 소유분 — 이 밖의 이름을 방출하면 계약 위반
S2_EVENTS = {"backlog_submit", "backlog_done", "relay_pick", "relay_bid",
             "relay_pass_to", "relay_block", "deadlock_signal"}


def _relay(events=None):
    log = (lambda ev, **f: events.append((ev, f))) if events is not None else None
    return BacklogRelay(subtask_id="ST1", log=log)


# ══ 제출 · 중복 게이트 ══════════════════════════════════════════════════════

def test_제출_중복게이트_차단과_force_우회():
    r = _relay()
    b1 = r.submit(A, "프론트 카드 컴포넌트 렌더링")
    assert b1.status == OPEN and b1.backlog_id == "B1"
    # 표현만 바꾼 같은 일 → 차단 + 기존 id 안내(변형 게이트 동형 — 시스템이 병합하지 않는다)
    with pytest.raises(DuplicateBacklog) as e:
        r.submit(B, "카드 컴포넌트 프론트 렌더")
    assert e.value.existing_id == "B1"
    # 제출자가 '진짜 다른 일'을 명시(force)하면 통과
    assert r.submit(B, "카드 컴포넌트 프론트 렌더", force=True).backlog_id == "B2"
    # 이질 제출은 그냥 통과
    assert r.submit(B, "백엔드 저장 API 설계").backlog_id == "B3"


def test_빈_제출_거부():
    with pytest.raises(BacklogError):
        _relay().submit(A, "   ")


def test_백로그_작업생각은_체크포인트에_영속():
    r = _relay()
    b = r.submit(A, "프론트 카드 구현")
    r.pick(A, b.backlog_id, B)
    b.activity.extend(["[프론트엔드] 💭 구조 확인", "[프론트엔드] 💭 렌더 구현"])
    restored = BacklogRelay.from_ckpt(r.to_ckpt())
    assert restored.get("B1").activity == b.activity


def test_참조표기_백로그_반려_모든경로(monkeypatch):
    """[U-041 실측(2026-07-22, 사용자: '5개 통과 서브태스크 완수됐는데 갑자기 백로그 수만 늘어남')]
    병합 회의 등록·작업 중 report_iter 자동생성 양쪽에서 'B4'·'B2 점수 공식…' 같은 의존/참조 줄이
    백로그로 태어나 즉시 완료 churn → 서브태스크 거짓 완수. 순수 참조는 submit 관문에서 force와
    무관하게 반려(모든 경로 공통)."""
    r = _relay()
    for ref in ("B4", "B2 점수 공식 정의", "#3", "BL-2 저장", "B 12 뭔가"):
        with pytest.raises(BacklogError):
            r.submit(A, ref)
        with pytest.raises(BacklogError):
            r.submit(A, ref, force=True)        # force도 우회 불가(참조는 '진짜 다른 일'이 아님)
    # 참조로 오인될 수 없는 실작업은 통과
    assert r.submit(A, "Board 상태 직렬화 구현").backlog_id == "B1"   # 'Board'는 B\d 아님


# ══ 규칙 ① 지명 ════════════════════════════════════════════════════════════

def test_규칙1_첫배분은_자유_이후는_마무리자만():
    r = _relay()
    b1 = r.submit(A, "프론트 카드")
    b2 = r.submit(A, "백엔드 API")
    r.pick(C, b1.backlog_id, B)                 # 첫 배분: 아직 마무리자 없음 → 누구든
    r.done(B, b1.backlog_id)                    # B가 마무리 → 배분권은 B에게
    with pytest.raises(BacklogError):
        r.pick(C, b2.backlog_id, C)             # 마무리자 아닌 C의 지명 → 거부
    assert r.pick(B, b2.backlog_id, C).assignee == C


def test_규칙1_지명은_open_blocked만():
    r = _relay()
    b = r.submit(A, "프론트 카드")
    r.pick(A, b.backlog_id, B)
    with pytest.raises(BacklogError):
        r.pick(A, b.backlog_id, C)              # in_progress 재지명 불가
    r.done(B, b.backlog_id)
    with pytest.raises(BacklogError):
        r.pick(B, b.backlog_id, C)              # done 재지명 불가


def test_거절은_open으로_되돌린다():
    r = _relay()
    b = r.submit(A, "프론트 카드")
    r.pick(A, b.backlog_id, B)
    r.decline(B, b.backlog_id, "도메인 밖")
    assert b.status == OPEN and b.assignee is None
    with pytest.raises(BacklogError):
        r.decline(C, b.backlog_id)              # 수행자 아닌 거절 불가


# ══ 규칙 ② 응찰 ════════════════════════════════════════════════════════════

def test_규칙2_유일최고점_배정():
    r = _relay()
    b = r.submit(A, "프론트 카드")
    won, tie = r.bid_round(b.backlog_id, {B: 3, C: 7, D: 0})   # 0점 = 패스
    assert won is b and tie is None
    assert b.status == IN_PROGRESS and b.assignee == C


def test_규칙2_동률은_배정없음_결정권자_해소():
    r = _relay()
    b = r.submit(A, "프론트 카드")
    won, tie = r.bid_round(b.backlog_id, {B: 7, C: 7})
    assert won is None and tie == [B, C]
    assert b.status == OPEN                      # 시스템이 임의로 깨지 않는다(권한은 결정권자 ②)
    r.resolve_tie(99, b.backlog_id, C)           # 결정권자(99)의 해소 표면
    assert b.status == IN_PROGRESS and b.assignee == C


def test_규칙2_무응찰은_규칙3으로():
    r = _relay()
    b = r.submit(A, "프론트 카드")
    assert r.bid_round(b.backlog_id, {}) == (None, None)
    assert r.bid_round(b.backlog_id, {B: 0}) == (None, None)   # 전원 패스 = 무응찰
    assert b.status == OPEN


# ══ 규칙 ③ 무응찰 = 마지막 작업자가 지정 ═══════════════════════════════════

def test_규칙3_마지막작업자만_지정():
    r = _relay()
    b1 = r.submit(A, "프론트 카드")
    b2 = r.submit(A, "백엔드 API")
    r.pick(A, b1.backlog_id, B)
    r.done(B, b1.backlog_id)                     # 마지막 작업자 = B
    r.bid_round(b2.backlog_id, {})               # 무응찰
    with pytest.raises(BacklogError):
        r.pass_to(C, b2.backlog_id, D)           # B가 아닌 지정 → 거부(결정권자도 여기 개입 못 함)
    assert r.pass_to(B, b2.backlog_id, D).assignee == D


def test_규칙3_재무산도_같은사람이_재지정():
    r = _relay()
    b1 = r.submit(A, "프론트 카드")
    b2 = r.submit(A, "백엔드 API")
    r.pick(A, b1.backlog_id, B)
    r.done(B, b1.backlog_id)
    r.pass_to(B, b2.backlog_id, C)               # B 지정 → C
    r.decline(C, b2.backlog_id, "선행 지식 없음")
    r.bid_round(b2.backlog_id, {})               # 응찰 무산이 또 돌아와도
    assert r.pass_to(B, b2.backlog_id, D).assignee == D   # 같은 사람(B)이 재지정


def test_규칙3_마무리자_없으면_pick으로():
    r = _relay()
    b = r.submit(A, "프론트 카드")
    with pytest.raises(BacklogError):
        r.pass_to(A, b.backlog_id, B)            # 아직 아무도 일 안 함 → pass_to 아님


# ══ 차단 핸드오프 · 교착 ═══════════════════════════════════════════════════

def test_차단_보존과_턴이동():
    r = _relay()
    b = r.submit(A, "프론트 카드")
    r.pick(A, b.backlog_id, B)
    blocked, deadlock = r.block(B, b.backlog_id, next_starter=C, reason="API 스펙 선행 필요")
    assert blocked.status == BLOCKED and not deadlock    # 미완 보존(버리지 않는다)
    assert r.turn_holder == C                            # 차단자가 다음 시작자를 지정
    with pytest.raises(BacklogError):
        r.pick(A, "B1", D)                               # 배분권은 이제 C의 것
    assert r.pick(C, "B1", D).status == IN_PROGRESS     # 재방문(차단 이력 보존)
    assert r.get("B1").block_count == 1


def test_교착신호_같은백로그_2회차단():
    r = _relay()
    b = r.submit(A, "프론트 카드")
    r.pick(A, b.backlog_id, B)
    _, dl1 = r.block(B, b.backlog_id, C, "선행 1")
    assert not dl1
    r.pick(C, b.backlog_id, D)                           # 재방문
    _, dl2 = r.block(D, b.backlog_id, C, "선행 여전")     # 2회째 차단
    assert dl2                                           # → deadlock_signal(중재는 결정권자 권한 ③)


def test_백로그_소진시_회의코칭_아니면_다음선정():
    """[백로그 소진 트리거(2026-07-14, 사용자: '백로그 다 돌고 서브태스크·백로그 회의')] 종결 순간
    남은 백로그가 있으면 [다음 선정], 하나도 없으면(풀 소진) [백로그 소진] 회의 코칭(meet/vote_stop)."""
    from system.rule.backlog import handoff_note, relay_for
    f, st, ev = _pipe_flow()
    f._pipeline_notes = []
    r = relay_for(f, st)
    r.submit(A, "저장 API")
    r.submit(B, "프론트 카드")
    r.pick(A, "B1", A); r.done(A, "B1")
    handoff_note(f, r, A, "완료됐습니다")
    assert any("[다음 선정]" in n for n in f._pipeline_notes)     # 아직 B2 남음
    f._pipeline_notes.clear()
    r.pick(A, "B2", B); r.done(B, "B2")                           # 마지막까지 종결
    handoff_note(f, r, B, "완료됐습니다")
    note = "\n".join(f._pipeline_notes)
    assert "[백로그 소진]" in note and "vote_stop" in note and "meet" in note  # 회의 코칭


def test_중단_배분권_우회_봉합():
    """[배분권 우회 봉합(2026-07-14, 정합 감사)] 착수(in_progress)한 백로그의 중단만 마무리자 자격 —
    대기 중 멤버가 자기 미착수(OPEN) 백로그를 버려 turn_holder(배분권)를 탈취하던 것 차단."""
    r = _relay()
    r.submit(A, "저장 API"); r.submit(B, "프론트 카드")
    r.pick(A, "B1", A); r.done(A, "B1")            # A가 마무리 → 배분권 A
    assert r.turn_holder == A
    r.drop(B, "B2", "안 할래")                      # B가 자기 미착수(OPEN) B2를 중단
    assert r.turn_holder == A                       # 배분권 그대로 A (B가 탈취 못 함)
    assert r.get("B2").status == "dropped"


def test_차단_배선_선행필요_출구():
    """[block 배선(2026-07-14, 정합 감사 최대위험 — 무출구 교착)] 막힌 일감을 버리지 않고 BLOCKED로
    보존하는 출구. 2회째 차단이면 deadlock 신호(→ renegotiate/vote_stop 라우팅).
    (2026-07-31: 순차 잠금 자체가 폐기돼 '잠금 풀림' 축은 사라졌고, 보존·재방문·신호만 남는다.)"""
    r = _relay()
    b1 = r.submit(A, "저장 API")
    b2 = r.submit(B, "프론트 카드")
    r.pick(A, "B1", A)                                # A 착수
    _bl, dl = r.block(A, "B1", next_starter=A, reason="스키마 선행 필요")
    assert _bl.status == BLOCKED and not dl           # 보존(버리지 않음 — 차단 출구의 핵심)
    assert r.pick(A, "B2", B).status == IN_PROGRESS   # 차단된 것과 무관하게 다음이 선다
    r.done(B, "B2")
    r.pick(A, "B1", A)                                # 선행 풀려 재방문
    _, dl2 = r.block(A, "B1", A, "여전히 막힘")        # 2회째 차단
    assert dl2                                        # deadlock 신호(→ renegotiate/vote_stop 라우팅)


def test_각자_자기_일감을_동시에_진행한다():
    """[전원 병렬(2026-07-31, 사용자: '전체 직원이 계속 자기꺼 하면 되잖아')] 종전 '한 번에 한
    백로그'는 폐기됐다 — 다른 일감이 돈다는 이유로 착수를 막지 않는다(U-442 실측: 96분 내내 동시 1)."""
    r = _relay()
    r.submit(A, "저장 API")
    r.submit(B, "프론트 카드")
    r.pick(A, "B1", A)                                # A 착수(self-claim)
    assert r.pick(A, "B2", B).status == IN_PROGRESS   # B도 곧바로 착수 — 기다리지 않는다
    assert [b.status for b in r.backlogs] == [IN_PROGRESS, IN_PROGRESS]


def test_동시_진행_상한만_남는다(monkeypatch):
    """남는 제한은 자원 보호용 상한 하나뿐이다."""
    monkeypatch.setenv("ORGANT_BACKLOG_PARALLEL", "1")
    r = _relay()
    r.submit(A, "저장 API")
    r.submit(B, "프론트 카드")
    r.pick(A, "B1", A)
    with pytest.raises(BacklogError, match="동시 진행 상한"):
        r.pick(A, "B2", B)


def test_완료는_수행자만():
    r = _relay()
    b = r.submit(A, "프론트 카드")
    r.pick(A, b.backlog_id, B)
    with pytest.raises(BacklogError):
        r.done(C, b.backlog_id)
    r.done(B, b.backlog_id)
    assert b.status == DONE and r.turn_holder == B


# ══ SubTask iter — 잔여 정리·종료 ═══════════════════════════════════════════

def test_iter_정리_잔여는_done_참칭_안함_풀종료():
    r = _relay()
    b1 = r.submit(A, "프론트 카드")
    b2 = r.submit(A, "백엔드 API")
    r.pick(A, b1.backlog_id, B)
    r.done(B, b1.backlog_id)
    left = r.close_iter()                        # 완수조건 충족(검증은 S1 게이트) 후의 정리
    assert [x.backlog_id for x in left] == ["B2"]
    assert r.get("B2").status == OPEN            # 정직: 정리 ≠ 완료(상태 참칭 없음, note만)
    assert "정리" in r.get("B2").note
    with pytest.raises(BacklogError):
        r.submit(A, "새 백로그")                  # 종료 후 제출 불가
    with pytest.raises(BacklogError):
        r.pick(B, "B2", C)                       # 종료 후 배분 불가


def test_주기중_추가는_허용():
    r = _relay()
    b1 = r.submit(A, "프론트 카드")
    r.pick(A, b1.backlog_id, B)
    assert r.submit(C, "배포 파이프라인 점검").backlog_id == "B2"   # 진행 중 제출 OK(§2)


# ══ §9 저장 — ckpt 왕복 후 중간 재개 ════════════════════════════════════════

def test_ckpt_왕복_릴레이_중간재개():
    ev1 = []
    r = _relay(ev1)
    b1 = r.submit(A, "프론트 카드")
    b2 = r.submit(A, "백엔드 API")
    b3 = r.submit(A, "배포 점검")
    r.pick(A, b1.backlog_id, B)
    r.done(B, b1.backlog_id)                     # 마무리자 B
    r.pick(B, b2.backlog_id, C)
    r.block(C, b2.backlog_id, next_starter=B, reason="선행")   # blocked(1회), 턴=B
    r.pick(B, b3.backlog_id, D)                  # in_progress 하나 살아있는 중간 상태

    snap = json.loads(json.dumps(r.to_ckpt()))   # JSON 왕복 = flow _ckpt 동승 가능 증명
    ev2 = []
    r2 = BacklogRelay.from_ckpt(snap, log=lambda ev, **f: ev2.append((ev, f)))

    # 재시작 후 그 지점부터 그대로 — 상태·턴·차단 이력 전부 복원
    assert r2.turn_holder == B and r2._seq == 3
    assert r2.get("B1").status == DONE
    assert r2.get("B2").status == BLOCKED and r2.get("B2").block_count == 1
    assert r2.get("B3").status == IN_PROGRESS and r2.get("B3").assignee == D
    # 이어서 진행: D 완료 → D가 배분권 → 재방문 pick → 2회차 차단 = 교착 신호
    r2.done(D, "B3")
    r2.pick(D, "B2", C)
    _, deadlock = r2.block(C, "B2", D, "선행 여전")
    assert deadlock
    assert [e for e, _ in ev2] == ["backlog_done", "relay_pick", "relay_block", "deadlock_signal"]


# ══ §11 이벤트 — 대본 관측 (완수조건 ②: relay_pick → relay_bid → relay_block 사슬) ══

def test_이벤트사슬_대본관측():
    ev = []
    r = _relay(ev)
    b1 = r.submit(A, "프론트 카드 컴포넌트")
    b2 = r.submit(A, "백엔드 저장 API")
    r.pick(A, b1.backlog_id, A)                  # 자기 지명 허용
    r.done(A, b1.backlog_id)
    r.pick(A, b2.backlog_id, B)
    r.decline(B, b2.backlog_id, "도메인 밖")
    r.bid_round(b2.backlog_id, {B: 0, C: 5})     # C 응찰 승
    r.block(C, b2.backlog_id, next_starter=A, reason="스키마 선행")
    r.pick(A, b2.backlog_id, D)                  # 재방문
    r.block(D, b2.backlog_id, A, "스키마 여전히 없음")   # 2회차 → 교착

    names = [e for e, _ in ev]
    # 어휘 밖 이름 0개(§11 그대로)
    assert set(names) <= S2_EVENTS
    # 완수조건 ②의 사슬이 실제 순서로 관측된다
    def _after(a, b):
        return names.index(a) < names.index(b)
    assert "relay_pick" in names and "relay_bid" in names and "relay_block" in names
    assert _after("relay_pick", "relay_bid") and _after("relay_bid", "relay_block")
    assert names[-1] == "deadlock_signal"
    # payload 최소 계약: 이벤트마다 subtask 표기(어느 SubTask의 릴레이인지)
    assert all(f.get("subtask") == "ST1" for _, f in ev)


def test_관측실패가_규칙을_죽이지_않는다():
    def boom(ev, **f):
        raise RuntimeError("로그 다운")
    r = BacklogRelay(subtask_id="ST1", log=boom)
    b = r.submit(A, "프론트 카드")               # 로그가 죽어도 제출·전이는 정상
    r.pick(A, b.backlog_id, B)
    assert r.done(B, b.backlog_id).status == DONE


# ══ 통합주기 2 — 위임축 배선 (실물 Milestone/SubTask + flow ckpt 동승) ═══════════

import asyncio
import types

from system.rule.backlog import (active_subtask, on_subtask_wrapup, relay_for,
                                 sync_completion, sync_delegation)
from system.rule.milestone import Criterion, Milestone, SubTask


def _pipe_flow():
    """S1 실물(Milestone/SubTask)을 단 가짜 flow — 배선 함수가 만지는 표면만 갖춘다."""
    ms = Milestone(ms_id="MS-1", goal="목표", criteria=[Criterion("조건", "run x")])
    st = SubTask(st_id="MS-1/ST-1", goal="부분목표", criteria=[Criterion("조건", "run x")])
    ms.subtasks.append(st)
    ev = []
    f = types.SimpleNamespace(milestones=[ms], backlog_relays={},
                              log=lambda e, **k: ev.append((e, k)),
                              _info=lambda x: f"봇{x}")
    return f, st, ev


def test_배선_플래그OFF는_전부_무동작(monkeypatch):
    monkeypatch.delenv("ORGANT_PIPELINE", raising=False)
    f, st, ev = _pipe_flow()
    assert sync_delegation(f, A, B, "[백로그 B1] 뭐든") is None
    sync_completion(f, B)
    assert f.backlog_relays == {} and ev == []   # 릴레이 생성조차 없음 = 기존 동작 불변


def test_배선_마커위임이_배분이_된다(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f, st, ev = _pipe_flow()
    r = relay_for(f, st)
    b = r.submit(A, "프론트 카드 컴포넌트")
    assert sync_delegation(f, A, B, "[백로그 B1] 카드 컴포넌트 만들어줘") is None
    assert b.status == IN_PROGRESS and b.assignee == B
    assert B in st.participants and st.backlog_ids == ["B1"]   # S1 접점 동기
    sync_completion(f, B)                                       # 실작업 인도 지점
    assert b.status == DONE and r.turn_holder == B              # 배분권이 현장으로
    assert [e for e, _ in ev if e in ("relay_pick", "backlog_done")] == ["relay_pick", "backlog_done"]


def test_배선_어휘겹침으로도_매칭(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f, st, _ = _pipe_flow()
    r = relay_for(f, st)
    b = r.submit(A, "백엔드 저장 API 설계")
    assert sync_delegation(f, A, C, "백엔드 저장 API 설계 맡아주세요 — 스키마부터") is None   # 마커 없이도
    assert b.assignee == C
    # 조사 변형 등으로 겹침이 60% 미달이면 매칭 안 됨 = 장부 밖 통과(안전한 저하 — 위임은 그대로 감).
    # 정밀 경로는 [백로그 Bn] 마커. 이 경계는 test_배선_백로그밖_위임은_그대로_통과가 고정한다.


def test_배선_턴규칙_남의_배분_거부(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f, st, _ = _pipe_flow()
    r = relay_for(f, st)
    b1 = r.submit(A, "프론트 카드")
    b2 = r.submit(A, "백엔드 API")
    assert sync_delegation(f, A, B, "[백로그 B1] 카드") is None
    sync_completion(f, B)                        # 마무리자 = B(배분권)
    msg = sync_delegation(f, C, D, "[백로그 B2] API")
    assert msg and "배분권" in msg               # C의 지정 → 코칭 거부(위임 자체가 막힘)
    assert b2.status == OPEN
    assert sync_delegation(f, B, D, "[백로그 B2] API") is None   # 배분권자 B는 통과


def test_배선_겹침방지_남의_진행분_위임_거부(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f, st, _ = _pipe_flow()
    r = relay_for(f, st)
    r.submit(A, "프론트 카드")
    assert sync_delegation(f, A, B, "[백로그 B1] 카드") is None
    msg = sync_delegation(f, A, C, "[백로그 B1] 카드")           # 같은 백로그를 다른 사람에게
    assert msg and "손에 있습니다" in msg
    assert sync_delegation(f, A, B, "[백로그 B1] 이어서") is None  # 같은 수행자 재전달은 통과


def test_배선_백로그밖_위임은_그대로_통과(monkeypatch):
    """[자기 등재 원칙(2026-07-14, 사용자: '백로그가 백엔드 지능으로 만들어지면 안 되고 각자 만들어
    전담해야')] 매칭 없는 위임은 백로그를 **날조하지 않고** 그대로 통과 — 종전 자동 제출(의무화 1단)이
    위임문 원문을 수행자 in_progress로 등재해, U-019에서 '네 백로그를 등록하라'는 메타 지시가 작업
    백로그로 둔갑했다. 등재는 수행자 본인의 pick_backlog(desc)만 — 계층 보장은 산출물 게이트가 맡는다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f, st, ev = _pipe_flow()
    r = relay_for(f, st)
    r.submit(A, "프론트 카드")
    assert sync_delegation(f, A, B, "배포 계정 설정 확인 부탁") is None   # 겹침 없음 → 장부 밖
    assert r.get("B1").status == OPEN
    assert len(r.backlogs) == 1                                        # 날조된 항목 없음(B2 미생성)
    assert not any(b.assignee == B for b in r.backlogs)                # 수행자에게 자동 배정 없음


def test_중단_dropped_본인만_처리제외_핸드오프(monkeypatch):
    """[중단(2026-07-14, 사용자: '개인이 올린거니 중지가 아니라 중단 — 처리에서 제외')] drop은
    본인(수행자/제출자)만, dropped는 remaining에서 빠지고 재선정 불가, 중단자가 새 턴 홀더(다음
    선정 담당자). 종결 시 남은 백로그 보유자 응찰 공고(handoff_note)가 노트로 게시된다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.backlog import handoff_note, BacklogError
    f, st, ev = _pipe_flow()
    r = relay_for(f, st)
    b1 = r.submit(A, "저장 API 스키마 설계")
    b2 = r.submit(B, "프론트 카드 렌더")
    r.pick(A, "B1", A)
    try:
        r.drop(B, "B1", "남의 것")                              # 본인 아님 — 거부
        assert False
    except BacklogError:
        pass
    r.drop(A, "B1", "역량 밖 — 인프라 권한 필요")
    assert b1.status == "dropped" and b1 not in r.remaining()    # 처리 제외
    assert r.turn_holder == A                                    # 중단자 = 다음 선정 담당
    try:
        r.pick(A, "B1", A)                                       # 재선정 불가
        assert False
    except BacklogError:
        pass
    handoff_note(f, r, A, "중단됐습니다")
    notes = "\n".join(getattr(f, "_pipeline_notes", []) or [])
    assert "[다음 선정]" in notes and "B2" in notes               # 남은 보유자 응찰 공고
    assert ("backlog_dropped" in [e for e, _ in ev])


def test_배선_wrapup_정리와_장부요지(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f, st, _ = _pipe_flow()
    r = relay_for(f, st)
    r.submit(A, "프론트 카드")
    r.submit(A, "백엔드 API")
    sync_delegation(f, A, B, "[백로그 B1] 카드")
    sync_completion(f, B)
    st.iter_n = 1
    summary = on_subtask_wrapup(f, st)           # dossier 없는 flow에서도 안전(정리는 진행)
    assert "1 완료" in summary.replace("백로그 ", "") and "잔여 1" in summary
    assert r.closed and r.get("B2").status == OPEN            # 정리 ≠ 완료 참칭
    assert on_subtask_wrapup(f, st) == "정리할 백로그 없음"     # 재호출 안전


def test_배선_ckpt_동승_왕복(monkeypatch):
    """§9 — 체크포인트 빌더/복원 경로에 릴레이가 실제로 실리고 되살아난다(재시작 중간 재개)."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.sys_recovery import checkpoint_open_task, restore_open_task
    f, st, _ = _pipe_flow()
    r = relay_for(f, st)
    r.submit(A, "프론트 카드")
    sync_delegation(f, A, B, "[백로그 B1] 카드")
    sync_completion(f, B)                        # 턴 홀더 = B인 중간 상태
    proj = {}
    fake_sys = types.SimpleNamespace(projects={500: proj},
                                     _task_snapshot=lambda fl, c: None,
                                     _save_projects=lambda: None)
    f.project_channel, f.current, f.file_owner = 500, None, None
    checkpoint_open_task(fake_sys, f)
    assert proj["backlog_relays"]["MS-1/ST-1"]["turn_holder"] == B
    # 재시작: 새 flow에 복원 — open_task 없어도 릴레이는 독립 복원된다
    f2 = types.SimpleNamespace(milestones=[], backlog_relays={})
    asyncio.get_event_loop_policy()
    assert asyncio.run(restore_open_task(fake_sys, f2, proj)) is None
    r2 = f2.backlog_relays["MS-1/ST-1"]
    assert r2.turn_holder == B and r2.get("B1").status == DONE
