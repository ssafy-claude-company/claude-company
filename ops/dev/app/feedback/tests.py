"""feedback 앱 테스트 — admin 게이트·핀 생성·상호 가시성·상태 기계·AI 처리 경로.

[이식성 — 적대검증 2026-07-06] sns를 최상위 import하지 않는다(수집 단계에서 ModuleNotFoundError로
죽으면 sns 없는 다른 서비스에 이식 시 `test feedback`이 즉사). 호스트(sns) 미설치면 통합 테스트는
skip으로 퇴화 — auth.py 어댑터가 유일 접점이라는 이식 계약과 정합. Person은 지연 해석(apps.get_model).
"""
import unittest

from django.apps import apps
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from .models import FeedbackItem

_HAS_SNS = apps.is_installed("sns")


def _Person():
    return apps.get_model("sns", "Person")


def _c(tok):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Token {tok}")
    return c


@unittest.skipUnless(_HAS_SNS, "murmur 호스트(sns) 통합 — 이식 시 skip")
class FeedbackApiTest(TestCase):
    def setUp(self):
        Person = _Person()
        Person.objects.create(handle="dojin", name="도진", token="tok_dj", is_admin=True)
        Person.objects.create(handle="hyunjun", name="현준", token="tok_hj", is_admin=True)
        Person.objects.create(handle="pleb", name="일반", token="tok_pl")

    def _pin(self, tok="tok_dj", **kw):
        d = {"route": "/channels/U-001", "selector": "#send-btn", "element_label": "버튼 '보내기'",
             "anchor_text": "보내기", "pos_x": 40, "pos_y": 60, "body": "버튼이 너무 작아요"}
        d.update(kw)
        return _c(tok).post("/api/feedback/", d, format="json")

    def test_비admin은_전부_403(self):
        self.assertEqual(_c("tok_pl").get("/api/feedback/").status_code, 403)
        self.assertEqual(self._pin("tok_pl").status_code, 403)
        self.assertEqual(_c("tok_pl").get("/api/feedback/summary/").status_code, 403)
        self.assertEqual(APIClient().get("/api/feedback/").status_code, 403)   # 무인증

    def test_핀_생성과_상호_가시성(self):
        r = self._pin("tok_dj")
        self.assertEqual(r.status_code, 201)
        iid = r.json()["id"]
        # 현준이 도진의 핀을 보고 댓글
        lst = _c("tok_hj").get("/api/feedback/?route=/channels/U-001").json()["items"]
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0]["author"], "dojin")
        rc = _c("tok_hj").post(f"/api/feedback/{iid}/comments/", {"body": "동의 — 모바일에선 더 작음"}, format="json")
        self.assertEqual(rc.status_code, 201)
        # 도진이 스레드에서 현준 댓글 확인
        detail = _c("tok_dj").get(f"/api/feedback/{iid}/").json()
        self.assertEqual(detail["comments"][0]["author"], "hyunjun")

    def test_상태기계_처리_반려_보류_완료_재오픈(self):
        iid = self._pin().json()["id"]
        # AI 처리 경로(관리명령) → resolved + 노트
        call_command("feedback_resolve", iid, note="버튼 44px로 확대(커밋 abc123)", by="Claude 세션")
        i = FeedbackItem.objects.get(id=iid)
        self.assertEqual(i.status, "resolved")
        self.assertIn("44px", i.resolution)
        # 사용자 완료(close) — 어느 상태서든
        r = _c("tok_dj").patch(f"/api/feedback/{iid}/", {"status": "closed"}, format="json")
        self.assertEqual(r.json()["status"], "closed")
        self.assertEqual(r.json()["status_label"], "완료")
        # 재오픈
        r = _c("tok_hj").patch(f"/api/feedback/{iid}/", {"status": "open"}, format="json")
        self.assertEqual(r.json()["status"], "open")

    def test_반려_보류_상태(self):
        i1 = self._pin(body="반려대상").json()["id"]
        i2 = self._pin(body="보류대상").json()["id"]
        call_command("feedback_resolve", i1, "--kind", "reject", note="라벨 충돌로 불가")
        call_command("feedback_resolve", i2, "--kind", "defer", note="방향 확정 후")
        self.assertEqual(FeedbackItem.objects.get(id=i1).status, "rejected")
        self.assertEqual(FeedbackItem.objects.get(id=i2).status, "deferred")
        s = _c("tok_dj").get("/api/feedback/summary/").json()
        self.assertEqual((s["rejected"], s["deferred"]), (1, 1))
        # 상태별 필터가 5상태 다 걸린다(옛 화이트리스트 버그 회귀 — deferred/rejected/closed가 무시되던 것)
        self.assertEqual(len(_c("tok_dj").get("/api/feedback/?status=rejected").json()["items"]), 1)
        self.assertEqual(len(_c("tok_dj").get("/api/feedback/?status=deferred").json()["items"]), 1)
        self.assertEqual(len(_c("tok_dj").get("/api/feedback/?status=closed").json()["items"]), 0)  # 아직 close 안 함
        # 반려도 close 가능 → closed 필터에 잡힘
        self.assertEqual(_c("tok_dj").patch(f"/api/feedback/{i1}/", {"status": "closed"}, format="json").json()["status"], "closed")
        self.assertEqual(len(_c("tok_dj").get("/api/feedback/?status=closed").json()["items"]), 1)

    def test_댓글_삭제(self):
        iid = self._pin().json()["id"]
        cid = _c("tok_dj").post(f"/api/feedback/{iid}/comments/", {"body": "지울 댓글"}, format="json").json()["id"]
        self.assertEqual(_c("tok_hj").delete(f"/api/feedback/{iid}/comments/{cid}/").status_code, 200)  # admin 누구나
        self.assertEqual(len(_c("tok_dj").get(f"/api/feedback/{iid}/").json()["comments"]), 0)
        self.assertEqual(_c("tok_dj").delete(f"/api/feedback/{iid}/comments/99999/").status_code, 404)

    def test_summary와_필터(self):
        self._pin(body="1")
        iid = self._pin(body="2", route="/agents").json()["id"]
        call_command("feedback_resolve", iid, note="done")
        s = _c("tok_dj").get("/api/feedback/summary/").json()
        self.assertEqual((s["open"], s["resolved"]), (1, 1))
        self.assertEqual(len(_c("tok_dj").get("/api/feedback/?status=open").json()["items"]), 1)
        self.assertEqual(len(_c("tok_dj").get("/api/feedback/?route=/agents").json()["items"]), 1)

    def test_삭제와_빈본문(self):
        self.assertEqual(self._pin(body="").status_code, 400)
        iid = self._pin().json()["id"]
        self.assertEqual(_c("tok_hj").delete(f"/api/feedback/{iid}/").status_code, 200)  # admin 누구나 정리
        self.assertFalse(FeedbackItem.objects.filter(id=iid).exists())

    def test_service_격리(self):
        self._pin(service="othersvc")
        self.assertEqual(len(_c("tok_dj").get("/api/feedback/").json()["items"]), 0)     # 기본 murmur
        self.assertEqual(len(_c("tok_dj").get("/api/feedback/?service=othersvc").json()["items"]), 1)

    def test_비유한좌표_클램프_목록마비_방지(self):
        # [적대검증 P1] inf/nan/범위밖 좌표가 통과하면 목록·상세 GET 전체가 500. 클램프 확인.
        for bad in ("1e999", "Infinity", "-40", "250"):
            r = self._pin(pos_x=bad, pos_y=bad, body=f"b{bad}")
            self.assertEqual(r.status_code, 201)
            self.assertTrue(0 <= r.json()["pos_x"] <= 100 and 0 <= r.json()["pos_y"] <= 100)
        # 오염 없어 목록·상세·필터 GET 전부 200(500 마비 없음)
        self.assertEqual(_c("tok_dj").get("/api/feedback/").status_code, 200)
        self.assertEqual(_c("tok_dj").get("/api/feedback/?status=open").status_code, 200)

    def test_상태역전이_confirmed_at_리셋(self):
        # [적대검증 P2] confirmed→resolved 재진입 시 confirmed_at 잔존 모순 방지
        iid = self._pin().json()["id"]
        call_command("feedback_resolve", iid, note="fix")
        _c("tok_dj").patch(f"/api/feedback/{iid}/", {"status": "confirmed"}, format="json")
        self.assertIsNotNone(FeedbackItem.objects.get(id=iid).confirmed_at)
        call_command("feedback_resolve", iid, note="refix")          # 재처리 → 확인 대기로
        i = FeedbackItem.objects.get(id=iid)
        self.assertEqual(i.status, "resolved")
        self.assertIsNone(i.confirmed_at)                            # 확인 흔적 제거됨
        # API 경로도 동일
        _c("tok_dj").patch(f"/api/feedback/{iid}/", {"status": "confirmed"}, format="json")
        _c("tok_dj").patch(f"/api/feedback/{iid}/", {"status": "resolved"}, format="json")
        self.assertIsNone(FeedbackItem.objects.get(id=iid).confirmed_at)
