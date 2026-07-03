# [M5 호환 shim] organt_discord.main → murmur-stack 단일 진실원 재수출
from guide.discord_main import *          # noqa: F401,F403
from guide.discord_main import (          # noqa: F401
    KOREAN_NAMES, assign_stable_names, load_roster, find_pending_request,
    graduated_project, projects_to_resume, resume_continue_body,
)
from organt.builder import _make_builder  # noqa: F401
