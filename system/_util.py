"""[Core util] 도구·Rule이 공유하는 표현·디버그·반응 유틸 — Rule 로직이 아니라 횡단 관심사.
guide_tools와 rule/ 양쪽이 *순환의존 없이* 여기서 import(종전 guide_tools에 흩어졌던 것 중립화)."""
import os


_DEBUG = bool(os.environ.get("ORGANT_DEBUG"))


def _dbg(msg):
    """진단 로그(기본 off). ORGANT_DEBUG 설정 시에만 stdout으로."""
    if _DEBUG:
        print(msg, flush=True)


def _ok(text):
    return {"content": [{"type": "text", "text": text}]}


async def _react(g, channel_id, message_id, emoji):
    """이모지 반응(상태 표시). Guide에 react가 없으면(테스트 등) 조용히 건너뜀."""
    fn = getattr(g, "react", None)
    if fn:
        await fn(channel_id, message_id, emoji)


def _speech_clip(s, n=1500) -> str:
    """발언 안전망: 폭주만 막고 **침묵 절단하지 않는다** — 잘리면 잘렸다고 표기한다.
    종전의 하드컷([:300]/[:400])은 '3~5줄' 지시를 지킨 발언(한국어 200~400자+)까지 단어
    중간에서 잘랐다(라이브: 회의 발언 전원이 307~308자로 박제, "…프론트엔"에서 끊김 — 사용자
    관측). 더 나쁜 건 회의록도 잘려 **다음 발언자들이 서로의 잘린 주장을 보고 토론**한 것 —
    분량 통제는 지시(프롬프트)와 모델 판단의 몫이고, 시스템은 안전망만 친다."""
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + f" …(발언 {len(s)}자 — {n}자 안전망에서 잘림)"


def _looks_transient(text: str) -> bool:
    """동료 응답이 일시적 API 오류로 보이는지 — 그렇다면 답으로 취급하지 말고 재시도."""
    t = (text or "").strip().lower()
    return t.startswith("api error") or t.startswith("(동료 처리 중 오류")


# ══ [Task Dossier — BOT_ARCH_REDESIGN 2026-07-03 W2] 협의 원본의 단일 기록(.collab/) ══
# 같은 협의가 4곳 사본(채팅·collab_notes·스냅샷·매 위임 재동봉)으로 갈라지고, 6,000자 head-keep 캡이
# '표기는 남고 내용은 영구 소멸'하던 것을 — <workspace>/.collab/T-<task_id>/(GOAL/MINUTES/REPORTS)에
# SYS가 원자적으로 단일 기록해 닫는다. rule/task·rule/communication·sys_core가 공유하는 횡단 유틸이라
# 여기(순환의존 없는 _util)에 둔다. 전부 best-effort: 문서 쓰기 실패가 흐름을 절대 막지 않는다(B-09
# Phase A는 관측 전용 — 주입 무변경이라 항상 켜도 안전).
#
# 경로는 항상 '호출 시점의 flow.workspace' 기준으로 새로 해석한다 — `_idify_workspace`가 흐름 도중
# 폴더를 개명(new-… → p-00n-슬러그)하므로 절대경로 캐시 금지(스냅샷 키 dossier_path도 상대 경로).

def doc_collab_on() -> bool:
    """[Phase B/C 플래그] ORGANT_DOC_COLLAB — 미설정=off(위임 dedup·meet 축소·복구 문서소스 전부
    기존동작 유지). Phase A(문서 *쓰기*)는 플래그와 무관하게 항상 켜져 있다(주입 무변경 — 안전)."""
    return (os.environ.get("ORGANT_DOC_COLLAB") or "").strip().lower() in ("1", "true", "yes", "on")


def dossier_rel(task_id) -> str:
    """Task Dossier의 워크스페이스-상대 경로(스냅샷·프롬프트 참조용 — 절대경로 금지)."""
    return os.path.join(".collab", f"T-{task_id}")


def _dossier_dir(flow, task_id=None):
    """<workspace>/.collab/T-<task_id> 절대경로(지금의 workspace 기준). 워크스페이스/Task가 없으면
    None — 테스트·비프로젝트 흐름에서 조용히 무해."""
    ws = str(getattr(flow, "workspace", "") or "")
    tid = task_id if task_id is not None else getattr(getattr(flow, "current", None), "task_id", None)
    if not ws or not tid or not os.path.isdir(ws):
        return None
    return os.path.join(ws, dossier_rel(tid))


def _atomic_write(path, text):
    """tmp+fsync+rename 원자 쓰기(sys_core._save_projects 관례) — 크래시에도 반쪽 파일 0."""
    import time
    tmp = f"{path}.tmp-{time.monotonic_ns()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def dossier_write(flow, filename, text, task_id=None) -> bool:
    """문서 전체 재작성(멱등) — GOAL.md(set_goal마다)·craft 미러(증류 반영)용. 실패는 False(무해)."""
    try:
        d = _dossier_dir(flow, task_id)
        if not d:
            return False
        os.makedirs(d, exist_ok=True)
        _atomic_write(os.path.join(d, filename), (text or "").rstrip("\n") + "\n")
        return True
    except Exception:
        return False


def dossier_append(flow, filename, block, task_id=None) -> bool:
    """append-only 기록 — MINUTES.md(회의·표결)·REPORTS.md(Work 응답 전문)용. 기존 내용+블록을
    원자 재작성하므로(append 의미론) 크래시에도 반쪽 파일이 없다. 절단 없음(전문 보존이 존재 이유)."""
    try:
        d = _dossier_dir(flow, task_id)
        if not d:
            return False
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, filename)
        prev = ""
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                prev = f.read()
        joined = ((prev.rstrip("\n") + "\n\n") if prev.strip() else "") + (block or "").rstrip("\n") + "\n"
        _atomic_write(p, joined)
        return True
    except Exception:
        return False


def dossier_read(flow, filename, task_id=None):
    """문서 읽기 + 무결성 검사(존재·읽힘·비어있지 않음) — 실패 시 None(호출부가 스냅샷 폴백).
    '사실' 필드(owner_delivered·gate_pass 등)는 항상 스냅샷 소스 — 문서는 '내용'의 원본만 담당."""
    try:
        d = _dossier_dir(flow, task_id)
        if not d:
            return None
        with open(os.path.join(d, filename), encoding="utf-8") as f:
            text = f.read()
        return text if text.strip() else None
    except Exception:
        return None


def clip(text, limit) -> str:
    """[문장 한가운데서 끊지 않는다(2026-07-30, 사용자 지적)] 사람이 읽는 문장을 하드 컷하면
    끊긴 표시가 없어 그게 결론 전부인 줄 읽힌다(U-442 실측: '…1명을 채용하고'에서 멈춘 주기 목표,
    '반대 요지: … 두 명령의 exi'에서 멈춘 표결 요약). 낱말 경계에서 끊고 '…'로 줄었음을 남긴다.
    """
    raw = str(text or "")
    if len(raw) <= int(limit):
        return raw
    cut = raw[:int(limit)]
    sp = max(cut.rfind(" "), cut.rfind("·"), cut.rfind(","), cut.rfind("."))
    if sp >= int(limit) * 0.6:
        cut = cut[:sp]
    return cut.rstrip() + "…"
