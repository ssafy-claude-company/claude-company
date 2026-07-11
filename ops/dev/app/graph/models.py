"""메타 서비스의 1급 개체 — 프로젝트.

이 서비스는 murmur·claude-company 전용이 아니다. claude-company가 만드는 모든 product
(봇 산출물 포함)를 등록해 같은 능력(스캔→지도, 논리층, 피드백 핀)을 얹는 플랫폼이다.
데이터는 전부 여기(DB)에 산다 — 정적 파일·일회용 스크립트 없음.
"""
from django.db import models


class Project(models.Model):
    slug = models.SlugField(unique=True)                  # = 피드백 service 슬러그
    name = models.CharField(max_length=120)
    root = models.CharField(max_length=400, help_text="스캔 루트(VPS 파일시스템 경로)")
    skips = models.JSONField(default=list, blank=True, help_text="기본 제외 외 추가 제외(상대 경로 접두)")
    stages = models.JSONField(default=list, blank=True, help_text="논리층 열 이름(비면 기본 축)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self):
        return self.slug


class ScanRun(models.Model):
    """스캔 1회 결과 — 그래프 전체를 스냅샷(JSON)으로 보존(이력·비교 가능)."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="scans")
    created_at = models.DateTimeField(auto_now_add=True)
    ok = models.BooleanField(default=True)
    error = models.TextField(blank=True)
    stats = models.JSONField(default=dict)                # {files, edges, loc, secs}
    data = models.JSONField(default=dict)                 # {nodes:[…], edges:[…], meta:{…}}

    class Meta:
        ordering = ["-id"]


class Concept(models.Model):
    """논리(추상) 레이어 노드 — 정본·의도에서 도출되는 층. UI에서 편집한다(손 JSON 아님)."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="concepts")
    cid = models.SlugField()
    name = models.CharField(max_length=120)
    one = models.CharField(max_length=300, blank=True)    # 한 줄 설명
    src = models.CharField(max_length=200, blank=True)    # 근거(정본 문서·구술)
    stage = models.IntegerField(default=0)                # 열(축 위 위치)
    order = models.IntegerField(default=0)
    globs = models.JSONField(default=list, blank=True)    # 소스 매핑(경로 접두, 최장 우선)
    x = models.FloatField(null=True, blank=True)          # 드래그 배치(플랫폼에 영속)
    y = models.FloatField(null=True, blank=True)

    class Meta:
        unique_together = [("project", "cid")]
        ordering = ["stage", "order", "cid"]


class ConceptEdge(models.Model):
    """논리 관계(캐논) — s→t, 동사 라벨. 자동 집계(import)는 저장하지 않고 뷰에서 계산."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="concept_edges")
    s = models.SlugField()
    t = models.SlugField()
    label = models.CharField(max_length=120, blank=True)
    both = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]
