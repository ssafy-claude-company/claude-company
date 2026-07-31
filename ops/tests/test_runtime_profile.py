"""실행 설정의 주소 검증 계약(2026-07-31, 현준-4).

등록된 주소는 우리 러너가 접속하는 주소다. 러너는 egress 통제 밖이라 내부에 다 닿는다
(실측: murmur 내부 API 200, DB 연결 성립). 남이 지정한 주소를 그대로 부르면 우리 내부를
대신 두드려 주는 셈이다.
"""
import importlib.util
import os

import pytest

# 정본 경로를 박으면 워크트리에서 돌 때 정본 코드를 시험하게 된다 - 이 파일 기준으로 찾는다.
_BACKEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "murmur", "backend")
_spec = importlib.util.spec_from_file_location(
    "_rp_under_test", os.path.join(_BACKEND, "sns", "runtime_profiles.py"))
_rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rp)
EndpointRejected, validate_endpoint = _rp.EndpointRejected, _rp.validate_endpoint


def test_평문은_거부한다():
    """평문이면 그 판의 대화가 그대로 흐른다 - 기능이 아니라 데이터 이전이다."""
    with pytest.raises(EndpointRejected) as e:
        validate_endpoint("http://example.com/v1")
    assert "https" in str(e.value)


def test_루프백은_거부한다():
    """러너가 자기 자신을 두드리게 만드는 고전적 SSRF."""
    for url in ("https://127.0.0.1/v1", "https://localhost/v1"):
        with pytest.raises(EndpointRejected):
            validate_endpoint(url)


def test_사설대역은_거부한다():
    with pytest.raises(EndpointRejected):
        validate_endpoint("https://192.168.0.5/v1")


def test_링크로컬_메타데이터는_거부한다():
    """클라우드 자격증명 종단 - 여기 닿으면 호스트 키가 통째로 샌다."""
    with pytest.raises(EndpointRejected):
        validate_endpoint("https://169.254.169.254/latest/meta-data/")


def test_주소에_박은_자격증명은_거부한다():
    """URL에 담긴 키는 로그와 오류 메시지로 새어 나간다 - 금고를 쓰게 한다."""
    with pytest.raises(EndpointRejected) as e:
        validate_endpoint("https://user:secret@example.com/v1")
    assert "금고" in str(e.value)


def test_빈_주소는_통과한다():
    """우리 엔진이면 주소가 없다 - 그건 거부 대상이 아니다."""
    assert validate_endpoint("") == ""
    assert validate_endpoint(None) == ""


def test_공인주소는_통과한다():
    """정상 등록이 막히면 기능이 죽는다 - 거부만 하는 검증은 검증이 아니다."""
    assert validate_endpoint("https://api.openai.com/v1") == "https://api.openai.com/v1"
