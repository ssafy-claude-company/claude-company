"""[호환 shim] 실코드는 guide/archive/discord_main.py — Discord 시대 산물 아카이브(비검증).
ops/organt_discord/main.py(M5 shim)가 이 경로를 재수출한다 — 경로 무변경."""
from .archive.discord_main import *                           # noqa: F401,F403
from .archive.discord_main import (                           # noqa: F401
    KOREAN_NAMES, assign_stable_names, load_roster, find_pending_request,
    graduated_project, projects_to_resume, resume_continue_body,
)
