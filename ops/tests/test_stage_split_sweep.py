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
    errs = stage_preflight("goal", "[수렴안]\n목표: 2인 턴제 카드 대전\n내용 폭: 기능 3종\n창의 설계: 방패병 — 앞 열이 받는 피해 40% 감소\n최대 표준: 실제 예 대조 · 핵심 기능 3종 · 주 사용 흐름 원탭\n[/수렴안]")
    assert not errs, errs


def test_완수조건회의_사전검사는_조건을_요구한다():
    errs = stage_preflight("criteria", "[수렴안]\n그냥 잘 만들자\n[/수렴안]")
    assert errs and any("조건" in e for e in errs)


def test_변경은_회의록에만_남고_채널을_도배하지_않는다():
    """[사용자 지적 2026-07-30: '회의 작업이 도배됐어'] 결론이 어떻게 바뀌었는지는 남아야 하지만,
    매 발언마다 채널 메시지로 띄우면 회의 한 건이 수십 줄로 분다. 기록은 회의록이 진다."""
    import inspect

    from system.rule import communication as _c
    src = inspect.getsource(_c)
    assert "region_lines" in src, "변경 추적 자체는 유지"
    i = src.index("region_lines")
    seg = src[i - 1500:i + 1500]
    assert '_say_speech(flow, m, "[결론 변경]"' not in seg, "채널 게시는 도배가 된다"
    assert 'minutes.append(f"[변경]' in seg, "회의록에는 남아야 한다"
