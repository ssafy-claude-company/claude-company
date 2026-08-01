"""빈칸을 두고 회의를 닫지 않는다(U-478 실측: 기고 0건·빈칸 2개인 채 전원 종료 → 판이 목표 없이 죽음)."""
import inspect

from system.rule import communication, floor


def test_엔진이_완성여부를_물어본다():
    src = inspect.getsource(floor.run_conversation)
    assert "can_close" in src
    assert "결론 미완 — 빈칸이 남아 종결 보류" in src


def test_회의가_빈칸_수를_그_판정에_쓴다():
    src = inspect.getsource(communication)
    i = src.index("_can_close")
    seg = src[i:i + 700]
    assert "_ms_dstat" in seg and "_ph0 == 0" in seg


def test_아무도_안_나서면_결국_닫는다():
    """무한 연장 금지 — 한 라운드 더 열어도 응찰이 없으면 종전대로 종결."""
    src = inspect.getsource(floor.run_conversation)
    i = src.index("결론 미완 — 빈칸이 남아 종결 보류")
    assert "break" in src[i:i + 900]
