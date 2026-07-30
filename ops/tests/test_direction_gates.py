"""방향성 관문 — Task는 최대로, 주기는 탈 것으로(문구만 있고 검사가 없던 두 자리)."""
from system.rule.milestone import (goal_narrowing_error, roadmap_phase_is_process,
                                   roadmap_process_errors, stage_preflight)


def test_열린_요청을_좁힌_목표는_반려된다():
    """[U-436 실측] 원문 '게임 만들어줘'가 '화면 1개 · 로그인·서버·멀티플레이 없음'으로 등록됐다."""
    e = goal_narrowing_error("2D 회피 게임. 로그인·서버·멀티플레이 없음", "게임 만들어줘")
    assert e and "갈 수 있는 곳까지" in e


def test_사용자가_직접_좁힌_것은_존중한다():
    assert not goal_narrowing_error("혼자 하는 회피 게임 — 멀티플레이 없이",
                                    "혼자 하는 게임 만들어줘 멀티플레이 없이")


def test_최대로_잡은_목표는_통과한다():
    assert not goal_narrowing_error("둘이 겨루는 실시간 대전 게임을 만든다", "게임 만들어줘")


def test_공정_쪼개기_로드맵은_반려된다():
    """[U-436 실측] 로컬 기능 → 입력·뷰포트 → 시각 피드백 → 배포 = 같은 달구지를 다듬는 공정."""
    assert roadmap_phase_is_process("입력·뷰포트 완성본")
    assert roadmap_phase_is_process("배포 완성본")
    errs = roadmap_process_errors(["로컬 기능 완성본", "입력·뷰포트 완성본", "배포 완성본"])
    assert errs and "달구지" in errs[0]


def test_탈것_로드맵은_통과한다():
    assert not roadmap_process_errors(["혼자 즐기는 회피 게임", "둘이 겨루는 대전 모드",
                                       "기록이 공유되는 랭킹판"])


def test_목표회의_사전검사에_배선돼_있다():
    class _F:
        origin_request = "게임 만들어줘"
    errs = stage_preflight("goal", "[수렴안]\n목표: 2D 회피 게임. 멀티플레이 없음\n[/수렴안]", _F())
    assert any("갈 수 있는 곳까지" in e for e in errs)


def test_회의_골격이_보여주는_예시_로드맵은_관문을_통과한다():
    """골격이 반려당할 예시('최소버전 → 확장 → 완성')를 보여주면 봇은 그대로 베껴서 막힌다."""
    import re
    from system.rule.milestone import _STAGE_META, _STAGE_FRAME
    for src in (_STAGE_META["milestone"][1], _STAGE_FRAME.get("milestone", "")):
        for line in str(src).splitlines():
            if not line.startswith("단계:") or "예:" not in line:
                continue
            ex = re.sub(r".*예:\s*", "", line).rstrip("⟧ ")
            assert not roadmap_process_errors([p.strip() for p in ex.split("→")]), ex


def test_빼는_말_없이_규모만_줄인_목표도_반려된다():
    """[ch263 실측] "브라우저에서 즉시 실행되는 1인용 별 수집 미니게임 … 한 세션 60초 이내"."""
    e = goal_narrowing_error("브라우저에서 즉시 실행되는 1인용 별 수집 미니게임을 만든다. "
                             "한 세션은 60초 이내이며 키보드·터치로 이동", "게임 만들어줘")
    assert e and "갈 수 있는 곳까지" in e


def test_사용자가_미니게임을_요청하면_통과한다():
    assert not goal_narrowing_error("별 수집 미니게임을 만든다", "미니게임 하나 만들어줘")
