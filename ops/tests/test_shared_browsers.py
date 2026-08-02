"""[같은 브라우저를 판마다 한 벌씩 더 올린다(2026-08-02, 실측)] 구판은 `.qa-browsers`(646MB)를 명령에
경로째 박아 쓰고 있었다 — 공유 캐시에 같은 개정판이 다 있는데도. 4코어 머신에 같은 chromium이 두 벌
올라가 두 판이 서로를 느리게 만들었다."""
import json

from system.guide_tools import prefer_shared_browsers


def _ws(tmp_path, rev="1538"):
    d = tmp_path / "node_modules" / "playwright-core"
    d.mkdir(parents=True)
    (d / "browsers.json").write_text(
        json.dumps({"browsers": [{"name": "firefox", "revision": rev}]}), encoding="utf-8")
    return str(tmp_path)


def test_공유캐시에_다_있으면_판별경로를_공유로_바꾼다(tmp_path):
    cache = tmp_path / "cache"
    (cache / "firefox-1538").mkdir(parents=True)
    ws = _ws(tmp_path / "ws")
    out, note = prefer_shared_browsers("PLAYWRIGHT_BROWSERS_PATH=.qa-browsers node t.mjs", ws, str(cache))
    assert out == f"PLAYWRIGHT_BROWSERS_PATH={cache} node t.mjs"
    assert ".qa-browsers" in note


def test_빈_자리가_있으면_손대지_않는다(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    ws = _ws(tmp_path / "ws")
    cmd = "PLAYWRIGHT_BROWSERS_PATH=.qa-browsers node t.mjs"
    assert prefer_shared_browsers(cmd, ws, str(cache)) == (cmd, "")


def test_이미_공유캐시면_그대로_둔다(tmp_path):
    cache = tmp_path / "cache"
    (cache / "firefox-1538").mkdir(parents=True)
    ws = _ws(tmp_path / "ws")
    cmd = f"PLAYWRIGHT_BROWSERS_PATH={cache} node t.mjs"
    assert prefer_shared_browsers(cmd, ws, str(cache)) == (cmd, "")


def test_변수가_없는_명령은_그대로(tmp_path):
    cmd = "npm run test"
    assert prefer_shared_browsers(cmd, str(tmp_path)) == (cmd, "")
