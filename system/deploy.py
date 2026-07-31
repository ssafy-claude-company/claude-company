"""산출물 공개 배포 — GitHub repo push + 웹 서빙(기본: 이 VPS, 옵션: Render).

Guide의 `deploy` 리더 툴이 호출한다. 자격증명은 환경변수/금고로 주입한다(코드/로그에 박지 않음):
  GH_PAT, GH_USER (+Render 타겟일 때만 RENDER_KEY, RENDER_OWNER)
Node 앱(서버가 process.env.PORT 사용) 또는 정적 산출물(public/·index.html)을 지원한다.
같은 name으로 다시 부르면 갱신 배포한다.

[Render 종속 제거 — 2026-07-08 사용자 방향] 기본 타겟 = vps: 산출물을 이 VPS의
ops/var/organt_apps/<name>/으로 복사해 로컬 포트에 기동하고, murmur 게이트웨이
(/apps/<name>/)가 서빙한다. ORGANT_DEPLOY_TARGET=render로 되돌릴 수 있다(롤백 한 줄).
"""
import json
import os
import re
import shutil
import signal
import subprocess
import ipaddress
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


def _url_safe(url: str) -> bool:
    """[SSRF 가드 — REVIEW M4] 배포결과 URL 자동 fetch/goto 전 검증. http(s) 스킴만 허용하고,
    호스트가 해석되는 *모든* IP가 공인이어야 통과(사설·루프백·링크로컬·예약·멀티캐스트·클라우드
    메타데이터 169.254.169.254 차단). 배포 URL은 Render 응답이나 name/repo가 봇 입력에서 오므로 방어."""
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        port = u.port or (443 if u.scheme == "https" else 80)
        for _fam, _t, _p, _c, sockaddr in socket.getaddrinfo(u.hostname, port, proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(sockaddr[0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                    or ip.is_multicast or ip.is_unspecified):
                return False
        return True
    except Exception:
        return False

GITHUB_API = "https://api.github.com"
RENDER_API = "https://api.render.com/v1"
_TERMINAL_FAIL = ("build_failed", "update_failed", "canceled", "deactivated", "pre_deploy_failed")
# [데모 인프라 보호] 슬롯 확보용 '고아 서비스' 자동삭제가 *절대* 건드리면 안 되는 이름(데모 앱·러너 API 등).
# organt-sns 자체는 채널이 참조하지 않아 '고아'로 오인돼 삭제될 수 있다 — 그러면 데모·러너가 통째로 죽는다.
# env ORGANT_PROTECT_SERVICES(쉼표구분)로 추가 가능. 기본은 organt-sns.
_PROTECT = {s.strip() for s in (os.environ.get("ORGANT_PROTECT_SERVICES") or "organt-sns").split(",") if s.strip()}


def _mask_secret(text, *secrets):
    """에러/로그 문자열에서 자격증명(PAT 등)을 마스킹한다 — 토큰 박힌 remote URL이 에러 메시지로 새는 것
    방지(보안 핫픽스 2026-06)."""
    s = str(text or "")
    for sec in secrets:
        if sec and isinstance(sec, str) and len(sec) >= 8:
            s = s.replace(sec, "***")
    return s


_MAX_FILE = 100 * 1024 * 1024   # GitHub 파일당 하드 거부 임계(100MB)


def _oversized_files(stage):
    """스테이징된(git add=.gitignore 반영) 파일 중 GitHub 100MB 한도 초과 → [(rel, size)].
    push 전에 검사해 크립틱한 거부 대신 명확히 안내하기 위함(deploy는 매번 fresh init이라 워킹트리 크기만 본다)."""
    rc, out = _git(["ls-files", "-z"], str(stage))
    if rc != 0:
        return []
    big = []
    for rel in out.split("\0"):
        rel = rel.strip("\0 \n\r\t")
        if not rel:
            continue
        try:
            sz = os.path.getsize(os.path.join(str(stage), rel))
        except OSError:
            continue
        if sz > _MAX_FILE:
            big.append((rel, sz))
    return big


def _http(method, url, token, data=None, retries=5):
    """응답을 못 받은 경우(네트워크/DNS 실패, 502/503/504 게이트웨이)에만 안전 재시도.
    egress 프록시의 api.render.com DNS 해석이 간헐 실패하므로(요청이 서버에 도달조차 못 함),
    비멱등 POST(배포 트리거)라도 재시도가 안전하다. 서버가 실제 응답한 4xx/유효 5xx는 즉시 반환."""
    body = json.dumps(data).encode() if data is not None else None
    last = ""
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        if body:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or "{}")
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < retries - 1:   # 일시적 게이트웨이/프록시(DNS) — 재시도
                last = f"HTTP {e.code}"
                time.sleep(2 * (attempt + 1))
                continue
            try:
                return e.code, json.loads(e.read() or "{}")
            except Exception:
                return e.code, {}
        except Exception as e:                       # 네트워크/DNS 실패(응답 못 받음) — 재시도
            last = str(e)
            time.sleep(2 * (attempt + 1))
    return 0, {"error": last}


def _git(args, cwd):
    # [dubious ownership 차단] 작업공간 repo는 run 툴이 샌드박스 실행용으로 비특권 유저(nobody)에게
    # chown한다(_chown_tree) — 그럼 배포 git(러너=root/다른 유저)이 'detected dubious ownership'로
    # 거부해 push가 안 된다(라이브 P-005: 봇이 'git 인프라 hiccup'으로 오인해 무한 재배포). safe.directory=*
    # 로 소유자 불일치와 무관하게 이 트리를 신뢰(배포는 우리 인프라의 작업공간이라 안전).
    cmd = ["git", "-c", "safe.directory=*", "-c", "commit.gpgsign=false",
           "-c", "user.email=deploy@organt.local", "-c", "user.name=Organt Deploy", *args]
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


def _check_live(url, tries=6):
    """배포된 URL이 실제로 응답하는지 확인(콜드스타트 감안 재시도) → HTTP 코드 또는 None."""
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code           # 4xx/5xx도 서버가 응답한 것(라우팅은 됨)
        except Exception:
            time.sleep(8)           # 콜드스타트/미기동 — 재시도
    return None


def _verify_live_assets(url, workspace, limit=12, tries=3, wait=6, fetch=None):
    """[구조 검증 — 스테일 배포 차단] '배포 성공'을 선언하기 전에, 라이브가 **방금 만든 그 파일**을
    서빙하는지 바이트 대조로 증명한다. URL 200은 '서버가 떠 있다'까지만 보증한다 — 옛 빌드가
    캐시/이전 배포로 서빙되는데 '배포 완료'로 보고되던 부류(라이브 관측: 클라 수정이 라이브에
    안 보임 → 사용자 재보고)를 도구 레벨에서 원천 차단한다. 대조 대상 = 클라이언트가 실제로 받는
    public/* 정적 파일(서버 코드는 비서빙이라 대조 불가). public/ 없는 산출물(순수 API 서버 등)은
    생략. 직후 전파 지연을 감안해 재시도 후에도 다르면 불일치 목록을 반환(비면 통과)."""
    pub = Path(workspace) / "public"
    if not pub.is_dir():
        return []
    if fetch is None and not _url_safe(url):
        return []   # [SSRF 가드] 사설·비http URL은 자동 fetch 안 함(공인 배포 URL만 대조)
    if fetch is None:
        def fetch(u):
            req = urllib.request.Request(u, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
    names = sorted(p.name for p in pub.iterdir() if p.is_file())[:limit]
    bad = []
    for attempt in range(tries):
        bad = []
        for nm in names:
            local = (pub / nm).read_bytes()
            try:
                live = fetch(f"{url.rstrip('/')}/{nm}")
            except Exception as e:
                bad.append(f"{nm}(조회 실패: {str(e)[:60]})")
                continue
            if live != local:
                bad.append(f"{nm}(라이브 {len(live)}B ≠ 산출물 {len(local)}B)")
        if not bad:
            return []
        if attempt < tries - 1:
            time.sleep(wait)      # 새 인스턴스/엣지 전파 직후의 일시 불일치 — 잠시 뒤 재대조
    return bad


def _measure_usability(url: str) -> str:
    """[품질 우선 — 기계적 사용성 측정(사용자 확정: 토큰<품질)] 배포 성공 후 실제 브라우저로 첫
    로드를 재본다 — 웹 산출물에서 '뜬다(HTTP 200)'와 '쓸 만하다'는 다르다(라이브 P-009: 200인데
    첫 로드 60s+, 브라우저 즉석 모델학습 렉을 200 검사가 통과시킴 — 사용자가 첫 발견).
    도메인 무관(웹이라는 산출물 형태에만 의존), best-effort — 측정 실패가 배포를 막지 않는다."""
    if not _url_safe(url):
        return "\n[라이브 사용성 측정] 생략 — 안전하지 않은 URL(사설·루프백·비http) SSRF 가드."
    try:
        import time as _t
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            try:
                pg = b.new_page()
                errs = []
                pg.on("console", lambda m: errs.append(m.text[:80]) if m.type == "error" else None)
                pg.on("pageerror", lambda e: errs.append(str(e)[:80]))
                t0 = _t.time()
                try:
                    pg.goto(url, timeout=20000, wait_until="load")
                    note = f"첫 로드 {_t.time() - t0:.1f}s"
                except Exception:
                    note = "첫 로드 **20s 초과(미완)** — 사용자는 빈 화면을 봅니다"
                _t.sleep(2)   # 로드 직후 에러 수집 창
                e_note = f", 콘솔/페이지 에러 {len(errs)}건" + (f" (첫: {errs[0]})" if errs else "")
                return (f"\n[라이브 사용성 측정] {note}{e_note} — 수치가 나쁘면 '배포됨'이지 "
                        f"'완성'이 아닙니다(원인을 고치기 전 완료 보고 금지).")
            finally:
                b.close()
    except Exception as e:
        return f"\n[라이브 사용성 측정 불가(참고): {type(e).__name__}]"


def _onrender_subs(text: str) -> set:
    """문자열에서 참조되는 *.onrender.com 서비스 서브도메인(=서비스명) 집합."""
    return set(re.findall(r"https?://([a-z0-9][a-z0-9-]*)\.onrender\.com", text or ""))


def _referenced_services(projects_path=None):
    """등록 레지스트리(logs/projects.json)가 아직 참조하는 onrender 서비스명 집합(keep-set).
    '남아있는 채널이 링크로 가리키는' 서비스 — 풀 정리 시 절대 삭제 금지 대상.
    [보수 폴백(보안·정확성 핫픽스 2026-06)] 파일이 *있는데 못 읽으면*(parse 실패) None을 돌려준다 —
    빈 set으로 오인하면 *참조 중 서비스까지 고아로 보고 삭제할 위험*이 있어, 호출부가 '슬롯 정리 자체를
    건너뛰게' 한다. 파일이 아예 없으면(프로젝트 0) set()(정당한 빈 keep)."""
    p = Path(projects_path) if projects_path else (Path(__file__).resolve().parent.parent / "logs" / "projects.json")
    if not p.exists():
        return set()                                  # 프로젝트 없음 = 정당한 빈 keep
    try:
        return _onrender_subs(p.read_text())
    except Exception:
        return None                                   # 있는데 못 읽음 = '판단 불가' → 호출부가 정리 중단


def _list_render_services(render_key) -> list:
    """Render 계정의 모든 서비스 → [{id,name,url,created}] (커서 페이지네이션)."""
    out, cursor = [], None
    for _ in range(20):
        u = f"{RENDER_API}/services?limit=100" + (f"&cursor={cursor}" if cursor else "")
        st, data = _http("GET", u, render_key)
        if st != 200 or not isinstance(data, list) or not data:
            break
        for item in data:
            s = item.get("service", item)
            out.append({"id": s.get("id"), "name": s.get("name"),
                        "url": (s.get("serviceDetails") or {}).get("url", ""),
                        "created": s.get("createdAt", "")})
            cursor = item.get("cursor")
        if len(data) < 100:
            break
    return out


def _billing_suspended(render_key) -> bool:
    """Render 계정의 무료 서비스가 'billing'으로 정지됐는지 — 무료 월 인스턴스시간(750h, 전 무료 서비스
    공유)이 소진되면 Render가 전 서비스를 정지하고 신규 무료 서비스 생성도 막는다(라이브 P-021 관측:
    suspended=12 전원 suspenders=['billing'], 신규 POST /services 실패). 이건 재시도나 슬롯 정리(서비스
    '수')로 풀리지 않는 '비-일시' 차단(월 리셋·유료 전환·서비스 축소가 필요)이라, 무한 재시도 대신
    사용자에게 보고해야 한다."""
    st, data = _http("GET", f"{RENDER_API}/services?limit=100", render_key)
    if st != 200 or not isinstance(data, list) or not data:
        return False
    susp = 0
    for item in data:
        s = item.get("service", item)
        sl = s.get("suspenders") or []
        if s.get("suspended") == "suspended" and any("billing" in str(x).lower() for x in sl):
            susp += 1
    return susp >= max(2, len(data) // 2)   # 절반 이상 billing 정지 = 계정 차원 차단


def _free_slots(render_key, keep, want_free=2, cap=25) -> list:
    """[풀 자가관리 — 한도로 인한 작업 멈춤 차단] 무료 티어는 서비스 개수 상한(cap)이 있어, 풀이
    차면 신규 배포가 막혀 작업이 통째로 멈춘다(라이브 P-019: '한도 초과 → 사용자 보고로 마감').
    '현 채널이 참조하지 않는' 고아 서비스(옛 테스트·삭제된 채널·이름 중복)를 오래된 것부터 삭제해
    슬롯을 되찾는다 — keep-set(참조 중 링크)은 절대 건드리지 않는다. 슬롯이 이미 충분하면 아무것도
    안 한다(보수적: 한도 임박에서만 동작). 삭제된 서비스명 목록 반환."""
    try:
        svcs = _list_render_services(render_key)
    except Exception:
        return []
    if cap - len(svcs) >= want_free:
        return []
    orphans = [s for s in svcs
               if s["name"] not in keep and not (_onrender_subs(s["url"]) & keep)
               and s["name"] not in _PROTECT
               and not any(p and p in (s.get("url") or "") for p in _PROTECT)]   # 보호 서비스(데모 앱)는 고아여도 절대 삭제 안 함
    orphans.sort(key=lambda s: s.get("created") or "")     # 오래된 고아부터
    need = want_free - (cap - len(svcs))
    deleted = []
    for s in orphans[:max(need, 0)]:
        if not s.get("id"):
            continue
        st, _ = _http("DELETE", f"{RENDER_API}/services/{s['id']}", render_key)
        if st in (200, 204):
            deleted.append(s["name"])
    return deleted


def _final_deploy_result(url, workspace, repo_url, status,
                         check_live=None, verify=None, measure=None):
    """[배포 보고 정확성 — 라이브 P-020] 폴링 창(480s)이 끝났는데 Render 무료 티어 빌드는 그보다
    길어지기도 한다 — 빌드가 폴 창 뒤 비동기로 완료돼 라이브가 됐는데도 리더가 '미완·수동배포 필요'로
    오보(false negative)하던 문제(라이브: P-020이 멀쩡히 라이브인데 요약은 '배포 미완'). 창이 끝나면
    *한 번 더 길게* 진짜 라이브인지 확인하고(빌드가 방금 끝났을 수 있음), 그래도 아직이면 '실패'가
    아니라 '빌드 진행 중 → 곧 자동 라이브'임을 못박아 리더가 '실패/수동배포'로 오보하지 않게 한다."""
    check_live = check_live or _check_live
    verify = verify or _verify_live_assets
    measure = measure or _measure_usability
    served = check_live(url, tries=10)            # 콜드/지연 빌드 마지막 확인(더 길게 — 방금 끝났을 수 있음)
    if served:
        stale = verify(url, workspace)
        if not stale:
            return (f"배포 성공 ✅ 라이브(HTTP {served} + 산출물 일치): {url}  (repo: {repo_url})" + measure(url))
        return (f"배포 실패(스테일 서빙): 라이브 파일이 산출물과 다릅니다 — {', '.join(stale[:4])}. 옛 빌드가 "
                f"서빙 중일 수 있습니다 — 캐시·빌드 로그 확인 후 다시 배포(이 상태로 '완료' 보고 금지).")
    return (f"배포 진행 중(**실패 아님** — 빌드가 폴링 창보다 길어졌을 뿐): {url} — Render가 빌드를 마치면 "
            f"**자동으로 라이브**됩니다(보통 1~3분 더). **수동 배포하지 마세요**(이미 트리거됨). 1~3분 뒤 이 "
            f"URL을 다시 확인하면 200을 받습니다. 이걸 '실패/미완/수동배포 필요'로 보고하지 말고 **'배포 트리거 "
            f"완료 — 곧 라이브'**로 보고하세요(status={status}).")


# ══ VPS 배포 백엔드 — Render 종속 제거(2026-07-08 사용자 방향) ══════════════════
# 산출물을 이 VPS의 앱 풀(ops/var/organt_apps/<name>/)로 복사해 로컬 포트에 기동하고,
# murmur 게이트웨이(/apps/<name>/)가 공개 서빙한다. 프로세스는 detached(setsid)라 러너
# 재시작과 독립. 레지스트리(registry.json)가 이름→포트/PID의 단일 장부다.

_APPS_PORT_LO, _APPS_PORT_HI = 4100, 4199   # 앱 풀 포트 대역(로컬 전용 — 게이트웨이만 접근)


def _apps_dir() -> Path:
    from .config import ROOT
    d = Path(os.environ.get("ORGANT_APPS_DIR") or (Path(ROOT) / "ops" / "var" / "organt_apps"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def pool_live_url(name) -> str:
    """[장부가 아니라 사실을 본다(2026-07-31, U-442 실측)] 이 판의 앱이 앱 풀에 실제로 살아 있으면
    그 공개 주소를 돌려준다(아니면 빈 값). 도구를 거치지 않고(운영자·복구 스크립트) 올라간 배포도
    배포다 — '누가 올렸나'가 아니라 '지금 열리나'가 배달의 사실이기 때문이다.
    """
    nm = str(name or "").strip().lower()
    if not nm:
        return ""
    try:
        reg = _load_registry()
    except Exception:
        return ""
    entry = None
    for key in (f"organt-{nm}", nm):
        if isinstance(reg.get(key), dict):
            entry, nm = reg[key], key
            break
    if entry is None:
        # 장부에 없어도 게이트웨이가 200으로 열어 주면 그것이 사실이다(레지스트리 경로가 다른
        # 체크아웃에서 도는 경우·기록 유실 — 사람이 여는 데는 아무 지장이 없다).
        for key in (f"organt-{nm}", nm):
            _u = f"{_apps_base_url()}/{key}/"
            _st = _check_live(_u, tries=1)
            # _check_live는 4xx도 코드로 돌려준다(라우팅은 됐다는 뜻) — 배달 판정은 **2xx만**.
            if _st and 200 <= int(_st) < 300:
                return _u
        return ""
    if not entry.get("static"):
        pid_ = entry.get("pid")
        try:
            if not (pid_ and os.path.exists(f"/proc/{int(pid_)}")):
                return ""
        except (TypeError, ValueError):
            return ""
    url = f"{_apps_base_url()}/{nm}/"
    _st = _check_live(url, tries=1)
    return url if (_st and 200 <= int(_st) < 300) else ""


def _apps_base_url() -> str:
    # [서버 이전(2026-07-28)] 기본값이 구 VPS(murmur-ai.duckdns.org)를 가리켜, 봇 보고에 실린
    # 산출물 링크가 은퇴한 호스트로 갔다(실측: 1세대 보고의 works 링크). env 미설정 시 현 라이브
    # 도메인을 쓴다 — 링크는 눌러서 열리는 것이 사실이어야 한다.
    return (os.environ.get("ORGANT_APPS_BASE_URL") or "https://murmur.dojin-mini.shop/apps").rstrip("/")


def _registry_path() -> Path:
    return _apps_dir() / "registry.json"


def _load_registry() -> dict:
    try:
        return json.loads(_registry_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_registry(reg: dict) -> None:
    tmp = _registry_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_registry_path())          # 원자 교체 — 게이트웨이가 반쯤 쓴 파일을 읽지 않게


class _RegistryLock:
    """레지스트리 flock — 서로 다른 흐름의 동시 배포가 포트/장부를 밟지 않게(단일 러너라도 flow는 병렬 가능)."""

    def __enter__(self):
        import fcntl
        self._f = open(_apps_dir() / ".lock", "w")
        fcntl.flock(self._f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        self._f.close()


def _alloc_port(reg: dict, name: str) -> int:
    cur = (reg.get(name) or {}).get("port")
    if cur:
        return int(cur)                     # 재배포는 같은 포트 재사용(게이트웨이 무변경)
    used = {int(e.get("port") or 0) for e in reg.values()}
    for p in range(_APPS_PORT_LO, _APPS_PORT_HI + 1):
        if p in used:
            continue
        # [실점유 검사(2026-07-10)] 레지스트리 밖 점유(격리 테스트↔라이브 풀 교차, 레지스트리 유실)면
        # 그 포트의 남의 앱을 '내 배포'로 검증하는 오판이 난다(라이브: e2e-demo가 p-010 4100 충돌).
        import socket as _sk
        with _sk.socket() as _s:
            if _s.connect_ex(("127.0.0.1", p)) == 0:
                continue
        return p
    raise RuntimeError("앱 포트 대역(4100~4199) 소진 — 오래된 앱을 정리해야 합니다")


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _stop_app(entry: dict) -> None:
    """기존 앱 종료 — transient 유닛이면 systemctl stop, 레거시 pid면 프로세스 그룹 종료."""
    d = entry.get("dir")
    if d:
        subprocess.run(["systemctl", "stop", _app_unit(Path(d))], capture_output=True)
    pid = entry.get("pid")
    if not pid or not _pid_alive(pid):
        return
    for sig, wait in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(int(pid), sig)
        except Exception:
            return
        deadline = time.time() + wait
        while time.time() < deadline:
            if not _pid_alive(pid):
                return
            time.sleep(0.1)


def _copy_workspace(ws: Path, dst: Path) -> None:
    """산출물만 복사 — .git(히스토리)·.collab(협의 원본)·node_modules(현지 install)·로그 제외.
    dst의 기존 node_modules는 남겨 npm install 캐시로 쓴다."""
    ignore = shutil.ignore_patterns(".git", ".collab", "node_modules", "app.log", "__pycache__")
    dst.mkdir(parents=True, exist_ok=True)
    for item in ws.iterdir():
        if item.name in (".git", ".collab", "node_modules", "app.log", "__pycache__"):
            continue
        to = dst / item.name
        if item.is_dir():
            shutil.copytree(item, to, dirs_exist_ok=True, ignore=ignore)
        else:
            shutil.copy2(item, to)


def _app_unit(appdir: Path) -> str:
    import re as _re
    return "organt-app-" + _re.sub(r"[^a-zA-Z0-9-]", "-", appdir.name)[:50]


def _spawn_app(appdir: Path, port: int, start_cmd: str) -> int:
    """[수명 분리 근본 수리(2026-07-11)] start_new_session은 세션만 분리하고 **cgroup은 러너 소속** —
    systemctl restart organt-runner(KillMode=control-group)가 배포 앱을 함께 죽였다(ch53 라이브:
    '외부에서 죽는' 유령 크래시 5회의 진범). transient systemd 유닛으로 기동해 러너와 완전 분리하고
    Restart=on-failure로 자가복구(watchdog)까지 얻는다."""
    unit = _app_unit(appdir)
    subprocess.run(["systemctl", "reset-failed", unit], capture_output=True)
    subprocess.run(["systemctl", "stop", unit], capture_output=True)
    r = subprocess.run(["systemd-run", "--unit", unit, "--collect",
                        "-p", "Restart=on-failure", "-p", "RestartSec=2",
                        "-p", f"WorkingDirectory={appdir}",
                        "-p", f"Environment=PORT={port}", "-p", "Environment=NODE_ENV=production",
                        "-p", f"StandardOutput=append:{appdir}/app.log",
                        "-p", f"StandardError=append:{appdir}/app.log",
                        "/bin/sh", "-c", start_cmd], capture_output=True, text=True)
    if r.returncode != 0:
        # systemd 불가 환경(테스트 컨테이너 등) 폴백 — 종전 방식
        log = open(appdir / "app.log", "ab")
        p2 = subprocess.Popen(start_cmd, shell=True, cwd=str(appdir),
                              env={**os.environ, "PORT": str(port), "NODE_ENV": "production"},
                              stdout=log, stderr=log, start_new_session=True)
        return p2.pid
    for _ in range(20):
        out = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", unit],
                             capture_output=True, text=True).stdout.strip()
        if out and out != "0":
            return int(out)
        time.sleep(0.2)
    return 0


def _local_health(port: int, tries: int = 20) -> Optional[int]:
    """로컬 포트가 HTTP로 응답할 때까지 대기 → 상태코드(응답=산 것, 4xx도 기동은 된 것)."""
    for _ in range(tries):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            time.sleep(0.5)
    return None


def _local_fetch(port: int):
    def fetch(u):
        rel = u.split("/", 3)[-1] if u.count("/") >= 3 else ""
        req = urllib.request.Request(f"http://127.0.0.1:{port}/{rel}",
                                     headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()
    return fetch


def _push_best_effort(ws: Path, name: str, gh_pat, gh_user) -> str:
    """GitHub push(레포 공개는 유지 가치) — 자격증명 없으면 건너뛴다(VPS 서빙은 push와 무관).
    render 경로의 3)·4)단계와 같은 일을 하되, 실패가 배포를 막지 않는 best-effort."""
    if not (gh_pat and gh_user):
        return ""
    st, resp = _http("POST", f"{GITHUB_API}/user/repos", gh_pat,
                     {"name": name, "private": False,
                      "description": f"{name} — deployed by Organt Core multi-agent system"})
    if st not in (201, 422):
        return f" (repo push 생략: GitHub HTTP {st})"
    real_user = gh_user
    if st == 201 and isinstance(resp.get("owner"), dict) and resp["owner"].get("login"):
        real_user = resp["owner"]["login"]
    else:
        _who_st, _who = _http("GET", f"{GITHUB_API}/user", gh_pat)
        if _who_st == 200 and _who.get("login"):
            real_user = _who["login"]
    push_url = f"https://x-access-token:{gh_pat}@github.com/{real_user}/{name}.git"
    rc, out = _git(["push", "-q", "-f", push_url, "main:main"], str(ws))
    if rc != 0:
        return f" (repo push 실패: {_mask_secret(out, gh_pat)[-80:]})"
    return f"  (repo: https://github.com/{real_user}/{name})"


def deploy_vps_sync(workspace, name, gh_pat=None, gh_user=None):
    """workspace를 VPS 앱 풀로 배포 → 결과 문자열(라이브 URL 포함).

    Node 앱(server.js/npm start)은 로컬 포트에 기동, 정적 산출물(public/·index.html)은
    무프로세스로 게이트웨이가 직접 서빙. 검증 3단: ①로컬 포트 응답 ②로컬 바이트 대조
    ③공개 URL(게이트웨이) 확인 — ③이 아직 안 열렸으면(웹 미배포) 그 사실을 명시해 반환."""
    ws = Path(workspace)
    if not ws.exists() or not any(ws.iterdir()):
        return "배포 실패: 작업공간이 비어 있습니다(먼저 구현·검증하세요)."
    pkg = ws / "package.json"
    has_server = (ws / "server.js").exists()
    scripts = {}
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
        except Exception:
            scripts = {}
    start_cmd = "npm start" if scripts.get("start") else ("node server.js" if has_server else "")
    static_only = not start_cmd
    if static_only and not (ws / "public").is_dir() and not (ws / "index.html").exists():
        return ("배포 실패: 서빙할 것이 없습니다 — Node 서버(server.js 또는 package.json의 start)나 "
                "정적 산출물(public/ 또는 index.html)이 필요합니다.")

    # 산출물 레포 커밋(히스토리 유지 — render 경로와 동일 규율) + .collab 유출 차단
    if not (ws / ".git").exists():
        _git(["init", "-q", "-b", "main"], str(ws))
    gi = ws / ".gitignore"
    try:
        _gi = gi.read_text() if gi.exists() else ""
    except OSError:
        _gi = ""
    if ".collab" not in _gi:
        gi.write_text((_gi.rstrip("\n") + "\nnode_modules/\n*.log\n.env\n__pycache__/\n.collab/\n").lstrip("\n"))
    _git(["add", "-A"], str(ws))
    _git(["commit", "-q", "-m", f"deploy {name}"], str(ws))
    repo_note = _push_best_effort(ws, name, gh_pat, gh_user)

    with _RegistryLock():
        reg = _load_registry()
        entry = reg.get(name) or {}
        appdir = _apps_dir() / name
        _stop_app(entry)                          # 구버전 프로세스 정리 후 복사(파일 잠김 회피)
        _copy_workspace(ws, appdir)
        port = None
        if not static_only:
            if pkg.exists():
                r = subprocess.run(["npm", "install", "--omit=dev", "--no-audit", "--no-fund"],
                                   cwd=str(appdir), capture_output=True, text=True, timeout=300)
                if r.returncode != 0:
                    return f"배포 실패(npm install): {(r.stdout + r.stderr)[-300:]}"
            port = _alloc_port(reg, name)
            pid = _spawn_app(appdir, port, start_cmd)
            reg[name] = {"port": port, "pid": pid, "dir": str(appdir),
                         "static": False, "cmd": start_cmd, "ts": time.time()}
        else:
            reg[name] = {"port": None, "pid": None, "dir": str(appdir),
                         "static": True, "ts": time.time()}
        _save_registry(reg)

    url = f"{_apps_base_url()}/{name}/"
    if not static_only:
        served = _local_health(port)
        if served is None:
            # [실행 CLI가 devDependency인 앱(2026-07-31, U-442 실측)] `--omit=dev`로 깔면 start가
            # 부르는 도구(vinext·next·vite 등)가 없어 기동이 실패한다("sh: vinext: not found").
            # 앱을 못 띄우면 배달 자체가 없으므로, 개발 의존까지 포함해 한 번 더 깔고 다시 띄운다.
            try:
                _tail0 = (appdir / "app.log").read_text(errors="replace")[-400:]
            except OSError:
                _tail0 = ""
            if pkg.exists() and "not found" in _tail0:
                r2 = subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                                    cwd=str(appdir), capture_output=True, text=True, timeout=600)
                if r2.returncode == 0:
                    with _RegistryLock():
                        reg2 = _load_registry()
                        _stop_app(reg2.get(name) or {})
                        pid = _spawn_app(appdir, port, start_cmd)
                        reg2[name] = {"port": port, "pid": pid, "dir": str(appdir),
                                      "static": False, "cmd": start_cmd, "ts": time.time()}
                        _save_registry(reg2)
                    served = _local_health(port)
        if served is None:
            tail = ""
            try:
                tail = (appdir / "app.log").read_text(errors="replace")[-240:]
            except OSError:
                pass
            return (f"배포 실패: 앱이 로컬 포트({port})에서 응답하지 않습니다 — 서버가 process.env.PORT로 "
                    f"listen하는지 확인하세요. 로그 꼬리: {tail}")
        stale = _verify_live_assets(f"http://127.0.0.1:{port}", ws, fetch=_local_fetch(port))
        if stale:
            return (f"배포 실패(서빙 불일치): 기동은 됐지만 앱이 방금 만든 파일을 서빙하지 않습니다 — "
                    f"{', '.join(stale[:4])}. public/ 정적 서빙 경로를 확인하고 다시 배포하세요.")
    public = _check_live(url, tries=2)
    if public:
        return (f"배포 성공 ✅ 라이브(HTTP {public} + 산출물 일치): {url}{repo_note}"
                + _measure_usability(url))
    return (f"배포 성공(로컬 검증 완료) — 앱은 이 서버에서 정상 기동·서빙 중입니다. 공개 URL {url} 은 "
            f"게이트웨이(murmur 웹) 반영 대기 상태일 수 있습니다 — 잠시 뒤 다시 확인하고, 계속 안 열리면 "
            f"'게이트웨이 미반영'으로 보고하세요(앱 자체는 검증 통과).{repo_note}")


# ══ 배포 provider 레지스트리 — 특정 플랫폼 종속 제거(2026-07-08 사용자 방향) ═══════
# "하나에 종속되지 않게, 봇이 맘대로 할 수 있게 — AWS를 써야 되면 그걸, GCP면 그걸."
# 배포 타겟은 **데이터**다: provider 하나 = deploy(workspace, name, creds, config)->str.
# 봇은 상황에 맞는 provider를 스스로 고른다(매체중립의 배포판). 내장 = vps·render·script.
# 새 플랫폼(AWS/GCP/Fly/Vercel…)은 ① provider 클래스 추가 후 register(), 또는 ② 즉석은
# script provider로 봇이 배포 레시피(aws/gcloud/flyctl CLI 등)를 직접 지정 — 코드 변경 없이.

class DeployProvider:
    """배포 타겟 한 곳의 계약. name·deploy만 구현하면 레지스트리에 꽂힌다."""
    name = "base"
    needs_creds = ()          # 이 provider가 요구하는 자격증명 키(없으면 게이트가 먼저 안내)

    def deploy(self, workspace, name, *, creds, config) -> str:
        raise NotImplementedError


class _VpsProvider(DeployProvider):
    name = "vps"              # 자체 서버(/apps/) — 자격증명 불요
    def deploy(self, workspace, name, *, creds, config):
        return deploy_vps_sync(workspace, name, creds.get("gh_pat"), creds.get("gh_user"))


class _RenderProvider(DeployProvider):
    name = "render"
    needs_creds = ("gh_pat", "gh_user", "render_key", "owner_id")
    def deploy(self, workspace, name, *, creds, config):
        return _deploy_render_sync(workspace, name, creds.get("gh_pat"), creds.get("gh_user"),
                                   creds.get("render_key"), creds.get("owner_id"),
                                   creds.get("region") or "singapore")


class _ScriptProvider(DeployProvider):
    """만능 탈출구 — 봇이 지정한 배포 명령을 실행한다(AWS CLI·gcloud·flyctl·vercel 등 무엇이든).
    금고 자격증명은 env로 주입(봇이 AWS_ACCESS_KEY_ID 등을 금고에 넣어두면 자동으로 닿는다).
    config: command(필수, 배포 셸)·url(선택, 결과 공개 URL — 있으면 실응답까지 확인)."""
    name = "script"
    def deploy(self, workspace, name, *, creds, config):
        cmd = str((config or {}).get("command") or "").strip()
        if not cmd:
            return ("배포 실패(script): command가 필요합니다 — 배포 셸 명령을 주세요"
                    "(예: aws s3 sync public/ s3://버킷 && aws cloudfront create-invalidation …).")
        env = {**os.environ, **{str(k): str(v) for k, v in (creds.get("extra") or {}).items()}}
        env["DEPLOY_NAME"] = name
        try:
            r = subprocess.run(cmd, shell=True, cwd=str(workspace), env=env,
                               capture_output=True, text=True, timeout=900)
        except Exception as e:
            return f"배포 실패(script 실행 오류): {type(e).__name__}: {str(e)[:200]}"
        tail = _mask_secret((r.stdout + r.stderr), *[str(v) for v in (creds.get("extra") or {}).values()])
        if r.returncode != 0:
            return f"배포 실패(script exit {r.returncode}): {tail[-400:]}"
        url = str((config or {}).get("url") or "").strip()
        if url:
            served = _check_live(url, tries=6) if _url_safe(url) else None
            if served:
                return f"배포 성공 ✅ (script, HTTP {served}): {url}\n로그: {tail[-200:]}"
            return (f"배포 완료(script) — 명령은 성공했으나 {url} 가 아직 응답하지 않습니다"
                    f"(전파 지연일 수 있음). 잠시 뒤 확인하세요. 로그: {tail[-200:]}")
        return f"배포 완료(script, URL 미지정 — 봇이 결과 위치를 직접 보고): {tail[-300:]}"


_PROVIDERS: dict = {}


def register_provider(provider: DeployProvider) -> None:
    """새 배포 타겟을 꽂는다 — AWS/GCP 등은 DeployProvider를 구현해 여기로 등록(코드 확장점)."""
    _PROVIDERS[provider.name] = provider


for _p in (_VpsProvider(), _RenderProvider(), _ScriptProvider()):
    register_provider(_p)


def deploy_targets() -> list:
    """등록된 배포 타겟 이름들 — 봇 도구 설명·안내가 동적으로 참조한다."""
    return sorted(_PROVIDERS)


def deploy_sync(workspace, name, gh_pat, gh_user, render_key, owner_id, region="singapore",
                target=None, config=None):
    """workspace를 배포한다 → 결과 문자열(라이브 URL 포함). **타겟에 종속되지 않는다**
    (2026-07-08 사용자: "하나에 종속되지 않게, 봇이 맘대로 — AWS면 AWS, GCP면 GCP").
    타겟 선택: 호출별 명시(target) > 전역 env(ORGANT_DEPLOY_TARGET) > 기본 vps.
    provider 레지스트리에서 찾아 위임한다 — 모르는 타겟이면 사용 가능 목록을 돌려준다."""
    t = (target or os.environ.get("ORGANT_DEPLOY_TARGET") or "vps").strip().lower()
    prov = _PROVIDERS.get(t)
    if prov is None:
        return (f"배포 실패: 모르는 배포 타겟 '{t}'. 사용 가능: {', '.join(deploy_targets())}. "
                f"임의 플랫폼(AWS/GCP 등)은 target='script' + command로 배포하세요.")
    creds = {"gh_pat": gh_pat, "gh_user": gh_user, "render_key": render_key,
             "owner_id": owner_id, "region": region, "extra": (config or {}).get("env") or {}}
    try:
        return prov.deploy(workspace, name, creds=creds, config=config or {})
    except Exception as e:
        return f"배포 처리 오류({t}): {type(e).__name__}: {str(e)[:200]}"


def _deploy_render_sync(workspace, name, gh_pat, gh_user, render_key, owner_id, region="singapore"):
    """[종전 경로 — Render] workspace를 push하고 Render 웹서비스로 배포."""
    ws = Path(workspace)
    if not ws.exists() or not any(ws.iterdir()):
        return "배포 실패: 작업공간이 비어 있습니다(먼저 구현·검증하세요)."
    pkg = ws / "package.json"
    if not pkg.exists():
        return "배포 실패: package.json이 없습니다. Node 앱만 지원합니다(서버는 process.env.PORT 사용)."
    try:
        scripts = json.loads(pkg.read_text()).get("scripts", {})
    except Exception:
        scripts = {}
    start_cmd = "npm start" if scripts.get("start") else "node server.js"

    # 1) [산출물 레포화(2026-07, 사용자 설계)] 작업공간의 *지속* git 레포에서 직접 배포한다 — 매번
    #    fresh init한 /tmp 사본이 아니라, Organt이 그 폴더 안에서 작업하며 쌓은 커밋 히스토리를 그대로
    #    push한다(산출물 = 독립 레포 관리). 프로젝트 등록 시 _init_artifact_repo가 이미 init하지만,
    #    구 흐름(레포 없는 작업공간)은 여기서 폴백 init. .git은 원격이 받는 트리에 안 실려도 무해.
    stage = ws
    if not (ws / ".git").exists():
        _git(["init", "-q", "-b", "main"], stage)
    # [B-07 — Task Dossier 유출 차단(BOT_ARCH_REDESIGN 2026-07-03)] .gitignore를 '없을 때만 생성'이
    # 아니라 append-if-missing으로 — `.collab/`(협의 원본: GOAL/MINUTES/REPORTS)이 `git add -A`(아래)로
    # 공개 GitHub에 push되는 유출을 SYS 첫 문서 쓰기(B-09)보다 *선행*해 막는다. 기존 워크스페이스 레포도
    # 다음 배포 때 여기서 자동 반영된다(append-if-missing이라 멱등).
    gi = ws / ".gitignore"
    if not gi.exists():
        gi.write_text("node_modules/\n*.log\n.env\n__pycache__/\n.collab/\n")
    else:
        try:
            _gi_txt = gi.read_text()
        except OSError:
            _gi_txt = ""
        if ".collab" not in _gi_txt:
            gi.write_text(_gi_txt.rstrip("\n") + "\n.collab/\n")

    # 2) 변경 스테이징 + 크기 검사 + 커밋(.gitignore가 node_modules·.env 제외)
    _git(["add", "-A"], stage)
    # [>100MB 예방 — GitHub 하드 거부 전에 명확히 안내(2026-07, '흐름 원활·안전')] GitHub는 100MB 초과 파일이
    # 든 push를 거부한다(라이브 P-003: 181MB 파일로 push 5회 실패, 봇은 원인 규명에 오래 헤맴). git add(=.gitignore
    # 반영) 뒤 *실제 스테이징된* 파일만 검사해, 초과분이 있으면 크립틱한 push 거부 대신 *뭘 할지*를 파일명과 함께
    # 즉시 안내 → 봇이 그 파일을 빼고 재배포하면 통과(비-일시라 cap엔 안 쌓임).
    big = _oversized_files(stage)
    if big:
        lst = ", ".join(f"{r}({s // (1024 * 1024)}MB)" for r, s in sorted(big, key=lambda x: -x[1])[:5])
        return ("배포 실패(비-일시 — 파일 크기): GitHub는 **100MB 초과 파일**을 거부합니다. 재배포 전에 다음을 "
                f"제거하거나 .gitignore에 넣으세요(대용량 에셋·바이너리·모델은 대개 배포에 불필요): {lst}. "
                "조치 후 재배포하면 통과합니다(이 실패는 배포 cap에 세지 않습니다).")
    _git(["commit", "-q", "-m", f"deploy {name}"], stage)   # 변경 없으면 non-zero지만 무해(기존 HEAD를 push)

    # 3) GitHub repo 보장(있으면 422 → 재사용)
    st, resp = _http("POST", f"{GITHUB_API}/user/repos", gh_pat,
                     {"name": name, "private": False,
                      "description": f"{name} — deployed by Organt Core multi-agent system"})
    if st not in (201, 422):
        return f"배포 실패(GitHub repo): HTTP {st} {resp.get('message', '')}"
    # [ground-truth 계정 — GH_USER 설정 드리프트 자가교정(2026-06-30 라이브 P-003)] 리포는 POST /user/repos =
    # *PAT 인증계정* 아래 생긴다. GH_USER 설정이 그 계정과 어긋나면(P-003: GH_USER=byundojin인데 토큰은
    # thisiscount01 → 리포는 thisiscount01에 생기고 push는 byundojin/…로 가 404) push가 통째로 죽는다.
    # 설정값이 아니라 *실제 owner*(201 응답 owner / 422 재사용 시 whoami)를 진실원으로 써 push한다.
    real_user = gh_user
    if st == 201 and isinstance(resp.get("owner"), dict) and resp["owner"].get("login"):
        real_user = resp["owner"]["login"]
    else:
        _who_st, _who = _http("GET", f"{GITHUB_API}/user", gh_pat)
        if _who_st == 200 and _who.get("login"):
            real_user = _who["login"]
    repo_url = f"https://github.com/{real_user}/{name}"

    # 4) push(force — 재배포 시 최신 상태로 덮어씀)
    push_url = f"https://x-access-token:{gh_pat}@github.com/{real_user}/{name}.git"
    rc, out = _git(["push", "-q", "-f", push_url, "main:main"], stage)
    # [산출물 레포화] stage=작업공간(지속 레포)이므로 삭제하지 않는다 — 다음 배포가 히스토리를 이어감
    if rc != 0:
        _o = _mask_secret(out, gh_pat)
        # [비-일시 분류 — 재시도로 안 풀리는 설정/인증 오류는 즉시 보고(2026-06-30, 사용자: '믿음만 믿고 5번
        # 재시도가 이상'). Repository not found·인증 실패·권한 거부는 *결정적* 오류라 같은 자격증명으로 재시도하면
        # 같은 실패 → cap 5회를 헛되이 태운다. _billing_suspended와 같은 비-일시 처리를 push 오류에도.]
        _low = _o.lower()
        if any(m in _low for m in ("repository not found", "authentication failed", "could not read username",
                                   "invalid username or password", "permission denied", "denied to", "403 forbidden")):
            return ("배포 실패(비-일시 — 재시도 무의미): git push 인증/접근 오류입니다. **재배포하지 마세요** "
                    "— 같은 자격증명이면 결과도 같습니다(코드 문제 아님). 대개 GH_PAT 무효·권한 부족·리포 접근 "
                    "문제이니, complete_task에 '배포 자격증명 점검 필요: <원인>'으로 사용자에게 정직히 보고하세요. "
                    f"상세: {_o[-180:]}")
        return f"배포 실패(git push): {_o[-300:]}"

    # 5) 기존 서비스 찾기 → 있으면 재배포, 없으면 생성
    st, svcs = _http("GET", f"{RENDER_API}/services?name={name}&limit=10", render_key)
    sid, url = None, ""
    if isinstance(svcs, list):
        for x in svcs:
            s = x.get("service", x)
            if s.get("name") == name:
                sid = s.get("id")
                url = s.get("serviceDetails", {}).get("url", "")
                break
    dep_id = None
    if sid:
        st, dep = _http("POST", f"{RENDER_API}/services/{sid}/deploys", render_key, {})
        dep_id = dep.get("id") if isinstance(dep, dict) else None   # 방금 트리거한 '그' 배포
    else:
        # 신규 서비스 생성 전에 풀이 한도에 임박했으면 '참조 없는 고아'를 정리해 슬롯을 확보한다
        # (참조 중 링크는 보존). 한도가 차서 작업이 멈추던 구멍(P-019)을 배포 경로가 스스로 막는다.
        keep = _referenced_services()
        keep_unknown = keep is None     # projects.json 못 읽음 → keep-set 불명
        if keep_unknown:
            # [보수 폴백] 참조 목록을 못 읽으면 고아 수거를 *건너뛴다*(빈 keep으로 참조 서비스 오삭제 방지).
            # 슬롯이 정말 부족하면 신규 생성이 cap에서 실패하고 그건 아래 비-일시 분기/보고로 잡힌다.
            keep = {name}
        else:
            keep.add(name)
            _free_slots(render_key, keep, want_free=2)
        payload = {"type": "web_service", "name": name, "ownerId": owner_id,
                   "repo": repo_url, "branch": "main", "autoDeploy": "yes",
                   "serviceDetails": {"runtime": "node", "plan": "free", "region": region,
                                      "envSpecificDetails": {"buildCommand": "npm install",
                                                             "startCommand": start_cmd}}}
        st, resp = _http("POST", f"{RENDER_API}/services", render_key, payload)
        if st != 201:
            blob = (json.dumps(resp) + " " + str(st)).lower()   # 한도/요금제로 보이면 고아 더 정리 후 1회 재시도
            if any(w in blob for w in ("limit", "maximum", "quota", "exceed", "free", "plan", "402", "429")):
                if not keep_unknown and _free_slots(render_key, keep, want_free=3):
                    st, resp = _http("POST", f"{RENDER_API}/services", render_key, payload)
            if st != 201:
                # [비-일시 차단 식별 — 무한 재시도 차단] 계정의 무료 서비스가 'billing'으로 모두 정지된
                # 상태(무료 월 시간 소진)면 재시도·슬롯정리로 안 풀린다. '잠시 후 재시도' 대신 '재시도
                # 무의미·사용자 보고'로 정확히 안내해 13회 헛도는 루프(라이브 P-021)를 끊는다.
                if _billing_suspended(render_key):
                    return ("배포 불가(Render 무료 플랜 billing 정지 — 재시도 무의미): 계정의 무료 서비스가 모두 "
                            "'billing'으로 정지됐습니다. Render 무료 월 인스턴스시간(전 무료 서비스 공유, 750h)이 "
                            "소진된 상태로, **deploy를 다시 불러도·슬롯을 정리해도 풀리지 않습니다**(월 리셋 또는 "
                            "유료 전환·무료 서비스 축소가 필요). **deploy를 반복 호출하지 말고 이 사실을 사용자에게 "
                            "보고**하세요 — 빌드·검증 결과는 유효하니 산출물 결함이 아니라 '배포 보류(플랫폼 billing "
                            "정지)'로 보고하면 됩니다. 기존 배포 링크들도 같은 이유로 현재 모두 정지(503) 상태입니다.")
                return (f"배포 실패(Render 서비스 생성): HTTP {st} {json.dumps(resp)[:160]} — 이건 산출물 결함이 "
                        "아니라 배포 플랫폼의 용량/네트워크 문제입니다. **다른 플랫폼(Vercel·Railway·Fly 등)은 "
                        "설정돼 있지 않으니 시도하지 마세요 — 구성된 배포 대상은 Render 하나뿐입니다.** 빌드·검증 "
                        "결과는 유효하니 작업을 '실패'로 마감하지 말고 '배포 보류(플랫폼 용량)'로 보고하세요"
                        "(잠시 후 deploy를 다시 부르면 됩니다 — 풀은 자동 정리됩니다).")
        svc = resp.get("service", {})
        sid = svc.get("id")
        dep_id = resp.get("deployId")
        url = svc.get("serviceDetails", {}).get("url", "")

    # 6) '방금 트리거한 배포'가 live 될 때까지 폴링(옛 배포의 live를 거짓 성공으로 읽지 않도록)
    deadline = time.time() + 480   # 빌드 8분까지 동행 — '트리거됨' 비종결 반환(폴링 초대) 최소화
    status = "?"
    while time.time() < deadline:
        if dep_id:
            st, d = _http("GET", f"{RENDER_API}/services/{sid}/deploys/{dep_id}", render_key)
            status = d.get("status", "?") if isinstance(d, dict) else "?"
        else:
            st, deps = _http("GET", f"{RENDER_API}/services/{sid}/deploys?limit=1", render_key)
            status = deps[0]["deploy"]["status"] if isinstance(deps, list) and deps else "?"
        if status == "live":
            served = _check_live(url)          # 라이브 URL이 '실제로 응답'하는지까지 확인
            if served:
                # [완료 = 증명된 완료] 응답(200)만으론 부족하다 — 라이브가 '방금 만든 그 파일'을
                # 서빙하는지까지 바이트 대조로 확인해야 '배포 성공'을 말할 수 있다(스테일 배포가
                # 완료로 보고되던 구멍의 도구 레벨 차단). 불일치면 이 호출 자체가 실패라서,
                # 리더는 구조적으로 이 상태를 '완료'로 보고할 수 없다.
                stale = _verify_live_assets(url, workspace)
                if stale:
                    return (f"배포 실패(스테일 서빙): Render는 live지만 라이브 파일이 산출물과 다릅니다 — "
                            f"{', '.join(stale[:4])}. 옛 빌드가 서빙 중일 수 있습니다 — 캐시 헤더·빌드 "
                            f"로그를 확인하고 다시 배포하세요(이 상태로 '완료' 보고 금지).")
                return (f"배포 성공 ✅ 라이브(HTTP {served} + 산출물 바이트 일치 확인): {url}  "
                        f"(repo: {repo_url})" + _measure_usability(url))
            return f"배포 실패: Render는 live인데 {url} 가 응답하지 않음(서버 기동 실패 가능) — 로그 확인 필요."
        if status in _TERMINAL_FAIL:
            return f"배포 실패(Render {status}) — 빌드 로그 확인 필요. 예정 URL: {url}"
        time.sleep(6)
    # 폴링 창이 끝남 — 빌드가 더 길 수 있다(P-020). '실패/미완'으로 오보하지 않게 최종 라이브 확인 후 정확히 보고.
    return _final_deploy_result(url, workspace, repo_url, status)
