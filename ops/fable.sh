#!/usr/bin/env bash
# [Fable 워커 세션 재기동] Fable-세션2(e4414f9b)에서 포크한 병렬 세션 6개를 각자 worktree에서 되살린다.
#   이현준-1~3 · 변도진-1~3  (전부 Fable 모델 · max effort · RC)
#   ops/fable.sh <이름|all>      예: ops/fable.sh 이현준-2   /   ops/fable.sh all
#
# 규율(중요):
#  - 재기동은 **각자 자기 세션ID로 --resume**(재포크 금지 — 재포크하면 그동안 쌓은 작업이 날아간다).
#  - **--model fable --effort max 를 launch 플래그로 박는다**: /model·/effort로 런타임만 바꾸면
#    재시작 시 상속값(예: high)으로 되돌아간다. 기본설정을 여기 플래그로 고정.
#  - 이미 살아있는 세션은 스킵(사용자 세션 안 죽이기).
#  - Korean 경로는 .claude 프로젝트 디렉터리에서 충돌하지만(이현준-N·변도진-N 동수),
#    resume는 아래 **명시 세션ID**로 하므로 조회가 모호하지 않다.
set -uo pipefail

# 이름 → 자기 포크 세션ID (재기동 resume 대상, 안정 · 재포크 안 함)
declare -A SID=(
  [이현준-1]=d39dd393-06d8-476a-862e-2fcc3541bfd0
  [이현준-2]=c83fd0fa-987d-4fe6-9afa-a2e139266cd4
  [이현준-3]=da4ba956-f41c-4d2e-81b0-0d3e7f2e7a2b
  [변도진-1]=b5cfc899-3821-4c3c-a85f-89ad83791be3
  [변도진-2]=475724d5-4f96-4e4d-8e95-fde920b33a14
  [변도진-3]=9750db36-de40-4f10-95af-18db7d47a8df
)
ORDER="이현준-1 이현준-2 이현준-3 변도진-1 변도진-2 변도진-3"

launch() {
  local N="$1" W="/root/wt/$1"
  [ -n "${SID[$N]:-}" ] || { echo "⚠ 모르는 세션: $N (이름: $ORDER)"; return 2; }
  [ -d "$W" ] || { echo "⚠ worktree 없음: $W — 먼저 'bash ops/wt.sh new $N'"; return 1; }
  if tmux has-session -t "$N" 2>/dev/null; then
    echo "$N 이미 살아있음 — 스킵(안 건드림)"; return 0
  fi
  tmux new-session -d -s "$N" \
    "cd '$W' && MURMUR_ROOT='$W' claude --resume ${SID[$N]} --model fable --effort max --remote-control '$N' --permission-mode auto; exec bash"
  echo "$N 기동 · resume ${SID[$N]:0:8}… · model fable · effort max · RC"
  echo "   URL: tmux capture-pane -t '$N' -p -J | grep -oE 'https://claude.ai/code[^ ]*'"
}

case "${1:-all}" in
  all) for N in $ORDER; do launch "$N"; done ;;
  *)   launch "$1" ;;
esac
