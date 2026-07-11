"""메타 서비스 URL — 프로젝트가 1급.

/                      프로젝트 목록(홈)
/p/<slug>/             프로젝트 지도(논리→소스)
/p/<slug>/feedback     프로젝트 백로그(피드백)
/feedback              전체 백로그(모든 프로젝트)
/api/projects/…        graph API   ·   /api/feedback/… 피드백 API
/static/…              자산(fb.js·vendor)   ·   /codegraph/ 옛 경로 → 새 홈으로
"""
from pathlib import Path

from django.http import FileResponse, Http404, HttpResponseRedirect
from django.urls import include, path

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

_CT = {".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
       ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8"}


def _page(name):
    def view(request, **kw):
        p = STATIC_DIR / name
        if not p.exists():
            raise Http404(name)
        return FileResponse(open(p, "rb"), content_type="text/html; charset=utf-8")
    return view


def _static(request, name):
    """static/ 파일 서빙(vendor/ 하위 포함) — resolve로 static/ 밖 탈출 차단."""
    p = (STATIC_DIR / name).resolve()
    if not str(p).startswith(str(STATIC_DIR.resolve()) + "/") or not p.is_file():
        raise Http404(name)
    return FileResponse(open(p, "rb"), content_type=_CT.get(p.suffix, "application/octet-stream"))


urlpatterns = [
    path("", _page("index.html")),
    path("p/<slug:slug>/", _page("project.html")),          # 캔버스 허브
    path("p/<slug:slug>/c/<slug:cid>/", _page("canvas.html")),
    path("p/<slug:slug>/feedback", _page("backlog.html")),
    path("feedback", _page("backlog.html")),
    path("api/projects/", include("graph.urls")),
    path("api/feedback/", include("feedback.urls")),
    path("static/<path:name>", _static),
    path("codegraph/", lambda r: HttpResponseRedirect("../")),   # 옛 경로 호환
]
