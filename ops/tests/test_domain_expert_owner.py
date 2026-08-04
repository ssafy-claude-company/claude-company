"""[도메인 전문가가 선점한다(2026-08-04, 사용자: '무조건 도메인 전문가가 백로그를 선점하는게
맞을듯해 너무 많아지면 일손이 부족한거지')]

실측 U-496 ST-8: 재검증 회의의 의제 원료가 QA의 결함 보고라(정보 비대칭), 제품 수리 줄까지 QA가
썼고 '발제자=주인' 귀속이 그 줄들을 QA 담당으로 등재했다. 등재자=담당 불변이므로 등재 시점이
도메인을 정하는 유일한 자리 — 등재자는 줄 작성자가 아니라 도메인 적임자다."""
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import domain_expert_owner

POOL = {11: "프론트엔드", 12: "QA", 13: "백엔드"}


def test_남의_도메인_줄은_적임자에게_등재된다():
    """QA(12)가 쓴 프론트엔드 수리 줄 → 프론트엔드(11) 담당."""
    q = "플레이 화면 프론트엔드 입력을 Canvas 렌더링에 연결한다"
    assert domain_expert_owner(q, POOL, author=12) == 11


def test_자기_도메인_줄은_작성자가_유지된다():
    """QA가 쓴 QA 검증 줄 → 발제자=주인 존중."""
    q = "QA 브라우저 검증 매트릭스를 실행해 증거를 남긴다"
    assert domain_expert_owner(q, POOL, author=12) == 12


def test_무주_줄도_적임자에게():
    q = "백엔드 저장 API 계약을 고정한다"
    assert domain_expert_owner(q, POOL, author=0) == 13


def test_팀이_비면_작성자_그대로():
    assert domain_expert_owner("아무 일", {}, author=12) == 12
