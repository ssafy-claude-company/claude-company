"""[호환 shim] 실코드는 guide/archive/channels.py — Discord 채널 유틸 아카이브(비검증)."""
from .archive.channels import *                               # noqa: F401,F403
from .archive.channels import choose_text_channel_id, resolve_channel_id  # noqa: F401
