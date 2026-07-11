"""dev 서비스 URL — /(백로그) · /codegraph/(코드 지도) · /static/ · /api/feedback/.

nginx가 /dev/를 이 서비스로 프록시(접두 제거)하면 브라우저 경로는 /dev/…가 된다.
페이지 안의 참조는 전부 상대경로라 접두가 있든 없든 동작한다.
"""
from pathlib import Path

from django.http import FileResponse, Http404
from django.urls import include, path

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"   # ops/dev/static


def _file(name, ctype):
    def view(request):
        p = STATIC_DIR / name
        if not p.exists():
            raise Http404(name)
        return FileResponse(open(p, "rb"), content_type=ctype)
    return view


urlpatterns = [
    path("", _file("backlog.html", "text/html; charset=utf-8")),
    path("codegraph/", _file("codegraph.html", "text/html; charset=utf-8")),
    path("static/codegraph.json", _file("codegraph.json", "application/json; charset=utf-8")),
    path("codegraph/codegraph.json", _file("codegraph.json", "application/json; charset=utf-8")),   # 페이지 상대참조(./codegraph.json)용 별칭
    path("api/feedback/", include("feedback.urls")),
]
