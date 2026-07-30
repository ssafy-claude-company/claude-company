"""백로그 병렬 계약 — 쓰기 영역이 겹치지 않을 때만 동시, 겹치면 종전대로 순차."""
import pytest

from system.rule.backlog import (BacklogError, backlog_parallel_width, declared_write_scope,
                                 relay_for, write_scopes_conflict)
from system.rule.milestone import open_milestone, open_subtask


class _G:
    async def post(self, *a, **k):
        return None


def _relay(tmp_path):
    from system.flow import Flow
    f = Flow(_G(), channel_id=1, guild_id=1, leader_id=11, bot_info={11: "L", 12: "M", 13: "Q"})
    f.start_root("root")
    f.workspace = str(tmp_path)
    ms = open_milestone(f, "주기", [{"desc": "동작", "verify": "pytest"}])
    st = open_subtask(f, ms, "단계", [])
    return f, relay_for(f, st)


def test_영역_선언_파싱():
    assert declared_write_scope("[쓰기: public/, scripts/verify.mjs] 화면 만들기") == [
        "public/", "scripts/verify.mjs"]
    assert declared_write_scope("선언 없는 일감") == []


def test_선언이_없으면_모두와_겹친다():
    assert write_scopes_conflict([], ["public/"]) is True
    assert write_scopes_conflict(["public/"], []) is True
    assert write_scopes_conflict(["public/"], ["scripts/"]) is False
    assert write_scopes_conflict(["public/"], ["public/index.html"]) is True


def test_겹치지_않으면_동시_착수된다(tmp_path):
    f, r = _relay(tmp_path)
    a = r.submit(12, "[쓰기: public/] 화면 만들기", force=True)
    b = r.submit(13, "[쓰기: scripts/] 검증기 만들기", force=True)
    r.pick(11, a.backlog_id, 12)
    r.turn_holder = 11
    r.pick(11, b.backlog_id, 13)                    # 겹치지 않음 → 허용
    assert a.status == "in_progress" and b.status == "in_progress"


def test_겹치면_종전대로_막힌다(tmp_path):
    f, r = _relay(tmp_path)
    a = r.submit(12, "[쓰기: public/] 화면 만들기", force=True)
    b = r.submit(13, "[쓰기: public/index.html] 같은 화면 고치기", force=True)
    r.pick(11, a.backlog_id, 12)
    r.turn_holder = 11
    with pytest.raises(BacklogError, match="같은 영역"):
        r.pick(11, b.backlog_id, 13)


def test_선언이_없으면_순차_그대로(tmp_path):
    f, r = _relay(tmp_path)
    a = r.submit(12, "화면 만들기", force=True)
    b = r.submit(13, "검증기 만들기", force=True)
    r.pick(11, a.backlog_id, 12)
    r.turn_holder = 11
    with pytest.raises(BacklogError):
        r.pick(11, b.backlog_id, 13)


def test_상한을_넘으면_대기(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANT_BACKLOG_PARALLEL", "2")
    f, r = _relay(tmp_path)
    xs = [r.submit(12, f"[쓰기: area{i}/] 일 {i}", force=True) for i in range(3)]
    for x in xs[:2]:
        r.turn_holder = 11
        r.pick(11, x.backlog_id, 12)
    r.turn_holder = 11
    with pytest.raises(BacklogError, match="동시 진행 상한"):
        r.pick(11, xs[2].backlog_id, 13)
    assert backlog_parallel_width() == 2
