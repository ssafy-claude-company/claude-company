"""task 순수 판정기 검증 — 데이터 출처(_wants_real_data·_synthesizes_data·_has_real_dataset)·
지각 비대칭(_perceptual_essential)·검증 기능 식별(_is_verifier).

PJT tests/test_sys.py 순수부(데이터출처 헬퍼·percept·QA 식별 어서션)의 unittest 포팅 —
Flow/Sys 없이 함수 단독으로 판정 로직을 고정한다.

실행:
  cd /root/murmur-stack && PYTHONPATH=/root/murmur-stack \
  /root/murmur-stack/.venv/bin/python -m unittest discover -s system/tests -t /root/murmur-stack -v
"""
import os
import tempfile
import unittest

from system.guide_tools import (_has_real_dataset, _is_verifier,
                                _perceptual_essential, _synthesizes_data,
                                _wants_real_data)


class DataProvenanceJudgeTest(unittest.TestCase):

    def test_데이터출처_헬퍼_발동조건과_합성탐지(self):
        """[라이브 P-021] 데이터 출처 게이트의 판단 로직: 요청이 '실제/공공 데이터 학습'을 요구할
        때만 발동하고, 학습 코드의 합성/하드코딩 흔적과 '받아온 실데이터 파일' 부재를 본다."""
        assert _wants_real_data("지금까지 안 쓴 공공데이터로 AI 학습시켜줘") is True
        assert _wants_real_data("국토부 실거래가 예측 모델 만들어줘") is True
        assert _wants_real_data("스네이크 게임 만들어줘") is False          # 데이터 학습 요청 아님 → 발동 안 함
        assert _wants_real_data("") is False
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "ws")
            os.makedirs(os.path.join(ws, "model"))
            with open(os.path.join(ws, "model", "train.py"), "w", encoding="utf-8") as fp:
                fp.write("# 합성 데이터로 학습\nimport numpy as np\ndef generate_price():\n    return 1\n")
            hit = _synthesizes_data(ws)
            assert hit is not None and hit[0] == "train.py"               # 합성 흔적 탐지
            assert _has_real_dataset(ws) is False                        # 실데이터 파일 없음
            with open(os.path.join(ws, "seoul_apt.csv"), "w", encoding="utf-8") as fp:
                fp.write("gu,area,price\n" + "강남,84,150000\n" * 200)   # 받아온 실데이터(>2KB)
            assert _has_real_dataset(ws) is True                         # 이제 실데이터 증거 있음


class PerceptualJudgeTest(unittest.TestCase):

    def test_지각차원_오디오신호_essential_판정(self):
        """팀 라벨 또는 텍스트에 오디오 신호가 있으면 essential(도메인 무관) — 빈 '[지각차원 없음]'
        반사 선언을 모순으로 거부하는 percept 게이트의 판정 함수."""
        assert _perceptual_essential(["사운드 디자이너", "백엔드"], ["게임"]) is True
        assert _perceptual_essential(["백엔드"], ["BGM 좋은 게임"]) is True        # 기준 텍스트로도 탐지
        assert _perceptual_essential(["백엔드", "프론트엔드"], ["퍼즐 게임"]) is False  # 오디오 신호 없음


class VerifierJudgeTest(unittest.TestCase):

    def test_검증기능_식별_is_verifier(self):
        """'검증/품질(QA)' 기능 역할을 능력 키워드로 식별(타이틀 하드코딩 아님) — 검증 게이트가
        최종 인수를 QA에 우선 라우팅하는 판정 함수."""
        assert _is_verifier("QA") and _is_verifier("품질 검증자") and _is_verifier("Quality Engineer")
        assert not _is_verifier("백엔드") and not _is_verifier("프론트엔드") and not _is_verifier("")


if __name__ == "__main__":
    unittest.main()
