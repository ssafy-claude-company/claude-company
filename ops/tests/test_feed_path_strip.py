"""[사용자 화면에 서버 내부 경로가 뜬다(2026-08-02, 피드 전수 스캔)] 채널 발언 45건(ch267)·8건(ch303)에
작업공간 절대경로가 그대로 실렸고 링크로 렌더된 것도 있었다. 표시 직전에만 뿌리를 떼어낸다."""
from guide.murmur_guide import strip_server_paths


def test_작업공간_절대경로는_상대경로가_된다():
    t = "[verifier](</root/murmur-stack/ops/var/organt_sns_workspace/p-078-게임-79/scripts/verify.py>)"
    assert strip_server_paths(t) == "[verifier](<scripts/verify.py>)"


def test_정본_체크아웃_경로도_같이_처리한다():
    t = "PYTHONPATH=/root/ClaudeCompany/ops/var/organt_sns_workspace/p-079-x/.qa-deps python3 a.py"
    assert strip_server_paths(t) == "PYTHONPATH=.qa-deps python3 a.py"


def test_한_줄에_여러_번_나와도_모두_처리한다():
    root = "/root/murmur-stack/ops/var/organt_sns_workspace/p-1-a/"
    assert strip_server_paths(f"{root}x.py 와 {root}y.py") == "x.py 와 y.py"


def test_작업공간이_아닌_경로는_건드리지_않는다():
    t = "관계없는 /root/other/path 는 그대로"
    assert strip_server_paths(t) == t


def test_빈값도_안전하다():
    assert strip_server_paths(None) == "" and strip_server_paths("") == ""
