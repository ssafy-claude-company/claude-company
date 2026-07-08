"""[호환 shim] 실코드는 guide/archive/discord_guide.py — Discord 시대 산물 아카이브(비검증).
소비처(ops/tests·organt_discord shim)의 경로를 깨지 않기 위한 재수출. M9 파사드 관례와 동형."""
from .archive.discord_guide import *                          # noqa: F401,F403
from .archive.discord_guide import DiscordGuide, _split_for_discord  # noqa: F401
