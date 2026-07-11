"""범용 코드 스캐너 — 어떤 프로젝트 루트든 파일=노드, import=엣지로.

특정 레포 하드코딩 없음(메타 서비스). 언어: py(ast) · js/vue/ts(상대 import 정규식).
html/css/md는 노드로만(구조 대상, 엣지 없음). 해석은 지도 목적의 휴리스틱이다 —
py 절대 import는 '경로 꼬리 일치'로 푼다(패키지 루트가 여럿인 저장소도 대충 맞게).
"""
import ast
import re
import time
from pathlib import Path

DEFAULT_SKIPS = {".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv",
                 "package-lock.json", ".next", "coverage"}
CODE_EXT = {".py", ".js", ".vue", ".ts", ".mjs"}
NODE_ONLY_EXT = {".html", ".css", ".md", ".json", ".sh", ".sql"}
JS_IMPORT_RE = re.compile(   # from-import · dynamic import() · require() · side-effect import './x'
    r"""(?:import\s+[^'"]*?from\s+|import\s*\(\s*|require\s*\(\s*|import\s+)['"]([^'"]+)['"]""")
MAX_FILES = 3000


def _skip(rel_parts, extra):
    if any(p in DEFAULT_SKIPS for p in rel_parts):
        return True
    rel = "/".join(rel_parts)
    return any(rel == e or rel.startswith(e.rstrip("/") + "/") for e in (extra or []))


def _doc(path, kind):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if kind == "py":
        try:
            d = ast.get_docstring(ast.parse(text)) or ""
        except SyntaxError:
            d = ""
        return d.strip().splitlines()[0][:160] if d.strip() else ""
    for ln in text.splitlines()[:10]:
        s = ln.strip()
        if s.startswith("//"):
            return s.lstrip("/ ").strip()[:160]
        if s.startswith("<!--"):
            return s.replace("<!--", "").replace("-->", "").strip()[:160]
        if s.startswith("#") and kind in ("sh", "md"):
            return s.lstrip("# ").strip()[:160]
    return ""


class _PyResolver:
    """dotted import → 파일. 루트 기준 완전 일치 → 꼬리 일치(최장) 순."""

    def __init__(self, rels):
        self.exact = {}
        self.tail = {}
        for rel in rels:
            if not rel.endswith(".py"):
                continue
            parts = tuple(Path(rel).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            self.exact[parts] = rel
            for i in range(len(parts)):
                self.tail.setdefault(parts[i:], rel)

    def resolve(self, mod_parts):
        parts = tuple(mod_parts)
        while parts:
            if parts in self.exact:
                return self.exact[parts]
            if parts in self.tail:
                return self.tail[parts]
            parts = parts[:-1]
        return None


def _py_edges(path, rel, resolver):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return set()
    out = set()
    pkg = Path(rel).with_suffix("").parts
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                t = resolver.resolve(al.name.split("."))
                if t:
                    out.add(t)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(pkg[:-node.level])
                mod = base + (node.module.split(".") if node.module else [])
            else:
                mod = node.module.split(".") if node.module else []
            if not mod:
                continue
            t = resolver.resolve(mod)
            if t:
                out.add(t)
            for al in node.names:
                t2 = resolver.resolve(mod + [al.name])
                if t2:
                    out.add(t2)
    return out


def _js_edges(path, rel, known):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    out = set()
    for m in JS_IMPORT_RE.finditer(text):
        spec = m.group(1)
        if not spec.startswith("."):
            continue
        base = (path.parent / spec).resolve()
        for cand in (base, *(base.with_suffix(e) for e in (".js", ".vue", ".ts", ".mjs")), base / "index.js"):
            try:
                r = str(cand.relative_to(path.parents[len(Path(rel).parts) - 1]))
            except ValueError:
                continue
            if r in known:
                out.add(r)
                break
    return out


def scan(root, extra_skips=None):
    """루트를 스캔해 {nodes, edges, meta}. 파일 상한(MAX_FILES) 초과분은 meta에 명시."""
    t0 = time.time()
    root = Path(root).resolve()
    files, truncated = [], False
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if _skip(rel_parts, extra_skips):
            continue
        if p.suffix in CODE_EXT or p.suffix in NODE_ONLY_EXT:
            files.append(p)
            if len(files) >= MAX_FILES:
                truncated = True
                break
    rels = [str(p.relative_to(root)) for p in files]
    known = set(rels)
    resolver = _PyResolver(rels)
    nodes, edges = [], []
    for p, rel in zip(files, rels):
        kind = p.suffix.lstrip(".")
        try:
            loc = sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
        except OSError:
            loc = 0
        area = rel.split("/")[0] if "/" in rel else "."
        nodes.append({"id": rel, "label": p.name, "area": area, "dir": str(Path(rel).parent),
                      "kind": kind, "loc": loc, "doc": _doc(p, kind)})
        targets = set()
        if kind == "py":
            targets = _py_edges(p, rel, resolver)
        elif kind in ("js", "vue", "ts", "mjs"):
            targets = _js_edges(p, rel, known)
        for t in sorted(targets):
            if t != rel and t in known:
                edges.append({"s": rel, "t": t})
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "root": str(root),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "counts": {"nodes": len(nodes), "edges": len(edges)},
            "truncated": truncated,
            "secs": round(time.time() - t0, 2),
        },
    }
