"""SYS 영속·레지스트리 — sys_core.Sys에서 파사드 보존 추출(LLM_DX_AUDIT 1-C ①②③⑦).

projects.json(레지스트리·큐)·jobs.json(직업 기억)·role_profiles.json(직무 기준/경험/개인 기준/실적)·
personas.json·채널 토픽 영속과 프로젝트 등록/신원 규칙을 담는다. 공개 표면은 그대로 Sys 메서드
(`Sys._register_project`·`Sys._save_projects` 등)이며 그 메서드들이 여기로 위임한다 — 외부(테스트·러너)는
종전 이름을 그대로 쓴다. sys_core를 import하지 않는다(단방향). 함수 첫 인자 `sys`는 Sys 인스턴스이고,
다른 Sys 메서드 호출은 `sys._X()` 경유 — 테스트 monkeypatch·서브클래스 오버라이드 의미 보존.
"""
import asyncio
import json
import os
import re
import subprocess
import time
from typing import Optional


def _init_artifact_repo(workspace):
    """[산출물 레포화] 프로젝트 작업공간을 *지속* git 레포로 만든다 — Organt이 그 안에서 작업하며
    커밋하고, deploy가 매번 fresh-init이 아니라 '그 레포'를 push한다(산출물=독립 레포 관리, 사용자 설계).
    이미 레포면 무해(멱등). git 없거나 실패해도 프로젝트 등록은 계속(best-effort)."""
    try:
        ws = str(workspace or "")
        if not ws or not os.path.isdir(ws) or os.path.isdir(os.path.join(ws, ".git")):
            return
        gi = os.path.join(ws, ".gitignore")
        if not os.path.exists(gi):
            with open(gi, "w", encoding="utf-8") as f:
                # [B-07] .collab/(Task Dossier 협의 원본)은 배포 레포에 실리면 안 됨 — 생성 시점부터 제외.
                f.write("node_modules/\n*.log\n.env\n__pycache__/\n.DS_Store\n.collab/\n")
        env = {**os.environ,
               "GIT_AUTHOR_NAME": "Organt", "GIT_AUTHOR_EMAIL": "organt@local",
               "GIT_COMMITTER_NAME": "Organt", "GIT_COMMITTER_EMAIL": "organt@local"}
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=ws, env=env,
                       timeout=15, capture_output=True)
    except Exception:
        pass


# ══ [B-22 — persona 매체중립 저장소(BOT_ARCH_REDESIGN 2026-07-03 설계 3)] ══
# session_dir의 SYS 소유 personas.json(스키마: {"personas": {봇id: persona}} — jobs.json 관례 동형).
# persona는 사용자 소유·정적이라 시스템이 되쓰지 않는다 — 쓰기는 러너(시스템)만: murmur 러너가
# DB(Agent.persona)→JSON 미러하고, Discord 러너가 JSON 로드→빌더(persona_map)에 전달한다(guide는
# murmur를 import 불가 — 단방향 의존이라 파일이 매체중립 전달 경로. Discord persona 소스 0 결함의 소스 신설).
def save_personas(session_dir, persona_map):
    """persona_map({봇id: persona}) → personas.json 원자 저장(tmp+fsync+rename — _save_profiles 관례)."""
    if not session_dir:
        return
    try:
        path = os.path.join(str(session_dir), "personas.json")
        tmp = f"{path}.tmp-{time.monotonic_ns()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"personas": {str(k): str(v) for k, v in (persona_map or {}).items()
                                    if str(v or "").strip()}},
                      f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        pass


def load_personas(session_dir) -> dict:
    """personas.json → {봇id(int): persona}. 파일 부재·손상 시 빈 map — 종전 동작 폴백(무중단:
    Discord 미기동/미러 이전 환경 무영향)."""
    try:
        path = os.path.join(str(session_dir or ""), "personas.json")
        if not session_dir or not os.path.exists(path):
            return {}
        data = json.load(open(path, encoding="utf-8"))
        return {int(k): str(v) for k, v in (data.get("personas") or {}).items() if str(v or "").strip()}
    except Exception:
        return {}


def load_projects(sys):
    """디스크에서 프로젝트 레지스트리 복원 — 프로세스가 끝나도 '원래 작업'에 개입 가능.
    디스크(logs/)가 없으면(컨테이너 리클레임으로 유실) 커밋된 시드에서 복원하되 'seeded' 마커를
    남긴다 — 시드는 커밋 시점에 멈춘 과거라, 부팅 reconcile에서 Discord 채널 토픽(런타임마다
    갱신되는 영속 진실원)이 있으면 그쪽이 이긴다(리더 재지정·워크스페이스가 시드로 원복되던 한계 해소)."""
    path, seeded = sys.projects_path, False
    if not path or not os.path.exists(path):
        if sys.seed_path and os.path.exists(sys.seed_path):
            path, seeded = sys.seed_path, True
        else:
            return
    try:
        data = json.load(open(path, encoding="utf-8"))
        sys.projects = {int(k): v for k, v in data.get("projects", {}).items()}
        sys._proj_n = data.get("n", len(sys.projects))
        if not seeded:   # [큐 영속 복원] 시드(커밋 시점 과거)의 큐는 복원 안 함 — 실제 logs 저장분만
            sys.queue = [tuple(item) for item in data.get("queue", [])
                         if isinstance(item, (list, tuple)) and len(item) >= 3]
        if seeded:
            for p in sys.projects.values():
                p["seeded"] = True
            sys._save_projects()   # logs에 물질화(마커 포함 — reconcile이 보고 토픽 우선 적용)
            sys._log("projects_seed_restored", n=len(sys.projects))
    except Exception:
        pass


def save_projects(sys):
    if not sys.projects_path:
        return
    try:
        # [큐 영속(2026-06-23 전수감사)] 대기열도 함께 저장 — 종전 인메모리라 컨테이너 킬에 유실돼
        # '접수됨, 다시 안 보내도 됨' 약속이 거짓이 됐다(대기 요청 증발). SIGTERM flush가 _save_projects를
        # 부르므로 죽기 직전 대기열이 디스크에 남고, 부팅 때 _load_projects가 되살린다.
        data = {"n": sys._proj_n,
                "projects": {str(k): v for k, v in sys.projects.items()},
                "queue": [list(item) for item in sys.queue]}
        # 원자적 저장: 임시파일에 다 쓰고 flush+fsync 후 교체 → 쓰는 도중 프로세스가 죽어도
        # 원본 projects.json이 '반쪽(깨진 JSON)'으로 남지 않는다(개입 레지스트리 유실 방지).
        tmp = f"{sys.projects_path}.tmp-{time.monotonic_ns()}"   # 병렬 흐름 동시 저장 경합 방지
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, sys.projects_path)
    except Exception:
        pass


def load_jobs(sys):
    """디스크(jobs.json)에 영속된 '직업 기억'(예비→채용 직군)을 roster 라벨·현재 라벨에 덮어쓴다 —
    프로세스 재시작 뒤에도 채용했던 직군(예: 게임 기획자)이 '예비'로 원복되지 않게(1봇 1직군 유지)."""
    if not sys.jobs_path or not os.path.exists(sys.jobs_path):
        return
    try:
        data = json.load(open(sys.jobs_path, encoding="utf-8"))
        for k, v in (data.get("jobs") or {}).items():
            kid = int(k)
            sys._roster_labels[kid] = v
            if kid in sys.bot_info:
                sys.bot_info[kid] = v
    except Exception:
        pass


def save_jobs(sys):
    """현재 '직업 기억'(예비가 아닌 라벨)을 jobs.json에 원자적 저장 — 재시작 넘어 직군 유지."""
    if not sys.jobs_path:
        return
    try:
        jobs = {str(k): v for k, v in sys._roster_labels.items()
                if v and not str(v).startswith("예비")}
        tmp = f"{sys.jobs_path}.tmp-{time.monotonic_ns()}"   # 병렬 흐름 동시 저장 경합 방지
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"jobs": jobs}, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, sys.jobs_path)
    except Exception:
        pass


def persist_job(sys, mid, role):
    """recruit가 예비를 직군으로 채용/자기직군 확정할 때 호출 — 메모리(_roster_labels)+디스크(jobs.json) 갱신."""
    sys._roster_labels[int(mid)] = role
    sys._save_jobs()


def persist_capability(sys, mid, ev):
    """[B-21] 품질 게이트 통과분 실적 영속 — rule/task._ledger_accrue가 'owner 정당 수임(owner_delivered)
    +교차검증 통과 Task의 owner 저작'만 골라 호출한다. 장부는 role_profiles.json의 capability_ledger 키.
    capability_earned 로그가 관측 지표(용도 ④) — 게이트 판정 소비처는 없다(cover 비편입)."""
    led = sys.capability_ledger.setdefault(int(mid), {})
    for cap, n in (ev or {}).items():
        led[cap] = int(led.get(cap, 0)) + int(n)
    sys._save_profiles()
    sys._log("capability_earned", bot=int(mid), caps={k: int(v) for k, v in (ev or {}).items()})


def register_project(sys, channel_id, name, workspace, leader, purpose="",
                     origin_msg="", reuse_ok=None) -> str:
    """프로젝트를 1급 엔티티로 등록 → 식별번호 P-XXX 부여. 같은 채널이나 같은 이름이 이미
    있으면 재사용(중복 방지). 등록 채널에 다시 명령이 오면 '개입'으로 라우팅된다.
    purpose = 프로젝트를 탄생시킨 **사용자 원문**(docs Project.md의 '방향성') — 개입마다
    주입돼, 마지막 미완 Task만 보고 마감하는 시야 협착을 막는다(라이브 관측: '이어서 진행해'가
    아트 Task 하나만 닫고 멀티·배포가 남은 프로젝트를 끝났다고 보고).
    origin_msg = 프로젝트를 탄생시킨 원요청의 메시지 ID — 부팅 복구가 '이미 프로젝트로 졸업한
    원요청'을 새 흐름으로 재발사하지 않고 그 프로젝트 채널 '개입'으로 잇는 연결 고리(라이브:
    동면 복구가 P-009 원요청을 재발사해 진행을 버리고 처음부터 새로 시작 — 사용자 지적)."""
    ch = int(channel_id)
    if ch in sys.projects:
        p = sys.projects[ch]
        changed = False
        if purpose and not p.get("purpose"):
            p["purpose"] = purpose[:700]
            changed = True
        if origin_msg and not p.get("origin_msg"):
            p["origin_msg"] = str(origin_msg)
            changed = True
        if changed:
            sys._save_projects()
        return p["id"]
    # 같은 이름이 이미 있으면 식별번호를 '그대로 유지'하고 채널만 현재 것으로 이동(증가/중복 금지)
    for c, p in list(sys.projects.items()):
        if p.get("name") == name:
            # [신규가 기본 — 주소 지정의 이치(사용자 사건 2026-06-12)] 메인 채널의 새 요청이
            # 기존 작품을 이어가는 길은 둘뿐: 그 프로젝트 채널 개입, 또는 원문에 P-번호 명시.
            # 단어가 유사한 '다른 작품'("2인 협동…디펜스")이 유사 안내+같은 이름 작명으로 기존
            # P-009의 신원·작업공간·채널을 통째로 가져가던 사고 차단 — 이름이 같아도 신설(고유화).
            if reuse_ok is not None and p.get("id") not in reuse_ok:
                name = f"{name}-{sys._proj_n + 1}"
                sys._log("project_reuse_denied_new_request", existing=p.get("id"), made=name)
                break
            # [신원 가드 — 이름은 라벨이지 신원이 아니다] 일반명사 이름("public-data-website")이
            # 우연히 일치하면 다른 작품이 기존 프로젝트의 채널·작업공간·배포 슬롯을 통째로
            # 차지했다(라이브: 지진 사이트가 대기질 P-006을 하이재킹). 재사용은 '진짜 같은
            # 작품'(목표 원문 유사)일 때만 — 다르면 이름을 자동 고유화해 신규 등록한다.
            if purpose and p.get("purpose") and not sys._same_purpose(purpose, p["purpose"]):
                name = f"{name}-{sys._proj_n + 1}"   # 라벨 충돌 해소(신원 분리)
                sys._log("project_name_uniquified", asked=p.get("name"), made=name,
                         existing=p.get("id"))
                break
            # [채널 하이재킹 가드] 미완 Task가 영속된 '진행 중' 프로젝트의 채널은 옮기지 않는다 —
            # 같은 작품을 다른 채널에서 다시 등록하는 흐름(라이브: 동면 복구 재발사가 새 채널을 파고
            # create_project)이 원래 작업 채널에서 신원·토픽·open_task를 떼어가 '기존 채널이 죽고
            # 새 채널에서 처음부터'가 되던 사고 차단. 신원은 돌려주되(같은 작품 인지) 채널·미완
            # Task는 원래 자리를 지킨다 — 이어가기는 그 채널 개입으로.
            if c != ch and p.get("open_task"):
                sys._log("project_channel_move_refused", project=p.get("id"),
                         kept=c, asked=ch)
                return p["id"]
            # [연장 = 기존 산출물 위에서] 재사용은 작업공간을 새 흐름의 임시 폴더로 덮지 않는다 —
            # 이어가기의 본질은 '그 작품의 폴더'를 계속 쓰는 것(덮으면 산출물 연속성이 끊긴다).
            p["channel"] = ch
            if purpose and not p.get("purpose"):
                p["purpose"] = purpose[:700]
            if origin_msg and not p.get("origin_msg"):
                p["origin_msg"] = str(origin_msg)
            sys.projects[ch] = p
            if c != ch:
                del sys.projects[c]
                sys._clear_topic(c)   # 옛 채널의 스테일 토픽 제거(부팅 reconcile 때 유령 등록 방지)
            sys._save_projects()
            sys._sync_topic(ch)
            return p["id"]
    sys._proj_n += 1
    pid = f"P-{sys._proj_n:03d}"
    workspace = sys._idify_workspace(workspace, pid, name)   # 신원=번호: p-00n-슬러그 개명
    _init_artifact_repo(workspace)                           # [산출물 레포화] 지속 git 레포로 — Organt이 그 안에서 작업·커밋
    sys.projects[ch] = {"id": pid, "name": name, "channel": ch,
                        "workspace": workspace, "leader": leader, "summary": "",
                        "purpose": purpose[:700], "origin_msg": str(origin_msg or "")}
    sys._save_projects()
    sys._sync_topic(ch)
    return pid


# --- 레지스트리의 Discord 영속(채널 토픽) — logs/는 리클레임으로 사라지므로, 직군을 Discord '역할'에
# 영속하듯 등록 정보(식별번호·리더·워크스페이스·이름)를 그 프로젝트 '채널 토픽'에 영속한다.
# 우선순위: 런타임 디스크 > 채널 토픽 > 커밋 시드. ---

_TOPIC_RE = re.compile(r"^\[ORGANT:(P-\d+)\]\s+leader=(\d+)\s+\|\s+ws=(.*?)\s+\|\s+name=(.*)$", re.S)


def topic_for(p) -> str:
    return (f"[ORGANT:{p['id']}] leader={int(p.get('leader') or 0)} "
            f"| ws={p.get('workspace') or ''} | name={p.get('name') or ''}")[:1024]


def parse_project_topic(topic) -> Optional[dict]:
    m = _TOPIC_RE.match((topic or "").strip())
    if not m:
        return None
    return {"id": m.group(1), "leader": int(m.group(2)),
            "workspace": m.group(3).strip() or None, "name": m.group(4).strip()}


def spawn_topic_write(sys, channel_id, topic: str):
    if not hasattr(sys.guide, "set_channel_topic"):
        return

    async def _write():
        try:
            r = await sys.guide.set_channel_topic(int(channel_id), topic)
        except Exception:
            return
        if r is None:   # 404 — 채널 죽음. 프로젝트 *기록은 유지*하되 'channel_dead' 표시만 →
            # 다음 부팅부터 reconcile이 이 채널 토픽쓰기를 건너뛴다(죽은 채널 churn 제거, 부팅 stall 차단).
            p = sys.projects.get(int(channel_id))
            if p is not None and not p.get("channel_dead"):
                p["channel_dead"] = True
                sys._save_projects()
                sys._log("channel_marked_dead", channel=int(channel_id), project=p.get("id"))
    try:
        asyncio.get_running_loop().create_task(_write())
    except RuntimeError:    # 이벤트 루프 밖(동기 테스트 등) — best-effort라 건너뜀
        pass


def sync_topic(sys, channel_id):
    """등록/리더 재지정 때 레지스트리 요지를 채널 토픽에 기록(best-effort, 비동기)."""
    p = sys.projects.get(int(channel_id))
    if p:
        sys._spawn_topic_write(channel_id, topic_for(p))


def clear_topic(sys, channel_id):
    sys._spawn_topic_write(channel_id, "")


async def reconcile_projects_from_discord(sys):
    """부팅 시 Discord 채널 토픽으로 레지스트리를 보강한다(리클레임 내구성의 마지막 조각).
    - 레지스트리에 없는 토픽 프로젝트(시드 이후 생겼거나 시드에도 없던 것): 토픽에서 등록 복원.
    - 시드로 복원된 항목(seeded 마커): 토픽이 더 최신이므로 leader/workspace/name을 토픽으로 갱신
      (리더 재지정이 시드로 원복되던 한계 해소). 런타임 디스크 항목은 그대로(디스크가 진실원).
    끝나면 마커를 지우고, 토픽이 없거나 깨진 등록 채널엔 토픽을 다시 채워 자가치유한다."""
    if not hasattr(sys.guide, "get_channel_topics") or not sys.guild_id:
        return
    try:
        topics = await sys.guide.get_channel_topics(sys.guild_id) or {}
    except Exception:
        topics = {}
    changed = False
    for ch, topic in topics.items():
        info = sys.parse_project_topic(topic)
        if not info:
            continue
        ch, cur = int(ch), sys.projects.get(int(ch))
        if cur is None:
            # 같은 식별번호가 다른 채널에 이미 살아 있으면(채널 이동 후 남은 스테일 토픽) 유령 등록 금지
            if any(p.get("id") == info["id"] for p in sys.projects.values()):
                continue
            sys.projects[ch] = {"id": info["id"], "name": info["name"], "channel": ch,
                                "workspace": info["workspace"], "leader": info["leader"],
                                "summary": ""}
            changed = True
            sys._log("project_restored_from_topic", project=info["id"], channel=ch)
        elif cur.pop("seeded", None):
            changed = True
            if (cur.get("leader") != info["leader"] or cur.get("name") != info["name"]
                    or (info["workspace"] and cur.get("workspace") != info["workspace"])):
                cur["leader"], cur["name"] = info["leader"], info["name"]
                if info["workspace"]:
                    cur["workspace"] = info["workspace"]
                sys._log("project_updated_from_topic", project=cur["id"], channel=ch)
        try:
            sys._proj_n = max(sys._proj_n, int(info["id"].split("-")[1]))
        except (IndexError, ValueError):
            pass
    for ch, p in sys.projects.items():
        if p.pop("seeded", None):    # 토픽이 없던 시드 항목 — 시드 값이 최선, 마커만 제거
            changed = True
        if not p.get("channel_dead") and sys.parse_project_topic(topics.get(int(ch), "")) is None:
            sys._sync_topic(ch)     # 자가치유: 등록돼 있는데 토픽이 없으면/깨졌으면 다시 기록(죽은 채널은 스킵)
    if changed:
        sys._save_projects()


def idify_workspace(workspace, pid, name) -> str:
    """[신원=번호 — 사용자 제안] 흐름의 임시 폴더(new-…)를 'p-00n-슬러그'로 개명한다 — 작업
    공간의 정체성을 리더의 작명이 아니라 식별번호가 보증해, 일반명사 이름 충돌이 폴더·배포
    수준에서 무해해진다. 흐름 임시 폴더(new-*)일 때만 작동(직접 등록·시드 경로는 전달값 유지)."""
    try:
        ws = str(workspace or "").rstrip("/")
        parent, cur = os.path.dirname(ws), os.path.basename(ws)
        if not (cur.startswith("new-") and os.path.isdir(ws)):
            return str(workspace)
        slug = re.sub(r"[^0-9a-z가-힣-]+", "-", str(name or "").lower()).strip("-")[:32]
        pidl = pid.lower()
        if slug == pidl or slug.startswith(pidl + "-"):   # 이름에 식별번호가 새도 'p-021-p-021' 중복 접두 방지
            slug = slug[len(pidl):].strip("-")
        tgt = os.path.join(parent, f"{pid.lower()}{('-' + slug) if slug else ''}")
        if tgt != ws and not os.path.exists(tgt):
            os.replace(ws, tgt)
            return tgt
    except OSError:
        pass
    return str(workspace)


def same_purpose(a, b) -> bool:
    """두 목표 원문이 '같은 작품'을 가리키는지 — 토큰 겹침 50% 이상(짧은 쪽 기준).
    이름 일치 재사용의 신원 검증용: 라벨이 같아도 작품이 다르면 차지(하이재킹) 금지."""
    ta = {t for t in re.split(r"[^0-9A-Za-z가-힣]+", str(a or "")) if len(t) >= 2}
    tb = {t for t in re.split(r"[^0-9A-Za-z가-힣]+", str(b or "")) if len(t) >= 2}
    if not ta or not tb:
        return True   # 비교 불능이면 종전 동작(이름 신뢰) 유지
    return len(ta & tb) >= max(1, int(min(len(ta), len(tb)) * 0.5))


def similar_projects(sys, text) -> str:
    """새 요청과 기존 프로젝트(이름+목표 원문)의 토큰 겹침으로 유사 후보를 찾는다 — 임계는
    '겹친 토큰 3개 이상 또는 요청 토큰의 30%'. 정답을 정하지 않는다(신설/재사용은 리더 판단),
    리더가 몰라서 중복 신설하는 일만 막는다."""
    toks = {t for t in re.split(r"[^0-9A-Za-z가-힣]+", str(text or "")) if len(t) >= 2}
    if not toks:
        return ""
    out = []
    for p in sys.projects.values():
        base = f"{p.get('name', '')} {p.get('purpose', '')}"
        ptoks = {t for t in re.split(r"[^0-9A-Za-z가-힣]+", base) if len(t) >= 2}
        inter = toks & ptoks
        if len(inter) >= max(3, int(len(toks) * 0.3)):
            out.append(f"{p['id']} '{p.get('name', '')}'")
    return " / ".join(out[:3])


def save_file_owner(sys, flow) -> None:
    """[소유 경계 영속(2026-06-23, 사용자)] flow.file_owner를 프로젝트 레지스트리에 써 복구에도 유지한다
    — act_by·_gate_pass의 인메모리 리셋 결함을 반복하지 않기 위함(소유는 파일 도메인 경계의 ground truth).
    새 파일이 귀속될 때마다 PostToolUse 훅이 호출(파일 단위라 빈도 제한적)."""
    ch = getattr(flow, "project_channel", None)
    if not ch or int(ch) not in sys.projects:
        return
    try:
        sys.projects[int(ch)]["file_owner"] = dict(getattr(flow, "file_owner", {}) or {})
        # [단순 허락 영속] file_permits(파일→편집권 직군 집합)도 함께 — set은 JSON 불가라 정렬 리스트로
        sys.projects[int(ch)]["file_permits"] = {p: sorted(d) for p, d in (getattr(flow, "file_permits", {}) or {}).items() if d}
        sys._save_projects()
    except Exception:
        pass


def seed_file_owner(sys, flow) -> None:
    """[전환기 시딩 — 프로젝트당 1회] 추적 시작 시점에 *이미 있는* 작업공간 파일의 owner를 audit 이력의
    *최초 작성자 직군*으로 시딩한다(분류가 아니라 실제 생성 기록 — first-toucher 오귀속 방지). 이후 신규
    파일은 PostToolUse가 귀속·영속하므로 file_owner가 비었을 때만 1회 돈다. best-effort(실패해도 흐름 무관)."""
    ws = getattr(flow, "workspace", None)
    if not ws or not sys.projects_path or not os.path.isdir(str(ws)):
        return
    apath = os.path.join(os.path.dirname(str(sys.projects_path)), "audit.jsonl")
    if not os.path.isfile(apath):
        return
    try:
        from .guide_tools import _jobs_of, _norm_job
        ws_real = os.path.realpath(str(ws)) + os.sep
        owner = {}
        with open(apath, encoding="utf-8") as f:
            for ln in f:
                if '"tool_use"' not in ln or ('"Write"' not in ln and '"Edit"' not in ln):
                    continue
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                if d.get("event") != "tool_use" or d.get("tool") not in ("Write", "Edit"):
                    continue
                ti = d.get("tool_input") or {}
                fp = ti.get("file_path") or ti.get("path")
                if not fp:
                    continue
                rp = os.path.realpath(fp if os.path.isabs(fp) else os.path.join(str(ws), fp))
                if rp in owner or not rp.startswith(ws_real):
                    continue        # 이미 최초작성자 확정됐거나 작업공간 밖
                doms = [_norm_job(j) for j in _jobs_of(str(d.get("role") or "")) if j.strip()]
                doms = [x for x in doms if x and not x.startswith("예비")]
                if doms and os.path.isfile(rp):
                    owner[rp] = doms[0]      # 최초 작성자의 주 직군 = owner
        if owner:
            flow.file_owner = owner
            sys._save_file_owner(flow)
            sys._log("file_owner_seeded", project=getattr(flow, "project_id", None), files=len(owner))
    except Exception:
        pass


def load_profiles(sys):
    """디스크(role_profiles.json)에서 직무 기준을 복원한다. 리클레임으로 사라지면 그만 —
    각 직군 전문가가 첫 작업 때 다시 작성한다(자가 재생; 사용자 디스코드를 오염시키지 않음)."""
    if not sys.profiles_path or not os.path.exists(sys.profiles_path):
        return
    try:
        data = json.load(open(sys.profiles_path, encoding="utf-8"))
        sys.role_profiles.update({k: v for k, v in (data.get("profiles") or {}).items() if v})
        sys.role_experience.update({k: list(v)[-sys._EXP_KEEP:]
                                    for k, v in (data.get("experience") or {}).items() if v})
        sys.bot_experience.update({int(k): list(v)[-sys._EXP_KEEP:]    # [개인별] 봇별 경험 복원
                                   for k, v in (data.get("bot_experience") or {}).items() if v})
        # [B-19·B-21] 신설 키 관용 로드 — 구 role_profiles.json(키 부재)도 그대로 열린다(하위호환).
        sys.bot_profiles.update({int(k): str(v) for k, v in (data.get("bot_profiles") or {}).items() if v})
        sys.capability_ledger.update({int(k): {c: int(n) for c, n in dict(v or {}).items()}
                                      for k, v in (data.get("capability_ledger") or {}).items() if v})
        sys.bot_distill_counts.update({int(k): int(v) for k, v in                # [천장 성장 연동]
                                       (data.get("bot_distill_counts") or {}).items()})
        # [사수 전수] 온보딩(이름·인격) 완료 표식 — 온보딩이 기준을 더는 안 채우므로(기준=사수 몫)
        # '기준 없음'으로 재온보딩되지 않게 별도 영속(재시작 무한 재온보딩 방지).
        sys.onboarded.update(int(k) for k in (data.get("onboarded") or []))
    except Exception:
        pass


def save_profiles(sys):
    if not sys.profiles_path:
        return
    try:
        tmp = f"{sys.profiles_path}.tmp-{time.monotonic_ns()}"   # 병렬 흐름 동시 저장 경합 방지
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"profiles": sys.role_profiles, "experience": sys.role_experience,
                       "bot_experience": {str(k): v for k, v in sys.bot_experience.items()},
                       "bot_profiles": {str(k): v for k, v in sys.bot_profiles.items()},         # [B-19]
                       "capability_ledger": {str(k): v for k, v in sys.capability_ledger.items()},  # [B-21]
                       "bot_distill_counts": {str(k): int(v) for k, v in sys.bot_distill_counts.items()},  # [천장 성장]
                       "onboarded": sorted(str(k) for k in sys.onboarded)},   # [사수 전수] 온보딩 완료 표식
                      f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, sys.profiles_path)
    except Exception:
        pass


def record_user_feedback(sys, channel_id, text):
    """사용자가 프로젝트 채널에 남긴 말을 그 프로젝트에 누적한다(RFC-011 M3 — 취향 축적).

    상용 품질의 천장은 LLM 취향(인간 상관 ~0.5)이라 유일한 신뢰 앵커는 사용자다. 사용자가
    이 프로젝트에서 반복해 지적·요구한 것(되풀이되는 불만)을 쌓아 두면 set_goal·검증에서 그걸
    '이 작품의 품질 기준'으로 되돌릴 수 있다 — 직군·도메인 키워드 하드코딩 없이(사용자 자신의
    말), 배포→플레이→비평이 돌수록 기준이 스스로 올라가는 학습 고리. projects.json에 영속해
    동면·재시작 후에도 누적이 유지된다(신규 채널은 아직 미등록이라 자동 skip — 원문은 purpose로 보존)."""
    text = (text or "").strip()
    if not text:
        return
    p = sys.projects.get(int(channel_id))
    if p is None:
        return
    # [좀비 부활 재무장] 사용자가 이 프로젝트로 돌아왔다 → '자동 1회 재개됨' 표시를 해제해 다음
    # 부팅에서 다시 자동 재개 대상이 되게 한다(능동 반복 작업은 계속 이어가고, 버려진 채로만 멈춤).
    p.pop("recovery_attempted", None)
    fb = p.setdefault("feedback", [])
    if fb and fb[-1].get("text") == text:   # 복구 재발사·중복 전송 가드(연속 동일 무시)
        return
    fb.append({"ts": int(time.time()), "text": text[:600]})
    del fb[:-50]   # 저장 위생: 최근 50개만(품질 게이트 아님 — 용량 바운드)
    sys._save_projects()


def aggregate_feedback(sys, proj):
    """[크로스-프로젝트 취향 — '사용자=유일 불만족 엔진' 영속화(2026-06-20)] 이 프로젝트 피드백(전부) +
    과거 프로젝트들의 피드백(중복 제거, 최근순 8개)을 합쳐 '이 사용자가 *작품을 가로질러* 반복 요구하는
    표준'으로 반환한다. 종전엔 이 프로젝트 것만 봐서 한 작품서 고친 걸 다음 작품서 또 틀렸다 — 게이트를
    불만마다 새로 다는 대신(끝없음), 인간 신호가 표준으로 누적돼 스스로 개선되게."""
    own = (proj.get("feedback") if isinstance(proj, dict) else None) or []
    seen = {(f.get("text") or "").strip() for f in own}
    cross = []
    for pp in sys.projects.values():
        if pp is proj or not isinstance(pp, dict):
            continue
        for fb in (pp.get("feedback") or []):
            t = (fb.get("text") or "").strip()
            if t and t not in seen:
                seen.add(t); cross.append(fb)
    cross.sort(key=lambda f: f.get("ts", 0), reverse=True)
    return own + cross[:8]   # 이 프로젝트(전부) + 과거 작업 최근 취향 8개


def valid_leader(sys, proj):
    """[프로젝트↔봇 결합 해제, 2026-06-15] 프로젝트 리더가 현재 로스터(연결된 봇)에 없으면 — 봇이
    해고·예비환원·미연결된 경우 — 가용 봇으로 자동 재배정해 반환한다. 프로젝트가 특정 봇ID에 종속돼
    깨지지 않게(봇은 자유롭게 넣고 뺄 수 있고, 기존 프로젝트는 유지). 우선순위: 옛 리더와 같은 직군 >
    아무 가용 봇(특정 직군 선호 하드코딩 없음 — 도메인 중립). 재배정은 영속(projects.json). 멀티봇 협업
    구조엔 영향 없음 — 리더 1명만 정하고 팀은 흐름이 현재 로스터에서 다시 꾸린다(복잡한 일=협업 그대로)."""
    if not proj:
        return None
    lead = proj.get("leader")
    if lead and lead in sys.bot_info:
        return lead   # 유효(연결돼 있음) — 그대로
    # 무효(해고/예비환원/미연결) → 재배정해 프로젝트를 살린다
    old_role = str(sys.bot_info.get(lead, "") or "") if lead else ""
    avail = [b for b in sys.bot_info if not str(sys.bot_info.get(b, "")).startswith("예비")]
    pick = next((b for b in avail if old_role and sys.bot_info.get(b) == old_role), None)
    if pick is None:
        pick = avail[0] if avail else lead   # 같은 직군 없으면 아무 가용 봇(특정 직군 선호 하드코딩 제거)
    if pick and pick != lead:
        sys._log("project_leader_reassigned", project=proj.get("id"), old=lead, new=pick,
                 reason="리더 봇 부재(해고/미연결) — 프로젝트 유지 위해 재배정")
        proj["leader"] = pick
        try:
            sys._save_projects()
        except Exception:
            pass
    return pick or lead
