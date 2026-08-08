"""막힘 표시는 착지에서 풀린다 (2026-08-07 실측 U-536).

주기 2 계획 회의가 형식 검사에 막혀 파킹된 뒤, 관문을 고치고 재개했더니 회의가 정상으로
착지했다. 그런데 판은 다시 멈췄다:

    14259 [회의 마무리][단계:milestone] 결론 … — 마일스톤 MS-138398995-2 등록(조건 1개).
    14260 [막혀서 멈췄어요] … — 막힌 지점 — 계획 회의가 같은 형식 검사에 3회 막혔습니다

성사 바로 다음 줄에 파킹 안내가 섰다. 원인은 해소 자리의 비대칭이다: 착지 시 카운터
(`_consec_stuck`)는 0으로 되돌렸지만 **표시(`_stage_stuck`)는 그대로 뒀다.** 파킹을 집행하는
쪽(sys_core 이어가기 루프)은 그 표시를 보고 판을 세운다 — 이미 해결된 사실로 판을 멈춘 것이다.

착지가 곧 해소다. 카운터와 표시를 같은 자리에서 함께 되돌린다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import communication as C


def test_착지가_막힘_표시를_지운다():
    src = inspect.getsource(C)
    i = src.find("flow._consec_stuck = 0")
    assert i > 0, "착지 리셋 자리가 사라졌다"
    block = src[i:i + 900]
    assert "flow._stage_stuck = None" in block, "카운터만 풀고 표시를 남긴다 — 성사된 판이 멈춘다"


def test_착지하지_못하면_표시는_남는다():
    """해소는 착지에서만 일어난다 — 미착지 회의의 표시를 지우면 파킹이 영영 안 걸린다."""
    src = inspect.getsource(C)
    i = src.find("flow._consec_stuck = 0")
    head = src[max(0, i - 200):i]
    assert "if _landed:" in head, "착지 조건 밖에서 표시를 지운다"


def test_막힘_표시를_세우는_자리는_그대로다():
    """연속 소진 2회면 여전히 파킹 신호를 세운다 — 이 수리는 해소만 더한다."""
    src = inspect.getsource(C)
    assert "if flow._consec_stuck >= 2:" in src
    i = src.find("if flow._consec_stuck >= 2:")
    assert "flow._stage_stuck = (" in src[i:i + 900]
