# 스케일아웃 런북 — 러너를 2번째 VPS로 분리

> **결정 근거(2026-07-18 측정):** 병목은 CPU/웹이 아니라 **봇 CLI 서브프로세스 메모리**다(2코어 load 0.09,
> 봇 `claude` 프로세스가 90~650MB씩 여럿). 웹·DB는 가볍다. 따라서 최적 트레이드오프 = **러너+봇을 전용
> 박스로 분리**(러너 1개, 샤딩 아님). 웹+DB는 현 VPS(박스 A)에 유지. 관리형·S3·러너샤딩 불필요.
>
> **전제:** 이 세션에서 상태 외부화 코드가 이미 커밋됨(ms_status·레지스트리 DB, `guide/state` API,
> `_workspace` DB해석 — 전부 플래그). 이 런북은 그 플래그를 켜고 네트워크·스토리지만 연결한다.

## 토폴로지

```
        인터넷
          │ TLS
     ┌────▼─────────────────┐          사설 네트워크(VPC)          ┌──────────────────────┐
     │  박스 A (현 VPS)       │◄───────────────────────────────────►│  박스 B (신규·메모리우선)  │
     │  nginx · gunicorn 웹   │   Postgres 5432 (사설IP)            │  organt-runner + 봇들   │
     │  Postgres(진실원)      │   NFS: workspace 공유               │  claude CLI 서브프로세스  │
     │  NFS 서버              │   HTTP: 러너→웹 /api/guide/         │  NFS 클라이언트          │
     └──────────────────────┘                                     └──────────────────────┘
```

핵심: 러너↔웹은 **HTTP 전용**(이미 그렇게 설계됨). 상태는 **DB**(플래그 on). 봇 산출물 파일만 **NFS 공유**.

---

## 박스 A (웹+DB, 현 VPS) 변경분

> **Postgres는 건드리지 않는다(127.0.0.1 유지).** ✅ 라이브 검증: remote 모드 러너는 로스터·픽업·상태를
> **전부 HTTP(`_remote_roster`/guide API)로** 받고 **ORM을 안 쓴다**(`_local_*`는 로컬 모드 전용).
> 실측: 라이브 러너 프로세스의 Postgres 커넥션 = **0개**, env에 DATABASE_URL 없음. 따라서 박스 B에
> DB를 열어줄 필요가 없다 — PG 노출은 불필요한 보안 구멍이니 하지 말 것. 웹만 로컬 PG를 본다.

### A1. 웹 HTTP를 러너 박스에 노출 (사설 net만)
러너(박스 B)가 웹의 guide API에 닿아야 한다. gunicorn을 사설 IP:8000에도 bind + ufw로 박스 B만 허용
(nginx TLS 오버헤드 없이 러너 폴이 빠름 — 권장):
```bash
# murmur-web systemd ExecStart: --bind 127.0.0.1:8000 에 --bind <박스A_사설IP>:8000 추가
#   gunicorn ... --bind 127.0.0.1:8000 --bind <박스A_사설IP>:8000 ...
systemctl daemon-reload && systemctl restart murmur-web
ufw allow from <박스B_사설IP> to any port 8000 proto tcp
```
> 대안(사설 IP 없이): nginx `/api/guide/`에 `allow <박스B_공인IP>; deny all;` + proxy_pass 8000.
> 단 이 경우 guide 트래픽이 공인망을 타므로 **사설 네트워크(VPC) 방식을 강권**(토큰이 평문 HTTP면 위험 —
> 사설망이면 안전, 공인망이면 웹을 TLS로).

### A2. NFS 서버 — 워크스페이스 export
봇 산출물·업로드가 사는 `ops/var/organt_sns_workspace`만 공유(상태파일은 DB로 감).
```bash
apt-get install -y nfs-kernel-server
echo "/root/ClaudeCompany/ops/var/organt_sns_workspace  <박스B_사설IP>(rw,sync,no_subtree_check,no_root_squash)" >> /etc/exports
exportfs -ra && systemctl enable --now nfs-kernel-server
ufw allow from <박스B_사설IP> to any port 2049 proto tcp
```

### A3. 웹은 추가 플래그 불필요
웹은 `ms_status`·레지스트리를 **DB-우선으로 이미 읽는다**(코드 기본 — 없으면 파일 폴백). push하는 쪽은
러너다. 따라서 박스 A 웹 env엔 상태 플래그가 필요 없다. (A1의 gunicorn 사설 bind만 반영해 재시작.)

---

## 박스 B (러너+봇, 신규) 셋업

### B1. 코드·venv 배치
박스 A와 동일한 체크아웃(`/root/ClaudeCompany`)을 git clone + venv 구성(PYTHONPATH=`/root/ClaudeCompany`).
심볼릭 `/root/murmur-stack → /root/ClaudeCompany` 동일 생성. **코드는 복제, 상태는 공유(DB/NFS).**

### B2. 워크스페이스 NFS 마운트 (박스 A와 같은 절대경로)
```bash
apt-get install -y nfs-common
mkdir -p /root/ClaudeCompany/ops/var/organt_sns_workspace
echo "<박스A_사설IP>:/root/ClaudeCompany/ops/var/organt_sns_workspace  /root/ClaudeCompany/ops/var/organt_sns_workspace  nfs  rw,hard,intr  0 0" >> /etc/fstab
mount -a
```

### B3. 러너 env (박스 B `/etc/organt-runner.env`)
박스 A 것 복사 후 다음만 변경/추가:
```
ORGANT_GUIDE_TOKEN=<박스A와 동일 토큰>   # 필수 — guide 인증
ORGANT_STATE_DB=1                        # ms_status DB 미러
ORGANT_REGISTRY_DB=1                     # 레지스트리 DB write-through
ORGANT_REGISTRY_FROM_DB=1                # 부팅 때 DB에서 레지스트리 복원(로컬 파일 아님)
# DATABASE_URL 은 러너에 불필요(HTTP 전용) — 넣지 말 것(넣으면 fail-closed 무관하나 혼선)
```
> **주의(이 세션의 사고 교훈):** 러너는 `DJANGO_SECRET_KEY` 없이 뜬다(관리명령은 fail-closed 제외).
> 그대로 두면 된다 — 러너에 웹 키를 복사하지 말 것(비밀 확산 방지).

### B4. systemd ExecStart — 웹을 사설 IP로
`/etc/systemd/system/organt-runner.service`:
```
ExecStart=/root/murmur-stack/.venv/bin/python manage.py run_organt_sns --remote http://<박스A_사설IP>:8000 --poll 3
```
```bash
systemctl daemon-reload && systemctl restart organt-runner
```

### B5. 박스 A의 러너 중지
분리 후 러너는 박스 B에만. 박스 A:
```bash
systemctl disable --now organt-runner   # 웹+DB만 남김
```

---

## 검증 (박스 B 러너 뜬 뒤)
```bash
# 1) 러너가 웹에 닿나(사설) — &는 셸 특수문자라 URL 인용
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $ORGANT_GUIDE_TOKEN" \
  "http://<박스A_사설IP>:8000/api/guide/state/?channel_id=0&kind=registry"   # 200
# 2) 러너가 DB에서 레지스트리 복원했나
journalctl -u organt-runner --since "2 min ago" | grep projects_db_restored
# 3) 봇 산출물이 NFS로 박스 A에 보이나 — 웹 파일 트리가 채워지는지 UI 확인
# 4) 메모리: 박스 A는 봇 프로세스 사라져 여유, 박스 B가 봇 메모리 부담
free -m   # 양쪽에서
```

## 롤백
1. 박스 A: `systemctl enable --now organt-runner` (러너 복귀), 박스 B 러너 중지.
2. 러너 env 플래그(`ORGANT_REGISTRY_FROM_DB` 등) 제거 → 파일 경로로 복귀(무회귀).
3. nginx/pg/ufw 변경 원복(참조본 `ops/infra/`).
> 상태가 DB·NFS 양쪽에 있어, 롤백해도 파일 폴백이 살아 데이터 유실 없음.

---

## 남는 상세 (분리 후 다룰 것)
- **봇이 만든 앱 배포**(`deploy.py` vps 타겟, node server.js 4100~4199)는 **박스 B**에 뜬다. `app_gateway`가
  박스 A(웹)에 있으면 loopback 프록시가 박스 B 앱에 안 닿는다 → app_gateway가 박스 B 사설 IP로
  프록시하도록 조정하거나, 봇 앱을 Render 타겟(`ORGANT_DEPLOY_TARGET=render`)으로. (별개 서비스라 후순위.)
- **한 러너로 부족해질 때만** 러너 N개 샤딩(P4) — 채널 리스 테이블 + 하트비트. 지금은 CPU가 놀아 불필요.
- **관리형 Postgres 승격**은 DB SPOF가 걱정될 때(백업·PITR·자동 페일오버). 분리와 독립.
