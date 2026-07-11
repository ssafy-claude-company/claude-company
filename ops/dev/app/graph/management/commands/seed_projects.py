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

from graph.models import Edge, Node, Project, ScanRun, View
from graph.scanner import scan
from graph.sync import sync_scan
from graph.views import ensure_default_views

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
        self._ensure_canvases()
        self._migrate_pins()

    def _ensure_canvases(self):
        from graph.models import Canvas, Item, Link
        for p in Project.objects.all():
            c, made = Canvas.objects.get_or_create(project=p, cid="main", defaults={"name": "메인"})
            if p.slug == "claude-company" and not c.items.exists():
                # AI의 첫 되그림 — 이 캔버스가 무엇인지 그 자체로 보여주는 최소 다이어그램(지워도 됨)
                b1 = Item.objects.create(canvas=c, kind="sticky", origin="ai", x=80, y=120, w=230,
                    text="여기는 사람+AI 공유 캔버스.\n더블클릭=메모 · ● 드래그=연결 · 📌=AI에게 시키기.\n이 메모는 지워도 됩니다.")
                s1 = Item.objects.create(canvas=c, kind="sticky", origin="ai", x=420, y=100, w=170, text="네 스케치·핀")
                s2 = Item.objects.create(canvas=c, kind="sticky", origin="ai", x=680, y=100, w=170, text="세션·봇의 작업")
                s3 = Item.objects.create(canvas=c, kind="sticky", origin="ai", x=550, y=250, w=190, text="AI가 여기 되그림(문서화)")
                Link.objects.create(canvas=c, s=s1, t=s2, label="백로그로", origin="ai")
                Link.objects.create(canvas=c, s=s2, t=s3, label="결과", origin="ai")
                Link.objects.create(canvas=c, s=s3, t=s1, label="확인·재스케치", origin="ai")
                self.stdout.write("메인 캔버스에 AI 안내 다이어그램")

    def _ensure(self, slug, name, root, skips):
        p, made = Project.objects.get_or_create(slug=slug, defaults={"name": name, "root": root, "skips": skips})
        ensure_default_views(p)
        if made or not p.scans.filter(ok=True).exists():
            data = scan(p.root, p.skips)
            st = sync_scan(p, data)
            ScanRun.objects.create(project=p, ok=True, data=data, stats=st)
            self.stdout.write(f"{slug}: 등록+스캔 {data['meta']['counts']}")
        else:
            self.stdout.write(f"{slug}: 이미 있음")

    def _absorb_logical(self):
        """옛 손시드 logical.json → 개념 노드(type=concept)로 흡수(개념이 하나도 없을 때만)."""
        p = Project.objects.filter(slug="claude-company").first()
        src = ROOT / "ops" / "dev" / "static" / "logical.json"
        if not p or p.nodes.filter(type="concept").exists() or not src.exists():
            return
        d = json.loads(src.read_text())
        stages = d.get("stages") or []
        for n in d["nodes"]:
            lane = stages[n.get("stage", 0)] if 0 <= n.get("stage", 0) < len(stages) else ""
            Node.objects.create(project=p, nid=n["id"], type="concept", origin="user", name=n["name"],
                                meta={k: v for k, v in {"desc": n.get("one", ""), "src": n.get("src", ""),
                                                        "lane": lane, "globs": n.get("globs", [])}.items() if v})
        for e in d["edges"]:
            Edge.objects.create(project=p, s=e["s"], t=e["t"], label=e.get("label", ""),
                                both=bool(e.get("both")), origin="user")
        View.objects.update_or_create(project=p, vid="concept",
                                      defaults={"name": "개념", "types": ["concept"], "lanes": stages, "order": 0})
        self.stdout.write(f"논리층 흡수: 개념 {p.nodes.filter(type='concept').count()} · 관계 {p.edges.filter(origin='user').count()}")

    def _migrate_pins(self):
        try:
            from feedback.models import FeedbackItem
        except Exception:
            return
        n = FeedbackItem.objects.filter(service="codegraph").update(
            service="claude-company", route="/dev/p/claude-company/")
        if n:
            self.stdout.write(f"옛 codegraph 핀 {n}건 → claude-company로 이관")
