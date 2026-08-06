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
