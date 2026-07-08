#!/usr/bin/env bash
# [fable-* 세션 재기동] Fable-세션2(e4414f9b)에서 포크한 4개 병렬 세션을 각자 worktree에서 되살린다.
#   ops/fable.sh <1-4|all>
#
# 규율(중요):
#  - 재기동은 **각자 자기 세션ID로 --resume**(재포크 금지 — 재포크하면 그동안 쌓은 작업이 날아간다).
#  - **--effort max 고정**: /effort로 런타임만 바꾸면 재시작 시 상속값(high)으로 돌아간다.
#    effort를 여기 launch 플래그로 박아 재시작해도 항상 max로 뜨게 한다.
#  - 이미 살아있는 세션은 스킵(사용자 세션 안 죽이기).
set -uo pipefail

# 각 fable의 자기 포크 세션ID (재기동 resume 대상 — 안정, 재포크 안 함)
declare -A SID=(
  [1]=e6a40f93-5aa1-4a56-baa3-b3284b217912
  [2]=2f3959d2-3ef2-4854-b219-a29c83e79f41
  [3]=419969b6-ecb8-44cd-82d2-f22c3adee6c3
  [4]=48ee815a-f5f9-4e94-883c-cc3b719a09c1
)

launch() {
  local i="$1" W="/root/wt/fable-$i"
  [ -d "$W" ] || { echo "⚠ worktree 없음: $W — 먼저 'bash ops/wt.sh new fable-$i'"; return 1; }
  if tmux has-session -t "fable-$i" 2>/dev/null; then
    echo "fable-$i 이미 살아있음 — 스킵(안 건드림)"; return 0
  fi
  tmux new-session -d -s "fable-$i" \
    "cd '$W' && MURMUR_ROOT='$W' claude --resume ${SID[$i]} --effort max --remote-control fable-$i --permission-mode auto; exec bash"
  echo "fable-$i 기동 · resume ${SID[$i]:0:8}… · effort max · RC (URL: tmux capture-pane -t fable-$i -p -J | grep -oE 'https://claude.ai/code[^ ]*')"
}

case "${1:-all}" in
  all)   for i in 1 2 3 4; do launch "$i"; done ;;
  [1-4]) launch "$1" ;;
  *)     echo "사용: ops/fable.sh <1-4|all>"; exit 2 ;;
esac
