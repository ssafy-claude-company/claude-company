"""예시가 정답이 되면 안 된다 (2026-08-06, 사용자: '내가 말했던거 그대로 나왔는데 너가 프롬프트로
다른 이상한 짓 한거 아닌가 의심스러워서').

정당한 의심이었다. 깊이 축·존재이유 관문을 넣으며 반려문·골격에 '로그라이크 → 강화 없이는 5웨이브
생존 불가', "'웨이브 10단'·'해금 요소 5개'" 같은 **장르 지목 예시**를 썼다. 봇이 곧바로 '로그라이트
생존 액션·웨이브마다 능력 선택'을 냈으니, 그 설계가 팀의 판단인지 내 예시의 반향인지 구분할 수 없다.

관문은 **축이 있는지**만 물어야 하고 무엇을 만들지는 팀이 정한다. 봇에게 보이는 문구에서 장르
지목을 걷어내고, 예시는 여러 형태(게임·도구·문서)로 흩어 어느 하나가 정답처럼 보이지 않게 한다.
(주석의 실측 인용은 기록이라 남긴다 — 봇 프롬프트에는 안 들어간다.)
"""
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import stage_agenda, stage_draft_template

_GENRES = ("로그라이크", "로그라이트", "뱀서", "소울라이크", "메트로배니아",
           "웨이브 10단", "60초", "3레인")


def _visible(stage):
    return (stage_agenda(stage)[1] or "") + (stage_draft_template(stage, "안건") or "")


def test_봇에게_보이는_문구에_장르_지목이_없다():
    for st in ("goal", "criteria", "milestone", "subtask", "backlog"):
        leaked = [w for w in _GENRES if w in _visible(st)]
        assert not leaked, f"{st} 단계 문구에 장르 누출: {leaked}"


def test_깊이_축_요구는_그대로_있다():
    """장르를 지우되 축 자체는 계속 묻는다. 2026-08-06에 축이 둘로 갈렸다 —
    한 판 안에서 달라지는 축 · 세션을 넘어 남는 축(사용자가 다시 올 이유)."""
    t = _visible("goal")
    assert "한 판 안에서 달라지는 축" in t
    assert "세션을 넘어 남는 축" in t


def test_존재이유_요구도_그대로_있다():
    t = _visible("criteria")
    assert "존재이유" in t


def test_예시는_여러_형태로_흩어져_있다():
    """게임 하나만 예로 들면 그게 정답이 된다 — 도구·문서도 함께."""
    from system.rule import milestone as M
    import inspect
    src = inspect.getsource(M)
    i = src.find("가짓수만 늘리면")
    assert i > 0
    block = src[i:i + 700]
    assert "도구면" in block and "문서면" in block
