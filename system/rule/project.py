"""[Project Rule] 프로젝트(산출물)의 배포 신원·타겟 적합성 규칙 — 원래 설계(REWORK_DESIGN §7
rule/project.py) 복원. 잘못된 구현이 guide_tools에 병합했던 Project 배포 규칙을 되돌린다:
① 배포 서비스명 = 프로젝트 식별번호(P-번호)로 결정적(작명 사고·슬롯 하이재킹 차단),
② 배포 타겟(Render Node) 적합성(런타임에 Python spawn/exec하는 구조 사전 차단).
guide_tools가 re-export해 기존 소비처(sys_core·tests)는 그대로 동작."""
import json
import os
import re


# (그 전에 토큰·빌드 낭비) → *첫 배포 전에* 구조적으로 잡고 명확한 처방을 준다. (빌드타임 학습용 Python은 OK —
# 런타임 spawn/exec와 start 커맨드의 Python 실행만 차단.) 오발 최소: spawn/exec에 python/pip가 *명시*될 때만.
_RUNTIME_PY_RE = re.compile(
    r"(?:spawn|exec|execSync|execFile|fork)\s*\(\s*[`'\"]\s*(?:python|pip|gunicorn|uvicorn|flask|streamlit)",
    re.IGNORECASE)
_PY_START_RE = re.compile(r"\b(?:python3?|pip|gunicorn|uvicorn|flask|streamlit|fastapi|conda)\b", re.IGNORECASE)


def _deploy_infeasibility(workspace) -> str:
    """배포 타겟(Render Node)에서 못 뜨는 구조면 사유 문자열, 아니면 ''. ① package.json start/main이 Python류를
    직접 실행하나 ② .js 서버가 런타임에 python/pip를 spawn/exec 하나. (Python을 빌드타임 학습에만 쓰는 건 통과.)"""
    ws = str(workspace or "")
    if not ws or not os.path.isdir(ws):
        return ""
    pj = os.path.join(ws, "package.json")
    if os.path.isfile(pj):
        try:
            with open(pj, encoding="utf-8") as fh:
                data = json.load(fh)
            start = str((data.get("scripts") or {}).get("start") or "") + " " + str(data.get("main") or "")
            if _PY_START_RE.search(start):
                return f"package.json의 start/main이 Python류를 실행합니다('{start.strip()[:60]}')."
        except Exception:
            pass
    for root, dirs, files in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "dist", "build", ".next", "__pycache__")]
        for fn in files:
            if not fn.endswith((".js", ".mjs", ".cjs", ".ts")):
                continue
            try:
                with open(os.path.join(root, fn), encoding="utf-8", errors="ignore") as fh:
                    m = _RUNTIME_PY_RE.search(fh.read())
            except Exception:
                continue
            if m:
                return f"{os.path.relpath(os.path.join(root, fn), ws)}가 런타임에 Python을 spawn/exec 합니다('{m.group(0)[:40]}…')."
    return ""


def deploy_service_name(flow, arg_name: str = "") -> str:
    """배포 서비스명 결정 — [멀티 프로젝트] 등록 프로젝트는 식별번호(P-번호)로 **결정적으로**
    정한다: 같은 프로젝트는 늘 같은 서비스, 다른 프로젝트는 다른 서비스. 미등록 흐름은 슬롯이
    **없다**("") — 배포 신원은 프로젝트가 보증한다(사용자 설계 확인 2026-06-12: 배포는
    프로젝트마다. 과거의 DEPLOY_NAME env 폴백은 미등록 배포를 공유 슬롯(P-002 라이브 겸용
    todo-organt-demo)으로 보내 덮어쓰기 위험을 남겼었다). 에이전트 임의 명명(arg_name)은
    등록·미등록 어디서도 슬롯이 되지 못한다(작명 사고 차단)."""
    pname = getattr(flow, "project_name", None)
    pid = getattr(flow, "project_id", None)
    if pid:
        # [신원=번호] 등록 프로젝트의 슬롯은 무조건 식별번호 — 이름 슬러그를 쓰면 일반명사 이름이
        # 충돌할 때 다른 작품이 같은 슬롯을 덮어쓴다(라이브: 지진·모션이 대기질 슬롯을 연쇄 점유).
        return f"organt-{str(pid).lower()}"
    if pname:
        slug = re.sub(r"[^a-z0-9-]", "-", str(pname).lower()).strip("-")[:40]
        if slug:
            return f"organt-{slug}"
    return ""


async def create_project(flow, args):
    """[Project Rule 로직] create_project 도구의 규칙 — 전용 채널 생성 + 팀 배정 + 레지스트리 등록.
    @tool 래퍼(guide_tools)가 _ok로 감싸므로 여기선 평문 문자열 반환(rule→guide_tools 순환 회피).
    flow는 duck-typed(guide·guild_id·pool·leader·register_project·project_* 보유)."""
    from .communication import _resolve_members, _uniq
    g = flow.guide
    if flow.project_channel is not None:
        return (f"이미 project_channel={flow.project_channel} (project_id={flow.project_id}) — "
                f"개입 중이면 create_project 말고 바로 작업하세요.")
    # [방어] 봇이 이름 앞에 'P-번호'를 끼워넣으면 떼어낸다 — 식별번호는 시스템(register_project)이
    # 자동 부여하는데, 포트폴리오 목록의 'P-NNN' 표기를 흉내 내 이름에 번호를 박으면 채널 이름이
    # 'P-021 …'이 되고 작업공간 폴더가 'p-021-p-021-…'로 번호가 중복된다. 번호는 시스템 몫이다.
    _raw = str(args.get("name") or "").strip()
    _clean = re.sub(r"^\s*[Pp]-\d+[\s:·\-–—.]*", "", _raw).strip()
    args["name"] = _clean or _raw
    flow.project_channel = await g.create_project_channel(flow.guild_id, args["name"])
    # [작업공간 격리·신원] 폴더는 여기서 깎지 않는다 — 흐름은 시작부터 고유 임시 폴더(new-…)에서
    # 일했고, 아래 register_project가 그 폴더를 **식별번호 이름(p-00n-슬러그)으로 개명**해
    # 신원을 번호로 확정한다(리더 작명 충돌이 폴더·배포 수준에서 무해 — 사용자 제안).
    assigned = _resolve_members(args.get("team", ""), flow, flow.pool)
    if assigned:
        flow.project_team = _uniq([flow.leader] + assigned)
    # 프로젝트는 내부 레지스트리에만 등록(채널 자체가 프로젝트 식별자 — 채널에 앵커 안 박음).
    flow.project_name = args["name"]   # 배포 슬롯 유도용(프로젝트별 결정적 서비스명)
    if flow.register_project:
        flow.project_id = flow.register_project(flow.project_channel, args["name"])
    return (f"project_channel={flow.project_channel} project_id={flow.project_id} "
            f"프로젝트팀={flow._names(flow.project_team)}")


async def send_file(flow, me_id, args):
    """[Rule 로직] send_file — guide_tools에서 이관(평문 반환, @tool이 _ok 래핑)."""
    from .._util import _dbg, _ok
    import os
    g = flow.guide
    rel = str(args.get("path", "")).strip()
    if not rel:
        return _ok("오류: path가 비었습니다(작업공간 기준 상대경로를 주세요).")
    ws = getattr(flow, "workspace", None)
    if not ws:
        return _ok("전송 불가: 작업공간이 없습니다.")
    # [보안] 작업공간 안의 파일만 — 경로 탈출(../)·시스템 경로 차단
    base = os.path.realpath(str(ws))
    full = os.path.realpath(os.path.join(base, rel))
    if not (full == base or full.startswith(base + os.sep)):
        return _ok("전송 거부: 작업공간 밖 경로는 보낼 수 없습니다 — 작업공간 기준 상대경로를 주세요.")
    if not os.path.isfile(full):
        return _ok(f"전송 거부: 그런 파일이 없습니다 — {rel}(작업공간 기준). run으로 만든 뒤 보내세요.")
    sz = os.path.getsize(full)
    if sz > 25 * 1024 * 1024:
        return _ok(f"전송 거부: {sz // (1024 * 1024)}MB — Discord 첨부 한도(25MB) 초과. 큰 산출물은 "
                   f"deploy(배포 URL)로 전달하세요.")
    try:
        await g.send_file(flow.user_channel, full, sender_id=me_id,
                          caption=str(args.get("caption", "")))
    except Exception as e:
        return _ok(f"파일 전송 오류: {e}")
    if flow.log:
        flow.log("file_sent_to_user", path=rel, size=sz, seg=getattr(flow, "leader_segment", 0))
    _dbg(f"[FILE→user] {me_id} {rel} ({sz}B)")
    return _ok(f"파일 전송됨 → 사용자: {rel} ({sz // 1024}KB). 사용자가 Discord에서 직접 받습니다.")


async def deploy(flow, args, me_id=None):
    """[Rule 로직] deploy — guide_tools에서 이관(평문 반환, @tool이 _ok 래핑)."""
    from .._util import _ok, _speech_clip
    from .task import _ckpt
    import anyio
    import asyncio
    import os
    import time
    g = flow.guide
    # [배포 폴링 차단] 재호출은 '점검'이 아니라 새 배포 트리거(git push+빌드 리셋)다 — 빌드가
    # 길어지면 리더가 1분마다 deploy를 다시 불러 빌드를 계속 리셋하는 자기 영속 루프 + 같은 턴
    # 병렬 4연발이 라이브 관측됨([안내][배포] 도배). 흐름당 동시 1회만, 진행 중엔 [대기].
    if getattr(flow, "deploy_inflight", False):
        return _ok("[대기] 배포가 이미 진행 중입니다 — deploy를 다시 부르지 마세요(재호출은 점검이 "
                   "아니라 **새 배포를 또 트리거**해 빌드를 계속 리셋합니다). 진행 중인 배포의 "
                   "성공/실패 결과가 곧 이 도구의 응답으로 돌아옵니다 — 그때 판단하세요.")
    # [런어웨이 배포 차단 — 횟수 상한(2026-06-21 라이브 P-028: 깨진 배포를 코드 바꿔가며 23회 재배포한 루프)]
    # [재배포 = 새 정보 요구 — 카운터 폐지(2026-07-08, 사용자: '5회 같은 하드코딩 반창고 말고 원리로')]
    # 종전 '흐름당 5회' 캡은 임의 숫자라 정당한 반복(매 배포 사이 검증이 실결함을 잡음 — 라이브 P-005)을
    # 6회째에 막아 정직 마감을 예외 보고로 강등시켰다. 런어웨이(P-028: 밤새 23회)와 정당 반복의 기계적
    # 차이는 횟수가 아니라 **배포 사이 독립 검증의 유무**다 — 그래서 규칙은: 재배포(2회차+)는 직전 배포
    # 이후 *다른 봇의 검증*(cross_checks 증가)이 있어야 한다. 맹목 스핀은 2회째에 즉시 잡히고(23회까지
    # 갈 것 없이), 검증 낀 반복은 무제한. 동료가 정말 없는 팀은 요구 불가라 면제(교차검증 게이트와 동일
    # 예외 패턴 — '제3멤버 있는 한 우회 없음, 없을 때만 예외'). 트레이드오프: 솔로 브링업도 반복마다
    # 동료 검증 1턴이 필요해짐 — 교차검증 의무와 같은 원리의 비용이라 수용(사용자 방향).
    _cc_now0 = int(getattr(flow.current, "cross_checks", 0) or 0) if flow.current is not None else 0
    _cc_at0 = int(getattr(flow, "_deploy_cross", -1))
    _peers = ([m for m in getattr(flow.current, "team", []) if m != flow.leader]
              if flow.current is not None else [])
    if (getattr(flow, "_deployed_once", False) and _cc_at0 >= 0
            and _cc_now0 <= _cc_at0 and _peers):
        if flow.log:
            flow.log("deploy_blind_held", cross=_cc_now0)
        # SYS 강제배포(_ensure_deploy)가 이 보류를 우회하지 않게 — 종전 deploy_capped와 같은 하류 의미.
        flow.deploy_capped = True
        _ckpt(flow)
        return _ok(
            "[재배포 보류 — 직전 배포 이후 독립 검증 0] 배포를 반복하기 전에 **다른 동료가 라이브/산출물을 "
            "실제로 검증**해야 합니다(맹목 재배포는 같은 결과를 반복하며 빌드·토큰만 태웁니다 — 검증이 "
            "새 정보를 줘야 다음 배포가 의미 있음). ① 동료에게 request(Work)로 라이브 검증을 맡기고 그 "
            "결과를 받아 고친 뒤 배포하세요(검증이 오르면 이 보류는 자동 해제). ② 검증으로도 못 푸는 "
            "배포 구조 문제(타겟 불일치 등)면 complete_task에 '배포 구조 문제: <원인>'을 정직하게 적어 "
            "사용자에게 보고하세요.")
    flow.deploy_capped = False   # 검증이 올랐거나 첫 배포 — 보류 해제(상태 기반, 영구 잠금 아님)
    # [배포 반-스래싱 — 변경 없는 재배포 차단(2026-06-21 라이브 P-026: 리더가 18회 재배포로 30분 낭비)]
    # Render 무료 빌드는 60s+라 deploy_sync가 *타임아웃*으로 보여도 빌드는 계속 진행된다. 리더가 '실패했나'
    # 싶어 코드 변경 없이 재배포하면 빌드를 처음부터 리셋해 *더 느려진다*(자기영속 thrash — deploy_inflight는
    # *동시*만 막고 *순차* 재배포는 못 막음). 직전 배포 이후 Write/Edit가 0이면(=같은 코드) 차단하고 'URL을
    # curl 확인하라'로 돌린다 — 진짜 결함을 고쳤으면 writes가 늘어 통과(교차검증 cc_held와 같은 정신, deploy판).
    _dwrites = sum((getattr(flow, "writes_by_role", None) or {}).values())
    if getattr(flow, "_deployed_once", False) and _dwrites == getattr(flow, "_deploy_writes", -1):
        if flow.log:
            flow.log("deploy_thrash", writes=_dwrites)
        return _ok("[재배포 차단 — 직전 배포 후 코드 변경 없음] Render 무료 빌드는 60초+라 deploy가 "
                   "*타임아웃*으로 보여도 **빌드는 계속 진행 중**입니다. 변경 없이 재배포하면 빌드를 처음부터 "
                   "리셋해 *더 느려집니다*(라이브 P-026: 18회 재배포로 30분 낭비). **재배포하지 말고 ~90초 뒤 "
                   "라이브 URL을 `run`으로 curl 확인**하세요(HTTP 200이면 완료 — 그 URL을 그대로 보고). 정말 "
                   "결함을 고쳤다면(Write/Edit) 그 변경 후에 1회만 재배포하세요.")
    name = deploy_service_name(flow, args.get("name", ""))   # 프로젝트별 결정적 서비스명
    if not name:
        return _ok("배포 불가: 미등록 흐름은 배포 슬롯이 없습니다 — 배포는 프로젝트마다"
                   "(P-번호 슬롯, organt-p-00n) 설정됩니다. create_project로 등록한 뒤 "
                   "다시 배포하세요.")
    # [BYO 자격증명 — 소유자 금고에서(2026-06, 구조 수정)] 배포 키를 전역 env가 아니라 *프로젝트
    # 소유자의 금고*에서 가져온다(매체가 지원하면 — SNS는 owner PersonSecret, Discord는 메서드 없어 env 폴백).
    # 각 사용자가 자기 키로 자기 프로젝트를 배포(env 땜빵 제거). 금고 키가 env를 우선(소유자 계정 배포).
    gh, ghu = os.environ.get("GH_PAT"), os.environ.get("GH_USER")
    rk, owner = os.environ.get("RENDER_KEY"), os.environ.get("RENDER_OWNER")
    _vault_env = {}          # 금고 전체(AWS_*·GCP_* 등 임의 키 포함) — script provider가 env로 주입
    _vault = getattr(getattr(flow, "guide", None), "deploy_creds", None)
    if _vault:
        try:
            vc = await _vault(getattr(flow, "project_channel", None)) or {}
            _vault_env = {str(k): v for k, v in vc.items() if v is not None}
            gh = vc.get("GH_PAT") or gh
            ghu = vc.get("GH_USER") or ghu
            rk = vc.get("RENDER_KEY") or rk
            owner = vc.get("RENDER_OWNER") or owner
        except Exception as _e:
            if flow.log:
                flow.log("deploy_creds_vault_err", err=str(_e)[:120])
    # [구분 없음(2026-07-08 사용자: "구분하지 말라고 그냥")] 봇은 타겟을 고르지 않는다 —
    # 그냥 배포하거나(기본), 특별한 배포 방법이 있으면 command로 그 셸 명령만 준다.
    # command 있음 = 봇이 준 방법 그대로(AWS/GCP/Fly/뭐든), 없음 = 기본(자체 서버, env로 변경 가능).
    _command = str(args.get("command") or "").strip()
    _url = str(args.get("url") or "").strip()
    _default_target = (os.environ.get("ORGANT_DEPLOY_TARGET") or "vps").strip().lower()
    _target = "script" if _command else _default_target
    _creds_ok = bool(gh and ghu and rk and owner) if (_target == "render" and not _command) else True
    if not _creds_ok:
        # [하드블록 — 스핀 차단(2026-06, 사용자)] 자격증명 없음은 봇이 *코드로 못 푸는 인프라 벽*이다.
        # 종전엔 봇이 재검증·재시도만 반복(act_count↑=가짜 진행)해 며칠씩 루프하다 무진행 컷났다
        # (라이브 fps: 배포-자격증명 벽에서 121메시지·4.5일). 막힘을 카운트해 2회째엔 흐름을 '하드블록'
        # 표시 → 이어가기 루프가 멈춰(사람에게 넘기고) 깔끔히 종결한다.
        flow._deploy_block_n = getattr(flow, "_deploy_block_n", 0) + 1
        if flow._deploy_block_n >= 3:        # 안내를 무시하고 같은 배포만 3회 반복 = 헛돎 → 최후 종결
            flow._hard_blocked = "배포 자격증명 미설정 — 소유자 금고에 키 필요(사람 조치)"
        return _ok("배포가 자격증명에서 막혔습니다. **같은 배포를 다시 시도하지 마세요 — 똑같이 막혀 "
                   "헛돕니다.** 반복 말고 *문제를 다르게 푸세요*:\n"
                   "① **이미 라이브인지 먼저 확인** — 이 산출물이 이미 배포돼 있을 수 있습니다(기억이 끊겼을 수도). "
                   "라이브 URL을 run/WebFetch로 직접 열어 의도한 동작이 되면 **'배포 완료'로 판정하고 마감**하세요. "
                   "이게 가장 흔한 해결입니다.\n"
                   "② 코드/구성을 점검해 배포 *방식*을 바꿔 풀 수 있는지 보세요(타겟 호환 등).\n"
                   "③ 자격증명 자체가 없으면 그건 소유자가 금고(설정→환경변수)에 RENDER_KEY·GH_PAT·GH_USER·"
                   "RENDER_OWNER를 넣어야 얻어집니다 — 그 *구체적 한 가지*만 명확히 요청하세요.\n"
                   "**재검증을 반복하는 건 진행이 아닙니다. ①부터 실제로 해보고, 다른 길을 찾으세요.**")
    if not getattr(flow, "workspace", None):
        return _ok("배포 불가: 작업공간이 없습니다.")
    # [배포 타겟 호환 사전검증 — 첫 배포 전에(2026-06-22 P-028)] Render Node 런타임엔 Python이 없다 —
    # 서버가 런타임에 Python을 spawn하면 라이브에서 502로 죽는다. 5회 상한(사후)이 아니라 *지금* 잡아
    # 명확한 처방을 준다(토큰·빌드 낭비 차단). 빌드타임 학습용 Python은 통과 — 런타임 의존만 차단.
    _infeasible = _deploy_infeasibility(flow.workspace) if (_target == "render" and not _command) else ""
    if _infeasible:
        if flow.log:
            flow.log("deploy_infeasible", reason=_infeasible[:80])
        return _ok(
            f"배포 불가(타겟 호환 — Render Node 전용): {_infeasible} 배포 타겟은 **Render Node 런타임 "
            f"하나뿐**이라 런타임에 Python이 없습니다 — 이 구조는 라이브에서 502로 죽습니다(P-028: "
            f"ECONNREFUSED, 모델 고아화). 고치는 법(해당 owner에게 위임): ① **Node로만 서빙** — Python은 "
            f"*빌드타임 오프라인 학습*에만 쓰고 예측을 미리 계산해 JSON으로 떨궈 Node가 서빙(정적/캐시), "
            f"또는 ② 모델을 **ONNX/TF.js로 변환**해 Node에서 추론. 런타임 Python spawn/exec를 제거한 뒤 "
            f"다시 배포하세요(이건 코드 수정으로 안 고쳐지는 *구조* 문제 — 재시도 말고 아키텍처를 바꾸세요).")
    from ..deploy import deploy_sync
    flow.deploy_inflight = True
    flow._deploy_writes = _dwrites         # 이 배포 시점의 저작 수 — 다음 배포가 '변경 없음'을 판정
    _dep = {"on": False}

    async def _do_deploy():
        # [논블로킹 배포 — 단일흐름 안정성(2026-06-22)] Render 빌드는 수 분(deploy_sync 폴링 480초)이라
        # 도구 호출 안에서 기다리면 75초 CLI 한도에 잘려 detach→리더가 '실패로 오인'→재배포 thrash
        # (라이브 P-026 18회·P-028 23회)의 뿌리였다. 위임과 동일하게: 즉시 반환하고 deploy_sync는
        # 인플라이트로 돌려 SYS가 호출 *밖*에서(75초 미적용·idle 720초>빌드 480초) 완주시켜 라이브 URL로
        # 리더를 재개한다. 베턴은 안 건드린다(배포는 위임 아님) — 동시 재배포는 deploy_inflight가 단속.
        async def _deploy_heartbeat():
            # [살아있음 신호 — 무진행 워치독 오컷의 근본 교정(2026-06, 사용자)] Render 빌드(deploy_sync
            # 폴링 ~480초+)가 도는 동안엔 도구·메시지가 없어 last_activity가 침묵한다 → idle 워치독이
            # '행'으로 오인해 *잘 돌아가던 배포 흐름을 정지*시켰다. 특히 동시 배포가 여럿이면 Render가
            # 경합으로 느려져 빌드가 12~20분을 넘겨 '12분째 진행 없음 → 정지'가 났다(1개만 통과하던 증상).
            # 빌드 진행 중 주기적으로 시계를 갱신해 '침묵=죽음' 오판을 없앤다(빌드가 길어도·여러 개여도 안전).
            try:
                while True:
                    await asyncio.sleep(30)
                    flow.last_activity = time.monotonic()
            except asyncio.CancelledError:
                pass
        _hb_task = asyncio.ensure_future(_deploy_heartbeat())
        try:
            if _command:                        # 봇이 준 배포 방법 그대로 실행(플랫폼 무관)
                from functools import partial
                _cfg = {"command": _command, "url": _url, "env": _vault_env}
                _dep_fn = partial(deploy_sync, flow.workspace, name, gh, ghu, rk, owner,
                                  target="script", config=_cfg)
                r = await anyio.to_thread.run_sync(_dep_fn)
            elif _default_target != "vps":          # 전역 기본이 따로 있으면(예: render) 그걸로
                from functools import partial
                _dep_fn = partial(deploy_sync, flow.workspace, name, gh, ghu, rk, owner,
                                  target=_default_target, config={"env": _vault_env})
                r = await anyio.to_thread.run_sync(_dep_fn)
            else:                                    # 기본 — 자체 서버(종전 시그니처 호환)
                r = await anyio.to_thread.run_sync(deploy_sync, flow.workspace, name, gh, ghu, rk, owner)
        except Exception as e:
            r = f"배포 처리 오류: {e}"
        finally:
            _hb_task.cancel()
        flow.deploy_inflight = False
        flow.deployed = r                  # 배포 호출됨 기록(SYS의 배포 강제가 중복 안 하게)
        # [배포 목표 달성 영속(2026-07, 사용자: '재개돼도 fix가 켜져야')] 라이브 검증까지 통과한 '배포 성공'은
        # 배포-목표 달성 신호다 — *영속 플래그*로 남긴다. flow.deployed(문자열)는 체크포인트에 안 실려 재개
        # 흐름에서 사라지므로, 그것만 보던 완료-인식(무한 QA 차단)은 restore된 흐름(러너 재시작·복구)에선
        # 안 먹었다. _deploy_live는 sys_recovery가 영속·복원해 재개 후에도 완료 인식이 유지된다.
        if isinstance(r, str) and r.startswith("배포 성공"):
            flow._deploy_live = True
        # [런어웨이 cap 정정(2026-07, 사용자: 'cap을 고쳐 배포 되게')] cap의 목적은 *깨진 앱을 계속
        # 재배포*(P-028: push는 성공했으나 앱이 깨진 채 23회) 차단이다. push/설정 단계 실패(계정·리포·
        # 인증·크기·비-일시)는 *앱까지 가보지도 못한 설정 문제*라 고치면 성공한다 — 이걸 앱-런어웨이 cap에
        # 세면 설정을 고친 뒤에도 영구 차단된다(라이브 P-003: GH_USER·181MB로 push 5회 실패 → 둘 다
        # 고쳤는데 cap이 막음). *실제 push까지 간* 배포만 cap·thrash에 센다.
        _push_stage_fail = isinstance(r, str) and (
            r.startswith("배포 실패(git push)") or r.startswith("배포 실패(GitHub repo)")
            or r.startswith("배포 실패(비-일시") or r.startswith("배포 처리 오류"))
        if not _push_stage_fail:
            flow._deployed_once = True
            # [캡 = 맹목 스핀만(2026-07-08, 사용자: '정당한 반복 수정 배포가 캡에 막혀 예외로 닫힘 — 재발
            # 방지')] 캡의 표적(P-028)은 *검증 없이* 배포를 반복하는 런어웨이다. 직전 배포 이후 **독립
            # 교차검증이 있었던** 배포는 '검증이 잡은 결함을 고쳐 다시 내는' 정당한 반복이라 캡에 안 센다
            # (라이브 P-005: 매 배포 사이 QA/PM 검증이 실결함을 잡았는데 5회 캡이 6번째 정당 배포를 차단 →
            # 정직 마감이 '배포 구조 문제' 예외 보고로 강등). 맹목 스핀(검증 0 사이 재배포)은 종전대로 센다.
            _cc_now = int(getattr(flow.current, "cross_checks", 0) or 0) if flow.current is not None else 0
            _cc_at = int(getattr(flow, "_deploy_cross", -1))
            if _cc_at >= 0 and _cc_now > _cc_at:
                if flow.log:
                    flow.log("deploy_verified_iteration", cross=_cc_now)   # 검증 주도 반복 — 캡 미과금
            else:
                flow._deploy_count = getattr(flow, "_deploy_count", 0) + 1   # 런어웨이 상한 — 검증 없는 재배포만
            flow._deploy_cross = _cc_now
        _ckpt(flow)   # [즉시 영속(2026-06-23 전수감사)] deploy_count는 Task 전이 사이에 증가하므로
                      # 전이 때만 찍던 스냅샷은 stale → 죽으면 cap이 0으로 리셋돼 무한 재배포. 배포 즉시 영속.
        if _dep["on"]:
            flow.detached_results.append(f"배포 결과 → {_speech_clip(r, 4000)}")
            # [배포 결과 피드 가시화(2026-07-08, 사용자: '트리거됐다 하고 끝났네')] detached 배포는 결과가
            # 어느 봇 턴에도 안 실려 피드에 '트리거됨'만 남고 '라이브 확인'이 증발했다 — 관찰자에겐 미완으로
            # 보임. 결과 도착 즉시 SYS 명의(0)로 스레드에 게시(동기 배포는 봇 보고에 실리므로 제외 — 중복 0).
            try:
                if flow.current is not None:
                    # 피드엔 헤드라인만(첫 줄). [배포=발언(2026-07-08, 사용자: '배포도 검증 과정의 발언')]
                    # 무주체 SYS 구분선이 아니라 **배포를 호출한 봇 명의**로 흐름에 들어간다 — 검증 과정의
                    # 일부로 읽히고, 이후 의견이 이 발언에 자연히 달린다(순차 부착).
                    _head = (r or "").splitlines()[0] if r else ""
                    _note = str(args.get("note") or "").strip()
                    _why = f"{_speech_clip(_note, 160)} — " if _note else ""
                    await flow.guide.post(int(flow.current.thread_id), int(me_id or 0),
                                          f"[배포 결과] {_why}{_speech_clip(_head, 200)}")
            except Exception:
                pass
        return _ok(r)

    inner = asyncio.ensure_future(_do_deploy())
    flow.inflight_tasks.add(inner)
    inner.add_done_callback(flow.inflight_tasks.discard)
    if getattr(flow, "_handoff", False):
        _dep["on"] = True
        return _ok("[배포 트리거됨 — SYS가 빌드가 라이브가 될 때까지 확인해 그 결과(라이브 URL)로 당신을 "
                   "재개합니다. **재배포·추가 도구 호출 없이 이 턴을 마치세요** — 재배포는 빌드를 처음부터 "
                   "리셋합니다. 단, **턴을 마치는 보고에 이번에 무엇을 왜 바꿨는지(파일·위치·로직)는 반드시 "
                   "포함**하세요 — '배포 트리거됨' 공지가 작업 보고를 대체하지 않습니다(검증자가 뭘 재검증할지 "
                   "알아야 함). 라이브 URL 확인은 결과가 도착한 뒤에.]")
    try:
        return await asyncio.shield(inner)
    except asyncio.CancelledError:
        if not inner.done():
            _dep["on"] = True       # 도구 호출만 죽고 배포는 계속 — 결과는 detached로 전달
        raise
