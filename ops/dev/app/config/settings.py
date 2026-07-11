"""dev 피드백 서비스 설정 — murmur에서 이식한 feedback 앱의 독립 호스트.

의도: 제품(murmur)과 dev 도구(코드 지도·피드백 백로그)의 분리. DB는 sqlite 단일 파일
(ops/var/devfeedback.sqlite3) — dev 도구 규모에 충분하고 인프라 0. 계정 원천은 두 겹:
정적 토큰(env, murmur 없이 자립) → murmur /api/me 위임(같은 호스트, 선택). auth.py 참조.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]          # ops/dev/app
DEV_DIR = BASE_DIR.parent                               # ops/dev
VAR_DIR = Path(os.environ.get("DEV_VAR", str(DEV_DIR.parents[1] / "ops" / "var")))

# dev 내부 도구 — 시크릿은 세션 고정값이면 충분(외부 노출은 nginx 뒤 + admin 토큰 게이트)
SECRET_KEY = os.environ.get("DEV_FEEDBACK_SECRET", "devfeedback-not-a-product-secret")
DEBUG = False
ALLOWED_HOSTS = ["*"]                                   # nginx(같은 호스트) 뒤에서만 리슨

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",                              # DRF가 요구하는 최소(세션·admin 미사용)
    "rest_framework",
    "feedback",
    "graph",
]

MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DEV_FEEDBACK_DB", str(VAR_DIR / "devfeedback.sqlite3")),
    }
}

REST_FRAMEWORK = {
    "UNAUTHENTICATED_USER": None,                       # 자체 어댑터(resolve_admin)만 쓴다
}

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "UTC"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
