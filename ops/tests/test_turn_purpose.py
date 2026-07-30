"""턴 목적 분류 계약 — 결산에 목적이 실려야 구간별 원가를 추정 아닌 집계로 낼 수 있다."""
from system.sys_core import Sys


def test_본문_머리표로_목적을_가른다():
    f = Sys._purpose_of
    assert f("[회의 2라운드] 주제: …", "Info") == "회의 발언"
    assert f("[표] 결론 확정 표결 …", "Info") == "표결"
    assert f("[다음 백로그 응찰] 지금 남아 있는 …", "Info", micro=True) == "인계 응찰"
    assert f("[다음 백로그 선정] 당신이 방금 …", "Info", micro=True) == "인계 선정"
    assert f("[작업중 — 이어서] 백로그 B3 …", "Info") == "백로그 작업"
    assert f("[참여 응찰] 새 판이 열렸습니다 …", "Info") == "참여 응찰"
    assert f("[SYS — 보고 형식 재요청(1회)] …", "Work") == "보고 반려"


def test_머리표가_없으면_micro_여부로_가른다():
    assert Sys._purpose_of("그냥 본문", "Work") == "작업"
    assert Sys._purpose_of("그냥 본문", "Info", micro=True) == "짧은 상호작용"
