"""단계 분리 전수 정합 — 각 회의가 '자기 하나'만 요구하고, 표에 빠진 단계가 없다."""
from system.rule.milestone import (_STAGE_FRAME, _STAGE_KEY, _STAGE_META, _STAGE_TITLE,
                                   stage_agenda, stage_draft_template, stage_preflight)

STAGES = ("goal", "criteria", "milestone", "subtask", "backlog")


def test_모든_표가_전_단계를_덮는다():
    for st in STAGES:
        assert st in _STAGE_KEY, f"_STAGE_KEY에 {st} 없음"
        assert st in _STAGE_FRAME, f"_STAGE_FRAME에 {st} 없음"
        assert st in _STAGE_TITLE, f"_STAGE_TITLE에 {st} 없음"
        assert st in _STAGE_META, f"_STAGE_META에 {st} 없음"
        assert stage_agenda(st)[0], f"{st} 안건 없음"


def test_목표회의_골격에는_완수조건_칸이_없다():
    """[사용자 지적] 골격에 조건 칸이 남아 있으면, 단계를 쪼개도 봇들이 그 칸을 채우느라
    이 회의에서 검증 명령·수치를 확정한다(U-436 실측: 150ms·exit 0·verify_ui.py)."""
    body = stage_draft_template("goal") or ""
    assert "목표:" in body and "구성 점검:" in body
    assert "완수조건" not in body and "실증:" not in body


def test_완수조건회의_골격에는_조건만():
    body = stage_draft_template("criteria") or ""
    assert "완수조건:" in body and "실증:" in body
    assert "목표:" not in body and "단위:" not in body


def test_목표회의_사전검사는_조건을_요구하지_않는다():
    """등록·골격에서 조건을 뺐는데 사전검사가 계속 요구하면 회의가 영영 안 닫힌다(잔재)."""
    errs = stage_preflight("goal", "[수렴안]\n목표: 2인 턴제 카드 대전\n[/수렴안]")
    assert not errs, errs


def test_완수조건회의_사전검사는_조건을_요구한다():
    errs = stage_preflight("criteria", "[수렴안]\n그냥 잘 만들자\n[/수렴안]")
    assert errs and any("조건" in e for e in errs)


def test_변경_가시화가_배선돼_있다():
    """[사용자: '이의 제출했습니다 이러고 그게 뭔지는 안 보이니깐'] 결론이 어떻게 바뀌었는지를
    채널이 보여야 한다 — 파일 안에만 있으면 사람은 못 본다."""
    import inspect

    from system.rule import communication as _c
    src = inspect.getsource(_c)
    assert "[결론 변경]" in src and "region_lines" in src
