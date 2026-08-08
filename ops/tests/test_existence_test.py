"""존재이유 테스트 복원 (2026-08-06, 사용자: '여러 좋은 설계가 나왔던 옛 구조적에서 얻을 이점은
다 얻은거야?').

[규명] 디코 시절 set_goal에는 '존재이유 게이트'가 있었다 — acceptance에 *이 산출물이 진짜 그것임을
증명하는 전체·부정형 검증*(실패하면 핵심 목적이 깨지는 것)이 없으면 확정을 보류했다. p-012 GOAL은
그 항목을 둘 갖고 있었다: '메타 재화 0계정 대비 최대계정 도달시간 차 15% 미만이면 성장루프가 깨진
것' · '리더보드 API가 500이어도 플레이는 계속돼야 한다'.

지금 파이프라인의 완수조건은 criteria 회의가 낳고 set_goal을 거치지 않는다 — 그래서 그 관문이
통째로 빠졌다(실측: 현행 전 판 Acceptance에 존재이유 0건). 그 자리를 '버튼이 있나·이벤트가
발화하나' 같은 부품 체크가 채웠고, 부품은 통과인데 전체는 목적 미달인 산출물이 마감됐다.

[수리] criteria 등록에 존재이유 표기(조건 줄의 `[존재이유]` 또는 `존재이유:` 줄)를 요구한다.
형태만 본다 — 그 검증이 좋은지 나쁜지는 판단하지 않는다.
"""
import sys
import types

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.milestone import register_stage, stage_draft_template


class _Cur:
    def __init__(self):
        self.task_id = "T-1"
        self.status = types.SimpleNamespace(goal="2인 협동 게임")
        self.acceptance = ""
        self.content_floor = []
        self.creative = []
        self.standard = ""
        self.interfaces = ""


class _Flow:
    def __init__(self, tmp):
        self.current = _Cur()
        self.log = None
        self.milestones = []
        self.workspace = str(tmp)


def test_부품_체크만이면_반려된다(tmp_path):
    ok, why = register_stage(_Flow(tmp_path), "criteria",
                             "[수렴안]\n조건: 시작 버튼이 있다 | 실증: node t.js\n[/수렴안]")
    assert not ok and "존재이유" in why and "부품은 통과인데" in why


def test_존재이유가_있으면_등록된다(tmp_path):
    f = _Flow(tmp_path)
    ok, why = register_stage(f, "criteria",
                             "[수렴안]\n조건: 한 판이 끝난다 | 실증: node t.js\n"
                             "조건: [존재이유] 솔로로는 클리어 불가 | 실증: node solo.js\n[/수렴안]")
    assert ok, why
    assert "솔로로는 클리어 불가" in f.current.acceptance


def test_별도_줄_표기도_받는다(tmp_path):
    f = _Flow(tmp_path)
    ok, why = register_stage(f, "criteria",
                             "[수렴안]\n조건: 한 판이 끝난다 | 실증: node t.js\n"
                             "존재이유: 강화 없이는 5웨이브 생존 불가\n[/수렴안]")
    assert ok, why


def test_골격이_존재이유_칸을_보여준다():
    t = stage_draft_template("criteria", "안건")
    assert "[존재이유]" in t
