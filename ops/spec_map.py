#!/usr/bin/env python3
"""정본(spec-map) 도출 엔진 — P1 스파이크.

프로젝트의 **구조·문서·계약**을 소스에서 직접 도출해 `ops/var/spec_map.json`으로 낸다.
손으로 적은 수치(테스트 수·레포 수·경로)를 계산값이 대체한다 — 그래서 드리프트하지 않는다.

낸다:
- nodes: 레포·문서·계약(각자 도출 사실 부착)
- edges: 크로스레포 import(의존 방향 = 매체중립 불변식 증명)·문서→파일 참조
- facts: 마이그레이션·모델·테스트 수·LOC 등 계산값
- drift: 문서가 주장하는 값 vs 소스가 말하는 값의 불일치(죽은 경로·stale 수치·고아·권위 위반)

Django·외부 의존 없음(순수 stdlib) — CI/커밋 훅에서 돌 수 있게. 읽기 전용(소스 미변경).

사용: python3 ops/spec_map.py [--root /root/ClaudeCompany] [--out ops/var/spec_map.json] [--stdout]
"""
import argparse
import ast
import json
import os
import re
import subprocess
import time

REPOS = ["system", "organt", "guide", "murmur"]
# 크로스레포 의존 탐지용 — 각 레포 최상위 패키지명
REPO_PKGS = {"system": "system", "organt": "organt", "guide": "guide", "murmur": "murmur"}
DATE_RE = re.compile(r"_(\d{4})-(\d{2})-(\d{2})")   # 파일명 날짜 = 스냅샷


def sh(cmd, cwd=None):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return ""


def count_py_loc(root, rel):
    """레포의 대략 LOC(추적 파일 기준). git 있으면 git ls-files, 없으면 walk."""
    d = os.path.join(root, rel)
    total = 0
    files = 0
    for dp, dns, fns in os.walk(d):
        if any(x in dp for x in ("/.venv", "/node_modules", "/migrations", "/dist", "/.git", "__pycache__")):
            continue
        for f in fns:
            if f.endswith(".py"):
                files += 1
                try:
                    with open(os.path.join(dp, f), encoding="utf-8", errors="ignore") as fh:
                        total += sum(1 for _ in fh)
                except Exception:
                    pass
    return total, files


def cross_repo_imports(root):
    """각 레포가 어느 레포를 import 하나(의존 방향). system이 타레포 0이어야 매체중립 성립.
    grep 기반(빠름) — `from <pkg>` / `import <pkg>`."""
    edges = {}   # (src_repo, dst_repo) -> count
    for src in REPOS:
        srcd = os.path.join(root, src)
        if not os.path.isdir(srcd):
            continue
        for dst in REPOS:
            if dst == src:
                continue
            pkg = REPO_PKGS[dst]
            out = sh(["grep", "-rEl", "--include=*.py",
                      "--exclude-dir=.venv", "--exclude-dir=node_modules", "--exclude-dir=migrations",
                      r"^\s*(from|import)\s+%s(\.|\s|$)" % re.escape(pkg), srcd])
            n = len([x for x in out.splitlines() if x.strip()])
            if n:
                edges[(src, dst)] = n
    return edges


def migrations_info(root):
    d = os.path.join(root, "murmur/backend/sns/migrations")
    migs = sorted(f for f in os.listdir(d) if re.match(r"\d{4}_", f)) if os.path.isdir(d) else []
    latest = migs[-1].split("_")[0] if migs else None
    return len(migs), latest, [m[:-3] for m in migs]


def model_count(root):
    """models.py에서 Django 모델 클래스 수(AST — models.Model 상속)."""
    p = os.path.join(root, "murmur/backend/sns/models.py")
    if not os.path.isfile(p):
        return None
    try:
        tree = ast.parse(open(p, encoding="utf-8").read())
    except Exception:
        return None
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                src = ast.unparse(base) if hasattr(ast, "unparse") else ""
                if "Model" in src:
                    n += 1
                    break
    return n


def test_counts(root, run):
    """테스트 수 — run=True면 실제 collect(pytest --co, 느림). 아니면 None(문서 대조는 스킵)."""
    out = {}
    if not run:
        return out
    # 브레인 pytest collect-only
    co = sh(["bash", "-c",
             "cd %s && PYTHONPATH=%s .venv/bin/python -m pytest ops/tests/ --co -q 2>/dev/null | tail -1" % (root, root)])
    m = re.search(r"(\d+)\s+tests?", co) or re.search(r"(\d+)\s+selected", co)
    if m:
        out["brain_pytest"] = int(m.group(1))
    return out


def doc_inventory(root):
    """거버넌스 문서 인벤토리 — 정본(무날짜) vs 스냅샷(날짜)·나이·상호참조. 봇 산출물/캐시 제외."""
    docs = []
    roots = [("murmur/docs", "murmur"), ("ops", "ops"), ("", "root")]
    now = time.time()
    seen = set()
    for rel, zone in roots:
        base = os.path.join(root, rel)
        if not os.path.isdir(base):
            continue
        for f in sorted(os.listdir(base)):
            if not f.endswith(".md"):
                continue
            path = os.path.join(base, f)
            if not os.path.isfile(path) or path in seen:
                continue
            seen.add(path)
            m = DATE_RE.search(f)
            dated = bool(m)
            age_days = None
            if dated:
                try:
                    ts = time.mktime((int(m.group(1)), int(m.group(2)), int(m.group(3)), 0, 0, 0, 0, 0, -1))
                    age_days = int((now - ts) / 86400)
                except Exception:
                    pass
            try:
                size = os.path.getsize(path)
            except Exception:
                size = 0
            docs.append({"name": f, "zone": zone, "rel": os.path.join(rel, f).lstrip("/"),
                         "kind": "snapshot" if dated else "canonical", "age_days": age_days, "size": size})
    return docs


def doc_refs(root, docs):
    """문서 간 마크다운 링크(상호참조) — 고아 탐지용. 링크 대상 파일명이 인벤토리에 있으면 엣지."""
    names = {d["name"]: d for d in docs}
    refs = {}    # name -> set(target names)
    referenced = set()
    for d in docs:
        p = os.path.join(root, d["rel"])
        try:
            txt = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        tgts = set()
        for mt in re.finditer(r"\[[^\]]*\]\(([^)]+\.md)\)", txt):
            tgt = os.path.basename(mt.group(1))
            if tgt in names and tgt != d["name"]:
                tgts.add(tgt)
                referenced.add(tgt)
        if tgts:
            refs[d["name"]] = sorted(tgts)
    orphans = [d["name"] for d in docs
               if d["name"] not in referenced and d["name"] not in ("README.md",) and d["zone"] == "murmur"]
    return refs, orphans


def detect_drift(root, docs, facts):
    """문서 주장 vs 소스 사실 대조 — P1은 고가치 몇 종만(죽은 경로·stale 수치·권위). 확장은 P2."""
    drift = []
    # 스캔 대상 문서 본문 로드
    bodies = {}
    for d in docs:
        try:
            bodies[d["name"]] = open(os.path.join(root, d["rel"]), encoding="utf-8", errors="ignore").read()
        except Exception:
            bodies[d["name"]] = ""

    def hits(pat):
        return [n for n, b in bodies.items() if re.search(pat, b)]

    # 1) 죽은 경로 — 문서가 가리키는 테스트 경로가 실재하나
    for deadpath in ("/root/ClaudeCompany/tests", "PJT/tests"):
        exists = os.path.exists(os.path.join(root, deadpath.replace("/root/ClaudeCompany/", "")))
        if not exists:
            hs = hits(re.escape(deadpath))
            if hs:
                drift.append({"kind": "dead_path", "severity": "crit",
                              "claim": deadpath, "truth": "존재하지 않음",
                              "docs": hs, "detail": "문서가 가리키는 경로가 파일시스템에 없음"})

    # 2) 마이그레이션 수 — 문서가 낡은 최댓값을 말하나
    latest = facts.get("migrations_latest")
    if latest:
        for stale in ("0017", "0015"):
            if stale != latest:
                hs = [n for n, b in bodies.items() if re.search(r"000?1[\s~\-–]+%s" % stale, b) or ("0001~%s" % stale) in b]
                if hs:
                    drift.append({"kind": "stale_number", "severity": "warn",
                                  "claim": "마이그 0001~%s" % stale, "truth": "0001~%s" % latest,
                                  "docs": hs, "detail": "마이그레이션 범위가 실제보다 낡음"})

    # 3) 레포 수 — 2레포(병합)인데 4레포라 서술
    hs4 = [n for n, b in bodies.items() if re.search(r"4\s*개?\s*레포|4레포|4개 독립", b)]
    if hs4:
        drift.append({"kind": "stale_number", "severity": "crit",
                      "claim": "4레포", "truth": "2레포(병합됨)",
                      "docs": hs4, "detail": "레포 병합이 일부 문서에만 착지"})

    # 4) Render 잔존 — VPS 단일화됐는데 Render 전제
    hsR = [n for n, b in bodies.items() if re.search(r"render\.yaml|onrender\.com|웹\s*=\s*Render|Render 배포", b, re.I)]
    if hsR:
        drift.append({"kind": "stale_deploy", "severity": "crit",
                      "claim": "Render 배포", "truth": "VPS(Render 폐기)",
                      "docs": hsR, "detail": "배포 호스트 전환이 일부 문서에 미반영"})

    # 5) 매체중립 위반 — system이 타레포 import 하면 불변식 깨짐
    if facts.get("media_neutral") is False:
        drift.append({"kind": "invariant_violation", "severity": "crit",
                      "claim": "system 매체중립", "truth": "system이 타레포 import",
                      "docs": [], "detail": "system/이 organt/guide/murmur를 import(불변식 위반)"})

    return drift


def build(root, run_tests):
    edges_imp = cross_repo_imports(root)
    media_neutral = not any(src == "system" for (src, dst) in edges_imp)
    mig_n, mig_latest, mig_list = migrations_info(root)
    models = model_count(root)
    docs = doc_inventory(root)
    refs, orphans = doc_refs(root, docs)
    tests = test_counts(root, run_tests)

    facts = {
        "media_neutral": media_neutral,
        "migrations_count": mig_n, "migrations_latest": mig_latest,
        "models": models,
        "docs_total": len(docs),
        "docs_canonical": sum(1 for d in docs if d["kind"] == "canonical"),
        "docs_snapshot": sum(1 for d in docs if d["kind"] == "snapshot"),
        "orphans": orphans,
        **{("tests_" + k): v for k, v in tests.items()},
    }
    for r in REPOS:
        loc, nfiles = count_py_loc(root, r)
        facts["loc_" + r] = loc
        facts["files_" + r] = nfiles

    drift = detect_drift(root, docs, facts)

    # 노드: 레포 + 문서(정본/스냅샷)
    nodes = []
    for r in REPOS:
        nodes.append({"id": "repo:" + r, "type": "repo", "label": r,
                      "loc": facts.get("loc_" + r), "files": facts.get("files_" + r),
                      "core": (r == "system")})
    for d in docs:
        d_drift = [x for x in drift if d["name"] in x.get("docs", [])]
        nodes.append({"id": "doc:" + d["name"], "type": "doc", "label": d["name"],
                      "zone": d["zone"], "kind": d["kind"], "age_days": d["age_days"],
                      "orphan": d["name"] in orphans,
                      "drift": len(d_drift),
                      "drift_max": ("crit" if any(x["severity"] == "crit" for x in d_drift)
                                    else ("warn" if d_drift else None))})

    edges = []
    for (src, dst), n in edges_imp.items():
        edges.append({"from": "repo:" + src, "to": "repo:" + dst, "type": "import", "count": n})
    for name, tgts in refs.items():
        for t in tgts:
            edges.append({"from": "doc:" + name, "to": "doc:" + t, "type": "ref"})

    return {
        "generated_at": time.time(),
        "root": root,
        "facts": facts,
        "nodes": nodes,
        "edges": edges,
        "drift": sorted(drift, key=lambda x: 0 if x["severity"] == "crit" else 1),
        "summary": {
            "repos": len(REPOS), "docs": len(docs),
            "drift_crit": sum(1 for x in drift if x["severity"] == "crit"),
            "drift_warn": sum(1 for x in drift if x["severity"] == "warn"),
            "orphans": len(orphans),
            "media_neutral": media_neutral,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("ORGANT_PJT", "/root/ClaudeCompany"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--run-tests", action="store_true", help="테스트 수를 실제 collect(느림)")
    ap.add_argument("--stdout", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    data = build(root, a.run_tests)
    out = a.out or os.path.join(root, "ops/var/spec_map.json")
    if a.stdout:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        s = data["summary"]
        print("spec_map → %s" % out)
        print("  레포 %d · 문서 %d · 드리프트 %d(crit %d·warn %d) · 고아 %d · 매체중립 %s"
              % (s["repos"], s["docs"], s["drift_crit"] + s["drift_warn"],
                 s["drift_crit"], s["drift_warn"], s["orphans"], "✓" if s["media_neutral"] else "✗"))


if __name__ == "__main__":
    main()
