"""반려는 사유를 말해야 한다 + 주기는 배달까지다(U-442 실측: 8시간 파킹, 열리지 않는 링크)."""
import types

from system.rule.evidence import verifier_reject_reason
from system.rule.milestone import cycle_delivery_error, stage_preflight


def test_서버_기동을_이어붙인_명령은_어긴_규칙을_말해준다():
    """[U-442 실측] `… && (PORT=4173 npm run start & sleep 5; python3 verify.py)`로 3회 막혀 파킹."""
    r = verifier_reject_reason(
        "npm test && npm run lint && (PORT=4173 npm run start & sleep 5; python3 v.py)",
        require_existing=False)
    assert "백그라운드" in r and "스크립트 하나로 감싸세요" in r


def test_띄우기만_하는_명령과_빈말_명령도_사유가_다르다():
    a = verifier_reject_reason("python3 -m http.server 3000", require_existing=False)
    b = verifier_reject_reason("echo ok", require_existing=False)
    assert "종료코드" in a and "판정하지 않는" in b and a != b


def test_정상_명령은_사유가_없다():
    assert verifier_reject_reason("pytest -q tests/test_a.py", require_existing=False) == ""


def test_반려_문구가_회의_사전검사에_실려나온다():
    f = types.SimpleNamespace(workspace="", milestones=[], current=None)
    errs = stage_preflight(
        "milestone",
        "## 결정\n단계: 혼자 쓰는 판 → 둘이 쓰는 판\n이번 주기: 한 판\n"
        "- 한 판이 끝난다 | 실증: npm start & sleep 5\n## 참고 (자유 — 판정 대상 아님)\n", f)
    assert any("백그라운드" in e for e in errs)


def test_열_수_있는_주소가_없으면_주기가_안_닫힌다(tmp_path):
    """[U-442 실측] 로컬 헤드리스 검증만으로 주기가 닫혀 보고된 링크가 404였다."""
    ms = types.SimpleNamespace(goal="브라우저에서 여는 한 판", criteria=[])
    f = types.SimpleNamespace(workspace=str(tmp_path), milestones=[ms])
    assert "사람이 열 수 있는 곳이 없습니다" in cycle_delivery_error(f)
    (tmp_path / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    assert cycle_delivery_error(f) == ""


def test_웹이_아닌_산출물엔_배달을_요구하지_않는다(tmp_path):
    ms = types.SimpleNamespace(goal="CSV를 집계하는 커맨드라인 도구", criteria=[])
    f = types.SimpleNamespace(workspace=str(tmp_path), milestones=[ms])
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    assert cycle_delivery_error(f) == ""


def test_밖에만_올린_것은_배달이_아니다(tmp_path, monkeypatch):
    """[사용자 지시(2026-07-31)] 외부 배포는 인정하되, 우리 판에서도 열려야 검증할 수 있다.
    (U-442 실측: 외부 주소는 401 Sign in required였고 사용자는 아무것도 열지 못했다.)"""
    import types

    from system.rule import milestone as _ms
    ms = types.SimpleNamespace(goal="브라우저에서 여는 한 판", criteria=[])
    f = types.SimpleNamespace(workspace=str(tmp_path), milestones=[ms],
                              _deploy_url="https://example.invalid/app/", _deploy_live=True)
    err = _ms.cycle_delivery_error(f)
    assert "이 판에서 열 수 있는 입구가 없습니다" in err and "앱 풀에도" in err


def test_정적_진입이_있으면_주소가_죽어도_닫힌다(tmp_path):
    """주소가 안 열려도 이 판이 직접 서빙할 수 있으면 사람은 열 수 있다."""
    import types

    from system.rule import milestone as _ms
    (tmp_path / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    ms = types.SimpleNamespace(goal="브라우저에서 여는 한 판", criteria=[])
    f = types.SimpleNamespace(workspace=str(tmp_path), milestones=[ms],
                              _deploy_url="https://example.invalid/app/")
    assert _ms.cycle_delivery_error(f) == ""
