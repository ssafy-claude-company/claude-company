# 원격 백업 — 정본

> **왜 이 문서가 있나**: 2026-07-28 서버 이전 준비 중, 브레인 레포가 **7/3~7/28 3주간 원격에 한 번도
> 올라가지 않은 것**이 드러났다. 커밋 828개는 정상이었다 — 없던 것은 이력이 아니라 **다른 곳의 사본**이다.
> 7/6 병합 스냅샷을 GitHub에 올린 뒤 원격 설정이 이어지지 않았고, 그 사실을 아무도 몰랐다.
> **이력이 있다 ≠ 백업이 있다.** 이 문서는 그 재발을 막는 최소 규율이다.

## 1. 현재 구조 (실측)
| 레포 | 내용 | 원격 |
|---|---|---|
| `claude-company` (루트 `/root/ClaudeCompany`) | 브레인 = `system`+`organt`+`guide`+`ops` | 연결 필요 |
| `murmur` (nested) | SNS 플랫폼(Django+Vue) | `ssafy-claude-company/murmur` |

`system`·`organt`·`guide`는 **독립 레포가 아니다** — 루트 레포가 직접 소유한 디렉터리다(7/6 병합).
따라서 루트 레포 하나만 백업되면 브레인 전체가 백업된다.

## 2. 원격 연결 (최초 1회)
```bash
cd /root/ClaudeCompany
git remote add origin <레포 URL>
git fetch origin
```

### 계보 주의 — 그냥 밀면 안 되는 경우가 있다
GitHub의 `main`이 **7/6에 한 번 올린 스냅샷 1커밋**이고, 로컬 이력(828커밋)과 **뿌리가 다르다**
(`git merge-base`가 비어 있음). 이 상태에서 `push`는 거부되거나, 강제하면 기존 내용이 사라진다.

셋 중 하나를 **의식적으로** 고른다:
```bash
# (a) 안전 — 기존 main을 보존하고 실제 이력을 별도 브랜치로 올린다 (권장)
git push -u origin master:master

# (b) 교체 — 7/6 스냅샷을 버리고 실제 이력을 main으로 삼는다(스냅샷 내용은 로컬에 이미 포함)
git push --force origin master:main

# (c) 새 레포 — 빈 레포를 만들어 거기에 올린다
```
**(a)를 기본으로 하라.** 되돌릴 수 있는 선택이 먼저다.

## 3. 상시 감지 — `verify.sh` 6번 항목
`bash ops/verify.sh`가 매번 백업 상태를 보고한다(차단 아닌 경고):
```
== 6) 원격 백업 신선도 (미푸시 감지) ==
  claude-company  ⚠ 원격 미연결 — 이 디스크가 유일본(백업 없음)
  murmur          ✓ 원격 백업 최신
```
⚠가 보이면 그 자리에서 푸시하라. 이번 사고는 **아무 신호도 없었기 때문에** 3주를 갔다.

## 4. 자동 백업 (사람이 잊어도 유지되게)
하루 1회 자동 푸시. 두 레포 모두 대상.
```bash
cat > /etc/systemd/system/git-backup.service <<'EOF'
[Unit]
Description=git 원격 백업 (브레인 + murmur)
[Service]
Type=oneshot
ExecStart=/bin/bash -c 'cd /root/ClaudeCompany && git push origin HEAD 2>&1; cd murmur && git push origin HEAD 2>&1'
EOF

cat > /etc/systemd/system/git-backup.timer <<'EOF'
[Unit]
Description=git 원격 백업 매일
[Timer]
OnCalendar=daily
Persistent=true
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload && systemctl enable --now git-backup.timer
systemctl list-timers git-backup.timer          # 다음 실행 시각 확인
journalctl -u git-backup.service -n 20          # 결과 확인
```
> 인증은 미리 통해 있어야 한다(토큰 저장 또는 SSH 원격). 자동 푸시가 **조용히 실패**하면
> 이번 사고의 재판이다 — 위 `journalctl`로 주기적으로 확인하거나, verify 6번 경고를 신뢰하라.

## 5. 규율
1. **커밋했으면 푸시한다.** 로컬 커밋은 저장이 아니라 기록일 뿐이다.
2. **검증의 6번 경고를 무시하지 않는다.** 그게 유일한 조기 신호다.
3. **비밀값은 올리지 않는다.** `.dburl`·`.venv`는 `.gitignore`에 있다. `ops/` 문서엔 서버 IP·경로가
   있으므로 레포는 **비공개(private)** 로 둔다.
4. **서버 이전·재구축 때 원격 연결을 함께 옮긴다.** 이번 사고의 직접 원인이 그 누락이었다.
