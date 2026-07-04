#!/usr/bin/env bash
# task별 격리 세션 런처. worktree 생성 → 그 안에서 클코 실행.
#   ops/session.sh <task> [bg]        예: ops/session.sh sns-추천개선        (대화형)
#                                          ops/session.sh bot-지능 bg          (백그라운드)
set -uo pipefail
MS=/root/ClaudeCompany
T="${1:?사용: session.sh <task> [bg|web|fg] [\"할 일 설명\"]}"; MODE="${2:-fg}"; DESC="${3:-}"
W="/root/wt/$T"
[ -e "$W" ] || bash "$MS/ops/wt.sh" new "$T" system organt guide murmur
cd "$W"
PROMPT="너는 task '$T' 담당 세션. 정향: CLAUDE.md→ops/STATE.md. 시작 시 'bash ops/claim.sh add $T <실제만질파일glob>'. 계약(ops/CONTRACTS.md 17seam) 지키며 개발. 착지 전: claim.sh check + ops/STATE.md 갱신 + 'bash ops/verify.sh' green. 라이브 인프라는 사용자 승인 후. 판단은 Fable 에이전트, 집행은 Opus. ── 할 일: ${DESC:-(사용자가 곧 지시. 우선 정향+claim만.)}"
# Opus4.8·max에폭·자동승인(auto=이 세션과 같은 모드: 일반 자동, 위험만 분류기가 잡음)
FLAGS="--model opus --effort max --permission-mode auto"
if [ "$MODE" = bg ]; then
  MURMUR_ROOT="$W" claude --bg $FLAGS "$PROMPT"
  echo "백그라운드 dispatch됨(자동승인) → 'claude agents'로 모니터"
elif [ "$MODE" = web ]; then
  echo "웹 제어 세션 — claude.ai/code 링크가 뜨면 브라우저에서 열어 인증:"
  MURMUR_ROOT="$W" claude remote-control --spawn worktree --permission-mode auto --name "$T"
else
  echo "대화형 진입: cd $W (MURMUR_ROOT=$W). 아래 실행:"
  echo "  MURMUR_ROOT=$W claude $FLAGS"
  echo "  (웹 제어 원하면 세션 안에서 /remote-control)"
fi
