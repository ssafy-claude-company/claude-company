"""잘린 턴이 느린 것인지 멈춘 것인지 가른다(2026-08-03, 실측 U-478).

무진행 워치독은 도구 무활동으로 턴을 끊는데(sys_core._run_until_silent), 로그에는 끊었다는
사실과 임계값(sec)만 남았다. 그래서 그 봇이 첫 도구 호출에 오래 걸린 것인지 아예 멈춘 것인지
구분할 수 없다 — 24시간에 agent_timeout이 49회 났는데 어느 쪽인지 모르니 고칠 수도 없다.

실측 U-478 17:05:54: agent_timeout(worker, sec=480). 그 턴은 turn_done이 없었고, 같은 판의
다른 턴은 12~228초였다. 검증기 자체는 27초다(직접 측정) — 긴 실행이 원인이 아니다.

여기서 고치지 않고 먼저 재는 이유: 임계값을 올리는 것은 원인을 모른 채 상한으로 때우는 것이다.
"""
import inspect

from system import permissions, sys_core


def test_턴_시작과_첫_도구_호출을_표시한다():
    src = inspect.getsource(sys_core.Sys._run_until_silent)
    assert "_turn_t0" in src and "_turn_first_tool" in src


def test_도구_훅이_첫_호출_시각을_남긴다():
    src = inspect.getsource(permissions)
    assert "_turn_first_tool" in src, "도구가 불린 순간을 표시하지 않으면 가를 수 없다"
    i = src.index("_turn_first_tool")
    assert "is None" in src[max(0, i - 160):i + 160], "첫 호출만 기록해야 한다(매번 덮어쓰면 무의미)"


def test_타임아웃_로그가_세_값을_함께_남긴다():
    src = inspect.getsource(sys_core)
    i = src.index(chr(34) + "agent_timeout" + chr(34) + ", organt=")
    window = src[i:i + 600]
    for k in ("tool_ran", "first_tool_s", "turn_s"):
        assert k in window, k + "가 없으면 원인 판별이 안 된다"


def test_도구를_한_번도_안_부른_턴은_자기_시작부터_잰다():
    """[멈춘 봇은 옆 사람이 일하면 안 보인다(2026-08-03, 계측 확인)]

    워치독의 idle은 **흐름 전체**의 도구 활동을 잰다. 그래서 한 봇이 완전히 멈춰도 다른 봇들이
    도구를 부르는 동안에는 시계가 갱신돼 그 봇이 가려진다.

    계측 실측: tool_ran=False인 턴이 turn_s=2141.4(35분) 살아 있다가, 판 전체가 조용해진 뒤에야
    잘렸다. 그 사이 그 봇에게 간 교차검증 요청은 오류로 끝났고(카운터는 응답에서만 오른다),
    마감 관문은 cc=0으로 계속 거절해 리더가 마감을 열 번 반복 호출했다(complete_thrash holds 10).

    워치독의 선언된 목적은 "완전히 멈춘 것만 끊는다"인데 정작 멈춘 것을 못 봤다.
    """
    src = inspect.getsource(sys_core.Sys._run_until_silent)
    assert "own_idle" in src, "턴 자신의 무활동을 재지 않으면 멈춘 봇은 계속 가려진다"
    assert "_turn_first_tool" in src and "_turn_t0" in src
    # 도구를 부른 턴은 종전대로 흐름 시계로만 잰다(일하는 워커 보호는 그대로).
    i = src.index("own_idle = idle")
    assert "is None" in src[i:i + 200], "도구를 부른 턴까지 자기 시작부터 재면 오래 일하는 워커가 잘린다"
