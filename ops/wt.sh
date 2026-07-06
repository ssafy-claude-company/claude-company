#!/usr/bin/env bash
# [세션 worktree 격리] 워커별 독립 스택 (2레포: claude-company + murmur).
#   wt.sh new <task>    claude-company(system/organt/guide/ops) + murmur 를 s/<task> 브랜치로
#   wt.sh rm  <task>    워크트리 제거(브랜치는 남김)
#   wt.sh list
# [Fable] 기본 협업은 정본 트리 직접 작업(task+claim). worktree는 레포-로컬 대량작업 등 opt-in만.
# 워커는:  MURMUR_ROOT=/root/wt/<task> bash /root/wt/<task>/ops/verify.sh --only <레포>
set -uo pipefail
MS=/root/ClaudeCompany
CMD="${1:-list}"; S="${2:-}"

case "$CMD" in
  new)
    [ -n "$S" ] || { echo "사용: wt.sh new <task>"; exit 2; }
    W="/root/wt/$S"
    [ -e "$W" ] && { echo "이미 존재: $W"; exit 1; }
    git -C "$MS" worktree add "$W" -b "s/$S" >/dev/null            # claude-company(브레인+ops+메타)
    echo "  [브랜치] claude-company (system/organt/guide/ops)"
    git -C "$MS/murmur" worktree add "$W/murmur" -b "s/$S" >/dev/null  # murmur(별도 레포)
    echo "  [브랜치] murmur"
    ln -s "$MS/.venv" "$W/.venv"
    ln -sfn "$MS/murmur/frontend/node_modules" "$W/murmur/frontend/node_modules" 2>/dev/null || true
    echo "✓ 스택: $W"
    echo "  워커 진입:  MURMUR_ROOT=$W bash $W/ops/verify.sh --only <레포>" ;;
  rm)
    [ -n "$S" ] || { echo "사용: wt.sh rm <task>"; exit 2; }
    W="/root/wt/$S"
    git -C "$MS/murmur" worktree remove --force "$W/murmur" 2>/dev/null || true
    git -C "$MS" worktree remove --force "$W" 2>/dev/null || true
    rm -rf "$W"
    echo "✓ 제거: $W (브랜치 s/$S 는 남김 — 병합 후 정리)" ;;
  list)
    echo "== 활성 세션 스택 =="; ls -d /root/wt/*/ 2>/dev/null | sed 's/^/  /' || echo "  (없음)"
    echo "== claude-company worktrees =="; git -C "$MS" worktree list | sed 's/^/  /'
    echo "== murmur worktrees =="; git -C "$MS/murmur" worktree list | sed 's/^/  /' ;;
  *) echo "명령: new|rm|list"; exit 2 ;;
esac
