"""dev 호스트 대본 검증 — 정적 토큰 어댑터 + API 왕복(생성·목록·상태기계·댓글).

murmur 통합 테스트(tests.py)는 sns 미설치라 skip되는 게 정상. 이 파일이 dev 서비스의
게이트다: 이식 계약(auth 어댑터·service 격리)이 이 호스트에서 실제로 동작하는가.
실행: DEV_FEEDBACK_TOKENS 없이도 setUp에서 주입한다.
"""
import os
import unittest

from django.test import TestCase
from rest_framework.test import APIClient

from .models import FeedbackItem


def _c(tok=None):
    c = APIClient()
    if tok:
        c.credentials(HTTP_AUTHORIZATION=f"Token {tok}")
    return c


class DevAuthApiTest(TestCase):
    def setUp(self):
        os.environ["DEV_FEEDBACK_TOKENS"] = "tok-dojin:dojin,tok-hj:hj"
        os.environ.pop("DEV_FEEDBACK_MURMUR", None)   # 위임 경로 차단 — 정적 토큰만 검증

    def tearDown(self):
        os.environ.pop("DEV_FEEDBACK_TOKENS", None)

    def _pin(self, c, **kw):
        body = {"route": "/dev/codegraph/", "selector": "#cg-system-sys-core-py",
                "element_label": "파일: system/sys_core.py", "anchor_text": "system/sys_core.py",
                "pos_x": 50, "pos_y": 40, "body": "테스트 핀", "service": "codegraph"}
        body.update(kw)
        return c.post("/api/feedback/", body, format="json")

    def test_gate_and_roundtrip(self):
        # 미인증·엉뚱 토큰 = 403
        self.assertEqual(_c().get("/api/feedback/").status_code, 403)
        self.assertEqual(_c("wrong").get("/api/feedback/").status_code, 403)
        # 정적 토큰 = 생성·목록·작성자 귀속
        c = _c("tok-dojin")
        r = self._pin(c)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["author"], "dojin")
        items = c.get("/api/feedback/?service=codegraph").json()["items"]
        self.assertEqual(len(items), 1)
        # 다른 admin도 서로의 핀을 본다(멀티 admin 계약)
        self.assertEqual(len(_c("tok-hj").get("/api/feedback/?service=all").json()["items"]), 1)

    def test_service_isolation_and_states(self):
        c = _c("tok-dojin")
        self._pin(c)
        self._pin(c, service="murmur", route="/channels/U-004")
        self.assertEqual(len(c.get("/api/feedback/?service=codegraph").json()["items"]), 1)
        self.assertEqual(len(c.get("/api/feedback/?service=murmur").json()["items"]), 1)
        self.assertEqual(len(c.get("/api/feedback/?service=all").json()["items"]), 2)
        # 상태 기계(라운드2): open → resolved(노트) → closed(완료) → open(재오픈)
        fid = FeedbackItem.objects.filter(service="codegraph").first().id
        ok = c.patch(f"/api/feedback/{fid}/", {"status": "resolved", "resolution": "고침", "resolved_by": "테스트"}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(c.patch(f"/api/feedback/{fid}/", {"status": "closed"}, format="json").json()["status"], "closed")
        self.assertEqual(c.patch(f"/api/feedback/{fid}/", {"status": "open"}, format="json").json()["status"], "open")
        # 댓글
        r = c.post(f"/api/feedback/{fid}/comments/", {"body": "메모"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)


if __name__ == "__main__":
    unittest.main()
