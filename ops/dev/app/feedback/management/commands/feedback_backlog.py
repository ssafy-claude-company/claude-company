"""feedback_backlog — AI 세션이 피드백 백로그를 보는 1급 경로.

사용: manage.py feedback_backlog [--status open|resolved|confirmed|all] [--service murmur]
운영 루프: 사용자(admin)가 화면에 핀 코멘트 → 여기 open으로 쌓임 → AI 세션이 이 명령으로
읽고 처리 → feedback_resolve로 노트와 함께 resolved → 사용자가 백로그 화면에서 확인(confirmed).
"""
from django.core.management.base import BaseCommand

from feedback.models import FeedbackItem


class Command(BaseCommand):
    help = "피드백 백로그 출력(AI 처리용)."

    def add_arguments(self, parser):
        parser.add_argument("--status", default="open")
        parser.add_argument("--service", default="murmur")

    def handle(self, *args, **opts):
        qs = FeedbackItem.objects.filter(service=opts["service"]).order_by("created_at")
        if opts["status"] != "all":
            qs = qs.filter(status=opts["status"])
        if not qs.exists():
            self.stdout.write(f"({opts['status']} 피드백 없음)")
            return
        for i in qs:
            self.stdout.write(f"#{i.id} [{i.status}] {i.route}  요소: {i.element_label or i.selector[:40]}")
            if i.anchor_text:
                self.stdout.write(f"    텍스트: {i.anchor_text}")
            self.stdout.write(f"    {i.author_handle}: {i.body}")
            for c in i.comments.all():
                self.stdout.write(f"      ↳ {c.author_handle}: {c.body}")
            if i.resolution:
                self.stdout.write(f"    처리: {i.resolution} ({i.resolved_by})")
            self.stdout.write("")
