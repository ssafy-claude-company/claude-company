"""회의 발언도 단계 경계에서 스레드를 끊는다 (2026-08-07 실측 U-536).

같은 판 안에서 '회의 발언' 턴의 평균 입력이 이렇게 자랐다:

    판 초반(1~126턴)   78,597 토큰   턴당 $0.0225
    판 후반(45분 구간) 414,960 토큰  턴당 $0.0800

늘어난 것은 프롬프트가 아니라 **그 봇 스레드의 재전송분**이다(캐시 비율 95%가 그 증거다).
2026-08-01에 '스레드는 일감 경계에서 끊는다'를 넣었지만, 표식(`_work_scope`)이 **활성 백로그
id**로만 세워졌다 — 회의 발언 턴은 일감이 없어 표식이 빈 문자열이고, 그래서 스레드가 판이
끝날 때까지 한 번도 안 끊겼다.

단계가 바뀌면 앞 단계의 발언 기록을 통째로 다시 실어 나를 이유가 없다: 회의 프롬프트가
'못 본 발언'을 싣고, 결정은 DRAFT 파일과 채널이 들고 있다. 같은 규칙을 같은 성질의 자리에 준다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system import sys_core as S


def test_일감이_없으면_단계로_표식을_세운다():
    src = inspect.getsource(S)
    i = src.find("organt._work_scope = _mine")
    assert i > 0, "스레드 표식 자리가 사라졌다"
    head = src[max(0, i - 1400):i]
    assert "if not _mine:" in head, "일감 없는 턴에 표식이 안 선다(스레드가 안 끊긴다)"
    assert 'f"stage::{_st_now}"' in head, "단계 표식 형식이 없다"


def test_일감이_있으면_종전대로_일감_경계다():
    """작업 턴은 08-01 계약 그대로 — 단계 표식이 일감 표식을 덮으면 일감 중간에 스레드가 끊긴다."""
    src = inspect.getsource(S)
    i = src.find("organt._work_scope = _mine")
    head = src[max(0, i - 1400):i]
    j = head.find("if not _mine:")
    assert j > 0
    # 단계 표식은 _mine이 비었을 때만 계산된다
    assert "_mine = f\"stage::{_st_now}\" if _st_now else \"\"" in head[j:], "일감 표식을 덮어쓴다"


def test_단계를_모르면_종전대로_이어간다():
    """단계가 안 잡히는 흐름(증류·수면 등)에서 표식을 세우면 매 턴 새 스레드가 된다 — 그건 손해다."""
    src = inspect.getsource(S)
    i = src.find("organt._work_scope = _mine")
    head = src[max(0, i - 1400):i]
    assert 'if _st_now else ""' in head, "단계가 없을 때 빈 표식으로 떨어지지 않는다"


def test_끊는_판정은_한_곳에서만_한다():
    """_scope_changed와 _resume_sid가 같은 사실을 봐야 한다(organt.py 주석의 계약)."""
    from organt import organt as O
    src = inspect.getsource(O)
    assert "_scope_changed" in src and "_work_scope_seen" in src
