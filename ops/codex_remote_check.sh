#!/usr/bin/env bash
# 사람용 Codex 공식 Remote SSH의 서버 측 준비 상태를 읽기 전용으로 점검한다.
set -euo pipefail

PROJECT="${MURMUR_ROOT:-/root/ClaudeCompany}"
CONFIG="/root/.codex/config.toml"

fail() {
  echo "✗ $*" >&2
  exit 1
}

[ -x /usr/local/bin/codex ] || fail "/usr/local/bin/codex가 없거나 실행 불가"
CODEX_VERSION="$(/usr/local/bin/codex --version 2>&1 | tail -n 1)"
echo "✓ CLI: $CODEX_VERSION"

AUTH_STATUS="$(/usr/local/bin/codex login status 2>&1)"
grep -q "Logged in using ChatGPT" <<<"$AUTH_STATUS" || fail "ChatGPT 인증 없음"
echo "✓ 인증: ChatGPT"

DAEMON_JSON="$(/usr/local/bin/codex app-server daemon version 2>&1)"
grep -q '"status":"running"' <<<"$DAEMON_JSON" || fail "app-server daemon 미기동: $DAEMON_JSON"
SOCKET_PATH="$(sed -n 's/.*"socketPath":"\([^"]*\)".*/\1/p' <<<"$DAEMON_JSON")"
[ -n "$SOCKET_PATH" ] || fail "app-server socket 경로를 읽지 못함"
[ -S "$SOCKET_PATH" ] || fail "app-server Unix socket 없음: $SOCKET_PATH"
echo "✓ app-server: Unix socket $SOCKET_PATH"

systemctl is-active --quiet codex-remote-tui.service ||
  fail "Linux 원격 TUI 서비스 미기동: codex-remote-tui.service"
curl --noproxy '*' --fail --silent --show-error --max-time 3 \
  http://127.0.0.1:4500/readyz >/dev/null ||
  fail "Linux 원격 TUI app-server readiness 실패"
LISTEN_ADDRS="$(ss -ltnH 'sport = :4500' 2>/dev/null | awk '{print $4}')"
[ "$LISTEN_ADDRS" = "127.0.0.1:4500" ] ||
  fail "4500 리스너가 loopback 단일 주소가 아님: ${LISTEN_ADDRS:-없음}"
echo "✓ Linux 원격 TUI: SSH 전용 loopback ws://127.0.0.1:4500"

git -C "$PROJECT" rev-parse --git-dir >/dev/null 2>&1 || fail "프로젝트 Git 저장소 없음: $PROJECT"
[ -r "$PROJECT/AGENTS.md" ] || fail "AGENTS.md 없음: $PROJECT"
[ -r "$PROJECT/ops/STATE.md" ] || fail "ops/STATE.md 없음: $PROJECT"
echo "✓ 프로젝트: $PROJECT"

grep -q '^model = "gpt-5.6-luna"$' "$CONFIG" || fail "전역 모델이 gpt-5.6-luna가 아님"
grep -q '^model_reasoning_effort = "max"$' "$CONFIG" || fail "전역 reasoning effort가 max가 아님"
echo "✓ 기본 모델: GPT-5.6-Luna / max"

SSHD_EFFECTIVE="$(sshd -T 2>/dev/null || true)"
if grep -q '^passwordauthentication no$' <<<"$SSHD_EFFECTIVE"; then
  echo "✓ SSH: 비밀번호 로그인 비활성"
else
  echo "△ SSH: 비밀번호 로그인 활성 — 로컬 공개키 검증 뒤 하드닝 필요"
fi

echo "READY: Linux Codex CLI SSH 터널 또는 Mac/Windows ChatGPT 데스크톱 SSH에서 $PROJECT 연결 가능"
