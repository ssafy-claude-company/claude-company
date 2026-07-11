"""feedback_resolve — AI가 피드백을 판단하고 노트를 남기는 1급 경로.

사용:
  manage.py feedback_resolve <id> --note "고친 내용(커밋)"             # 처리(resolved, 기본)
  manage.py feedback_resolve <id> --kind reject --note "안 하는 이유"   # 반려(rejected)
  manage.py feedback_resolve <id> --kind defer  --note "나중 이유"      # 보류(deferred)
  [--by "Claude 세션"]

처리/반려/보류 모두 사용자 백로그의 각 탭에 떠서 [완료]/[재오픈] 대상이 된다.
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from feedback.models import FeedbackItem

_KIND = {"resolve": "resolved", "reject": "rejected", "defer": "deferred"}


class Command(BaseCommand):
    help = "피드백을 처리/반려/보류로 전이 + 노트 기록."

    def add_arguments(self, parser):
        parser.add_argument("item_id", type=int)
        parser.add_argument("--note", required=True, help="판단 내용(고친 것·안 하는 이유·나중 이유)")
        parser.add_argument("--kind", default="resolve", choices=list(_KIND),
                            help="resolve(처리, 기본)|reject(반려)|defer(보류)")
        parser.add_argument("--by", default="Claude 세션", help="처리 주체 표기")

    def handle(self, *args, **opts):
        i = FeedbackItem.objects.filter(id=opts["item_id"]).first()
        if not i:
            raise CommandError(f"피드백 #{opts['item_id']} 없음")
        i.status = _KIND[opts["kind"]]
        i.resolution, i.resolved_by = opts["note"][:4000], opts["by"][:60]
        i.resolved_at, i.confirmed_at = timezone.now(), None
        i.save()
        label = {"resolved": "처리", "rejected": "반려", "deferred": "보류"}[i.status]
        self.stdout.write(self.style.SUCCESS(
            f"#{i.id} {label} — {i.route} {i.element_label}: {opts['note'][:60]}"))
