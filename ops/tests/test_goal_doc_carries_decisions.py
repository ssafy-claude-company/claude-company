"""산출물 문서가 결정을 담는다 (2026-08-07, 사용자: '품질이나 협업 대화나 내용은 얼마나 고도화
됐는지 봐봐').

[실측 대조] 워크스페이스의 GOAL.md 81개를 세어 시절별로 갈랐다.

    디스코드 시절(p-001~040)  n= 6   GOAL 72.8줄 · Acceptance 23.7항목 · MINUTES 183.7줄
    현행(p-060~)              n=13   GOAL 25.4줄 · Acceptance  3.4항목 · MINUTES  77.9줄

가장 두꺼운 p-032는 GOAL.md 안에 완수조건 55항목을 담고 있었다. 현행 판(U-520=p-098)의 GOAL.md는
`## Acceptance`가 **빈 줄**인데, 같은 판 채널에는 '완수조건 19개 등록'이 찍혀 있다.

원인: `_write_goal_md`가 **goal 단계 등록에서 딱 한 번** 불린다. 그때 완수조건은 아직 없으므로
Acceptance가 빈칸으로 굳고, 뒤이어 criteria 회의가 조건을 등록해도 파일은 갱신되지 않는다.
결정은 장부(flow.current.acceptance)에만 살고 산출물 문서에는 없다.

조건이 정해지는 자리에서 문서를 다시 쓴다. 비준안 원문은 goal 때 보관해 두고 재사용한다
(재서술하면 표결로 확정된 문장이 흔들린다).
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import milestone as M


def test_criteria_등록이_GOAL_문서를_다시_쓴다():
    src = inspect.getsource(M.register_stage)
    i = src.find('_cur.acceptance = "\\n".join')
    assert i > 0, "완수조건 기록 지점이 사라졌다"
    assert "_write_goal_md(" in src[i:i + 900], "조건을 정하고도 GOAL.md를 갱신하지 않는다"


def test_비준안_원문을_보관해_재사용한다():
    src = inspect.getsource(M.register_stage)
    assert "_cur.goal_decision = str(prop" in src, "표결로 확정된 원문이 보관되지 않는다"
    i = src.find('_cur.acceptance = "\\n".join')
    assert 'getattr(_cur, "goal_decision"' in src[i:i + 900], "갱신 때 원문을 재서술한다"


def test_문서_틀에_결정_칸이_모두_있다():
    src = inspect.getsource(M._write_goal_md)
    for sec in ("Ratified Decision", "Purpose", "Goal", "Acceptance", "Standard", "Interfaces"):
        assert f"## {sec}" in src, sec
