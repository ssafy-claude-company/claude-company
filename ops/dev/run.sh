#!/usr/bin/env bash
# dev 피드백 서비스 기동 — 127.0.0.1:8100 (nginx /dev/ 프록시 대상)
# env: DEV_FEEDBACK_TOKENS(자립 토큰) 또는 DEV_FEEDBACK_MURMUR(=http://127.0.0.1:8000, murmur 계정 위임)
cd "$(dirname "$0")/app"
export DJANGO_SETTINGS_MODULE=config.settings
export DEV_FEEDBACK_MURMUR="${DEV_FEEDBACK_MURMUR:-http://127.0.0.1:8000}"
PY=/root/ClaudeCompany/.venv/bin/python
"$PY" manage.py migrate --noinput
exec "$PY" -m gunicorn config.wsgi -b 127.0.0.1:8100 --workers 2
