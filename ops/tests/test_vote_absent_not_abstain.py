"""[응답 없음은 기권이 아니다(2026-08-04, 실측 U-496)]

타 흐름에 바쁜 봇은 표결 fork가 건너뛰어 응답이 None이 되는데, 종전엔 이것이 **무사유 기권**으로
집계·게시됐다('투표: 기획@556001 | 기권' — 사유 없음, 채널에 6건). '빈 표는 무효·반려'
구조(2026-07-22)가 정확히 이 경로에서만 우회됐다 — 반려(_redo)는 res is not None만 겨냥했다.

기권은 판단 보류라는 **판단**이고 사유가 있어야 한다. 응답이 없는 것은 불참, 반려 후에도 사유가
빈 것은 무효 — 사실대로 표기한다(집계는 종전과 동일: 셋 다 찬반 셈 밖)."""
import io, re, sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])


def _src():
    import system.rule.communication as c
    return io.open(c.__file__.replace(".pyc", ".py"), encoding="utf-8").read()


def test_무응답은_불참으로_표기된다():
    s = _src()
    i = s.index('_resp.get(m) is None')
    seg = s[i:i + 400]
    assert '"absent"' in seg, "무응답이 여전히 기권으로 둔갑한다"


def test_반려후에도_빈_기권은_무효다():
    s = _src()
    assert '"invalid"' in s
    i = s.index('_vote == "abstain" and not _reason')
    assert '"invalid"' in s[i:i + 300]


def test_라벨이_사실을_말한다():
    s = _src()
    assert "불참(응답 없음)" in s and "무효(사유 없음)" in s
