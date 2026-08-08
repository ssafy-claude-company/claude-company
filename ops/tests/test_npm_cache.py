"""배포의 npm은 쓸 수 있는 캐시를 쓴다 (2026-08-07 실측 U-536).

판이 work 단계에 닿아 산출물을 공개하려 할 때 배포가 네 번 연속 같은 자리에서 죽었다:

    [배포 결과] B1 공개 배포: … — 배포 실패(npm install): npm ERR!
    [배포 결과] B1 공개 진입 경로 완성: … — 배포 실패(npm install): npm ERR!
    [배포 결과] B1: Render 진입점 start 스크립트를 명시하고 … — 배포 실패(npm install): npm ERR!
    [배포 결과] B1 완료: … — 배포 실패(npm install): npm ERR!

그 사이사이 'SYS 자동 이어가기'가 같은 배포를 다시 시켰다 — 고쳐지지 않는 벽에 턴만 태웠다.

재현하니 원인은 산출물이 아니라 실행 계정이었다. 러너는 2026-07-30 강등으로 organt(uid 999)로
도는데 HOME은 /root로 남겨 뒀다(봇 세션 기억이 HOME 슬러그로 저장돼 옮기면 기억을 잃는다).
그 강등은 쓰기 ACL을 ~/.claude·~/.codex·logs·ops/var에 얹었는데, npm이 쓰는 $HOME/.npm이
그 목록에 없었다:

    npm ERR! path /root/.npm/_cacache/tmp/e1a18206
    npm ERR! errno -13
    npm ERR!   sudo chown -R 999:983 "/root/.npm"

같은 package.json을 root로 돌리면 2초 만에 끝난다 — 산출물은 멀쩡했다.

캐시를 쓸 수 있는 자리(/var/lib/organt/npm-cache)로 준다. 러너 유닛 env에도 넣었지만, 그 파일이
없는 설치에서도 배포가 되어야 하므로 배포 코드가 한 번 더 못 박는다.
"""
import inspect
import os
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system import deploy as D


def test_npm은_쓸_수_있는_캐시를_받는다():
    env = D._npm_env()
    assert env.get("npm_config_cache"), "npm 캐시 경로를 정하지 않는다 — $HOME/.npm으로 떨어진다"


def test_이미_정해진_캐시는_존중한다():
    """운영 유닛이 env로 정해 두면 그것이 정본 — 코드가 덮어쓰지 않는다."""
    old = os.environ.get("npm_config_cache")
    os.environ["npm_config_cache"] = "/tmp/some-cache"
    try:
        assert D._npm_env()["npm_config_cache"] == "/tmp/some-cache"
    finally:
        if old is None:
            os.environ.pop("npm_config_cache", None)
        else:
            os.environ["npm_config_cache"] = old


def test_배포의_모든_npm이_이_env를_쓴다():
    """한 자리만 고치면 폴백 설치(app.log에 'not found'가 뜬 뒤 재설치)가 같은 벽에 다시 부딪힌다."""
    src = inspect.getsource(D)
    calls = [ln for ln in src.splitlines() if '"npm", "install"' in ln]
    assert len(calls) >= 2, f"npm install 호출을 찾지 못했다: {calls}"
    # 각 호출 뒤 200자 안에 env=_npm_env()가 있어야 한다
    for c in calls:
        i = src.find(c)
        assert "env=_npm_env()" in src[i:i + 320], f"이 npm 호출이 캐시 env를 안 쓴다: {c.strip()[:60]}"
