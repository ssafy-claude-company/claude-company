"""보고에 실리는 확인 링크는 실제로 열려야 한다(사용자 제보: P-078 링크 404)."""
from guide.murmur_guide import MurmurGuide


class _G(MurmurGuide):
    def __init__(self, rows):
        self._rows = rows
        self._origin_channel = None

    def _get_sync(self, path, params=None):
        return {"results": self._rows}


def test_열리는_판만_주소를_준다():
    g = _G([{"id": 267, "pid": "U-442", "has_work": True}])
    assert g.work_url("P-078", 267) == "https://murmur.dojin-mini.shop/api/projects/U-442/works/"


def test_서빙할_산출물이_없으면_빈값():
    g = _G([{"id": 267, "pid": "U-442", "has_work": False}])
    assert g.work_url("P-078", 267) == ""


def test_러너_id는_주소에_쓰이지_않는다():
    g = _G([{"id": 267, "pid": "U-442", "has_work": True}])
    assert "P-078" not in g.work_url("P-078", 267)


def test_채널을_모르면_지어내지_않는다():
    g = _G([{"id": 267, "pid": "U-442", "has_work": True}])
    assert g.work_url("P-078") == ""
