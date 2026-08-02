"""[같은 검증을 고친 것 없이 되돌린다(2026-08-02, 감사로그 실측)] 최근 24시간 run 203회 중 51%가 같은
명령의 반복이었다(`npm run verify:milestone1` 34회, `npm run test:all` 18회). 작업공간이 한 글자도
안 바뀐 채 같은 명령을 또 돌리면 같은 결과를 다시 사는 것이다. 막지는 않고 사실만 알린다."""
from system.guide_tools import run_repeat_note


class _F:
    log = None


def test_두번째까지는_조용하다():
    f = _F()
    assert run_repeat_note(f, "npm test", "AAA") == ""
    assert run_repeat_note(f, "npm test", "AAA") == ""


def test_세번째부터_사실을_알린다():
    f = _F()
    for _ in range(2):
        run_repeat_note(f, "npm test", "AAA")
    msg = run_repeat_note(f, "npm test", "AAA")
    assert "3번째" in msg and "block_backlog" in msg


def test_작업공간이_바뀌면_처음부터_센다():
    f = _F()
    for _ in range(3):
        run_repeat_note(f, "npm test", "AAA")
    assert run_repeat_note(f, "npm test", "BBB") == ""


def test_다른_명령은_따로_센다():
    f = _F()
    for _ in range(3):
        run_repeat_note(f, "npm test", "AAA")
    assert run_repeat_note(f, "npm run build", "AAA") == ""


def test_스탬프를_못_뜨면_알리지_않는다():
    assert run_repeat_note(_F(), "npm test", "") == ""
