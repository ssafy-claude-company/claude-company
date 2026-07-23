"""[P0 — 레포 간 공개 계약 manifest(실행가능)] Fable 판정(B: 단일진실원+계약).

이 파일이 4레포의 *유일한 공개 계약*이다. 여기 없는 것은 공식적으로 내부(internal) = 자유 변경.
두 가지를 기계 검증한다:
  (1) 선언된 계약 진입점이 실제로 import 가능하고 존재하는가(공급자 약속 이행).
  (2) *미선언* 크로스레포 import가 존재하지 않는가(seam이 manifest 없이 넓어지는 것 차단).

**하위호환 금지 규약(Fable):** 계약 변경 = 이 manifest 갱신 + 전 소비자를 *같은 작업 단위*에서 수정.
shim·deprecated 별칭·버전 분기 코드 금지. (외부 소비자 0·배포 대상 1이라 버전 공존 수요가 없다.)
계약을 넓히려면 여기 CONTRACT에 추가하라 — 그러면 (2)가 통과한다. 그게 유일한 확장 경로다.
"""
import ast
import importlib
import os

# full import path → 소비 레포 목록. (2026-07-03 실측 seam, 총 17개)
CONTRACT = {
    "guide.murmur_guide.MurmurGuide": ["murmur"],
    "organt.builder._make_builder": ["guide", "murmur"],
    "system.audit.AuditLog": ["guide", "murmur", "organt"],
    "system.audit.make_post_tool_use_hook": ["organt"],
    "system.config.Config": ["murmur", "organt"],
    "system.config.ROOT": ["guide", "organt"],
    "system.config.load_config": ["guide"],
    "system.guide_tools.make_guide_tools": ["organt"],   # GPT 봇 codex 경로가 원 guide 도구를 HTTP 브리지에 물림(2026-07-22)
    "system.permissions.make_pre_tool_use_hook": ["organt"],
    "system.protocol.Kind": ["guide", "murmur"],
    "system.protocol.Marker": ["organt"],   # 수행문 마커 사전(narrate 필터 — builder가 참조)
    "system.protocol.Request": ["guide", "murmur"],
    "system.protocol.Response": ["guide", "murmur"],
    "system.protocol.parse": ["guide"],
    "system.protocol.PIPELINE_CTX": ["guide"],   # 파이프라인 소속 컨텍스트(SYS가 채움 → Guide가 게시 payload에 동봉, 2026-07-10)
    "system.role_fit.ROLE_HINTS": ["murmur"],    # 역할적합 시소러스 정본(브레인) — 추천(F1301)이 재수출(2026-07-14)
    "system.role_fit.tokens": ["murmur"],        # 토크나이저 정본 — 선거·추천 공용
    "system.sys_core.Sys": ["guide", "murmur"],
    "system.sys_core.load_personas": ["guide"],
    "system.sys_core.save_personas": ["murmur"],
    "system.tool_names.FLOW_TOOLS": ["organt"],
    "system.tool_names.LEADER_TOOLS": ["organt"],
}

# 자기 트리 루트(<root>/ops/tests → <root>) — 정본/worktree 어디서 돌든 "지금 검증 중인 트리"를 스캔.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_DIRS = {"system": "system", "organt": "organt", "guide": "guide", "murmur": "murmur/backend"}


def test_계약_진입점이_실제로_존재하고_import된다():
    """(1) 공급자 약속 이행 — 선언된 이름이 그 모듈에 실재하는가."""
    missing = []
    for path in CONTRACT:
        mod, _, name = path.rpartition(".")
        try:
            m = importlib.import_module(mod)
            if not hasattr(m, name):
                missing.append(f"{path} (모듈엔 있으나 {name} 없음)")
        except Exception as e:
            missing.append(f"{path} (import 실패: {str(e)[:60]})")
    assert not missing, "계약 위반(공급자가 약속을 안 지킴):\n" + "\n".join(missing)


def _cross_repo_imports():
    """각 레포의 .py에서 '다른 레포'를 import하는 (full_path, 소비레포) 전수."""
    import re
    found = set()
    for cons, rel in _REPO_DIRS.items():
        for root, _, files in os.walk(os.path.join(_ROOT, rel)):
            if any(x in root for x in ("__pycache__", "node_modules", "/migrations", "/tests")):
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                try:
                    s = open(os.path.join(root, fn)).read()
                except OSError:
                    continue
                for m in re.finditer(r"^\s*from ((?:system|organt|guide)[.\w]*) import ([^\n#()]+)", s, re.M):
                    mod = m.group(1)
                    if mod.split(".")[0] == cons:
                        continue   # 자기 레포 = seam 아님
                    for nm in m.group(2).split(","):
                        nm = nm.strip().split(" as ")[0].strip()
                        if nm:
                            found.add((f"{mod}.{nm}", cons))
    return found


def test_미선언_크로스레포_import_없음():
    """(2) seam 봉쇄 — manifest에 없는 크로스레포 import는 계약 위반.
    새 계약이 필요하면 CONTRACT에 추가하라(그게 유일한 확장 경로 = 하위호환 금지 규약의 강제 지점)."""
    found = _cross_repo_imports()
    assert found, f"seam 스캔 실효 0({_ROOT}) — 트리 경로가 틀리면 빈 스캔=가짜 통과"
    undeclared = []
    for path, cons in found:
        allowed = CONTRACT.get(path)
        if allowed is None:
            undeclared.append(f"{path} ← {cons} (manifest 미선언)")
        elif cons not in allowed:
            undeclared.append(f"{path} ← {cons} (선언됐으나 {cons}는 허용 소비자 아님)")
    assert not undeclared, (
        "미선언 크로스레포 import(seam이 계약 없이 넓어짐):\n" + "\n".join(undeclared)
        + "\n→ 정당한 계약이면 tests/test_contracts.py의 CONTRACT에 추가하라.")
