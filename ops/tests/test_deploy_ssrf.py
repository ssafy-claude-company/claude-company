"""SSRF 가드 회귀 (REVIEW M4): 배포결과 URL 자동 fetch 전 _url_safe 검증."""
from system.deploy import _url_safe, _verify_live_assets


def test_공인_URL_통과():
    assert _url_safe("https://app.onrender.com")
    assert _url_safe("https://github.com/o/r")


def test_사설_예약_비http_차단():
    for bad in ["http://127.0.0.1:8000", "http://localhost/x",
                "http://169.254.169.254/latest/meta-data/",   # 클라우드 메타데이터
                "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/",
                "file:///etc/passwd", "ftp://x.com", "gopher://x", ""]:
        assert not _url_safe(bad), bad


def test_verify_live_assets_사설URL_자동fetch안함(tmp_path):
    # public/ 있어도 사설 URL이면 네트워크 fetch 없이 즉시 통과(빈 목록)
    pub = tmp_path / "public"; pub.mkdir()
    (pub / "index.html").write_text("x")
    fetched = []

    def spy(u):
        fetched.append(u); return b"x"
    # 기본 fetch(None) + 사설 URL → 가드가 막아 fetch 호출 0
    assert _verify_live_assets("http://127.0.0.1/", str(tmp_path)) == []
    # 커스텀 fetch를 주면(테스트) 가드 우회 — 정상 대조 경로는 유지
    assert _verify_live_assets("http://127.0.0.1/", str(tmp_path), fetch=spy) == []
    assert fetched  # 커스텀 fetch는 호출됨


def test_run_deny에_SSRF_비밀경로_토큰_포함():
    """[보안 감사(2026-07-18)] run 셸 deny-list가 메타데이터·SSH키·/proc·내부API를 막는지.
    워크스페이스 마스킹(prefix 치환) 뒤에도 이 토큰들이 남아 걸린다 — `..`/심볼릭 우회 방어."""
    from system.guide_tools import _RUN_DENY
    dl = "|".join(_RUN_DENY).lower()
    for tok in ("169.254.169.254", ".ssh", "id_rsa", "/proc/", "/api/guide", "/api/atelier", "deploy_creds"):
        assert tok.lower() in dl, tok
    # 마스킹으로 워크스페이스 /root/... 접두가 지워져도 민감 표적 토큰이 남아 차단됨
    ws = "/root/organt_workspace"
    masked = f"cat {ws}/../.ssh/id_rsa".replace(ws, " ").lower()   # run()의 _scan 마스킹 재현
    assert any(d in masked for d in _RUN_DENY)                     # .ssh·id_rsa로 여전히 걸림
