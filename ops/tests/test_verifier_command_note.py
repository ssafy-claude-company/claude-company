"""실증 명령 뒤에 붙은 설명 때문에 회의가 막히던 자리(ch263 실측)."""
from system.rule.evidence import direct_verifier_command, normalize_verifier_command


def test_명령_뒤_설명이_붙어도_명령으로_읽는다():
    v = "`python3 verify_ui.py` (exit code 0 required; non-zero on failure or missing verifier)."
    assert normalize_verifier_command(v) == "python3 verify_ui.py"
    assert direct_verifier_command(v, require_existing=False) == "python3 verify_ui.py"


def test_설명이_없으면_종전과_같다():
    assert normalize_verifier_command("`pytest -q`") == "pytest -q"
    assert normalize_verifier_command("pytest -q") == "pytest -q"


def test_뒤가_쉘_연결자면_손대지_않는다():
    v = "`pytest -q` && echo ok"
    assert normalize_verifier_command(v) == v
    assert direct_verifier_command(v, require_existing=False) == ""


def test_한글_설명이_붙은_명령도_읽는다():
    v = "`node verify.mjs` (통과면 exit 0)"
    assert normalize_verifier_command(v) == "node verify.mjs"
