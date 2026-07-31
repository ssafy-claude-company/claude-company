"""원장 셋째 축의 계약(2026-07-31, 현준-4).

두 축은 이미 있다 - 누가 내나(person), 어디서 태웠나(project). 봇 대여가 붙으면
'누구 엔진으로 돌았나'가 없어서 빌려준 사람과 엔진 제공자의 몫을 나눌 수 없다.
"""
import importlib.util
import os
from types import SimpleNamespace

_BACKEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "murmur", "backend")


def _src(name):
    return open(os.path.join(_BACKEND, "sns", name), encoding="utf-8").read()


def test_적립_실패가_과금을_멈추지_않는다():
    """앞의 두 축과 같은 규칙 - 정산 근거 하나 때문에 청구가 멈추면 안 된다."""
    s = _src("usage.py")
    i = s.index("def accrue_engine(")
    body = s[i:s.index("def engine_for(")]
    assert "except DatabaseError" in body
    assert "return None" in body
    # 조용히 비되 흔적은 남긴다 - 유실을 모르면 정산이 틀린 줄도 모른다.
    assert "warning(" in body


def test_모르면_적지_않는다():
    """붙은 것도 전역 기본도 없으면 None - 운영자 몫으로 적으면 정산이 틀린다."""
    s = _src("usage.py")
    body = s[s.index("def engine_for("):s.index("def project_usage_summary(")]
    assert "return None" in body or "getattr(cfg" in body
    a = s[s.index("def accrue_engine("):]
    assert "if profile is None:" in a


def test_봇에_붙은_설정이_전역보다_먼저다():
    """빌린 봇이 자기 엔진을 들고 오면 그쪽이 이겨야 정산이 맞는다."""
    s = _src("usage.py")
    body = s[s.index("def engine_for("):s.index("def project_usage_summary(")]
    assert body.index("Agent.objects.filter") < body.index("SiteConfig.objects")


def test_주인_유무와_무관하게_적는다():
    """청구가 아니라 정산 근거다 - 운영자 기본 엔진 몫도 보여야 남의 몫을 뺄 수 있다."""
    s = _src("guide_bridge.py")
    i = s.index("accrue_engine(engine_for(")
    j = s.index("if proj and proj.owner_id:", i)
    # 적립이 owner 검사보다 앞에 있어야 한다.
    assert i < j


def test_일_버킷_규칙이_세_원장에서_같다():
    """창 합산 코드가 세 원장을 같은 방식으로 다룰 수 있어야 한다."""
    s = _src("usage.py")
    body = s[s.index("def accrue_engine("):s.index("def engine_for(")]
    assert "current_period(ts)" in body
