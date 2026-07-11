"""초기 프로젝트 시드 + 옛 정적 자산의 1회 흡수(멱등).

- claude-company: 루트 전체(브레인+murmur 동거 트리). 옛 logical.json이 있으면 논리층으로 흡수.
- murmur: SNS 플랫폼만 따로 보는 렌즈.
- 봇 산출물: ops/var/organt_sns_workspace/* 중 살아있는 것 — 메타 서비스의 요점
  (claude company가 만든 product에도 같은 능력을 얹는다).
- 옛 피드백 핀(service=codegraph, route=/dev/codegraph/)을 새 좌표(claude-company)로 이관.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from graph.models import Concept, ConceptEdge, Project
from graph.scanner import scan
from graph.models import ScanRun

ROOT = Path("/root/ClaudeCompany")

SEED = [
    {"slug": "claude-company", "name": "claude-company (브레인+murmur)", "root": str(ROOT),
     "skips": ["ops/var", "ops/tests", "murmur/frontend/dist", "murmur/e2e",
               "murmur/backend/sns/migrations", "murmur/backend/feedback/migrations",
               "ops/dev/static/vendor", ".dburl"]},
    {"slug": "murmur", "name": "murmur (SNS 플랫폼)", "root": str(ROOT / "murmur"),
     "skips": ["frontend/dist", "e2e", "backend/sns/migrations", "backend/feedback/migrations"]},
]


class Command(BaseCommand):
    help = "메타 서비스 초기 시드 — 프로젝트 등록·첫 스캔·옛 논리층/핀 흡수(멱등)"

    def handle(self, *args, **opts):
        for s in SEED:
            self._ensure(**s)
        # 봇 산출물 — 워크스페이스에서 실제 코드가 있는 것만
        ws = ROOT / "ops" / "var" / "organt_sns_workspace"
        if ws.exists():
            from django.utils.text import slugify
            for d in sorted(ws.iterdir()):
                if not (d.is_dir() and d.name.startswith("p-")):
                    continue
                if not (any(d.rglob("*.py")) or any(d.rglob("*.js"))):
                    continue
                slug = slugify(d.name)[:40] or d.name[:8]   # URL 컨버터는 ASCII — 한글은 떨어져 나감(p-005 등 접두는 유일)
                self._ensure(slug=slug, name=f"봇 산출물 — {d.name}", root=str(d), skips=[])
        self._absorb_logical()
        self._migrate_pins()

    def _ensure(self, slug, name, root, skips):
        p, made = Project.objects.get_or_create(slug=slug, defaults={"name": name, "root": root, "skips": skips})
        if made or not p.scans.filter(ok=True).exists():
            data = scan(p.root, p.skips)
            ScanRun.objects.create(project=p, ok=True, data=data, stats={
                "files": data["meta"]["counts"]["nodes"], "edges": data["meta"]["counts"]["edges"],
                "secs": data["meta"]["secs"], "truncated": data["meta"]["truncated"]})
            self.stdout.write(f"{slug}: 등록+스캔 {data['meta']['counts']}")
        else:
            self.stdout.write(f"{slug}: 이미 있음")

    def _absorb_logical(self):
        """옛 손시드 logical.json → claude-company 논리층(비어 있을 때만)."""
        p = Project.objects.filter(slug="claude-company").first()
        src = ROOT / "ops" / "dev" / "static" / "logical.json"
        if not p or p.concepts.exists() or not src.exists():
            return
        d = json.loads(src.read_text())
        p.stages = d.get("stages") or []
        p.save(update_fields=["stages"])
        for n in d["nodes"]:
            Concept.objects.create(project=p, cid=n["id"], name=n["name"], one=n.get("one", ""),
                                   src=n.get("src", ""), stage=n.get("stage", 0), order=n.get("order", 0),
                                   globs=n.get("globs", []))
        for e in d["edges"]:
            ConceptEdge.objects.create(project=p, s=e["s"], t=e["t"], label=e.get("label", ""),
                                       both=bool(e.get("both")))
        self.stdout.write(f"논리층 흡수: 개념 {p.concepts.count()} · 관계 {p.concept_edges.count()}")

    def _migrate_pins(self):
        try:
            from feedback.models import FeedbackItem
        except Exception:
            return
        n = FeedbackItem.objects.filter(service="codegraph").update(
            service="claude-company", route="/dev/p/claude-company/")
        if n:
            self.stdout.write(f"옛 codegraph 핀 {n}건 → claude-company로 이관")
