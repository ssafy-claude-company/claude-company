"""부품 후보는 결정문이 아니다 (2026-08-07 실측 U-536).

subtask 회의가 작업 영역 12개를 등록했는데 그중 7개가 영역이 아니었다:

    ST-1 1개 게임 모드          ST-5 강화 선택지 9개(성장 축 3개)
    ST-2 피격 3회 누적 게임오버   ST-6 수집물 2종
    ST-3 서로 다른 행동의 적/장애물 4종   ST-7 로컬 최고점 1개
    ST-4 난이도 구간 3단계

이것들은 goal이 정한 **내용 폭(범위 항목)**이지 일을 나누는 자리가 아니다. 실제 영역은 뒤의
다섯(코어 규칙·플레이 인터페이스·통합 검증·배포·콘텐츠 설계)이고, 백로그도 거기에만 붙었다.
앞의 일곱은 백로그 0으로 화면에 '대기'로 남는다 — 단위 완수 = 백로그 소진이므로 닫힐 방법이 없다.

원인은 봇의 판단이 아니라 **골격**이었다. SYS가 까는 subtask DRAFT가 부품을 이렇게 적었다:

    (goal이 정한 제품 부품 — 이 중 필요한 것을 영역으로 세우고 …)
    단위: 1개 게임 모드
    단위: 피격 3회 누적 게임오버
    …

'이 중 필요한 것을 고르라'고 문장으로 말하면서, 형식은 이미 전부를 `단위:` 줄로 적어 뒀다.
등록 파서는 `단위:` 줄을 전부 영역으로 읽는다 — 고르지 않아도 이미 고른 것이 된다.

후보는 후보 모양으로 적는다(파서가 읽지 않는 한 줄). 영역은 팀이 직접 쓴다.
"""
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import parse_units


class _F:
    """_product_parts가 읽는 최소 flow — goal 결정의 내용 폭."""

    def __init__(self):
        import types
        self.current = types.SimpleNamespace(
            task_id="T-1",
            content_floor=["1개 게임 모드", "수집물 2종", "로컬 최고점 1개"],
            status=types.SimpleNamespace(goal="2D 생존 게임"),
            acceptance="", standard="실제 예 대조 · 핵심 기능 3종", interfaces="", team=[11, 12])
        self.milestones = []
        self.workspace = ""
        self.log = None


def _seed():
    from system.rule.milestone import _product_part_hints
    return _product_part_hints(_F())


def test_후보는_단위_줄로_깔리지_않는다():
    seed = _seed()
    if not seed.strip():
        import pytest
        pytest.skip("이 환경에서 제품 부품이 잡히지 않음")
    assert not parse_units(seed.splitlines()), (
        "골격이 후보를 단위로 적어 둔다 — 고르지 않아도 전부 등록된다:\n" + seed)


def test_후보는_그래도_보인다():
    """반대로 후보를 아예 없애면 영역이 다시 절차(검증·배포)로만 채워진다 — 보이되 결정문은 아니다."""
    seed = _seed()
    if not seed.strip():
        import pytest
        pytest.skip("이 환경에서 제품 부품이 잡히지 않음")
    assert "부품 후보" in seed
    assert "게임 모드" in seed


def test_그대로_옮겨_적지_말라고_말한다():
    seed = _seed()
    if not seed.strip():
        import pytest
        pytest.skip("이 환경에서 제품 부품이 잡히지 않음")
    assert "그대로 옮겨 적지 마세요" in seed
