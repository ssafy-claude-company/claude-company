"""e2e가 요청한 비준 회의를 실제로 여는 연결(2026-08-03, 실측 U-496).

e2e 개시부는 비준이 없어 못 열 때 "길만 비켜 준다 — 단계 기계가 그 회의를 연다"고 적고 15분을
물러선다(_e2e_ratify_until). 그런데 그 회의를 여는 조건인 _goal_verifier_unrunnable은 **명령은
있는데 파일이 없는** 경우만 잡는다(빈 명령은 건너뛴다). U-496의 막힘은
"condition:3에 고정된 exact verifier가 **없습니다**" — 명령 자체가 없는 경우라 술어가 False를
돌려주고, 회의는 영영 열리지 않는다.

실측 U-496:
  18:38 e2e_ratify_meeting_requested(tries 1)
  18:53 e2e_ratify_meeting_requested(tries 2)   ← 정확히 15분 뒤
  19:08 stalled_stopped(repicks 4)              ← 다시 15분 뒤
  그 한 시간 동안 이 판의 이벤트는 2건뿐이었다(턴 1회). 기다리라 해놓고 기다리면 죽인다.
  재개하면 tries가 0으로 돌아가 같은 30분을 다시 돈다.

술어의 원래 문장("비준 자체가 없는 경우는 최종 주기의 정상 경로가 맡는다")은 Task 경계에서는
성립하지 않는다 — 로드맵을 다 돌아 열 주기가 남아 있지 않다.
"""
import inspect

from system.rule import milestone


def test_비준_요청_자체가_회의_개시_근거가_된다():
    src = inspect.getsource(milestone.meeting_stage)
    assert "_e2e_ratify_tries" in src, (
        "e2e가 비준을 요청해도 회의를 여는 근거가 없으면 15분 침묵만 반복된다")


def test_원래_술어는_그대로_남아_있다():
    """파일이 없는 경우(원래 잡던 것)는 종전대로 잡는다 — 범위를 넓혔지 바꾸지 않았다."""
    src = inspect.getsource(milestone.meeting_stage)
    assert "_goal_verifier_unrunnable(flow)" in src


def test_술어는_빈_명령을_건너뛴다는_사실을_고정한다():
    """이 사실이 이 연결의 존재 이유다 — 술어가 바뀌면 이 테스트가 먼저 알려 준다."""
    src = inspect.getsource(milestone._goal_verifier_unrunnable)
    assert "if not cmd:" in src and "continue" in src
