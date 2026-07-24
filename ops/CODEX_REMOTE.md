# Codex Remote SSH 운영

> 사람의 GPT/Codex 개발 세션을 murmur의 임시 웹 CLI 대신 OpenAI 공식 Remote SSH로 연결하는 정본이다.
> Organt의 GPT 봇 런타임(`127.0.0.1:8791`)과 Fable/Claude 세션은 별개이며 이 전환으로 건드리지 않는다.

## 연결 구조

```text
ChatGPT 데스크톱 앱(Mac/Windows)
  └─ 로컬 OpenSSH
       └─ root@murmur-ai.duckdns.org:22
            └─ Codex app-server(Unix socket, TCP 비공개)
                 └─ /root/ClaudeCompany
```

휴대폰에서는 ChatGPT Remote로 데스크톱 호스트에 접속하고, 그 호스트가 다시 SSH로 VPS 프로젝트를
연다. Linux VPS의 CLI만으로 휴대폰 Remote 호스트를 직접 등록하는 구조는 아니다.

공식 절차: <https://learn.chatgpt.com/docs/remote-connections>

## 서버 준비 상태

- SSH: `murmur-ai.duckdns.org:22`, UFW 허용, OpenSSH 공개키 인증 활성.
- Codex: `/usr/local/bin/codex`에서 로그인 셸과 비대화형 명령 모두 발견 가능.
- 인증: root의 Codex가 ChatGPT 구독 계정으로 로그인됨.
- app-server: `codex app-server daemon bootstrap` 완료. 전용 Unix socket만 사용하고 Codex TCP
  리스너는 열지 않음.
- 프로젝트: `/root/ClaudeCompany`; `AGENTS.md`와 `ops/STATE.md` 자동 정향 경로 유지.
- 전역 기본값: `gpt-5.6-luna`, reasoning effort `max`.

서버에서 다시 확인:

```bash
cd /root/ClaudeCompany
bash ops/codex_remote_check.sh
```

## 로컬 컴퓨터에서 한 번만 할 일

### 1. 공개키 확인

서버에 현재 등록된 ED25519 공개키 지문:

```text
SHA256:JggRBpwaLDuZkibNG5lmUPcO4QzGubiu+D0nsuoyIy4
```

로컬 공개키의 지문을 확인한다.

```bash
ssh-keygen -lf ~/.ssh/<개인키>.pub
```

일치하지 않으면 로컬에서 새 키를 만들고 공개키만 서버의 `/root/.ssh/authorized_keys`에 추가한다.
개인키를 VPS에서 만들거나 murmur에 업로드하지 않는다.

### 2. 구체 SSH 별칭 추가

로컬 `~/.ssh/config`:

```sshconfig
Host murmur-vps
  HostName murmur-ai.duckdns.org
  User root
  IdentityFile ~/.ssh/<개인키>
  IdentitiesOnly yes
  ServerAliveInterval 30
  ServerAliveCountMax 3
```

Codex는 `Host *` 같은 패턴만 있는 항목이 아니라 `murmur-vps` 같은 구체 별칭을 자동 발견한다.

### 3. 비밀번호 없이 접속 검증

```bash
ssh -o PasswordAuthentication=no murmur-vps
command -v codex
codex --version
codex login status
exit
```

첫 명령이 공개키만으로 성공하기 전에는 서버의 비밀번호 로그인을 끄지 않는다.

### 4. ChatGPT 데스크톱 앱에 등록

1. 최신 ChatGPT 데스크톱 앱에서 같은 Pro 계정으로 로그인한다.
2. `Settings → Connections → SSH`에서 `murmur-vps`를 추가하거나 활성화한다.
3. 원격 프로젝트 폴더로 `/root/ClaudeCompany`를 선택한다.
4. 첫 채팅에서 `pwd`, `git status --short --branch`, `ops/STATE.md` 확인을 요청한다.

앱은 SSH를 통해 원격 Codex app-server를 시작·관리한다. app-server WebSocket 포트를 공인망에
직접 열지 않는다.

## 전환 완료 판정

다음을 모두 만족해야 기존 murmur GPT/CLI 링크를 퇴역한다.

- 앱의 실행 위치가 `murmur-vps`이고 `pwd`가 `/root/ClaudeCompany`다.
- `AGENTS.md → ops/STATE.md` 정향이 적용된다.
- 읽기, 파일 수정, diff 검토, 명령 승인과 테스트 출력 확인이 한 원격 채팅에서 된다.
- 새 작업은 별도 worktree/claim 규율을 따르고 기존 라이브 checkout을 덮지 않는다.

퇴역 시에도 아래는 유지한다.

- `127.0.0.1:8791`: Organt GPT 봇이 Guide 도구를 쓰는 내부 MCP 브리지.
- Fable/Claude remote-control 세션과 해당 tmux/worktree.

`127.0.0.1:7681`의 ttyd 및 그 상위 프록시는 사람용 대체 연결이 실증된 뒤 정확한 소비자를 확인하고
별도 종료한다.

## SSH 하드닝 게이트

현재 root 비밀번호 접속 사용 이력이 있어 즉시 비활성화하면 잠금 위험이 있다. 위 공개키 전용 접속이
새 터미널에서 성공한 뒤에만 다음을 적용한다.

```bash
install -o root -g root -m 644 \
  /root/ClaudeCompany/ops/infra/sshd-codex-remote.conf \
  /etc/ssh/sshd_config.d/00-codex-remote.conf
sshd -t
systemctl reload ssh
```

적용 중에는 기존 SSH 창을 닫지 않고 두 번째 창에서 다시 아래 명령을 확인한다.

```bash
ssh -o PasswordAuthentication=no murmur-vps
```

실패하면 기존 창에서 `/etc/ssh/sshd_config.d/00-codex-remote.conf`를 제거하고 SSH를 reload한다.
