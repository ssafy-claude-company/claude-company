"""[U-074 실측 회귀(2026-07-28)] 같은 벽인데 사람에게 안 넘어가던 것.

계획 회의 초안이 등록 프리플라이트에 막히면 그 사유를 세어 3회면 사람에게 넘긴다. 그런데 열쇠가
**첫 사유 하나**였다: 같은 벽에서 검사 순서가 바뀌면(실증 명령 불가 ↔ GOAL@ 비준 누락) 열쇠가
달라져 카운터가 1,1,1,2로 흩어졌고, 판은 같은 자리를 돌며 1,315크레딧을 태우고 산출물 0으로 끝났다.
"""
from system.rule.communication import preflight_wall_key

ERR_CMD = ("마일스톤 완수조건은 회의가 비준한 exact executable verifier command여야 합니다 "
           "(자연어 절차·나중 QA의 임의 명령 제안은 release 증거가 될 수 없음).")
ERR_GOAL = ("최종 마일스톤의 자연어 GOAL 조건은 SYS가 DRAFT에 붙인 GOAL@spec-hash 1:1 행에서 "
            "exact command를 비준해야 합니다. 누락: 핵심 루프가 시작→플레이…")


def test_사유_순서가_바뀌어도_같은_벽():
    assert preflight_wall_key([ERR_CMD, ERR_GOAL]) == preflight_wall_key([ERR_GOAL, ERR_CMD])


def test_다른_벽은_다른_열쇠():
    assert preflight_wall_key([ERR_CMD]) != preflight_wall_key([ERR_GOAL])
    assert preflight_wall_key([ERR_CMD, ERR_GOAL]) != preflight_wall_key([ERR_CMD])


def test_같은_벽_3회면_사람에게_넘어간다():
    """실제 카운팅 규약을 그대로 재현 — U-074에서 이게 1,1,1,2로 흩어졌다."""
    seen, order = {}, [[ERR_CMD, ERR_GOAL], [ERR_GOAL, ERR_CMD], [ERR_CMD, ERR_GOAL]]
    hits = []
    for errs in order:
        k = preflight_wall_key(errs)
        seen[k] = seen.get(k, 0) + 1
        hits.append(seen[k])
    assert hits == [1, 2, 3]            # 종전 구현이면 [1, 1, 2] — 3에 영원히 못 닿는다


def test_빈_사유와_공백은_무시된다():
    assert preflight_wall_key([]) == ""
    assert preflight_wall_key(["", "   ", None]) == ""
    assert preflight_wall_key([ERR_CMD, ERR_CMD]) == preflight_wall_key([ERR_CMD])
