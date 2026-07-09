"""직군명 정규화(_same_job) — 로스터 파편(약칭) 병합의 계약.

사용자 피드백(2026-07-09 문제3): 로스터에 '프론트'와 '프론트엔드'가 따로 있어 문자열
매칭이 다른 직군으로 취급 → 적임자 누락·중복 생성. 접두(약칭)는 같게, 부분 포함은
전문화 보존 위해 다르게.
"""
from system.rule.comm_helpers import _same_job, _find_variant_job


def test_약칭_접두는_같은_직군():
    assert _same_job("프론트", "프론트엔드")
    assert _same_job("프론트엔드", "프론트")      # 대칭
    assert _same_job("기획", "기획자")
    assert _same_job("QA", "QA 엔지니어")
    assert _same_job("백엔드", "백엔드")           # 완전 일치


def test_부분포함은_다른_직군_전문화_보존():
    # '디자이너'가 뒤에 붙는 부분 포함은 별개 전문화(게임 비주얼 디자이너 ≠ 디자이너)
    assert not _same_job("디자이너", "게임 비주얼 디자이너")
    assert not _same_job("엔지니어", "AI 엔지니어")
    assert not _same_job("백엔드", "프론트엔드")   # 무관
    assert not _same_job("", "백엔드")             # 빈 값


def test_변형게이트_약칭은_재사용_통과():
    """'프론트엔드' 존재 시 '프론트' 공고는 변형(중복 생성)이 아니라 재사용 — None 반환(통과)."""
    assert _find_variant_job("프론트", {"프론트엔드", "백엔드"}) is None
    # 토큰 공유 변형은 여전히 잡힘(VFX 전문가 ↔ VFX 아티스트)
    assert _find_variant_job("VFX 아티스트", {"VFX 전문가"}) == "VFX 전문가"
    # 완전 무관은 통과
    assert _find_variant_job("사운드", {"백엔드", "QA"}) is None
