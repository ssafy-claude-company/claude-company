"""graph 플랫폼 대본 검증 — 원시 기능(Node·Edge·View) CRUD + 스캔 동기의 불변식."""
import os
import tempfile
from pathlib import Path

from django.test import TestCase
from rest_framework.test import APIClient

from .models import Project


def _c(tok="tok-dojin"):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Token {tok}")
    return c


class PlatformApiTest(TestCase):
    def setUp(self):
        os.environ["DEV_FEEDBACK_TOKENS"] = "tok-dojin:dojin"
        os.environ.pop("DEV_FEEDBACK_MURMUR", None)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "a.py").write_text('"""모듈 a."""\nimport b\n', encoding="utf-8")
        (root / "b.py").write_text('"""모듈 b."""\n', encoding="utf-8")
        (root / "web").mkdir()
        (root / "web" / "app.js").write_text("// 앱\nimport './util.js'\n", encoding="utf-8")
        (root / "web" / "util.js").write_text("// 유틸\n", encoding="utf-8")
        self.c = _c()
        r = self.c.post("/api/projects/", {"name": "샘플", "slug": "sample", "root": self.tmp.name}, format="json")
        assert r.status_code == 201, r.content

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DEV_FEEDBACK_TOKENS", None)

    def test_scan_sync_and_rescan_preserves_user_data(self):
        self.assertEqual(_c("bad").post("/api/projects/sample/scan/").status_code, 403)
        self.assertEqual(self.c.post("/api/projects/sample/scan/").status_code, 200)
        g = self.c.get("/api/projects/sample/graph/").json()
        ids = {n["nid"] for n in g["nodes"]}
        self.assertEqual(ids, {"f:a.py", "f:b.py", "f:web/app.js", "f:web/util.js"})
        self.assertTrue(any(e["s"] == "f:a.py" and e["t"] == "f:b.py" for e in g["edges"]))       # py
        self.assertTrue(any(e["s"] == "f:web/app.js" and e["t"] == "f:web/util.js" for e in g["edges"]))  # js
        self.assertEqual({v["vid"] for v in g["views"]}, {"concept", "files", "all"})            # 시드 뷰
        # 사람이 만든 것: 개념 노드 + 파일로의 관계 + 스캔 노드 메모
        n = self.c.post("/api/projects/sample/nodes/", {"type": "concept", "name": "코어"}, format="json").json()
        self.c.post("/api/projects/sample/edges/", {"s": n["nid"], "t": "f:a.py", "label": "담당"}, format="json")
        self.c.patch("/api/projects/sample/nodes/f:a.py/", {"meta": {"note": "핵심 파일"}}, format="json")
        # 파일 하나 삭제 후 재스캔 → scan 원산만 동기, 사람 것 보존
        (Path(self.tmp.name) / "b.py").unlink()
        st = self.c.post("/api/projects/sample/scan/").json()
        self.assertEqual(st["removed"], 1)
        g2 = self.c.get("/api/projects/sample/graph/").json()
        ids2 = {x["nid"] for x in g2["nodes"]}
        self.assertNotIn("f:b.py", ids2)
        self.assertIn(n["nid"], ids2)                                                            # 개념 생존
        fa = next(x for x in g2["nodes"] if x["nid"] == "f:a.py")
        self.assertEqual(fa["meta"].get("note"), "핵심 파일")                                     # 메모 생존
        self.assertTrue(any(e["label"] == "담당" for e in g2["edges"]))                           # 관계 생존

    def test_node_edge_view_crud_guards(self):
        self.c.post("/api/projects/sample/scan/")
        # 스캔 노드 삭제 금지·이름 불변, meta만 허용
        self.assertEqual(self.c.delete("/api/projects/sample/nodes/f:a.py/").status_code, 400)
        r = self.c.patch("/api/projects/sample/nodes/f:a.py/", {"name": "바꿔봄", "meta": {"note": "x"}}, format="json").json()
        self.assertEqual(r["name"], "a.py")
        # 사용자 노드 수정·삭제(관계 동반 삭제)
        n = self.c.post("/api/projects/sample/nodes/", {"name": "임시", "type": "decision"}, format="json").json()
        e = self.c.post("/api/projects/sample/edges/", {"s": n["nid"], "t": "f:a.py"}, format="json").json()
        self.assertEqual(self.c.patch(f"/api/projects/sample/nodes/{n['nid']}/", {"name": "결정1"}, format="json").json()["name"], "결정1")
        self.c.delete(f"/api/projects/sample/nodes/{n['nid']}/")
        g = self.c.get("/api/projects/sample/graph/").json()
        self.assertFalse(any(x["nid"] == n["nid"] for x in g["nodes"]))
        self.assertFalse(any(x["id"] == e["id"] for x in g["edges"]))
        # 잘못된 관계(없는 노드) 거부
        self.assertEqual(self.c.post("/api/projects/sample/edges/", {"s": "ghost", "t": "f:a.py"}, format="json").status_code, 400)
        # 뷰 CRUD — 레이어를 데이터로 만든다
        v = self.c.post("/api/projects/sample/views/", {"name": "결정만", "types": ["decision"], "lanes": ["제안", "확정"]}, format="json")
        self.assertEqual(v.status_code, 201, v.content)
        self.assertEqual(self.c.patch("/api/projects/sample/views/결정만/".replace("결정만", v.json()["vid"]) if False else f"/api/projects/sample/views/{v.json()['vid']}/",
                                      {"lanes": ["제안", "확정", "폐기"]}, format="json").json()["lanes"], ["제안", "확정", "폐기"])
        self.assertEqual(self.c.delete(f"/api/projects/sample/views/{v.json()['vid']}/").status_code, 200)


class CanvasApiTest(TestCase):
    """캔버스 — 사람과 AI가 같은 판에 그리는 계약."""

    def setUp(self):
        os.environ["DEV_FEEDBACK_TOKENS"] = "tok-dojin:dojin"
        os.environ.pop("DEV_FEEDBACK_MURMUR", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.c = _c()
        self.c.post("/api/projects/", {"name": "샘플", "slug": "sample", "root": self.tmp.name}, format="json")

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DEV_FEEDBACK_TOKENS", None)

    def test_canvas_flow(self):
        # 캔버스 생성(한글 이름 → ASCII cid 자동)
        cv = self.c.post("/api/projects/sample/canvases/", {"name": "설계 보드"}, format="json").json()
        cid = cv["cid"]
        # 사람 스티키 + AI 스티키 + 연결(라벨)
        a = self.c.post(f"/api/projects/sample/canvases/{cid}/items/", {"kind": "sticky", "text": "요청", "x": 0, "y": 0}, format="json").json()
        b = self.c.post(f"/api/projects/sample/canvases/{cid}/items/", {"kind": "box", "text": "AI 문서화", "origin": "ai", "x": 300, "y": 0}, format="json").json()
        self.assertEqual(b["origin"], "ai")
        l = self.c.post(f"/api/projects/sample/canvases/{cid}/links/", {"s": a["id"], "t": b["id"], "label": "흘러감"}, format="json")
        self.assertEqual(l.status_code, 201, l.content)
        # 이동·텍스트 수정(낙관 패치)
        self.assertEqual(self.c.patch(f"/api/projects/sample/canvases/{cid}/items/{a['id']}/", {"x": 50, "text": "요청 v2"}, format="json").json()["x"], 50)
        # objects 스냅샷
        o = self.c.get(f"/api/projects/sample/canvases/{cid}/objects/").json()
        self.assertEqual((len(o["items"]), len(o["links"])), (2, 1))
        # 삭제 캐스케이드: 객체 지우면 연결도
        self.c.delete(f"/api/projects/sample/canvases/{cid}/items/{a['id']}/")
        o2 = self.c.get(f"/api/projects/sample/canvases/{cid}/objects/").json()
        self.assertEqual((len(o2["items"]), len(o2["links"])), (1, 0))
        # 잘못된 kind·타 캔버스 연결 거부
        self.assertEqual(self.c.post(f"/api/projects/sample/canvases/{cid}/items/", {"kind": "circle"}, format="json").status_code, 400)
