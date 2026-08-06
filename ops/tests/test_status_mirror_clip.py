"""화면에 실리는 결론이 문장 한가운데서 끊기지 않는다(U-442 실측: 목표가 '…‘혼자 완주'로 끊겼다)."""
import types

from system.rule.milestone import _clip, ms_status_snapshot, register_stage
from tests.test_milestone import _flow


def test_clip은_낱말_경계에서_끊고_줄었음을_남긴다():
    assert _clip("짧은 문장", 40) == "짧은 문장"
    out = _clip("사용자가 브라우저에서 바로 열어 직무 1개의 JD를 작성하고 후보 6명을 평가한다", 30)
    assert out.endswith("…") and " " not in out[-2:] and len(out) <= 32


def test_주기_목표와_실증_명령은_잘리지_않는다(monkeypatch, tmp_path):
    """목표·완수조건·실증은 결정문이다 — 미러가 줄이면 사용자는 줄어든 것을 결론으로 읽는다."""
    monkeypatch.setenv("ORGANT_PIPELINE", "milestone")
    f = _flow()
    f.workspace = str(tmp_path)
    f.current = types.SimpleNamespace(
        task_id="T-U442", team=[11, 12],
        status=types.SimpleNamespace(goal="", purpose="긴 목표 미러"),
        acceptance="", standard="", interfaces="")
    f.checkpoint_task = lambda: None
    long_goal = ("사용자가 브라우저에서 바로 열어 직무 1개의 JD를 작성하고 익명 후보 6명 전원을 동일한 "
                 "서류 검토·구조화 면접으로 평가한 뒤, 예산 1,000,000원 안에서 역량 50·협업 30·성장성 "
                 "20으로 1명을 채용하고 결과 리포트를 확인하는 혼자 완주 가능한 한 판")
    long_cmd = ("node scripts/verify-recruitment-game.mjs --milestone=solo-mvp "
                "--url=http://127.0.0.1:3000 --headless --replay=complete --candidates=6 "
                "--hire-count=1 --budget=1000000")
    assert len(long_goal) > 140 and len(long_cmd) > 160
    ok, note = register_stage(f, "goal", "목표: 채용 의사결정 게임\n내용 폭: 기능 3종\n창의 설계: 방패병 — 앞 열이 받는 피해 40% 감소\n최대 표준: 실제 예 대조 · 핵심 기능 3종 · 주 사용 흐름 원탭\n- 한 판이 끝난다 | 실증: node v.js", "U-442")
    assert ok, note
    ok2, note2 = register_stage(
        f, "milestone",
        f"단계: 혼자 완주하는 한 판 → 둘이 겨루는 판\n이번 주기: {long_goal}\n- {long_goal} | 실증: {long_cmd}",
        "U-442")
    assert ok2, note2
    snap = ms_status_snapshot(f)
    cur = (snap.get("list") or [snap])[-1]
    assert cur["goal"] == long_goal                       # 문장 그대로
    assert cur["cr"][0]["d"] == long_goal
    assert cur["cr"][0]["v"] == long_cmd                  # 명령이 잘리면 검증이 거짓말이 된다
