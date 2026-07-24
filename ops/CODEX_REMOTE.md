# Codex 원격 접속 운영

> 사람의 GPT/Codex 개발 세션을 murmur의 임시 웹 CLI 대신 OpenAI 공식 클라이언트로 연결하는
> 정본이다. Organt의 GPT 봇 런타임(`127.0.0.1:8791`)과 Fable/Claude 세션은 별개이며 이 전환으로
> 건드리지 않는다.

## 플랫폼별 결론

| 로컬 컴퓨터 | 공식 경로 |
|---|---|
| **Linux** | Codex CLI 원격 TUI → SSH 로컬 포워딩 → VPS app-server |
| macOS/Windows | ChatGPT 데스크톱 앱의 SSH 연결 |
| 휴대폰 | macOS/Windows 데스크톱 앱을 호스트로 등록한 ChatGPT Remote |

ChatGPT 데스크톱 Remote의 호스트 지원 범위는 macOS와 Windows다. Linux에서는 데스크톱 앱
Remote가 아니라 공식 Codex CLI의 `--remote` 터미널 UI를 사용한다. Linux CLI만으로 휴대폰 Remote
호스트를 등록할 수는 없다.

공식 문서:

- 원격 터미널 UI와 SSH 포워딩: <https://learn.chatgpt.com/docs/app-server>
- 데스크톱/휴대폰 Remote 및 SSH 호스트: <https://learn.chatgpt.com/docs/remote-connections>

## 연결 구조

Linux 기본 경로:

```text
Linux Codex CLI
  └─ ws://127.0.0.1:4500
       └─ SSH -L (암호화, 외부 포트 개방 없음)
            └─ root@murmur-ai.duckdns.org:22
                 └─ VPS ws://127.0.0.1:4500
                      └─ Codex app-server → /root/ClaudeCompany
```

macOS/Windows 대안:

```text
ChatGPT 데스크톱 앱
  └─ 로컬 OpenSSH
       └─ root@murmur-ai.duckdns.org:22
            └─ Codex app-server → /root/ClaudeCompany
```

## 서버 준비 상태

- SSH: `murmur-ai.duckdns.org:22`, UFW 허용, OpenSSH 공개키 인증 활성.
- SSH 서버 ED25519 호스트키 지문:
  `SHA256:1F5gILzUVzLcBCfcpJxUhriU745mgai69ioNegQqR9U`.
- Codex: `/usr/local/bin/codex`에서 로그인 셸과 비대화형 명령 모두 발견 가능.
- 인증: root의 Codex가 ChatGPT 구독 계정으로 로그인됨.
- Linux 원격 TUI: systemd `codex-remote-tui.service`가 VPS
  `ws://127.0.0.1:4500`에서만 실행. 공인망과 UFW에는 4500을 열지 않음.
- 데스크톱/제어면: `codex app-server daemon bootstrap`의 전용 Unix socket도 유지.
- 프로젝트: `/root/ClaudeCompany`; `AGENTS.md`와 `ops/STATE.md` 자동 정향 경로 유지.
- 전역 기본값: `gpt-5.6-luna`, reasoning effort `max`.

서버에서 다시 확인:

```bash
cd /root/ClaudeCompany
bash ops/codex_remote_check.sh
```

## Linux 로컬 컴퓨터

### 1. Codex CLI 설치 또는 갱신

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex --version
```

서버와 로컬 CLI는 모두 가능한 최신 버전을 쓰는 것이 좋다. 원격 TUI는 서버의 ChatGPT 인증과
프로젝트 파일을 사용하므로 로컬로 소스를 복제하지 않는다.

### 2. SSH 공개키 확인

서버에 현재 등록된 ED25519 공개키 지문:

```text
SHA256:JggRBpwaLDuZkibNG5lmUPcO4QzGubiu+D0nsuoyIy4
```

로컬 공개키의 지문을 확인한다.

```bash
ssh-keygen -lf ~/.ssh/<개인키>.pub
```

일치하지 않으면 로컬에서 새 키를 만들고 **공개키 한 줄만** 서버의
`/root/.ssh/authorized_keys`에 추가한다. 개인키를 VPS에서 만들거나 murmur에 업로드하지 않는다.
공개키 등록 전에도 현재는 root 비밀번호 접속으로 터널을 시험할 수 있다.

### 3. SSH 별칭

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

첫 접속의 호스트 지문 질문에는
`SHA256:1F5gILzUVzLcBCfcpJxUhriU745mgai69ioNegQqR9U`가 표시되는지 확인한다.

### 4. SSH 터널 실행

첫 번째 로컬 터미널에서 계속 실행해 둔다.

```bash
ssh -N \
  -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:4500:127.0.0.1:4500 \
  murmur-vps
```

별칭을 아직 만들지 않았다면 마지막 인자를 `root@murmur-ai.duckdns.org`로 바꾼다. 로컬 4500을
다른 프로그램이 쓰면 왼쪽 포트만 4501로 바꿔
`-L 127.0.0.1:4501:127.0.0.1:4500`을 사용한다.

### 5. Codex 원격 TUI 실행

두 번째 로컬 터미널:

```bash
codex --remote ws://127.0.0.1:4500
```

터널의 로컬 포트를 4501로 바꿨다면 URL도 `ws://127.0.0.1:4501`로 바꾼다. app-server의
기본 작업 디렉터리는 `/root/ClaudeCompany`다. 첫 요청으로 아래를 확인한다.

```text
pwd와 git status --short --branch를 확인하고 ops/STATE.md를 읽어줘
```

## macOS/Windows ChatGPT 데스크톱 앱

1. 위 SSH 별칭과 공개키 전용 접속을 먼저 검증한다.
2. 최신 ChatGPT 데스크톱 앱에서 같은 Pro 계정으로 로그인한다.
3. `Settings → Connections → SSH`에서 `murmur-vps`를 추가하거나 활성화한다.
4. 원격 프로젝트 폴더로 `/root/ClaudeCompany`를 선택한다.
5. 첫 채팅에서 `pwd`, `git status --short --branch`, `ops/STATE.md` 확인을 요청한다.

## 전환 완료 판정

다음을 모두 만족해야 기존 murmur GPT/CLI 링크를 퇴역한다.

- 원격 Codex의 `pwd`가 `/root/ClaudeCompany`다.
- `AGENTS.md → ops/STATE.md` 정향이 적용된다.
- 읽기, 파일 수정, diff 검토, 명령 승인과 테스트 출력 확인이 한 원격 채팅에서 된다.
- 새 작업은 별도 worktree/claim 규율을 따르고 기존 라이브 checkout을 덮지 않는다.

퇴역 시에도 아래는 유지한다.

- `127.0.0.1:8791`: Organt GPT 봇이 Guide 도구를 쓰는 내부 MCP 브리지.
- Fable/Claude remote-control 세션과 해당 tmux/worktree.

`127.0.0.1:7681`의 ttyd 및 그 상위 프록시는 Linux 원격 TUI 실채팅이 관통된 뒤 정확한 소비자를
확인하고 별도 종료한다.

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
