"""기준이 한쪽으로 굳는다 (2026-08-06, 사용자: '왜 자꾸 봇이 60초 60초 거리고 단일 피하는 게임이나
반복적인 모습을 보이는 듯 하지?').

[규명 — 실측] 봇 개인 기준 151건의 축별 포함 비율:
    검증·증거 94% · 범위 축소 54% · 완성도·경험 35% · **성장·깊이 7%**

관문이 '닫힘'만 보상했으니 경험이 검증으로만 쌓였고, 그 기준이 **매 첫 wake에 프롬프트로 주입된다**
(craft_note). 그래서 다음 설계도 '검증하기 쉬운 최소'로 나온다 — 60초 단일 루프는 헤드리스
스크립트로 완벽히 판정되지만 로그라이크의 성장·증강은 그렇지 않다. 경험 → 증류 → 기준 → 다음
설계로 도는 자기강화 고리다.

[수리] 증류가 '어떻게 확인하나'뿐 아니라 '무엇이 좋은 산출물인가'도 남기게 한다. 내용을 지시하지
않고(무엇이 좋은지는 각자가 정한다) 두 축이 다 있는지만 묻는다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system import sys_core


def _distill_src():
    src = inspect.getsource(sys_core)
    i = src.find("[자기계발 시간 — 개인 기준 증류]")
    assert i > 0, "증류 프롬프트를 찾지 못했다"
    return src[i:i + 1800]


def test_증류가_두_축을_모두_요구한다():
    b = _distill_src()
    assert "두 축을" in b and "검증·증거" in b and "완성도" in b


def test_한쪽만_쌓일_때의_결과를_알려준다():
    b = _distill_src()
    assert "검증하기 쉬운 최소" in b, "왜 균형이 필요한지가 빠지면 지시로만 읽힌다"


def test_내용을_지시하지_않는다():
    """무엇이 좋은 산출물인지는 각자가 정한다 — 시스템은 축이 있는지만 본다."""
    b = _distill_src()
    for banned in ("반드시 60초", "장르는", "로그라이크로"):
        assert banned not in b


def test_예산_규칙은_그대로다():
    b = _distill_src()
    assert "예산: 전체" in b and "일회성 디테일" in b
