"""실증은 판정해야 한다 — 띄우기만 하는 명령은 완수조건이 될 수 없다."""
from system.rule.milestone import gate_criteria, verify_is_serve_only


def test_기동만_하는_명령은_실증이_아니다():
    """[U-436 실측] `python3 -m http.server …`가 완수조건 실증으로 통과해 GOAL에 박혔고, 뒤 회의가
    '기동만으로는 pass가 아니다' ↔ '그럼 무슨 명령이냐'로 3패스를 돌다 판이 파킹됐다."""
    assert verify_is_serve_only("python3 -m http.server 4173 --directory public")
    assert verify_is_serve_only("node server.js")
    assert verify_is_serve_only("npm run dev")


def test_기동에_점검이_붙으면_실증이다():
    assert not verify_is_serve_only(
        "python3 -m http.server 4173 --directory public & sleep 1; curl -fsS localhost:4173/")
    assert not verify_is_serve_only("node server.js & sleep 1; python3 verify_ui.py")


def test_보통_검증명령은_영향_없다():
    for v in ("pytest -q", "python3 verify_loop.py", "npm test", "node scripts/check.mjs"):
        assert not verify_is_serve_only(v), v


def test_게이트가_그_조건을_반려한다():
    err = gate_criteria([{"desc": "로컬에서 한 판이 돈다",
                          "verify": "python3 -m http.server 4173 --directory public"}])
    assert err and "띄우기만" in err


def test_점검이_붙은_조건은_통과한다():
    err = gate_criteria([{"desc": "로컬에서 한 판이 돈다",
                          "verify": "python3 -m http.server 4173 --directory public "
                                    "& sleep 1; curl -fsS localhost:4173/ >/dev/null"}])
    assert not err, err
