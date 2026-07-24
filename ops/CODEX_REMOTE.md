# Windows Codex 앱 → Linux SSH 운영

> Windows PC의 공식 ChatGPT 데스크톱 앱에서 **Codex 화면**을 열고, 프로젝트가 있는 Linux VPS에
> SSH로 연결하는 정본이다. Organt의 GPT 봇 런타임(`127.0.0.1:8791`)과 Fable/Claude 세션은
> 별개이며 이 전환으로 건드리지 않는다.

## 연결 구조

```text
Windows ChatGPT 데스크톱 앱 → Codex
  └─ Windows OpenSSH
       └─ root@murmur-ai.duckdns.org:22
            └─ 앱이 SSH 로그인 셸에서 Codex app-server 시작·관리
                 └─ /root/ClaudeCompany
```

로컬 PC가 Windows이고 원격 프로젝트 호스트가 Linux인 구조다. app-server 포트를 인터넷에
노출하지 않고 SSH 연결 자체가 제어·전송 경계가 된다.

공식 경로:

- Windows 앱 다운로드: <https://chatgpt.com/download/>
- SSH 원격 프로젝트 절차: <https://learn.chatgpt.com/docs/remote-connections>

## 서버 준비 상태

- SSH: `murmur-ai.duckdns.org:22`, UFW 허용, OpenSSH 공개키 인증 활성.
- SSH 서버 ED25519 호스트키 지문:
  `SHA256:1F5gILzUVzLcBCfcpJxUhriU745mgai69ioNegQqR9U`.
- Codex: `/usr/local/bin/codex`에서 로그인 셸과 비대화형 명령 모두 발견 가능.
- 인증: root의 Codex가 ChatGPT 구독 계정으로 로그인됨.
- app-server: `codex app-server daemon bootstrap` 완료. 앱이 SSH로 원격 app-server를
  시작·관리할 수 있고, 공인망 Codex TCP 리스너는 없음.
- 프로젝트: `/root/ClaudeCompany`; `AGENTS.md`와 `ops/STATE.md` 자동 정향 경로 유지.
- 전역 기본값: `gpt-5.6-luna`, reasoning effort `max`.

서버에서 다시 확인:

```bash
cd /root/ClaudeCompany
bash ops/codex_remote_check.sh
```

## Windows PC에서 한 번만 할 일

### 1. 공개키 확인

서버에 현재 등록된 ED25519 공개키 지문:

```text
SHA256:JggRBpwaLDuZkibNG5lmUPcO4QzGubiu+D0nsuoyIy4
```

PowerShell에서 Windows 공개키가 없으면 만들고 지문을 확인한다.

```powershell
if (-not (Test-Path "$HOME\.ssh\id_ed25519")) {
  ssh-keygen -t ed25519 -f "$HOME\.ssh\id_ed25519"
}
ssh-keygen -lf "$HOME\.ssh\id_ed25519.pub"
```

지문이 위 서버 등록 지문과 다르면 아래 출력의 **공개키 한 줄만** 서버
`/root/.ssh/authorized_keys`에 추가한다.

```powershell
Get-Content "$HOME\.ssh\id_ed25519.pub"
```

개인키인 `id_ed25519`는 Windows PC 밖으로 복사하지 않는다.

### 2. 구체 SSH 별칭 추가

PowerShell에서 `notepad "$HOME\.ssh\config"`로 아래 항목을 추가한다.

```sshconfig
Host murmur-vps
  HostName murmur-ai.duckdns.org
  User root
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  ServerAliveInterval 30
  ServerAliveCountMax 3
```

Codex는 `Host *` 같은 패턴만 있는 항목이 아니라 `murmur-vps` 같은 구체 별칭을 자동 발견한다.

### 3. 비밀번호 없이 접속 검증

```powershell
ssh -o PasswordAuthentication=no murmur-vps `
  "command -v codex; codex --version; codex login status"
```

첫 접속의 호스트 지문 질문에는
`SHA256:1F5gILzUVzLcBCfcpJxUhriU745mgai69ioNegQqR9U`가 표시되는지 확인한다.
첫 명령이 공개키만으로 성공하기 전에는 서버의 비밀번호 로그인을 끄지 않는다.

### 4. Windows Codex 프로그램에 등록

1. <https://chatgpt.com/download/>에서 최신 Windows 앱을 설치하고 같은 Pro 계정으로 로그인한다.
2. 앱 왼쪽 위에서 `Codex`를 선택한다.
3. `Settings → Connections → SSH`에서 자동 발견된 `murmur-vps`를 추가하거나 활성화한다.
4. 원격 프로젝트 폴더로 `/root/ClaudeCompany`를 선택한다.
5. 첫 채팅에서 `pwd`, `git status --short --branch`, `ops/STATE.md` 확인을 요청한다.

Windows 앱은 SSH를 통해 Linux 서버의 로그인 셸에서 원격 Codex app-server를 시작·관리한다.
별도 WebSocket 터널이나 4500 포트는 사용하지 않는다.

## 전환 완료 판정

다음을 모두 만족해야 기존 murmur GPT/CLI 링크를 퇴역한다.

- Windows 앱의 실행 위치가 `murmur-vps`이고 `pwd`가 `/root/ClaudeCompany`다.
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
