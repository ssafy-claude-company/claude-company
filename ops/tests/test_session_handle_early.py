"""죽은 턴을 이어붙일 손잡이는 시작할 때 잡는다 (2026-08-05, 현준-1).

[규명] organt-runner.service에는 KillMode·ExecStop·TimeoutStopSec이 없다 → systemd 기본
KillMode=control-group. `systemctl restart`는 러너와 **그 cgroup 안의 codex 서브프로세스 전부**에
SIGTERM을 보낸다. 러너에는 SIGTERM 핸들러도 드레인도 없어 진행 중 턴이 그 자리에서 죽는다.

그때 잃는 것은 파일이 아니라 **세션 손잡이**다. codex는 스트림 첫머리(thread.started)에 thread_id를
주는데, 종전 코드는 그 값을 프로세스가 끝난 뒤 반환값으로만 돌려줬고 organt.py가 그때서야
_save_session_id로 영속시켰다. 중간에 죽으면 반환이 영영 오지 않아 id가 사라지고 봇은 같은 일을
처음부터 다시 시작한다 — 백로그 작업 턴의 실측 입력이 4.55M 토큰이라 재지불액이 그만큼이다.
착지가 러너 재시작 앞에서 최대 20분을 기다리는 규칙(land.sh)도 이 손실을 피하려던 우회다.

[수리] thread.started에서 즉시 on_session으로 넘겨 영속시킨다. 프로브(신선 micro)는 종전 규칙대로
제외한다 — 본세션 기억을 포크시키지 않기 위해서다.
"""
import asyncio
import json
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from organt import codex_mcp_bridge as bridge_mod


def _fake_codex(monkeypatch, script):
    """진짜 스트림 경로로 돌린다 — 이벤트만 우리가 찍는다(기존 하네스와 같은 방식)."""
    real_spawn = asyncio.create_subprocess_exec

    async def fake_spawn(*_args, **kwargs):
        return await real_spawn(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr(bridge_mod.asyncio, "create_subprocess_exec", fake_spawn)


_STARTED_THEN_DIES = (
    "import json,sys\n"
    "sys.stdin.buffer.read()\n"
    "print(json.dumps({'type':'thread.started','thread_id':'sid-살아있다'}), flush=True)\n"
    # 최종 발화 없이 그대로 끝난다 = 러너가 중간에 죽어 턴이 잘린 모양.
)

_STARTED_THEN_SPEAKS = (
    "import json,sys\n"
    "sys.stdin.buffer.read()\n"
    "print(json.dumps({'type':'thread.started','thread_id':'sid-정상'}), flush=True)\n"
    "print(json.dumps({'type':'item.completed','item':"
    "{'type':'agent_message','text':'끝'}}, ensure_ascii=False), flush=True)\n"
)


def test_턴이_발화_없이_끊겨도_손잡이는_남는다(monkeypatch, tmp_path):
    """중간에 죽은 턴 = 최종 발화 없음. 그래도 세션 id는 이미 손에 들어와 있어야 한다."""
    _fake_codex(monkeypatch, _STARTED_THEN_DIES)
    seen = []
    text, sid = asyncio.run(bridge_mod.run_codex_turn(
        prompt="p", cwd=str(tmp_path), session_id=None, tools=[], model="gpt-test",
        on_session=seen.append))
    assert seen == ["sid-살아있다"], seen        # 반환을 기다리지 않고 이미 받았다
    assert text == ""                            # 발화는 없었다(잘린 턴)


def test_정상_턴에서도_한_번만_넘어온다(monkeypatch, tmp_path):
    _fake_codex(monkeypatch, _STARTED_THEN_SPEAKS)
    seen = []
    text, sid = asyncio.run(bridge_mod.run_codex_turn(
        prompt="p", cwd=str(tmp_path), session_id=None, tools=[], model="gpt-test",
        on_session=seen.append))
    assert seen == ["sid-정상"] and sid == "sid-정상" and text == "끝"


def test_손잡이_저장이_실패해도_턴은_계속된다(monkeypatch, tmp_path):
    """영속화 실패(디스크 오류 등)가 진행 중인 턴을 죽이면 안 된다."""
    _fake_codex(monkeypatch, _STARTED_THEN_SPEAKS)

    def _boom(_sid):
        raise OSError("디스크 없음")

    text, sid = asyncio.run(bridge_mod.run_codex_turn(
        prompt="p", cwd=str(tmp_path), session_id=None, tools=[], model="gpt-test",
        on_session=_boom))
    assert text == "끝" and sid == "sid-정상"


def test_콜백을_안_주면_종전_그대로다(monkeypatch, tmp_path):
    """on_session 없는 호출(구 경로·외부 대체)도 반환 계약 그대로 — 이중 수용."""
    _fake_codex(monkeypatch, _STARTED_THEN_SPEAKS)
    assert asyncio.run(bridge_mod.run_codex_turn(
        prompt="p", cwd=str(tmp_path), session_id=None, tools=[],
        model="gpt-test")) == ("끝", "sid-정상")


def test_신선_micro_프로브는_손잡이를_넘기지_않는다(monkeypatch):
    """프로브 세션을 저장하면 본세션 기억이 갈라진다 — 호출부가 콜백 자체를 안 준다."""
    import inspect
    from organt import organt as organt_mod

    src = inspect.getsource(organt_mod.Organt._run_codex)
    assert "on_session=" in src, "세션 콜백이 호출부에서 사라졌다"
    assert "ORGANT_MICRO_FRESH" in src.split("on_session=")[1][:200], \
        "신선 micro 제외 규칙이 콜백 인자에서 빠졌다"
