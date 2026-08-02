"""[없는 것을 '있다'고 거절하면 봇이 세 시간을 태운다(2026-08-02, U-478 실측)]

작업공간 node playwright 1.62.1은 firefox-1538·webkit-2336을 기대하는데 공유 캐시에는 1532·2311만
있었다. 거절 문구가 "이미 있음"이라 봇은 그 말을 믿고 /tmp에 apt 상태 디렉터리를 만들어 시스템
패키지를 손으로 풀었고, 백로그가 세 번 차단돼 판이 파킹됐다."""
import json

from system.guide_tools import _preinstalled_refusal, browser_build_gap


def _ws(tmp_path, browsers):
    d = tmp_path / "node_modules" / "playwright-core"
    d.mkdir(parents=True)
    (d / "browsers.json").write_text(json.dumps({"browsers": browsers}), encoding="utf-8")
    return str(tmp_path)


def test_캐시에_없는_개정판을_찾아낸다(tmp_path):
    cache = tmp_path / "cache"
    (cache / "firefox-1532").mkdir(parents=True)
    ws = _ws(tmp_path / "ws", [{"name": "firefox", "revision": "1538"},
                               {"name": "webkit", "revision": "2336"}])
    assert sorted(browser_build_gap(ws, str(cache))) == ["firefox-1538", "webkit-2336"]


def test_캐시에_다_있으면_빈_목록(tmp_path):
    cache = tmp_path / "cache"
    (cache / "firefox-1538").mkdir(parents=True)
    ws = _ws(tmp_path / "ws", [{"name": "firefox", "revision": "1538"}])
    assert browser_build_gap(ws, str(cache)) == []


def test_읽을_수_없으면_거절하지_않는다(tmp_path):
    assert browser_build_gap(str(tmp_path), str(tmp_path)) == []
    assert browser_build_gap("", "") == []


def test_빈_자리가_있으면_거절_대신_받는_법을_준다(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    ws = _ws(tmp_path / "ws", [{"name": "webkit", "revision": "2336"}])
    import system.guide_tools as gt
    old, gt.PW_CACHE = gt.PW_CACHE, str(cache)
    try:
        msg = _preinstalled_refusal("npx playwright install webkit", ws)
    finally:
        gt.PW_CACHE = old
    assert "설치 필요" in msg and "webkit-2336" in msg and "PLAYWRIGHT_BROWSERS_PATH=$PWD/.pw" in msg


def test_빈_자리가_없으면_종전대로_거절한다(tmp_path):
    cache = tmp_path / "cache"
    (cache / "webkit-2336").mkdir(parents=True)
    ws = _ws(tmp_path / "ws", [{"name": "webkit", "revision": "2336"}])
    import system.guide_tools as gt
    old, gt.PW_CACHE = gt.PW_CACHE, str(cache)
    try:
        msg = _preinstalled_refusal("npx playwright install webkit", ws)
    finally:
        gt.PW_CACHE = old
    assert "실행 거부(이미 있음)" in msg
