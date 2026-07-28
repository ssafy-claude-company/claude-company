"""[Core] atelier 클라이언트 — Organt가 '선택해서' 쓰는 공유 캔버스(외부 독립 서비스).

지위: deploy.py(Render API)와 동일한 외부 서비스 클라이언트 — 매체(Guide 구현체)가 아니므로
매체중립 불변식과 무관. 설정은 env로만: ATELIER_URL(기본 https://atelier.dojin-mini.shop),
ATELIER_TOKEN(service 토큰). 토큰 없으면 도구가 장착만 되고 호출 시 안내를 돌려준다.

동작 3개(전부 판의 공개 API — 사람과 동일한 문):
- note(project, canvas, text): 스티키 한 장(산출물 설명·증거 메모). 캔버스 없으면 만든다.
- shot(project, canvas, url, sel, title): 실화면 조각(라이브 임베드) 한 장.
- done(pin, note): atelier 핀 승격 요청([atelier 핀 #N])을 마감 회신(status=done).
"""
import json
import os
import re
import urllib.request


def _cfg():
    # [서버 이전(2026-07-28)] 기본값이 구 VPS(sslip)를 가리켜, env를 안 고치면 봇의 캔버스 기록이
    # 은퇴한 서버로 갔다(데이터 분기). 기본값도 현 라이브 호스트로 옮긴다.
    url = (os.environ.get("ATELIER_URL") or "https://atelier.dojin-mini.shop").rstrip("/")
    tok = os.environ.get("ATELIER_TOKEN", "").strip()
    return url, tok


def _call(path, method="GET", body=None):
    url, tok = _cfg()
    if not tok:
        raise RuntimeError("ATELIER_TOKEN 미설정 — 러너 env에 토큰을 넣어야 판에 그릴 수 있다")
    req = urllib.request.Request(url + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Token {tok}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read() or b"{}")


def _ensure_canvas(project: str, canvas: str) -> str:
    # cid는 slugify를 거치므로 한글 이름은 cid가 달라진다 — cid·name 양쪽으로 찾아야 멱등
    cs = _call(f"/api/projects/{project}/canvases/").get("canvases", [])
    for c in cs:
        if canvas in (c["cid"], c["name"]):
            return c["cid"]
    made = _call(f"/api/projects/{project}/canvases/", "POST", {"cid": canvas, "name": canvas})
    return made["cid"]


def _next_y(project: str, canvas: str) -> float:
    objs = _call(f"/api/projects/{project}/canvases/{canvas}/objects/")
    items = objs.get("items", [])
    return (max((i["y"] + i.get("h", 100) for i in items), default=0)) + 40


def note(project: str, canvas: str, text: str) -> str:
    cid = _ensure_canvas(project, canvas)
    y = _next_y(project, cid)
    it = _call(f"/api/projects/{project}/canvases/{cid}/items/", "POST",
               {"kind": "sticky", "text": text[:2000], "x": 40, "y": y, "w": 340, "h": 110,
                "origin": "ai"})
    url, _ = _cfg()
    return f"판에 남김 → {url}/p/{project}/c/{cid}/?focus={it['id']}"


def shot(project: str, canvas: str, url_: str, sel: str, title: str) -> str:
    cid = _ensure_canvas(project, canvas)
    y = _next_y(project, cid)
    it = _call(f"/api/projects/{project}/canvases/{cid}/items/", "POST",
               {"kind": "shot", "text": title[:120] or "실화면", "x": 40, "y": y, "w": 640, "h": 200,
                "origin": "ai", "meta": {"url": url_, "sel": sel or "", "fit": "component"}})
    url, _ = _cfg()
    return f"실화면 조각을 판에 → {url}/p/{project}/c/{cid}/?focus={it['id']}"


def read(project: str, canvas: str) -> str:
    """[AX] 판 읽기 — 캔버스를 AI용 요약문(digest)으로. 좌표 JSON 해석 없이 원문·연결·판정·핀."""
    cid = _ensure_canvas(project, canvas)
    d = _call(f"/api/projects/{project}/canvases/{cid}/digest/")
    return d.get("digest") or "(빈 판)"


def done(pin: str, note_: str) -> str:
    m = re.search(r"\d+", str(pin))
    if not m:
        raise RuntimeError("pin은 요청문의 '[atelier 핀 #N]'에 있는 숫자 N")
    pid = m.group(0)
    out = _call(f"/api/feedback/{pid}/promotion/", "POST",
                {"status": "done", "note": note_[:500]})
    return f"핀 #{pid} 마감 회신 완료(status={out.get('promotion', {}).get('status')})"
