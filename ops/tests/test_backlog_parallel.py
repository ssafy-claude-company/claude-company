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


def test_같은_영역이어도_막지_않는다(tmp_path):
    """[전원 병렬(2026-07-31, 사용자 지시)] 영역이 겹쳐도 착수는 막지 않는다 — 직원은 자기 것을 한다.
    (쓰기 영역 선언은 남는다: 무엇을 고칠 일인지 서로 읽는 정보로.)"""
    f, r = _relay(tmp_path)
    a = r.submit(12, "[쓰기: public/] 화면 만들기", force=True)
    b = r.submit(13, "[쓰기: public/index.html] 같은 화면 고치기", force=True)
    r.pick(11, a.backlog_id, 12)
    r.turn_holder = 11
    assert r.pick(11, b.backlog_id, 13).status == "in_progress"


def test_선언이_없어도_동시에_간다(tmp_path):
    f, r = _relay(tmp_path)
    a = r.submit(12, "화면 만들기", force=True)
    b = r.submit(13, "검증기 만들기", force=True)
    r.pick(11, a.backlog_id, 12)
    r.turn_holder = 11
    assert r.pick(11, b.backlog_id, 13).status == "in_progress"


def test_상한은_사람_수만큼_열린다():
    assert backlog_parallel_width() >= 8
