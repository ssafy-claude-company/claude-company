"""피드백 API — 전 엔드포인트 admin 전용(어댑터 resolve_admin이 판정).

계약(프론트·타 서비스 공용):
  GET    /api/feedback/?status=&route=&service=   목록
  POST   /api/feedback/                            핀 생성 {route, selector, element_label,
                                                   anchor_text, pos_x, pos_y, body}
  GET    /api/feedback/<id>/                       상세(+comments)
  PATCH  /api/feedback/<id>/                       상태 전이 {status: confirmed|open}
                                                   (+선택 {resolution, resolved_by, status:resolved})
  DELETE /api/feedback/<id>/                       삭제(admin 누구나 — 오타 핀 정리)
  POST   /api/feedback/<id>/comments/              댓글 {body}
  GET    /api/feedback/summary/                    {open,resolved,confirmed}

AI 처리 경로는 관리명령(feedback_backlog/feedback_resolve)이 1급 — API PATCH(resolved)도 허용해
어느 쪽이든 같은 상태 기계를 지난다.
"""
import math

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .auth import resolve_admin
from .models import FeedbackItem, FeedbackComment

_DENY = {"detail": "관리자만 쓸 수 있어요."}


def _item(i, with_comments=False):
    d = {
        "id": i.id, "service": i.service, "route": i.route, "selector": i.selector,
        "element_label": i.element_label, "anchor_text": i.anchor_text,
        "pos_x": i.pos_x, "pos_y": i.pos_y, "body": i.body,
        "author": i.author_handle, "status": i.status,
        "status_label": dict(FeedbackItem._meta.get_field("status").choices).get(i.status, i.status),
        "resolution": i.resolution, "resolved_by": i.resolved_by,
        "comments_count": i.comments.count(),
        "created_at": i.created_at.timestamp(),
        "resolved_at": i.resolved_at.timestamp() if i.resolved_at else None,
        "confirmed_at": i.confirmed_at.timestamp() if i.confirmed_at else None,
    }
    if with_comments:
        d["comments"] = [{"id": c.id, "author": c.author_handle, "body": c.body,
                          "created_at": c.created_at.timestamp()} for c in i.comments.all()]
    return d


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def items(request):
    handle = resolve_admin(request)
    if not handle:
        return Response(_DENY, status=403)
    if request.method == "GET":
        # [dev 호스트] service 미지정 = murmur(기존 클라 호환) · 'all' = 전체(백로그가 서비스 통합 뷰).
        svc = request.query_params.get("service") or "murmur"
        qs = FeedbackItem.objects.all() if svc == "all" else FeedbackItem.objects.filter(service=svc)
        st = request.query_params.get("status")
        if st in ("open", "resolved", "rejected", "deferred", "closed"):
            qs = qs.filter(status=st)
        route = request.query_params.get("route")
        if route:
            qs = qs.filter(route=route)
        return Response({"items": [_item(i) for i in qs[:300]]})
    body = (request.data.get("body") or "").strip()
    if not body:
        return Response({"detail": "피드백 내용을 적어주세요."}, status=400)
    def _f(k, default=50.0):
        # [적대검증 P1 — 2026-07-06] inf/nan 좌표(예 pos_x:"1e999"·"Infinity")가 통과하면 행은
        # 커밋되나 이후 목록·상세 GET의 JSON 렌더가 'Out of range float' 500으로 마비된다(오염 행
        # 하나가 공유 목록 전체를 죽이는 DoS). 비유한값은 기본으로, 상대좌표(%) 의미론대로 0~100 클램프.
        try:
            v = float(request.data.get(k, default))
        except (TypeError, ValueError):
            return default
        if not math.isfinite(v):
            return default
        return min(100.0, max(0.0, v))
    i = FeedbackItem.objects.create(
        service=(request.data.get("service") or "murmur")[:30],
        route=(request.data.get("route") or "/")[:300],
        selector=(request.data.get("selector") or "")[:2000],
        element_label=(request.data.get("element_label") or "")[:120],
        anchor_text=(request.data.get("anchor_text") or "")[:160],
        pos_x=_f("pos_x"), pos_y=_f("pos_y"),
        body=body[:4000], author_handle=handle)
    return Response(_item(i), status=201)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([AllowAny])
def item(request, item_id):
    handle = resolve_admin(request)
    if not handle:
        return Response(_DENY, status=403)
    i = FeedbackItem.objects.filter(id=item_id).first()
    if not i:
        return Response({"detail": "피드백을 찾을 수 없어요."}, status=404)
    if request.method == "GET":
        return Response(_item(i, with_comments=True))
    if request.method == "DELETE":
        iid = i.id
        i.delete()
        return Response({"deleted": iid})
    # [상태 전이 — 라운드2] 대기 → 처리/반려/보류(AI 판단+노트) → 완료(사용자 close). 어디서든
    # 재오픈(open) 가능. close는 어느 상태에서든(사용자가 수락·마감). resolution/resolved_by는
    # 처리·반려·보류 공통 노트(왜 그렇게 판단했나).
    st = request.data.get("status")
    if st in ("resolved", "rejected", "deferred"):     # AI/사용자 판단 회신
        i.status = st
        i.resolved_at, i.confirmed_at = timezone.now(), None
        if request.data.get("resolution") is not None:
            i.resolution = str(request.data["resolution"])[:4000]
        if request.data.get("resolved_by") is not None:
            i.resolved_by = str(request.data["resolved_by"])[:60]
    elif st == "closed":                               # 사용자가 닫음(수락·마감) — 어느 상태서든
        i.status, i.confirmed_at = "closed", timezone.now()
    elif st == "open":                                 # 재오픈(재작업 요청) — 어느 상태서든
        i.status, i.resolved_at, i.confirmed_at = "open", None, None
    elif st == "confirmed":                            # 하위호환(옛 프론트) → closed로
        i.status, i.confirmed_at = "closed", timezone.now()
    elif st is not None:
        return Response({"detail": f"지원하지 않는 상태예요: {st}"}, status=400)
    i.save()
    return Response(_item(i))


@api_view(["POST"])
@permission_classes([AllowAny])
def comments(request, item_id):
    handle = resolve_admin(request)
    if not handle:
        return Response(_DENY, status=403)
    i = FeedbackItem.objects.filter(id=item_id).first()
    if not i:
        return Response({"detail": "피드백을 찾을 수 없어요."}, status=404)
    body = (request.data.get("body") or "").strip()
    if not body:
        return Response({"detail": "댓글 내용을 적어주세요."}, status=400)
    c = FeedbackComment.objects.create(item=i, author_handle=handle, body=body[:2000])
    return Response({"id": c.id, "author": c.author_handle, "body": c.body,
                     "created_at": c.created_at.timestamp()}, status=201)


@api_view(["DELETE"])
@permission_classes([AllowAny])
def comment(request, item_id, comment_id):
    """[피드백 #12 라운드2] 댓글 삭제 — admin 누구나(오타/취소 정리). 종전엔 답글 삭제가 없었음."""
    handle = resolve_admin(request)
    if not handle:
        return Response(_DENY, status=403)
    c = FeedbackComment.objects.filter(id=comment_id, item_id=item_id).first()
    if not c:
        return Response({"detail": "댓글을 찾을 수 없어요."}, status=404)
    c.delete()
    return Response({"deleted": comment_id})


@api_view(["GET"])
@permission_classes([AllowAny])
def summary(request):
    handle = resolve_admin(request)
    if not handle:
        return Response(_DENY, status=403)
    qs = FeedbackItem.objects.filter(service=request.query_params.get("service") or "murmur")
    out = {"open": 0, "resolved": 0, "rejected": 0, "deferred": 0, "closed": 0}
    for row in qs.values("status"):
        if row["status"] in out:
            out[row["status"]] += 1
    return Response(out)
