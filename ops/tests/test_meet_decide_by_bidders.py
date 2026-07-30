"""혼자 응찰하면 그 한 명이 정한다(독식) — 심의단은 1명부터 선다."""
import inspect

from system.rule import communication as _c


def test_심의단은_1명부터_선다():
    src = inspect.getsource(_c)
    assert "if len(_sel) >= 1:" in src, "2명 이상일 때만 서면 혼자 응찰한 도메인이 전원 표결에 묻힌다"


def test_유권자는_응찰자다():
    src = inspect.getsource(_c)
    assert "_voters = list(dict.fromkeys(list(members)))" in src
    assert "_team_full) + list(members)" not in src, "전원 표결로 되돌아가면 말과 결정이 다시 어긋난다"


def test_단독_결정은_그렇게_표기된다():
    src = inspect.getsource(_c)
    assert "단독 결정" in src
