"""[2026-07-08 기억 튜닝 배치] 발동선 하향·천장 성장 연동·'없음' 기준 필터·장부 분류 확장의 계약 테스트.
검수 근거: 사용자 승인 6건 판단(증류 발동선 트레이드오프·capability_ledger 공백 원인·빈 기준 잔여물)."""
from system.audit import CAP_MIN, capability_of
from system.sys_core import Sys


def test_증류_발동선_기본값_5():
    # 위 경계: 첫 wake 원석 주입 창(최근 6건)을 넘기 전에 증류 — 유실 구간 제거.
    assert Sys._BOT_DISTILL_MIN == 5


def test_천장은_증류_실적에_비례_상한_1200():
    assert Sys._distill_cap(0) == 600
    assert Sys._distill_cap(5) == 900
    assert Sys._distill_cap(10) == 1200
    assert Sys._distill_cap(99) == 1200       # 상한 고정 — 무한 확장 금지
    assert Sys._distill_cap(None) == 600      # 결측 관용
    assert Sys._distill_cap(-3) == 600        # 음수 방어


def test_없음류_기준은_hollow로_판정():
    for t in ("없음", "없다.", " None ", "n/a", "-", "특이사항 없음", "해당 없음", ""):
        assert Sys._hollow_standard(t), t
    for t in ("배포 전 REPORTS.md로 역추적", "없음 판정 전에 실데이터 확인"):
        assert not Sys._hollow_standard(t), t


def test_장부_분류가_실업무를_덮는다():
    # [원인 교정] 종전 4범주는 웹앱 업무(js·html·py)를 증거 0으로 세어 장부가 늘 비었다.
    assert capability_of("Write", {"file_path": "web/app.js"}) == "웹 프론트엔드 구현"
    assert capability_of("Edit", {"file_path": "src/App.vue"}) == "웹 프론트엔드 구현"
    assert capability_of("Write", {"file_path": "public/index.html"}) == "웹 프론트엔드 구현"
    assert capability_of("Write", {"file_path": "api/server.py"}) == "백엔드·API 구현"
    assert capability_of("mcp__guide__run", {"command": "pytest -q tests/"}) == "품질 검증(QA)"
    assert capability_of("mcp__guide__run", {"command": "npx playwright test"}) == "품질 검증(QA)"
    # 기존 4범주 무변경(회귀 가드)
    assert capability_of("Write", {"file_path": "d/model.pkl"}) == "AI/ML(모델 학습·예측)"
    assert capability_of("mcp__guide__deploy", {}) == "배포·인프라(DevOps)"
    # 신규 범주도 표면화 임계치 보유(1회 우연 저작 배제 동일 적용)
    for k in ("웹 프론트엔드 구현", "백엔드·API 구현", "품질 검증(QA)"):
        assert CAP_MIN.get(k) == 3, k
