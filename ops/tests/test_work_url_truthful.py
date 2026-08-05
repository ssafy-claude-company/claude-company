"""보고에 실리는 확인 링크는 실제로 열려야 한다(사용자 제보: P-078 링크 404).

[개정 2026-08-05, 사용자: '마일스톤 보고에 링크가 보고가 안되는 느낌'] 종전 구현(목록 조회
/projects/)은 ①/api 누락(SPA HTML 수신) ②익명이라 비공개 판 미노출 ③anon 스로틀 429 —
셋 다에 걸려 링크가 늘 빈 값이었다. 이제 가이드 토큰 인증 엔드포인트(/api/guide/work_link/)가
서버 지식(앱 풀 정본 우선)으로 직접 답한다. '열리는 주소만 준다'는 계약은 그대로 — 지어내지
않고, 서버가 빈 값이면 빈 값이다.
"""
from guide.murmur_guide import MurmurGuide


class _G(MurmurGuide):
    def __init__(self, url_by_channel):
        self._urls = url_by_channel
        self._origin_channel = None
        self.calls = []

    def _get_sync(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        assert path == "/api/guide/work_link/"          # 목록 스캔이 아니라 서버에 직접 묻는다
        return {"url": self._urls.get(int((params or {}).get("channel", 0)), "")}


def test_열리는_판만_주소를_준다():
    g = _G({267: "/api/projects/U-442/works/"})
    assert g.work_url("P-078", 267) == "https://murmur.dojin-mini.shop/api/projects/U-442/works/"


def test_앱풀_정본도_공개_도메인으로_준다():
    g = _G({267: "/apps/organt-p-078/"})
    assert g.work_url("P-078", 267) == "https://murmur.dojin-mini.shop/apps/organt-p-078/"


def test_서빙할_산출물이_없으면_빈값():
    g = _G({267: ""})
    assert g.work_url("P-078", 267) == ""


def test_러너_id는_주소에_쓰이지_않는다():
    g = _G({267: "/api/projects/U-442/works/"})
    assert "P-078" not in g.work_url("P-078", 267)


def test_채널을_모르면_지어내지_않는다():
    g = _G({267: "/api/projects/U-442/works/"})
    assert g.work_url("P-078") == ""
    assert g.calls == []                                # 묻지도 않는다
