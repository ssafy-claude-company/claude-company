"""피드백 어노테이션 서비스 — 화면 요소에 핀 코멘트를 달고(open) AI가 처리하고(resolved)
사용자가 확인하는(confirmed) 루프. Figma/디자인 툴의 컴포넌트 코멘트를 서비스 UI에 가져온 것.

[이식성 설계 — 2026-07-06] 이 앱은 murmur에 *확장*으로 얹히지만 다른 서비스로 이식 가능해야
한다(사용자 요구). 그래서:
  · 호스트 서비스 모델에 FK를 걸지 않는다 — 작성자는 `author_handle` 문자열. 인증은
    `feedback/auth.py`의 어댑터 1함수(resolve_admin)로만 접촉 → 이식 = 어댑터+URL include 교체.
  · `service` 슬러그로 한 백엔드가 여러 서비스의 피드백을 수용(멀티테넌트).
  · 앵커 계약은 서비스 불문 동일: route + CSS selector + 요소 텍스트 스냅샷 + 상대좌표(%).
    UI가 바뀌어 셀렉터가 죽어도 anchor_text로 사람이 식별 가능(위치 소실 목록).
"""
from django.db import models

# [상태 세분화 — 2026-07-06 라운드2] 종전 open/resolved/confirmed 3단계는 반려·보류를 resolved에
# 뭉뚱그렸다. 이제 처리 결과를 5상태로 명시: 대기 → (처리|반려|보류) → 완료(사용자 close).
STATUS = [
    ("open", "대기"),         # 새 피드백 — 아직 판단 전
    ("resolved", "처리"),     # 고쳤음(사용자 확인 대기)
    ("rejected", "반려"),     # 안 함 — 사유
    ("deferred", "보류"),     # 나중 — 사유(방향 필요/대형)
    ("closed", "완료"),       # 사용자가 닫음(수락)
]
_OPEN_STATES = {"open"}       # 판단 전
_ACTED_STATES = {"resolved", "rejected", "deferred"}   # AI가 판단해 회신


class FeedbackItem(models.Model):
    """요소 하나에 달린 피드백 핀. 대기 → 처리/반려/보류(AI 판단+노트) → 완료(사용자 close)."""
    service = models.CharField(max_length=30, default="murmur", db_index=True,
                               help_text="피드백이 달린 서비스 슬러그(멀티서비스 확장)")
    route = models.CharField(max_length=300, help_text="페이지 경로(/channels/U-001)")
    selector = models.TextField(blank=True, help_text="핀 부착 CSS 선택자(프론트가 생성·복원)")
    element_label = models.CharField(max_length=120, blank=True, help_text="요소 설명(버튼 '보내기' 등)")
    anchor_text = models.CharField(max_length=160, blank=True,
                                   help_text="요소 텍스트 스냅샷 — 셀렉터가 깨져도 식별")
    pos_x = models.FloatField(default=50, help_text="요소 내 상대 X(%)")
    pos_y = models.FloatField(default=50, help_text="요소 내 상대 Y(%)")
    body = models.TextField(help_text="피드백 본문")
    author_handle = models.CharField(max_length=30, db_index=True)
    status = models.CharField(max_length=10, choices=STATUS, default="open", db_index=True)
    resolution = models.TextField(blank=True, help_text="처리 노트(무엇을 어떻게 고쳤나·커밋)")
    resolved_by = models.CharField(max_length=60, blank=True, help_text="처리 주체(AI 세션 등)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["service", "status"]),
                   models.Index(fields=["service", "route"])]

    def __str__(self):
        return f"[{self.status}] {self.route} {self.element_label or self.selector[:30]}: {self.body[:40]}"


class FeedbackComment(models.Model):
    """핀 아래 스레드 — admin들이 서로의 피드백에 이어 다는 댓글."""
    item = models.ForeignKey(FeedbackItem, on_delete=models.CASCADE, related_name="comments")
    author_handle = models.CharField(max_length=30)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
