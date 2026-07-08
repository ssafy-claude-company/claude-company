"""Audit 로그: 모든 흐름(수집·라우팅·툴 호출·응답)을 JSONL 한 줄씩 남긴다.

Step 1 증명의 '왕복이 로그에 그대로 남는다'를 담당한다.
"""
import json
import time
from pathlib import Path

# ══ [B-21 capability ledger — 증거 파생 명세(BOT_ARCH_REDESIGN 2026-07-03 §3)] ══
# 확장자→_CAPS 4능력 매핑 표(구현 시 명세 — 설계문서 B-21 위임분). 능력명은 rule/communication._CAPS의
# 표시명과 동일 문자열(같은 데이터를 라우팅 '공급'과 관측이 본다 — 단 cover *판정*은 이 장부 비편입).
# 여기서는 **증거 수집만** 한다(PostToolUse 파생: 확장자 범주·run 도메인·배포 이력, writes_by_role 관례) —
# 영속 적립은 rule/task._ledger_accrue가 'owner 정당 수임(owner_delivered)+교차검증 통과 Task의 owner 저작'
# 만 골라 수행한다(흡수형 저작은 폐기 — P-010형 능력 세탁 차단, 부록 A-5).
CAP_EXT = {
    "AI/ML(모델 학습·예측)": ("ipynb", "pkl", "pt", "pth", "onnx", "h5", "safetensors", "npz", "joblib"),
    "실데이터 수집·파이프라인": ("csv", "tsv", "parquet", "jsonl", "ndjson", "arrow", "avro"),
    "데이터 영속·DB": ("sql", "sqlite", "sqlite3", "db", "prisma", "ddl"),
    "배포·인프라(DevOps)": ("yml", "yaml", "dockerfile", "tf", "toml", "nginx", "service"),
    # [장부 공백 교정(2026-07-08, 사용자 승인)] 종전 4범주는 실제 라이브 업무(웹앱 js·html·py)를 하나도
    # 안 덮어 장부가 늘 비었다(관측: capability_earned 0건 / flow.jsonl). 아래 신규 범주는 **관측·표면화
    # 전용**(peers 강점줄·recommend·관측 — 종전과 동일 용도 한정)이며, 스태핑 cover 판정(_CAPS)에는
    # 편입하지 않는다(게이트 무변경 — B-21 '판정 비편입' 원칙 유지).
    "웹 프론트엔드 구현": ("js", "jsx", "ts", "tsx", "vue", "svelte", "html", "css", "scss"),
    "백엔드·API 구현": ("py", "go", "rb", "php", "java", "kt"),
}
# run 도메인 키워드(명령 문자열 소문자 부분일치) — 확장자에 안 잡히는 실행형 증거(학습 실행·DB 마이그레이션 등).
CAP_RUN = {
    "AI/ML(모델 학습·예측)": ("sklearn", "torch", "tensorflow", "keras", "xgboost", "model.fit", "학습"),
    "실데이터 수집·파이프라인": ("data.go.kr", "api.odcloud", "공공데이터", "crawl", "scrape", "etl"),
    "데이터 영속·DB": ("sqlite3 ", "psql", "mysql", "createdb", "alembic", "migrate"),
    "배포·인프라(DevOps)": ("docker", "kubectl", "systemctl", "nginx", "pm2"),
    "백엔드·API 구현": ("manage.py", "uvicorn", "gunicorn", "flask", "django"),
    "품질 검증(QA)": ("pytest", "npm test", "jest", "playwright", "vitest", "unittest"),
}
# 능력별 증거 임계치(횟수 하한) — 이 수 미만이면 강점줄·후보 나열에 표면화하지 않는다(1회 우연 저작 배제).
CAP_MIN = {
    "AI/ML(모델 학습·예측)": 3,
    "실데이터 수집·파이프라인": 3,
    "데이터 영속·DB": 3,
    "배포·인프라(DevOps)": 3,
    "웹 프론트엔드 구현": 3,
    "백엔드·API 구현": 3,
    "품질 검증(QA)": 3,
}


def capability_of(tool_name, tool_input):
    """[B-21] 도구 호출 1건 → 해당하는 _CAPS 능력명(없으면 None). Write/Edit=확장자 범주,
    run=도메인 키워드, deploy=배포 이력. 판정이 아니라 증거 분류(순수 함수)."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    if tool_name in ("Write", "Edit"):
        fp = str(ti.get("file_path") or ti.get("path") or "").lower()
        base = fp.rsplit("/", 1)[-1]
        ext = base.rsplit(".", 1)[-1] if "." in base else base   # 'Dockerfile'처럼 무확장 이름도 범주 토큰
        for cap, exts in CAP_EXT.items():
            if ext in exts:
                return cap
    elif tool_name == "mcp__guide__deploy":
        return "배포·인프라(DevOps)"                             # 배포 이력
    elif tool_name == "mcp__guide__run":
        cmd = str(ti.get("command") or "").lower()
        for cap, kws in CAP_RUN.items():
            if any(k in cmd for k in kws):
                return cap
    return None


def redact_tool_input(tool_input):
    """감사에 파일 *내용 전체*를 남기지 않는다 — Write/Edit의 content/new_string/old_string을 길이 요약으로
    대체(경로·도구명은 보존). 민감 내용 유출·audit 비대화 방지(보안 핫픽스 2026-06). 다른 필드는 그대로."""
    if not isinstance(tool_input, dict):
        return tool_input
    out = dict(tool_input)
    for k in ("content", "new_string", "old_string"):
        v = out.get(k)
        if isinstance(v, str) and len(v) > 80:
            out[k] = f"<{len(v)} chars 생략>"
    return out


class AuditLog:
    """append-only JSONL 기록기."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields) -> dict:
        """이벤트 한 건을 기록하고, 기록한 entry를 돌려준다."""
        entry = {"ts": time.time(), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry


def make_post_tool_use_hook(audit: AuditLog, actor=None, role=None, flow=None):
    """Organt의 모든 툴 호출을 audit에 남기는 PostToolUse 훅 콜백을 만든다.

    actor/role를 주면 '누가(어느 봇·역할)' 그 툴을 호출했는지 기록한다 — 협업 관찰성.
    flow를 주면 툴 '완료' 시점에도 무진행 시계(last_activity)를 갱신한다 — 오래 걸리는 단일 작업
    (예: 빌드·설치 run)이 시작(PreToolUse)만 찍히고 도중에 '행'으로 오인돼 잘리는 것을 막는다.
    hooks={"PostToolUse": [HookMatcher(hooks=[이 콜백])]} 으로 옵션에 주입한다.
    """
    async def hook(input_data, tool_use_id, context) -> dict:
        data = input_data if isinstance(input_data, dict) else {}
        if flow is not None:                 # 도구 완료도 '진행' 신호 — 긴 단일 작업 보호
            try:
                flow.last_activity = time.monotonic()
            except Exception:
                pass
        if flow is not None and actor is not None:
            # [B-21 capability ledger — audit PostToolUse 파생 증거 수집] 행위자별 능력 증거를 흐름에
            # 누계(writes_by_role 관례의 능력판). 여기선 관측만 — 영속 적립은 complete_task 시점에
            # '품질 게이트 통과 Task의 owner 저작'만(rule/task._ledger_accrue). 실패해도 audit는 계속.
            try:
                cap = capability_of(data.get("tool_name"), data.get("tool_input"))
                ev = getattr(flow, "cap_evidence", None)
                if cap and isinstance(ev, dict):
                    by = ev.setdefault(actor, {})
                    by[cap] = int(by.get(cap, 0)) + 1
            except Exception:
                pass
        audit.record(
            "tool_use",
            actor=actor,
            role=role,
            tool=data.get("tool_name"),
            tool_input=redact_tool_input(data.get("tool_input")),
            tool_use_id=tool_use_id,
        )
        return {}

    return hook
