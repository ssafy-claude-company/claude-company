"""[Discord 이행 M0 — DISCORD_MIGRATION_PLAN §2.2] intake 순수함수.
on_message 이벤트를 QueueStore 레코드로 정규화한다. *discord-free* — discord 객체가 아니라
원시값(id·텍스트·(filename,bytes) 튜플)을 받는다(테스트 가능·매체중립). route_to는 굳히지 않고
from_id는 소실 무해(route_channel_request가 안 씀). 첨부 바이트는 스테이징 디렉토리에 저장."""
import os


def intake(msg_id, channel_id, to_id, kind, body, attachments=None, staging_dir=None):
    """레코드 dict 반환. attachments=[(filename, bytes), ...]이면 staging_dir에 저장하고 경로만 레코드에.
    kind는 'W'(Work)/'I'(Info). payload는 QueueStore.add가 picked=False 초기화."""
    staged = []
    for i, (fn, data) in enumerate(attachments or []):
        if staging_dir:
            os.makedirs(staging_dir, exist_ok=True)
            safe = f"{msg_id}_{i}_{os.path.basename(str(fn)) or 'file'}"
            path = os.path.join(staging_dir, safe)
            with open(path, "wb") as f:
                f.write(data if isinstance(data, (bytes, bytearray)) else str(data).encode())
            staged.append(path)
    return {
        "msg_id": int(msg_id),
        "channel_id": int(channel_id),
        "to_id": int(to_id) if to_id else 0,
        "kind": "I" if str(kind).upper().startswith("I") else "W",
        "body": str(body or ""),
        "attachments": staged,
        "payload": {},
    }
