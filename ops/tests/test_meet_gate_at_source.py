"""관문은 회의가 열리는 자리에 있다 (2026-08-06, 사용자: '채용 시스템과 회의나 여러 시스템적
구조적 안정성을 확실화 하고 재시작해').

[실측 U-522] 러너 로그에 `stage_meeting_opened`가 없는데 goal 회의가 섰다. 연 것은 **채용 봇**,
참여 실무자는 **1명**이었다. 두 관문(정족수 3인 · 채용 배제)은 SYS 단계 루프(_stage_roster_ready)
에만 걸려 있었는데, 봇이 meet 도구를 직접 부르면 그 루프를 타지 않는다. 같은 날 앞선 수리
(앵커 회전·정족수 산술)도 전부 그 루프 위에 있었으므로 함께 우회됐다.

회의가 열리는 길은 meet() 하나다 — 관문을 그 자리로 내린다. 호출처마다 붙이는 방식은 새 호출처가
생길 때마다 같은 구멍이 다시 난다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import communication as C


def _meet_head():
    src = inspect.getsource(C.meet)
    # [창을 넓힌다(2026-08-07)] meet 머리에 관문이 늘면(끊긴 회의 닫기·기계 이의 갱신) 고정 폭
    # 슬라이스가 뒤쪽 관문을 잘라내 "관문이 없다"는 거짓 실패가 난다. 재는 것은 위치가 아니라 존재다.
    return src[:src.find("_resolve_members") + 9000]


def test_채용은_회의를_열_수_없다():
    head = _meet_head()
    assert "_is_spare(flow, me_id)" in head, "여는 사람이 시스템 봇인지 보지 않는다"
    assert "meet_denied_system_role" in head, "거절이 로그에 안 남는다"


def test_목표_회의는_정족수를_meet에서_본다():
    head = _meet_head()
    assert "meet_denied_thin_roster" in head, "정족수 관문이 meet에 없다"
    assert "GOAL_QUORUM_MIN" in head, "정족수 상수를 쓰지 않는다"


def test_거절은_다음_행동을_지시한다():
    """막기만 하면 봇이 같은 도구를 다시 부른다 — 채용을 먼저 하라고 말한다."""
    head = _meet_head()
    assert "채용을 " in head and "먼저" in head


def test_정족수는_실무자만_센다():
    head = _meet_head()
    i = head.find("meet_denied_thin_roster")
    block = head[max(0, i - 800):i]
    assert "not _is_spare(flow, m)" in block, "채용을 인원으로 센다"


def test_거절에_남은_인원과_기존_직군이_실린다():
    """[U-524 실측] '채용을 먼저 하세요'만 주면 채용 봇은 이미 있는 직군을 다시 공고한다 —
    게임플레이 프로그래머를 뽑은 직후 같은 직군을 재공고해 튕겼고 판은 1명으로 멈췄다.
    몇 명이 더 필요한지와 이미 있는 직군을 함께 준다(무엇을 뽑을지는 여전히 판이 정한다)."""
    src = inspect.getsource(C._hiring_note)
    assert "명이 더 필요합니다" in src
    assert "같은 직군을 다시 공고하지 마세요" in src
    assert "이미 있는 직군" in src


def test_채용_지시는_정족수를_채우면_사라진다():
    src = inspect.getsource(C._hiring_note)
    assert "if not _need:" in src and 'return ""' in src


def test_시스템이_직군_목록을_지정하지_않는다():
    """중앙 지시가 아니다 — 필요한 도메인은 판이 정한다."""
    src = inspect.getsource(C._hiring_note)
    for word in ("QA", "기획", "엔지니어", "디자이너"):
        assert f'"{word}' not in src, f"시스템이 직군을 지정한다: {word}"


def test_정족수를_채우면_그만_뽑으라고_말한다():
    """[실측 U-527] 네 도메인을 채운 뒤에도 채용 봇이 공고를 두 건 더 냈다 —
    '첫 회의를 소집하고 공동 DRAFT.md에 결론을 정리할 담당자', '플레이 흐름을 독립 확인할 담당자'.
    둘 다 이미 그 직군이 있어 튕겼다(recruit_genesis_skipped_same_job). 폐지된 사회자 자리를
    직군 이름으로 되살리려는 시도다. 거절문이 '담당자에게 넘기세요'라고만 하니 '담당자를 뽑아야겠다'로
    읽힌 것이다."""
    src = inspect.getsource(C._hiring_done_note)
    assert "시스템이 자동으로" in src, "회의가 자동으로 열린다는 사실을 안 알려 준다"
    assert "추가 공고를 내지 마세요" in src


def test_아직_모자라면_채용_지시가_우선이다():
    """정족수 전에는 '그만 뽑아라'가 아니라 '몇 명 더'가 나가야 한다."""
    src = _meet_head()
    assert "(_hn or _hiring_done_note(flow))" in src


def test_라벨이_없어도_팀이_아니면_못_연다():
    """[실측 U-535] _is_spare는 flow.bot_info의 직군 라벨로 판정하는데, 그 목록은 채널로 좁힌
    로스터라 시스템 존재인 채용 봇이 빠질 수 있다. 라벨이 ''이면 판정이 False가 되어 관문이
    통과됐다 — 채용이 goal 회의를 열고 여는 의견까지 냈다(실무자 1명):

        13956 정하준/채용 | [회의 시작][단계:goal] 무엇을 만들지 … [여는 의견] 브라우저에서 바로…

    라벨이 없고 이 Task의 팀도 아니면 구성원이 아니다."""
    head = _meet_head()
    assert "_in_team = int(me_id) in" in head, "팀 여부를 보지 않는다"
    assert 'not str(flow._info(me_id) or "").strip() and not _in_team' in head
