"""[이름이 비면 문장이 깨진다(2026-08-02, 대화 전수 실측)] 화면에 "의 백로그가 완료됐습니다"(이름 없음)와
"[다음] 556001 · …"(원시 id)가 그대로 떴다. 사람에게 보이는 줄에는 사람 이름이 있어야 한다."""
from system.rule.backlog import BacklogRelay, handoff_note


class _F:
    log = None

    def __init__(self, info):
        self._info = info
        self._pipeline_notes = []


def _run(info):
    f = _F(info)
    r = BacklogRelay("MS-X/ST-1")
    r.submit(11, "남은 일감 하나", force=True)
    handoff_note(f, r, 99, "완료됐습니다")
    return "\n".join(f._pipeline_notes)


def test_이름이_비면_중립어로_대체된다():
    out = _run(lambda x: "")
    assert "의 백로그가" in out and not out.startswith("[다음] 의")
    assert "담당자의 백로그가" in out


def test_원시_id는_이름이_아니다():
    out = _run(lambda x: "556001")
    assert "556001" not in out.split("·")[0]


def test_정상_이름은_그대로():
    assert "배승우의 백로그가" in _run(lambda x: "배승우")
