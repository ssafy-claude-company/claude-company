"""[채용봇은 시스템 존재(2026-08-04, 사용자: '채용 봇은 Organt가 아니고 시스템적인 존재야')]

실측 U-504: 채용 역할 봇이 팀 채용(genesis) 한 번 없이 백로그 75건 전부를 혼자 등재·수행하며
판을 뛰었다(활동 322줄). U-496에서도 등재 61·완료 45·발언 응찰 62. 채용은 신입을 빚는 제네시스의
리크루터일 뿐 판의 구성원이 아니다 — 회의·표결·팀 구성·기여 분모 전부에서 비참여(예비와 같은 자격).
온보딩·공고는 팀 밖 SYS 경로라 이 배제와 무관하게 돈다.
"""
import sys, types

sys.path.insert(0, __file__.rsplit('/ops/', 1)[0])
from system.rule.comm_helpers import _is_spare


class _F:
    def __init__(self, info):
        self._i = info

    def _info(self, oid):
        return self._i.get(int(oid), '')


def test_채용_역할은_비참여다():
    f = _F({1: '채용', 2: '프론트엔드', 3: '예비'})
    assert _is_spare(f, 1) is True      # 시스템 존재
    assert _is_spare(f, 3) is True      # 예비(레거시)
    assert _is_spare(f, 2) is False     # 일반 직군


def test_채용이_들어간_복합_직군명은_배제하지_않는다():
    """'채용 게임 기획' 같은 도메인 직군명 오배제 금지 — 정확히 '채용'만 시스템 존재."""
    f = _F({1: '채용 시스템 기획자'})
    assert _is_spare(f, 1) is False
