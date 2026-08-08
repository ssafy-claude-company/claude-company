"""npx 자체 플래그는 도구 이름이 아니다 (2026-08-07 실측 U-536 주기 3).

주기 2를 완수한 뒤 주기 3(완성·호환판) 계획 회의가 다섯 번 막히고 판이 멈췄다:

    meet_preflight_failed  passes=4  n=1
    meet_preflight_failed  passes=5  n=1
    stage_stuck_parked
    flow_done

거부된 줄은 이것이었다:

    실증: `npx --no-install playwright test tests/milestone-core.spec.js
           --project=chromium --project=webkit`

`--no-install`은 네트워크 설치를 금지하는 플래그다 — 검증 명령으로는 **오히려 더 결정적**이다.
그런데 판정기는 두 곳에서 tokens[1]을 곧바로 도구 이름으로 읽었다: `_PROBE_RE`는 러너 이름이
npx 바로 뒤에 오기를 요구했고, `_existing_verifier_targets`의 npx 갈래는 tool='--no-install',
action='playwright'로 읽어 떨어뜨렸다.

플래그를 건너뛰고 도구를 읽는다. 플래그가 붙었다고 판정이 달라지면 안 된다 — 붙지 않은 형태와
정확히 같은 결과를 낸다.
"""
import sys

sys.path.insert(0, __file__.rsplit("/ops/", 1)[0])
from system.rule.evidence import (direct_verifier_command,
                                  looks_like_verification_command)


def _ok(cmd):
    return looks_like_verification_command(cmd, "", require_existing=False)


def test_U536이_비준하려던_그_줄이_통과한다():
    cmd = ("npx --no-install playwright test tests/milestone-core.spec.js "
           "--project=chromium --project=webkit")
    assert direct_verifier_command(cmd, "", require_existing=False) == cmd


def test_플래그가_판정을_바꾸지_않는다():
    """붙은 형태와 안 붙은 형태의 결과가 같아야 한다 — 통과든 거부든."""
    pairs = [
        ("npx playwright test tests/a.spec.js", "npx --no-install playwright test tests/a.spec.js"),
        ("npx vitest run", "npx --yes vitest run"),                      # 둘 다 거부(대상 없음)
        ("npx vitest run tests/a.test.js", "npx --yes vitest run tests/a.test.js"),
        ("npx http-server -p 4173", "npx --no-install http-server -p 4173"),   # 둘 다 거부
    ]
    for plain, flagged in pairs:
        assert _ok(plain) == _ok(flagged), f"{plain!r} vs {flagged!r}"


def test_판정하지_않는_명령은_플래그를_붙여도_거부된다():
    assert not _ok("npx --no-install http-server")
    assert not _ok("npx --no-install playwright --help")


def test_도구_이름이_없으면_거부한다():
    assert not _ok("npx --no-install")
    assert not _ok("npx")
