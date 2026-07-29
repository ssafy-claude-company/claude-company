"""주기 안에서 풀 수 없는 막힘(e2e 재실증)은 접히고, 그 밖의 막힘은 그대로 남는다."""
from system.rule.backlog import DROPPED, drop_unresolvable_blocked, relay_for
from system.rule.milestone import open_milestone, open_subtask


class _G:
    async def post(self, *a, **k):
        return None


def _flow(tmp_path):
    from system.flow import Flow
    f = Flow(_G(), channel_id=1, guild_id=1, leader_id=11, bot_info={11: "L", 12: "QA"})
    f.start_root("root")
    f.workspace = str(tmp_path)
    return f


def test_e2e_재실증만_남은_막힘은_접힌다(tmp_path):
    f = _flow(tmp_path)
    ms = open_milestone(f, "결함 해소", [{"desc": "동작", "verify": "pytest"}])
    st = open_subtask(f, ms, "검증", [])
    r = relay_for(f, st)
    b1 = r.submit(12, "지정 명령을 봉인·1회 실행하고 신규 SYS receipt를 남긴다", force=True)
    b2 = r.submit(11, "화면 문구 정리", force=True)
    b1.status = "blocked"
    b1.block_reason = "release/e2e challenge의 정확한 target id 부재로 condition:13 봉인 불가"
    out = drop_unresolvable_blocked(f)
    assert out == [b1.backlog_id]
    assert b1.status == DROPPED and b2.status == "open"
    assert any("[접음]" in a for a in (b1.activity or []))


def test_사유가_활동기록에만_있어도_접는다(tmp_path):
    """[재시작 뒤 실측] block_reason은 복원에서 비고 본문에 'e2e'가 없을 수 있다 — 무엇에 막혔는지는
    그 백로그의 활동 기록에 남는다. 세 곳을 함께 읽어야 규칙이 실제로 걸린다."""
    f = _flow(tmp_path)
    ms = open_milestone(f, "결함 해소", [{"desc": "동작", "verify": "pytest"}])
    st = open_subtask(f, ms, "검증", [])
    r = relay_for(f, st)
    b = r.submit(12, "아래 명령을 봉인·1회 실행하고 신규 SYS receipt를 남긴다", force=True)
    b.status = "blocked"
    b.block_reason = ""
    b.activity.append("[QA] 💭 e2e_open이 미완 마일스톤 때문에 거절되고 condition:13 target이 없어 봉인 불가")
    assert drop_unresolvable_blocked(f) == [b.backlog_id]
    assert b.status == DROPPED


def test_보통_막힘은_접지_않는다(tmp_path):
    f = _flow(tmp_path)
    ms = open_milestone(f, "결함 해소", [{"desc": "동작", "verify": "pytest"}])
    st = open_subtask(f, ms, "검증", [])
    r = relay_for(f, st)
    b = r.submit(12, "입력 로직 구현", force=True)
    b.status = "blocked"
    b.block_reason = "선행 산출물(디자인 시안) 대기"
    assert drop_unresolvable_blocked(f) == []
    assert b.status == "blocked"
