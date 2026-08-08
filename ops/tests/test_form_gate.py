"""형식 관문은 등록기와 같은 눈으로 보고, 지워서 해소되지 않는다 (2026-08-07 실측 U-536).

criteria 회의가 발언 12건·**표결 0건**으로 소진되고 판이 파킹됐다. 채널에 남은 발언을 순서대로
읽으면 같은 동작이 여섯 번 반복된다:

    [회의] [주장] … 조건 줄을 추가합니다
    [회의] … 조건 줄을 추가하고 형식 이의를 해소했습니다      ← 이의 줄 삭제
    [회의] … 중복으로 남아 있던 형식 이의 줄만 삭제했습니다
    [회의] … 반복 생성된 형식 이의 줄만 삭제했습니다          ← 봇도 '반복 생성'을 알아챘다

DRAFT의 결정 구획에는 완수조건 15줄이 이미 구체적으로 차 있었고, 걸린 것은 이 한 줄이었다:

    > [이의 @형식] 결정 구획에 '조건' 줄이 **실제 결정으로** 필요합니다 …

원인 둘이 겹쳤다.

  ① `draft_missing_key`는 앞머리 목록 기호를 벗기지 않았다. 봇들은 결정 구획을 마크다운 목록으로
     쓴다 — `- 조건 — 위험·보상 … | 실증: …`. 그러면 이 검사에는 '조건' 줄이 **없는 것으로**
     보인다. 정작 등록기(`draft_to_proposal` → `draft_norm_line`)는 `- `를 벗기고 같은 줄을 조건으로
     읽는다. 관문이 등록기보다 엄해, 통과할 수 있는 문서를 막았다.

  ② 기계가 쓴 이의가 사람 이견과 같은 규칙('해소한 사람이 그 줄을 삭제')을 탔다. 지우면 이의 수가
     0이 되고, 다음 패스에서 관문이 같은 이의를 다시 쓴다. 지우기와 다시 쓰기가 서로를 불렀다.

관문은 등록기와 같은 정규화를 쓰고, 기계 이의는 표결을 막는 주체가 아니라 검사 결과의 표시로 둔다.
"""
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import (draft_missing_key, draft_status,
                                   draft_to_proposal, strip_form_objections)

_DOC = """# DRAFT [stage:criteria]

## 결정

완수조건:
- 조건 — 위험·보상 선택은 실제 결정을 만든다 | 실증: `npx playwright test choice.spec.js` 통과.
- 내용 폭 — 1개 게임 모드 | 실증: `npx playwright test acceptance.spec.js` 통과.

## 참고 (자유 — 판정 대상 아님)
메모
"""


def test_목록_기호가_붙은_키_줄을_관문이_읽는다():
    assert draft_missing_key("criteria", _DOC) is None


def test_관문과_등록기가_같은_줄을_본다():
    """등록기가 조건으로 읽는 줄이면 관문도 조건으로 읽어야 한다 — 둘이 갈리면 통과 불가 문서가 된다."""
    prop = draft_to_proposal("criteria", _DOC)
    assert any(l.startswith("조건") for l in prop.splitlines()), prop
    assert draft_missing_key("criteria", _DOC) is None


def test_키_줄이_정말_없으면_잡는다():
    doc = _DOC.replace("- 조건 — 위험·보상 선택은 실제 결정을 만든다 | 실증: `npx playwright test choice.spec.js` 통과.\n", "")
    assert draft_missing_key("criteria", doc) == "조건"


def test_미룸뿐인_값은_여전히_부재로_본다():
    doc = _DOC.replace("조건 — 위험·보상 선택은 실제 결정을 만든다 | 실증: `npx playwright test choice.spec.js` 통과.",
                       "조건: (후속: 다음 회의에서 정함)")
    assert draft_missing_key("criteria", doc) == "조건"


def test_기계_이의는_표결을_막는_이의로_세지_않는다():
    doc = _DOC.replace("## 참고", "> [이의 @형식] 결정 구획에 '조건' 줄이 필요합니다\n\n## 참고")
    ph, obj = draft_status(doc)
    assert (ph, obj) == (0, 0), (ph, obj)


def test_사람_이견은_그대로_센다():
    doc = _DOC.replace("## 참고", "> [이의 @게임QA] 입력 검증이 빠졌습니다\n\n## 참고")
    _ph, obj = draft_status(doc)
    assert obj == 1


def test_기계_이의는_한_번에_걷힌다():
    doc = _DOC.replace("## 참고", "> [이의 @형식] a\n> [이의 @형식] b\n> [이의 @게임QA] c\n\n## 참고")
    out = strip_form_objections(doc)
    assert "[이의 @형식]" not in out
    assert "[이의 @게임QA] c" in out


def test_기계_이의는_지워도_다시_선다():
    """회의 루프가 매 패스 낡은 기계 이의를 걷고 지금 결과만 쓴다 — 삭제로 해소되지 않는다."""
    import inspect

    from system.rule import communication as C
    src = inspect.getsource(C)
    assert "strip_form_objections as _strip_fo" in src, "기계 이의를 새로 쓰지 않는다"
    # 낡은 억제 조건(`"@형식" not in _dtxt`)이 **코드 줄**에 남아 있으면 안 된다 — 주석의 인용은 제외.
    _code = [l for l in src.splitlines() if not l.lstrip().startswith("#")]
    assert not [l for l in _code if '"@형식" not in _dtxt' in l], "이미 있으면 안 쓰는 조건이 남아 있다"
    assert "지워도 다음 패스에 다시 섭니다" in src, "지워도 소용없다는 사실을 봇에게 말하지 않는다"
