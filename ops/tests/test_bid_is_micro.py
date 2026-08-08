"""응찰 한 줄에 작업 스레드를 통째로 싣지 않는다 (2026-08-07 실측 U-536).

판 하나(U-536)의 turn_done 214건을 용도별로 가르면 이렇다:

    용도            턴    비용    비중    평균 입력
    작업            41   $5.73  36.8%    718,702
    백로그 작업      19   $5.67  36.4%  1,541,084
    회의 발언       114   $2.57  16.5%     78,597
    발언권 응찰      18   $1.43   9.1%    415,009   ← 한 줄 답하는 자리
    짧은 상호작용    10   $0.12   0.8%     14,071
    표결            12   $0.06   0.4%     13,062

표결·짧은 상호작용은 1.3만 토큰인데 **발언권 응찰만 41.5만 토큰**이다. 32배. 셋 다 '이 텍스트만
보고 한 줄로 답하라'는 자족적 프롬프트인데, 응찰만 봇의 작업 세션을 resume해서 스레드 전체를
다시 실어 보냈다. 한 세션은 응찰 20턴 만에 누적 입력 1,600만 토큰까지 자랐고 그 세션 하나가
$3.03이었다.

2026-07-30에 '짧은 상호작용은 세션을 물지 않는다'로 회의 응찰·표결을 micro로 돌렸는데, 작업
단계의 자기선택 응찰(sys_core의 _probe_body)만 그 수리에서 빠졌다. 같은 성질의 자리는 같은
규칙을 받는다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system import sys_core as S
from system.rule import communication as C


def test_작업단계_자기선택_응찰이_micro다():
    src = inspect.getsource(S)
    i = src.find("[발언권 응찰 — 자기선택]")
    assert i > 0, "자기선택 응찰 프롬프트가 사라졌다"
    block = src[i:i + 2600]
    j = block.find("_fork_collect(flow, lead, cands, _probe_body")
    assert j > 0, "응찰 수집 호출을 찾지 못했다"
    assert "micro=True" in block[j:j + 120], "응찰이 작업 세션을 resume한다(스레드 전체 재전송)"


def test_회의_응찰도_여전히_micro다():
    """한 자리만 고치고 다른 자리가 새면 같은 값을 두 번 낸다."""
    src = inspect.getsource(C)
    i = src.find("[회의 — 발언권 응찰]")
    assert i > 0
    block = src[i:i + 1200]
    assert "micro=True" in block, "회의 응찰이 micro가 아니다"


def test_표결도_micro다():
    src = inspect.getsource(C)
    i = src.find("_resp = {m: res for m, res, _note in")
    assert i > 0, "표결 수집 경로가 사라졌다"
    assert "micro=True" in src[i:i + 300], "표결이 micro가 아니다"
