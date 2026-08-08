"""합류는 즉시 영속된다 (2026-08-07, 실측 U-534).

팀 명단은 체크포인트에서 복원된다. 그런데 채용 직후 체크포인트가 없으면 **재시작 한 번에 그
사람이 팀에서 사라진다**:

    17:13:20  [채용 확정] 게임 비주얼 — genesis        ← 합류
    (러너 재시작)
    17:22:36  [지원] 게임 비주얼을 맡겠습니다           ← 같은 봇이 지원
    17:22:39  [채용 확정] 게임 비주얼 — 지원서 선발     ← 다시 확정

'이미 팀인 사람은 다시 뽑지 않는다' 관문(2026-08-07)은 flow.current.team을 본다 — 그 명단이
복원에서 비면 관문이 성립하지 않는다. 팀이 흔들리면 채용·정족수·표결 자격이 함께 흔들린다.

사람이 늘어난 그 자리에서 바로 적는다. 복원 규칙(스냅샷 팀을 좁히지 않는다)은 건드리지 않는다 —
그쪽을 넓히면 이 판에 합류하지 않은 사원까지 팀이 된다(test_open_task_복원은_프로젝트팀을_좁히지_않는다).
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import comm_ceremonies as CC
from system import sys_recovery as SR


def test_합류_직후_체크포인트를_찍는다():
    src = inspect.getsource(CC._recruit_join)
    i = src.find("_add_members(g, flow.current.thread_id, [mid])")
    assert i > 0, "합류 지점이 사라졌다"
    assert "_ckpt_join(flow)" in src[i:i + 900], "합류가 영속되지 않는다"


def test_팀에_넣은_뒤에_찍는다():
    """순서가 뒤집히면 방금 넣은 사람이 스냅샷에 안 들어간다."""
    src = inspect.getsource(CC._recruit_join)
    i = src.find("flow.current.team.append(mid)")
    j = src.find("_ckpt_join(flow)")
    assert 0 < i < j, "체크포인트가 팀 추가보다 먼저다"


def test_복원_규칙은_그대로다():
    """스냅샷 팀을 넓히는 방식은 기각됐다 — 이 판에 합류하지 않은 사원까지 팀이 된다."""
    src = inspect.getsource(SR)
    assert "restored_team_from_roster" not in src
    assert "restored_team_readmitted" in src, "종전 복원 규칙이 사라졌다"
