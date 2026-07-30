# 판별 격리 도우미 — 설치 절차

2026-07-30, 현준-4. 설계 근거는 `ops/2026-07-30-판격리-설계.md` 7절.

**설치하기 전에는 아무 영향이 없다.** 러너는 도우미를 부르지 않고, 지금처럼 단일 계정
(`organt`)으로 봇 셸을 돌린다. 설치는 특권 표면을 새로 만드는 일이라 사람이 판단해 넣는다.

## 이 도우미가 하는 일

봇 셸을 판(작품)별 uid로 내리는 일에만 특권이 필요하다. 그것 때문에 러너 전체를 root로
돌리면 러너의 인프로세스 파일 도구(Read/Write/Edit)까지 root가 된다. 그 한 조각만 떼어내
root로 실행하면, 러너는 비특권을 유지하면서 판 격리를 얻는다.

얻는 것:

  - 판끼리 **커널** 파일 경계 — 지금은 훅과 bwrap 바인드(정책)만이 막는다
  - 봇 트래픽을 러너 트래픽과 uid로 구분 가능 → egress 허용 목록을 걸 수 있다
    (지금은 러너와 봇이 같은 uid라 봇만 막을 수 없다)

## 신뢰 경계

`system/sandbox_guard.py`의 검증이 전부다. 호출자(러너)는 비특권이고 침해됐을 수 있다고
가정한다.

  - 작업공간은 정해진 뿌리 밑이어야 한다(realpath 기준 — 심링크 탈출 차단)
  - **uid를 인자로 받지 않는다.** 작업공간에서 도출한다. 고를 수 있으면 판 A의 트리를 판 B의
    uid로 넘겨 교차 오염을 만들 수 있다.
  - uid는 300000~300999이고 0이 될 수 없다
  - 판 대장(`/var/lib/organt-sandbox/uidmap.json`)은 root 전용 0600 — 호출자가 재배정 못 한다

검증은 `ops/tests/test_sandbox_guard.py`가 지킨다(거부해야 하는 것 8건 · 통과해야 하는 것 7건).

## 설치

    # 1) 도우미 배치 (root 소유·비쓰기)
    install -o root -g root -m 0755 ops/sandbox/organt-sandbox /usr/local/sbin/organt-sandbox

    # 2) 러너에게 이 프로그램만 허용
    cat >/etc/sudoers.d/60-organt-sandbox <<'EOF'
    organt ALL=(root) NOPASSWD: /usr/local/sbin/organt-sandbox
    EOF
    chmod 0440 /etc/sudoers.d/60-organt-sandbox
    visudo -c        # 문법 확인 — 깨지면 sudo 전체가 잠긴다

    # 3) 대장 자리
    install -d -o root -g root -m 0700 /var/lib/organt-sandbox

## 확인

    # 뿌리 밖은 거부돼야 한다
    sudo -u organt sudo -n /usr/local/sbin/organt-sandbox --workspace /etc --command 'id'
    # → organt-sandbox: 작업공간이 허용 뿌리 밖이다

    # 판 폴더는 그 판의 uid로 돈다(300000번대)
    sudo -u organt sudo -n /usr/local/sbin/organt-sandbox \
      --workspace /root/ClaudeCompany/ops/var/organt_sns_workspace/<판폴더> --command 'id -u; pwd'

## 되돌리기

    rm -f /etc/sudoers.d/60-organt-sandbox /usr/local/sbin/organt-sandbox

러너 코드는 도우미가 없으면 지금 동작을 그대로 쓴다 — 지우기만 하면 원상복구다.

## 남은 연결 작업 (설치 후)

도우미를 설치해도 러너가 아직 부르지 않는다. `run_workspace_command`가 플래그
(`ORGANT_SANDBOX_HELPER=1`)일 때 도우미를 경유하도록 잇는 작업이 남아 있다. 판 하나에서
실사용 흐름(서버 띄우기·localhost curl·npm install·git·playwright)을 통과시킨 뒤 켠다.
