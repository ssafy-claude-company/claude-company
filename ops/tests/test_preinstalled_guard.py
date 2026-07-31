"""이미 갖춰진 것을 다시 내려받아 12분·800MB를 태우던 자리(U-442 실측)."""
from system.guide_tools import _preinstalled_refusal


def test_브라우저_재설치는_거절되고_쓰는_법을_알려준다():
    r = _preinstalled_refusal("npx playwright install chromium")
    assert "이미" in r and "PLAYWRIGHT_BROWSERS_PATH" in r


def test_python_패키지_재설치도_거절된다():
    r = _preinstalled_refusal("pip install --target .qa-deps playwright")
    assert "이미" in r and "import playwright" in r


def test_보통_명령은_통과한다():
    assert _preinstalled_refusal("npm test") == ""
    assert _preinstalled_refusal("pip install requests") == ""
