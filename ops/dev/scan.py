#!/usr/bin/env python3
"""codegraph scan — ClaudeCompany 코드 구조를 그래프 JSON으로 뽑는다. (dev 도구 — 제품 아님)

`ops/dev/static/codegraph.html`(코드 지도) 뷰어의 데이터 생산자. 파일 = 노드, import = 엣지.
사용자는 코드를 파일로 읽지 않는다 — 이 그래프가 코드에 피드백을 찍는 표면이 된다.

사용:
  python3 ops/dev/scan.py                                # root=ClaudeCompany, out=ops/dev/static/codegraph.json
  python3 ops/dev/scan.py --root <경로> --out <경로>

설계 메모:
- 파이썬은 ast로 import를 해석(패키지 루트 = ClaudeCompany, murmur/backend 두 곳).
- 프론트(.vue/.js)는 상대경로 import만 정규식으로 해석(외부 패키지는 구조가 아니라 소음).
- 제외: 테스트 스위트(ops/tests)·migrations·dist·__pycache__ 등 — 구조 판단에 노이즈.
  단 각 영역의 존재는 meta.excluded에 남겨 "지도에 없음 = 없음"으로 오독되지 않게 한다.
"""
import argparse
import ast
import json
import re
import subprocess
import time
from pathlib import Path

# 스캔 대상 영역(루트 상대) — 표시 순서가 페이지 컬럼 순서가 된다.
AREAS = ["system", "organt", "guide", "ops", "murmur/backend", "murmur/frontend", "murmur/scripts"]

SKIP_DIRS = {"__pycache__", "node_modules", "dist", ".git", ".venv", "venv",
             "migrations", "tests", "e2e", "public", "assets",
             "var"}  # ops/var = 봇 런타임 상태·산출물 — 우리 코드가 아님
SKIP_FILES = {"package-lock.json"}

JS_IMPORT_RE = re.compile(r"""(?:import\s+[^'"]*?from\s+|import\s*\(\s*|require\s*\(\s*)['"]([^'"]+)['"]""")


def area_of(rel: str):
    for a in sorted(AREAS, key=len, reverse=True):
        if rel == a or rel.startswith(a + "/"):
            return a
    return None


def first_doc(path: Path, kind: str):
    """노드 설명 한 줄 — py는 모듈 docstring 첫 줄, vue/js는 첫 주석 줄."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if kind == "py":
        try:
            doc = ast.get_docstring(ast.parse(text)) or ""
        except SyntaxError:
            doc = ""
        return doc.strip().splitlines()[0][:160] if doc.strip() else ""
    for ln in text.splitlines()[:10]:
        s = ln.strip()
        if s.startswith("//"):
            return s.lstrip("/ ").strip()[:160]
        if s.startswith("<!--"):
            return s.replace("<!--", "").replace("-->", "").strip()[:160]
    return ""


def collect_files(root: Path):
    files = []
    for a in AREAS:
        base = root / a
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name in SKIP_FILES:
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
                continue
            if p.suffix == ".py" or (a == "murmur/frontend" and p.suffix in (".vue", ".js")):
                files.append(p)
    return files


def build_py_index(root: Path, files):
    """모듈 경로 → 파일. 패키지 루트 2곳: ClaudeCompany(system.x…), murmur/backend(sns.x…)."""
    idx = {}
    for p in files:
        if p.suffix != ".py":
            continue
        rel = p.relative_to(root)
        for pkg_root in (root, root / "murmur" / "backend"):
            try:
                mod = p.relative_to(pkg_root).with_suffix("")
            except ValueError:
                continue
            parts = list(mod.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                idx[tuple(parts)] = str(rel)
    return idx


def resolve_py(mod_parts, idx):
    """system.rule.floor → 파일. 가장 긴 접두 일치(from X import name 의 name이 모듈일 수도)."""
    parts = tuple(mod_parts)
    while parts:
        if parts in idx:
            return idx[parts]
        parts = parts[:-1]
    return None


def py_edges(path: Path, root: Path, idx):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return set()
    out = set()
    pkg = path.relative_to(root).with_suffix("").parts  # 상대 import 해석용
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                t = resolve_py(al.name.split("."), idx)
                if t:
                    out.add(t)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 상대 import: level만큼 부모로
                base = list(pkg[:-node.level])
                mod = base + (node.module.split(".") if node.module else [])
            else:
                mod = node.module.split(".") if node.module else []
            if not mod:
                continue
            t = resolve_py(mod, idx)
            if t:
                out.add(t)
            for al in node.names:  # from pkg import 모듈 형태
                t2 = resolve_py(mod + [al.name], idx)
                if t2:
                    out.add(t2)
    return out


def js_edges(path: Path, root: Path, known):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    out = set()
    for m in JS_IMPORT_RE.finditer(text):
        spec = m.group(1)
        if not spec.startswith("."):
            continue  # 외부 패키지 제외
        base = (path.parent / spec).resolve()
        for cand in (base, base.with_suffix(".js"), base.with_suffix(".vue"), base / "index.js"):
            try:
                rel = str(cand.relative_to(root))
            except ValueError:
                continue
            if rel in known:
                out.add(rel)
                break
    return out


def repo_head(path: Path):
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve()
    ap.add_argument("--root", default=str(here.parents[2]))  # ops/dev → ClaudeCompany
    ap.add_argument("--out", default=str(here.parent / "static" / "codegraph.json"))
    args = ap.parse_args()
    root = Path(args.root).resolve()

    files = collect_files(root)
    rels = {str(p.relative_to(root)) for p in files}
    idx = build_py_index(root, files)

    nodes, edges = [], []
    for p in files:
        rel = str(p.relative_to(root))
        kind = p.suffix.lstrip(".")
        loc = sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
        nodes.append({
            "id": rel, "label": p.name, "area": area_of(rel),
            "dir": str(Path(rel).parent), "kind": kind, "loc": loc,
            "doc": first_doc(p, kind),
        })
        targets = py_edges(p, root, idx) if kind == "py" else js_edges(p, root, rels)
        for t in sorted(targets):
            if t != rel and t in rels:
                edges.append({"s": rel, "t": t})

    nodes.sort(key=lambda n: (AREAS.index(n["area"]), n["dir"], n["id"]))
    data = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "root": str(root),
            "heads": {"claude-company": repo_head(root), "murmur": repo_head(root / "murmur")},
            "areas": AREAS,
            "counts": {"nodes": len(nodes), "edges": len(edges)},
            "excluded": "ops/tests·migrations·e2e·dist 등은 지도에서 제외(구조 노이즈) — 없음이 아니라 안 그린 것",
        },
        "nodes": nodes,
        "edges": edges,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"nodes={len(nodes)} edges={len(edges)} → {out}")


if __name__ == "__main__":
    main()
