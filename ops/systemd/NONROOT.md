# 서비스는 root로 돌지 않는다 (2026-08-06, 현준-4)

## 왜

바깥에 서는 프로그램이 root면, 그 프로그램의 결함 하나가 곧 서버 장악이다. 이 서버는 그 상태로
돌고 있었다 — 실측:

| 프로그램 | 전 | 후 |
|---|---|---|
| murmur-web (Django) | root | `murmurweb` |
| murmur-sse | root | `murmurweb` |
| murmur-voice (LiveKit) | root | `livekit` |
| 봇 배포 앱 7개 | root (6개) | `organt` |
| organt-runner | `organt` (원래 맞음) | 그대로 |

특히 봇 배포 앱이 컸다: 그 앱은 사람이 봇에게 시킨 대로 쓰인 코드인데 root로 돌면서
`/etc/murmur-web.env`(금고 열쇠·서명 키·결제 키)를 그냥 읽을 수 있었다.

## 사용자를 왜 이렇게 나눴나

**웹을 `organt`(러너 사용자)으로 낮추면 안 된다.** 그러면 러너가 금고 열쇠를 읽게 되고,
`engine_resolve`가 세운 성질("러너는 금고 열쇠를 갖지 않는다 — 그 성질을 깨면 러너 하나가 전
등록자의 키를 쥔다")이 깨진다. 그래서 웹은 별도 사용자다.

    murmurweb  웹·SSE       /etc/murmur-web.env 읽음 (600 murmurweb)
    organt     러너·배포 앱  /etc/organt-runner.env만 (금고 열쇠 없음)
    livekit    SFU          /etc/livekit.yaml 읽음 (640 root:livekit)
    p-<판>     봇 셸        organt-sandbox가 판별 uid로 강등

## 무엇이 필요한가 (새 서버에 올릴 때)

```sh
useradd --system --no-create-home --shell /usr/sbin/nologin murmurweb
useradd --system --no-create-home --shell /usr/sbin/nologin livekit

chown murmurweb:murmurweb /etc/murmur-web.env && chmod 600 /etc/murmur-web.env
chown root:livekit /etc/livekit.yaml && chmod 640 /etc/livekit.yaml
chown -R murmurweb:murmurweb <backend>/var          # 미디어·업로드는 웹이 쓴다

# 러너 자리에는 소유를 넘기지 않고 통과·쓰기만 준다(판별 uid 모델을 건드리지 않으려고)
setfacl -m u:murmurweb:x  /root
setfacl -m u:murmurweb:x  <PJT>/ops/var
setfacl -m u:murmurweb:rwx -d -m u:murmurweb:rwx <PJT>/ops/var/organt_sns_state
setfacl -m u:murmurweb:rx <PJT>/ops/var/organt_sns_workspace
```

유닛에는 `ops/systemd/`의 drop-in을 놓는다(이 폴더 참조).

## 되돌리기

drop-in 파일 하나만 지우고 `systemctl daemon-reload && systemctl restart <서비스>`.

## 확인

    systemctl show murmur-web -p User      # murmurweb
    sudo -u organt head -c1 /etc/murmur-web.env   # 막혀야 한다
    ops/tests/test_app_not_root.py         # 배포 앱을 띄우는 두 자리가 uid를 낮추는지

## 앱 풀 포트는 로컬 전용이다 (2026-08-06)

`system/deploy.py`는 4100–4199를 "로컬 전용 — 게이트웨이만 접근"이라 적어 두지만, 앱은 봇이 쓴
코드라 대개 `0.0.0.0`에 바인딩한다. 그러면 `/apps/<슬롯>/`에 건 멤버 확인을 **포트로 직접 가면
지나친다**. 공유기가 80/443만 넘겨서 인터넷에서는 못 닿지만, 같은 랜에서는 닿는다.

뜻을 규칙으로 강제한다 — `organt-apps-firewall.service`가 부팅마다 세운다:

    iptables -A INPUT -p tcp --dport 4100:4199 ! -i lo -j DROP

미디어(SFU UDP 50000-50100 · TCP 7881)는 건드리지 않는다 — 그건 밖에서 들어와야 하는 길이다.
`nginx → 127.0.0.1:<포트>`는 루프백이라 그대로 산다.

확인:

    iptables -S INPUT | grep 4100        # 규칙이 서 있는가
    curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:4100/   # 200 (게이트웨이 경로)
    # 랜의 다른 기기에서 http://<서버>:4100/ → 막혀야 한다(같은 서버에서는 lo로 배달돼 확인 불가)

## 비특권 전환이 기존 파일 권한과 만나는 자리 (2026-08-06)

서비스를 비특권으로 내리면 그 사용자에게 `/root` 통과 권한을 줘야 한다. 그 순간 **그 아래
`0644` 파일이 전부 그 사용자에게 열린다.** 내리는 일과 함께 반드시 이 축을 훑어야 한다.

실측으로 나온 것(전부 좁혔다):

| 파일 | 상태였던 것 | 누가 읽었나 |
|---|---|---|
| `/root/.claude/.credentials.json` · `daemon/control.key` | `/root/.claude`에 `organt:rwx` ACL | **러너가 운영자 Claude 자격증명·데몬 제어 키를 읽었다** |
| `/var/lib/postgresql/murmur.dump` | 0644 | 서비스 사용자 전부 (DB 전체 사본) |
| `/root/atelier/var/tokens.env` | 0644 | 러너·웹 |
| `/root/wt/*/.dburl` | 0644 | 러너·웹 (값은 비밀번호 교체로 이미 무효) |
| `/root/backups/murmur/*.sql.gz` | 0644 | 러너·웹 (별도 절에 기록) |

정당한 ACL(그대로 둔다):

    /root/ClaudeCompany/ops/var       organt:rwx    러너가 쓰는 자리
    /root/ClaudeCompany/logs          organt:rwx    러너 로그
    /root/.codex                      organt:rwx    러너가 codex를 직접 돌린다(auth.json 포함)
    /root                             organt:--x · murmurweb:--x   통과만

확인:

    # 각 사용자가 읽을 수 있는 비밀이 자기 것뿐인가
    sudo -u organt    head -c1 /etc/murmur-web.env    # 막혀야 한다
    sudo -u murmurweb head -c1 /etc/murmur-web.env    # 이것만 읽혀야 한다
    sudo -u organt    head -c1 /root/.claude/.credentials.json   # 막혀야 한다

## 밖에 서 있는 문 (2026-08-06 실측)

nginx는 레포에 사본을 두지 않으므로 상태를 여기 적는다.

닫혀 있는 것(확인):

    /api/guide/     deny all → 403    두뇌 브리지. 러너만 127.0.0.1:8000 직결로 쓴다
    /api/atelier/   deny all → 403    작업 승격 어댑터
    /gpt-…/         404               옛 ttyd(원격 root 셸) — 2026-07-29 폐쇄
    /admin/         404               MURMUR_DJANGO_ADMIN 꺼짐(2026-08-05)
    /api/           403               DRF 라우터 인덱스(경로 목록) — 기본 권한을 닫으며 함께
    /\.             404               숨은파일(.env·.git)

이번에 닫은 것:

    /dev/           404               127.0.0.1:8100 프록시였는데 그 포트에 아무도 없다(502).
                                      atelier는 자기 도메인에서 8200으로 돈다 — 옛 길이다.
                                      죽어서가 아니라, 누군가 8100에 무엇을 띄우면 그 순간
                                      인증 없이 공개되기 때문에 끊는다.

열려 있어야 하는 것:

    /api/relay/     커넥터가 **밖에서 우리를 폴링**하는 구조다(집 LLM 연동). 커넥터 토큰은
                    sha256 저장·256비트라 추측 불가.
    /livekit/       음성 시그널링(WebSocket). 표(JWT)로 방을 가른다.
