"""메타 서비스 API — 프로젝트 등록·스캔·그래프·논리층. 전부 admin(피드백과 같은 게이트).

GET  /api/projects/                     목록(+최근 스캔 요약·개념 수)
POST /api/projects/                     등록 {slug, name, root, skips?, stages?}
GET  /api/projects/<slug>/              상세
DELETE /api/projects/<slug>/            제거(스캔·개념 동반 삭제 — 피드백은 남음)
POST /api/projects/<slug>/scan/         스캔 실행 → 요약
GET  /api/projects/<slug>/graph/        최신 스캔 그래프 {nodes, edges, meta}
GET  /api/projects/<slug>/concepts/     논리층 {stages, nodes, edges}
PUT  /api/projects/<slug>/concepts/     논리층 전체 교체(UI 편집의 저장 계약)
"""
import os
import re

from django.utils.text import slugify
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from feedback.auth import resolve_admin

from .models import Concept, ConceptEdge, Project, ScanRun
from .scanner import scan

_DENY = {"detail": "관리자만 쓸 수 있어요."}
DEFAULT_STAGES = ["사람", "창 (표면)", "투영·전송", "세계 (법칙)", "존재"]


def _proj(p):
    last = p.scans.first()
    return {
        "slug": p.slug, "name": p.name, "root": p.root, "skips": p.skips,
        "stages": p.stages or DEFAULT_STAGES,
        "concepts": p.concepts.count(),
        "last_scan": ({"at": last.created_at.strftime("%Y-%m-%d %H:%M"), "ok": last.ok, **last.stats}
                      if last else None),
    }


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def projects(request):
    if not resolve_admin(request):
        return Response(_DENY, status=403)
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
    p = Project.objects.create(slug=slug, name=name, root=root,
                               skips=request.data.get("skips") or [],
                               stages=request.data.get("stages") or [])
    return Response(_proj(p), status=201)


@api_view(["GET", "DELETE"])
@permission_classes([AllowAny])
def project(request, slug):
    if not resolve_admin(request):
        return Response(_DENY, status=403)
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
    if not resolve_admin(request):
        return Response(_DENY, status=403)
    p = Project.objects.filter(slug=slug).first()
    if not p:
        return Response({"detail": "없는 프로젝트."}, status=404)
    try:
        data = scan(p.root, p.skips)
        run = ScanRun.objects.create(project=p, ok=True, data=data, stats={
            "files": data["meta"]["counts"]["nodes"], "edges": data["meta"]["counts"]["edges"],
            "secs": data["meta"]["secs"], "truncated": data["meta"]["truncated"],
        })
    except Exception as e:  # 스캔 실패도 이력으로 남긴다
        run = ScanRun.objects.create(project=p, ok=False, error=str(e)[:2000])
        return Response({"detail": f"스캔 실패: {e}"}, status=500)
    return Response({"ok": True, **run.stats})


@api_view(["GET"])
@permission_classes([AllowAny])
def project_graph(request, slug):
    if not resolve_admin(request):
        return Response(_DENY, status=403)
    p = Project.objects.filter(slug=slug).first()
    if not p:
        return Response({"detail": "없는 프로젝트."}, status=404)
    run = p.scans.filter(ok=True).first()
    if not run:
        return Response({"detail": "스캔이 아직 없어요 — 먼저 스캔을 실행하세요."}, status=404)
    return Response(run.data)


_CID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@api_view(["GET", "PUT"])
@permission_classes([AllowAny])
def project_concepts(request, slug):
    if not resolve_admin(request):
        return Response(_DENY, status=403)
    p = Project.objects.filter(slug=slug).first()
    if not p:
        return Response({"detail": "없는 프로젝트."}, status=404)
    if request.method == "GET":
        return Response({
            "stages": p.stages or DEFAULT_STAGES,
            "nodes": [{"id": c.cid, "name": c.name, "one": c.one, "src": c.src, "stage": c.stage,
                       "order": c.order, "globs": c.globs, "x": c.x, "y": c.y} for c in p.concepts.all()],
            "edges": [{"s": e.s, "t": e.t, "label": e.label, "both": e.both} for e in p.concept_edges.all()],
        })
    body = request.data or {}
    nodes = body.get("nodes") or []
    edges = body.get("edges") or []
    ids = set()
    for n in nodes:
        cid = str(n.get("id") or "").strip()
        if not _CID_RE.match(cid):
            return Response({"detail": f"개념 id 형식 오류: {cid!r} (소문자·숫자·하이픈)"}, status=400)
        ids.add(cid)
    for e in edges:
        if e.get("s") not in ids or e.get("t") not in ids:
            return Response({"detail": f"관계가 없는 개념을 가리켜요: {e.get('s')}→{e.get('t')}"}, status=400)
    # 전체 교체(단순한 저장 계약 — UI가 문서를 편집해 통째로 저장)
    p.concepts.all().delete()
    p.concept_edges.all().delete()
    for n in nodes:
        Concept.objects.create(project=p, cid=n["id"], name=n.get("name") or n["id"],
                               one=n.get("one") or "", src=n.get("src") or "",
                               stage=int(n.get("stage") or 0), order=int(n.get("order") or 0),
                               globs=n.get("globs") or [], x=n.get("x"), y=n.get("y"))
    for e in edges:
        ConceptEdge.objects.create(project=p, s=e["s"], t=e["t"],
                                   label=e.get("label") or "", both=bool(e.get("both")))
    if isinstance(body.get("stages"), list) and body["stages"]:
        p.stages = [str(s)[:40] for s in body["stages"]][:8]
        p.save(update_fields=["stages"])
    return Response({"ok": True, "nodes": len(nodes), "edges": len(edges)})
