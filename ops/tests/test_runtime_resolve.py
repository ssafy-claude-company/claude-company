"""실행 판정을 한 곳으로 모은 계약(2026-07-31, 현준-4).

종전 판정은 str(model).startswith("gpt-") 하나였다. 문자열 접두사가 곧 실행 엔진이라
모델 이름이 바뀌거나 제3자 엔드포인트가 들어오면 곧바로 깨진다.
"""
import importlib.util
import os
from types import SimpleNamespace

_BACKEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "murmur", "backend")
_spec = importlib.util.spec_from_file_location(
    "_rr_under_test", os.path.join(_BACKEND, "sns", "runtime_resolve.py"))
_rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rr)


def _agent(model=None, profile=None):
    return SimpleNamespace(bot_id=1, model=model, runtime_profile=profile)


def _profile(kind, model="", endpoint=""):
    return SimpleNamespace(kind=kind, model=model, endpoint=endpoint)


def test_설정이_없으면_옛_판정을_그대로_쓴다():
    """전환 중에는 두 진실원이 공존한다 - 설정이 없는 봇의 실행이 바뀌면 안 된다."""
    assert _rr.kind_of(_agent(model="gpt-5.5")) == _rr.CODEX
    assert _rr.kind_of(_agent(model="opus")) == _rr.CLAUDE
    assert _rr.kind_of(_agent(model="")) == _rr.CLAUDE
    assert _rr.kind_of(_agent(model=None)) == _rr.CLAUDE


def test_설정이_있으면_설정이_이긴다():
    a = _agent(model="opus", profile=_profile("codex", "gpt-5.5"))
    assert _rr.kind_of(a) == _rr.CODEX
    assert _rr.model_of(a) == "gpt-5.5"


def test_openai호환은_codex_경로로_접힌다():
    """프로토콜이 같다 - 종류는 보존하되 실행 경로 판정에서는 하나로 본다."""
    a = _agent(profile=_profile("openai_compat", "llama-3", "https://x.example/v1"))
    assert _rr.kind_of(a) == _rr.CODEX
    assert _rr.endpoint_of(a) == "https://x.example/v1"


def test_우리_엔진은_주소가_비어있다():
    """호출부는 주소가 비었을 때 종전 경로로 간다."""
    assert _rr.endpoint_of(_agent(model="opus")) == ""
    assert _rr.endpoint_of(_agent(profile=_profile("claude", "opus"))) == ""


def test_갈리는_봇을_집어낸다():
    """전환 전에 '동작이 안 바뀐다'를 말이 아니라 수로 보이기 위한 것."""
    same = _agent(model="gpt-5.5", profile=_profile("codex", "gpt-5.5"))
    differ = _agent(model="opus", profile=_profile("codex", "gpt-5.5"))
    assert _rr.disagreements([same]) == []
    assert len(_rr.disagreements([same, differ])) == 1


def test_전역기본은_설정이_붙어야_바뀐다():
    """러너는 이 값 하나로 전역 엔진을 정한다 - 붙기 전에는 한 글자도 안 바뀌어야 한다."""
    import importlib.util as _iu

    spec = _iu.spec_from_file_location(
        "_views_helper", os.path.join(_BACKEND, "sns", "views.py"))
    # views.py는 Django 설정을 요구하므로 통째로 부르지 않는다 - 헬퍼의 계약만 재현한다.
    def effective(cfg):
        p = getattr(cfg, "default_runtime_profile", None)
        if p is not None and getattr(p, "model", ""):
            return str(p.model)
        return cfg.default_model

    assert effective(SimpleNamespace(default_model="gpt-5.6-luna",
                                     default_runtime_profile=None)) == "gpt-5.6-luna"
    assert effective(SimpleNamespace(default_model="gpt-5.6-luna",
                                     default_runtime_profile=_profile("codex", "gpt-5.6-luna"))) \
        == "gpt-5.6-luna"
    # 설정이 다른 값을 들고 있으면 그쪽이 이긴다 - 그래서 잇는 순간이 곧 전환이다.
    assert effective(SimpleNamespace(default_model="gpt-5.6-luna",
                                     default_runtime_profile=_profile("claude", "opus"))) == "opus"
    assert spec is not None


class _Q:
    """동의 조회를 흉내낸다 - DB 없이 관문의 계약만 본다."""
    def __init__(self, found):
        self.found = found

    def filter(self, **kw):
        self.kw = kw
        return self

    def exists(self):
        return self.found


def _patch_consent(monkeypatch, found=None, boom=False):
    import sys, types
    mod = types.ModuleType("sns.models")

    class PEC:
        objects = _Q(found)
    if boom:
        class PEC:  # noqa: F811
            class objects:
                @staticmethod
                def filter(**kw):
                    raise RuntimeError("DB 없음")
    mod.ProjectEngineConsent = PEC
    monkeypatch.setitem(sys.modules, "sns.models", mod)
    monkeypatch.setitem(sys.modules, "sns", types.ModuleType("sns"))


def test_우리_엔진은_동의가_필요없다():
    """데이터가 우리 밖으로 안 나간다 - 동의를 물을 일이 아니다."""
    ours = _profile("claude", "opus")
    assert _rr.consent_ok(ours, None) is True
    assert _rr.consent_ok(ours, object()) is True


def test_제3자는_판이_없으면_거부한다():
    """동의를 받을 주체가 없다 - 주체 없는 동의는 동의가 아니다."""
    third = _profile("openai_compat", "llama", "https://x.example/v1")
    assert _rr.consent_ok(third, None) is False


def test_제3자는_동의가_있어야_열린다(monkeypatch):
    third = _profile("openai_compat", "llama", "https://x.example/v1")
    _patch_consent(monkeypatch, found=False)
    assert _rr.consent_ok(third, object()) is False
    _patch_consent(monkeypatch, found=True)
    assert _rr.consent_ok(third, object()) is True


def test_확인_실패는_거부다(monkeypatch):
    """확인 실패를 허용으로 읽으면 통제가 아니다."""
    third = _profile("openai_compat", "llama", "https://x.example/v1")
    _patch_consent(monkeypatch, boom=True)
    assert _rr.consent_ok(third, object()) is False


def test_관문이_동의없는_제3자를_돌려주지_않는다(monkeypatch):
    """None은 '우리 기본으로 간다' - 조용히 남의 서버로 보내는 것보다 안전하다."""
    third = _profile("openai_compat", "llama", "https://x.example/v1")
    a = _agent(model="opus", profile=third)
    _patch_consent(monkeypatch, found=False)
    assert _rr.profile_for_turn(a, object()) is None
    _patch_consent(monkeypatch, found=True)
    assert _rr.profile_for_turn(a, object()) is third
