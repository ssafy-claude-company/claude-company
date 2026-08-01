"""[저작 카운터는 codex 판에서 영원히 0이다(2026-08-01, U-442 실측)] guide의 Write/Edit만 세는
writes_by_role은 codex 봇이 자기 편집기로 고친 파일을 못 본다 — 최근 24시간 감사 로그에 Write/Edit
0건. 그래서 첫 배포 뒤 모든 재배포가 차단됐고, 공개 앱 풀이 옛 자산을 서빙한 채 팀이 교착에 빠졌다.
작업공간 내용 해시가 바뀌면 그것이 곧 '코드가 바뀌었다'는 사실이다."""
import asyncio
import os

from system.flow import Flow
from system.guide_tools import make_guide_tools
from test_sys import FakeGuide


def _flow_with_deploy(monkeypatch, tmp_path, calls):
    import system.deploy as dp
    f = Flow(FakeGuide(), channel_id=500, guild_id=1, leader_id=11, bot_info={11: "L"})
    f.start_root("root")
    f.workspace = str(tmp_path)
    f.project_id = "P-009"
    (tmp_path / "app.js").write_text("console.log(1)\n", encoding="utf-8")

    def fake_deploy_sync(ws, name, *a):
        calls["n"] += 1
        return f"배포 성공 ✅ 라이브: https://organt-{name}.onrender.com"

    monkeypatch.setattr(dp, "deploy_sync", fake_deploy_sync)
    for k in ("GH_PAT", "GH_USER", "RENDER_KEY", "RENDER_OWNER"):
        os.environ.setdefault(k, "x")
    return f, {x.name: x for x in make_guide_tools(f, 11, "leader")}


def test_저작카운터가_0이어도_파일이_바뀌면_재배포된다(monkeypatch, tmp_path):
    calls = {"n": 0}
    f, t = _flow_with_deploy(monkeypatch, tmp_path, calls)
    assert "배포 성공" in asyncio.run(t["deploy"].handler({"name": "site"}))["content"][0]["text"]
    assert calls["n"] == 1
    # codex 봇이 자기 편집기로 고친 상황 — writes_by_role은 그대로 0이다
    (tmp_path / "app.js").write_text("console.log(2)\n", encoding="utf-8")
    assert sum(f.writes_by_role.values()) == 0
    r = asyncio.run(t["deploy"].handler({"name": "site"}))
    assert "배포 성공" in r["content"][0]["text"] and calls["n"] == 2


def test_아무것도_안_바뀌면_종전대로_차단된다(monkeypatch, tmp_path):
    calls = {"n": 0}
    f, t = _flow_with_deploy(monkeypatch, tmp_path, calls)
    asyncio.run(t["deploy"].handler({"name": "site"}))
    r = asyncio.run(t["deploy"].handler({"name": "site"}))
    assert "재배포 차단" in r["content"][0]["text"] and calls["n"] == 1
