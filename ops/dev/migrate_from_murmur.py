#!/usr/bin/env python3
"""murmur postgres → dev sqlite로 피드백 데이터 1회 이사(멱등 — id 존재 시 skip).

사용(라이브 이사):
  MURMUR_DB=$(cat /root/ClaudeCompany/.dburl) /root/ClaudeCompany/.venv/bin/python ops/dev/migrate_from_murmur.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

import psycopg  # noqa: E402  (.venv에 murmur 백엔드 의존으로 이미 설치)

from feedback.models import FeedbackComment, FeedbackItem  # noqa: E402

SRC = os.environ.get("MURMUR_DB", "")
if not SRC:
    sys.exit("MURMUR_DB(=murmur DATABASE_URL) 필요")

ITEM_COLS = ["id", "service", "route", "selector", "element_label", "anchor_text", "pos_x", "pos_y",
             "body", "author_handle", "status", "resolution", "resolved_by",
             "created_at", "updated_at", "resolved_at", "confirmed_at"]
CMT_COLS = ["id", "item_id", "author_handle", "body", "created_at"]

with psycopg.connect(SRC) as conn, conn.cursor() as cur:
    cur.execute(f"SELECT {', '.join(ITEM_COLS)} FROM feedback_feedbackitem ORDER BY id")
    items = cur.fetchall()
    cur.execute(f"SELECT {', '.join(CMT_COLS)} FROM feedback_feedbackcomment ORDER BY id")
    comments = cur.fetchall()

ni = nc = 0
for row in items:
    d = dict(zip(ITEM_COLS, row))
    if FeedbackItem.objects.filter(id=d["id"]).exists():
        continue
    FeedbackItem.objects.create(**d)
    ni += 1
for row in comments:
    d = dict(zip(CMT_COLS, row))
    if FeedbackComment.objects.filter(id=d["id"]).exists():
        continue
    FeedbackComment.objects.create(**d)
    nc += 1
print(f"이사 완료: 항목 +{ni}(총 {FeedbackItem.objects.count()}) · 댓글 +{nc}(총 {FeedbackComment.objects.count()})")
