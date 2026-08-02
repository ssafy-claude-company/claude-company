"""[매인 사람 하나가 판 하나를 굶긴다(2026-08-02 실측)] 받을 봇이 다른 판에 매여 있으면 그 판을 통째로
건너뛴다. 봇 18명 중 16명은 판 전용인데 기획·채용 2명만 공유라 그 둘이 병목이 됐다 — 오늘 8.1시간 중
신판 3.0h·새판 3.9h를 굶었고(최장 92분) 재시작으로도 안 풀린 판이 있었다."""
import inspect

from system import sys_core


def _src():
    return inspect.getsource(sys_core.Sys.run)


def test_연속으로_건너뛴_판은_손이_빈_사람에게_넘긴다():
    src = _src()
    assert "_ch_skip" in src and "starved_channel_reassigned" in src


def test_처음_몇_번은_종전대로_기다린다():
    src = _src()
    i = src.index("_ch_skip")
    seg = src[i:i + 900]
    assert "< 5" in seg, "연속성 우선(임계 전에는 기다린다)이 사라졌다"


def test_같은_채널이_이미_작업_중이면_절대_새로_집지_않는다():
    src = _src()
    i = src.index("_ch_skip")
    seg = src[i:i + 900]
    assert "ch in busy_ch" in seg, "채널 단일 활성 보호가 빠졌다"


def test_한가한_사람이_없으면_그냥_기다린다():
    src = _src()
    i = src.index("starved_channel_reassigned")
    head = src[max(0, i - 400):i]
    assert "_free is None" in head and "continue" in head
