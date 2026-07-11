"""캔버스 API — 사람의 스케치와 AI의 문서화가 같은 판에 쓰는 단일 계약.

GET/POST      /api/projects/<slug>/canvases/
PATCH/DELETE  /api/projects/<slug>/canvases/<cid>/
GET           /api/projects/<slug>/canvases/<cid>/objects/     {items, links, rev}
POST          …/items/        {kind, text?, x, y, w?, h?, origin?, meta?}
PATCH/DELETE  …/items/<id>/
POST          …/links/        {s, t, label?, origin?}
PATCH/DELETE  …/links/<id>/

origin='ai' 로 쓰면 AI가 그린 것 — 화면이 구분 표시한다. 세션·봇도 이 API로 그린다.
"""
from django.utils.text import slugify
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from feedback.auth import resolve_admin

from .models import Canvas, Item, Link, Project

_DENY = {"detail": "관리자만 쓸 수 있어요."}


def _gate(request):
    return None if resolve_admin(request) else Response(_DENY, status=403)


def _canvas(c):
    return {"cid": c.cid, "name": c.name, "items": c.items.count(),
            "updated": c.updated_at.strftime("%m-%d %H:%M")}


def _item(i):
    return {"id": i.id, "kind": i.kind, "text": i.text, "x": i.x, "y": i.y, "w": i.w, "h": i.h,
            "origin": i.origin, "meta": i.meta, "z": i.z}


def _link(l):
    return {"id": l.id, "s": l.s_id, "t": l.t_id, "label": l.label, "origin": l.origin}


def _get_canvas(slug, cid):
    return Canvas.objects.filter(project__slug=slug, cid=cid).first()


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def canvases(request, slug):
    if (g := _gate(request)):
        return g
    p = Project.objects.filter(slug=slug).first()
    if not p:
        return Response({"detail": "없는 프로젝트."}, status=404)
    if request.method == "GET":
        return Response({"canvases": [_canvas(c) for c in p.canvases.all()]})
    name = (request.data.get("name") or "캔버스").strip()[:120]
    cid = slugify(request.data.get("cid") or name)
    if not cid or Canvas.objects.filter(project=p, cid=cid).exists():
        i = p.canvases.count() + 1
        while Canvas.objects.filter(project=p, cid=f"c{i}").exists():
            i += 1
        cid = f"c{i}"
    c = Canvas.objects.create(project=p, cid=cid, name=name)
    return Response(_canvas(c), status=201)


@api_view(["PATCH", "DELETE"])
@permission_classes([AllowAny])
def canvas(request, slug, cid):
    if (g := _gate(request)):
        return g
    c = _get_canvas(slug, cid)
    if not c:
        return Response({"detail": "없는 캔버스."}, status=404)
    if request.method == "DELETE":
        c.delete()
        return Response({"deleted": cid})
    if "name" in request.data:
        c.name = str(request.data["name"]).strip()[:120] or c.name
        c.save(update_fields=["name", "updated_at"])
    return Response(_canvas(c))


@api_view(["GET"])
@permission_classes([AllowAny])
def objects(request, slug, cid):
    if (g := _gate(request)):
        return g
    c = _get_canvas(slug, cid)
    if not c:
        return Response({"detail": "없는 캔버스."}, status=404)
    return Response({"name": c.name,
                     "items": [_item(i) for i in c.items.all()],
                     "links": [_link(l) for l in c.links.select_related(None).all()]})


@api_view(["POST"])
@permission_classes([AllowAny])
def items(request, slug, cid):
    if (g := _gate(request)):
        return g
    c = _get_canvas(slug, cid)
    if not c:
        return Response({"detail": "없는 캔버스."}, status=404)
    d = request.data or {}
    kind = d.get("kind") or "sticky"
    if kind not in ("sticky", "box"):
        return Response({"detail": f"kind는 sticky|box: {kind}"}, status=400)
    i = Item.objects.create(
        canvas=c, kind=kind, text=str(d.get("text") or "")[:4000],
        x=float(d.get("x") or 0), y=float(d.get("y") or 0),
        w=float(d.get("w") or (180 if kind == "sticky" else 320)),
        h=float(d.get("h") or (100 if kind == "sticky" else 220)),
        origin="ai" if d.get("origin") == "ai" else "user",
        meta=d.get("meta") or {}, z=int(d.get("z") or (0 if kind == "box" else 1)))
    return Response(_item(i), status=201)


@api_view(["PATCH", "DELETE"])
@permission_classes([AllowAny])
def item(request, slug, cid, iid):
    if (g := _gate(request)):
        return g
    i = Item.objects.filter(canvas__project__slug=slug, canvas__cid=cid, id=iid).first()
    if not i:
        return Response({"detail": "없는 객체."}, status=404)
    if request.method == "DELETE":
        i.delete()
        return Response({"deleted": iid})
    d = request.data or {}
    for f in ("x", "y", "w", "h"):
        if f in d:
            setattr(i, f, float(d[f]))
    if "text" in d:
        i.text = str(d["text"])[:4000]
    if "z" in d:
        i.z = int(d["z"])
    if isinstance(d.get("meta"), dict):
        i.meta = {**i.meta, **d["meta"]}
        i.meta = {k: v for k, v in i.meta.items() if v not in ("", None)}
    i.save()
    return Response(_item(i))


@api_view(["POST"])
@permission_classes([AllowAny])
def links(request, slug, cid):
    if (g := _gate(request)):
        return g
    c = _get_canvas(slug, cid)
    if not c:
        return Response({"detail": "없는 캔버스."}, status=404)
    d = request.data or {}
    s = c.items.filter(id=d.get("s")).first()
    t = c.items.filter(id=d.get("t")).first()
    if not s or not t or s.id == t.id:
        return Response({"detail": "s·t가 이 캔버스의 서로 다른 객체여야 해요."}, status=400)
    l = Link.objects.create(canvas=c, s=s, t=t, label=str(d.get("label") or "")[:120],
                            origin="ai" if d.get("origin") == "ai" else "user")
    return Response(_link(l), status=201)


@api_view(["PATCH", "DELETE"])
@permission_classes([AllowAny])
def link(request, slug, cid, lid):
    if (g := _gate(request)):
        return g
    l = Link.objects.filter(canvas__project__slug=slug, canvas__cid=cid, id=lid).first()
    if not l:
        return Response({"detail": "없는 연결."}, status=404)
    if request.method == "DELETE":
        l.delete()
        return Response({"deleted": lid})
    if "label" in request.data:
        l.label = str(request.data["label"]).strip()[:120]
        l.save(update_fields=["label"])
    return Response(_link(l))
