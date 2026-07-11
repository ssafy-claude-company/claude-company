"""플랫폼 원시 개체 — Node·Edge·View. 구조(레이어·축·타입)는 코드가 아니라 데이터다.

- Node.type 은 자유 문자열(concept·file·decision·…) — 코드에 타입 목록 없음.
- 스캔은 이 원시를 '사용'한다: 파일=Node(origin=scan), import=Edge(origin=scan).
  재스캔은 scan-원산만 동기화하고 사람이 만든 것은 절대 건드리지 않는다.
- View = 저장된 관점(타입 필터 + 선택적 레인 축). '논리/소스 레이어'는 시드된 View일 뿐.
"""
from django.db import models


class Project(models.Model):
    slug = models.SlugField(unique=True)                  # = 피드백 service 슬러그
    name = models.CharField(max_length=120)
    root = models.CharField(max_length=400, help_text="스캔 루트(VPS 파일시스템 경로)")
    skips = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self):
        return self.slug


class ScanRun(models.Model):
    """스캔 이력(스냅샷 보존) — 현행 그래프는 Node/Edge 테이블이 정본."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="scans")
    created_at = models.DateTimeField(auto_now_add=True)
    ok = models.BooleanField(default=True)
    error = models.TextField(blank=True)
    stats = models.JSONField(default=dict)
    data = models.JSONField(default=dict)

    class Meta:
        ordering = ["-id"]


class Node(models.Model):
    """범용 노드. nid는 안정 식별자(핀·엣지가 이걸 문다): scan 파일='f:<경로>', 사용자='u<pk>'."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="nodes")
    nid = models.CharField(max_length=420)
    type = models.CharField(max_length=40, default="concept", db_index=True)
    name = models.CharField(max_length=200)
    origin = models.CharField(max_length=10, default="user", db_index=True)   # user | scan
    meta = models.JSONField(default=dict, blank=True)     # desc·src·note·lane·globs… (자유)
    payload = models.JSONField(default=dict, blank=True)  # scan 소유 필드(loc·area·kind·doc·dir)
    x = models.FloatField(null=True, blank=True)
    y = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("project", "nid")]
        ordering = ["id"]


class Edge(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="edges")
    s = models.CharField(max_length=420)                  # nid
    t = models.CharField(max_length=420)                  # nid
    label = models.CharField(max_length=120, blank=True)
    both = models.BooleanField(default=False)
    origin = models.CharField(max_length=10, default="user", db_index=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["project", "s"]), models.Index(fields=["project", "t"])]


class View(models.Model):
    """저장된 관점 — 레이어를 사용자가 만든다. types 비면 전체. lanes 있으면 축(열) 배치."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="views")
    vid = models.SlugField()
    name = models.CharField(max_length=80)
    types = models.JSONField(default=list, blank=True)    # 포함할 Node.type 목록(비면 전체)
    lanes = models.JSONField(default=list, blank=True)    # 열 이름 순서(비면 자유 배치=force)
    order = models.IntegerField(default=0)

    class Meta:
        unique_together = [("project", "vid")]
        ordering = ["order", "id"]
