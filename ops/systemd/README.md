# ops/systemd — 관측 회전·알림 유닛 (설치는 운영자 — 사용자 승인 후)

이 디렉터리는 **파일 제공만** 한다. `/etc` 복사·`systemctl enable`은 반드시 사용자 승인 후 운영자가.

| 파일 | 역할 |
|---|---|
| `monitor-alerts.service` + `.timer` | 5분마다 `manage.py monitor_alerts` — 러너사망·스톨·수렴경보·denied 급증 → 채널/웹훅/stdout |
| `log-rotate.service` + `.timer` | 매시 `ops/log_rotate.sh` — flow/audit 5MB 초과 시 `.1`~`.5` 회전 |
| `reconcile-payments.service` + `.timer` | 30분마다 `manage.py reconcile_payments` — 결제 승인 유실 봉합·버려진 주문 만료(승인↔부여 정합 안전망) |
| `organt-runner-onfailure.conf.example` | 러너 유닛 실패 시 즉시 알림 스캔(드롭인 예시) |
| (형제) `../organt-logs.logrotate` | logrotate.d 방식 회전(위 timer의 **대안** — 하나만 활성화) |

## 설치 명령 (전부 승인 후)

```bash
# 1) 알림 (5분 주기)
cp /root/murmur-stack/ops/systemd/monitor-alerts.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now monitor-alerts.timer

# 2) 로그 회전 — 방식 A(자체 스크립트 timer) 또는 B(logrotate.d) 중 하나만
# A:
cp /root/murmur-stack/ops/systemd/log-rotate.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now log-rotate.timer
# B:
cp /root/murmur-stack/ops/organt-logs.logrotate /etc/logrotate.d/organt-logs

# 3) 결제 대사 (30분 주기 — 결제 실서비스 정합 안전망, 2026-07-27)
cp /root/ClaudeCompany/ops/systemd/reconcile-payments.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now reconcile-payments.timer

# 4) (선택) 러너 실패 즉발 알림
mkdir -p /etc/systemd/system/organt-runner.service.d
cp /root/murmur-stack/ops/systemd/organt-runner-onfailure.conf.example \
   /etc/systemd/system/organt-runner.service.d/onfailure.conf
systemctl daemon-reload

# 4) (선택) 알림 배달처 설정 — 없으면 stdout(저널)만
cat >/etc/organt-monitor.env <<'EOF'
ORGANT_ALERT_WEBHOOK=https://…       # Slack/Discord 호환 JSON POST
ORGANT_ALERT_CHANNEL=123456789       # murmur 운영 채널 id(GuideMessage 게시)
EOF
chmod 600 /etc/organt-monitor.env
```

## 확인·튜닝

```bash
systemctl list-timers 'monitor-alerts*' 'log-rotate*'
journalctl -u monitor-alerts.service -n 20      # 알림 이력
/root/murmur-stack/ops/obs.sh                    # 터미널 요약(설치 없이도 동작)
```

- 감지 임계 조정: `monitor-alerts.service`의 ExecStart에 `--dead-min 5 --stall-min 30 --window-min 15 --denied-threshold 10 --cooldown-min 60` 옵션 추가.
- 회전 임계 조정: `log-rotate.service`에 `Environment=MAX_BYTES=… KEEP=…`.
- 수동 청소(설치 불요): `ops/prune_state.sh --days 7` (dry-run) → `--apply`.
