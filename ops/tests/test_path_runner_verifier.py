"""설치된 러너를 제 경로로 부르는 것도 실증 명령이다 (2026-08-07 실측 U-536).

주기 1을 완수한 직후, 주기 2 계획 회의가 같은 형식 검사에 세 번 막혀 판이 파킹됐다.
팀이 비준하려던 줄은 이것이었다:

    실증: `npm test && ./node_modules/.bin/playwright test tests/milestone-core.spec.js
           --project=chromium --project=webkit`

한국어 설명도 섞이지 않은 순수 명령인데 `direct_verifier_command`가 빈 값을 돌려줬다. 원인은
`_PROBE_RE`의 마지막 갈래였다:

    (?:bash|sh|\\./)\\s*\\S*(?:test|spec|check|verify|browser|e2e)\\S*

`./` 로 시작하면 **경로 안에** test|spec|check|verify|browser|e2e 중 하나가 있어야 한다.
`./node_modules/.bin/playwright`에는 그런 낱말이 없다. 반면 `npx playwright`는 별도 갈래로
통과한다 — **같은 바이너리인데** npx로 부르면 되고 제 경로로 부르면 안 됐다. 오히려 경로 호출이
결정적인데도 그렇다. 팀은 무엇이 문제인지 모른 채 변형만 세 번 시도했고 회의가 소진됐다.

아는 러너 이름으로 끝나는 경로를 받는다. 임의 실행 파일을 여는 것이 아니다 — 서버를 띄우기만
하는 `./node_modules/.bin/http-server`나 `./scripts/serve.sh`는 여전히 실증이 아니다.
"""
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.evidence import (direct_verifier_command,
                                  looks_like_verification_command)


def _ok(cmd):
    return looks_like_verification_command(cmd, "", require_existing=False)


def test_경로로_부른_러너를_받는다():
    assert _ok("./node_modules/.bin/playwright test tests/milestone-core.spec.js "
               "--project=chromium --project=webkit")
    assert _ok("node_modules/.bin/vitest run")


def test_U536이_비준하려던_그_줄이_통과한다():
    cmd = ("npm test && ./node_modules/.bin/playwright test tests/milestone-core.spec.js "
           "--project=chromium --project=webkit")
    assert direct_verifier_command(cmd, "", require_existing=False) == cmd


def test_npx_경로는_종전대로_통과한다():
    assert _ok("npx playwright test tests/a.spec.js")


def test_판정하지_않는_명령은_여전히_거부한다():
    """서버를 띄우기만 하는 것은 아무것도 판정하지 않는다 — 이 수리가 그 문을 열면 안 된다."""
    assert not _ok("./node_modules/.bin/http-server -p 4173")
    assert not _ok("./scripts/serve.sh")
    assert not _ok("python3 -m http.server 8000")


def test_아무_실행_파일이나_열리지_않는다():
    """아는 러너 이름으로 끝나는 경로만 — 임의 바이너리는 실증 명령이 아니다."""
    assert not _ok("./bin/deploy")
    assert not _ok("/usr/local/bin/mytool --run")
