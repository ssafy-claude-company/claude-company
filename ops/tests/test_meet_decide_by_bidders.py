"""혼자 응찰하면 그 한 명이 정한다(독식) — 심의단은 1명부터 선다."""
import inspect

from system.rule import communication as _c


def test_심의단은_1명부터_선다():
    src = inspect.getsource(_c)
    assert "if len(_sel) >= 1:" in src, "2명 이상일 때만 서면 혼자 응찰한 도메인이 전원 표결에 묻힌다"


def test_유권자는_말한_사람이다():
    """[2026-08-06 확장] 계약은 '말한 사람이 정한다'다 — 응찰(members)이 그 통로였는데, 오늘 복원한
    goal·criteria 독립 라운드는 fork로 걷는 발언이라 응찰을 거치지 않는다. 전원이 자기 설계를 냈는데
    표결은 한 명이었다(실측 U-519). 독립 라운드 발언자도 유권자에 넣는다 — 계약을 어기는 것이 아니라
    지킨다. 팀 전원(_team_full) 표결로 되돌아가는 것은 여전히 금지."""
    src = inspect.getsource(_c)
    assert "_voters = list(dict.fromkeys(list(members) + sorted(_indep_spoke)))" in src
    assert "_team_full) + list(members)" not in src, "전원 표결로 되돌아가면 말과 결정이 다시 어긋난다"
    assert "_indep_spoke.add(int(_m))" in src, "독립 라운드 발언 기록이 없으면 유권자가 안 된다"


def test_단독_결정은_그렇게_표기된다():
    src = inspect.getsource(_c)
    assert "단독 결정" in src
