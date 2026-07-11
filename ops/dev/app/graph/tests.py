"""graph 앱 대본 검증 — 등록·스캔·그래프·논리층 왕복(임시 디렉터리, 정적 토큰 인증)."""
import os
import tempfile
from pathlib import Path

from django.test import TestCase
from rest_framework.test import APIClient


def _c(tok="tok-dojin"):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Token {tok}")
    return c


class GraphApiTest(TestCase):
    def setUp(self):
        os.environ["DEV_FEEDBACK_TOKENS"] = "tok-dojin:dojin"
        os.environ.pop("DEV_FEEDBACK_MURMUR", None)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "a.py").write_text('"""모듈 a — 시드."""\nimport b\n', encoding="utf-8")
        (root / "b.py").write_text('"""모듈 b."""\nX = 1\n', encoding="utf-8")
        (root / "web").mkdir()
        (root / "web" / "app.js").write_text("// 앱\nimport './util.js'\n", encoding="utf-8")
        (root / "web" / "util.js").write_text("// 유틸\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("DEV_FEEDBACK_TOKENS", None)

    def test_register_scan_graph_concepts(self):
        c = _c()
        # 게이트
        self.assertEqual(APIClient().get("/api/projects/").status_code, 403)
        # 등록(경로 검증 포함)
        bad = c.post("/api/projects/", {"name": "x", "root": "/없는/경로"}, format="json")
        self.assertEqual(bad.status_code, 400)
        r = c.post("/api/projects/", {"name": "샘플", "slug": "sample", "root": self.tmp.name}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        # 스캔 → 그래프
        self.assertEqual(c.post("/api/projects/sample/scan/").status_code, 200)
        g = c.get("/api/projects/sample/graph/").json()
        ids = {n["id"] for n in g["nodes"]}
        self.assertEqual(ids, {"a.py", "b.py", "web/app.js", "web/util.js"})
        self.assertIn({"s": "a.py", "t": "b.py"}, g["edges"])                 # py 해석
        self.assertIn({"s": "web/app.js", "t": "web/util.js"}, g["edges"])    # js 상대 해석
        self.assertEqual(g["nodes"][0]["doc"], "모듈 a — 시드.")
        # 논리층 PUT/GET 왕복 + 검증
        doc = {"stages": ["층A", "층B"],
               "nodes": [{"id": "core", "name": "코어", "one": "설명", "src": "구술", "stage": 0,
                          "globs": ["a.py"], "x": 10, "y": 20},
                         {"id": "web", "name": "웹", "stage": 1, "globs": ["web/"]}],
               "edges": [{"s": "core", "t": "web", "label": "제공", "both": False}]}
        self.assertEqual(c.put("/api/projects/sample/concepts/", doc, format="json").status_code, 200)
        got = c.get("/api/projects/sample/concepts/").json()
        self.assertEqual(len(got["nodes"]), 2)
        self.assertEqual(got["nodes"][0]["x"], 10)
        self.assertEqual(got["edges"][0]["label"], "제공")
        # 무결성: 없는 개념을 가리키는 관계 거부
        bad2 = c.put("/api/projects/sample/concepts/",
                     {"nodes": [{"id": "solo"}], "edges": [{"s": "solo", "t": "ghost"}]}, format="json")
        self.assertEqual(bad2.status_code, 400)
        # 목록에 요약
        lst = c.get("/api/projects/").json()["projects"]
        self.assertEqual(lst[0]["slug"], "sample")
        self.assertEqual(lst[0]["last_scan"]["files"], 4)
