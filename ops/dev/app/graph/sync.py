"""스캔 → 플랫폼 동기 — 스캔은 원시 기능(Node·Edge)의 소비자다.

파일=Node(origin=scan, nid='f:<경로>'), import=Edge(origin=scan, label='import').
동기 규칙: scan-원산만 upsert/삭제. 사람이 만든 노드·관계·메모(meta)·좌표는 불변 —
재스캔은 payload(스캔 소유 필드)만 갱신한다. nid가 경로 기반이라 핀·관계가 재스캔에도 산다.
"""
from .models import Edge, Node


def sync_scan(project, data):
    seen = set()
    created = updated = 0
    existing = {n.nid: n for n in project.nodes.filter(origin="scan")}
    for f in data["nodes"]:
        nid = "f:" + f["id"]
        seen.add(nid)
        payload = {"loc": f["loc"], "area": f["area"], "kind": f["kind"], "doc": f["doc"], "dir": f["dir"]}
        n = existing.get(nid)
        if n:
            if n.payload != payload or n.name != f["label"]:
                n.payload = payload
                n.name = f["label"]
                n.save(update_fields=["payload", "name", "updated_at"])
                updated += 1
        else:
            Node.objects.create(project=project, nid=nid, type="file", origin="scan",
                                name=f["label"], payload=payload)
            created += 1
    gone = [nid for nid in existing if nid not in seen]
    if gone:
        Node.objects.filter(project=project, nid__in=gone).delete()
        Edge.objects.filter(project=project, s__in=gone).delete()
        Edge.objects.filter(project=project, t__in=gone).delete()

    # 관계 동기(집합 대조 — scan 원산만)
    want = {("f:" + e["s"], "f:" + e["t"]) for e in data["edges"]}
    have = {(e.s, e.t): e.id for e in project.edges.filter(origin="scan")}
    add = [Edge(project=project, s=s, t=t, label="import", origin="scan")
           for (s, t) in want if (s, t) not in have]
    Edge.objects.bulk_create(add)
    drop = [eid for (st, eid) in have.items() if st not in want]
    if drop:
        Edge.objects.filter(id__in=drop).delete()

    return {"files": len(data["nodes"]), "edges": len(data["edges"]),
            "created": created, "updated": updated, "removed": len(gone),
            "secs": data["meta"]["secs"], "truncated": data["meta"]["truncated"]}
