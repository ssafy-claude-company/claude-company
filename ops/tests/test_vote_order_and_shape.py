"""표결은 형태 검사 뒤에 열리고, 한 표결은 카드 하나다 (2026-08-06, 사용자: '표결을 열고 모두
동의했는데 결론이 잘못된 방식으로 만들어져서 반려되거나 그런건 아니지? 서순도 중요하고' /
'저런 이상한 표결 표결은 왜 생겼는지 모르겠는데 … 이런게 구조적으로 불가능해야 하는데').

[실측 ① 서순] U-525 goal 회의:

    13144  [표] 결론 확정 표결 — 찬성 4 · 반대 0        ← 전원 동의
    13145  [회의] 결론 파일이 등록 게이트에 보류됐습니다 — '내용 폭:'이 가짓수만 셉니다
    13147  [표] 결론 확정 표결 — 찬성 4 · 반대 0        ← 고쳐서 다시 표결

형태 관문(내용 폭·깊이 축·창의 설계·최대 표준)이 register_stage에만 있어서, 전원이 찬성한 **뒤에**
반려됐다. stage_preflight(표결 전 검사)는 2026-07-17에 바로 이 낭비('표결 가결 후 등록 거부 사이클
6~9분×N')를 없애려고 만든 것인데, 그 뒤에 추가된 관문들이 거기 등록되지 않았다. 두 곳이 같은
함수를 쓰게 한다 — 새 관문을 한쪽에만 다는 실수가 구조적으로 불가능해진다.

[실측 ② 모양] 같은 판 13199·13200·13201:

    [표] 수정 후 재논의 — [표] 수정 후 재논의
    최고점 표시의 가시성 조건은 …

표 하나하나가 본인 명의 '[표] …' 메시지로 따로 게시됐다. 화면은 [표]를 표결 카드로 그리므로 한 번의
표결이 카드 3장으로 서고 집계 카드와 겹쳤다. 게다가 봇 응답 자체가 '[표] 수정 후 재논의'로 시작해
라벨이 두 번 찍혔다. 표는 집계 카드가 투표 줄로 전부(사유 포함) 싣는다 — 낱개 게시를 없앤다.
"""
import inspect
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule import comm_ceremonies as CC
from system.rule import milestone as M


class _F:
    origin_request = "게임 만들어줘"
    current = None


_GOOD = ("[수렴안]\n목표: 브라우저 액션 게임\n"
         "내용 폭: 적 4종 · 강화 3택1 · 해금 12종\n"
         "창의 설계: 방패병 — 앞 열이 받는 피해 40% 감소\n"
         "최대 표준: 실제 예 대조 · 핵심 기능 3종 · 시작→플레이→결과→재도전 흐름\n[/수렴안]")


def _floor_of(text):
    return text.replace("적 4종 · 강화 3택1 · 해금 12종", "적 4종 · 장애물 6종")


def test_형태_불량은_표결_전에_걸린다():
    errs = M.stage_preflight("goal", _floor_of(_GOOD), _F())
    assert any("깊이 축" in e for e in errs), errs


def test_표결_전_검사와_등록이_같은_함수를_쓴다():
    """한쪽에만 관문을 다는 실수가 다시 나지 않게 — 단일 정본."""
    pre = inspect.getsource(M.stage_preflight)
    reg = inspect.getsource(M.register_stage)
    assert "goal_form_errors(" in pre, "표결 전 검사가 형태 관문을 안 본다"
    assert "goal_form_errors(" in reg, "등록이 형태 관문을 안 본다"


def test_형태가_맞으면_표결_전_검사는_통과한다():
    errs = [e for e in M.stage_preflight("goal", _GOOD, _F())
            if "내용 폭" in e or "최대 표준" in e or "창의 설계" in e]
    assert not errs, errs


def test_지속_축_누락도_표결_전에_걸린다():
    errs = M.stage_preflight(
        "goal", _GOOD.replace("· 해금 12종", "· 웨이브 6개"), _F())
    assert any("다시 올 이유" in e for e in errs), errs


def test_표는_낱개로_게시되지_않는다():
    src = inspect.getsource(CC)
    assert 'await _say(flow, v, f"[표]' not in src, "표 하나가 표결 카드 한 장으로 선다"


def test_봇이_스스로_붙인_라벨은_벗긴다():
    """라벨은 시스템의 것이다 — 봇 응답이 '[표] …'로 시작해도 기록에 두 번 찍히지 않는다."""
    src = inspect.getsource(CC)
    assert '_res_clean = re.sub(r"^\\s*(?:\\[표\\]\\s*)+", "", str(res or ""))' in src


def test_집계_카드가_사유까지_싣는다():
    """낱개 게시를 없앤 대신, 집계가 전 투표자의 표와 사유를 담는지."""
    src = inspect.getsource(CC)
    assert 'reasons.append(f"{flow._info(v) or v}: {(pick or \'무효\')} — ' in src
