"""한 글자 백로그가 작업 목록에 남던 자리(U-442 실측: 본문 'x')."""
import re

from system import guide_tools


def _src():
    import inspect
    return inspect.getsource(guide_tools)


def test_짧은_본문은_등재_거부_문구를_갖는다():
    src = _src()
    assert "등재 거부: 백로그 본문이 너무 짧습니다" in src
    assert "len(desc) < 10 or len(desc.split()) < 2" in src


def test_거부_문구가_무엇을_쓰라고_알려준다():
    m = re.search(r"등재 거부: 백로그 본문이 너무 짧습니다[\s\S]{0,400}", _src())
    assert m and "한 문장" in m.group(0) and "예:" in m.group(0)
