"""[재검증 재판 차단(2026-08-04, 사용자: '적절한 가격과 시간으로')]

실측 U-496: 같은 3엔진×2뷰포트 매트릭스 검증이 단계를 옮겨가며 재등재돼(ST-5 done → ST-8에 같은
검증 5건) 하루 $87가 연소됐다. 이 마일스톤에서 이미 완료된 검증과 실질 중복인 검증 줄은 무엇이
바뀌어 다시 재는지 명시([변경 재검증: …])해야만 등재된다 — 산출물이 안 바뀐 재검증은 이미 있는
증거의 재구매다. 시스템은 내용을 판단하지 않는다(중복+무사유라는 형태만)."""
import io as _io, sys

sys.path.insert(0, __file__.rsplit('/ops/', 1)[0])


def _seg():
    import system.rule.milestone as m
    s = _io.open(m.__file__.replace('.pyc', '.py'), encoding='utf-8').read()
    i = s.index('backlog_verify_rerun_skipped')
    return s[i - 1600:i + 300]


def test_게이트가_등재_지점에_실재한다():
    seg = _seg()
    assert '[변경 재검증' in seg                      # 명시 탈출구
    assert "status', '') == 'done'" in seg           # 완료된 검증과의 중복만
    assert '_bov' in seg                              # 어휘 겹침 판정 재사용(하드코딩 없음)


def test_검증_어휘가_아닌_구현줄은_건드리지_않는다():
    seg = _seg()
    assert '_is_verify' in seg and '검증' in seg      # 검증 분류가 조건에 걸려 있다
