"""사다리는 한 번 오르기 시작하면 다시 그리지 않는다 (2026-08-06, 현준-1 — 사용자: '주기 정하기
마저도 마일스톤 안에 그 다음것이 들어가 있어 가장 초반 마일스톤 생성을 제외하고는').

실측 U-496: 첫 주기 회의(08-02 08:45)가 사다리를 2단으로 정했다 — '기본 피하기 게임판 → 피하기·
수집 완성 게임'. 그런데 다음 회의(08-03 09:33)부터 매 회의의 '단계:' 줄이 flow.roadmap을 **통째로
덮어써** 1단 '완성판'이 됐고, "이번 주기 = N단계"만 6까지 자랐다. 원래 사다리대로면 2주기 완주가
곧 e2e 경계였다. 로드맵은 전체 구조(첫 회의의 결정)인데, 매 주기 회의가 그 다음 것을 다시 정의할
수 있으면 소진 판정이 영영 서지 않는다.

수리: 계획 주기가 하나라도 완주된 뒤에는(roadmap_settled) 이후 회의의 '단계:'를 반영하지 않는다.
완주 전(첫 주기 진행 중 재협상)은 종전대로 다듬을 수 있다.
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import register_consensus, roadmap_settled


class _G:
    def __init__(self):
        self.ch = 1


class _Cur:
    def __init__(self):
        self.task_id = "T-1"
        self.status = types.SimpleNamespace(goal="피하기·수집 게임")
        self.acceptance = ""
        self.content_floor = []
        self.creative = []
        self.team = [11, 12]


class _Flow:
    def __init__(self):
        self.current = _Cur()
        self.milestones = []
        self.roadmap = []
        self.backlog_relays = {}
        self.log = None
        self.workspace = ""
        self.user_channel = 1
        self.notes = []


def _flow(monkeypatch):
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")   # 전역 os.environ 오염 금지 — 스위트 누출 방지
    return _Flow()


_BODY = ("목표: 기본판\n내용 폭: 기능 3종\n창의 설계: 방패병 — 앞 열이 받는 피해 40% 감소\n"
         "동작 | curl 확인")


def test_첫_회의의_사다리가_정본으로_남는다(monkeypatch):
    f = _flow(monkeypatch)
    ms, _ = register_consensus(f, "단계: 기본판 → 완성판\n" + _BODY, "1주기")
    assert not isinstance(ms, str)
    assert f.roadmap == ["기본판 → 완성판"] or f.roadmap == ["기본판", "완성판"]


def test_주기_완주_후_단계_재정의는_반영되지_않는다(monkeypatch):
    f = _flow(monkeypatch)
    ms, _ = register_consensus(f, "단계: 기본판 → 완성판\n" + _BODY, "1주기")
    assert not isinstance(ms, str)
    first = list(f.roadmap)
    ms.status = "done"                                   # 계획 주기 1개 완주 — 사다리 정착
    assert roadmap_settled(f) is True
    ms2, _ = register_consensus(f, "단계: 완성판\n" + _BODY.replace("기본판", "완성판"), "2주기")
    assert not isinstance(ms2, str)
    assert f.roadmap == first, f.roadmap                 # 덮어쓰기 안 됨


def test_완주_전_재협상은_종전대로_다듬을_수_있다(monkeypatch):
    f = _flow(monkeypatch)
    ms, _ = register_consensus(f, "단계: 기본판 → 완성판\n" + _BODY, "1주기")
    assert not isinstance(ms, str)
    assert roadmap_settled(f) is False                   # 아직 완주 0 — 정착 전
