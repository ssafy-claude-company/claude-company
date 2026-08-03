"""가리키는 파일이 없는 검증 명령은 봉인하지 않는다(2026-08-03, 실측 U-478·U-496).

봉인은 지금까지 명령의 **형태**만 봤다(command_matches_spec). 그래서 이 작업공간에 없는
절대경로가 그대로 봉인되고, 다음 run이 exit 2로 죽는다. receipt는 rc=0에서만 발급되므로
그 항목은 결과 없이 실패로 남고, e2e judge는 "검증기가 못 돌았다"와 "제품이 틀렸다"를
구분하지 않으므로(rule.wrapup.judge:87-95) 그것이 **제품 결함**으로 집계돼 수리 주기가 열린다.

실측 U-496 13:04 — e2e 결함 2건의 실제 원인을 팀이 이렇게 적었다:
  "이전 실패 원인은 봉인된 verifier의 잘못된 절대경로로 guide에서 exit 2/receipt 미발급된 것"
같은 날 U-478도 e2e 결함 1건으로 수리 주기를 열었지만, 그 작업공간에서 verify-milestone1.mjs를
직접 돌리면 두 뷰포트 3/3 PASS다(터치 포함).

판정자는 이미 있다 — _goal_verifier_unrunnable이 쓰는 술어("형태는 실행 명령인데 파일이 없다").
봉인 전에 말해 주면 결함 기록도 수리 주기도 생기지 않는다.
"""
import io
import os

import pytest

from system.guide_tools import _seal_verifier_command
from system.rule.milestone import Criterion, Milestone
from system.flow import Flow


class _G:
    async def post(self, *a, **k):
        return None


def _flow(tmp_path):
    f = Flow(_G(), channel_id=820, guild_id=1, leader_id=11, bot_info={11: "리더"})
    f.workspace = str(tmp_path)
    return f


def test_없는_파일을_가리키는_명령은_봉인이_거절한다(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    from system.rule.evidence import direct_verifier_command

    flow = _flow(tmp_path)
    cmd = "node scripts/verify-missing.mjs"
    # 전제 확인 — 형태는 실행 명령인데 파일이 없다(이 술어가 이 검사의 근거다)
    assert direct_verifier_command(cmd, str(tmp_path), require_existing=False)
    assert not direct_verifier_command(cmd, str(tmp_path), require_existing=True)


def test_있는_파일이면_그_술어가_통과한다(tmp_path):
    from system.rule.evidence import direct_verifier_command

    os.makedirs(os.path.join(tmp_path, "scripts"), exist_ok=True)
    io.open(os.path.join(tmp_path, "scripts", "verify-real.mjs"), "w").write("// ok\n")
    cmd = "node scripts/verify-real.mjs"
    assert direct_verifier_command(cmd, str(tmp_path), require_existing=True)


def test_봉인_함수가_그_검사를_실제로_들고_있다():
    """[회귀 고정] 검사 자체가 코드에서 사라지면 같은 사고가 되돌아온다."""
    import inspect

    src = inspect.getsource(_seal_verifier_command)
    assert "require_existing=True" in src and "가리키는 파일이 작업공간에 없습니다" in src
