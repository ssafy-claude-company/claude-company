"""플랫폼 API — 원시 기능(Node·Edge·View) CRUD + 프로젝트·스캔.

GET/POST      /api/projects/
GET/DELETE    /api/projects/<slug>/
POST          /api/projects/<slug>/scan/            스캔 → Node/Edge 동기(origin=scan만)
GET           /api/projects/<slug>/graph/           {nodes, edges, views}  (화면 1회 로드)
POST          /api/projects/<slug>/nodes/           {type, name, meta?, x?, y?}
PATCH/DELETE  /api/projects/<slug>/nodes/<nid>/     이름·타입·meta 병합·좌표 / 삭제(user만)
POST          /api/projects/<slug>/edges/           {s, t, label?, both?}
PATCH/DELETE  /api/projects/<slug>/edges/<id>/
GET/POST      /api/projects/<slug>/views/           {name, types, lanes}
PATCH/DELETE  /api/projects/<slug>/views/<vid>/
"""
import os

from django.utils.text import slugify
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from feedback.auth import resolve_admin

from .models import Edge, Node, Project, ScanRun, View
from .scanner import scan
from .sync import sync_scan

_DENY = {"detail": "관리자만 쓸 수 있어요."}


def _gate(request):
    return None if resolve_admin(request) else Response(_DENY, status=403)


def _proj(p):
    last = p.scans.first()
    return {"slug": p.slug, "name": p.name, "root": p.root, "skips": p.skips,
            "nodes": p.nodes.count(), "user_nodes": p.nodes.filter(origin="user").count(),
            "views": p.views.count(), "canvases": p.canvases.count(),
            "last_scan": ({"at": last.created_at.strftime("%Y-%m-%d %H:%M"), "ok": last.ok, **last.stats}
                          if last else None)}


def _node(n):
    return {"nid": n.nid, "type": n.type, "name": n.name, "origin": n.origin,
            "meta": n.meta, "payload": n.payload, "x": n.x, "y": n.y}


def _edge(e):
    return {"id": e.id, "s": e.s, "t": e.t, "label": e.label, "both": e.both, "origin": e.origin}


def _view(v):
    return {"vid": v.vid, "name": v.name, "types": v.types, "lanes": v.lanes, "order": v.order}


DEFAULT_VIEWS = [
    {"vid": "all", "name": "전체", "types": [], "lanes": [], "order": 9},
    {"vid": "concept", "name": "개념", "types": ["concept"], "lanes": [], "order": 0},
    {"vid": "files", "name": "파일", "types": ["file"], "lanes": [], "order": 5},
]


def ensure_default_views(p):
    for d in DEFAULT_VIEWS:
        View.objects.get_or_create(project=p, vid=d["vid"], defaults=d)


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def projects(request):
    if (g := _gate(request)):
        return g
    if request.method == "GET":
        return Response({"projects": [_proj(p) for p in Project.objects.all()]})
    name = (request.data.get("name") or "").strip()
    root = (request.data.get("root") or "").strip()
    slug = slugify(request.data.get("slug") or name)
    if not (name and root and slug):
        return Response({"detail": "name·root(·slug)가 필요해요."}, status=400)
    if not os.path.isdir(root):
        return Response({"detail": f"루트 경로가 없어요: {root}"}, status=400)
    if Project.objects.filter(slug=slug).exists():
        return Response({"detail": f"slug 중복: {slug}"}, status=400)
    p = Project.objects.create(slug=slug, name=name, root=root, skips=request.data.get("skips") or [])
    ensure_default_views(p)
    return Response(_proj(p), status=201)


@api_view(["GET", "DELETE"])
@permission_classes([AllowAny])
def project(request, slug):
    if (g := _gate(request)):
        return g
    p = Project.objects.filter(slug=slug).first()
    if not p:
        return Response({"detail": "없는 프로젝트."}, status=404)
    if request.method == "DELETE":
        p.delete()
        return Response({"deleted": slug})
    return Response(_proj(p))


@api_view(["POST"])
@permission_classes([AllowAny])
def project_scan(request, slug):
    if (g := _gate(request)):
        return g
    p = Project.objects.filter(slug=slug).first()
    if not p:
        return Response({"detail": "없는 프로젝트."}, status=404)
    try:
        data = scan(p.root, p.skips)
        st = sync_scan(p, data)
        ScanRun.objects.create(project=p, ok=True, data=data, stats=st)
        ensure_default_views(p)
    except Exception as e:
        ScanRun.objects.create(project=p, ok=False, error=str(e)[:2000])
        return Response({"detail": f"스캔 실패: {e}"}, status=500)
    return Response({"ok": True, **st})


@api_view(["GET"])
@permission_classes([AllowAny])
def project_graph(request, slug):
    if (g := _gate(request)):
        return g
    p = Project.objects.filter(slug=slug).first()
    if not p:
        return Response({"detail": "없는 프로젝트."}, status=404)
    return Response({"nodes": [_node(n) for n in p.nodes.all()],
                     "edges": [_edge(e) for e in p.edges.all()],
                     "views": [_view(v) for v in p.views.all()]})


@api_view(["POST"])
@permission_classes([AllowAny])
def nodes(request, slug):
    if (g := _gate(request)):
        return g
    p = Project.objects.filter(slug=slug).first()
    if not p:
        return Response({"detail": "없는 프로젝트."}, status=404)
    name = (request.data.get("name") or "").strip()
    if not name:
        return Response({"detail": "name이 필요해요."}, status=400)
    n = Node.objects.create(project=p, nid="pending", origin="user",
                            type=(request.data.get("type") or "concept").strip()[:40] or "concept",
                            name=name[:200], meta=request.data.get("meta") or {},
                            x=request.data.get("x"), y=request.data.get("y"))
    n.nid = f"u{n.id}"
    n.save(update_fields=["nid"])
    return Response(_node(n), status=201)


@api_view(["PATCH", "DELETE"])
@permission_classes([AllowAny])
def node(request, slug, nid):
    if (g := _gate(request)):
        return g
    n = Node.objects.filter(project__slug=slug, nid=nid).first()
    if not n:
        return Response({"detail": "없는 노드."}, status=404)
    if request.method == "DELETE":
        if n.origin == "scan":
            return Response({"detail": "스캔 노드는 지울 수 없어요(재스캔이 되살림) — 제외는 프로젝트 skips로."}, status=400)
        Edge.objects.filter(project=n.project).filter(s=n.nid).delete()
        Edge.objects.filter(project=n.project).filter(t=n.nid).delete()
        n.delete()
        return Response({"deleted": nid})
    d = request.data or {}
    if "name" in d and n.origin == "user":
        n.name = str(d["name"]).strip()[:200] or n.name
    if "type" in d and n.origin == "user":
        n.type = str(d["type"]).strip()[:40] or n.type
    if isinstance(d.get("meta"), dict):                    # 병합(메모는 스캔 노드에도 허용 — meta는 사람 영역)
        n.meta = {**n.meta, **d["meta"]}
        n.meta = {k: v for k, v in n.meta.items() if v not in ("", None)}
    if "x" in d:
        n.x = d["x"]
    if "y" in d:
        n.y = d["y"]
    n.save()
    return Response(_node(n))


@api_view(["POST"])
@permission_classes([AllowAny])
def edges(request, slug):
    if (g := _gate(request)):
        return g
    p = Project.objects.filter(slug=slug).first()
    if not p:
        return Response({"detail": "없는 프로젝트."}, status=404)
    s, t = request.data.get("s"), request.data.get("t")
    have = set(p.nodes.filter(nid__in=[s, t]).values_list("nid", flat=True))
    if s not in have or t not in have or s == t:
        return Response({"detail": "s·t가 이 프로젝트의 서로 다른 노드여야 해요."}, status=400)
    e = Edge.objects.create(project=p, s=s, t=t, origin="user",
                            label=(request.data.get("label") or "").strip()[:120],
                            both=bool(request.data.get("both")))
    return Response(_edge(e), status=201)


@api_view(["PATCH", "DELETE"])
@permission_classes([AllowAny])
def edge(request, slug, eid):
    if (g := _gate(request)):
        return g
    e = Edge.objects.filter(project__slug=slug, id=eid).first()
    if not e:
        return Response({"detail": "없는 관계."}, status=404)
    if request.method == "DELETE":
        e.delete()
        return Response({"deleted": eid})
    if e.origin == "scan":
        return Response({"detail": "스캔 관계는 편집 불가."}, status=400)
    if "label" in request.data:
        e.label = str(request.data["label"]).strip()[:120]
    if "both" in request.data:
        e.both = bool(request.data["both"])
    e.save()
    return Response(_edge(e))


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def views_(request, slug):
    if (g := _gate(request)):
        return g
    p = Project.objects.filter(slug=slug).first()
    if not p:
        return Response({"detail": "없는 프로젝트."}, status=404)
    if request.method == "GET":
        return Response({"views": [_view(v) for v in p.views.all()]})
    name = (request.data.get("name") or "").strip()
    if not name:
        return Response({"detail": "name이 필요해요."}, status=400)
    vid = slugify(request.data.get("vid") or name)
    if not vid:                                            # 한글 이름 등 — ASCII vid 자동 발급(URL 컨버터 제약)
        i = p.views.count() + 1
        while View.objects.filter(project=p, vid=f"v{i}").exists():
            i += 1
        vid = f"v{i}"
    if View.objects.filter(project=p, vid=vid).exists():
        return Response({"detail": f"vid 중복: {vid}"}, status=400)
    v = View.objects.create(project=p, vid=vid, name=name[:80],
                            types=[str(t)[:40] for t in (request.data.get("types") or [])][:12],
                            lanes=[str(l)[:40] for l in (request.data.get("lanes") or [])][:10],
                            order=int(request.data.get("order") or 1))
    return Response(_view(v), status=201)


@api_view(["PATCH", "DELETE"])
@permission_classes([AllowAny])
def view_(request, slug, vid):
    if (g := _gate(request)):
        return g
    v = View.objects.filter(project__slug=slug, vid=vid).first()
    if not v:
        return Response({"detail": "없는 뷰."}, status=404)
    if request.method == "DELETE":
        v.delete()
        return Response({"deleted": vid})
    d = request.data or {}
    if "name" in d:
        v.name = str(d["name"]).strip()[:80] or v.name
    if isinstance(d.get("types"), list):
        v.types = [str(t)[:40] for t in d["types"]][:12]
    if isinstance(d.get("lanes"), list):
        v.lanes = [str(l)[:40] for l in d["lanes"]][:10]
    if "order" in d:
        v.order = int(d["order"])
    v.save()
    return Response(_view(v))
