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
