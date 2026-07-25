"""Release/e2e 실행 증거를 정확한 검증 명령에 결속하는 작은 공통 계약.

영수증은 ``어떤 항목을 검증했다``는 호출자 문자열만 증명해서는 안 된다. SYS가 먼저 봉인한
검증 명령과 실제 subprocess 명령이 같아야 하고, 소비자는 그 명령/검증 명세 hash를 다시 확인한다.
"""
import hashlib
import os
import re
import shlex


def normalize_verifier_command(command) -> str:
    """봉인·실행 비교에 쓰는 유일한 명령 정규형(바깥 공백만 무시, 내부는 exact)."""
    return str(command or "").strip()


def verifier_command_hash(command) -> str:
    command = normalize_verifier_command(command)
    return hashlib.sha256(command.encode("utf-8", "surrogateescape")).hexdigest() if command else ""


def verifier_spec_hash(target, spec) -> str:
    target = str(target or "").strip()
    spec = str(spec or "").strip()
    if not target or not spec:
        return ""
    return hashlib.sha256(
        (target + "\0" + spec).encode("utf-8", "surrogateescape")
    ).hexdigest()


_PROBE_RE = re.compile(
    r"(?:^|(?:&&|\|\||;)\s*)"
    r"(?:"
      r"pytest(?:\s|$)|"
      r"python\d*\s+-m\s+(?:pytest|unittest)(?:\s|$)|"
      r"python\d*\s+manage\.py\s+test(?:\s|$)|"
      r"manage\.py\s+test(?:\s|$)|"
      r"npm\s+(?:test|run\s+(?:test|build|check|lint))(?:\s|$)|"
      r"npx\s+(?:playwright|vitest|jest|cypress)(?:\s|$)|"
      r"curl(?:\s|$)|grep(?:\s|$)|test(?:\s|$)|"
      r"node\s+--test(?:\s|$)|"
      r"node\s+\S*(?:test|spec|check|verify|browser|e2e)\S*\.m?js(?:\s|$)|"
      r"python\d*\s+\S*(?:test|spec|check|verify|browser|e2e)\S*\.py(?:\s|$)|"
      r"(?:bash|sh|\./)\s*\S*(?:test|spec|check|verify|browser|e2e)\S*"
    r")",
    re.I,
)
_TRIVIAL_RE = re.compile(r"^(?:true|:|echo(?:\s|$)|printf(?:\s|$))", re.I)
_ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])(/[^\s;&|\"']+)")
_REL_ESCAPE_RE = re.compile(r"(?:^|[\s\"'])(\.\./[^\s;&|\"']*)")
_SHELL_META_RE = re.compile(r"[|;#<>`\n\r]|\$\(|\$\{")


def _inside_workspace(path: str, workspace: str) -> bool:
    try:
        root = os.path.realpath(str(workspace or ""))
        candidate = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
        return bool(root and root != "/" and os.path.commonpath((root, candidate)) == root)
    except (OSError, ValueError):
        return False


def _confined_argv_paths(tokens, workspace: str) -> bool:
    """argv의 cwd/config/prefix 류가 workspace 밖으로 verifier를 돌리지 못하게 한다."""
    path_options = {
        "--rootdir", "--prefix", "--chdir", "--config", "--config-file",
        "--project", "-C",
    }

    def confined(value):
        raw = str(value or "").strip()
        if not raw or re.match(r"^https?://", raw, re.I):
            return True
        normalized = os.path.normpath(raw)
        if normalized == ".." or normalized.startswith(".." + os.sep):
            return False
        if os.path.isabs(raw):
            return bool(workspace) and _inside_workspace(raw, workspace)
        return True

    for index, token in enumerate(tokens):
        if token == ".." or token.startswith("../") or "/../" in token:
            return False
        option, marker, value = token.partition("=")
        if marker and option in path_options and not confined(value):
            return False
        if token in path_options:
            if index + 1 >= len(tokens) or not confined(tokens[index + 1]):
                return False
    return True


def _existing_verifier_targets(tokens, workspace: str, require_existing=True) -> bool:
    """명시한 verifier script/test path가 현재 workspace에 실제 존재하는지 확인한다."""
    if not tokens:
        return False
    if not _confined_argv_paths(tokens, workspace):
        return False

    def exists(path):
        if not workspace or not require_existing:
            return True
        raw = str(path or "").split("::", 1)[0]
        if not raw:
            return True
        candidate = raw if os.path.isabs(raw) else os.path.join(workspace, raw)
        return _inside_workspace(candidate, workspace) and os.path.exists(candidate)

    exe = os.path.basename(tokens[0]).lower()
    info_only = {"--help", "-h", "--version", "-v", "--collect-only", "--collectonly"}
    if any(token.lower() in info_only for token in tokens[1:]):
        return False
    if exe == "curl":
        # curl은 HTTP 4xx/5xx에도 기본 rc=0이다. release/e2e 영수증은 전송 성공이 아니라
        # endpoint 성공을 증명해야 하므로 fail-on-HTTP 옵션을 강제한다.
        has_fail = any(
            token in ("--fail", "--fail-with-body")
            or (token.startswith("-") and not token.startswith("--") and "f" in token[1:])
            for token in tokens[1:]
        )
        has_url = any(re.match(r"^https?://[^\s]+$", token, re.I) for token in tokens[1:])
        return has_fail and has_url
    if exe == "test":
        predicates = {
            "-e", "-f", "-s", "-d", "-r", "-w", "-x", "-L", "-h", "-b", "-c", "-p", "-S",
        }
        return ("!" not in tokens[1:] and any(token in predicates for token in tokens[1:])
                and len(tokens) >= 3)
    if exe == "grep":
        positional = [token for token in tokens[1:] if not token.startswith("-")]
        return len(positional) >= 2 and exists(positional[-1])
    if exe == "npx":
        if len(tokens) < 3:
            return False
        tool = tokens[1].lower()
        action = tokens[2].lower()
        if tool == "cypress" and action == "run":
            try:
                index = tokens.index("--spec")
                return index + 1 < len(tokens) and exists(tokens[index + 1])
            except ValueError:
                return False
        if ((tool == "playwright" and action == "test")
                or (tool in ("vitest", "jest") and action in ("run", "test"))):
            targets = [arg for arg in tokens[3:] if not arg.startswith("-")]
            return bool(targets) and all(exists(target) for target in targets)
        return False
    if re.fullmatch(r"python\d*", exe):
        if len(tokens) < 2:
            return False
        if tokens[1] == "-m":
            if len(tokens) < 3 or tokens[2] not in ("pytest", "unittest"):
                return False
            args = tokens[3:]
            if tokens[2] == "unittest" and not args:
                return False
        elif tokens[1] == "manage.py":
            return exists(tokens[1])
        else:
            return not tokens[1].startswith("-") and exists(tokens[1])
    elif exe == "node":
        if len(tokens) >= 2 and tokens[1] == "--test":
            args = tokens[2:]
            targets = [arg for arg in args if not arg.startswith("-")]
            return bool(targets) and all(exists(target) for target in targets)
        else:
            return len(tokens) >= 2 and not tokens[1].startswith("-") and exists(tokens[1])
    elif exe in ("bash", "sh"):
        script = next((arg for arg in tokens[1:] if not arg.startswith("-")), "")
        return bool(script) and exists(script)
    elif tokens[0].startswith("./"):
        return exists(tokens[0])
    elif exe == "pytest":
        args = tokens[1:]
    else:
        return True

    # pytest/unittest 옵션값(-k expression 등)은 경로가 아니다. 명시적으로 경로처럼 보이는
    # 인자만 검사하고, discovery-only 호출(`pytest`)은 허용한다.
    for arg in args:
        if arg.startswith("-"):
            continue
        probe = arg.split("::", 1)[0]
        if ("/" in probe or probe.startswith(".") or probe.endswith((".py", ".js", ".ts"))
                or probe.lower() in ("test", "tests")
                or probe.lower().startswith(("test_", "tests"))):
            if not exists(probe):
                return False
    return True


def looks_like_verification_command(command, workspace="", require_existing=True) -> bool:
    """영수증 후보가 실제 검사형 명령인지 판정한다.

    일반 run 허용 여부와 별개다. ``true``/출력만/inline 상수 assert 및 작업공간 밖 파일을 보는
    ``test``·``grep`` 같은 성공 제조 명령에는 release/e2e 영수증을 발급하지 않는다.
    """
    cmd = normalize_verifier_command(command)
    # receipt 문법은 의도적으로 작다. pipe/redirect/주석/서브셸/백그라운드/`;`/`||`는 앞 명령의
    # 실패를 뒤 성공으로 바꿀 수 있으므로 전부 거부하고, 실제 verifier argv를 `&&`로 잇는 것만 허용한다.
    scrubbed_amp = cmd.replace("&&", "")
    if (not cmd or _TRIVIAL_RE.match(cmd) or _SHELL_META_RE.search(cmd)
            or "&" in scrubbed_amp):
        return False
    segments = re.split(r"\s*&&\s*", cmd)
    if not segments or any(not segment.strip() for segment in segments):
        return False
    for segment in segments:
        segment = segment.strip()
        if not _PROBE_RE.match(segment):
            return False
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return False
        if (not tokens or tokens[0] in ("cd", "true", ":", "echo", "printf")
                or any(token in ("-c", "-e") for token in tokens[:2])
                or not _existing_verifier_targets(tokens, workspace, require_existing)):
            return False
    if workspace:
        for path in _ABS_PATH_RE.findall(cmd):
            # URL의 경로(`/...`)는 앞 문자가 ':'이므로 정규식에 잡힐 수 있다. URL 전체는 파일 경로가 아니다.
            if path.startswith("//"):
                continue
            if not _inside_workspace(path, workspace):
                return False
        for path in _REL_ESCAPE_RE.findall(cmd):
            if not _inside_workspace(path, workspace):
                return False
    return True


def direct_verifier_command(spec, workspace="", require_existing=True) -> str:
    """Criterion.verify 자체가 실행 가능한 명령이면 그 exact 원문, 자연어 절차면 빈 값."""
    raw = normalize_verifier_command(spec)
    if not raw or re.search(r"[가-힣]", raw):
        return ""
    return raw if looks_like_verification_command(
        raw, workspace, require_existing=require_existing) else ""


def command_matches_spec(command, spec, workspace="") -> bool:
    """기존 exact 명령 명세는 교체 불가, 자연어 명세만 검사형 command 제안을 허용."""
    command = normalize_verifier_command(command)
    if not looks_like_verification_command(command, workspace):
        return False
    exact = direct_verifier_command(spec, workspace)
    if exact:
        return command == exact
    lower_spec = str(spec or "").lower()
    lower_command = command.lower()
    categories = []
    if any(token in lower_spec for token in (
            "browser", "playwright", "page", "screen", "click", "console",
            "브라우저", "페이지", "화면", "클릭", "콘솔", "ui")):
        categories.append(("playwright", "cypress", "selenium", "browser", "e2e"))
    if any(token in lower_spec for token in (
            "http", "api", "endpoint", "route", "status", "curl", "get ", "post ",
            "응답", "상태코드", "라우트", "엔드포인트")):
        categories.append(("curl", "requests", "urllib", "fetch(", "http", "api"))
    if any(token in lower_spec for token in (
            "build", "compile", "bundle", "lint", "빌드", "컴파일", "번들")):
        categories.append(("build", "compile", "lint", "check"))
    if any(token in lower_spec for token in (
            "pytest", "unittest", "test", "테스트")):
        categories.append(("pytest", "unittest", " test", "test_", "_test", "spec"))
    if any(token in lower_spec for token in (
            "file", "path", "persist", "saved", "파일", "경로", "저장", "존재")):
        categories.append(("grep", "test ", "path", "file", "persist", "verify", "check"))
    # 자연어 명세가 검사 종류를 명시했다면 command도 그 종류 중 하나를 실제로 드러내야 한다.
    return not categories or any(
        any(token in lower_command for token in category)
        for category in categories
    )


def deterministic_verifier_suite(commands) -> str:
    """이미 검증된 명령들을 순서·중복에 흔들리지 않는 한 release suite로 합친다."""
    unique = sorted({
        normalize_verifier_command(command)
        for command in (commands or [])
        if normalize_verifier_command(command)
    })
    return " && ".join(unique)
