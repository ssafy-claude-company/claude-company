"""정본은 진행 중인 일도 담아야 한다(2026-08-03, 실측 U-478).

디스크 장부 `.collab/MILESTONES.md`는 지금까지 단계가 **닫힐 때 한 번만** 쓰였다
(rule.backlog.on_subtask_wrapup — 유일한 기록 지점). 그래서 진행 중인 단계는 정본에 아예 없고,
"정본을 Read로 대조"한 봇은 자기가 지금 일하는 단위를 못 찾는다.

실측 U-478 10:32~10:45 — 팀은 ST-7에서 일하는 중이었는데:
  "MILESTONES.md에는 ST-1~ST-6만 있고 B73/B74 및 MS-585233967-2/ST-7::B73의 공식 ID·goal·
   owner·predecessor가 없으며 …"
ST-7은 11:07 마감 시점에야 파일에 처음 나타났다. 그 사이 같은 확인이 다섯 턴 반복됐고
주기 마감도 못 했다. 봇이 틀린 게 아니라 정본이 비어 있었다.
"""
import io
import os

import pytest

from system.flow import Flow
from system.rule.milestone import Milestone, open_subtask


class _G:
    async def post(self, *a, **k):
        return None


@pytest.fixture()
def flow(tmp_path, monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = Flow(_G(), channel_id=810, guild_id=1, leader_id=11, bot_info={11: "리더"})
    f.workspace = str(tmp_path)
    f.root_id = "T-1"

    class _Cur:
        task_id = "T-1"
    f.current = _Cur()
    return f


def _ledger(flow):
    for root, _dirs, files in os.walk(flow.workspace):
        if "MILESTONES.md" in files:
            return io.open(os.path.join(root, "MILESTONES.md"), encoding="utf-8").read()
    return ""


def test_단계를_열면_정본에_그_단위가_바로_남는다(flow):
    ms = Milestone("MS-L", "판", [])
    flow.milestones = [ms]

    st = open_subtask(flow, ms, "배포·인프라", [])

    text = _ledger(flow)
    assert st.st_id in text, "진행 중인 단계가 정본에 없다 — 봇의 '정본 대조'가 그 단위를 못 찾는다"
    assert "배포·인프라" in text


def test_마감_기록은_종전대로_이어_쓴다(flow):
    from system.rule.backlog import on_subtask_wrapup, relay_for

    ms = Milestone("MS-L", "판", [])
    flow.milestones = [ms]
    st = open_subtask(flow, ms, "구현", [])
    r = relay_for(flow, st)
    b = r.submit(11, "실제 일감", force=True)
    r.pick(11, b.backlog_id, 11)
    r.done(11, b.backlog_id)

    on_subtask_wrapup(flow, st)

    text = _ledger(flow)
    assert text.count(st.st_id) >= 2, "개설 기록과 마감 기록이 함께 남아야 한다"
    assert b.backlog_id in text
