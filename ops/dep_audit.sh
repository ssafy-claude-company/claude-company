#!/usr/bin/env bash
# [빌려 쓰는 것도 우리 표면이다 (2026-08-06 감사, 현준-4)]
#
# 우리가 쓴 코드만 보면 절반만 본 것이다. 라이브러리의 구멍도 그대로 우리 구멍이다.
# 실제로 이 감사에서 셋이 나왔다 — cryptography(high), Django 5.2.15(3건), aiohttp(3건).
# 셋 다 우리 코드가 닿는 경로는 아니었지만, 버전으로는 해당됐고 전부 패치 릴리스였다.
#
# [왜 착지 게이트에 넣지 않았나] 이 검사는 **밖의 취약점 목록**을 본다. 새 CVE가 하나
# 공개되는 순간 모든 세션의 착지가 빨갛게 멈춘다 — 우리 변경과 무관한 이유로. 게이트는
# 우리가 고칠 수 있는 것만 봐야 한다. 이건 주기적으로 사람이 돌리고 판단하는 검사다.
#
#   bash ops/dep_audit.sh
#
# 나오는 것마다 물을 것: ① 우리 코드가 그 경로를 부르는가 ② 고치는 판이 패치 릴리스인가.
# ①이 아니어도 ②면 올린다. ①이면 급하다.
set -uo pipefail
R="${MURMUR_ROOT:-/root/ClaudeCompany}"
VENV="$R/.venv/bin"
fail=0

echo "== 파이썬 (설치된 것 전부) =="
if [ -x "$VENV/pip-audit" ]; then
  if ! "$VENV/pip-audit" --progress-spinner off 2>&1 | tail -20; then fail=1; fi
else
  echo "  pip-audit 없음 — $VENV/pip install pip-audit"
  fail=1
fi

echo
echo "== 프런트 (운영 의존성) =="
if [ -d "$R/murmur/frontend/node_modules" ]; then
  (cd "$R/murmur/frontend" && npm audit --omit=dev 2>&1 | tail -12) || fail=1
else
  echo "  node_modules 없음 — 건너뜀"
fi

echo
echo "== 프런트 (빌드 도구 포함) =="
if [ -d "$R/murmur/frontend/node_modules" ]; then
  (cd "$R/murmur/frontend" && npm audit 2>&1 | tail -12) || fail=1
fi

echo
echo "== GitHub 경보 (레포가 보는 것 — 위와 겹치지 않을 수 있다) =="
if command -v gh >/dev/null 2>&1; then
  for repo in ssafy-claude-company/murmur ssafy-claude-company/claude-company; do
    n=$(gh api "repos/$repo/dependabot/alerts" --jq '[.[] | select(.state=="open")] | length' 2>/dev/null || echo "?")
    echo "  $repo: 열린 경보 $n"
  done
else
  echo "  gh 없음 — 건너뜀"
fi

echo
[ "$fail" -eq 0 ] && echo "훑기 완료" || echo "훑기 중 실패한 단계가 있다 — 위를 볼 것"
exit 0        # 판단은 사람이 한다 — 이 스크립트는 막지 않고 보여 준다
